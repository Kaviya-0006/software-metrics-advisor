"""
Source-code parsing.

Uses real parsers per language, not regex approximations:

- Python: the standard-library `ast` module.
- Java: `javalang`, a pure-Python Java parser (no JVM required).
- C / C++: the dependency-free lexer + structure scanner in cfamily.py (a
  documented static approximation, not a full C++ front end).
- JavaScript / TypeScript: the dependency-free lexer + structure scanner in
  jsfamily.py (same approach: a documented static approximation).

All parsers feed into the same ParsedModule / ParsedClass / ParsedMethod
shape so the metric calculators in analyzer/metrics/ don't need to know
which language produced the data — only the metrics that genuinely differ
per language (e.g. complexity, Halstead tokens) branch on `language`.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

import javalang

from app.analyzer.cfamily import CFamilyParseError, parse_c_family_source
from app.analyzer.jsfamily import JsFamilyParseError, parse_js_family_source
from app.analyzer.scanner import SourceFile


@dataclass
class ParsedMethod:
    name: str
    is_method: bool  # False for a module-level/free function (Python only)
    owner_class: str | None = None


@dataclass
class ParsedClass:
    name: str
    # Name of the direct base class, if statically determinable. Only the
    # first/primary base is tracked — sufficient for a single-inheritance
    # Depth-of-Inheritance-Tree calculation (Java is single-inheritance for
    # classes; for Python multiple inheritance we track the first base,
    # which is documented as a known simplification in docs/metrics.md).
    base_name: str | None
    # Interfaces implemented (Java `implements` clause). Always empty for
    # Python, which has no separate interface construct.
    interface_names: list[str] = field(default_factory=list)
    method_names: list[str] = field(default_factory=list)
    field_names: list[str] = field(default_factory=list)


@dataclass
class ParsedModule:
    source_file: SourceFile
    language: str
    source_text: str
    classes: list[ParsedClass] = field(default_factory=list)
    functions: list[ParsedMethod] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    parse_error: str | None = None

    # Raw AST kept for language-specific metric calculators that need to
    # re-walk the tree (complexity, cohesion). Not serialized to the API.
    raw_tree: object = None


class ParseError(Exception):
    pass


def _parse_python(source_file: SourceFile, source_text: str) -> ParsedModule:
    tree = ast.parse(source_text, filename=source_file.file_name)

    classes: list[ParsedClass] = []
    functions: list[ParsedMethod] = []
    imports: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            base_name = None
            if node.bases:
                base = node.bases[0]
                base_name = base.id if isinstance(base, ast.Name) else (
                    base.attr if isinstance(base, ast.Attribute) else None
                )

            method_names = [
                n.name for n in node.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
            field_names = _python_self_assigned_fields(node)

            classes.append(
                ParsedClass(
                    name=node.name,
                    base_name=base_name,
                    method_names=method_names,
                    field_names=field_names,
                )
            )

        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # Only count top-level (module-scope) functions here; methods
            # are already captured via their owning ClassDef above. This
            # avoids double-counting methods as free functions.
            pass

        elif isinstance(node, ast.Import):
            imports.extend(alias.name.split(".")[0] for alias in node.names)

        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module.split(".")[0])

    # Top-level functions = direct children of the Module node only.
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append(ParsedMethod(name=node.name, is_method=False))

    return ParsedModule(
        source_file=source_file,
        language="python",
        source_text=source_text,
        classes=classes,
        functions=functions,
        imports=sorted(set(imports)),
        raw_tree=tree,
    )


def _python_self_assigned_fields(class_node: ast.ClassDef) -> list[str]:
    """
    Collect instance-attribute names assigned as `self.x = ...` anywhere in
    the class body. Used by the cohesion (LCOM) calculation.
    """
    fields: set[str] = set()
    for node in ast.walk(class_node):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id == "self" and isinstance(node.ctx, (ast.Store, ast.Load)):
                fields.add(node.attr)
    return sorted(fields)


def _parse_java(source_file: SourceFile, source_text: str) -> ParsedModule:
    tree = javalang.parse.parse(source_text)

    classes: list[ParsedClass] = []
    imports = [imp.path for imp in tree.imports] if tree.imports else []

    for _, node in tree.filter(javalang.tree.ClassDeclaration):
        base_name = node.extends.name if node.extends else None
        interface_names = [i.name for i in node.implements] if node.implements else []
        method_names = [m.name for m in node.methods]
        field_names: list[str] = []
        for f in node.fields:
            field_names.extend(d.name for d in f.declarators)

        classes.append(
            ParsedClass(
                name=node.name,
                base_name=base_name,
                interface_names=interface_names,
                method_names=method_names,
                field_names=field_names,
            )
        )

    # Java has no free (non-method) functions — every method belongs to a
    # class or interface, so `functions` stays empty for Java modules.
    return ParsedModule(
        source_file=source_file,
        language="java",
        source_text=source_text,
        classes=classes,
        functions=[],
        imports=sorted(set(imports)),
        raw_tree=tree,
    )


def _parse_c_family(source_file: SourceFile, source_text: str) -> ParsedModule:
    """C (`.c`, C-style `.h`) and C++ (`.cpp/.cc/.cxx/.hpp`, C++-style `.h`).

    Mapping onto the shared shape:
    - class / struct / union definitions -> ParsedClass (`base_name` is the
      first base class, further bases go to `interface_names`)
    - free (non-member) function definitions -> `functions`; member
      functions are listed on their class
    - `#include` targets -> `imports`
    The full CFamilyTree (namespaces, per-function bodies and calls) stays
    on `raw_tree` for the metric calculators.
    """
    tree = parse_c_family_source(source_text, source_file.language)

    classes = [
        ParsedClass(
            name=c.name,
            base_name=c.bases[0] if c.bases else None,
            interface_names=list(c.bases[1:]),
            method_names=list(c.methods),
            field_names=list(c.fields),
        )
        for c in tree.classes
    ]
    functions = [
        ParsedMethod(name=f.name, is_method=False)
        for f in tree.functions
        if f.owner_class is None
    ]
    return ParsedModule(
        source_file=source_file,
        language=source_file.language,
        source_text=source_text,
        classes=classes,
        functions=functions,
        imports=list(tree.includes),
        raw_tree=tree,
    )


def uses_jsx(file_name: str) -> bool:
    """JSX is legal in `.js`, `.jsx` and `.tsx`; plain `.ts` has none (there
    `<T>x` is a type assertion), so JSX recognition is switched off for it."""
    return file_name.lower().endswith((".js", ".jsx", ".tsx"))


def _parse_js_family(source_file: SourceFile, source_text: str) -> ParsedModule:
    """JavaScript (`.js`, `.jsx`) and TypeScript (`.ts`, `.tsx`).

    Mapping onto the shared shape:
    - class and TS interface declarations -> ParsedClass (`base_name` is the
      first `extends`; further `extends` entries of an interface and any TS
      `implements` go to `interface_names`)
    - free functions, arrow functions and callbacks -> `functions`; class
      methods are listed on their class (the constructor is a function
      definition but, as in Java/C++, not a method name)
    - import / require targets -> `imports`
    The full JsFamilyTree stays on `raw_tree` for the metric calculators.
    """
    tree = parse_js_family_source(
        source_text,
        source_file.language,
        uses_jsx(source_file.file_name),
        declaration=source_file.file_name.lower().endswith(".d.ts"),
    )
    classes = [
        ParsedClass(
            name=c.name,
            base_name=c.bases[0] if c.bases else None,
            interface_names=list(c.bases[1:]) + list(c.implements),
            method_names=list(c.methods),
            field_names=list(c.fields),
        )
        for c in tree.classes
    ]
    functions = [
        ParsedMethod(name=f.name, is_method=False)
        for f in tree.functions
        if f.owner_class is None
    ]
    return ParsedModule(
        source_file=source_file,
        language=source_file.language,
        source_text=source_text,
        classes=classes,
        functions=functions,
        imports=list(tree.imports),
        raw_tree=tree,
    )


def parse_file(source_file: SourceFile) -> ParsedModule:
    """
    Parse a single discovered source file. Never raises — a file that fails
    to parse (syntax error, encoding issue) comes back as a ParsedModule
    with `parse_error` set and empty metric inputs, so one bad file can't
    take down the whole project analysis.
    """
    try:
        source_text = source_file.absolute_path.read_text(
            encoding="utf-8", errors="replace"
        )
    except OSError as exc:
        return ParsedModule(
            source_file=source_file,
            language=source_file.language,
            source_text="",
            parse_error=f"Could not read file: {exc}",
        )

    try:
        if source_file.language == "python":
            return _parse_python(source_file, source_text)
        elif source_file.language == "java":
            return _parse_java(source_file, source_text)
        elif source_file.language in ("c", "cpp"):
            return _parse_c_family(source_file, source_text)
        elif source_file.language in ("javascript", "typescript"):
            return _parse_js_family(source_file, source_text)
        else:
            return ParsedModule(
                source_file=source_file,
                language=source_file.language,
                source_text=source_text,
                parse_error=f"Unsupported language: {source_file.language}",
            )
    except (
        SyntaxError,
        javalang.parser.JavaSyntaxError,
        javalang.tokenizer.LexerError,
        CFamilyParseError,
        JsFamilyParseError,
    ) as exc:
        return ParsedModule(
            source_file=source_file,
            language=source_file.language,
            source_text=source_text,
            parse_error=f"Parse error: {exc}",
        )
