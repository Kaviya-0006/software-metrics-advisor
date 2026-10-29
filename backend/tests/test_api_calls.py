"""
Phase 8C — frontend API-call detection (static analysis only).

Every test goes through the real scanner/analysis pipeline
(`analyze_project(..., retain_parsed=True)`), so the detector sees exactly the
token streams the metrics use.
"""

from pathlib import Path

import pytest

from app.analyzer import api_calls
from app.analyzer.api_calls import ApiCall, detect_api_calls, detect_calls_in_module
from app.analyzer.architecture import analyze_architecture
from app.analyzer.engine import analyze_project

HIGH, MEDIUM, LOW = "high", "medium", "low"


def run(tmp_path: Path, files: dict[str, str], architecture: bool = False):
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    analysis = analyze_project(tmp_path, retain_parsed=True)
    arch = analyze_architecture(analysis.modules) if architecture else None
    return detect_api_calls(analysis.parsed_modules, arch)


def calls(tmp_path: Path, code: str, name: str = "frontend/api.js", architecture: bool = True):
    return run(tmp_path, {name: code}, architecture)


def pairs(summary):
    return [(c.method, c.path) for c in summary.calls]


# ====================================================================== fetch


def test_fetch_defaults_to_get_with_line_and_library(tmp_path):
    code = 'const a = 1;\nfetch("/api/users");\n'
    summary = calls(tmp_path, code)
    call = summary.calls[0]
    assert (call.method, call.path, call.line_number) == ("GET", "/api/users", 2)
    assert (call.library, call.language, call.confidence, call.layer) == ("fetch", "javascript", HIGH, "frontend")


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH"])
def test_fetch_with_an_options_object(tmp_path, method):
    code = f'fetch("/api/users", {{\n  method: "{method}",\n  headers: {{ "Content-Type": "application/json" }},\n  body: JSON.stringify(x),\n}});\n'
    assert pairs(calls(tmp_path, code)) == [(method, "/api/users")]


def test_fetch_method_is_case_insensitive_and_options_may_omit_it(tmp_path):
    code = 'fetch("/a", { method: "post" });\nfetch("/b", { headers: {} });\n'
    assert pairs(calls(tmp_path, code)) == [("POST", "/a"), ("GET", "/b")]


def test_fetch_template_literal_parameters_become_placeholders(tmp_path):
    code = "fetch(`/api/users/${id}`);\nfetch(`/api/users/${user.id}/posts/${postId}`);\nfetch(`/api/static`);\n"
    assert pairs(calls(tmp_path, code)) == [
        ("GET", "/api/users/{}"), ("GET", "/api/users/{}/posts/{}"), ("GET", "/api/static"),
    ]


def test_fetch_promise_and_await_forms_and_window_fetch(tmp_path):
    code = (
        'fetch("/a").then((r) => r.json());\n'
        'async function f() { const r = await fetch("/b"); return window.fetch("/c"); }\n'
        'globalThis.fetch("/d");\n'
    )
    assert pairs(calls(tmp_path, code)) == [("GET", "/a"), ("GET", "/b"), ("GET", "/c"), ("GET", "/d")]


def test_fetch_with_non_static_method_or_options_is_skipped_and_counted(tmp_path):
    code = (
        'fetch("/a", { method: verb });\n'
        'fetch("/b", options);\n'
        'fetch("/c", { ...base });\n'
        'fetch("/d", { method: "GET" });\n'
    )
    summary = calls(tmp_path, code)
    assert pairs(summary) == [("GET", "/d")]
    assert summary.dynamic_urls_skipped == 3


# ===================================================================== axios


@pytest.mark.parametrize("verb", ["get", "post", "put", "patch", "delete"])
def test_axios_verbs(tmp_path, verb):
    code = f'import axios from "axios";\naxios.{verb}("/api/users");\n'
    summary = calls(tmp_path, code)
    assert pairs(summary) == [(verb.upper(), "/api/users")]
    assert (summary.calls[0].library, summary.calls[0].confidence) == ("axios", HIGH)


def test_axios_verbs_with_a_body_and_config(tmp_path):
    code = 'import axios from "axios";\naxios.post("/api/users", { name: "x" }, { headers: {} });\n'
    assert pairs(calls(tmp_path, code)) == [("POST", "/api/users")]


def test_axios_config_object_forms(tmp_path):
    code = (
        'import axios from "axios";\n'
        'axios({ url: "/api/users", method: "POST", data: x });\n'
        'axios({ url: "/api/default" });\n'
        'axios.request({ url: "/api/req", method: "delete" });\n'
    )
    assert pairs(calls(tmp_path, code)) == [("POST", "/api/users"), ("GET", "/api/default"), ("DELETE", "/api/req")]


