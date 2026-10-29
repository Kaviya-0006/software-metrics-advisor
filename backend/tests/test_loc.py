"""
Tests for the LOC metric (blank / comment / code line classification).
"""

from app.analyzer.metrics.loc import calculate_java_loc, calculate_python_loc


def test_python_loc_classifies_blank_comment_code():
    code = (
        "# a comment\n"
        "\n"
        "import os\n"
        "\n"
        "x = 1\n"
    )
    result = calculate_python_loc(code)
    assert result.total == 5
    assert result.blank == 2
    assert result.comments == 1
    assert result.code == 2
    assert result.total == result.blank + result.comments + result.code


def test_python_loc_never_equals_raw_line_count_for_commented_files():
    code = "# only comments\n# more comments\n# even more\n"
    result = calculate_python_loc(code)
    assert result.code == 0
    assert result.comments == 3


def test_java_loc_single_line_comments():
    code = (
        "// header comment\n"
        "\n"
        "public class Foo {\n"
        "    int x = 1; // trailing note counted as code here\n"
        "}\n"
    )
    result = calculate_java_loc(code)
    assert result.total == 5
    assert result.blank == 1
    assert result.comments == 1  # only the pure comment line
    assert result.code == 3


def test_java_loc_block_comments_span_multiple_lines():
    code = (
        "/*\n"
        " * block comment\n"
        " * still going\n"
        " */\n"
        "public class Foo {}\n"
    )
    result = calculate_java_loc(code)
    assert result.comments == 4
    assert result.code == 1
    assert result.total == result.blank + result.comments + result.code


def test_java_loc_single_line_block_comment():
    code = "/* one line block */\npublic class Foo {}\n"
    result = calculate_java_loc(code)
    assert result.comments == 1
    assert result.code == 1
