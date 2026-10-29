"""
Metric: Depth of Inheritance (Depth of Inheritance Tree / DIT).

Definition: for a class C, DIT(C) = the number of ancestor classes from C
up to its root base class, walking `extends` links. A class with no base
has DIT = 0. This is the classic Chidamber & Kemerer DIT metric.

Because a single uploaded project can span many files, DIT must be
computed against a *project-wide* registry of class name -> base class
name, built once after all files are parsed — not per file in isolation
(a subclass and its base are very often in different files).

Only single inheritance is tracked (`base_name`, the primary/first base —
see parser.py). This matches Java exactly (Java classes have exactly one
superclass). For Python, which supports multiple inheritance, this is a
documented simplification: DIT here follows the first base class only, so
a project using significant multiple inheritance will have DIT
under-measured for the classes that inherit from a second/third base.

If a class's base is not itself a class defined anywhere in the uploaded
project (e.g. it extends a standard-library or third-party class we never
parsed), we still count it as one level of depth — we know inheritance
exists, we just can't see further up the chain. This is a partial-depth
result, not a wrong one: the value is a lower bound on true DIT.

Cycle safety: a malformed or adversarial project could declare `class A
extends B` and `class B extends A`. We track visited class names while
walking up and stop (returning the depth reached so far) if a cycle is
detected, so DIT calculation can never infinite-loop.
"""

from __future__ import annotations

from app.analyzer.parser import ParsedModule

ClassRegistry = dict[str, str | None]  # class_name -> base_class_name (or None)


def build_class_registry(modules: list[ParsedModule]) -> ClassRegistry:
    """Build a project-wide map of class name -> its direct base class
    name, from every class across every parsed module."""
    registry: ClassRegistry = {}
    for module in modules:
        for cls in module.classes:
            # If the same class name appears in multiple files (unusual,
            # but possible in a messy upload), first definition wins —
            # documented as a known edge case rather than silently
            # merging conflicting hierarchies.
            registry.setdefault(cls.name, cls.base_name)
    return registry


def calculate_class_dit(class_name: str, registry: ClassRegistry) -> int:
    """Depth of Inheritance Tree for one class name, walking the registry."""
    depth = 0
    current = class_name
    visited: set[str] = set()

    while True:
        if current in visited:
            break  # cycle detected — stop, return depth reached so far
        visited.add(current)

        base = registry.get(current)
        if base is None:
            break  # no further (known or unknown) base — chain ends here

        depth += 1
        if base not in registry:
            # Base exists (we saw an extends clause) but isn't a class we
            # parsed ourselves (external/stdlib/third-party) — count this
            # one level and stop; we have no further information.
            break

        current = base

    return depth
