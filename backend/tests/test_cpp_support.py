"""
Tests for C++ support (`.cpp`, `.cc`, `.cxx`, `.hpp`, C++-style `.h`):
detection, parsing, the shared metric pipeline, scoring/risk, and upload.
"""

import io
import zipfile
from pathlib import Path

from app.analyzer.engine import analyze_project
from app.analyzer.metrics.loc import calculate_loc
from app.analyzer.parser import parse_file
from app.analyzer.risk import calculate_module_risk, rank_risky_modules
from app.analyzer.scanner import SourceFile, discover_source_files
from app.analyzer.scoring import calculate_project_health, score_module


def _write_project(root: Path, files: dict[str, str]) -> Path:
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return root


def _module(tmp_path: Path, name: str, code: str):
    path = tmp_path / name
    path.write_text(code)
    sf = SourceFile(
        absolute_path=path, relative_path=name, file_name=name,
        language="cpp", size_bytes=len(code),
    )
    return parse_file(sf)


def _only_module(tmp_path: Path, name: str, code: str):
    analysis = analyze_project(_write_project(tmp_path, {name: code}))
    assert len(analysis.modules) == 1
    return analysis.modules[0]


# --------------------------------------------------------------------- scanner


def test_scanner_detects_all_cpp_extensions(tmp_path):
    _write_project(tmp_path, {
        "a.cpp": "int a() { return 1; }\n",
        "b.cc": "int b() { return 1; }\n",
        "c.cxx": "int c() { return 1; }\n",
        "d.hpp": "int d();\n",
        "e.py": "x = 1\n",
    })
    found = {f.file_name: f.language for f in discover_source_files(tmp_path)}
    assert found == {"a.cpp": "cpp", "b.cc": "cpp", "c.cxx": "cpp", "d.hpp": "cpp", "e.py": "python"}


def test_dot_h_with_cpp_syntax_is_treated_as_cpp(tmp_path):
    _write_project(tmp_path, {
        "widget.h": "#pragma once\nclass Widget {\npublic:\n    void draw();\n};\n",
        "plain.h": "int plain(void);\n",
        "stl.h": "#include <vector>\nint n(void);\n",
    })
    found = {f.file_name: f.language for f in discover_source_files(tmp_path)}
    assert found == {"widget.h": "cpp", "plain.h": "c", "stl.h": "cpp"}


# --------------------------------------------------------------------- parsing


def test_cpp_free_function_with_conditions(tmp_path):
    code = (
        "double calculate(double x) {\n"
        "    if (x > 0)\n"
        "        return x * 2;\n"
        "    return 0;\n"
        "}\n"
    )
    module = _module(tmp_path, "calc.cpp", code)
    assert [f.name for f in module.functions] == ["calculate"]
    assert _only_module(tmp_path / "proj", "calc.cpp", code).complexity_total == 2


def test_cpp_class_methods_and_fields(tmp_path):
    code = (
        "class PaymentService {\n"
        "public:\n"
        "    PaymentService() : amount(0) {}\n"
        "    ~PaymentService() {}\n"
        "    void processPayment() { amount += 1; }\n"
        "    double getAmount() const { return amount; }\n"
        "private:\n"
        "    double amount;\n"
        "};\n"
    )
    module = _module(tmp_path, "PaymentService.hpp", code)
    assert module.parse_error is None
    cls = module.classes[0]
    assert cls.name == "PaymentService"
    assert cls.method_names == ["processPayment", "getAmount"]  # ctor/dtor are not methods
    assert cls.field_names == ["amount"]


def test_cpp_struct_and_class_both_recognised(tmp_path):
    code = "struct User {\n    int id;\n    char name[50];\n};\nclass Admin {\n    int level;\n};\n"
    module = _module(tmp_path, "types.hpp", code)
    assert {c.name for c in module.classes} == {"User", "Admin"}


