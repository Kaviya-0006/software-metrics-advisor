"""
Pipeline tests for TypeScript support (`.ts`, `.tsx`): detection, parsing,
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


def _cc(tmp_path: Path, body: str, name: str = "m.ts") -> int:
    return _only_module(tmp_path, name, body).complexity_total


# --------------------------------------------------------------------- scanner


def test_scanner_detects_ts_and_tsx(tmp_path):
    _write_project(tmp_path, {
        "a.ts": "const a: number = 1;\n",
        "b.tsx": "const B = () => <div/>;\n",
        "c.js": "const c = 1;\n",
        "d.py": "x = 1\n",
    })
    found = {f.file_name: f.language for f in discover_source_files(tmp_path)}
    assert found == {"a.ts": "typescript", "b.tsx": "typescript", "c.js": "javascript", "d.py": "python"}


def test_scanner_skips_binary_ts_video_segments_but_keeps_text_ts(tmp_path):
    _write_project(tmp_path, {
        "segment.ts": b"\x47\x40\x11\x10\x00\x02\xb0binary-mpegts",
        "real.ts": "export const x: number = 1;\n",
    })
    assert [f.file_name for f in discover_source_files(tmp_path)] == ["real.ts"]


# ------------------------------------------------------------------------- LOC


def test_ts_loc_with_types_comments_and_templates():
    code = (
        "// header\n"
        "/** doc\n"
        " *  more */\n"
        "interface A { id: number; }\n"
        "const s: string = `// not a comment ${1 + 1}`; // trailing\n"
        "\n"
        "type T = string | number;\n"
    )
    loc = calculate_loc("typescript", code, jsx=False)
    assert (loc.total, loc.code, loc.comments, loc.blank) == (7, 3, 3, 1)


# ------------------------------------------------------- functions & complexity


def test_ts_typed_function(tmp_path):
    code = "function add(a: number, b: number): number { return a > 0 ? a : b; }\n"
    module = _only_module(tmp_path, "add.ts", code)
    assert module.functions == 1 and module.complexity_total == 2


def test_ts_typed_arrows_and_generics(tmp_path):
    code = (
        "const gen = <T>(x: T): T => x ?? x;\n"
        "const typed: (n: number) => string = (n) => String(n);\n"
        "const withRet = (a: number): Promise<void> => { if (a) { return Promise.resolve(); } return Promise.resolve(); };\n"
    )
    module = _only_module(tmp_path, "arrows.ts", code)
    assert module.functions == 3  # the function *type* annotation is not a function
    assert module.complexity_total == 2 + 1 + 2


def test_ts_overload_signatures_are_not_function_definitions(tmp_path):
    code = (
        "function over(a: string): string;\n"
        "function over(a: number): number;\n"
        "function over(a: any): any { return a; }\n"
    )
    assert _only_module(tmp_path, "over.ts", code).functions == 1


def test_ts_optional_markers_and_type_syntax_add_no_complexity(tmp_path):
    code = (
        "function f(x: number, y?: string): number {\n"
        "  const inner = (b?: number) => b;\n"
        "  const o: { a?: string } = {};\n"
        "  return x ? 1 : 2;\n"
        "}\n"
    )
    assert _cc(tmp_path, code) == 2  # only the real ternary


def test_ts_type_assertion_in_plain_ts_is_not_jsx(tmp_path):
    module = _only_module(tmp_path, "cast.ts", "function f(x: any) { return <string>x; }\n")
    assert module.parse_error is None
    assert module.functions == 1 and module.complexity_total == 1


def test_ts_complexity_switch_try_catch_and_logic(tmp_path):
    code = (
        "function f(v: number, a: boolean, b: boolean): number {\n"
        "  switch (v) { case 1: return 1; case 2: return 2; default: break; }\n"
        "  try { return 0; } catch (e) { return 1; }\n"
        "  return a && b || v > 3 ? 1 : 2;\n"
        "}\n"
    )
    assert _cc(tmp_path, code) == 1 + 2 + 1 + 3


# ------------------------------------------- interfaces / aliases / enums / classes


def test_ts_interfaces_count_as_classes_but_aliases_and_enums_do_not(tmp_path):
    code = (
        "interface User { id: number; name: string; }\n"
        "type Id = string | number;\n"
        "enum Color { Red, Green }\n"
        "class Admin { level = 1; }\n"
    )
    module = _only_module(tmp_path, "types.ts", code)
    assert module.classes == 2  # User (interface) + Admin
    assert module.functions == 0


def test_ts_class_extends_and_interface_extends_depth(tmp_path):
    _write_project(tmp_path, {
        "Base.ts": "export class Base {}\n",
        "Child.ts": "import { Base } from './Base';\nexport class Child extends Base {}\n",
        "shapes.ts": "interface A {}\ninterface B extends A {}\n",
    })
    by_name = {m.file_name: m for m in analyze_project(tmp_path).modules}
    assert by_name["Base.ts"].depth_of_inheritance == 0
    assert by_name["Child.ts"].depth_of_inheritance == 1
    assert by_name["shapes.ts"].depth_of_inheritance == 0.5  # A = 0, B extends A = 1


def test_ts_classes_with_modifiers_and_typed_fields_parse_cleanly(tmp_path):
    code = (
        "export abstract class Repo<T> extends Base<T> implements IRepo {\n"
        "  private readonly items: T[] = [];\n"
        "  protected static instance?: Repo<any>;\n"
        "  public name!: string;\n"
        "  abstract find(id: number): T | undefined;\n"
        "  get size(): number { return this.items.length; }\n"
        "  public add(item: T): void { this.items.push(item); }\n"
        "}\n"
    )
    module = _only_module(tmp_path, "repo.ts", code)
    assert module.parse_error is None
    assert module.classes == 1
    assert module.functions == 2  # abstract signature is not a definition


# -------------------------------------------------------------------- coupling


def test_ts_coupling_imports_type_imports_and_reexports(tmp_path):
    code = (
        "import type { Foo } from './foo';\n"
        "import { Bar } from \"../bar\";\n"
        "import fs = require('fs');\n"
        "export * from './all';\n"
        "export { x } from './x';\n"
    )
    assert _only_module(tmp_path, "m.ts", code).coupling == 5


def test_ts_coupling_does_not_double_count_imported_bases_but_counts_local_ones(tmp_path):
    code = (
        "import { Repo } from './repo';\n"
        "class S implements Repo, Local {}\n"
    )
    assert _only_module(tmp_path, "s.ts", code).coupling == 2  # './repo' + Local; Repo already represented


# -------------------------------------------------------------------- cohesion


def test_ts_cohesion_abstract_methods_are_left_out(tmp_path):
    code = (
        "abstract class Shape {\n"
        "  protected w = 1;\n"
        "  abstract area(): number;\n"
        "  describe(): string { return 'w' + this.w; }\n"
        "  scale(k: number) { this.w *= k; }\n"
        "}\n"
    )
    assert _only_module(tmp_path, "shape.ts", code).cohesion == 0.0


def test_ts_cohesion_parameter_properties_are_fields(tmp_path):
    code = (
        "class P {\n"
        "  constructor(private a: number, private b: number) {}\n"
        "  getA() { return this.a; }\n"
        "  getB() { return this.b; }\n"
        "}\n"
    )
    assert _only_module(tmp_path, "p.ts", code).cohesion == 1.0  # disjoint field use


def test_ts_cohesion_is_none_for_interfaces(tmp_path):
    code = "interface I { a(): void; b(): void; x: number }\n"
    assert _only_module(tmp_path, "i.ts", code).cohesion is None  # not an invented number


# ------------------------------------------------------------------------- TSX


def test_tsx_generic_arrow_and_jsx_expressions(tmp_path):
    code = (
        "export const C = <T,>(p: T) => <b>{p ? \"y\" : \"n\"}</b>;\n"
        "export const D: React.FC<{ a: boolean }> = ({ a }) => { return <span>it's {a && 'yes'}</span>; };\n"
    )
    module = _only_module(tmp_path, "comp.tsx", code)
    assert module.parse_error is None
    assert module.language == "typescript"
    assert module.functions == 2
    assert module.complexity_total == 2 + 2  # C: ?:   D: &&


def test_tsx_jsx_text_with_keywords_adds_no_complexity(tmp_path):
    code = "export const N = (): JSX.Element => <p>Don't wait if you can; for now, catch a case.</p>;\n"
    module = _only_module(tmp_path, "n.tsx", code)
    assert module.functions == 1 and module.complexity_total == 1


def test_ts_difficult_braces_and_comment_markers_in_strings_and_templates(tmp_path):
    code = (
        "interface Cfg { path: string; }\n"
        "const render = (c: Cfg): string => {\n"
        "  const a = \"// not a comment { \";\n"
        "  const b = `}} ${ c.path ? '/* x */' : \"{\" } {{`;\n"
        "  return a + b; // real comment {\n"
        "};\n"
        "class Svc { run(): void { render({ path: '}' }); } }\n"
    )
    module = _only_module(tmp_path, "tricky.ts", code)
    assert module.parse_error is None
    assert module.classes == 2 and module.functions == 2
    assert module.complexity_total == 2 + 1  # render has one ternary; run has none


# ------------------------------------------------- maintainability / errors


def test_ts_maintainability_is_bounded_and_drops_for_tangled_code(tmp_path):
    branches = "\n".join(f"  if (a === {i} && b > {i}) r += {i};" for i in range(40))
    _write_project(tmp_path, {
        "simple.ts": "function one(): number { return 1; }\n",
        "tangled.ts": f"function tangled(a: number, b: number): number {{\n  let r = 0;\n{branches}\n  return r;\n}}\n",
    })
    by_name = {m.file_name: m for m in analyze_project(tmp_path).modules}
    for module in by_name.values():
        assert 0 <= module.maintainability_index <= 100
    assert by_name["simple.ts"].maintainability_index > by_name["tangled.ts"].maintainability_index


def test_ts_unbalanced_file_is_a_parse_error(tmp_path):
    analysis = analyze_project(_write_project(tmp_path, {"bad.ts": "class A {\n  m() {\n"}))
    assert analysis.modules[0].parse_error is not None
    assert analysis.files_with_errors == 1


# ------------------------------------------------------------- scoring / risk


def test_ts_to_metrics_to_scoring_to_risk(tmp_path):
    branches = "\n".join(f"  if (a === {i} && b > {i}) r += {i};" for i in range(40))
    _write_project(tmp_path, {
        "simple.ts": "export function one(): number { return 1; }\n",
        "tangled.tsx": f"export function tangled(a: number, b: number): number {{\n  let r = 0;\n{branches}\n  return r;\n}}\n",
    })
    analysis = analyze_project(tmp_path)
    scores = [score_module(m) for m in analysis.modules]
    risks = [calculate_module_risk(m, s) for m, s in zip(analysis.modules, scores)]

    health = calculate_project_health(analysis, scores)
    assert 0 <= health.health_score <= 100

    ranked = rank_risky_modules(risks)
    assert ranked[0]["file_name"] == "tangled.tsx"
    assert ranked[0]["risk_score"] > ranked[-1]["risk_score"]
    tangled_risk = next(r for r in risks if r.file_name == "tangled.tsx")
    assert "High cyclomatic complexity" in tangled_risk.risk_factors


# ---------------------------------------------------------------------- upload


def _zip_bytes(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def test_upload_ts_project_through_existing_endpoint(client):
    data = _zip_bytes({
        "my-ts-app/src/index.ts": "import { run } from './run';\nrun();\n",
        "my-ts-app/src/run.ts": "export function run(): number { if (Math.random() > 0.5) { return 1; } return 0; }\n",
        "my-ts-app/src/App.tsx": "export const App = () => <div>hi</div>;\n",
        "my-ts-app/src/types.ts": "export interface User { id: number }\n",
    })
    response = client.post(
        "/api/projects/upload", files={"file": ("my-ts-app.zip", data, "application/zip")}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["analysis"]["project"]["languages"] == ["typescript"]
    assert body["analysis"]["project"]["files"] == 4
    assert body["analysis"]["project"]["classes"] == 1  # the interface


def test_ts_project_works_with_detail_and_report_endpoints(client):
    data = _zip_bytes({"src/main.ts": "export function main(): number { return 0; }\n"})
    project_id = client.post(
        "/api/projects/upload", files={"file": ("ts.zip", data, "application/zip")}
    ).json()["project_id"]
    assert client.get(f"/api/projects/{project_id}").status_code == 200
    report = client.get(f"/api/projects/{project_id}/report")
    assert report.status_code == 200 and report.content.startswith(b"%PDF")