def test_axios_config_with_a_dynamic_url_or_method_is_skipped(tmp_path):
    code = (
        'import axios from "axios";\n'
        "axios({ url: target, method: 'GET' });\n"
        "axios({ url: '/a', method: m });\n"
        "axios({ ...cfg });\n"
    )
    summary = calls(tmp_path, code)
    assert summary.total_calls == 0 and summary.dynamic_urls_skipped == 3


def test_axios_without_an_import_is_medium_confidence(tmp_path):
    summary = calls(tmp_path, 'axios.get("/api/users");\n')
    assert summary.calls[0].confidence == MEDIUM


def test_axios_instance_with_a_literal_base_url(tmp_path):
    code = (
        'import axios from "axios";\n'
        'const api = axios.create({ baseURL: "/api", timeout: 1000 });\n'
        'api.get("/users");\n'
        "api.post(`/users/${id}/roles`);\n"
        'api({ url: "/ping", method: "GET" });\n'
    )
    summary = calls(tmp_path, code)
    assert pairs(summary) == [("GET", "/api/users"), ("POST", "/api/users/{}/roles"), ("GET", "/api/ping")]
    assert {c.library for c in summary.calls} == {"axios"}
    assert {c.confidence for c in summary.calls} == {MEDIUM}  # a base URL was involved


def test_axios_instance_base_url_from_a_constant_and_typed_declaration(tmp_path):
    code = (
        'import axios from "axios";\n'
        'const BASE = "http://localhost:5000/api";\n'
        "const client: AxiosInstance = axios.create({ baseURL: BASE });\n"
        'client.get("/users");\n'
    )
    assert pairs(calls(tmp_path, code, name="frontend/api.ts")) == [("GET", "/api/users")]


def test_axios_instance_without_a_base_url_uses_the_path_as_is(tmp_path):
    code = 'import axios from "axios";\nconst api = axios.create();\napi.get("/users");\n'
    assert pairs(calls(tmp_path, code)) == [("GET", "/users")]


def test_axios_instance_with_a_dynamic_base_url_is_skipped(tmp_path):
    code = 'import axios from "axios";\nconst api = axios.create({ baseURL: process.env.API });\napi.get("/users");\n'
    summary = calls(tmp_path, code)
    assert summary.total_calls == 0 and summary.dynamic_urls_skipped == 1


def test_absolute_axios_url_overrides_the_instance_base(tmp_path):
    code = (
        'import axios from "axios";\nconst api = axios.create({ baseURL: "/api" });\n'
        'api.get("http://localhost:9000/other");\n'
    )
    assert pairs(calls(tmp_path, code)) == [("GET", "/other")]


# ============================================================ XMLHttpRequest


def test_xhr_open_get_and_post(tmp_path):
    code = (
        "const xhr = new XMLHttpRequest();\n"
        'xhr.open("GET", "/api/users");\n'
        'xhr.send();\n'
        'xhr.open("post", "/api/users", true);\n'
    )
    summary = calls(tmp_path, code)
    assert pairs(summary) == [("GET", "/api/users"), ("POST", "/api/users")]
    assert {(c.library, c.confidence) for c in summary.calls} == {("XMLHttpRequest", HIGH)}


def test_xhr_receiver_not_created_in_the_file_is_medium(tmp_path):
    assert calls(tmp_path, 'req.open("GET", "/api/x");\n').calls[0].confidence == MEDIUM


def test_open_calls_that_are_not_xhr_are_ignored(tmp_path):
    code = (
        'window.open("/page");\n'
        'fs.open("/etc/hosts", "r");\n'
        'db.open("connect", "/x");\n'
        'xhr.open(method, "/x");\n'
    )
    assert calls(tmp_path, code).total_calls == 0


# ================================================================ path forms


@pytest.mark.parametrize(
    "url,expected",
    [
        ('"/api/users"', "/api/users"),
        ('"/api/users/"', "/api/users"),
        ('"/api//users"', "/api/users"),
        ('"/api/users?page=2&sort=asc"', "/api/users"),
        ('"/api/users#top"', "/api/users"),
        ('"/users/:id"', "/users/:id"),
        ('"/users/{id}"', "/users/{id}"),
        ("`/users/${id}`", "/users/{}"),
        ('"/users/123"', "/users/123"),  # a static segment stays static
        ("`/search?q=${q}`", "/search"),
        ('"/"', "/"),
    ],
)
def test_path_forms_are_normalized_but_static_segments_are_kept(tmp_path, url, expected):
    assert pairs(calls(tmp_path, f"fetch({url});\n")) == [("GET", expected)]


