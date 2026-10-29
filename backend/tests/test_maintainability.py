"""
Tests for the Maintainability Index metric.

Exact MI values are sensitive to floating-point formula details, so these
tests check documented properties (range, monotonic response to
complexity) rather than pinning exact numbers — except where radon's own
computation gives us a stable reference to compare the Java path against.
"""

import javalang

from app.analyzer.metrics.complexity import calculate_java_complexity
from app.analyzer.metrics.loc import calculate_java_loc
from app.analyzer.metrics.maintainability import (
    calculate_java_maintainability,
    calculate_python_maintainability,
)


def test_python_mi_in_valid_range():
    code = "def f(x):\n    if x:\n        return 1\n    return 0\n"
    mi = calculate_python_maintainability(code)
    assert 0.0 <= mi <= 100.0


def test_python_mi_decreases_as_complexity_increases():
    simple = "def f():\n    return 1\n"
    branchy = (
        "def f(x):\n"
        "    if x > 0:\n"
        "        for i in range(x):\n"
        "            if i % 2 == 0:\n"
        "                if i > 10:\n"
        "                    print(i)\n"
        "    return x\n"
    )
    mi_simple = calculate_python_maintainability(simple)
    mi_branchy = calculate_python_maintainability(branchy)
    assert mi_simple > mi_branchy


def _java_mi(source: str) -> float:
    loc = calculate_java_loc(source)
    tree = javalang.parse.parse(source)
    cc = calculate_java_complexity(tree)
    return calculate_java_maintainability(source, cc.total, loc)


def test_java_mi_in_valid_range():
    code = (
        "public class Foo {\n"
        "    public int f(int x) {\n"
        "        if (x > 0) { return 1; }\n"
        "        return 0;\n"
        "    }\n"
        "}\n"
    )
    assert 0.0 <= _java_mi(code) <= 100.0


def test_java_mi_decreases_as_complexity_increases():
    simple = (
        "public class Foo {\n"
        "    public int f() { return 1; }\n"
        "}\n"
    )
    branchy = (
        "public class Foo {\n"
        "    public int f(int x) {\n"
        "        if (x > 0) {\n"
        "            for (int i = 0; i < x; i++) {\n"
        "                if (i % 2 == 0) {\n"
        "                    if (i > 10) { System.out.println(i); }\n"
        "                }\n"
        "            }\n"
        "        }\n"
        "        return x;\n"
        "    }\n"
        "}\n"
    )
    assert _java_mi(simple) > _java_mi(branchy)


def test_java_mi_on_trivial_empty_class_is_high():
    code = "public class Empty {\n}\n"
    assert _java_mi(code) > 50.0
