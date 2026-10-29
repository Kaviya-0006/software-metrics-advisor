"""
Pipeline tests for JavaScript support (`.js`, `.jsx`): detection, parsing,
the shared metric pipeline, scoring/risk, and the existing upload workflow.
"""

import io
import zipfile
from pathlib import Path

from app.analyzer.engine import analyze_project
from app.analyzer.metrics.loc import calculate_loc
from app.analyzer.risk import calculate_module_risk, rank_risky_modules
from app.analyzer.scanner import discover_source_files
from app.analyzer.scoring import calculate_project_health, score_module


def _write_project(root: Path, files: dict) -> Path:
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content)
    return root


def _only_module(tmp_path: Path, name: str, code: str):
    analysis = analyze_project(_write_project(tmp_path, {name: code}))
    assert len(analysis.modules) == 1
    return analysis.modules[0]


def _cc(tmp_path: Path, body: str) -> int:
    return _only_module(tmp_path, "m.js", body).complexity_total


# --------------------------------------------------------------------- scanner


def test_scanner_detects_js_and_jsx_and_keeps_other_languages(tmp_path):
    _write_project(tmp_path, {
        "a.js": "const a = 1;\n",
        "b.jsx": "const B = () => <div/>;\n",
        "c.py": "x = 1\n",
        "D.java": "public class D {}\n",
        "e.c": "int e(void) { return 1; }\n",
        "notes.txt": "ignore\n",
    })
    found = {f.file_name: f.language for f in discover_source_files(tmp_path)}
    assert found == {"a.js": "javascript", "b.jsx": "javascript", "c.py": "python", "D.java": "java", "e.c": "c"}


def test_scanner_skips_minified_js(tmp_path):
    _write_project(tmp_path, {"app.js": "const a = 1;\n", "vendor.min.js": "var a=1;", "static/lib.MIN.JS": "var b=2;"})
    assert [f.file_name for f in discover_source_files(tmp_path)] == ["app.js"]


# ------------------------------------------------------------------------- LOC


def test_js_loc_comments_strings_templates_and_regex():
    code = (
        "// c\n"
        "/* a\n"
        " b */\n"
        "\n"
        'const a = "// x"; // trailing\n'
        "const t = `x\n"
        "// y\n"
        "/* z */`;\n"
        "const r = /\\/\\//;\n"
    )
    loc = calculate_loc("javascript", code)
    assert (loc.total, loc.code, loc.comments, loc.blank) == (9, 5, 3, 1)
    assert loc.total == loc.code + loc.comments + loc.blank


def test_js_loc_blank_line_inside_block_comment_is_comment():
    loc = calculate_loc("javascript", "/* a\n\n b */\nconst z = 1;\n")
    assert (loc.total, loc.code, loc.comments, loc.blank) == (4, 1, 3, 0)


def test_jsx_text_that_looks_like_a_comment_is_code():
    code = "const A = () => (\n  <p>\n    // not a comment\n  </p>\n);\n"
    loc = calculate_loc("javascript", code, jsx=True)
    assert (loc.code, loc.comments) == (5, 0)


# ------------------------------------------------------------------ complexity


def test_js_complexity_every_counted_construct(tmp_path):
    code = (
        "function f(a, b, c) {\n"
        "  if (a) { }\n"
        "  for (;;) { }\n"
        "  while (b) { }\n"
        "  do { } while (c);\n"
        "  switch (a) { case 1: case 2: break; }\n"
        "  try { } catch (e) { }\n"
        "  return a && b || c ? 1 : 2;\n"
        "}\n"
    )
    # if, for, while, do-while, case x2, catch, &&, ||, ?:  = 10 decisions
    assert _cc(tmp_path, code) == 11


def test_js_complexity_for_of_for_in_and_nullish(tmp_path):
    code = "function f(xs, o, a, b) { for (const x of xs) { } for (const k in o) { } return a ?? b; }\n"
    assert _cc(tmp_path, code) == 4


