"""
Tests for the parser: class, function/method, inheritance, and import
detection for both Python and Java.
"""

from pathlib import Path

from app.analyzer.parser import parse_file
from app.analyzer.scanner import SourceFile


def _source_file(tmp_path: Path, name: str, code: str, language: str) -> SourceFile:
    path = tmp_path / name
    path.write_text(code)
    return SourceFile(
        absolute_path=path, relative_path=name, file_name=name,
        language=language, size_bytes=len(code),
    )


def test_python_detects_classes_functions_and_inheritance(tmp_path):
    code = (
        "import os\n"
        "from collections import OrderedDict\n\n"
        "class Base:\n"
        "    def method_a(self):\n"
        "        pass\n\n"
        "class Child(Base):\n"
        "    def method_b(self):\n"
        "        pass\n\n"
        "def top_level_function():\n"
        "    pass\n"
    )
    sf = _source_file(tmp_path, "sample.py", code, "python")
    module = parse_file(sf)

    assert module.parse_error is None
    assert {c.name for c in module.classes} == {"Base", "Child"}
    child = next(c for c in module.classes if c.name == "Child")
    assert child.base_name == "Base"
    assert len(module.functions) == 1
    assert module.functions[0].name == "top_level_function"
    assert set(module.imports) == {"os", "collections"}


def test_python_syntax_error_does_not_raise(tmp_path):
    sf = _source_file(tmp_path, "broken.py", "def f(:\n    pass\n", "python")
    module = parse_file(sf)
    assert module.parse_error is not None
    assert module.classes == []


def test_java_detects_classes_methods_and_inheritance(tmp_path):
    code = (
        "package com.example;\n\n"
        "import java.util.List;\n\n"
        "public class Child extends Base implements Runnable {\n"
        "    private int x;\n"
        "    public void run() {}\n"
        "    public int getX() { return x; }\n"
        "}\n"
    )
    sf = _source_file(tmp_path, "Child.java", code, "java")
    module = parse_file(sf)

    assert module.parse_error is None
    assert len(module.classes) == 1
    cls = module.classes[0]
    assert cls.name == "Child"
    assert cls.base_name == "Base"
    assert cls.interface_names == ["Runnable"]
    assert set(cls.method_names) == {"run", "getX"}
    assert cls.field_names == ["x"]
    assert module.imports == ["java.util.List"]


def test_java_syntax_error_does_not_raise(tmp_path):
    sf = _source_file(tmp_path, "Broken.java", "public class {{{ broken\n", "java")
    module = parse_file(sf)
    assert module.parse_error is not None
    assert module.classes == []
