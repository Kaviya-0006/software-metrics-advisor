"""
Phase 8C — frontend ↔ backend API matching.

Unit tests drive the matching rules directly with hand-built `ApiCall` /
`ApiEndpoint` objects; end-to-end tests go through the real scanner, the 8A
architecture, the 8B endpoint detector and the 8C call detector. These are
*static-analysis findings*: nothing here may claim an endpoint is definitely
broken or unused.
"""

import random
import shutil
from pathlib import Path

import pytest

from app.analyzer.api_calls import ApiCall
from app.analyzer.api_endpoints import ApiEndpoint
from app.analyzer.api_matching import (
    NOTE,
    STATUS_MATCHED,
    STATUS_UNCERTAIN,
    STATUS_UNMATCHED_CALL,
    STATUS_UNUSED_ENDPOINT,
    analyze_api_mapping,
    match_api_calls,
    path_shape,
)
from app.analyzer.architecture import analyze_architecture
from app.analyzer.engine import analyze_project

HIGH, MEDIUM, LOW = "high", "medium", "low"


def call(method, path, *, line=3, file="frontend/api.ts", library="fetch", confidence=HIGH, layer="frontend", external=False):
    return ApiCall(method, path, file, line, "typescript", library, confidence, layer, external)


def endpoint(method, path, *, line=12, file="backend/UserController.java", framework="Spring", confidence=HIGH, layer="backend"):
    return ApiEndpoint(method, path, file, line, "java", framework, confidence, "handler", layer)


def one(calls, endpoints):
    report = match_api_calls(calls, endpoints)
    assert len(report.results) == 1, report.results
    return report.results[0]


# ===================================================================== shapes


@pytest.mark.parametrize(
    "path,expected",
    [
        ("/users/:id", ("users", "{}")),
        ("/users/{id}", ("users", "{}")),
        ("/users/{}", ("users", "{}")),
        ("/users/${id}", ("users", "{}")),
        ("/users/<id>", ("users", "{}")),
        ("/users/<int:user_id>", ("users", "{}")),
        ("/items/{item_id:int}", ("items", "{}")),
        ("/users/:id?", ("users", "{}")),
        ("/files/{name}.json", ("files", "{}.json")),
        ("/users/123", ("users", "123")),  # a static segment is never turned into a parameter
        ("/users//123/", ("users", "123")),
        ("/", ()),
    ],
)
def test_path_shape_normalizes_only_explicit_parameter_syntax(path, expected):
    assert path_shape(path) == expected


# ================================================================ unit rules


def test_exact_match_has_evidence_on_both_sides():
    result = one([call("GET", "/api/users", line=7)], [endpoint("GET", "/api/users", line=21)])
    assert (result.status, result.confidence, result.match_quality) == (STATUS_MATCHED, HIGH, "exact")
    data = result.to_dict()
    assert data["frontend"] == {"file": "frontend/api.ts", "line": 7, "call": "GET /api/users", "library": "fetch", "layer": "frontend"}
    assert data["backend"] == {
        "file": "backend/UserController.java", "line": 21, "endpoint": "GET /api/users", "framework": "Spring", "layer": "backend",
    }
    assert "matches backend GET /api/users" in data["reason"]


def test_method_mismatch_is_not_a_match_and_the_finding_says_why():
    report = match_api_calls([call("POST", "/users")], [endpoint("GET", "/users")])
    result = report.unmatched_frontend_calls[0]
    assert result.status == STATUS_UNMATCHED_CALL
    assert "only for GET" in result.reason
    assert result.call is not None and result.endpoint is None
    assert [r.status for r in report.results] == [STATUS_UNMATCHED_CALL, STATUS_UNUSED_ENDPOINT]  # GET /users has no consumer


def test_path_mismatch_is_not_a_match():
    report = match_api_calls([call("GET", "/api/orders")], [endpoint("GET", "/api/users")])
    assert [r.status for r in report.results] == [STATUS_UNMATCHED_CALL, STATUS_UNUSED_ENDPOINT]


@pytest.mark.parametrize(
    "endpoint_path",
    ["/users/:id", "/users/{id}", "/users/{user_id:int}", "/users/<int:id>", "/users/{}", "/users/${id}"],
)
def test_parameter_forms_are_comparable(endpoint_path):
    result = one([call("GET", "/users/{}")], [endpoint("GET", endpoint_path)])
    assert result.status == STATUS_MATCHED
    assert result.match_quality == ("exact" if endpoint_path == "/users/{}" else "normalized")
    assert result.confidence == (HIGH if endpoint_path == "/users/{}" else MEDIUM)  # normalization costs a level