def test_literal_base_url_constants_and_concatenation(tmp_path):
    code = (
        'const API = "/api";\n'
        'fetch(API + "/users");\n'
        "fetch(`${API}/users/${id}`);\n"
        'const USERS = API + "/users";\n'
        "fetch(USERS);\n"
        'fetch(API + "/a" + "/b");\n'
    )
    summary = calls(tmp_path, code)
    assert pairs(summary) == [
        ("GET", "/api/users"), ("GET", "/api/users/{}"), ("GET", "/api/users"), ("GET", "/api/a/b"),
    ]
    assert {c.confidence for c in summary.calls} == {MEDIUM}  # constant-resolved


def test_absolute_localhost_url_keeps_only_the_path(tmp_path):
    code = (
        'fetch("http://localhost:5000/api/users");\n'
        'fetch("https://127.0.0.1:8443/api/b?x=1");\n'
        'fetch("http://localhost:8000");\n'
    )
    summary = calls(tmp_path, code)
    assert pairs(summary) == [("GET", "/api/users"), ("GET", "/api/b"), ("GET", "/")]
    assert not any(c.external for c in summary.calls)
    assert all("localhost" not in c.path and "127" not in c.path for c in summary.calls)  # no host is stored


def test_external_host_url_is_flagged_and_low_confidence(tmp_path):
    summary = calls(tmp_path, 'fetch("https://api.github.com/users/octocat");\n')
    call = summary.calls[0]
    assert (call.path, call.external, call.confidence) == ("/users/octocat", True, LOW)


def test_environment_override_with_a_literal_fallback_resolves_at_medium(tmp_path):
    # the exact pattern used by this project's own frontend
    code = (
        'const BASE = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";\n'
        "fetch(`${BASE}/api/projects/${projectId}`);\n"
        'const OTHER = process.env.REACT_APP_API ?? "/api";\n'
        'fetch(OTHER + "/health");\n'
    )
    summary = calls(tmp_path, code)
    assert pairs(summary) == [("GET", "/api/projects/{}"), ("GET", "/api/health")]
    assert {c.confidence for c in summary.calls} == {MEDIUM}


def test_dynamic_urls_are_skipped_and_counted_never_guessed(tmp_path):
    code = (
        "fetch(url);\n"
        "fetch(getUrl());\n"
        'fetch("/users/" + id);\n'          # an unresolved operand
        "fetch(`${unknownBase}/users`);\n"  # an unknown base: cannot be reconstructed
        "fetch(cond ? '/a' : '/b');\n"
        'fetch("users");\n'                  # relative, base unknown
        'fetch("/ok");\n'
    )
    summary = calls(tmp_path, code)
    assert pairs(summary) == [("GET", "/ok")]
    assert summary.dynamic_urls_skipped == 6


def test_constants_defined_twice_with_different_values_are_not_used(tmp_path):
    code = 'const A = "/one";\nconst A = "/two";\nfetch(A + "/x");\n'
    summary = calls(tmp_path, code)
    assert summary.total_calls == 0 and summary.dynamic_urls_skipped == 1


# ============================================= unidentified client wrappers


def test_client_wrapper_calls_are_unknown_library_at_low_confidence(tmp_path):
    code = 'import api from "./api";\napi.get("/users");\nhttp.post("/users", body);\n'
    summary = calls(tmp_path, code)
    assert pairs(summary) == [("GET", "/users"), ("POST", "/users")]
    assert {(c.library, c.confidence) for c in summary.calls} == {("unknown", LOW)}


def test_angular_style_member_receiver_is_allowed_for_wrappers(tmp_path):
    code = "class S { load() { return this.http.get('/api/users'); } }\n"
    assert pairs(calls(tmp_path, code, name="frontend/s.ts")) == [("GET", "/api/users")]


def test_wrapper_lookalikes_are_not_calls(tmp_path):
    code = (
        'const h = req.get("host");\n'
        'const v = api.get("setting");\n'        # not a path
        'const c = cache.get("/key");\n'          # not a wrapper name
        'const p = params.get("/x");\n'
        "api.get(dynamicPath);\n"
    )
    assert calls(tmp_path, code).total_calls == 0


# ============================================================ false positives