def test_js_complexity_else_default_and_optional_chaining_add_nothing(tmp_path):
    code = (
        "function f(a, o) {\n"
        "  if (a) { return 1; } else { return o?.x?.y; }\n"
        "  switch (a) { case 1: break; default: break; }\n"
        "}\n"
    )
    assert _cc(tmp_path, code) == 3  # if + case only


def test_js_complexity_ternary_and_arrow_expression_examples(tmp_path):
    code = (
        "function pick(condition, a, b) { const value = condition ? a : b; return value; }\n"
        "const fn = (a, b) => a && b;\n"
    )
    module = _only_module(tmp_path, "m.js", code)
    assert module.functions == 2
    assert module.complexity_total == 2 + 2  # pick: 1 + ?:, fn: 1 + &&
    assert module.complexity_maximum == 2


def test_js_complexity_nested_control_flow(tmp_path):
    code = (
        "function nest(a, b) {\n"
        "  for (let i = 0; i < a; i++) {\n"
        "    if (i % 2) { while (b) { b--; if (b === 3 && i) break; } }\n"
        "    else if (i > 5) { continue; }\n"
        "  }\n"
        "  return a ? b : 0;\n"
        "}\n"
    )
    assert _cc(tmp_path, code) == 8  # for, if, while, if, &&, else-if, ?:


def test_js_complexity_ignores_keywords_in_strings_templates_comments_regex_and_jsx(tmp_path):
    code = (
        "function f(s) {\n"
        "  // if while for case && ||\n"
        "  const a = \"if (a && b) while ? case\";\n"
        "  const t = `for ${1} catch while`;\n"
        "  return /if|for|while/.test(s) + a + t;\n"
        "}\n"
        "const View = () => <p>if you want to wait for it, catch a case</p>;\n"
    )
    module = _only_module(tmp_path, "view.jsx", code)
    assert module.complexity_total == 2  # f = 1, View = 1


def test_js_promise_catch_method_is_not_a_catch_clause(tmp_path):
    assert _cc(tmp_path, "function f(p) { return p.catch(e => 1); }\n") == 1


def test_js_module_level_code_without_functions_has_no_complexity_score(tmp_path):
    module = _only_module(tmp_path, "cfg.js", "const v = process.env.X ? 1 : 2;\nmodule.exports = { v };\n")
    assert module.functions == 0 and module.complexity_total == 0
    assert score_module(module).complexity is None  # not a fabricated 100


def test_js_callbacks_count_as_functions_and_nested_ones_fold_into_the_parent(tmp_path):
    code = (
        "app.get('/x', (req, res) => { if (req.q) res.send(1); });\n"
        "function outer(items) { return items.map(i => i ? 1 : 0); }\n"
    )
    module = _only_module(tmp_path, "srv.js", code)
    assert module.functions == 2  # the route callback and outer (the inner arrow is part of outer)
    assert module.complexity_total == 2 + 2


# ------------------------------------------------------------------ classes etc


def test_js_class_methods_constructor_and_counts(tmp_path):
    code = (
        "class User {\n"
        "  constructor(n) { this.name = n; }\n"
        "  label() { return this.name; }\n"
        "  static make() { return new User('x'); }\n"
        "}\n"
    )
    module = _only_module(tmp_path, "user.js", code)
    assert module.classes == 1
    assert module.functions == 3  # constructor + 2 methods are function definitions


def test_jsx_file_is_analyzed_as_javascript(tmp_path):
    module = _only_module(tmp_path, "App.jsx", "export const App = ({ ok }) => <div>{ok ? <b>y</b> : <i>n</i>}</div>;\n")
    assert module.language == "javascript" and module.parse_error is None
    assert module.functions == 1 and module.complexity_total == 2