def test_a_literal_segment_may_satisfy_a_parameter_segment_but_only_at_low_confidence():
    result = one([call("GET", "/users/123")], [endpoint("GET", "/users/{id}")])
    assert (result.status, result.match_quality, result.confidence) == (STATUS_MATCHED, "parameter-vs-static", LOW)


def test_a_call_parameter_does_not_match_a_static_endpoint_segment():
    report = match_api_calls([call("GET", "/users/{}")], [endpoint("GET", "/users/me")])
    assert [r.status for r in report.results] == [STATUS_UNMATCHED_CALL, STATUS_UNUSED_ENDPOINT]


def test_static_endpoint_is_preferred_over_a_parameter_endpoint():
    report = match_api_calls([call("GET", "/users/me")], [endpoint("GET", "/users/{id}", line=5), endpoint("GET", "/users/me", line=9)])
    matched = report.matched[0]
    assert matched.endpoint.path == "/users/me" and matched.match_quality == "exact"
    assert [r.endpoint.path for r in report.unused_backend_endpoints] == ["/users/{id}"]


def test_a_missing_prefix_is_not_assumed_and_the_finding_hints_at_a_mount():
    report = match_api_calls([call("GET", "/api/users")], [endpoint("GET", "/users")])
    unmatched = report.unmatched_frontend_calls[0]
    assert "without the leading /api" in unmatched.reason and "mount" in unmatched.reason
    assert unmatched.confidence == LOW  # likely an unresolved mount, not a real gap
    assert [r.status for r in report.results] == [STATUS_UNMATCHED_CALL, STATUS_UNUSED_ENDPOINT]


def test_a_proven_full_path_matches():
    assert one([call("GET", "/api/users")], [endpoint("GET", "/api/users")]).status == STATUS_MATCHED


def test_an_any_method_endpoint_matches_every_method_at_most_medium():
    for method in ("GET", "POST", "DELETE"):
        result = one([call(method, "/x")], [endpoint("ANY", "/x")])
        assert (result.status, result.confidence) == (STATUS_MATCHED, MEDIUM)


def test_trailing_slashes_do_not_prevent_a_match():
    assert one([call("GET", "/users")], [endpoint("GET", "/users/")]).status == STATUS_MATCHED


def test_wildcard_endpoints_do_not_swallow_every_call():
    report = match_api_calls([call("GET", "/anything/at/all")], [endpoint("GET", "*"), endpoint("GET", "/file/*")])
    assert report.matched == []


def test_many_calls_can_consume_one_endpoint():
    report = match_api_calls([call("GET", "/x", line=1), call("GET", "/x", line=9)], [endpoint("GET", "/x")])
    assert len(report.matched) == 2 and report.unused_backend_endpoints == []


def test_equally_good_duplicate_endpoints_are_all_consumed():
    twin = [endpoint("GET", "/x", file="backend/a.py"), endpoint("GET", "/x", file="backend/b.py")]
    report = match_api_calls([call("GET", "/x")], twin)
    assert len(report.matched) == 1 and report.unused_backend_endpoints == []


# ============================================================ result categories


def test_unmatched_frontend_call_uses_cautious_wording():
    report = match_api_calls([call("GET", "/api/orders")], [endpoint("GET", "/api/users")])
    result = report.unmatched_frontend_calls[0]
    assert result.reason.startswith("Potential integration issue:")
    assert "GET /api/orders" in result.reason
    assert result.confidence in (MEDIUM, LOW)  # never above MEDIUM


def test_unused_backend_endpoint_uses_cautious_wording():
    report = match_api_calls([call("GET", "/api/users")], [endpoint("GET", "/api/users"), endpoint("POST", "/api/payment", line=40)])
    result = report.unused_backend_endpoints[0]
    assert (result.method, result.path) == ("POST", "/api/payment")
    assert result.reason.startswith("Potentially unused backend endpoint:")
    assert "could not find a matching frontend consumer" in result.reason
    assert "may still be used by clients outside the analyzed code" in result.reason
    assert result.confidence in (MEDIUM, LOW) and result.call is None and result.endpoint is not None