def test_comments_strings_and_documentation_examples_are_ignored(tmp_path):
    code = (
        '// fetch("/commented")\n'
        '/* axios.get("/block") */\n'
        '/** @example fetch("/jsdoc") */\n'
        'const doc = "fetch(\'/in-a-string\')";\n'
        "const tpl = `axios.post('/in-a-template')`;\n"
        'it("calls fetch(\\"/in-a-test-title\\")", () => {});\n'
        'fetch("/real");\n'
    )
    assert pairs(calls(tmp_path, code)) == [("GET", "/real")]


def test_unrelated_functions_named_like_clients_are_ignored(tmp_path):
    code = (
        'fetchUsers("/api/users");\n'
        'myfetch("/api/users");\n'
        'this.fetch("/api/users");\n'
        'cache.fetch("/api/users");\n'
        "class Repo { fetch(url) { return url; } }\n"
        "function fetch(url) { return url; }\n"
        "const o = { fetch: () => 1 };\n"
    )
    assert calls(tmp_path, code).total_calls == 0


def test_typescript_definitions_named_fetch_are_not_counted_as_calls_or_skips(tmp_path):
    # With a return type the token after `)` is `:` not `{`, so only the
    # "definition keyword before the name" rule recognizes these as definitions.
    code = (
        "function fetch(url: string): Promise<Response> { return Promise.reject(); }\n"
        "class Api { async fetch(url: string): Promise<Response> { return Promise.reject(); } }\n"
        'fetch("/ok");\n'
    )
    summary = calls(tmp_path, code, name="frontend/api.ts")
    assert pairs(summary) == [("GET", "/ok")]
    assert summary.dynamic_urls_skipped == 0  # definitions are not "call-like expressions"


def test_a_server_framework_route_is_not_a_frontend_call(tmp_path):
    code = 'const express = require("express");\nconst app = express();\napp.get("/api/users", h);\nrouter.post("/x", h);\n'
    assert calls(tmp_path, code, name="src/server.js", architecture=False).total_calls == 0


def test_jsx_text_that_looks_like_a_call_is_ignored(tmp_path):
    code = 'const A = () => <p>fetch("/in-jsx") and axios.get("/also")</p>;\nfetch("/real");\n'
    assert pairs(calls(tmp_path, code, name="frontend/A.jsx")) == [("GET", "/real")]


def test_malformed_and_non_javascript_sources_never_crash(tmp_path):
    summary = run(tmp_path, {"frontend/bad.js": 'fetch("/x", {\n', "frontend/ok.js": 'fetch("/ok");\n', "a.py": "fetch('/x')\n", "B.java": "class B {}\n"}, True)
    assert pairs(summary) == [("GET", "/ok")]
    assert summary.files_scanned == 1  # the broken file is not searched


def test_a_failing_detector_never_takes_the_analysis_down(tmp_path, monkeypatch):
    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend" / "a.js").write_text('fetch("/x");\n')
    analysis = analyze_project(tmp_path, retain_parsed=True)

    def boom(module, layer):
        raise RuntimeError("detector bug")

    monkeypatch.setattr(api_calls, "_detect", boom)
    assert detect_calls_in_module(analysis.parsed_modules[0]) == ([], 0)


# ====================================================== layers and confidence


FETCH = 'fetch("/api/users");\n'


def test_backend_code_is_not_searched_when_architecture_identifies_it(tmp_path):
    # Server-side HTTP clients must not be treated as frontend calls.
    files = {
        "backend/client.js": 'import axios from "axios";\naxios.get("https://partner.example/api/x");\nfetch("/internal");\n',
        "frontend/app.js": FETCH,
    }
    summary = run(tmp_path, files, architecture=True)
    assert pairs(summary) == [("GET", "/api/users")]
    assert summary.files_excluded_by_layer == 1


def test_test_files_are_not_searched(tmp_path):
    files = {"frontend/__tests__/app.test.js": 'fetch("/mock");\n', "tests/api.spec.ts": 'fetch("/also");\n', "frontend/app.js": FETCH}
    summary = run(tmp_path, files, architecture=True)
    assert pairs(summary) == [("GET", "/api/users")] and summary.files_excluded_by_layer == 2


def test_unknown_layer_files_are_searched_but_capped_at_medium(tmp_path):
    summary = run(tmp_path, {"src/app.js": FETCH}, architecture=True)
    call = summary.calls[0]
    assert (call.layer, call.confidence) == ("unknown", MEDIUM)  # no frontend evidence for the file


