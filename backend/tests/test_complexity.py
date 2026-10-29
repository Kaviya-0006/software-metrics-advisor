"""
Tests for the cyclomatic complexity metric.

Each sample function/method below has a decision count that was worked out
by hand (see comments), so these assert exact expected values rather than
just "some number > 0" — the point of the test is to catch a wrong formula,
not just a crash.
"""

from app.analyzer.metrics.complexity import (
    calculate_java_complexity,
    calculate_python_complexity,
)
import javalang


def test_python_complexity_no_branches_is_one():
    result = calculate_python_complexity(
        "def f():\n    return 1\n"
    )
    assert result.per_function[0].complexity == 1


def test_python_complexity_known_branch_count():
    # decisions: if, for, inner if, "or" = 4 -> CC = 5
    code = (
        "def f(x):\n"
        "    if x > 0:\n"
        "        for i in range(x):\n"
        "            if i % 2 == 0 or i == 1:\n"
        "                print(i)\n"
        "    return x\n"
    )
    result = calculate_python_complexity(code)
    assert result.per_function[0].complexity == 5
    assert result.total == 5
    assert result.maximum == 5


def test_python_complexity_module_aggregates_across_functions():
    code = (
        "def a():\n    return 1\n\n"
        "def b(x):\n    if x:\n        return 1\n    return 0\n"
    )
    result = calculate_python_complexity(code)
    assert result.total == 1 + 2  # a=1, b=2
    assert result.average == 1.5
    assert result.maximum == 2


def test_java_complexity_no_branches_is_one():
    code = (
        "public class Foo {\n"
        "    public void f() { System.out.println(1); }\n"
        "}\n"
    )
    tree = javalang.parse.parse(code)
    result = calculate_java_complexity(tree)
    assert result.per_function[0].complexity == 1


def test_java_complexity_known_branch_count():
    # decisions: if, for, inner if, "||" = 4 -> CC = 5
    code = (
        "public class Foo {\n"
        "    public int f(int x) {\n"
        "        if (x > 0) {\n"
        "            for (int i = 0; i < x; i++) {\n"
        "                if (i % 2 == 0 || i == 1) {\n"
        "                    System.out.println(i);\n"
        "                }\n"
        "            }\n"
        "        }\n"
        "        return x;\n"
        "    }\n"
        "}\n"
    )
    tree = javalang.parse.parse(code)
    result = calculate_java_complexity(tree)
    assert result.per_function[0].complexity == 5


def test_java_complexity_switch_case_excludes_default():
    # decisions: 2 non-default case labels -> CC = 1 + 2 = 3
    code = (
        "public class Foo {\n"
        "    public int f(int x) {\n"
        "        switch (x) {\n"
        "            case 1: break;\n"
        "            case 2: break;\n"
        "            default: break;\n"
        "        }\n"
        "        return x;\n"
        "    }\n"
        "}\n"
    )
    tree = javalang.parse.parse(code)
    result = calculate_java_complexity(tree)
    assert result.per_function[0].complexity == 3


def test_java_complexity_module_with_no_methods_is_empty():
    code = "public interface Empty {}\n"
    tree = javalang.parse.parse(code)
    result = calculate_java_complexity(tree)
    assert result.total == 0
    assert result.per_function == []