def test_findings_never_claim_certainty():
    report = match_api_calls(
        [call("GET", "/a"), call("GET", "/b", external=True), call("GET", "/c", library="unknown", confidence=LOW)],
        [endpoint("GET", "/d")],
    )
    text = " ".join(r.reason for r in report.results).lower() + NOTE.lower()
    for forbidden in ("definitely", "is broken", "will fail", "dead code", "certainly", "is unused"):
        assert forbidden not in text


def test_external_host_calls_are_uncertain_not_unmatched():
    report = match_api_calls([call("GET", "/users/octocat", external=True, confidence=LOW)], [endpoint("GET", "/other")])
    assert report.uncertain[0].confidence == LOW
    assert "another host" in report.uncertain[0].reason
    assert report.unmatched_frontend_calls == []


def test_unidentified_client_wrappers_are_uncertain_not_unmatched():
    report = match_api_calls([call("GET", "/x", library="unknown", confidence=LOW)], [endpoint("GET", "/other")])
    assert [r.status for r in report.results] == [STATUS_UNUSED_ENDPOINT, STATUS_UNCERTAIN]
    assert "could not be identified as an HTTP client" in report.uncertain[0].reason


def test_a_project_without_detected_endpoints_cannot_have_unmatched_calls():
    report = match_api_calls([call("GET", "/x")], [])
    assert [r.status for r in report.results] == [STATUS_UNCERTAIN]
    assert "no backend endpoints were detected" in report.results[0].reason


def test_a_project_without_detected_calls_cannot_have_unused_endpoints():
    report = match_api_calls([], [endpoint("GET", "/x"), endpoint("POST", "/y")])
    assert [r.status for r in report.results] == [STATUS_UNCERTAIN, STATUS_UNCERTAIN]
    assert "no frontend API calls were detected" in report.results[0].reason


def test_an_external_call_that_does_match_is_low_confidence():
    result = one([call("GET", "/x", external=True, confidence=LOW)], [endpoint("GET", "/x")])
    assert (result.status, result.confidence) == (STATUS_MATCHED, LOW)


# ============================================================ match confidence


def test_confidence_needs_clear_layers_for_high():
    clear = one([call("GET", "/x", layer="frontend")], [endpoint("GET", "/x", layer="backend")])
    assert clear.confidence == HIGH
    api_layer = one([call("GET", "/x", layer="frontend")], [endpoint("GET", "/x", layer="api")])
    assert api_layer.confidence == HIGH
    for call_layer, endpoint_layer in [("unknown", "backend"), ("frontend", "unknown"), (None, None), ("shared", "backend")]:
        weaker = one([call("GET", "/x", layer=call_layer)], [endpoint("GET", "/x", layer=endpoint_layer)])
        assert weaker.confidence == MEDIUM, (call_layer, endpoint_layer)


def test_match_confidence_never_exceeds_either_side():
    assert one([call("GET", "/x", confidence=MEDIUM)], [endpoint("GET", "/x")]).confidence == MEDIUM
    assert one([call("GET", "/x")], [endpoint("GET", "/x", confidence=MEDIUM)]).confidence == MEDIUM
    assert one([call("GET", "/x", confidence=LOW)], [endpoint("GET", "/x")]).confidence == LOW


# ============================================================ report / summary


def test_report_summary_and_serialization():
    report = match_api_calls(
        [call("GET", "/a"), call("GET", "/gone"), call("GET", "/ext", external=True, confidence=LOW)],
        [endpoint("GET", "/a"), endpoint("DELETE", "/unused")],
    )
    assert report.to_dict() == {
        "total_calls": 3, "total_endpoints": 2, "matched": 1, "unmatched_frontend_calls": 1,
        "unused_backend_endpoints": 1, "uncertain": 1, "matched_by_confidence": {"high": 1}, "note": NOTE,
    }
    assert report.by_status == {STATUS_MATCHED: 1, STATUS_UNMATCHED_CALL: 1, STATUS_UNUSED_ENDPOINT: 1, STATUS_UNCERTAIN: 1}
    assert "results" not in report.to_dict() and len(report.to_dict(include_results=True)["results"]) == 4
    assert [r.status for r in report.results] == [STATUS_MATCHED, STATUS_UNMATCHED_CALL, STATUS_UNUSED_ENDPOINT, STATUS_UNCERTAIN]
    with pytest.raises(Exception):
        report.results[0].status = "changed"  # immutable


