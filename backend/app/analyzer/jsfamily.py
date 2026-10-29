"""
JavaScript / TypeScript source support (static, dependency-free approximation).

Same architecture as cfamily.py: no real parser exists in this project's
dependency set and pulling in a JS/TS compiler is out of proportion for a
metrics tool, so this module implements a *safe static approximation* in
three small layers:

1. **Lexer pass** (`lex_source`). A recursive character-level pass that
   separates code from comments, string literals, template literals (with
   arbitrarily nested `${ ... }` expressions), regular-expression literals
   (told apart from division by the previous significant token) and, for
   `.js/.jsx/.tsx`, JSX markup and JSX text. It produces two views of the
   file that are exactly as long as the original:
     - `no_comments`: comments blanked, everything else untouched;
     - `clean`: comments, string/template text, regex bodies and JSX
       markup/text blanked. Only real code remains (including the code
       inside `${...}` and JSX `{...}` expressions), so a brace, a `//` or
       the word `if` inside a string or JSX text can never be mistaken for
       structure or for a decision point.

2. **Tokenizer + bracket matcher.** `clean` is cut into identifier / number
   / string / punctuator tokens and every `() [] {}` is paired. Unbalanced
   brackets raise JsFamilyParseError (reported as a parse error, exactly like
   a Python/Java syntax error).

3. **Structure scanner** (`parse_js_family_source`). Walks the tokens and
   recognizes function declarations, function expressions, arrow functions
   (block- and expression-bodied, named from their variable / property /
   assignment target), classes (constructor, methods, getters/setters,
   static, private `#names`, class-field arrows, `extends`, TS `implements`,
   fields, parameter properties), TS interfaces (with `extends`), type
   aliases, enums and namespaces, plus imports / requires / exports and
   calls.

Known limits (also listed in docs/metrics.md): no type checking or module
resolution, JSX is only recognized in files where it is legal and by the
syntactic position of `<`, JavaScript decorators are skipped not analysed,
and anything inside a function body (nested functions, callbacks) is
attributed to the enclosing function rather than reported separately.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass, field

from app.analyzer.cfamily import _normalize_newlines, _split_physical_lines


class JsFamilyParseError(Exception):
    """Raised when a JS/TS file's bracket structure cannot be trusted."""


class _LexFail(Exception):
    """Internal: a speculative lex step (JSX element) did not pan out."""


# ---------------------------------------------------------------------------
# Layer 1: lexer
# ---------------------------------------------------------------------------

_MAX_NEST = 120

# A `/` starts a regex literal (not a division) when the previous significant
# token is one of these keywords, or any punctuator except a closing bracket
# or ++/--.
_REGEX_PRECEDING_KEYWORDS = frozenset(
    "return typeof instanceof in of new delete void throw case do else yield await".split()
)
_NOT_REGEX_PRECEDING_PUNCT = frozenset([")", "]", "}", "++", "--"])

# JSX can only begin where an expression can begin.
_JSX_PRECEDING_PUNCT = frozenset(
    ["(", ",", "=", ":", "?", "[", "{", "&&", "||", "??", "=>", "!", "&", "|", ";"]
)
_JSX_PRECEDING_KEYWORDS = frozenset("return default yield await case".split())

_MULTI_PUNCT = re.compile(r"=>|&&|\|\||\?\?|\+\+|--|\?\.(?!\d)")
_NUMBER_RE = re.compile(
    r"(?:0[xXbBoO][\da-fA-F_]+|\d[\d_]*(?:\.[\d_]*)?(?:[eE][+-]?\d+)?|\.\d[\d_]*(?:[eE][+-]?\d+)?)n?"
)


def _is_ident_start(c: str) -> bool:
    return c.isalpha() or c in "_$"


def _is_ident_char(c: str) -> bool:
    return c.isalnum() or c in "_$"


@dataclass
class JsLexResult:
    no_comments: str
    clean: str
    comment_lines: set[int]
    lines: list[str]
    # (open, close) character positions of every template literal's backticks
    template_spans: list[tuple[int, int]] = field(default_factory=list)


