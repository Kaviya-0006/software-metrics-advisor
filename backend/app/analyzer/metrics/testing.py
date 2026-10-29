"""Static testing-signal analysis.

The analyzer never executes uploaded source code. When no real coverage report is
provided, the Testing category therefore uses a conservative *test-file
presence score* rather than pretending to be line or branch coverage.
"""
from __future__ import annotations

from pathlib import Path


def is_test_path(relative_path: str) -> bool:
    """Return True for common test-file and test-directory conventions."""
    parts = [p.lower() for p in Path(relative_path).parts]
    name = parts[-1] if parts else ""
    stem = Path(name).stem.lower()
    test_dirs = {"test", "tests", "__tests__", "spec", "specs"}
    if any(part in test_dirs for part in parts[:-1]):
        return True
    return (
        stem.startswith("test_")
        or stem.startswith("test-")
        or stem.endswith("_test")
        or stem.endswith("-test")
        or stem.endswith("_spec")
        or stem.endswith("-spec")
    )


def calculate_testing_score(relative_paths: list[str]) -> tuple[float | None, int, int]:
    """Return (score, test_files, production_files).

    Score is a simple, deterministic test-file presence proxy:
    one test source file for every production source file earns 100; the
    score is capped at 100. It is deliberately named a testing score, not
    code coverage, because source files are not executed.
    """
    if not relative_paths:
        return None, 0, 0
    test_files = sum(1 for path in relative_paths if is_test_path(path))
    production_files = len(relative_paths) - test_files
    if production_files <= 0:
        return 100.0, test_files, production_files
    score = min(100.0, (test_files / production_files) * 100.0)
    return round(score, 2), test_files, production_files
