"""
Metric: Cohesion (class-level).

Variant used: **LCOM1** (Chidamber & Kemerer's original Lack of Cohesion Of
Methods, 1991 definition), computed per class as:

    Let P = number of method pairs that share NO common instance field.
    Let Q = number of method pairs that share AT LEAST ONE common field.
    LCOM1 = max(P - Q, 0)

Interpretation: LCOM1 = 0 means the class's methods are, on balance, well
connected through shared state (good cohesion). Larger values indicate a
class doing multiple unrelated jobs (a God-class smell) — the field-access
patterns of its methods don't overlap.

We report this as `cohesion` per class, and as `null` (Python `None`) when
it cannot be reliably computed:

  - a class with fewer than 2 methods (no pairs to compare — nothing to
    measure "lack of shared state" between), or
  - a class with no instance fields at all (methods can't possibly share
    field access, so the number would be an artifact of the class having
    no state, not a real cohesion signal).

Module-level `cohesion` (used in the per-file metric result) is the
average of its classes' LCOM1 values (excluding classes for which cohesion
is null). A module with no classes, or with classes that all returned
null, reports module-level cohesion as null too.

Limitation: LCOM1 does not account for methods calling other methods of
the same class (a common way cohesion is also expressed) — it only looks
at direct field access, per the original definition. This is a known,
documented limitation of LCOM1 versus later variants (LCOM2-5).
"""

from __future__ import annotations

from itertools import combinations

from app.analyzer.parser import ParsedClass, ParsedModule


def _method_field_access_python(class_node, method_names: list[str]) -> dict[str, set[str]]:
    """
    For a Python ast.ClassDef, map each method name to the set of
    `self.<attr>` names it reads or writes anywhere in its body.
    """
    import ast

    access: dict[str, set[str]] = {name: set() for name in method_names}
    for node in class_node.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in access:
            fields: set[str] = set()
            for sub in ast.walk(node):
                if isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name):
                    if sub.value.id == "self":
                        fields.add(sub.attr)
            access[node.name] = fields
    return access


def _method_field_access_java(class_node, method_names: list[str]) -> dict[str, set[str]]:
    """
    For a javalang ClassDeclaration, map each method name to the set of
    this class's own field names it references in its body (matched by
    name against MemberReference nodes — this also naturally covers
    `this.<field>` usage, since javalang represents that as a `This` node
    wrapping a MemberReference selector, which the walk below visits too).
    """
    import javalang

    field_names = {d.name for f in class_node.fields for d in f.declarators}
    access: dict[str, set[str]] = {name: set() for name in method_names}

    for method in class_node.methods:
        if method.body is None or method.name not in access:
            continue
        used: set[str] = set()
        for _, node in method:
            if isinstance(node, javalang.tree.MemberReference) and node.member in field_names:
                used.add(node.member)
        access[method.name] = used

    return access


def _method_field_access_cfamily(tree, cls: ParsedClass) -> dict[str, set[str]]:
    """
    For a C++ class, map each method that has a body in this file to the set
    of the class's own fields its body mentions (whole-word match on the
    comment/string-free body; also covers `this->field`). Methods that are
    only declared here (body lives in another file) are left out rather
    than counted as touching nothing, which would fabricate low cohesion.
    A local variable shadowing a field name is a known over-count.
    """
    import re

    bodies = tree.method_bodies.get(cls.name, {})
    fields = set(cls.field_names)
    access: dict[str, set[str]] = {}
    for method in cls.method_names:
        body = bodies.get(method)
        if body is None:
            continue
        access[method] = {f for f in fields if re.search(rf"\b{re.escape(f)}\b", body)}
    return access


_THIS_ACCESS_RE = None


def _method_field_access_jsfamily(tree, cls: ParsedClass) -> dict[str, set[str]]:
    """
    For a JS/TS class, map each method that has a body to the set of the
    class's own fields it touches through `this.<field>`. Methods without a
    body (TS abstract/overload signatures) are left out rather than counted
    as touching nothing; interfaces have no bodies at all, so they come out
    as "not calculable" (None) instead of an invented number. The constructor
    is not a method here (as in Java/C++); its assignments are what declare
    the fields in plain JavaScript.
    """
    import re

    global _THIS_ACCESS_RE
    if _THIS_ACCESS_RE is None:
        _THIS_ACCESS_RE = re.compile(r"\bthis\s*\.\s*(#?[A-Za-z_$][\w$]*)")

    bodies = tree.method_bodies.get(cls.name, {})
    fields = set(cls.field_names)
    access: dict[str, set[str]] = {}
    for method in cls.method_names:
        body = bodies.get(method)
        if body is None:
            continue
        access[method] = {m.group(1) for m in _THIS_ACCESS_RE.finditer(body)} & fields
    return access


def _lcom1_from_access_map(access: dict[str, set[str]]) -> float | None:
    method_names = list(access.keys())
    if len(method_names) < 2:
        return None
    if not any(access.values()):
        return None  # no field access anywhere — nothing meaningful to compare

    shared_pairs = 0
    disjoint_pairs = 0
    for a, b in combinations(method_names, 2):
        if access[a] & access[b]:
            shared_pairs += 1
        else:
            disjoint_pairs += 1

    return float(max(disjoint_pairs - shared_pairs, 0))


def calculate_class_cohesion(module: ParsedModule, cls: ParsedClass) -> float | None:
    if len(cls.method_names) < 2 or not cls.field_names:
        return None

    if module.language == "python":
        import ast

        class_node = next(
            (n for n in ast.walk(module.raw_tree) if isinstance(n, ast.ClassDef) and n.name == cls.name),
            None,
        )
        if class_node is None:
            return None
        access = _method_field_access_python(class_node, cls.method_names)

    elif module.language == "java":
        import javalang

        class_node = next(
            (n for _, n in module.raw_tree.filter(javalang.tree.ClassDeclaration) if n.name == cls.name),
            None,
        )
        if class_node is None:
            return None
        access = _method_field_access_java(class_node, cls.method_names)

    elif module.language in ("c", "cpp"):
        access = _method_field_access_cfamily(module.raw_tree, cls)

    elif module.language in ("javascript", "typescript"):
        access = _method_field_access_jsfamily(module.raw_tree, cls)

    else:
        return None

    return _lcom1_from_access_map(access)


def calculate_module_cohesion(module: ParsedModule) -> float | None:
    """Average LCOM1 across the module's classes; None if there are no
    classes or none of them could be scored."""
    values = [
        v for v in (calculate_class_cohesion(module, cls) for cls in module.classes)
        if v is not None
    ]
    if not values:
        return None
    return round(sum(values) / len(values), 2)