class _Lexer:
    def __init__(self, text: str, jsx: bool) -> None:
        self.s = text
        self.n = len(text)
        self.i = 0
        self.nc: list[str] = []
        self.cl: list[str] = []
        self.line = 0
        self.comment_log: list[int] = []
        self.template_spans: list[tuple[int, int]] = []
        self.last: tuple[str, str] | None = None
        self.jsx = jsx
        self.depth = 0

    # -- emit helpers: every call appends exactly one char to both views ----
    def put(self, ch: str, cl: str | None = None) -> None:
        self.nc.append(ch)
        self.cl.append(ch if cl is None else cl)
        if ch == "\n":
            self.line += 1

    def blank(self, ch: str) -> None:
        self.put(ch, "\n" if ch == "\n" else " ")

    # -- comments ----------------------------------------------------------
    def _line_comment(self) -> None:
        self.comment_log.append(self.line)
        while self.i < self.n and self.s[self.i] != "\n":
            self.put(" ", " ")
            self.i += 1

    def _block_comment(self) -> None:
        s, n = self.s, self.n
        self.comment_log.append(self.line)
        self.put(" ", " ")
        self.put(" ", " ")
        self.i += 2
        while self.i < n:
            if s[self.i] == "*" and self.i + 1 < n and s[self.i + 1] == "/":
                self.put(" ", " ")
                self.put(" ", " ")
                self.i += 2
                return
            if s[self.i] == "\n":
                self.put("\n")
                self.comment_log.append(self.line)
            else:
                self.put(" ", " ")
            self.i += 1

    # -- strings / templates / regex ----------------------------------------
    def _string(self, quote: str) -> None:
        s, n = self.s, self.n
        self.put(quote)
        self.i += 1
        while self.i < n:
            c = s[self.i]
            if c == "\\":
                self.blank(c)
                self.i += 1
                if self.i < n:
                    self.blank(s[self.i])
                    self.i += 1
                continue
            if c == quote:
                self.put(quote)
                self.i += 1
                break
            if c == "\n":
                break  # unterminated string: stop at end of line
            self.blank(c)
            self.i += 1
        self.last = ("str", quote)

    def _template(self) -> None:
        s, n = self.s, self.n
        open_pos = len(self.cl)
        self.put("`")
        self.i += 1
        while self.i < n:
            c = s[self.i]
            if c == "\\":
                self.blank(c)
                self.i += 1
                if self.i < n:
                    self.blank(s[self.i])
                    self.i += 1
                continue
            if c == "`":
                self.put("`")
                self.i += 1
                break
            if c == "$" and self.i + 1 < n and s[self.i + 1] == "{":
                self.blank("$")
                self.blank("{")
                self.i += 2
                self.lex_code(True)  # the ${ ... } expression is real code
                continue
            self.blank(c)
            self.i += 1
        self.template_spans.append((open_pos, len(self.cl) - 1))
        self.last = ("str", "`")

    def _regex_allowed(self) -> bool:
        if self.last is None:
            return True
        kind, text = self.last
        if kind == "punct":
            return text not in _NOT_REGEX_PRECEDING_PUNCT
        if kind == "id":
            return text in _REGEX_PRECEDING_KEYWORDS
        return False

    def _regex(self) -> bool:
        s, n = self.s, self.n
        j = self.i + 1
        in_class = False
        closed = False
        while j < n:
            ch = s[j]
            if ch == "\n":
                return False
            if ch == "\\":
                j += 2
                continue
            if ch == "[":
                in_class = True
            elif ch == "]":
                in_class = False
            elif ch == "/" and not in_class:
                closed = True
                break
            j += 1
        if not closed:
            return False
        j += 1
        while j < n and s[j].isalpha():
            j += 1
        for k, ch in enumerate(s[self.i:j]):
            if ch == "\n":
                self.put("\n")
            else:
                self.put(ch, "0" if k == 0 else " ")
        self.i = j
        self.last = ("num", "0")
        return True

    # -- JSX ----------------------------------------------------------------
    def _jsx_allowed(self) -> bool:
        if self.last is None:
            return True
        kind, text = self.last
        if kind == "punct":
            return text in _JSX_PRECEDING_PUNCT
        if kind == "id":
            return text in _JSX_PRECEDING_KEYWORDS
        return False

    def _try_jsx(self) -> bool:
        snap = (len(self.nc), self.i, self.line, len(self.comment_log), self.last, self.depth, len(self.template_spans))
        try:
            self._jsx_run()
            return True
        except _LexFail:
            n_chars, i, line, n_comments, last, depth, n_spans = snap
            del self.nc[n_chars:]
            del self.cl[n_chars:]
            del self.template_spans[n_spans:]
            self.i, self.line, self.last, self.depth = i, line, last, depth
            del self.comment_log[n_comments:]
            return False

    def _jsx_run(self) -> None:
        s, n = self.s, self.n
        first = len(self.cl)
        depth = 0
        while True:
            if self.i >= n or s[self.i] != "<":
                raise _LexFail
            if self.i + 1 < n and s[self.i + 1] == "/":  # closing tag
                end = s.find(">", self.i)
                if end == -1 or depth == 0:
                    raise _LexFail
                for k in range(self.i, end + 1):
                    self.blank(s[k])
                self.i = end + 1
                depth -= 1
                if depth == 0:
                    break
            else:  # opening tag
                self.blank("<")
                self.i += 1
                while self.i < n and (_is_ident_char(s[self.i]) or s[self.i] in ".-:"):
                    self.blank(s[self.i])
                    self.i += 1
                self_closing = False
                seen_attr = False
                while True:
                    while self.i < n and s[self.i].isspace():
                        self.blank(s[self.i])
                        self.i += 1
                    if self.i >= n:
                        raise _LexFail
                    c = s[self.i]
                    if c == "/" and s[self.i + 1:self.i + 2] == ">":
                        self.blank("/")
                        self.blank(">")
                        self.i += 2
                        self_closing = True
                        break
                    if c == ">":
                        self.blank(c)
                        self.i += 1
                        break
                    if c == "{":  # {...spread} or an attribute-less expression
                        self.blank(c)
                        self.i += 1
                        self.lex_code(True)
                        continue
                    if _is_ident_start(c):
                        start = self.i
                        while self.i < n and (_is_ident_char(s[self.i]) or s[self.i] in "-:."):
                            self.blank(s[self.i])
                            self.i += 1
                        word = s[start:self.i]
                        if not seen_attr and word == "extends":
                            raise _LexFail  # `<T extends X>(...) =>`, a TS generic
                        seen_attr = True
                        j = self.i
                        while j < n and s[j].isspace():
                            j += 1
                        if j < n and s[j] == "=":
                            while self.i <= j:
                                self.blank(s[self.i])
                                self.i += 1
                            while self.i < n and s[self.i].isspace():
                                self.blank(s[self.i])
                                self.i += 1
                            if self.i >= n:
                                raise _LexFail
                            v = s[self.i]
                            if v in "\"'":
                                close = s.find(v, self.i + 1)
                                if close == -1:
                                    raise _LexFail
                                for k in range(self.i, close + 1):
                                    self.blank(s[k])
                                self.i = close + 1
                            elif v == "{":
                                self.blank(v)
                                self.i += 1
                                self.lex_code(True)
                            else:
                                raise _LexFail
                        continue
                    raise _LexFail
                if self_closing:
                    if depth == 0:
                        break
                else:
                    depth += 1
            # children: text and {expressions} until the next tag
            while True:
                if self.i >= n:
                    raise _LexFail
                c = s[self.i]
                if c == "{":
                    self.blank(c)
                    self.i += 1
                    self.lex_code(True)
                    continue
                if c == "<":
                    break
                self.blank(c)
                self.i += 1
        self.cl[first] = "0"  # the whole element behaves like one operand
        self.last = ("num", "0")

    # -- main loop -----------------------------------------------------------
    def lex_code(self, stop_at_close_brace: bool) -> None:
        self.depth += 1
        if self.depth > _MAX_NEST:
            raise JsFamilyParseError("Nesting too deep to analyse.")
        s, n = self.s, self.n
        braces = 0
        while self.i < n:
            c = s[self.i]
            nxt = s[self.i + 1] if self.i + 1 < n else ""
            if c.isspace():
                self.put(c)
                self.i += 1
                continue
            if c == "/" and nxt == "/":
                self._line_comment()
                continue
            if c == "/" and nxt == "*":
                self._block_comment()
                continue
            if c == "#" and self.i == 0 and nxt == "!":
                self._line_comment()  # hashbang line
                continue
            if c in "\"'":
                self._string(c)
                continue
            if c == "`":
                self._template()
                continue
            if c == "{":
                self.put(c)
                self.i += 1
                braces += 1
                self.last = ("punct", "{")
                continue
            if c == "}":
                if braces == 0 and stop_at_close_brace:
                    self.blank(c)  # delimiter of ${...} or a JSX {...}
                    self.i += 1
                    self.depth -= 1
                    return
                self.put(c)
                self.i += 1
                braces = max(braces - 1, 0)
                self.last = ("punct", "}")
                continue
            if c == "/" and self._regex_allowed() and self._regex():
                continue
            if c == "<" and self.jsx and (nxt.isalpha() or nxt in "_$>") and self._jsx_allowed():
                if self._try_jsx():
                    continue
            if "0" <= c <= "9" or (c == "." and "0" <= nxt <= "9"):
                m = _NUMBER_RE.match(s, self.i)
                text = m.group(0) if m else c
                for ch in text:
                    self.put(ch)
                self.i += len(text)
                self.last = ("num", text)
                continue
            if _is_ident_start(c) or (c == "#" and nxt and _is_ident_start(nxt)):
                j = self.i + 1
                while j < n and _is_ident_char(s[j]):
                    j += 1
                word = s[self.i:j]
                for ch in word:
                    self.put(ch)
                self.i = j
                self.last = ("id", word)
                continue
            m = _MULTI_PUNCT.match(s, self.i)
            tok = m.group(0) if m else c
            for ch in tok:
                self.put(ch)
            self.i += len(tok)
            self.last = ("punct", tok)
        if stop_at_close_brace:
            raise _LexFail
        self.depth -= 1


