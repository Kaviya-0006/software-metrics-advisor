"""
Tests for C support (`.c`, C-style `.h`): detection, parsing, the shared
metric pipeline, scoring/risk, and the existing upload workflow.
"""

import io
import zipfile
from pathlib import Path

from app.analyzer.engine import analyze_project
from app.analyzer.metrics.loc import calculate_loc
from app.analyzer.parser import parse_file
from app.analyzer.risk import calculate_module_risk, rank_risky_modules
from app.analyzer.scanner import SourceFile, discover_source_files
from app.analyzer.scoring import (
    HEALTH_SCORE_WEIGHTS,
    calculate_project_health,
    score_module,
)


def _write_project(root: Path, files: dict[str, str]) -> Path:
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return root


def _module(tmp_path: Path, name: str, code: str, language: str = "c"):
    path = tmp_path / name
    path.write_text(code)
    sf = SourceFile(
        absolute_path=path, relative_path=name, file_name=name,
        language=language, size_bytes=len(code),
    )
    return parse_file(sf)


def _only_module(tmp_path: Path, name: str, code: str):
    analysis = analyze_project(_write_project(tmp_path, {name: code}))
    assert len(analysis.modules) == 1
    return analysis.modules[0]


# --------------------------------------------------------------------- scanner


def test_scanner_detects_c_files_and_keeps_python_java(tmp_path):
    _write_project(tmp_path, {
        "a.c": "int a(void) { return 1; }\n",
        "b.h": "int a(void);\n",
        "c.py": "x = 1\n",
        "D.java": "public class D {}\n",
        "notes.txt": "ignore me\n",
    })
    found = {f.file_name: f.language for f in discover_source_files(tmp_path)}
    assert found == {"a.c": "c", "b.h": "c", "c.py": "python", "D.java": "java"}


# --------------------------------------------------------------------- parsing


def test_c_simple_function(tmp_path):
    module = _module(tmp_path, "add.c", "int add(int a, int b) {\n    return a + b;\n}\n")
    assert module.parse_error is None
    assert [f.name for f in module.functions] == ["add"]
    assert module.classes == []


def test_c_multiple_functions_and_calls(tmp_path):
    code = (
        "static int helper(int x) { return x * 2; }\n"
        "int compute(int x) { return helper(x) + helper(x + 1); }\n"
        "int main(void) { return compute(3); }\n"
    )
    module = _module(tmp_path, "m.c", code)
    assert [f.name for f in module.functions] == ["helper", "compute", "main"]
    compute = next(f for f in module.raw_tree.functions if f.name == "compute")
    assert compute.calls == ["helper"]  # control keywords and sizeof are not calls


def test_c_struct_is_recognised_with_fields(tmp_path):
    code = "struct User {\n    int id;\n    char name[50];\n};\n"
    module = _module(tmp_path, "user.h", code)
    assert [c.name for c in module.classes] == ["User"]
    assert set(module.classes[0].field_names) == {"id", "name"}
    assert module.classes[0].base_name is None


def test_c_struct_typed_pointer_field_is_kept(tmp_path):
    # Regression: `struct Node *next;` was once dropped because the statement
    # started with the `struct` keyword.
    code = "struct Node {\n    struct Node *next;\n    enum Level lvl;\n    int v;\n};\n"
    module = _module(tmp_path, "node.h", code)
    assert module.classes[0].field_names == ["next", "lvl", "v"]


def test_c_typedef_anonymous_struct_takes_typedef_name(tmp_path):
    module = _module(tmp_path, "p.h", "typedef struct {\n    int x;\n    int y;\n} Point;\n")
    assert [c.name for c in module.classes] == ["Point"]


def test_c_includes_are_collected(tmp_path):
    code = '#include <stdio.h>\n#include <stdlib.h>\n#include "auth.h"\nint x;\n'
    module = _module(tmp_path, "i.c", code)
    assert set(module.imports) == {"stdio.h", "stdlib.h", "auth.h"}


def test_c_unbalanced_braces_is_a_parse_error_not_a_crash(tmp_path):
    module = _module(tmp_path, "bad.c", "int f(void) {\n    return 1;\n")
    assert module.parse_error is not None


# ------------------------------------------------------------------------- LOC


def test_c_loc_comments_and_comment_like_strings():
    code = (
        "// line comment\n"
        "/* block\n"
        "   still comment */\n"
        "int x; // trailing comment stays code\n"
        'char *s = "// not a comment";\n'
        'char *t = "/* nor this */";\n'
        "\n"
        "int y;\n"
    )
    loc = calculate_loc("c", code)
    assert (loc.total, loc.code, loc.comments, loc.blank) == (8, 4, 3, 1)
    assert loc.total == loc.code + loc.comments + loc.blank