def test_cpp_inheritance_forms(tmp_path):
    code = (
        "class Parent {};\n"
        "class A {};\n"
        "class Child : public Parent {};\n"
        "class Multi : public Parent, protected A {};\n"
        "struct S : Parent {};\n"
        "class V : virtual public Parent {};\n"
        "class Q : public ns::Parent {};\n"
    )
    module = _module(tmp_path, "inh.hpp", code)
    by_name = {c.name: c for c in module.classes}
    assert by_name["Parent"].base_name is None
    assert by_name["Child"].base_name == "Parent"
    assert by_name["Multi"].base_name == "Parent"
    assert by_name["Multi"].interface_names == ["A"]
    assert by_name["S"].base_name == "Parent"
    assert by_name["V"].base_name == "Parent"
    assert by_name["Q"].base_name == "Parent"


def test_cpp_namespaces_including_nested_and_cpp17(tmp_path):
    code = (
        "namespace outer {\n"
        "namespace inner {\n"
        "int f() { return 1; }\n"
        "}\n"
        "}\n"
        "namespace a::b {\n"
        "int g() { return 2; }\n"
        "}\n"
    )
    module = _module(tmp_path, "ns.cpp", code)
    assert set(module.raw_tree.namespaces) == {"outer", "inner", "a::b"}
    assert [f.name for f in module.functions] == ["f", "g"]  # namespace functions are free functions


def test_cpp_out_of_line_method_gets_its_owner_class(tmp_path):
    code = "class Foo {\npublic:\n    void bar();\n};\nvoid Foo::bar() { if (true) { } }\n"
    module = _module(tmp_path, "foo.cpp", code)
    bar = next(f for f in module.raw_tree.functions if f.name == "bar")
    assert bar.owner_class == "Foo"
    assert module.functions == []  # not a free function
    assert module.classes[0].method_names == ["bar"]


def test_cpp_includes_system_and_local(tmp_path):
    code = '#include <vector>\n#include <iostream>\n#include "User.hpp"\nint x;\n'
    module = _module(tmp_path, "inc.cpp", code)
    assert set(module.imports) == {"vector", "iostream", "User.hpp"}


def test_cpp_templates_operators_and_ctor_init_lists(tmp_path):
    code = (
        "template <typename T>\n"
        "T maxOf(T a, T b) { return a > b ? a : b; }\n"
        "class P {\n"
        "public:\n"
        "    P(int v) : value{v}, other(0) {}\n"
        "    bool operator==(const P& o) const { return value == o.value; }\n"
        "private:\n"
        "    int value;\n"
        "    int other;\n"
        "};\n"
    )
    module = _module(tmp_path, "t.hpp", code)
    assert module.parse_error is None
    assert "maxOf" in [f.name for f in module.functions]
    cls = module.classes[0]
    assert cls.method_names == ["operator=="]
    assert set(cls.field_names) == {"value", "other"}


def test_cpp_nested_aggregate_declarator_and_enum_member_are_fields(tmp_path):
    code = (
        "class Shape {\n"
        "public:\n"
        "    struct { int a; int b; } pair;\n"
        "    enum Mode { X, Y } mode;\n"
        "    int w{5};\n"
        "    void f();\n"
        "};\n"
    )
    module = _module(tmp_path, "shape.hpp", code)
    shape = next(c for c in module.classes if c.name == "Shape")
    assert shape.field_names == ["pair", "mode", "w"]
    assert shape.method_names == ["f"]


def test_cpp_member_function_calls_are_collected(tmp_path):
    code = "class S {\npublic:\n    void run() { log(); helper::go(); }\n};\n"
    module = _module(tmp_path, "s.hpp", code)
    run = next(f for f in module.raw_tree.functions if f.name == "run")
    assert run.calls == ["helper::go", "log"]


# ------------------------------------------------------------------------- LOC