def lex_source(source_text: str, jsx: bool = True) -> JsLexResult:
    s = _normalize_newlines(source_text)
    lexer = _Lexer(s, jsx)
    try:
        lexer.lex_code(False)
    except _LexFail as exc:
        raise JsFamilyParseError("Unterminated expression in template literal or JSX.") from exc
    return JsLexResult(
        no_comments="".join(lexer.nc),
        clean="".join(lexer.cl),
        comment_lines=set(lexer.comment_log),
        lines=_split_physical_lines(s),
        template_spans=list(lexer.template_spans),
    )


# ---------------------------------------------------------------------------
# Layer 2: tokenizer + bracket matching
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(
    r"""
    (?P<ws>\s+)
  | (?P<str>"[ \n]*"|'[ \n]*')
  | (?P<num>(?:0[xXbBoO][\da-fA-F_]+|\d[\d_]*(?:\.[\d_]*)?(?:[eE][+-]?\d+)?|\.\d[\d_]*(?:[eE][+-]?\d+)?)n?)
  | (?P<id>(?:[^\W\d]|[$#])[\w$]*)
  | (?P<punct>>>>=|\.\.\.|===|!==|\*\*=|<<=|>>=|>>>|&&=|\|\|=|\?\?=|=>|==|!=|<=|>=|&&|\|\||\?\?|\?\.(?!\d)|\+\+|--|\+=|-=|\*=|/=|%=|&=|\|=|\^=|\*\*|<<|>>|\S)
    """,
    re.VERBOSE,
)

_OPEN = {"(": ")", "[": "]", "{": "}"}
_CLOSE = {")": "(", "]": "[", "}": "{"}


@dataclass
class _Tokens:
    kinds: list[str]
    texts: list[str]
    starts: list[int]
    ends: list[int]
    lines: list[int]
    match: list[int]
    in_tpl: list[bool] = field(default_factory=list)


def _tokenize(clean: str, template_spans: list[tuple[int, int]] | None = None) -> _Tokens:
    kinds: list[str] = []
    texts: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    lines: list[int] = []
    line = 0
    for m in _TOKEN_RE.finditer(clean):
        kind = m.lastgroup
        text = m.group(0)
        if kind == "ws":
            line += text.count("\n")
            continue
        if kind == "punct" and text == "`":
            kind = "str"
        kinds.append(kind)
        texts.append(text)
        starts.append(m.start())
        ends.append(m.end())
        lines.append(line)
        line += text.count("\n")

    match = [-1] * len(texts)
    stack: list[int] = []
    for idx, text in enumerate(texts):
        if kinds[idx] != "punct":
            continue
        if text in _OPEN:
            stack.append(idx)
        elif text in _CLOSE:
            if not stack or texts[stack[-1]] != _CLOSE[text]:
                raise JsFamilyParseError("Unbalanced brackets — file structure cannot be determined.")
            opener = stack.pop()
            match[opener] = idx
            match[idx] = opener
    if stack:
        raise JsFamilyParseError("Unbalanced brackets — file structure cannot be determined.")
    # Tokens strictly after a template's opening backtick up to and including
    # its closing backtick: a newline inside a template never ends a statement.
    in_tpl = [False] * len(texts)
    for open_pos, close_pos in template_spans or []:
        lo = bisect_right(starts, open_pos)
        hi = bisect_right(starts, close_pos)
        for idx in range(lo, hi):
            in_tpl[idx] = True
    return _Tokens(kinds, texts, starts, ends, lines, match, in_tpl)


# ---------------------------------------------------------------------------
# Layer 3: structure scanner
# ---------------------------------------------------------------------------


@dataclass
class JsFunction:
    name: str
    owner_class: str | None
    body: str  # cleaned body text
    tok_start: int
    tok_end: int  # token range [tok_start, tok_end) of the body
    calls: list[str] = field(default_factory=list)


@dataclass
class JsClass:
    name: str
    kind: str  # "class" | "interface"
    bases: list[str] = field(default_factory=list)  # class: extends (<=1); interface: extends list
    implements: list[str] = field(default_factory=list)
    methods: list[str] = field(default_factory=list)
    fields: list[str] = field(default_factory=list)


@dataclass
class JsFamilyTree:
    language: str
    no_comments: str
    clean: str
    kinds: list[str]
    texts: list[str]
    starts: list[int]
    ends: list[int]
    imports: list[str] = field(default_factory=list)  # distinct module specifiers
    import_bindings: set[str] = field(default_factory=set)
    exports: list[str] = field(default_factory=list)
    classes: list[JsClass] = field(default_factory=list)
    functions: list[JsFunction] = field(default_factory=list)
    type_aliases: list[str] = field(default_factory=list)
    enums: list[str] = field(default_factory=list)
    namespaces: list[str] = field(default_factory=list)
    # class name -> method name -> concatenated cleaned bodies (for LCOM1)
    method_bodies: dict[str, dict[str, str]] = field(default_factory=dict)


