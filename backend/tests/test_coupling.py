"""
Tests for the coupling metric.
"""

import tempfile
from pathlib import Path

from app.analyzer.metrics.coupling import calculate_coupling
from app.analyzer.parser import parse_file
from app.analyzer.scanner import SourceFile


def _parse(code: str, language: str, name: str):
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / name
        path.write_text(code)
        sf = SourceFile(
            absolute_path=path, relative_path=name, file_name=name,
            language=language, size_bytes=len(code),
        )
        return parse_file(sf)


def test_python_coupling_counts_distinct_top_level_imports():
    code = (
        "import os\n"
        "import sys\n"
        "from collections import OrderedDict\n"
        "from os.path import join\n"  # same top-level package as `import os`
    )
    module = _parse(code, "python", "t.py")
    # distinct top-level packages: os, sys, collections -> 3
    assert calculate_coupling(module) == 3


def test_python_no_imports_has_zero_coupling():
    module = _parse("x = 1\n", "python", "t.py")
    assert calculate_coupling(module) == 0


def test_java_coupling_counts_imports_and_inheritance():
    code = (
        "import java.util.List;\n"
        "import java.util.ArrayList;\n\n"
        "public class Foo extends BaseService implements Runnable {\n"
        "}\n"
    )
    module = _parse(code, "java", "Foo.java")
    # java.util.List, java.util.ArrayList, BaseService, Runnable -> 4
    assert calculate_coupling(module) == 4


def test_java_no_imports_no_inheritance_has_zero_coupling():
    code = "public class Foo {\n}\n"
    module = _parse(code, "java", "Foo.java")
    assert calculate_coupling(module) == 0
