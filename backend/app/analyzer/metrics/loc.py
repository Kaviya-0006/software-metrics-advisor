"""
Metric: Lines of Code (LOC).

Definition used here (see docs/metrics.md for the full writeup):

    total_lines   = every physical line in the file
    blank_lines   = lines containing only whitespace
    comment_lines = lines that are entirely a comment (single-line comment
                    markers, or lines fully inside a block/docstring
                    comment)
    code_lines    = total_lines - blank_lines - comment_lines

This deliberately does NOT use file length / newline count as a stand-in
for "LOC" — a blank-heavy or heavily-commented file should not score as
having as much real code as a dense one.

Python uses radon's raw-metrics analyzer (`radon.raw.analyze`), which
already implements this classification correctly against the Python
grammar (including triple-quoted docstrings). Java has no equivalent
lightweight library in this stack, so we implement an explicit state-
machine line classifier for Java's comment styles (`//`, `/* */`).
"""

from __future__ import annotations

from dataclasses import dataclass

from radon.raw import analyze as radon_raw_analyze

from app.analyzer.cfamily import lex_source
from app.analyzer.jsfamily import lex_source as js_lex_source


@dataclass
class LocResult:
    total: int
    code: int
    comments: int
    blank: int


def calculate_python_loc(source_text: str) -> LocResult:
    raw = radon_raw_analyze(source_text)
    # radon.raw.analyze splits physical lines into: sloc (source/code),
    # comments (comment-only lines), blank, and multi (lines that are part
    # of a multi-line/triple-quoted string, which may or may not be a
    # docstring — radon can't reliably tell the two apart). We count
    # `multi` lines as code so that total == code + comments + blank holds
    # exactly; this is a documented approximation for triple-quoted
    # docstrings (see docs/metrics.md).
    return LocResult(
        total=raw.loc,
        code=raw.sloc + raw.multi,
        comments=raw.comments,
        blank=raw.blank,
    )


def calculate_java_loc(source_text: str) -> LocResult:
    lines = source_text.splitlines()
    total = len(lines)
    blank = 0
    comment = 0
    code = 0

    in_block_comment = False

    for raw_line in lines:
        stripped = raw_line.strip()

        if not stripped and not in_block_comment:
            blank += 1
            continue

        if in_block_comment:
            comment += 1
            if "*/" in stripped:
                in_block_comment = False
                # If there's real code after the closing marker on the same
                # line, this simplified classifier still counts the whole
                # line as a comment line — a documented approximation.
            continue

        if stripped.startswith("//"):
            comment += 1
            continue

        if stripped.startswith("/*"):
            comment += 1
            if "*/" not in stripped:
                in_block_comment = True
            continue

        code += 1

    return LocResult(total=total, code=code, comments=comment, blank=blank)


def calculate_c_family_loc(source_text: str) -> LocResult:
    """C/C++ LOC using the real lexer pass (cfamily.lex_source), so comment
    markers inside string/char literals are never treated as comments.

    - blank:   whitespace-only line that is not inside a block comment
    - comment: a line that contains comment text and no code outside it
               (`// x`, `/* x */`, and every line inside a multi-line block
               comment, including blank ones — same convention as Java)
    - code:    everything else, including preprocessor directives and a
               line with code plus a trailing comment
    """
    return _loc_from_lexed(lex_source(source_text))


def _loc_from_lexed(lexed) -> LocResult:
    """Shared classifier for the lexer-based languages (C/C++ and JS/TS).
    `lexed` provides `.lines`, `.comment_lines` and `.no_comments`."""
    without_comments = lexed.no_comments.split("\n")
    blank = comment = code = 0

    for idx, raw_line in enumerate(lexed.lines):
        in_comment = idx in lexed.comment_lines
        if not raw_line.strip() and not in_comment:
            blank += 1
        elif in_comment and (idx >= len(without_comments) or not without_comments[idx].strip()):
            comment += 1
        else:
            code += 1

    return LocResult(total=len(lexed.lines), code=code, comments=comment, blank=blank)


def calculate_js_family_loc(source_text: str, jsx: bool = True) -> LocResult:
    """JS/TS LOC with the same conventions as C/C++. The JS lexer knows
    template literals (multi-line text stays code, `//` inside stays code),
    regex literals and JSX text, so none of them can be mistaken for comments."""
    return _loc_from_lexed(js_lex_source(source_text, jsx=jsx))


def calculate_loc(language: str, source_text: str, jsx: bool = True) -> LocResult:
    if language == "python":
        return calculate_python_loc(source_text)
    if language == "java":
        return calculate_java_loc(source_text)
    if language in ("c", "cpp"):
        return calculate_c_family_loc(source_text)
    if language in ("javascript", "typescript"):
        return calculate_js_family_loc(source_text, jsx=jsx)
    raise ValueError(f"Unsupported language for LOC: {language}")