_KEYWORDS = frozenset(
    "break case catch class const continue debugger default delete do else enum export "
    "extends false finally for function if import in instanceof new null return super "
    "switch this throw true try typeof var void while with yield let static await".split()
)
_NOT_METHOD_NAMES = frozenset("if for while switch catch with function return".split())
_MEMBER_MODS = frozenset(
    "public private protected static readonly abstract override async get set declare accessor".split()
)
_OBJ_PREV = frozenset(
    ["=", "(", ",", ":", "[", "return", "=>", "?", "||", "&&", "??", "yield", "await", "default", "!"]
)
_TYPE_PREV_OPS = frozenset(
    [":", "|", "&", "<", ",", "=>", "(", "?", "extends", "keyof", "typeof", "is", "as",
     "readonly", "[", "asserts", "infer"]
)
_NEXT_CONT_PUNCT = frozenset(
    [".", "?.", "?", ":", "&&", "||", "??", "+", "-", "*", "/", "%", "**", "=", "==", "===",
     "!=", "!==", "<", ">", "<=", ">=", "|", "&", "^", "(", "[", "=>", ",", "<<", ">>", ">>>",
     "+=", "-=", "*=", "/=", "%=", "&&=", "||=", "??=", "&=", "|=", "^="]
)
_NEXT_CONT_IDS = frozenset(["in", "instanceof", "as", "satisfies", "extends", "implements"])
_PREV_CONT_IDS = frozenset(
    "typeof new delete void await in instanceof of as satisfies extends keyof is readonly async".split()
)
_CALL_RE = re.compile(
    r"(?<![\w$])((?:[A-Za-z_$#][\w$]*)(?:\s*\??\.\s*[A-Za-z_$#][\w$]*)*)\s*\("
)
_CALL_EXCLUDED = frozenset("if for while switch catch function return typeof void delete import await".split())
_THIS_RE = re.compile(r"\bthis\s*\.\s*(#?[A-Za-z_$][\w$]*)")


