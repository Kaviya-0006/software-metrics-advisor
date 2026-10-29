"""
Phase 8D — unit tests for the architecture report assembler.

These drive `build_architecture_report` with the real scanner + parser and the
real 8A/8B/8C analyzers (no mocks of analysis), using lightweight stand-ins only
for the metric-side `ModuleResult` fields the assembler reads (path, language,
LOC, parse_error).
"""

from pathlib import Path
from types import SimpleNamespace

from app.analyzer.architecture_report import (
    PROJECT_BACKEND_ONLY,
    PROJECT_FRONTEND_ONLY,
    PROJECT_FULL_STACK,
    PROJECT_UNDETERMINED,
    build_architecture_report,
    summarize_report,
)
from app.analyzer.parser import parse_file
from app.analyzer.scanner import discover_source_files

FASTAPI_BACKEND = (
    "from fastapi import FastAPI\n"
    "app = FastAPI()\n"
    "@app.get('/api/users')\n"
    "def users():\n"
    "    return []\n"
    "@app.delete('/api/users/{id}')\n"
    "def remove(id):\n"
    "    return 1\n"
)
JS_FRONTEND = (
    "export function load() { return fetch('/api/users'); }\n"
    "export function broken() { return fetch('/api/missing', { method: 'POST' }); }\n"
)


def _report(tmp_path: Path, files: dict[str, str]) -> dict:
    for name, text in files.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    source_files = discover_source_files(tmp_path)
    parsed = [parse_file(sf) for sf in source_files]
    modules = [
        SimpleNamespace(
            file_path=sf.relative_path,
            language=sf.language,
            loc=SimpleNamespace(total=len(Path(sf.absolute_path).read_text().splitlines())),
            parse_error=pm.parse_error,
        )
        for sf, pm in zip(source_files, parsed)
    ]
    return build_architecture_report(modules, parsed)


def test_full_stack_report_sections(tmp_path):
    report = _report(tmp_path, {"backend/main.py": FASTAPI_BACKEND, "frontend/src/api.js": JS_FRONTEND})

    assert report["project_type"] == PROJECT_FULL_STACK
    assert report["summary"]["detected_layers"] == ["frontend", "backend"]
    layers = {layer["layer"]: layer for layer in report["layers"]}
    assert layers["frontend"]["languages"] == {"javascript": 1}
    assert layers["backend"]["languages"] == {"python": 1}
    assert layers["backend"]["files"] == 1 and layers["backend"]["loc"] == 8

    mapping = report["api_mapping"]
    assert mapping["matched"] == 1
    assert mapping["unmatched_frontend_calls"] == 1
    assert mapping["unused_backend_endpoints"] == 1

    matched = mapping["matched_items"][0]
    assert {e["side"] for e in matched["evidence"]} == {"frontend", "backend"}
    for evidence in matched["evidence"]:
        assert evidence["file"] and isinstance(evidence["line"], int) and evidence["line"] >= 1
    assert matched["evidence"][0]["file"] == "frontend/src/api.js"
    assert matched["evidence"][1]["file"] == "backend/main.py"


def test_diagram_is_projection_of_real_results(tmp_path):
    report = _report(tmp_path, {"backend/main.py": FASTAPI_BACKEND, "frontend/src/api.js": JS_FRONTEND})
    diagram = report["diagram"]

    assert [n["id"] for n in diagram["nodes"]] == [layer["layer"] for layer in report["layers"]]
    assert len(diagram["edges"]) == 1
    edge = diagram["edges"][0]
    assert (edge["source"], edge["target"], edge["count"]) == ("frontend", "backend", 1)
    nodes = {n["id"]: n for n in diagram["nodes"]}
    assert nodes["frontend"]["unmatched_calls"] == 1
    assert nodes["backend"]["unused_endpoints"] == 1


def test_frontend_only_project(tmp_path):
    report = _report(tmp_path, {"src/api.js": "fetch('/api/a')\n", "src/util.js": "export const x = 1\n"})
    assert report["project_type"] == PROJECT_FRONTEND_ONLY
    # No backend exists, so the call is "could not assess", never "broken".
    assert report["api_mapping"]["unmatched_frontend_calls"] == 0
    assert report["api_mapping"]["uncertain"] == 1
    assert report["diagram"]["edges"] == []


def test_backend_only_project(tmp_path):
    report = _report(tmp_path, {"backend/main.py": FASTAPI_BACKEND})
    assert report["project_type"] == PROJECT_BACKEND_ONLY
    assert report["api_mapping"]["unused_backend_endpoints"] == 0
    assert report["api_mapping"]["uncertain"] == 2
    assert report["diagram"]["edges"] == []


def test_undetermined_when_no_evidence(tmp_path):
    report = _report(tmp_path, {"main.py": "def f():\n    return 1\n"})
    assert report["project_type"] == PROJECT_UNDETERMINED
    assert any(u["kind"] == "layer" and "could not be assigned" in u["message"] for u in report["uncertainties"])


def test_mixed_language_zip(tmp_path):
    report = _report(
        tmp_path,
        {
            "backend/main.py": FASTAPI_BACKEND,
            "backend/Helper.java": "public class Helper { }\n",
            "frontend/src/api.ts": "export const load = () => fetch('/api/users');\n",
            "frontend/src/App.tsx": "export const App = () => null;\n",
        },
    )
    layers = {layer["layer"]: layer for layer in report["layers"]}
    assert layers["backend"]["languages"] == {"java": 1, "python": 1}
    assert layers["frontend"]["languages"] == {"typescript": 2}
    assert report["api_mapping"]["matched"] == 1
    assert sum(layer["files"] for layer in report["layers"]) == report["summary"]["total_files"] == 4


def test_parse_error_files_are_reported_as_uncertain(tmp_path):
    report = _report(tmp_path, {"backend/ok.py": FASTAPI_BACKEND, "backend/bad.py": "def (:\n"})
    coverage = [u for u in report["uncertainties"] if u["kind"] == "coverage"]
    assert report["coverage"]["files_with_parse_errors"] == 1
    assert any(e["file"] == "backend/bad.py" for u in coverage for e in u["evidence"])


def test_report_is_json_serializable_and_deterministic(tmp_path):
    import json

    files = {"backend/main.py": FASTAPI_BACKEND, "frontend/src/api.js": JS_FRONTEND}
    first = _report(tmp_path, files)
    second = _report(tmp_path, files)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    headline = summarize_report(first)
    assert headline["matched"] == 1 and headline["total_calls"] == 2