def test_c_loc_blank_line_inside_block_comment_is_comment():
    loc = calculate_loc("c", "/* a\n\n b */\nint z;\n")
    assert (loc.total, loc.code, loc.comments, loc.blank) == (4, 1, 3, 0)


def test_c_loc_code_after_block_comment_counts_as_code():
    loc = calculate_loc("c", "/* note */ int z;\n")
    assert (loc.code, loc.comments) == (1, 0)


# ------------------------------------------------------------------ complexity


def test_c_complexity_if_else(tmp_path):
    code = (
        "int sign(int a) {\n"
        "    if (a > 0) return 1;\n"
        "    else if (a < 0) return -1;\n"
        "    else return 0;\n"
        "}\n"
    )
    assert _only_module(tmp_path, "s.c", code).complexity_total == 3  # 1 + if + else-if


def test_c_complexity_loops(tmp_path):
    code = (
        "void loops(int n) {\n"
        "    for (int i = 0; i < n; i++) { }\n"
        "    while (n > 0) { n--; }\n"
        "    do { n++; } while (n < 3);\n"
        "}\n"
    )
    assert _only_module(tmp_path, "l.c", code).complexity_total == 4


def test_c_complexity_switch_counts_cases_not_default(tmp_path):
    code = (
        "int pick(int v) {\n"
        "    switch (v) {\n"
        "        case 1: return 10;\n"
        "        case 2: return 20;\n"
        "        case 3: return 30;\n"
        "        default: return 0;\n"
        "    }\n"
        "}\n"
    )
    assert _only_module(tmp_path, "sw.c", code).complexity_total == 4


def test_c_complexity_logical_operators_and_ternary(tmp_path):
    code = "int f(int a, int b, int c) { return a && b || c ? 1 : 0; }\n"
    assert _only_module(tmp_path, "t.c", code).complexity_total == 4


def test_c_complexity_ignores_keywords_in_comments_and_strings(tmp_path):
    code = (
        "int f(void) {\n"
        "    // if while for case && ||\n"
        "    /* if (x) { } */\n"
        '    const char *s = "if (a && b) while ? case";\n'
        "    return 0;\n"
        "}\n"
    )
    assert _only_module(tmp_path, "c.c", code).complexity_total == 1


def test_c_braces_inside_strings_and_chars_do_not_break_parsing(tmp_path):
    code = (
        "int f(void) {\n"
        "    char open = '{';\n"
        '    const char *s = "}}}";\n'
        "    return 0;\n"
        "}\n"
        "int g(void) { return 1; }\n"
    )
    module = _module(tmp_path, "br.c", code)
    assert module.parse_error is None
    assert [f.name for f in module.functions] == ["f", "g"]


# --------------------------------------------------------------- shared metrics


def test_c_module_metrics_flow_through_existing_pipeline(tmp_path):
    code = (
        "#include <stdio.h>\n"
        "#include <stdlib.h>\n"
        "// entry point\n"
        "int main(void) {\n"
        "    for (int i = 0; i < 3; i++) {\n"
        '        if (i % 2 == 0 && i > 0) printf("x\\n");\n'
        "    }\n"
        "    return 0;\n"
        "}\n"
    )
    m = _only_module(tmp_path, "main.c", code)
    assert m.language == "c"
    assert m.functions == 1
    assert m.complexity_total == 4  # 1 + for + if + &&
    assert m.coupling == 2  # stdio.h, stdlib.h
    assert m.loc.comments == 1
    assert m.maintainability_index is not None and 0 <= m.maintainability_index <= 100
    assert m.cohesion is None  # no classes -> neutral default
    assert m.depth_of_inheritance is None  # C has no inheritance -> neutral default
    assert m.parse_error is None


def test_c_own_header_is_not_counted_as_coupling(tmp_path):
    _write_project(tmp_path, {
        "auth.c": '#include "auth.h"\n#include <string.h>\nint login(int u) { return u; }\n',
        "auth.h": "int login(int u);\n",
    })
    analysis = analyze_project(tmp_path)
    auth_c = next(m for m in analysis.modules if m.file_name == "auth.c")
    assert auth_c.coupling == 1  # string.h only


def test_c_header_with_only_declarations_has_no_complexity_score(tmp_path):
    m = _only_module(tmp_path, "api.h", "struct Node;\nint count(struct Node *n);\n")
    assert m.functions == 0
    assert m.complexity_total == 0
    assert score_module(m).complexity is None  # not a fabricated 100


