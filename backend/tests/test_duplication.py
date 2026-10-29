"""
Tests for the block-hash duplication metric.
"""

from app.analyzer.metrics.duplication import BLOCK_SIZE, calculate_project_duplication


def test_no_duplication_across_distinct_files():
    files = {
        "a.py": "\n".join(f"a_line_{i}" for i in range(10)),
        "b.py": "\n".join(f"b_line_{i}" for i in range(10)),
    }
    result, per_file = calculate_project_duplication(files)
    assert result.duplication_percentage == 0.0
    assert result.duplicated_lines == 0
    assert per_file["a.py"].duplication_percentage == 0.0


def test_shared_block_across_two_files_is_detected():
    shared = "\n".join(f"shared_{i}" for i in range(BLOCK_SIZE))
    files = {
        "a.py": shared + "\nonly_in_a\n",
        "b.py": shared + "\nonly_in_b\n",
    }
    result, per_file = calculate_project_duplication(files)
    assert result.duplicated_lines == BLOCK_SIZE * 2  # counted in both files
    assert per_file["a.py"].duplicated_lines == BLOCK_SIZE
    assert per_file["b.py"].duplicated_lines == BLOCK_SIZE
    assert per_file["a.py"].duplication_percentage > 0.0


def test_duplication_within_a_single_file_is_detected():
    shared = "\n".join(f"shared_{i}" for i in range(BLOCK_SIZE))
    files = {"a.py": shared + "\nunique\n" + shared + "\n"}
    result, per_file = calculate_project_duplication(files)
    assert per_file["a.py"].duplicated_lines == BLOCK_SIZE * 2


def test_file_shorter_than_block_size_has_no_duplication():
    files = {"a.py": "one\ntwo\n"}
    result, per_file = calculate_project_duplication(files)
    assert per_file["a.py"].duplicated_lines == 0


def test_indentation_differences_are_normalized():
    # Same logical lines, different indentation — should still match.
    block_a = "\n".join(f"    if x_{i}:" for i in range(BLOCK_SIZE))
    block_b = "\n".join(f"if x_{i}:" for i in range(BLOCK_SIZE))
    files = {"a.py": block_a, "b.py": block_b}
    result, per_file = calculate_project_duplication(files)
    assert result.duplicated_lines == BLOCK_SIZE * 2
