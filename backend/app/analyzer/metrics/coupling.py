"""
Metric: Coupling.

IMPORTANT — operational definition (read this before citing "coupling" in
the viva):

This project measures a *static, import/reference-based coupling count*,
NOT the full Chidamber & Kemerer "Coupling Between Objects" (CBO) metric.
True CBO requires resolving every type actually *used* in a class body
(field types, parameter types, return types, local variable types, cast
expressions, method calls on other objects, etc.) against a fully-built
project type model — that requires a real compiler/type-resolution pass,
which is out of scope for this static, single-file-at-a-time analyzer.

What we actually compute, per module:

    Python:  the number of distinct top-level modules imported by the file
             (from `import x` / `from x import y`, counting the root
             package name once). This approximates Martin's "efferent
             coupling" (Ce) — how many other things this module depends on.

    Java:    the number of distinct external types referenced by the file:
             the union of (a) imported type names and (b) type names
             appearing as a class's `extends`/`implements` clause. This is
             closer in spirit to CBO than the Python count (it captures
             inheritance-based coupling too) but still does not resolve
             field/parameter/local-variable types, so it undercounts true
             CBO for classes that reference other project types only
             through method signatures or local variables without an
             import (e.g. same-package classes).

Both are returned as a single integer per module: `coupling`. This is
intentionally simple and cheap to compute, and its limitations are
documented here and in docs/metrics.md so the number is never presented as
more precise than it is.
"""

from __future__ import annotations

from pathlib import PurePosixPath

from app.analyzer.parser import ParsedModule


def calculate_python_coupling(module: ParsedModule) -> int:
    """Efferent coupling approximation: count of distinct imported
    top-level modules/packages."""
    return len(set(module.imports))


def calculate_java_coupling(module: ParsedModule) -> int:
    """Coupling approximation: distinct imported types unioned with
    distinct extends/implements targets referenced in this file."""
    referenced: set[str] = set(module.imports)
    for cls in module.classes:
        if cls.base_name:
            referenced.add(cls.base_name)
        referenced.update(cls.interface_names)
    return len(referenced)


def calculate_c_family_coupling(module: ParsedModule) -> int:
    """C/C++ coupling approximation, in the same spirit as Java: distinct
    `#include` targets unioned with distinct base classes. A source file's
    own header (foo.c -> foo.h) is not counted as a dependency, since it is
    the file's own interface rather than another module. Function calls are
    deliberately NOT counted: without a type/symbol table they cannot be
    told apart from library calls (printf, malloc...), and would double
    count what the includes already express."""
    own_stem = PurePosixPath(module.source_file.file_name).stem.lower()
    referenced: set[str] = {
        inc for inc in module.imports
        if PurePosixPath(inc).stem.lower() != own_stem
    }
    for cls in module.classes:
        if cls.base_name:
            referenced.add(cls.base_name)
        referenced.update(cls.interface_names)
    return len(referenced)


def calculate_js_family_coupling(module: ParsedModule) -> int:
    """JS/TS coupling approximation: distinct module specifiers (`import`,
    `require`, dynamic `import()`, `export ... from`) unioned with inheritance
    bases (`extends`, TS `implements`, interface `extends`). A base that is
    itself an imported binding is NOT counted again: its import already
    represents that dependency. Function calls are not counted — without type
    information they cannot be told apart from local or library calls."""
    bindings = getattr(module.raw_tree, "import_bindings", set())
    referenced: set[str] = set(module.imports)
    for cls in module.classes:
        for base in ([cls.base_name] if cls.base_name else []) + list(cls.interface_names):
            if base not in bindings:
                referenced.add(base)
    return len(referenced)


def calculate_coupling(module: ParsedModule) -> int:
    if module.language == "python":
        return calculate_python_coupling(module)
    if module.language == "java":
        return calculate_java_coupling(module)
    if module.language in ("c", "cpp"):
        return calculate_c_family_coupling(module)
    if module.language in ("javascript", "typescript"):
        return calculate_js_family_coupling(module)
    raise ValueError(f"Unsupported language for coupling: {module.language}")