def test_c_header_extern_c_guard_stays_c(tmp_path):
    guard = (
        "#ifndef X_H\n#define X_H\n#ifdef __cplusplus\nextern \"C\" {\n#endif\n"
        "int f(void);\n#ifdef __cplusplus\n}\n#endif\n#endif\n"
    )
    _write_project(tmp_path, {"x.h": guard})
    assert discover_source_files(tmp_path)[0].language == "c"


def test_c_duplicate_blocks_across_files_use_existing_duplication(tmp_path):
    block = (
        "int dup(int a) {\n"
        "    int r = a;\n"
        "    r += 1;\n"
        "    r += 2;\n"
        "    r += 3;\n"
        "    return r;\n"
        "}\n"
    )
    _write_project(tmp_path, {"one.c": block, "two.c": block})
    analysis = analyze_project(tmp_path)
    assert all(m.duplicated_lines > 0 for m in analysis.modules)
    assert analysis.duplication_percentage > 0


def test_c_multiple_files_summary(tmp_path):
    _write_project(tmp_path, {
        "src/main.c": '#include "auth.h"\nint main(void) { return login(1); }\n',
        "src/auth.c": '#include "auth.h"\nint login(int u) { if (u) return 1; return 0; }\n',
        "src/auth.h": "int login(int u);\n",
        "src/user.c": '#include "user.h"\nvoid touch(void) { }\n',
        "src/user.h": "void touch(void);\n",
    })
    analysis = analyze_project(tmp_path)
    assert analysis.files_analyzed == 5
    assert analysis.languages == ["c"]
    assert analysis.total_functions == 3  # definitions only; header prototypes are declarations
    assert analysis.files_with_errors == 0


# ----------------------------------------------------- scoring / risk (C -> ...)


def test_scoring_weights_unchanged():
    assert HEALTH_SCORE_WEIGHTS == {
        "maintainability": 0.20, "complexity": 0.20, "code_quality": 0.15,
        "testing": 0.15, "coupling_cohesion": 0.10, "duplication": 0.10,
        "documentation": 0.05, "code_churn": 0.05,
    }


def test_c_to_metrics_to_scoring_to_risk(tmp_path):
    branches = "\n".join(f"    if (a == {i} && b > {i}) r += {i};" for i in range(40))
    _write_project(tmp_path, {
        "simple.c": "int one(void) { return 1; }\n",
        "tangled.c": f"int tangled(int a, int b) {{\n    int r = 0;\n{branches}\n    return r;\n}}\n",
    })
    analysis = analyze_project(tmp_path)
    scores = [score_module(m) for m in analysis.modules]
    risks = [calculate_module_risk(m, s) for m, s in zip(analysis.modules, scores)]

    health = calculate_project_health(analysis, scores)
    assert 0 <= health.health_score <= 100
    assert health.health_status in {"CRITICAL", "POOR", "NEEDS_ATTENTION", "HEALTHY", "EXCELLENT"}
    assert "testing" in health.unavailable_categories  # unchanged behaviour

    ranked = rank_risky_modules(risks)
    assert ranked[0]["file_name"] == "tangled.c"
    assert ranked[0]["risk_score"] > ranked[-1]["risk_score"]
    tangled_risk = next(r for r in risks if r.file_name == "tangled.c")
    assert "High cyclomatic complexity" in tangled_risk.risk_factors


# ---------------------------------------------------------------------- upload


def _zip_bytes(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def test_upload_c_project_through_existing_endpoint(client):
    data = _zip_bytes({
        "my-c-project/src/main.c": '#include "auth.h"\nint main(void) { return login(1); }\n',
        "my-c-project/src/auth.c": '#include "auth.h"\nint login(int u) { if (u) return 1; return 0; }\n',
        "my-c-project/src/auth.h": "int login(int u);\n",
        "my-c-project/src/user.c": '#include "user.h"\nvoid touch(void) { }\n',
        "my-c-project/src/user.h": "void touch(void);\n",
    })
    response = client.post(
        "/api/projects/upload", files={"file": ("my-c-project.zip", data, "application/zip")}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["analysis"]["project"]["languages"] == ["c"]
    assert body["analysis"]["project"]["files"] == 5
    assert body["project_id"] > 0


def test_c_project_works_with_report_and_detail_endpoints(client):
    data = _zip_bytes({"src/main.c": "int main(void) { return 0; }\n"})
    project_id = client.post(
        "/api/projects/upload", files={"file": ("c.zip", data, "application/zip")}
    ).json()["project_id"]

    assert client.get(f"/api/projects/{project_id}").status_code == 200
    report = client.get(f"/api/projects/{project_id}/report")
    assert report.status_code == 200
    assert report.content.startswith(b"%PDF")