def test_matching_is_deterministic_regardless_of_input_order():
    calls = [call("GET", f"/r{i}", line=i) for i in range(6)]
    endpoints = [endpoint("GET", f"/r{i}", line=i) for i in range(0, 6, 2)] + [endpoint("POST", "/extra")]
    baseline = match_api_calls(calls, endpoints).to_dict(include_results=True)
    shuffled_calls, shuffled_endpoints = calls[:], endpoints[:]
    random.Random(5).shuffle(shuffled_calls)
    random.Random(6).shuffle(shuffled_endpoints)
    assert match_api_calls(shuffled_calls, shuffled_endpoints).to_dict(include_results=True) == baseline


# ===================================================== end-to-end: real files


def mapping(tmp_path: Path, files: dict[str, str]):
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    analysis = analyze_project(tmp_path, retain_parsed=True)
    return analyze_api_mapping(analysis.parsed_modules, analyze_architecture(analysis.modules))


USER_CONTROLLER = (
    '@RestController\n@RequestMapping("/api")\npublic class UserController {\n'
    '    @GetMapping("/users")\n    public String list() { return ""; }\n'
    '    @PostMapping("/payment")\n    public String pay() { return ""; }\n}\n'
)


def test_mixed_project_typescript_frontend_and_java_backend(tmp_path):
    files = {
        "frontend/api.ts": 'export const users = () => fetch("/api/users");\n',
        "backend/UserController.java": USER_CONTROLLER,
    }
    report = mapping(tmp_path, files)
    matched = report.matched[0]
    assert (matched.status, matched.confidence, matched.match_quality) == (STATUS_MATCHED, HIGH, "exact")
    assert matched.call.source_file == "frontend/api.ts" and matched.call.layer == "frontend"
    assert matched.endpoint.source_file == "backend/UserController.java" and matched.endpoint.framework == "Spring"
    assert matched.endpoint.line_number == 4 and matched.endpoint.path == "/api/users"


def test_the_three_documented_scenarios(tmp_path):
    files = {
        "frontend/api.ts": 'fetch("/api/users");\nfetch("/api/orders");\n',
        "backend/UserController.java": USER_CONTROLLER,
    }
    report = mapping(tmp_path, files)
    assert [(r.method, r.path) for r in report.matched] == [("GET", "/api/users")]  # ✓ MATCHED
    assert [(r.method, r.path) for r in report.unmatched_frontend_calls] == [("GET", "/api/orders")]  # ⚠ potential issue
    assert [(r.method, r.path) for r in report.unused_backend_endpoints] == [("POST", "/api/payment")]  # ℹ potentially unused
    assert "Potential integration issue" in report.unmatched_frontend_calls[0].reason
    assert "Potentially unused backend endpoint" in report.unused_backend_endpoints[0].reason


def test_react_axios_with_template_parameter_against_fastapi(tmp_path):
    files = {
        "frontend/src/api.ts": 'import axios from "axios";\nexport const get = (id: number) => axios.get(`/api/items/${id}`);\n',
        "backend/main.py": 'from fastapi import FastAPI\napp = FastAPI()\n@app.get("/api/items/{item_id}")\ndef item(item_id: int): ...\n',
    }
    result = mapping(tmp_path, files).matched[0]
    assert (result.match_quality, result.confidence) == ("normalized", MEDIUM)
    assert result.endpoint.framework == "FastAPI" and result.call.library == "axios"


def test_a_proven_express_mount_makes_a_prefixed_call_match(tmp_path):
    backend = (
        'const express = require("express");\nconst app = express();\nconst router = express.Router();\n'
        'router.get("/users", h);\napp.use("/api", router);\n'
    )
    proven = mapping(tmp_path / "a", {"frontend/api.js": 'fetch("/api/users");\n', "backend/server.js": backend})
    assert [r.status for r in proven.results] == [STATUS_MATCHED]

    unproven = backend.replace('app.use("/api", router);', 'app.use(PREFIX, router);')
    report = mapping(tmp_path / "b", {"frontend/api.js": 'fetch("/api/users");\n', "backend/server.js": unproven})
    assert STATUS_UNMATCHED_CALL in report.by_status  # the prefix could not be proven
    assert "mount" in report.unmatched_frontend_calls[0].reason


def test_flask_blueprint_prefix_makes_a_prefixed_call_match(tmp_path):
    files = {
        "frontend/api.js": 'fetch("/api/users", { method: "POST" });\n',
        "backend/app.py": (
            'from flask import Flask, Blueprint\napp = Flask(__name__)\nbp = Blueprint("a", __name__)\n'
            '@bp.route("/users", methods=["POST"])\ndef add(): ...\n'
            'app.register_blueprint(bp, url_prefix="/api")\n'
        ),
    }
    assert [r.status for r in mapping(tmp_path, files).results] == [STATUS_MATCHED]