def test_js_depth_of_inheritance_across_files(tmp_path):
    _write_project(tmp_path, {
        "Base.js": "export class Base {}\n",
        "Mid.js": "import { Base } from './Base';\nexport class Mid extends Base {}\n",
        "Leaf.js": "import { Mid } from './Mid';\nexport class Leaf extends Mid {}\n",
    })
    by_name = {m.file_name: m for m in analyze_project(tmp_path).modules}
    assert by_name["Base.js"].depth_of_inheritance == 0
    assert by_name["Mid.js"].depth_of_inheritance == 1
    assert by_name["Leaf.js"].depth_of_inheritance == 2


# -------------------------------------------------------------------- coupling


def test_js_coupling_counts_distinct_import_forms(tmp_path):
    code = (
        "import a from 'a';\n"
        "import { b } from 'b';\n"
        "import './side.css';\n"
        "const c = require('c');\n"
        "const lazy = () => import('./lazy');\n"
        "import again from 'a';\n"
        "export { x } from './x';\n"
    )
    assert _only_module(tmp_path, "m.js", code).coupling == 6  # a b ./side.css c ./lazy ./x


def test_js_coupling_does_not_double_count_an_imported_base(tmp_path):
    code = "import { Base } from './base';\nclass A extends Base {}\n"
    assert _only_module(tmp_path, "a.js", code).coupling == 1


def test_js_coupling_counts_a_base_that_is_not_imported(tmp_path):
    assert _only_module(tmp_path, "a.js", "class Admin extends User {}\n").coupling == 1


def test_js_function_calls_are_not_dependencies(tmp_path):
    code = "function f() { return g(1) + h(2) + a.b.c(3); }\n"
    assert _only_module(tmp_path, "m.js", code).coupling == 0


# -------------------------------------------------------------------- cohesion


def test_js_cohesion_fully_cohesive_class_is_zero(tmp_path):
    code = "class K {\n  constructor() { this.x = 0; }\n  a() { this.x = 1; }\n  b() { return this.x; }\n}\n"
    assert _only_module(tmp_path, "k.js", code).cohesion == 0.0


def test_js_cohesion_lcom1_for_split_class(tmp_path):
    # a/b share x; c touches only y -> pairs: (a,b) shared, (a,c) and (b,c) disjoint -> 2 - 1 = 1
    code = (
        "class S {\n"
        "  constructor() { this.x = 0; this.y = 0; }\n"
        "  a() { this.x = 1; }\n"
        "  b() { this.x = 2; }\n"
        "  c() { this.y = 3; }\n"
        "}\n"
    )
    assert _only_module(tmp_path, "s.js", code).cohesion == 1.0


def test_js_cohesion_class_field_arrow_methods_count_as_methods(tmp_path):
    code = "class H {\n  x = 0;\n  inc = () => { this.x++; };\n  dec = () => { this.x--; };\n}\n"
    assert _only_module(tmp_path, "h.js", code).cohesion == 0.0


def test_js_cohesion_is_none_for_single_method_or_no_fields(tmp_path):
    one = "class One { constructor() { this.x = 1; } a() { return this.x; } }\n"
    no_fields = "class N { a() { return 1; } b() { return 2; } }\n"
    assert _only_module(tmp_path / "one", "one.js", one).cohesion is None
    assert _only_module(tmp_path / "nf", "nf.js", no_fields).cohesion is None


# ----------------------------------------------------- maintainability / errors


def test_js_maintainability_is_bounded_and_drops_for_tangled_code(tmp_path):
    branches = "\n".join(f"  if (a === {i} && b > {i}) r += {i};" for i in range(40))
    _write_project(tmp_path, {
        "simple.js": "function one() { return 1; }\n",
        "tangled.js": f"function tangled(a, b) {{\n  let r = 0;\n{branches}\n  return r;\n}}\n",
    })
    by_name = {m.file_name: m for m in analyze_project(tmp_path).modules}
    for module in by_name.values():
        assert 0 <= module.maintainability_index <= 100
    assert by_name["simple.js"].maintainability_index > by_name["tangled.js"].maintainability_index


