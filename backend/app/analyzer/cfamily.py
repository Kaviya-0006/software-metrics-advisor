"""
C / C++ source support (static, dependency-free approximation).

Python has `ast` and Java has `javalang`; there is no equivalent real
parser for C/C++ in this project's dependency set, and a faithful C++
front end (preprocessor, templates, overload resolution) is far out of
scope. This module therefore implements a *safe static approximation* in
two layers, deliberately small and documented:

1. A character-level **lexer pass** (`lex_source`) that separates code from
   comments, string/char literals (including C++ raw strings and digit
   separators) and preprocessor directives. Every later step works on
   these cleaned views, so a string such as "// not a comment" or a brace
   inside a literal can never be mistaken for code structure.

2. A **declaration-level structure scanner** (`parse_c_family_source`) that
   walks the cleaned text tracking scopes (namespace / class / struct /
   extern "C"), and recognizes function definitions (with bodies), class
   and struct definitions, base classes, fields, member declarations,
   includes and function calls.

The result is a `CFamilyTree`, which parser.py converts into the same
ParsedModule / ParsedClass / ParsedMethod shape Python and Java use, so the
existing metric calculators consume C/C++ through the normal pipeline.

Known limits (also listed in docs/metrics.md): macros are not expanded,
conditional compilation (#if/#else) is not evaluated, templates are not
instantiated, and a class's out-of-line method definitions in a *different*
file than its declaration are not merged back to the class.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


class CFamilyParseError(Exception):
    """Raised when a C/C++ file's brace structure cannot be trusted."""


# ---------------------------------------------------------------------------
# Layer 1: lexer pass
# ---------------------------------------------------------------------------


@dataclass
class LexResult:
    no_comments: str  # comments blanked; strings and directives preserved
    clean: str  # comments, string/char contents and directives blanked
    directives: list[str]  # preprocessor directive text, comments removed
    comment_lines: set[int]  # 0-based line indexes containing/inside comments
    lines: list[str]  # original physical lines (splitlines-compatible)


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _split_physical_lines(normalized: str) -> list[str]:
    lines = normalized.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def _is_digit_separator(nc: list[str], nxt: str) -> bool:
    """True if a `'` is a C++14 digit separator (1'000) rather than the
    start of a character literal."""
    if not nc or not nxt.isalnum() or not nc[-1].isalnum():
        return False
    k = len(nc) - 1
    while k >= 0 and (nc[k].isalnum() or nc[k] in "_'"):
        k -= 1
    return nc[k + 1].isdigit()


