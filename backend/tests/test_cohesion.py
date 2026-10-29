"""
Tests for the cohesion (LCOM1) metric.

Note: LCOM1 has a known, documented weakness — a constructor that touches
every field creates a "shared field access" link between every method
pair, which can mask genuinely poor cohesion elsewhere in the class. The
non-cohesive test case below deliberately avoids a shared constructor so
the disjoint-field-access pattern is actually visible, matching the
limitation documented in cohesion.py and docs/metrics.md.
"""

import tempfile
from pathlib import Path

from app.analyzer.metrics.cohesion import calculate_class_cohesion, calculate_module_cohesion
from app.analyzer.parser import parse_file
from app.analyzer.scanner import SourceFile


def _parse_python(code: str, name: str = "t.py"):
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / name
        path.write_text(code)
        sf = SourceFile(
            absolute_path=path, relative_path=name, file_name=name,
            language="python", size_bytes=len(code),
        )
        return parse_file(sf)


def _parse_java(code: str, name: str = "T.java"):
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / name
        path.write_text(code)
        sf = SourceFile(
            absolute_path=path, relative_path=name, file_name=name,
            language="java", size_bytes=len(code),
        )
        return parse_file(sf)


def test_python_cohesive_class_scores_zero():
    code = (
        "class Counter:\n"
        "    def __init__(self):\n"
        "        self.count = 0\n"
        "        self.step = 1\n\n"
        "    def increment(self):\n"
        "        self.count += self.step\n\n"
        "    def reset(self):\n"
        "        self.count = 0\n"
    )
    module = _parse_python(code)
    assert calculate_class_cohesion(module, module.classes[0]) == 0.0


def test_python_noncohesive_class_scores_above_zero():
    code = (
        "class Mixed:\n"
        "    def do_a(self):\n        self.a = 1\n\n"
        "    def more_a(self):\n        self.a += 1\n\n"
        "    def do_b(self):\n        self.b = 1\n\n"
        "    def more_b(self):\n        self.b += 1\n"
    )
    module = _parse_python(code)
    assert calculate_class_cohesion(module, module.classes[0]) > 0.0


def test_python_class_with_no_fields_returns_none():
    code = (
        "class Stateless:\n"
        "    def a(self):\n        return 1\n\n"
        "    def b(self):\n        return 2\n"
    )
    module = _parse_python(code)
    assert calculate_class_cohesion(module, module.classes[0]) is None


def test_python_class_with_one_method_returns_none():
    code = "class Solo:\n    def only(self):\n        self.x = 1\n"
    module = _parse_python(code)
    assert calculate_class_cohesion(module, module.classes[0]) is None


def test_java_cohesive_class_scores_zero():
    code = (
        "public class Counter {\n"
        "    private int count;\n"
        "    private int step;\n\n"
        "    public void increment() { count += step; }\n"
        "    public void reset() { count = 0; }\n"
        "}\n"
    )
    module = _parse_java(code)
    assert calculate_class_cohesion(module, module.classes[0]) == 0.0


def test_java_noncohesive_class_scores_above_zero():
    code = (
        "public class Mixed {\n"
        "    private int a;\n"
        "    private int b;\n\n"
        "    public void doA() { a = 1; }\n"
        "    public void moreA() { a += 1; }\n"
        "    public void doB() { b = 1; }\n"
        "    public void moreB() { b += 1; }\n"
        "}\n"
    )
    module = _parse_java(code)
    assert calculate_class_cohesion(module, module.classes[0]) > 0.0


def test_module_cohesion_is_none_when_no_classes():
    code = "def free_function():\n    return 1\n"
    module = _parse_python(code)
    assert calculate_module_cohesion(module) is None