def test_cpp_loc_with_comments_strings_and_raw_strings():
    code = (
        "// c++ comment\n"
        "/* block */\n"
        'const char* a = "// string";\n'
        'const char* r = R"(/* raw */\n'
        '  // still raw\n'
        ')";\n'
        "int n = 1'000'000; // digit separator is not a char literal\n"
        "\n"
    )
    loc = calculate_loc("cpp", code)
    assert (loc.total, loc.code, loc.comments, loc.blank) == (8, 5, 2, 1)


# ------------------------------------------------------------------ complexity


def test_cpp_complexity_loops_try_catch_and_range_for(tmp_path):
    code = (
        "int f(std::vector<int>& v) {\n"
        "    int s = 0;\n"
        "    for (auto x : v) { s += x; }\n"
        "    for (int i = 0; i < 3; ++i) { }\n"
        "    while (s > 100) { s--; }\n"
        "    try { s++; } catch (const std::exception& e) { s--; }\n"
        "    return s > 0 ? s : 0;\n"
        "}\n"
    )
    assert _only_module(tmp_path, "c.cpp", code).complexity_total == 1 + 2 + 1 + 1 + 1  # for,for,while,catch,?


def test_cpp_complexity_switch_and_logical(tmp_path):
    code = (
        "int f(int v, bool a, bool b) {\n"
        "    switch (v) { case 1: return 1; case 2: return 2; default: break; }\n"
        "    if (a && b || v > 3) return 9;\n"
        "    return 0;\n"
        "}\n"
    )
    assert _only_module(tmp_path, "s.cpp", code).complexity_total == 1 + 2 + 1 + 2  # cases, if, &&, ||


# ------------------------------------------------- shared metrics: coupling etc.


def test_cpp_coupling_includes_and_bases_excluding_own_header(tmp_path):
    _write_project(tmp_path, {
        "User.hpp": "class Base {};\nclass User : public Base {\npublic:\n    void a();\n};\n",
        "User.cpp": '#include "User.hpp"\n#include <iostream>\n#include <string>\nvoid User::a() { }\n',
    })
    analysis = analyze_project(tmp_path)
    by_name = {m.file_name: m for m in analysis.modules}
    assert by_name["User.cpp"].coupling == 2  # iostream, string (own header excluded)
    assert by_name["User.hpp"].coupling == 1  # base class Base


def test_cpp_depth_of_inheritance_uses_project_wide_registry(tmp_path):
    _write_project(tmp_path, {
        "Base.hpp": "class Base {};\n",
        "Mid.hpp": '#include "Base.hpp"\nclass Mid : public Base {};\n',
        "Leaf.hpp": '#include "Mid.hpp"\nclass Leaf : public Mid {};\n',
    })
    by_name = {m.file_name: m for m in analyze_project(tmp_path).modules}
    assert by_name["Base.hpp"].depth_of_inheritance == 0
    assert by_name["Mid.hpp"].depth_of_inheritance == 1
    assert by_name["Leaf.hpp"].depth_of_inheritance == 2


def test_cpp_cohesion_lcom1_for_class_with_bodies(tmp_path):
    # a/b share `x`; c touches only `y`.  Pairs: (a,b) shared, (a,c) and
    # (b,c) disjoint -> LCOM1 = max(2 - 1, 0) = 1
    code = (
        "class K {\n"
        "public:\n"
        "    void a() { x = 1; }\n"
        "    void b() { x = 2; }\n"
        "    void c() { y = 3; }\n"
        "private:\n"
        "    int x;\n"
        "    int y;\n"
        "};\n"
    )
    assert _only_module(tmp_path, "k.hpp", code).cohesion == 1.0


def test_cpp_cohesion_fully_cohesive_class_is_zero(tmp_path):
    code = (
        "class K {\npublic:\n"
        "    void a() { x = 1; }\n    void b() { x = 2; }\n"
        "private:\n    int x;\n};\n"
    )
    assert _only_module(tmp_path, "k2.hpp", code).cohesion == 0.0