def lex_source(source_text: str) -> LexResult:
    s = _normalize_newlines(source_text)
    n = len(s)
    nc: list[str] = []  # no_comments
    cl: list[str] = []  # clean
    directives: list[str] = []
    comment_lines: set[int] = set()

    line = 0
    i = 0
    only_ws_on_line = True
    in_directive = False
    directive_start = 0

    def emit(ch: str, clean_ch: str | None = None) -> None:
        nc.append(ch)
        if clean_ch is None:
            clean_ch = " " if (in_directive and ch != "\n") else ch
        cl.append(clean_ch)

    while i < n:
        ch = s[i]
        nxt = s[i + 1] if i + 1 < n else ""

        if ch == "\n":
            if in_directive and not (nc and nc[-1] == "\\"):
                directives.append("".join(nc[directive_start:]).strip())
                in_directive = False
            emit("\n", "\n")
            line += 1
            only_ws_on_line = True
            i += 1
            continue

        if ch == "/" and nxt == "/":  # line comment (may continue with "\")
            while i < n:
                if s[i] == "\n":
                    if nc and cl and s[i - 1] == "\\":
                        comment_lines.add(line + 1)
                        emit("\n", "\n")
                        line += 1
                        i += 1
                        continue
                    break
                comment_lines.add(line)
                emit(" ", " ")
                i += 1
            continue

        if ch == "/" and nxt == "*":  # block comment
            comment_lines.add(line)
            emit(" ", " ")
            emit(" ", " ")
            i += 2
            while i < n:
                if s[i] == "*" and i + 1 < n and s[i + 1] == "/":
                    emit(" ", " ")
                    emit(" ", " ")
                    i += 2
                    break
                if s[i] == "\n":
                    emit("\n", "\n")
                    line += 1
                    comment_lines.add(line)
                else:
                    emit(" ", " ")
                i += 1
            only_ws_on_line = False
            continue

        if ch == "#" and only_ws_on_line and not in_directive:
            in_directive = True
            directive_start = len(nc)
            only_ws_on_line = False
            emit("#")
            i += 1
            continue

        if ch == '"':
            only_ws_on_line = False
            # C++ raw string:  R"delim( ... )delim"
            if nc and nc[-1] == "R":
                m = re.match(r'"([^\s()\\"]{0,16})\(', s[i:i + 20])
                if m:
                    closer = ")" + m.group(1) + '"'
                    end = s.find(closer, i + m.end())
                    end = n if end == -1 else end + len(closer)
                    emit('"')
                    for k in range(i + 1, end):
                        c = s[k]
                        if c == "\n":
                            emit("\n", "\n")
                            line += 1
                        else:
                            emit(c, " ")
                    if end > i + 1 and s[end - 1] == '"':
                        cl[-1] = '"'
                    i = end
                    continue
            emit('"')
            i += 1
            while i < n and s[i] != "\n":
                c = s[i]
                if c == "\\" and i + 1 < n:
                    emit(c, " ")
                    i += 1
                    if s[i] == "\n":
                        break  # let the main loop handle the newline
                    emit(s[i], " ")
                    i += 1
                    continue
                if c == '"':
                    emit('"')
                    i += 1
                    break
                emit(c, " ")
                i += 1
            continue

        if ch == "'":
            only_ws_on_line = False
            if _is_digit_separator(nc, nxt):
                emit(ch)
                i += 1
                continue
            emit("'")
            i += 1
            while i < n and s[i] != "\n":
                c = s[i]
                if c == "\\" and i + 1 < n and s[i + 1] != "\n":
                    emit(c, " ")
                    emit(s[i + 1], " ")
                    i += 2
                    continue
                if c == "'":
                    emit("'")
                    i += 1
                    break
                emit(c, " ")
                i += 1
            continue

        if not ch.isspace():
            only_ws_on_line = False
        emit(ch)
        i += 1

    if in_directive:
        directives.append("".join(nc[directive_start:]).strip())

    return LexResult(
        no_comments="".join(nc),
        clean="".join(cl),
        directives=directives,
        comment_lines=comment_lines,
        lines=_split_physical_lines(s),
    )


# ---------------------------------------------------------------------------
# Header language sniffing (.h is ambiguous between C and C++)
# ---------------------------------------------------------------------------

_CPP_HEADER_HINTS = re.compile(
    r"\bclass\s+[A-Za-z_]\w*|\bnamespace\b|\btemplate\s*<|::|"
    r"\b(?:public|private|protected)\s*:|\bvirtual\b|\bnullptr\b|"
    r"\bconstexpr\b|\busing\s+namespace\b|\boperator\b"
)
_CPP_STD_INCLUDE = re.compile(r"#\s*include\s*<[A-Za-z_]+>")


def looks_like_cpp(source_text: str) -> bool:
    """Cheap, deterministic rule used only for `.h` files: C++ if the code
    (comments/strings/directives excluded) uses C++-only syntax, or it
    includes an extension-less standard header such as <vector>. A
    `#ifdef __cplusplus extern "C"` guard alone does NOT make a header C++."""
    lexed = lex_source(source_text)
    if _CPP_HEADER_HINTS.search(lexed.clean):
        return True
    return any(_CPP_STD_INCLUDE.match(d) for d in lexed.directives)


# ---------------------------------------------------------------------------
# Layer 2: structure scanner
# ---------------------------------------------------------------------------


@dataclass
class CFunction:
    name: str
    owner_class: str | None
    body: str  # cleaned body text (between the braces)
    calls: list[str] = field(default_factory=list)


@dataclass
class CClass:
    name: str
    kind: str  # "class" | "struct" | "union"
    bases: list[str] = field(default_factory=list)
    methods: list[str] = field(default_factory=list)  # declared + defined, no ctors/dtors
    fields: list[str] = field(default_factory=list)


@dataclass
class CFamilyTree:
    language: str
    no_comments: str
    clean: str
    includes: list[str] = field(default_factory=list)
    namespaces: list[str] = field(default_factory=list)
    classes: list[CClass] = field(default_factory=list)
    functions: list[CFunction] = field(default_factory=list)
    # class name -> method name -> concatenated cleaned bodies (for LCOM1)
    method_bodies: dict[str, dict[str, str]] = field(default_factory=dict)


