"""
Tests for the scanner: source-file discovery and safe ZIP extraction.
"""

import zipfile
from pathlib import Path

import pytest

from app.analyzer.scanner import UnsafeZipError, discover_source_files, safe_extract_zip


def test_discovers_python_and_java_files(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "B.java").write_text("public class B {}\n")
    (tmp_path / "notes.txt").write_text("not source code\n")

    found = discover_source_files(tmp_path)
    languages = {f.language for f in found}
    names = {f.file_name for f in found}

    assert languages == {"python", "java"}
    assert names == {"a.py", "B.java"}


def test_ignores_configured_directories(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("x = 1\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "vendored.py").write_text("y = 2\n")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "cache.py").write_text("z = 3\n")

    found = discover_source_files(tmp_path)
    relative_paths = {f.relative_path for f in found}

    assert "src/main.py" in relative_paths
    assert not any("node_modules" in p for p in relative_paths)
    assert not any("__pycache__" in p for p in relative_paths)


def test_unsupported_extensions_are_skipped(tmp_path):
    (tmp_path / "readme.md").write_text("# hi\n")
    (tmp_path / "image.png").write_bytes(b"\x89PNG")
    found = discover_source_files(tmp_path)
    assert found == []


def test_safe_extract_valid_zip(tmp_path):
    zip_path = tmp_path / "project.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("main.py", "print('hi')\n")
        zf.writestr("sub/util.py", "def f(): pass\n")

    dest = tmp_path / "extracted"
    safe_extract_zip(zip_path, dest)

    assert (dest / "main.py").exists()
    assert (dest / "sub" / "util.py").exists()


def test_safe_extract_rejects_path_traversal(tmp_path):
    zip_path = tmp_path / "evil.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("../../etc/passwd", "malicious\n")

    dest = tmp_path / "extracted"
    with pytest.raises(UnsafeZipError):
        safe_extract_zip(zip_path, dest)


def test_safe_extract_rejects_absolute_path_entry(tmp_path):
    zip_path = tmp_path / "evil2.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("/etc/passwd", "malicious\n")

    dest = tmp_path / "extracted"
    with pytest.raises(UnsafeZipError):
        safe_extract_zip(zip_path, dest)


def test_safe_extract_rejects_non_zip_file(tmp_path):
    fake_zip = tmp_path / "not_a_zip.zip"
    fake_zip.write_text("this is just text, not a zip archive")

    dest = tmp_path / "extracted"
    with pytest.raises(UnsafeZipError):
        safe_extract_zip(fake_zip, dest)