def test_frontend_layer_files_get_high_confidence_for_literal_urls(tmp_path):
    assert calls(tmp_path, FETCH).calls[0].confidence == HIGH


def test_without_architecture_there_is_no_frontend_evidence(tmp_path):
    summary = run(tmp_path, {"frontend/app.js": FETCH}, architecture=False)
    assert (summary.calls[0].layer, summary.calls[0].confidence) == (None, MEDIUM)


def test_server_side_imports_outside_a_frontend_file_mark_it_as_server_code(tmp_path):
    files = {
        "src/job.js": 'const fetch = require("node-fetch");\nfetch("/api/x");\n',
        "src/proxy.js": 'const express = require("express");\nconst axios = require("axios");\naxios.get("/api/y");\n',
        "src/view.js": FETCH,
    }
    summary = run(tmp_path, files, architecture=True)
    assert pairs(summary) == [("GET", "/api/users")]
    assert summary.files_skipped_server_side == 2


def test_a_clear_frontend_file_is_trusted_even_if_it_imports_http(tmp_path):
    code = 'import http from "http";\nfetch("/api/users");\n'  # e.g. a Vite/Electron helper
    assert pairs(calls(tmp_path, code)) == [("GET", "/api/users")]


def test_typescript_and_tsx_files_are_searched(tmp_path):
    files = {
        "frontend/api.ts": 'export const load = (): Promise<Response> => fetch("/api/a");\n',
        "frontend/View.tsx": 'export const V = () => { fetch("/api/b"); return <div/>; };\n',
    }
    summary = run(tmp_path, files, architecture=True)
    assert pairs(summary) == [("GET", "/api/b"), ("GET", "/api/a")]  # sorted by path: View.tsx before api.ts
    assert summary.calls_by_language == {"typescript": 2}


# ============================================================ model / summary


def test_call_model_and_summary_serialization(tmp_path):
    files = {
        "frontend/a.js": 'fetch("/a");\nfetch("/b", { method: "POST" });\naxios.get("/c");\n',
        "frontend/b.ts": 'fetch("/d");\n',
    }
    summary = run(tmp_path, files, architecture=True)
    assert summary.to_dict() == {
        "total_calls": 4,
        "calls_by_method": {"GET": 3, "POST": 1},
        "calls_by_library": {"fetch": 3, "axios": 1},
        "calls_by_language": {"javascript": 3, "typescript": 1},
        "calls_by_confidence": {"high": 3, "medium": 1},  # the un-imported axios is MEDIUM
        "files_scanned": 2,
        "files_excluded_by_layer": 0,
        "files_skipped_server_side": 0,
        "dynamic_urls_skipped": 0,
    }
    first = summary.calls[0]
    assert isinstance(first, ApiCall)
    assert set(first.to_dict()) == {"method", "path", "source_file", "line_number", "language", "library", "confidence", "layer", "external"}
    assert "calls" not in summary.to_dict() and len(summary.to_dict(include_calls=True)["calls"]) == 4
    with pytest.raises(Exception):
        first.path = "/changed"  # immutable


def test_calls_are_sorted_by_file_and_line(tmp_path):
    files = {"frontend/z.js": 'fetch("/z");\n', "frontend/a.js": 'fetch("/a2");\nfetch("/a1");\n'}
    summary = run(tmp_path, files, architecture=True)
    assert [(c.source_file, c.line_number) for c in summary.calls] == [
        ("frontend/a.js", 1), ("frontend/a.js", 2), ("frontend/z.js", 1),
    ]


def test_this_projects_own_frontend_api_calls_are_detected(tmp_path):
    """Real code: the React frontend of this very project."""
    import shutil

    source = Path(__file__).resolve().parents[2] / "frontend" / "src"
    shutil.copytree(source, tmp_path / "frontend" / "src")
    analysis = analyze_project(tmp_path, retain_parsed=True)
    summary = detect_api_calls(analysis.parsed_modules, analyze_architecture(analysis.modules))
    assert sorted(pairs(summary)) == sorted(
        [
            ("GET", "/api/health"), ("POST", "/api/projects/upload"), ("GET", "/api/projects"),
            ("GET", "/api/projects/{}"), ("GET", "/api/projects/{}/metrics"), ("GET", "/api/projects/{}/risks"),
            ("GET", "/api/projects/{}/recommendations"), ("GET", "/api/projects/{}/history"),
            ("GET", "/api/projects/{}/report"),
        ]
    )
    assert summary.dynamic_urls_skipped == 0
    assert {c.confidence for c in summary.calls} == {MEDIUM}  # env override with a literal fallback