_INCLUDE_RE = re.compile(r'#\s*include(?:_next)?\s*[<"]([^>"]+)[>"]')

_NON_FUNCTION_NAMES = {
    "if", "while", "for", "switch", "catch", "return", "sizeof", "alignof",
    "decltype", "defined", "typeof", "__attribute__", "else", "do", "new",
    "delete", "throw", "static_assert", "void", "int", "char", "short",
    "long", "float", "double", "signed", "unsigned", "bool", "auto",
}
_CALL_EXCLUDED = _NON_FUNCTION_NAMES | {
    "static_cast", "dynamic_cast", "reinterpret_cast", "const_cast",
    "noexcept", "typeid", "alignas", "_Alignof", "__builtin_expect",
}
_CALL_RE = re.compile(r"\b([A-Za-z_]\w*(?:\s*::\s*[A-Za-z_]\w*)*)\s*\(")

_ATTRIBUTE_PATTERNS = [
    re.compile(r"__attribute__\s*\(\(.*?\)\)", re.S),
    re.compile(r"\[\[.*?\]\]", re.S),
    re.compile(r"__declspec\s*\([^)]*\)"),
    re.compile(r"\b_?[Aa]lignas\s*\([^)]*\)"),
]
_ACCESS_LABEL_RE = re.compile(r"^\s*(?:public|private|protected)\s*:(?!:)")
_OPERATOR_SYMBOL_RE = re.compile(
    r"\boperator\s*(?:\(\s*\)|\[\s*\]|new\s*\[\s*\]|delete\s*\[\s*\]|new\b|delete\b|"
    r"->\*|->|<<=|>>=|<=>|<<|>>|<=|>=|==|!=|\+\+|--|&&|\|\||\+=|-=|\*=|/=|%=|&=|\|=|\^=|"
    r"[-+*/%^&|~!=<>,])"
)
_OPERATOR_CONV_RE = re.compile(r"\boperator\s+[A-Za-z_][\w:\s*&<>]*?(?=\s*\()")
_SINGLE_COLON_RE = re.compile(r"(?<!:):(?!:)")
_IDENT_RE = re.compile(r"[A-Za-z_]\w*")
_QUALIFIED_NAME_RE = re.compile(
    r"((?:[A-Za-z_]\w*(?:<[^<>()]*>)?\s*::\s*)*~?[A-Za-z_]\w*(?:<[^<>()]*>)?)\s*$"
)


def _match_brace(text: str, open_idx: int) -> int:
    """Index of the `}` matching the `{` at open_idx, or -1."""
    depth = 0
    for k in range(open_idx, len(text)):
        c = text[k]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return k
    return -1


def _strip_attributes(header: str) -> str:
    for pattern in _ATTRIBUTE_PATTERNS:
        header = pattern.sub(" ", header)
    return header


def _strip_template_prefix(header: str) -> str:
    h = header.lstrip()
    while h.startswith("template"):
        lt = h.find("<")
        if lt == -1:
            break
        depth = 0
        k = lt
        end = -1
        while k < len(h):
            c = h[k]
            if c == "<":
                depth += 1
            elif c == ">" and h[k - 1] != "-":
                depth -= 1
                if depth == 0:
                    end = k
                    break
            k += 1
        if end == -1:
            break
        h = h[end + 1:].lstrip()
    return h


def _depth0_index(text: str, needle: str) -> int:
    """Index of the first `needle` char at paren/bracket depth 0, or -1."""
    depth = 0
    for k, c in enumerate(text):
        if c in "([":
            depth += 1
        elif c in ")]":
            depth -= 1
        elif c == needle and depth == 0:
            return k
    return -1


def _has_depth0_assignment(text: str) -> bool:
    depth = 0
    for k, c in enumerate(text):
        if c in "([":
            depth += 1
        elif c in ")]":
            depth -= 1
        elif c == "=" and depth == 0:
            prev = text[k - 1] if k else ""
            nxt = text[k + 1] if k + 1 < len(text) else ""
            if nxt == "=" or prev in "=!<>":
                continue
            return True
    return False


