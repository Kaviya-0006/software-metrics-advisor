"""
Metric: Code Duplication.

Method used: **normalized-line, sliding-window block hashing**, computed
project-wide (duplication is a cross-file phenomenon — the same helper
copy-pasted into two files is exactly the kind of duplication this metric
should catch, so it cannot be computed one file at a time).

Algorithm:

1. For every source file, take its *code* lines only (blank and comment
   lines excluded — see analyzer/metrics/loc.py) and normalize each line
   by stripping leading/trailing whitespace and collapsing internal
   whitespace runs to a single space. This makes the comparison
   indentation- and formatting-insensitive.
2. Slide a window of `BLOCK_SIZE` consecutive normalized lines (default 6)
   over each file and hash each window's text.
3. Any window hash that occurs 2+ times *across the whole project* (in the
   same file or a different one) marks every line inside every occurrence
   of that window as "duplicated".
4. `duplicated_lines` (per file, and project total) = number of distinct
   code lines in that file marked duplicated by step 3.
5. `duplication_percentage` = duplicated_lines / total_code_lines * 100.

This is a standard, well-understood approach ("token/line-based clone
detection via rolling hash") appropriate for a student project — it is not
a full clone-detection tool (it does not do AST-based Type-2/Type-3 clone
detection, so renamed-variable duplicates that still line up textually are
caught, but structurally-identical code with reordered statements is not).
This limitation is documented here and in docs/metrics.md.

`BLOCK_SIZE = 6` is a configurable constant: too small (e.g. 2-3 lines) and
short, coincidentally-similar snippets (like `if x is None: return None`)
get flagged as "duplication"; too large and only near-identical large
functions get caught. 6 lines is a common default in academic and
industrial clone-detection tooling for this reason.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

BLOCK_SIZE = 6

_WHITESPACE_RE = re.compile(r"\s+")


@dataclass
class DuplicationResult:
    duplication_percentage: float
    duplicated_lines: int


def _normalize_line(line: str) -> str:
    return _WHITESPACE_RE.sub(" ", line.strip())


def _code_lines_only(source_text: str) -> list[str]:
    """
    Lightweight re-derivation of "non-blank, non-trivial" lines for
    duplication purposes. We intentionally don't require full comment
    stripping here (already-blank lines are filtered, and a comment-only
    line normalizes to something short and is very unlikely to coincide
    with five neighbouring lines elsewhere by chance) — this keeps the
    duplication pass independent of per-language LOC parsing, since it
    must run across files of both languages uniformly.
    """
    return [
        _normalize_line(line)
        for line in source_text.splitlines()
        if line.strip()
    ]


def calculate_project_duplication(
    file_sources: dict[str, str],
) -> tuple[DuplicationResult, dict[str, DuplicationResult]]:
    """
    Given {relative_file_path: source_text} for every scanned file in the
    project, return (project-level result, {file_path: per-file result}).
    """
    file_lines: dict[str, list[str]] = {
        path: _code_lines_only(text) for path, text in file_sources.items()
    }

    # window_hash -> list of (file_path, start_line_index)
    window_occurrences: dict[int, list[tuple[str, int]]] = {}

    for path, lines in file_lines.items():
        if len(lines) < BLOCK_SIZE:
            continue
        for start in range(len(lines) - BLOCK_SIZE + 1):
            window_text = "\n".join(lines[start:start + BLOCK_SIZE])
            window_hash = hash(window_text)
            window_occurrences.setdefault(window_hash, []).append((path, start))

    duplicated_line_indices: dict[str, set[int]] = {path: set() for path in file_lines}

    for occurrences in window_occurrences.values():
        if len(occurrences) < 2:
            continue
        for path, start in occurrences:
            for offset in range(BLOCK_SIZE):
                duplicated_line_indices[path].add(start + offset)

    per_file: dict[str, DuplicationResult] = {}
    total_code_lines = 0
    total_duplicated_lines = 0

    for path, lines in file_lines.items():
        code_line_count = len(lines)
        dup_count = len(duplicated_line_indices[path])
        percentage = round((dup_count / code_line_count) * 100, 2) if code_line_count else 0.0
        per_file[path] = DuplicationResult(
            duplication_percentage=percentage, duplicated_lines=dup_count
        )
        total_code_lines += code_line_count
        total_duplicated_lines += dup_count

    project_percentage = (
        round((total_duplicated_lines / total_code_lines) * 100, 2)
        if total_code_lines else 0.0
    )
    project_result = DuplicationResult(
        duplication_percentage=project_percentage,
        duplicated_lines=total_duplicated_lines,
    )

    return project_result, per_file