class _Scanner:
    def __init__(self, language: str, clean: str, nc: str, toks: _Tokens, declaration: bool = False) -> None:
        self.is_ts = language == "typescript"
        # `.d.ts` files are ambient declarations: they cannot contain executable
        # code, so every `=>` in one is part of a *type*, never an arrow function.
        self.is_decl = declaration
        self.clean = clean
        self.nc = nc
        self.kinds, self.texts = toks.kinds, toks.texts
        self.starts, self.ends = toks.starts, toks.ends
        self.lines, self.match = toks.lines, toks.match
        self.in_tpl = toks.in_tpl
        self.n = len(self.texts)
        self.object_colons: set[int] = set()
        self.classes: list[JsClass] = []
        self.functions: list[JsFunction] = []
        self.type_aliases: list[str] = []
        self.enums: list[str] = []
        self.namespaces: list[str] = []

    # -- small helpers --------------------------------------------------------
    def t(self, i: int) -> str:
        return self.texts[i] if 0 <= i < self.n else ""

    def _name_of(self, i: int) -> str:
        if self.kinds[i] == "str":
            return self.nc[self.starts[i] + 1:self.ends[i] - 1] or "<string>"
        return self.texts[i]

    def _is_punct(self, i: int, text: str) -> bool:
        return 0 <= i < self.n and self.kinds[i] == "punct" and self.texts[i] == text

    def _continues_prev(self, p: int, typed: bool) -> bool:
        kind, text = self.kinds[p], self.texts[p]
        if kind == "punct":
            if typed and text in (">", ">>", ">>>"):
                return False
            return text not in (")", "]", "}", "++", "--")
        if typed and text == "void":
            return False  # the *type* `void` ends a type; only the operator `void x` needs an operand
        return kind == "id" and text in _PREV_CONT_IDS

    def _continues_next(self, k: int) -> bool:
        kind, text = self.kinds[k], self.texts[k]
        if kind == "punct":
            return text in _NEXT_CONT_PUNCT
        return kind == "id" and text in _NEXT_CONT_IDS

    def _asi_break(self, k: int, typed: bool) -> bool:
        """Automatic-semicolon-insertion approximation: a statement ends at a
        newline unless the previous token needs a right-hand side or the next
        token continues the expression."""
        if k <= 0 or self.lines[k] == self.lines[k - 1] or self.in_tpl[k]:
            return False
        return not self._continues_prev(k - 1, typed) and not self._continues_next(k)

    def _skip_angle(self, k: int) -> int:
        """Index after the `<...>` group opening at k, or -1 if it isn't one."""
        depth = 0
        j = k
        while j < self.n:
            tx = self.texts[j]
            if self.kinds[j] == "punct":
                if tx == "<":
                    depth += 1
                elif tx in (">", ">>", ">>>"):
                    depth -= len(tx)
                    if depth <= 0:
                        return j + 1
                elif tx in ("(", "[", "{"):
                    j = self.match[j]
                elif tx in (";", ")", "]", "}"):
                    return -1
            j += 1
        return -1

    def _angle_has_function_type(self, a: int, b: int) -> bool:
        """True if the `<...>` group [a, b) has an `=>` at its own nesting level
        (so it is a type argument list holding a function type)."""
        k = a + 1
        while k < b:
            if self.kinds[k] == "punct":
                if self.texts[k] in ("(", "[", "{"):
                    k = self.match[k]
                elif self.texts[k] == "=>":
                    return True
            k += 1
        return False

    def _expr_end(self, q: int, limit: int, typed: bool = False) -> int:
        """Index just past the expression/type that begins at q."""
        k = q
        angle = 0
        while k < limit:
            tx, kd = self.texts[k], self.kinds[k]
            if kd == "punct":
                if tx in ("(", "[", "{"):
                    k = self.match[k] + 1
                    continue
                if tx in (")", "]", "}"):
                    return k
                if tx == ";":
                    return k
                if typed and tx == "<":
                    angle += 1
                elif typed and tx in (">", ">>", ">>>"):
                    angle = max(angle - len(tx), 0)
                elif tx == "," and angle == 0:
                    return k
            if k > q and angle == 0 and self._asi_break(k, typed):
                return k
            k += 1
        return limit

    def _skip_type_to_body(self, r: int, limit: int) -> int:
        """From the first token of a return type, find the body `{` (or the
        index where a body-less signature stops)."""
        k = r
        while k < limit:
            tx, kd = self.texts[k], self.kinds[k]
            if k > r and tx != "{" and self._asi_break(k, True):
                return k
            if kd == "punct":
                if tx == "{":
                    if self.t(k - 1) in _TYPE_PREV_OPS:
                        k = self.match[k] + 1  # object type, not the body
                        continue
                    return k
                if tx in ("(", "["):
                    k = self.match[k] + 1
                    continue
                if tx == "<":
                    k2 = self._skip_angle(k)
                    k = k2 if k2 != -1 else k + 1
                    continue
                if tx == ";":
                    return k
            k += 1
        return limit

    def _skip_type_member(self, m: int, limit: int) -> int:
        """Skip a TS type annotation; stop at `=`, `;`, or the end of the member."""
        k = m
        while k < limit:
            tx, kd = self.texts[k], self.kinds[k]
            if k > m and self._asi_break(k, True):
                return k
            if kd == "punct":
                if tx in ("(", "[", "{"):
                    k = self.match[k] + 1
                    continue
                if tx == "<":
                    k2 = self._skip_angle(k)
                    k = k2 if k2 != -1 else k + 1
                    continue
                if tx in ("=", ";"):
                    return k
            k += 1
        return limit

    # -- function registration -------------------------------------------------
    def _add_block_function(self, name: str, owner: str | None, open_idx: int) -> None:
        close = self.match[open_idx]
        body = self.clean[self.ends[open_idx]:self.starts[close]]
        self.functions.append(JsFunction(name, owner, body, open_idx + 1, close, _extract_calls(body)))

    def _add_expr_function(self, name: str, owner: str | None, q: int, e: int) -> None:
        body = self.clean[self.starts[q]:self.ends[e - 1]] if e > q else ""
        self.functions.append(JsFunction(name, owner, body, q, e, _extract_calls(body)))

    # -- naming ----------------------------------------------------------------
    def _infer_name(self, p: int) -> str:
        """Name for an anonymous function from what precedes it (token index p)."""
        if p < 0:
            return "<anonymous>"
        tx = self.texts[p]
        if tx == "=":
            q = p - 1
            if q >= 0 and self.kinds[q] == "id" and self.texts[q] not in _KEYWORDS:
                chain = q  # walk back over `a.b.c` so `exports.handler =` and `x: A.B =` are told apart
                while chain >= 2 and self.texts[chain - 1] == "." and self.kinds[chain - 2] == "id":
                    chain -= 2
                if self.t(chain - 1) not in (":", "=>", "|", "&", "<", "extends"):
                    return self.texts[q]
            k = q
            steps = 0
            while k >= 0 and steps < 80:
                t2 = self.texts[k]
                if self.kinds[k] == "punct" and t2 in (")", "]", "}"):
                    k = self.match[k] - 1
                    steps += 1
                    continue
                if t2 == ";":
                    break
                if t2 == ":" and k >= 1 and self.kinds[k - 1] == "id":
                    return self.texts[k - 1]
                k -= 1
                steps += 1
            return "<anonymous>"
        if tx == ":" and p >= 1:
            if self.kinds[p - 1] == "id":
                return self.texts[p - 1]
            if self.kinds[p - 1] == "str":
                return self._name_of(p - 1)
        return "<anonymous>"

    # -- functions -------------------------------------------------------------
    def _method_parts(self, k: int, limit: int) -> tuple[int, int, int | None, int] | None:
        """At k: optional generics then `(params)` [`: ReturnType`] then `{body}`.
        Returns (params_open, params_close, body_open | None, index after)."""
        if self.t(k) == "<":
            k2 = self._skip_angle(k)
            if k2 == -1:
                return None
            k = k2
        if not self._is_punct(k, "("):
            return None
        pc = self.match[k]
        r = pc + 1
        if self._is_punct(r, ":"):
            r = self._skip_type_to_body(r + 1, limit)
        if self._is_punct(r, "{") and r < self.n:
            return (k, pc, r, self.match[r] + 1)
        return (k, pc, None, r)

    def _parse_function(self, i: int, hi: int, name: str | None = None, owner: str | None = None) -> int:
        j = i + 1
        if self._is_punct(j, "*"):
            j += 1
        own = None
        if j < self.n and self.kinds[j] == "id" and self.texts[j] != "(":
            own = self.texts[j]
            j += 1
        parts = self._method_parts(j, hi)
        if parts is None:
            return max(j, i + 1)
        _, _, body_open, nxt = parts
        if body_open is None:
            return max(nxt, i + 1)  # overload / `declare function` signature
        if name is None:
            if own:
                name = own
            else:
                p = i - 1
                if p >= 0 and self.texts[p] == "async":
                    p -= 1
                name = self._infer_name(p)
        self._add_block_function(name, owner, body_open)
        return nxt

    def _back_over_type(self, j: int, lo: int) -> int:
        """Walk back over a TS return type ending at j; return the index of
        the `:` that introduces it, or -1."""
        depth = 0
        k = j
        steps = 0
        while k >= lo and steps < 80:
            tx = self.texts[k]
            if self.kinds[k] == "punct":
                if tx in (">", ">>", ">>>"):
                    depth += len(tx)
                elif tx == "<":
                    depth -= 1
                elif tx in (")", "]", "}"):
                    k = self.match[k]
                elif tx == ":" and depth <= 0:
                    return k
                elif tx in (";", "=", "=>", "(", "{", "["):
                    return -1
            k -= 1
            steps += 1
        return -1

    def _arrow_info(self, i: int, lo: int):
        """Describe the arrow function whose `=>` is at i, or None when this
        `=>` is part of a function *type*. Returns (name_prev_idx, body_kind,
        body_a, body_b, next_idx) where body_kind is "block" or "expr"."""
        j = i - 1
        if j < 0:
            return None
        ps = None
        if self.texts[j] == ")" and self.kinds[j] == "punct":
            ps = self.match[j]
            if self.is_ts and ps >= 2 and self.texts[ps - 1] == ":" and (ps - 1) not in self.object_colons:
                q = ps - 2  # token before the colon
                if self._is_punct(q, ")"):
                    qs = self.match[q]
                    pr = qs - 1
                    # A real parameter list is not preceded by an identifier or a closer
                    # (that would make it a call, as in `c ? f(x) : (y) => z`).
                    is_call = pr >= 0 and (
                        (self.kinds[pr] == "id" and self.texts[pr] not in _KEYWORDS and self.texts[pr] != "async")
                        or self.texts[pr] in (")", "]")
                    )
                    if not is_call:
                        ps = qs  # `(params): (ReturnType) =>`
        elif self.is_ts and self.kinds[j] != "punct" or (self.is_ts and self.texts[j] in (">", ">>", ">>>", "]", "}")):
            colon = self._back_over_type(j, lo)
            if colon > 0 and self.texts[colon - 1] == ")":
                ps = self.match[colon - 1]
        if ps is None and self.kinds[j] == "id" and self.texts[j] not in _KEYWORDS:
            ps = j
        if ps is None or ps < 0:
            return None
        p = ps - 1
        if self.is_ts and p >= 0 and self.kinds[p] == "punct" and self.texts[p] in (">", ">>", ">>>"):
            depth, k = 0, p
            while k >= 0 and k > p - 60:
                if self.kinds[k] == "punct":
                    if self.texts[k] in (">", ">>", ">>>"):
                        depth += len(self.texts[k])
                    elif self.texts[k] == "<":
                        depth -= 1
                        if depth <= 0:
                            break
                k -= 1
            if k >= 0 and self.texts[k] == "<" and depth <= 0:
                p = k - 1
        if p >= 0 and self.texts[p] == "async":
            p -= 1
        if self.is_ts and p >= 0 and self.texts[p] in (":", "|", "&", "<", "new", "extends", "as", "satisfies") and p not in self.object_colons:
            return None  # `x: (a) => void` — a function type, not a function
        q = i + 1
        if self._is_punct(q, "{"):
            return (p, "block", q, self.match[q], self.match[q] + 1, ps)
        e = self._expr_end(q, self.n)
        return (p, "expr", q, e, e, ps)

    def _parse_arrow(self, i: int, lo: int, name: str | None = None, owner: str | None = None) -> int:
        info = self._arrow_info(i, lo)
        if info is None:
            return i + 1
        p, kind, a, b, nxt, params_start = info
        # An arrow in this arrow's *parameter list* (`({ f = () => true }) => …`)
        # was registered while the walk passed over the parameters, but it is
        # part of the enclosing function's signature, not a function of its own.
        while self.functions and params_start < self.functions[-1].tok_start < i:
            self.functions.pop()
        fname = name if name is not None else self._infer_name(p)
        if kind == "block":
            self._add_block_function(fname, owner, a)
        else:
            self._add_expr_function(fname, owner, a, b)
        return max(nxt, i + 1)

    # -- classes / interfaces -------------------------------------------------
    def _heritage(self, j: int) -> tuple[list[str], list[str], int]:
        """Parse `extends A, B<T>` / `implements C` up to the body `{`."""
        extends: list[str] = []
        implements: list[str] = []
        mode = None
        while j < self.n and not self._is_punct(j, "{"):
            tx = self.texts[j]
            if tx == "extends":
                mode = extends
                j += 1
            elif tx == "implements":
                mode = implements
                j += 1
            elif self.kinds[j] == "id" and mode is not None:
                name = tx
                j += 1
                while self._is_punct(j, ".") and j + 1 < self.n and self.kinds[j + 1] == "id":
                    name = self.texts[j + 1]
                    j += 2
                mode.append(name)
                while True:
                    if self.t(j) == "<":
                        k2 = self._skip_angle(j)
                        j = k2 if k2 != -1 else j + 1
                    elif self._is_punct(j, "(") or self._is_punct(j, "["):
                        j = self.match[j] + 1
                    else:
                        break
            elif self._is_punct(j, "("):
                j = self.match[j] + 1
            else:
                j += 1
        return extends, implements, j

    def _parse_class(self, i: int, hi: int) -> int:
        j = i + 1
        name = None
        if j < self.n and self.kinds[j] == "id" and self.texts[j] not in ("extends", "implements"):
            name = self.texts[j]
            j += 1
        if self.t(j) == "<":
            k2 = self._skip_angle(j)
            j = k2 if k2 != -1 else j + 1
        extends, implements, jo = self._heritage(j)
        if not self._is_punct(jo, "{"):
            return i + 1
        jc = self.match[jo]
        if name is None:
            p = i - 1
            name = self._infer_name(p) if p >= 0 and self.texts[p] == "=" else "<anonymous>"
        cls = JsClass(name=name, kind="class", bases=extends[:1], implements=implements)
        self._parse_class_body(jo, jc, cls)
        self.classes.append(cls)
        return jc + 1

    def _parse_interface(self, i: int) -> int:
        j = i + 1
        name = self.texts[j]
        j += 1
        if self.t(j) == "<":
            k2 = self._skip_angle(j)
            j = k2 if k2 != -1 else j + 1
        extends, _, jo = self._heritage(j)
        if not self._is_punct(jo, "{"):
            return i + 1
        jc = self.match[jo]
        cls = JsClass(name=name, kind="interface", bases=extends)
        k = jo + 1
        while k < jc:
            tx = self.texts[k]
            if tx in (";", ","):
                k += 1
                continue
            j2 = k
            while self.t(j2) in ("readonly", "get", "set") and self.t(j2 + 1) not in ("(", ":", "?", ";", ",", "}", "<"):
                j2 += 1
            if self._is_punct(j2, "["):
                nm, kk = None, self.match[j2] + 1
            elif j2 < jc and self.kinds[j2] in ("id", "str", "num"):
                nm, kk = self._name_of(j2), j2 + 1
            else:
                k += 1
                continue
            if self.t(kk) in ("?", "!"):
                kk += 1
            if nm is not None:
                if self.t(kk) in ("(", "<"):
                    if nm not in cls.methods:
                        cls.methods.append(nm)
                elif self.t(kk) == ":" and nm not in cls.fields:
                    cls.fields.append(nm)
            k = max(self._expr_end(max(kk, k + 1), jc, typed=True), k + 1)
        self.classes.append(cls)
        return jc + 1

    def _ctor_param_props(self, po: int, pc: int, cls: JsClass) -> None:
        """TS parameter properties: `constructor(private x: T, public y = 1)`."""
        k = po + 1
        while k < pc:
            seg_end = k
            while seg_end < pc and not (self._is_punct(seg_end, ",")):
                if self.kinds[seg_end] == "punct" and self.texts[seg_end] in ("(", "[", "{"):
                    seg_end = self.match[seg_end]
                seg_end += 1
            m = k
            had_mod = False
            while m < seg_end and self.texts[m] in ("public", "private", "protected", "readonly", "override"):
                had_mod = True
                m += 1
            if had_mod and m < seg_end and self.kinds[m] == "id" and self.texts[m] not in cls.fields:
                cls.fields.append(self.texts[m])
            k = seg_end + 1

    def _parse_class_body(self, o: int, c: int, cls: JsClass) -> None:
        i = o + 1
        while i < c:
            tx = self.texts[i]
            if tx in (";", ","):
                i += 1
                continue
            if tx == "@":  # decorator
                i += 1
                while i < c and self.kinds[i] == "id":
                    i += 1
                    if self._is_punct(i, "."):
                        i += 1
                        continue
                    break
                if i < c and self._is_punct(i, "("):
                    i = self.match[i] + 1
                continue
            if tx == "static" and self._is_punct(i + 1, "{"):
                close = self.match[i + 1]  # static initialization block
                self._scan_member_expression(i + 2, close, cls)
                i = close + 1
                continue
            j = i
            while (
                j + 1 < c
                and self.kinds[j] == "id"
                and self.texts[j] in _MEMBER_MODS
                and self.t(j + 1) not in ("(", "=", ":", ";", "?", "!", "<", ",", "}")
            ):
                j += 1
            if self._is_punct(j, "*"):
                j += 1
            if j >= c:
                break
            if self._is_punct(j, "["):
                name, k = "[computed]", self.match[j] + 1
            elif self.kinds[j] in ("id", "str", "num"):
                name, k = self._name_of(j), j + 1
            else:
                i = j + 1
                continue
            if self.t(k) in ("?", "!") and self.t(k + 1) in ("(", ":", "=", ";", "<", ""):
                k += 1

            parts = self._method_parts(k, c) if self.t(k) in ("(", "<") else None
            if parts is not None:
                po, pc, body_open, nxt = parts
                if body_open is not None:
                    self._add_block_function(name, cls.name, body_open)
                    if name == "constructor":
                        self._ctor_param_props(po, pc, cls)
                    elif name not in cls.methods:
                        cls.methods.append(name)
                    i = max(nxt, i + 1)
                else:  # abstract method / overload signature
                    if name != "constructor" and name not in cls.methods:
                        cls.methods.append(name)
                    i = max(nxt, i + 1)
                continue

            # property (field), possibly with a type annotation and initializer
            m = k
            if self._is_punct(m, ":"):
                m = self._skip_type_member(m + 1, c)
            if m < c and self._is_punct(m, "="):
                init_s = m + 1
                init_e = self._expr_end(init_s, c)
                handled = self._class_field_function(init_s, init_e, name, cls)
                if not handled:
                    # The initializer is not itself a function, but may contain
                    # callbacks (`#h = wrap((e) => {...})`, `x = [a.map(f => f)]`).
                    self._scan_member_expression(init_s, init_e, cls)
                if not handled and not name.startswith("[") and name not in cls.fields:
                    cls.fields.append(name)
                i = max(init_e, m + 1)
            else:
                if not name.startswith("[") and name not in cls.fields:
                    cls.fields.append(name)
                i = max(m, k, i + 1)

    def _scan_member_expression(self, s: int, e: int, cls: JsClass) -> None:
        """Register callbacks found in [s, e) as functions owned by the class
        (so their `this.x` accesses and complexity belong to it)."""
        before = len(self.functions)
        self.scan_range(s, e)
        for func in self.functions[before:]:
            func.owner_class = cls.name

    def _class_field_function(self, s: int, e: int, name: str, cls: JsClass) -> bool:
        """`handle = () => {...}` / `run = function () {...}` as a class
        property: register it as a method of the class."""
        k = s
        if self.t(k) == "async":
            k += 1
        if self.t(k) == "function":
            self._parse_function(k, e, name=name, owner=cls.name)
            if name not in cls.methods:
                cls.methods.append(name)
            return True
        j = k
        while j < e:
            if self.kinds[j] == "punct":
                if self.texts[j] == "=>":
                    info = self._arrow_info(j, k)
                    if info is None:
                        return False
                    p, kind, a, b, _, _ = info
                    if kind == "block":
                        self._add_block_function(name, cls.name, a)
                    else:
                        self._add_expr_function(name, cls.name, a, b)
                    if name not in cls.methods:
                        cls.methods.append(name)
                    return True
                if self.texts[j] in ("(", "[", "{"):
                    j = self.match[j]
            j += 1
        return False

    # -- object literals -------------------------------------------------------
    def _is_object_start(self, i: int) -> bool:
        return self.t(i - 1) in _OBJ_PREV

    def _scan_object(self, o: int, c: int) -> None:
        i = o + 1
        while i < c:
            e = i
            while e < c:
                if self.kinds[e] == "punct":
                    if self.texts[e] in ("(", "[", "{"):
                        e = self.match[e] + 1
                        continue
                    if self.texts[e] == "<" and self.is_ts:
                        k2 = self._skip_angle(e)  # `Foo<A, B>` / `<T, U>(…) =>`
                        if k2 != -1 and k2 <= c:
                            e = k2
                            continue
                    if self.texts[e] == ",":
                        break
                e += 1
            self._scan_entry(i, min(e, c))
            i = e + 1

    def _scan_entry(self, s: int, e: int) -> None:
        j = s
        if j >= e:
            return
        if self.texts[j] == "...":
            self.scan_range(j + 1, e)
            return
        while j + 1 < e and self.texts[j] in ("async", "get", "set") and self.t(j + 1) not in ("(", ":", ",", "=", "<", "}"):
            j += 1
        if self._is_punct(j, "*"):
            j += 1
        if j >= e:
            return
        if self._is_punct(j, "["):
            name, j = "[computed]", self.match[j] + 1
        else:
            name = self._name_of(j)
            j += 1
        if j >= e:
            return
        if self.t(j) in ("(", "<"):
            parts = self._method_parts(j, e)
            if parts is not None and parts[2] is not None and name not in _NOT_METHOD_NAMES:
                self._add_block_function(name, None, parts[2])
            return
        if self._is_punct(j, ":"):
            self.object_colons.add(j)
            self.scan_range(j + 1, e)
            return
        self.scan_range(j, e)

    # -- statement-level walk ---------------------------------------------------
    def scan_range(self, lo: int, hi: int) -> None:
        i = lo
        while i < hi:
            tx, kd = self.texts[i], self.kinds[i]
            prev = self.texts[i - 1] if i > 0 else ""
            if kd == "punct":
                if tx == "{" and self._is_object_start(i):
                    close = self.match[i]
                    self._scan_object(i, close)
                    i = close + 1
                    continue
                if tx == "=>":
                    i = i + 1 if self.is_decl else self._parse_arrow(i, lo)
                    continue
                i += 1
                continue
            if (
                kd == "id" and self.is_ts and i + 2 < hi and self._is_punct(i + 1, "<")
                and self.starts[i + 1] == self.ends[i]
            ):
                k2 = self._skip_angle(i + 1)
                if k2 != -1 and self._angle_has_function_type(i + 1, k2):
                    i = k2  # `Map<string, () => void>` — the arrow is part of a type argument
                    continue
            if kd == "id" and prev not in (".", "?."):
                if tx == "class":
                    i = self._parse_class(i, hi)
                    continue
                if tx == "function":
                    i = self._parse_function(i, hi)
                    continue
                if self.is_ts:
                    nxt_is_id = i + 1 < self.n and self.kinds[i + 1] == "id"
                    if tx == "interface" and nxt_is_id:
                        i = self._parse_interface(i)
                        continue
                    if tx == "type" and nxt_is_id and self.t(i + 2) in ("=", "<"):
                        self.type_aliases.append(self.texts[i + 1])
                        j = i + 2
                        if self.texts[j] == "<":
                            k2 = self._skip_angle(j)
                            j = k2 if k2 != -1 else j + 1
                        i = max(self._expr_end(j + 1, hi, typed=True), i + 1) if self.t(j) == "=" else i + 1
                        continue
                    if tx == "enum" and nxt_is_id and self._is_punct(i + 2, "{"):
                        self.enums.append(self.texts[i + 1])
                        i = self.match[i + 2] + 1
                        continue
                    if tx in ("namespace", "module") and nxt_is_id:
                        j = i + 1
                        parts = [self.texts[j]]
                        j += 1
                        while self._is_punct(j, ".") and j + 1 < self.n:
                            parts.append(self.texts[j + 1])
                            j += 2
                        if self._is_punct(j, "{"):
                            self.namespaces.append(".".join(parts))
                            i = j + 1
                            continue
            i += 1