def _prepare_header(header: str) -> tuple[str, dict[str, str]]:
    """Strip attributes/template prefix/access labels and replace
    `operator<sym>` spellings with plain identifiers so that name and
    paren detection work. Returns (prepared header, placeholder -> name)."""
    h = _strip_attributes(header)
    h = _strip_template_prefix(h)
    while True:
        stripped = _ACCESS_LABEL_RE.sub("", h)
        if stripped == h:
            break
        h = stripped

    placeholders: dict[str, str] = {}

    def _replace(match: re.Match) -> str:
        key = f"operator__{len(placeholders)}"
        placeholders[key] = re.sub(r"\s+", " ", match.group(0))
        return key

    h = _OPERATOR_SYMBOL_RE.sub(_replace, h)
    h = _OPERATOR_CONV_RE.sub(_replace, h)
    return h.strip(), placeholders


def _classify_header(prepared: str) -> str:
    """One of: namespace, extern, enum, class, function, opaque."""
    if not prepared:
        return "opaque"
    first = _IDENT_RE.match(prepared)
    first_word = first.group(0) if first else ""

    if first_word == "namespace" or re.match(r"inline\s+namespace\b", prepared):
        return "namespace"
    if re.match(r'extern\s*"[^"]*"\s*$', prepared):
        return "extern"
    if re.match(r"(?:typedef\s+)?enum\b", prepared):
        return "enum"
    if _has_depth0_assignment(prepared):
        return "opaque"
    if re.match(r"(?:typedef\s+)?(?:(?:static|const|final|extern)\s+)*(?:class|struct|union)\b", prepared) \
            and _depth0_index(prepared, "(") == -1 and "(" not in prepared:
        return "class"
    if "(" in prepared:
        return "function"
    return "opaque"