def test_cpp_cohesion_is_none_when_bodies_are_in_another_file(tmp_path):
    code = "class K {\npublic:\n    void a();\n    void b();\nprivate:\n    int x;\n};\n"
    assert _only_module(tmp_path, "k3.hpp", code).cohesion is None  # not fabricated


def test_cpp_cohesion_is_none_for_cpp_struct_without_methods(tmp_path):
    assert _only_module(tmp_path, "d.hpp", "struct D { int a; int b; };\n").cohesion is None


def test_cpp_multiple_cpp_files_and_hpp_headers(tmp_path):
    _write_project(tmp_path, {
        "src/main.cpp": '#include "User.hpp"\nint main() { return 0; }\n',
        "src/User.cpp": '#include "User.hpp"\nvoid User::run() { }\n',
        "src/User.hpp": "class User {\npublic:\n    void run();\n};\n",
        "src/Payment.cpp": "int pay(int a) { return a ? 1 : 0; }\n",
    })
    analysis = analyze_project(tmp_path)
    assert analysis.files_analyzed == 4
    assert analysis.languages == ["cpp"]
    assert analysis.total_classes == 1
    assert analysis.files_with_errors == 0


def test_mixed_python_java_c_cpp_project(tmp_path):
    _write_project(tmp_path, {
        "a.py": "def f():\n    return 1\n",
        "B.java": "public class B {\n    public void m() {}\n}\n",
        "c.c": "int c(void) { return 1; }\n",
        "d.cpp": "int d() { return 1; }\n",
    })
    analysis = analyze_project(tmp_path)
    assert analysis.languages == ["c", "cpp", "java", "python"]
    assert analysis.files_with_errors == 0


# ----------------------------------------------------- scoring / risk (C++ -> ...)


def test_cpp_to_metrics_to_scoring_to_risk(tmp_path):
    branches = "\n".join(f"    if (a == {i} && b > {i}) r += {i};" for i in range(40))
    _write_project(tmp_path, {
        "simple.cpp": "int one() { return 1; }\n",
        "tangled.cpp": f"int tangled(int a, int b) {{\n    int r = 0;\n{branches}\n    return r;\n}}\n",
    })
    analysis = analyze_project(tmp_path)
    scores = [score_module(m) for m in analysis.modules]
    risks = [calculate_module_risk(m, s) for m, s in zip(analysis.modules, scores)]

    health = calculate_project_health(analysis, scores)
    assert 0 <= health.health_score <= 100
    ranked = rank_risky_modules(risks)
    assert ranked[0]["file_name"] == "tangled.cpp"
    assert ranked[0]["risk_score"] > ranked[-1]["risk_score"]


# ---------------------------------------------------------------------- upload


def _zip_bytes(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def test_upload_cpp_project_through_existing_endpoint(client):
    data = _zip_bytes({
        "my-cpp-project/src/main.cpp": '#include "User.hpp"\nint main() { return 0; }\n',
        "my-cpp-project/src/User.cpp": '#include "User.hpp"\nvoid User::run() { if (true) { } }\n',
        "my-cpp-project/src/User.hpp": "class User {\npublic:\n    void run();\n};\n",
        "my-cpp-project/src/Payment.cpp": "int pay(int a) { return a; }\n",
    })
    response = client.post(
        "/api/projects/upload", files={"file": ("my-cpp-project.zip", data, "application/zip")}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["analysis"]["project"]["languages"] == ["cpp"]
    assert body["analysis"]["project"]["files"] == 4
    assert body["analysis"]["project"]["classes"] == 1


def test_cpp_project_works_with_report_endpoint(client):
    data = _zip_bytes({"src/main.cpp": "int main() { return 0; }\n"})
    project_id = client.post(
        "/api/projects/upload", files={"file": ("cpp.zip", data, "application/zip")}
    ).json()["project_id"]
    report = client.get(f"/api/projects/{project_id}/report")
    assert report.status_code == 200
    assert report.content.startswith(b"%PDF")