def _extract_calls(body: str) -> list[str]:
    calls: set[str] = set()
    for m in _CALL_RE.finditer(body):
        name = re.sub(r"\s+", "", m.group(1)).replace("?.", ".")
        if name.split(".")[-1] in _CALL_EXCLUDED:
            continue
        calls.add(name)
    return sorted(calls)


# ---------------------------------------------------------------------------
# imports / exports
# ---------------------------------------------------------------------------

_FROM_RE = re.compile(r"\bfrom\s*([\"'])( *)\1")
_SIDE_EFFECT_RE = re.compile(r"(?<![\w$.])import\s*([\"'])( *)\1")
_REQUIRE_RE = re.compile(r"(?<![\w$.])require\s*\(\s*([\"'])( *)\1\s*\)")
_DYNAMIC_IMPORT_RE = re.compile(r"(?<![\w$.])import\s*\(\s*([\"'])( *)\1")
_IMPORT_CLAUSE_RE = re.compile(r"(?<![\w$.])import\s+(?:type\s+)?([^;\"'`]+?)\s+from\s*[\"']")
_REQUIRE_BINDING_RE = re.compile(
    r"(?:const|let|var)\s+(\{[^}]*\}|[A-Za-z_$][\w$]*)\s*=\s*require\s*\("
)
_IMPORT_EQUALS_RE = re.compile(r"(?<![\w$.])import\s+([A-Za-z_$][\w$]*)\s*=\s*require\s*\(")
_EXPORT_DECL_RE = re.compile(
    r"(?<![\w$.])export\s+(?:default\s+)?(?:declare\s+)?(?:async\s+)?(?:abstract\s+)?"
    r"(?:function\s*\*?|class|const\s+enum|const|let|var|interface|type|enum|namespace)\s+(?!(?:extends|implements)\b)([A-Za-z_$][\w$]*)"
)
_EXPORT_LIST_RE = re.compile(r"(?<![\w$.])export\s+(?:type\s+)?\{([^}]*)\}")
_EXPORT_DEFAULT_RE = re.compile(r"(?<![\w$.])export\s+default\b")
_EXPORT_STAR_RE = re.compile(r"(?<![\w$.])export\s+\*")
_COMMONJS_EXPORT_RE = re.compile(r"(?<![\w$.])(?:module\s*\.\s*exports|exports)\s*(?:\.\s*([A-Za-z_$][\w$]*)\s*)?=(?!=)")
_IDENT_FULL = re.compile(r"[A-Za-z_$][\w$]*")