def _split_depth0(text: str, sep: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    cur: list[str] = []
    for c in text:
        if c in "(<[{":
            depth += 1
        elif c in ")>]}":
            depth -= 1
        if c == sep and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(c)
    parts.append("".join(cur))
    return parts


def _simple_type_name(text: str) -> str | None:
    """`public ns::Base<T>` -> `Base`."""
    t = re.sub(r"<.*>", "", text)
    t = re.sub(r"\b(?:public|private|protected|virtual)\b", " ", t)
    names = re.findall(r"[A-Za-z_]\w*", t)
    return names[-1] if names else None


def _parse_class_header(prepared: str) -> tuple[str | None, str, list[str], bool]:
    """-> (name, kind, bases, is_typedef)."""
    is_typedef = bool(re.match(r"typedef\b", prepared))
    m = re.search(r"\b(class|struct|union)\b", prepared)
    kind = m.group(1) if m else "struct"
    after = prepared[m.end():] if m else prepared
    colon = _SINGLE_COLON_RE.search(after)
    head = after[:colon.start()] if colon else after
    base_text = after[colon.end():] if colon else ""

    head = re.sub(r"<.*>", "", head)  # drop specialization args
    head = re.sub(r"\b(?:final|alignas)\b", " ", head)
    names = re.findall(r"[A-Za-z_]\w*", head)
    name = names[-1] if names else None

    bases: list[str] = []
    for piece in _split_depth0(base_text, ","):
        base = _simple_type_name(piece)
        if base:
            bases.append(base)
    return name, kind, bases, is_typedef


def _extract_function_name(prepared: str, placeholders: dict[str, str]) -> tuple[str, str | None] | None:
    """-> (function name, qualifier component preceding it or None)."""
    pos = 0
    while True:
        p = prepared.find("(", pos)
        if p == -1:
            return None
        m = _QUALIFIED_NAME_RE.search(prepared[:p].rstrip())
        if m:
            chain = re.sub(r"<[^<>()]*>", "", m.group(1))
            parts = [x.strip() for x in chain.split("::") if x.strip()]
            name = parts[-1]
            if name not in _NON_FUNCTION_NAMES:
                name = placeholders.get(name, name)
                qualifier = parts[-2] if len(parts) > 1 else None
                return name, qualifier
        pos = p + 1


def _ctor_init_brace_pending(prepared: str) -> bool:
    """True when `prepared` ends in a constructor member-init like
    `Foo() : a` so the upcoming `{` is a brace-initializer, not the body."""
    p = prepared.find("(")
    if p == -1:
        return False
    depth = 0
    close = -1
    for k in range(p, len(prepared)):
        if prepared[k] == "(":
            depth += 1
        elif prepared[k] == ")":
            depth -= 1
            if depth == 0:
                close = k
                break
    if close == -1:
        return False
    rest = prepared[close + 1:].strip()
    return rest.startswith(":") and not rest.startswith("::") and bool(re.search(r"[A-Za-z_>]$", rest))


def _extract_calls(body: str) -> list[str]:
    calls: set[str] = set()
    for m in _CALL_RE.finditer(body):
        name = re.sub(r"\s+", "", m.group(1))
        if name.split("::")[-1] in _CALL_EXCLUDED:
            continue
        calls.add(name)
    return sorted(calls)


def _extract_field_names(statement: str) -> list[str]:
    """Data-member names from a class-scope declaration statement."""
    st = _strip_attributes(statement)
    st = _strip_template_prefix(st)
    st = re.sub(r"\b(?:public|private|protected)\s*:", " ", st)
    if re.match(r"\s*(?:using|typedef|friend|static_assert|template)\b", st):
        return []

    fp = re.search(r"\(\s*\*\s*([A-Za-z_]\w*)\s*\)\s*\(", st)  # function-pointer field
    if fp:
        return [fp.group(1)]
    if "(" in st[: (_depth0_index(st, "=") if _depth0_index(st, "=") != -1 else len(st))]:
        return []  # a method declaration, not a field

    names: list[str] = []
    for part in _split_depth0(st, ","):
        decl = part
        cut = len(decl)
        for ch in ("=", "{"):
            idx = decl.find(ch)
            if idx != -1:
                cut = min(cut, idx)
        decl = decl[:cut]
        decl = re.sub(r"\[[^\]]*\]", " ", decl)  # arrays
        decl = _SINGLE_COLON_RE.sub(" ", decl) if ":" in decl and "::" not in decl else decl  # bit-fields
        decl = re.sub(r"\b\d+\b", " ", decl)
        idents = re.findall(r"[A-Za-z_]\w*", re.sub(r"<[^<>]*>", " ", decl))
        if idents:
            names.append(idents[-1])
    # A lone identifier (e.g. a stray `Foo` after `}`) is not a declaration.
    if len(re.findall(r"[A-Za-z_]\w*", st)) < 2:
        return []
    return names


def _is_ctor_or_dtor(name: str, class_name: str | None) -> bool:
    return name.startswith("~") or (class_name is not None and name == class_name)


@dataclass
class _Scope:
    kind: str  # "namespace" | "extern" | "class"
    cls: CClass | None = None
    typedef: bool = False


def parse_c_family_source(source_text: str, language: str) -> CFamilyTree:
    lexed = lex_source(source_text)
    clean = lexed.clean

    if clean.count("{") != clean.count("}"):
        raise CFamilyParseError("Unbalanced braces — file structure cannot be determined.")

    tree = CFamilyTree(language=language, no_comments=lexed.no_comments, clean=clean)
    seen_includes: list[str] = []
    for directive in lexed.directives:
        m = _INCLUDE_RE.match(directive)
        if m:
            seen_includes.append(m.group(1).strip())
    tree.includes = sorted(set(seen_includes))

    scopes: list[_Scope] = []
    class_by_name: dict[str, CClass] = {}
    namespaces: set[str] = set()
    n = len(clean)
    i = 0
    stmt_start = 0

    def current_class() -> CClass | None:
        for sc in reversed(scopes):
            if sc.kind == "class":
                return sc.cls
        return None

    def register_class(cls: CClass) -> None:
        tree.classes.append(cls)
        class_by_name.setdefault(cls.name, cls)

    def handle_statement(text: str) -> None:
        cls = current_class()
        if cls is None or not scopes or scopes[-1].kind != "class":
            return  # only class-scope statements carry members we track
        prepared, placeholders = _prepare_header(text)
        # Drop balanced {...} blocks (enum bodies, brace initializers) so the
        # remaining text is just `type declarator`.
        while True:
            reduced = re.sub(r"\{[^{}]*\}", " ", prepared)
            if reduced == prepared:
                break
            prepared = reduced
        prepared = prepared.strip()
        if not prepared or re.match(r"(?:using|typedef|friend|static_assert)\b", prepared):
            return
        # `struct Foo;` / `enum class Bar;` forward declarations carry no member.
        if re.match(r"(?:class|struct|union|enum(?:\s+(?:class|struct))?)\s+[\w:]+\s*$", prepared):
            return
        eq = _depth0_index(prepared, "=")
        head = prepared if eq == -1 else prepared[:eq]
        fp = re.search(r"\(\s*\*\s*[A-Za-z_]\w*\s*\)\s*\(", head)
        if "(" in head and not fp:
            found = _extract_function_name(prepared, placeholders)
            if found and not _is_ctor_or_dtor(found[0], cls.name) and found[0] not in cls.methods:
                cls.methods.append(found[0])
        else:
            for fld in _extract_field_names(prepared):
                if fld not in cls.fields:
                    cls.fields.append(fld)

    while i < n:
        c = clean[i]
        if c == ";":
            handle_statement(clean[stmt_start:i])
            stmt_start = i + 1
        elif c == "{":
            header = clean[stmt_start:i]
            prepared, placeholders = _prepare_header(header)
            kind = _classify_header(prepared)

            if kind == "function" and _ctor_init_brace_pending(prepared):
                kind = "opaque"

            if kind in ("namespace", "extern"):
                if kind == "namespace":
                    ns = re.sub(r"^(?:inline\s+)?namespace\s*", "", prepared).strip()
                    if ns:
                        namespaces.add(ns)
                scopes.append(_Scope(kind))
                stmt_start = i + 1
            elif kind == "class":
                name, ckind, bases, is_typedef = _parse_class_header(prepared)
                cls = CClass(name=name or "", kind=ckind, bases=bases)
                scopes.append(_Scope("class", cls=cls, typedef=is_typedef))
                stmt_start = i + 1
            elif kind == "function":
                end = _match_brace(clean, i)
                if end == -1:
                    raise CFamilyParseError("Unbalanced braces near a function body.")
                found = _extract_function_name(prepared, placeholders)
                if found is None:
                    i = end
                    stmt_start = end + 1
                else:
                    name, qualifier = found
                    owner = current_class().name if current_class() else None
                    if owner is None and qualifier and qualifier not in namespaces:
                        owner = qualifier
                    body = clean[i + 1:end]
                    tree.functions.append(
                        CFunction(name=name, owner_class=owner, body=body, calls=_extract_calls(body))
                    )
                    if owner and current_class() is not None and not _is_ctor_or_dtor(name, owner):
                        cur = current_class()
                        if name not in cur.methods:
                            cur.methods.append(name)
                    i = end
                    stmt_start = end + 1
            else:  # enum / opaque initializer block — skip, keep accumulating the statement
                end = _match_brace(clean, i)
                if end == -1:
                    raise CFamilyParseError("Unbalanced braces in an initializer.")
                i = end
        elif c == "}":
            if scopes:
                scope = scopes.pop()
                if scope.kind == "class":
                    cls = scope.cls
                    j = clean.find(";", i + 1)
                    trailing = clean[i + 1:j] if j != -1 else ""
                    if j != -1 and not re.search(r"[{}()]", trailing):
                        if not cls.name and scope.typedef:
                            first = _split_depth0(trailing, ",")[0]
                            idents = re.findall(r"[A-Za-z_]\w*", first)
                            cls.name = idents[-1] if idents else ""
                        elif trailing.strip() and not scope.typedef and scopes and scopes[-1].kind == "class":
                            # `struct { ... } pair;` inside a class: `pair` is a data member.
                            for part in _split_depth0(trailing, ","):
                                idents = re.findall(r"[A-Za-z_]\w*", re.sub(r"\[[^\]]*\]", " ", part))
                                if idents and idents[-1] not in scopes[-1].cls.fields:
                                    scopes[-1].cls.fields.append(idents[-1])
                        i = j
                    if cls.name:
                        register_class(cls)
            stmt_start = i + 1
        i += 1

    # Attach out-of-line / inline method bodies to classes defined in this file
    for func in tree.functions:
        if func.owner_class is None:
            continue
        cls = class_by_name.get(func.owner_class)
        if cls is None:
            continue
        if not _is_ctor_or_dtor(func.name, cls.name) and func.name not in cls.methods:
            cls.methods.append(func.name)
        if not _is_ctor_or_dtor(func.name, cls.name):
            bodies = tree.method_bodies.setdefault(cls.name, {})
            bodies[func.name] = bodies.get(func.name, "") + "\n" + func.body

    tree.namespaces = sorted(namespaces)
    return tree