def test_js_unbalanced_file_is_a_parse_error_not_a_crash(tmp_path):
    _write_project(tmp_path, {"bad.js": "function f() {\n  return 1;\n", "ok.js": "const a = 1;\n"})
    analysis = analyze_project(tmp_path)
    bad = next(m for m in analysis.modules if m.file_name == "bad.js")
    assert bad.parse_error is not None
    assert analysis.files_with_errors == 1


def test_js_duplication_uses_existing_algorithm(tmp_path):
    block = (
        "function dup(a) {\n  let r = a;\n  r += 1;\n  r += 2;\n  r += 3;\n  return r;\n}\n"
    )
    _write_project(tmp_path, {"one.js": block, "two.js": block})
    analysis = analyze_project(tmp_path)
    assert all(m.duplicated_lines > 0 for m in analysis.modules)
    assert analysis.duplication_percentage > 0


def test_mixed_project_reports_all_languages(tmp_path):
    _write_project(tmp_path, {
        "a.py": "def f():\n    return 1\n",
        "B.java": "public class B {\n    public void m() {}\n}\n",
        "c.c": "int c(void) { return 1; }\n",
        "d.cpp": "int d() { return 1; }\n",
        "e.js": "const e = () => 1;\n",
        "f.ts": "const f = (): number => 1;\n",
    })
    analysis = analyze_project(tmp_path)
    assert analysis.languages == ["c", "cpp", "java", "javascript", "python", "typescript"]
    assert analysis.files_with_errors == 0


# ------------------------------------------------------------- scoring / risk


def test_js_to_metrics_to_scoring_to_risk(tmp_path):
    branches = "\n".join(f"  if (a === {i} && b > {i}) r += {i};" for i in range(40))
    _write_project(tmp_path, {
        "simple.js": "function one() { return 1; }\n",
        "tangled.js": f"function tangled(a, b) {{\n  let r = 0;\n{branches}\n  return r;\n}}\n",
    })
    analysis = analyze_project(tmp_path)
    scores = [score_module(m) for m in analysis.modules]
    risks = [calculate_module_risk(m, s) for m, s in zip(analysis.modules, scores)]

    health = calculate_project_health(analysis, scores)
    assert 0 <= health.health_score <= 100
    assert "testing" in health.unavailable_categories  # unchanged behaviour

    ranked = rank_risky_modules(risks)
    assert ranked[0]["file_name"] == "tangled.js"
    assert ranked[0]["risk_score"] > ranked[-1]["risk_score"]
    tangled_risk = next(r for r in risks if r.file_name == "tangled.js")
    assert "High cyclomatic complexity" in tangled_risk.risk_factors


# ---------------------------------------------------------------------- upload


def _zip_bytes(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def test_upload_js_project_through_existing_endpoint(client):
    data = _zip_bytes({
        "my-js-app/src/index.js": "import { run } from './run';\nrun();\n",
        "my-js-app/src/run.js": "export function run() { if (Math.random() > 0.5) return 1; return 0; }\n",
        "my-js-app/src/App.jsx": "export const App = () => <div>hi</div>;\n",
        "my-js-app/dist/bundle.min.js": "var a=1;",
    })
    response = client.post(
        "/api/projects/upload", files={"file": ("my-js-app.zip", data, "application/zip")}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["analysis"]["project"]["languages"] == ["javascript"]
    assert body["analysis"]["project"]["files"] == 3  # the minified bundle is skipped


def test_js_project_works_with_detail_and_report_endpoints(client):
    data = _zip_bytes({"src/main.js": "function main() { return 0; }\n"})
    project_id = client.post(
        "/api/projects/upload", files={"file": ("js.zip", data, "application/zip")}
    ).json()["project_id"]
    assert client.get(f"/api/projects/{project_id}").status_code == 200
    report = client.get(f"/api/projects/{project_id}/report")
    assert report.status_code == 200 and report.content.startswith(b"%PDF")