def _binding_names(clause: str) -> set[str]:
    names: set[str] = set()
    for part in re.split(r"[{},]", clause):
        part = re.sub(r"^\s*type\s+", "", part.strip())
        if not part:
            continue
        if ":" in part:
            part = part.split(":")[-1]
        if re.search(r"\bas\b", part):
            part = re.split(r"\bas\b", part)[-1]
        part = part.replace("*", "").strip()
        if _IDENT_FULL.fullmatch(part):
            names.add(part)
    return names


def _extract_imports(clean: str, nc: str) -> tuple[list[str], set[str]]:
    specifiers: set[str] = set()
    for rx in (_FROM_RE, _SIDE_EFFECT_RE, _REQUIRE_RE, _DYNAMIC_IMPORT_RE):
        for m in rx.finditer(clean):
            spec = nc[m.start(2):m.end(2)].strip()
            if spec:
                specifiers.add(spec)

    bindings: set[str] = set()
    for m in _IMPORT_CLAUSE_RE.finditer(clean):
        bindings |= _binding_names(m.group(1))
    for m in _REQUIRE_BINDING_RE.finditer(clean):
        bindings |= _binding_names(m.group(1))
    for m in _IMPORT_EQUALS_RE.finditer(clean):
        bindings.add(m.group(1))
    return sorted(specifiers), bindings