def test_three_backend_frameworks_and_one_frontend(tmp_path):
    files = {
        "frontend/api.js": 'fetch("/py");\nfetch("/js");\nfetch("/api/java");\nfetch("/nowhere");\n',
        "backend/a.py": 'from fastapi import FastAPI\napp = FastAPI()\n@app.get("/py")\ndef a(): ...\n',
        "server/b.js": 'const express = require("express");\nconst app = express();\napp.get("/js", h);\n',
        "backend/C.java": '@RequestMapping("/api")\nclass C {\n    @GetMapping("/java")\n    public String c() { return ""; }\n}\n',
    }
    report = mapping(tmp_path, files)
    assert report.to_dict()["matched"] == 3 and report.to_dict()["unmatched_frontend_calls"] == 1
    assert {r.endpoint.framework for r in report.matched} == {"FastAPI", "Express", "Spring"}


def test_frontend_only_and_backend_only_projects_are_uncertain_not_alarming(tmp_path):
    frontend_only = mapping(tmp_path / "a", {"frontend/api.js": 'fetch("/x");\n'})
    assert frontend_only.by_status == {STATUS_UNCERTAIN: 1}
    backend_only = mapping(tmp_path / "b", {"backend/a.py": 'from fastapi import FastAPI\napp = FastAPI()\n@app.get("/x")\ndef a(): ...\n'})
    assert backend_only.by_status == {STATUS_UNCERTAIN: 1}


def test_unknown_layers_still_match_but_at_most_medium(tmp_path):
    files = {
        "src/client.js": 'fetch("/x");\n',
        "src/server.py": 'from fastapi import FastAPI\napp = FastAPI()\n@app.get("/x")\ndef a(): ...\n',
    }
    result = mapping(tmp_path, files).matched[0]
    assert (result.call.layer, result.endpoint.layer, result.confidence) == ("unknown", "unknown", MEDIUM)


def test_test_files_on_either_side_are_not_findings(tmp_path):
    files = {
        "frontend/api.js": 'fetch("/real");\n',
        "backend/a.py": 'from fastapi import FastAPI\napp = FastAPI()\n@app.get("/real")\ndef a(): ...\n',
        "frontend/__tests__/x.test.js": 'fetch("/mocked");\n',
        "tests/test_a.py": 'from fastapi import FastAPI\napp = FastAPI()\n@app.get("/test-only")\ndef t(): ...\n',
    }
    report = mapping(tmp_path, files)
    assert report.by_status == {STATUS_MATCHED: 1}


def test_orchestrator_exposes_the_detected_inputs(tmp_path):
    files = {"frontend/a.js": 'fetch("/x");\n', "backend/a.py": 'from fastapi import FastAPI\napp = FastAPI()\n@app.get("/x")\ndef a(): ...\n'}
    report = mapping(tmp_path, files)
    assert report.call_summary.total_calls == 1 and report.endpoint_summary.total_endpoints == 1
    assert (report.total_calls, report.total_endpoints) == (1, 1)


def test_this_projects_own_frontend_and_backend_map_to_each_other(tmp_path):
    """Real full-stack code: the React frontend and FastAPI backend of this project."""
    root = Path(__file__).resolve().parents[2]
    shutil.copytree(root / "frontend" / "src", tmp_path / "frontend" / "src")
    shutil.copytree(root / "backend" / "app", tmp_path / "backend" / "app", ignore=shutil.ignore_patterns("__pycache__"))
    analysis = analyze_project(tmp_path, retain_parsed=True)
    report = analyze_api_mapping(analysis.parsed_modules, analyze_architecture(analysis.modules))
    assert (report.total_calls, report.total_endpoints) == (9, 10)
    assert len(report.matched) == 9 and report.unmatched_frontend_calls == [] and report.uncertain == []
    assert [(r.method, r.path) for r in report.unused_backend_endpoints] == [("GET", "/")]  # the root banner route
    assert {r.confidence for r in report.matched} == {MEDIUM}  # the frontend base URL is an env override
    upload = next(r for r in report.matched if r.path == "/api/projects/upload")
    assert upload.method == "POST" and upload.endpoint.source_file.endswith("api/upload.py")