def _extract_exports(clean: str) -> list[str]:
    names: list[str] = []

    def add(name: str) -> None:
        if name and name not in names:
            names.append(name)

    for m in _EXPORT_DECL_RE.finditer(clean):
        add(m.group(1))
    for m in _EXPORT_LIST_RE.finditer(clean):
        for part in m.group(1).split(","):
            part = part.strip()
            if part:
                add(re.split(r"\bas\b", part)[-1].strip())
    if _EXPORT_DEFAULT_RE.search(clean):
        add("default")
    if _EXPORT_STAR_RE.search(clean):
        add("*")
    for m in _COMMONJS_EXPORT_RE.finditer(clean):
        add(m.group(1) or "module.exports")
    return names


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def parse_js_family_source(
    source_text: str, language: str, jsx: bool, declaration: bool = False
) -> JsFamilyTree:
    try:
        lexed = lex_source(source_text, jsx=jsx)
        toks = _tokenize(lexed.clean, lexed.template_spans)
        scanner = _Scanner(language, lexed.clean, lexed.no_comments, toks, declaration)
        scanner.scan_range(0, scanner.n)
    except RecursionError as exc:
        raise JsFamilyParseError("Nesting too deep to analyse.") from exc

    imports, bindings = _extract_imports(lexed.clean, lexed.no_comments)
    tree = JsFamilyTree(
        language=language,
        no_comments=lexed.no_comments,
        clean=lexed.clean,
        kinds=toks.kinds,
        texts=toks.texts,
        starts=toks.starts,
        ends=toks.ends,
        imports=imports,
        import_bindings=bindings,
        exports=_extract_exports(lexed.clean),
        classes=scanner.classes,
        functions=scanner.functions,
        type_aliases=scanner.type_aliases,
        enums=scanner.enums,
        namespaces=sorted(set(scanner.namespaces)),
    )

    # Attach method bodies to their class, and derive instance fields from
    # `this.<name>` usage (JavaScript declares fields by assigning them).
    class_by_name = {c.name: c for c in tree.classes}
    for func in tree.functions:
        cls = class_by_name.get(func.owner_class) if func.owner_class else None
        if cls is None:
            continue
        for m in _THIS_RE.finditer(func.body):
            attr = m.group(1)
            if attr not in cls.methods and attr not in cls.fields:
                cls.fields.append(attr)
        if func.name != "constructor":
            bodies = tree.method_bodies.setdefault(cls.name, {})
            bodies[func.name] = bodies.get(func.name, "") + "\n" + func.body
    return tree
