"""
Phase 8B — API endpoint detection (static analysis only).

Every test goes through the real scanner and analysis pipeline
(`analyze_project(..., retain_parsed=True)`), so the detector is exercised on
exactly the parse trees the metrics use. Covers FastAPI, Flask, Spring and
Express (JavaScript and TypeScript), false-positive control, confidence,
line numbers, malformed input, Phase 8A architecture integration, and the
additive engine hook.
"""

import random
import shutil
from pathlib import Path

import pytest

from app.analyzer import api_endpoints as api
from app.analyzer.api_endpoints import (
    ApiEndpoint,
    detect_api_endpoints,
    detect_endpoints_in_module,
    join_paths,
)
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
    return detect_api_endpoints(analysis.parsed_modules, arch)


def pairs(summary):
    return [(e.method, e.path) for e in summary.endpoints]


def one(tmp_path: Path, name: str, code: str):
    return run(tmp_path, {name: code})


# =============================================================== path joining


@pytest.mark.parametrize(
    "parts,expected",
    [
        (("/api", "/users"), "/api/users"),
        (("api", "users"), "/api/users"),
        (("/api/", "/users/"), "/api/users"),
        (("/api", ""), "/api"),
        (("", ""), "/"),
        (("/", "/"), "/"),
        (("", "/{id}"), "/{id}"),
        ((None, "/x"), "/x"),
        (("/a//b", "c"), "/a/b/c"),
    ],
)
def test_join_paths(parts, expected):
    assert join_paths(*parts) == expected


# ================================================================== FastAPI


def test_fastapi_every_verb_with_lines_and_handlers(tmp_path):
    code = (
        "from fastapi import FastAPI\n"          # 1
        "app = FastAPI()\n"                      # 2
        "\n"                                     # 3
        '@app.get("/users")\n'                   # 4
        "async def list_users(): ...\n"          # 5
        "\n"                                     # 6
        '@app.post("/users")\n'                  # 7
        "def create_user(): ...\n"               # 8
        '@app.put("/users/{id}")\n'              # 9
        "def replace(id): ...\n"                 # 10
        '@app.patch("/users/{id}")\n'            # 11
        "def update(id): ...\n"                  # 12
        '@app.delete("/users/{id}")\n'           # 13
        "def remove(id): ...\n"                  # 14
    )
    summary = one(tmp_path, "main.py", code)
    assert pairs(summary) == [
        ("GET", "/users"), ("POST", "/users"), ("PUT", "/users/{id}"), ("PATCH", "/users/{id}"), ("DELETE", "/users/{id}"),
    ]
    assert [e.line_number for e in summary.endpoints] == [4, 7, 9, 11, 13]
    assert [e.handler for e in summary.endpoints] == ["list_users", "create_user", "replace", "update", "remove"]
    assert {(e.framework, e.confidence, e.language) for e in summary.endpoints} == {("FastAPI", HIGH, "python")}
    assert {e.source_file for e in summary.endpoints} == {"main.py"}


def test_fastapi_router_with_literal_prefix(tmp_path):
    code = (
        "from fastapi import APIRouter\n"
        'router = APIRouter(prefix="/users", tags=["u"])\n'
        '@router.get("/{user_id}")\n'
        "def get_one(user_id): ...\n"
        '@router.post("")\n'
        "def create(): ...\n"
    )
    summary = one(tmp_path, "routes.py", code)
    assert pairs(summary) == [("GET", "/users/{user_id}"), ("POST", "/users")]
    assert all(e.framework == "FastAPI" and e.confidence == HIGH for e in summary.endpoints)


def test_fastapi_router_decorator_without_a_prefix(tmp_path):
    code = 'from fastapi import APIRouter\nrouter = APIRouter()\n@router.get("/health")\ndef h(): ...\n'
    assert pairs(one(tmp_path, "r.py", code)) == [("GET", "/health")]


def test_fastapi_non_literal_prefix_keeps_the_endpoint_at_medium_confidence(tmp_path):
    code = (
        "from fastapi import APIRouter\n"
        "from settings import PREFIX\n"
        "router = APIRouter(prefix=PREFIX)\n"
        '@router.get("/x")\n'
        "def x(): ...\n"
    )
    summary = one(tmp_path, "r.py", code)
    assert pairs(summary) == [("GET", "/x")]  # prefix unresolved, path as declared
    assert summary.endpoints[0].confidence == MEDIUM


def test_fastapi_api_route_with_several_methods(tmp_path):
    code = (
        "from fastapi import FastAPI\napp = FastAPI()\n"
        '@app.api_route("/multi", methods=["GET", "POST"])\n'
        "def multi(): ...\n"
        '@app.api_route("/default")\n'
        "def default(): ...\n"
    )
    summary = one(tmp_path, "m.py", code)
    assert pairs(summary) == [("GET", "/multi"), ("POST", "/multi"), ("GET", "/default")]
    assert [e.line_number for e in summary.endpoints] == [3, 3, 5]  # one declaration, one line


def test_stacked_decorators_declare_separate_endpoints(tmp_path):
    code = (
        "from fastapi import FastAPI\napp = FastAPI()\n"
        '@app.get("/a")\n'
        '@app.get("/b")\n'
        "def both(): ...\n"
    )
    summary = one(tmp_path, "s.py", code)
    assert pairs(summary) == [("GET", "/a"), ("GET", "/b")]
    assert [e.line_number for e in summary.endpoints] == [3, 4]


def test_multiline_decorator_reports_its_first_line(tmp_path):
    code = (
        "from fastapi import FastAPI\napp = FastAPI()\n"
        "@app.get(\n"
        '    "/long",\n'
        "    response_model=None,\n"
        ")\n"
        "def long(): ...\n"
    )
    assert one(tmp_path, "l.py", code).endpoints[0].line_number == 3


# =================================================================== Flask


def test_flask_route_defaults_to_get(tmp_path):
    code = 'from flask import Flask\napp = Flask(__name__)\n@app.route("/users")\ndef users(): ...\n'
    summary = one(tmp_path, "app.py", code)
    assert pairs(summary) == [("GET", "/users")]
    assert (summary.endpoints[0].framework, summary.endpoints[0].confidence) == ("Flask", HIGH)


def test_flask_explicit_methods(tmp_path):
    code = (
        "from flask import Flask\napp = Flask(__name__)\n"
        '@app.route("/login", methods=["POST"])\n'
        "def login(): ...\n"
        '@app.route("/both", methods=["GET", "POST"])\n'
        "def both(): ...\n"
        '@app.route("/tuple", methods=("put", "delete"))\n'
        "def tup(): ...\n"
    )
    assert pairs(one(tmp_path, "app.py", code)) == [
        ("POST", "/login"), ("GET", "/both"), ("POST", "/both"), ("DELETE", "/tuple"), ("PUT", "/tuple"),  # same line: sorted by method
    ]


def test_flask_blueprint_with_url_prefix_and_flask2_shortcuts(tmp_path):
    code = (
        "from flask import Blueprint\n"
        'bp = Blueprint("api", __name__, url_prefix="/api")\n'
        '@bp.route("/items")\n'
        "def items(): ...\n"
        '@bp.post("/items")\n'
        "def add(): ...\n"
    )
    summary = one(tmp_path, "bp.py", code)
    assert pairs(summary) == [("GET", "/api/items"), ("POST", "/api/items")]
    assert {e.framework for e in summary.endpoints} == {"Flask"}


def test_flask_routes_inside_an_application_factory(tmp_path):
    code = (
        "from flask import Flask\n"
        "def create_app():\n"
        "    app = Flask(__name__)\n"
        '    @app.route("/ping")\n'
        "    def ping(): ...\n"
        "    return app\n"
    )
    summary = one(tmp_path, "factory.py", code)
    assert pairs(summary) == [("GET", "/ping")] and summary.endpoints[0].confidence == HIGH


# ================================================ Python framework evidence


def test_python_confidence_follows_framework_evidence(tmp_path):
    bare_get = run(tmp_path / "a", {"a.py": '@app.get("/users")\ndef f(): ...\n'})
    assert (bare_get.endpoints[0].framework, bare_get.endpoints[0].confidence) == ("FastAPI", MEDIUM)

    bare_route = run(tmp_path / "b", {"b.py": '@app.route("/users")\ndef f(): ...\n'})
    assert (bare_route.endpoints[0].framework, bare_route.endpoints[0].confidence) == ("Flask", MEDIUM)

    imported = run(tmp_path / "c", {"c.py": 'from fastapi import FastAPI\nfrom .main import app\n@app.get("/u")\ndef f(): ...\n'})
    assert (imported.endpoints[0].framework, imported.endpoints[0].confidence) == ("FastAPI", HIGH)

    flask_imported = run(tmp_path / "d", {"d.py": 'import flask\nfrom .main import app\n@app.route("/u")\ndef f(): ...\n'})
    assert (flask_imported.endpoints[0].framework, flask_imported.endpoints[0].confidence) == ("Flask", HIGH)

    both = run(tmp_path / "e", {"e.py": 'import flask, fastapi\nfrom .m import app\n@app.route("/u")\ndef f(): ...\n'})
    assert both.endpoints[0].confidence == MEDIUM  # two candidate frameworks: not certain


def test_files_of_other_python_frameworks_are_not_reported(tmp_path):
    code = 'from sanic import Sanic\napp = Sanic("x")\n@app.route("/users")\nasync def f(r): ...\n'
    assert one(tmp_path, "s.py", code).total_endpoints == 0


def test_fastapi_receiver_cannot_use_flask_route_and_vice_versa(tmp_path):
    code = (
        "from fastapi import FastAPI\napp = FastAPI()\n"
        '@app.route("/x")\ndef f(): ...\n'
    )
    assert one(tmp_path / "a", "a.py", code).total_endpoints == 0
    code = (
        "from flask import Flask\napp = Flask(__name__)\n"
        '@app.api_route("/x")\ndef f(): ...\n'
    )
    assert one(tmp_path / "b", "b.py", code).total_endpoints == 0


def test_mock_patch_and_look_alike_decorators_are_not_routes(tmp_path):
    # Real false positive found by scanning ~10,000 installed Python files:
    # `@mock.patch("os.path.join")` has the same shape as `@app.patch("/x")`.
    code = (
        "import os\nfrom unittest import mock\nimport pytest\n"
        '@mock.patch("os.path.join")\n'
        '@mock.patch.object(os, "getcwd")\n'
        '@mock.patch("/tmp/x")\n'
        '@pytest.mark.get("/x")\n'
        '@client.get("/users")\n'
        "def test_something(a, b, c): ...\n"
    )
    assert one(tmp_path, "test_x.py", code).total_endpoints == 0


def test_python_route_paths_must_start_with_a_slash(tmp_path):
    code = 'from fastapi import FastAPI\napp = FastAPI()\n@app.get("users")\ndef f(): ...\n'
    assert one(tmp_path, "a.py", code).total_endpoints == 0


def test_python_decorators_that_are_not_routes_are_ignored(tmp_path):
    code = (
        "from fastapi import FastAPI\nfrom flask import Flask\n"
        "app = FastAPI()\nflask_app = Flask(__name__)\n"
        "@app.get()\ndef a(): ...\n"
        '@app.on_event("startup")\ndef b(): ...\n'
        '@app.middleware("http")\ndef c(): ...\n'
        "@flask_app.before_request\ndef d(): ...\n"
        "@flask_app.errorhandler(404)\ndef e(): ...\n"
        "@staticmethod\ndef g(): ...\n"
    )
    assert one(tmp_path, "a.py", code).total_endpoints == 0


def test_python_comments_docstrings_and_strings_are_ignored(tmp_path):
    code = (
        "from fastapi import FastAPI\napp = FastAPI()\n"
        '# @app.get("/commented-out")\n'
        '"""\n@app.get("/in-a-docstring")\n"""\n'
        'EXAMPLE = \'@app.post("/in-a-string")\'\n'
        '@app.get("/real")\ndef real(): ...\n'
    )
    assert pairs(one(tmp_path, "a.py", code)) == [("GET", "/real")]


def test_python_dynamic_paths_are_skipped_and_counted(tmp_path):
    code = (
        "from fastapi import FastAPI\nfrom flask import Flask\n"
        "app = FastAPI()\nflask_app = Flask(__name__)\n"
        "PATH = '/x'\nMETHODS = ['GET']\n"
        "@app.get(PATH)\ndef a(): ...\n"
        '@app.get(f"/v{1}/users")\ndef b(): ...\n'
        '@app.get("/a" + "/b")\ndef c(): ...\n'
        "@flask_app.route('/m', methods=METHODS)\ndef d(): ...\n"
        '@app.get("/static")\ndef e(): ...\n'
    )
    summary = one(tmp_path, "d.py", code)
    assert pairs(summary) == [("GET", "/static")]
    assert summary.dynamic_paths_skipped == 4


# ===================================================================== Spring


def test_spring_verb_mappings(tmp_path):
    code = (
        "public class C {\n"                                  # 1
        '    @GetMapping("/users")\n'                         # 2
        "    public String list() { return \"\"; }\n"         # 3
        '    @PostMapping("/users")\n'                        # 4
        "    public String add() { return \"\"; }\n"          # 5
        '    @PutMapping("/users/{id}")\n'                    # 6
        "    public String put() { return \"\"; }\n"          # 7
        '    @PatchMapping("/users/{id}")\n'                  # 8
        "    public String patch() { return \"\"; }\n"        # 9
        '    @DeleteMapping("/users/{id}")\n'                 # 10
        "    public String del() { return \"\"; }\n"          # 11
        "}\n"
    )
    summary = one(tmp_path, "C.java", code)
    assert pairs(summary) == [
        ("GET", "/users"), ("POST", "/users"), ("PUT", "/users/{id}"), ("PATCH", "/users/{id}"), ("DELETE", "/users/{id}"),
    ]
    assert [e.line_number for e in summary.endpoints] == [2, 4, 6, 8, 10]
    assert [e.handler for e in summary.endpoints] == ["list", "add", "put", "patch", "del"]
    assert {(e.framework, e.confidence, e.language) for e in summary.endpoints} == {("Spring", HIGH, "java")}


def test_spring_class_level_prefix_is_joined_to_method_paths(tmp_path):
    code = (
        '@RequestMapping("/api")\n'
        "class UserController {\n"
        '    @GetMapping("/users")\n'
        "    public String list() { return \"\"; }\n"
        '    @PostMapping("login")\n'
        "    public String login() { return \"\"; }\n"
        "}\n"
    )
    assert pairs(one(tmp_path, "U.java", code)) == [("GET", "/api/users"), ("POST", "/api/login")]


def test_spring_request_mapping_with_request_method(tmp_path):
    code = (
        "class C {\n"
        '    @RequestMapping(value = "/login", method = RequestMethod.POST)\n'
        "    public String a() { return \"\"; }\n"
        '    @RequestMapping(path = "/both", method = {RequestMethod.GET, RequestMethod.PUT})\n'
        "    public String b() { return \"\"; }\n"
        "}\n"
    )
    summary = one(tmp_path, "C.java", code)
    assert pairs(summary) == [("POST", "/login"), ("GET", "/both"), ("PUT", "/both")]
    assert {e.confidence for e in summary.endpoints} == {HIGH}


def test_spring_request_mapping_without_a_method_is_any_at_medium_confidence(tmp_path):
    code = 'class C {\n    @RequestMapping("/any")\n    public String a() { return ""; }\n}\n'
    endpoint = one(tmp_path, "C.java", code).endpoints[0]
    assert (endpoint.method, endpoint.path, endpoint.confidence) == ("ANY", "/any", MEDIUM)


def test_spring_mapping_attribute_forms(tmp_path):
    code = (
        '@RequestMapping({"/v1", "/v2"})\n'
        "class C {\n"
        "    @GetMapping\n"
        "    public String root() { return \"\"; }\n"
        '    @GetMapping(value = "/a", produces = "text/plain")\n'
        "    public String a() { return \"\"; }\n"
        '    @GetMapping({"/b", "/c"})\n'
        "    public String bc() { return \"\"; }\n"
        "}\n"
    )
    assert pairs(one(tmp_path, "C.java", code)) == [
        ("GET", "/v1"), ("GET", "/v2"),
        ("GET", "/v1/a"), ("GET", "/v2/a"),
        ("GET", "/v1/b"), ("GET", "/v1/c"), ("GET", "/v2/b"), ("GET", "/v2/c"),
    ]


def test_spring_non_literal_paths_are_skipped_or_downgraded(tmp_path):
    code = (
        "@RequestMapping(Constants.BASE)\n"
        "class C {\n"
        '    @GetMapping("/ok")\n'
        "    public String ok() { return \"\"; }\n"
        "    @GetMapping(Constants.PATH)\n"
        "    public String dyn() { return \"\"; }\n"
        "}\n"
    )
    summary = one(tmp_path, "C.java", code)
    assert pairs(summary) == [("GET", "/ok")]  # base unresolved -> path as declared
    assert summary.endpoints[0].confidence == MEDIUM
    assert summary.dynamic_paths_skipped == 2


def test_spring_feign_clients_declare_outgoing_calls_not_endpoints(tmp_path):
    code = (
        '@FeignClient(name = "users")\n'
        "interface UserClient {\n"
        '    @GetMapping("/users")\n'
        "    String list();\n"
        "}\n"
    )
    assert one(tmp_path, "UserClient.java", code).total_endpoints == 0


def test_spring_interface_mappings_are_medium_confidence(tmp_path):
    code = 'interface Api {\n    @GetMapping("/users")\n    String list();\n}\n'
    endpoint = one(tmp_path, "Api.java", code).endpoints[0]
    assert (endpoint.method, endpoint.path, endpoint.confidence) == ("GET", "/users", MEDIUM)


def test_spring_nested_classes_use_their_own_prefix(tmp_path):
    code = (
        "class Outer {\n"
        '    @RequestMapping("/inner")\n'
        "    static class Inner {\n"
        '        @GetMapping("/x")\n'
        "        public String x() { return \"\"; }\n"
        "    }\n"
        '    @GetMapping("/top")\n'
        "    public String top() { return \"\"; }\n"
        "}\n"
    )
    assert sorted(pairs(one(tmp_path, "Outer.java", code))) == [("GET", "/inner/x"), ("GET", "/top")]


def test_spring_comments_strings_and_other_annotations_are_ignored(tmp_path):
    code = (
        "class C {\n"
        '    // @GetMapping("/commented")\n'
        '    /* @PostMapping("/block") */\n'
        '    String s = "@GetMapping(\\"/string\\")";\n'
        "    @Override\n"
        "    @Deprecated\n"
        '    @SuppressWarnings("unchecked")\n'
        "    public String plain() { return s; }\n"
        '    @GetMapping("/real")\n'
        "    public String real() { return s; }\n"
        "}\n"
    )
    assert pairs(one(tmp_path, "C.java", code)) == [("GET", "/real")]


# ================================================================== Express


EXPRESS_HEAD = 'const express = require("express");\nconst app = express();\n'  # 2 lines


def test_express_app_verbs_with_lines(tmp_path):
    code = EXPRESS_HEAD + (
        'app.get("/users", (req, res) => res.send("x"));\n'      # 3
        'app.post("/login", handler);\n'                         # 4
        'app.put("/users/:id", handler);\n'                      # 5
        'app.patch("/users/:id", handler);\n'                    # 6
        'app.delete("/users/:id", auth, remove);\n'              # 7
    )
    summary = one(tmp_path, "server.js", code)
    assert pairs(summary) == [
        ("GET", "/users"), ("POST", "/login"), ("PUT", "/users/:id"), ("PATCH", "/users/:id"), ("DELETE", "/users/:id"),
    ]
    assert [e.line_number for e in summary.endpoints] == [3, 4, 5, 6, 7]
    assert {(e.framework, e.confidence, e.language) for e in summary.endpoints} == {("Express", HIGH, "javascript")}


def test_express_router_receivers(tmp_path):
    code = (
        'const express = require("express");\n'
        "const router = express.Router();\n"
        'const other = express.Router();\n'
        'router.get("/a", h);\n'
        'router.delete("/b/:id", h);\n'
        'other.post("/c", h);\n'
    )
    summary = one(tmp_path, "routes.js", code)
    assert pairs(summary) == [("GET", "/a"), ("DELETE", "/b/:id"), ("POST", "/c")]
    assert {e.confidence for e in summary.endpoints} == {HIGH}


def test_express_router_imported_by_name_and_require_call_form(tmp_path):
    code = (
        'import { Router } from "express";\n'
        "const r = Router();\n"
        'r.get("/x", h);\n'
        'const app = require("express")();\n'
        'app.get("/y", h);\n'
    )
    summary = one(tmp_path, "m.js", code)
    assert pairs(summary) == [("GET", "/x"), ("GET", "/y")]
    assert {e.confidence for e in summary.endpoints} == {HIGH}


def test_express_in_typescript_with_typed_receivers_and_class_members(tmp_path):
    code = (
        'import express, { Router, Express } from "express";\n'                  # 1
        "const app: Express = express();\n"                                      # 2
        "class UserController {\n"                                               # 3
        "  router = Router();\n"                                                 # 4
        "  init(): void {\n"                                                     # 5
        '    this.router.get("/users", this.list);\n'                            # 6
        '    this.router.post("/users", this.create);\n'                         # 7
        "  }\n"
        "}\n"
        'app.put("/ts-put", (req: express.Request, res: express.Response) => {});\n'  # 10
    )
    summary = one(tmp_path, "api.ts", code)
    assert pairs(summary) == [("GET", "/users"), ("POST", "/users"), ("PUT", "/ts-put")]
    assert [e.line_number for e in summary.endpoints] == [6, 7, 10]
    assert {(e.language, e.framework, e.confidence) for e in summary.endpoints} == {("typescript", "Express", HIGH)}


def test_express_confidence_follows_evidence(tmp_path):
    assert one(tmp_path / "a", "a.js", 'app.get("/u", h);\n').endpoints[0].confidence == LOW  # name only
    imported = 'const express = require("express");\nfunction register(app) {\n  app.get("/u", h);\n}\n'
    assert one(tmp_path / "b", "b.js", imported).endpoints[0].confidence == MEDIUM  # param receiver
    router_no_import = "const router = Router();\nrouter.get('/u', h);\n"
    assert one(tmp_path / "c", "c.js", router_no_import).endpoints[0].confidence == MEDIUM


def test_express_look_alike_calls_are_not_endpoints(tmp_path):
    code = EXPRESS_HEAD + (
        'app.get("title");\n'                       # settings getter: no handler
        'app.get("env")\n'
        'axios.get("/users", { headers: {} });\n'   # HTTP client
        'api.get("/users", cfg);\n'                 # unknown receiver
        'request(app).get("/users");\n'              # supertest
        'req.app.get("/users", h);\n'                # member chain
        '$.get("/url", cb);\n'
        'app.use("/api", router);\n'                 # mounting is not an endpoint
        'app.listen(3000, () => {});\n'
        'app.get(handler);\n'
    )
    assert one(tmp_path, "s.js", code).total_endpoints == 0


def test_express_comments_strings_and_test_descriptions_are_ignored(tmp_path):
    code = EXPRESS_HEAD + (
        '// app.get("/commented", h);\n'
        '/* app.post("/block", h); */\n'
        'const doc = "app.get(\'/in-a-string\', h)";\n'
        'const tpl = `app.get("/in-a-template", h)`;\n'
        'describe("GET /users", () => { it("app.get(\'/x\', h)", () => {}); });\n'
        'app.get("/real", h);\n'
    )
    assert pairs(one(tmp_path, "c.js", code)) == [("GET", "/real")]


def test_express_path_forms(tmp_path):
    code = EXPRESS_HEAD + (
        "app.get(`/static-template`, h);\n"
        'app.get(["/one", "/two"], h);\n'
        "app.get(`/dyn/${id}`, h);\n"
        "app.get(PATH, h);\n"
        'app.get("/a" + id, h);\n'
        "app.get(/^\\/regex/, h);\n"
        'app.get("/after", h);\n'
    )
    summary = one(tmp_path, "p.js", code)
    assert pairs(summary) == [("GET", "/static-template"), ("GET", "/one"), ("GET", "/two"), ("GET", "/after")]
    assert summary.dynamic_paths_skipped == 4


def test_express_multiline_call_reports_the_verb_line(tmp_path):
    code = EXPRESS_HEAD + "app\n  .get(\n    '/long',\n    handler\n  );\n"
    assert one(tmp_path, "m.js", code).endpoints[0].line_number == 4


def test_files_of_other_node_frameworks_are_not_reported(tmp_path):
    koa = "const Router = require('@koa/router');\nconst router = new Router();\nrouter.get('/users', h);\n"
    fastify = "const fastify = require('fastify')();\nfastify.get('/users', h);\nconst app = fastify;\napp.get('/x', h);\n"
    assert one(tmp_path / "a", "koa.js", koa).total_endpoints == 0
    assert one(tmp_path / "b", "f.js", fastify).total_endpoints == 0


def test_jsx_and_tsx_files_are_searched_without_confusion(tmp_path):
    code = EXPRESS_HEAD + 'const View = () => <p>app.get("/in-jsx", h)</p>;\napp.get("/real", h);\n'
    assert pairs(one(tmp_path, "s.jsx", code)) == [("GET", "/real")]


# =================================================== mixed-language projects


MIXED = {
    "backend/api/users.py": 'from fastapi import FastAPI\napp = FastAPI()\n@app.get("/users")\ndef a(): ...\n@app.post("/login")\ndef b(): ...\n',
    "backend/src/UserController.java": (
        '@RequestMapping("/api")\nclass UserController {\n'
        '    @GetMapping("/orders")\n    public String o() { return ""; }\n'
        '    @DeleteMapping("/orders/{id}")\n    public String d() { return ""; }\n}\n'
    ),
    "server/index.js": EXPRESS_HEAD + 'app.get("/health", h);\n',
    "server/routes.ts": 'import express from "express";\nconst router = express.Router();\nrouter.put("/items", h);\n',
}


def test_mixed_language_project_summary(tmp_path):
    summary = run(tmp_path, MIXED)
    assert summary.total_endpoints == 6
    assert summary.to_dict() == {
        "total_endpoints": 6,
        "by_method": {"GET": 3, "DELETE": 1, "POST": 1, "PUT": 1},
        "by_framework": {"Express": 2, "FastAPI": 2, "Spring": 2},
        "by_confidence": {"high": 6},
        "files_scanned": 4,
        "files_excluded_by_layer": 0,
        "dynamic_paths_skipped": 0,
    }
    assert {(e.language, e.framework) for e in summary.endpoints} == {
        ("python", "FastAPI"), ("java", "Spring"), ("javascript", "Express"), ("typescript", "Express"),
    }
    assert ("DELETE", "/api/orders/{id}") in pairs(summary)


def test_endpoint_list_is_sorted_and_independent_of_input_order(tmp_path):
    analysis_dir = tmp_path
    for name, content in MIXED.items():
        path = analysis_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    analysis = analyze_project(tmp_path, retain_parsed=True)
    baseline = detect_api_endpoints(analysis.parsed_modules).endpoints
    shuffled = analysis.parsed_modules[:]
    random.Random(3).shuffle(shuffled)
    assert detect_api_endpoints(shuffled).endpoints == baseline
    assert baseline == sorted(baseline, key=lambda e: (e.source_file, e.line_number, e.method, e.path))


# ============================================== Phase 8A architecture integration


LAYERED = {
    "backend/app.js": EXPRESS_HEAD + 'app.get("/api/users", h);\n',
    "frontend/mock-server.js": EXPRESS_HEAD + 'app.get("/mock", h);\n',
    "tests/test_app.py": 'from fastapi import FastAPI\napp = FastAPI()\n@app.get("/under-test")\ndef f(): ...\n',
    "src/standalone.js": EXPRESS_HEAD + 'app.get("/standalone", h);\n',
}


def test_architecture_excludes_frontend_and_test_layers_but_keeps_backend_and_unknown(tmp_path):
    summary = run(tmp_path, LAYERED, architecture=True)
    assert pairs(summary) == [("GET", "/api/users"), ("GET", "/standalone")]
    assert summary.files_excluded_by_layer == 2  # frontend mock server + test file
    assert summary.files_scanned == 2
    layers = {e.source_file: e.layer for e in summary.endpoints}
    assert layers == {"backend/app.js": "backend", "src/standalone.js": "unknown"}  # unknown stays unknown


def test_without_architecture_every_supported_file_is_searched(tmp_path):
    summary = run(tmp_path, LAYERED, architecture=False)
    assert sorted(pairs(summary)) == [("GET", "/api/users"), ("GET", "/mock"), ("GET", "/standalone"), ("GET", "/under-test")]
    assert summary.files_excluded_by_layer == 0
    assert {e.layer for e in summary.endpoints} == {None}


def test_layer_exclusion_only_counts_supported_languages(tmp_path):
    files = {"frontend/native.cpp": "int app_get(){ return 0; }\n", "frontend/a.ts": "const a = 1;\n"}
    assert run(tmp_path, files, architecture=True).files_excluded_by_layer == 1  # a.ts, not the C++ file


# ===================================================== robustness / safety


def test_malformed_sources_do_not_crash_and_are_not_counted(tmp_path):
    files = {
        "bad.py": "def broken(:\n    @app.get('/x')\n",
        "Bad.java": 'class Bad {\n    @GetMapping("/x")\n    public String f( {\n',
        "bad.js": 'app.get("/x", function () {\n',
        "ok.py": 'from fastapi import FastAPI\napp = FastAPI()\n@app.get("/ok")\ndef f(): ...\n',
    }
    summary = run(tmp_path, files)
    assert pairs(summary) == [("GET", "/ok")]
    assert summary.files_scanned == 1  # files that failed to parse are not searched


def test_unsupported_languages_are_ignored(tmp_path):
    files = {"a.cpp": 'void f() { app.get("/x", h); }\n', "b.c": "int main(void) { return 0; }\n"}
    summary = run(tmp_path, files)
    assert summary.total_endpoints == 0 and summary.files_scanned == 0


def test_empty_project_and_empty_input(tmp_path):
    assert detect_api_endpoints([]).to_dict()["total_endpoints"] == 0
    assert run(tmp_path, {"readme.txt": "nothing"}).total_endpoints == 0


def test_a_failing_detector_never_takes_the_analysis_down(tmp_path, monkeypatch):
    (tmp_path / "a.py").write_text("x = 1\n")
    analysis = analyze_project(tmp_path, retain_parsed=True)

    def boom(module):
        raise RuntimeError("detector bug")

    monkeypatch.setitem(api._DETECTORS, "python", boom)
    assert detect_endpoints_in_module(analysis.parsed_modules[0]) == ([], 0)
    assert detect_api_endpoints(analysis.parsed_modules).total_endpoints == 0


# ======================================================= model and summary


def test_endpoint_model_and_summary_serialization(tmp_path):
    summary = run(tmp_path, MIXED)
    first = summary.endpoints[0]
    assert isinstance(first, ApiEndpoint)
    assert set(first.to_dict()) == {
        "method", "path", "source_file", "line_number", "language", "framework", "confidence", "handler", "layer",
    }
    compact = summary.to_dict()
    assert "endpoints" not in compact  # compact by default, like the architecture summary
    detailed = summary.to_dict(include_endpoints=True)
    assert len(detailed["endpoints"]) == detailed["total_endpoints"] == 6
    with pytest.raises(Exception):
        first.path = "/changed"  # endpoints are immutable


def test_summary_counts_are_ordered_by_frequency_then_name(tmp_path):
    summary = run(tmp_path, MIXED)
    assert list(summary.by_method) == ["GET", "DELETE", "POST", "PUT"]
    assert list(summary.by_framework) == ["Express", "FastAPI", "Spring"]


# =============================== real code: ground truth and false positives


def test_detection_matches_this_projects_own_fastapi_route_table(tmp_path):
    """The strongest check available offline: compare static detection with
    the routes FastAPI actually registered for this very backend."""
    from fastapi.routing import APIRoute

    from app.main import app

    expected = {(m, r.path) for r in app.routes if isinstance(r, APIRoute) for m in r.methods - {"HEAD", "OPTIONS"}}
    source = Path(__file__).resolve().parents[1] / "app"
    shutil.copytree(source, tmp_path / "app", ignore=shutil.ignore_patterns("__pycache__"))
    analysis = analyze_project(tmp_path, retain_parsed=True)
    summary = detect_api_endpoints(analysis.parsed_modules)
    assert expected and {(e.method, e.path) for e in summary.endpoints} == expected
    assert summary.by_confidence == {"high": len(expected)}


def test_the_bundled_demo_projects_declare_no_endpoints():
    root = Path(__file__).resolve().parents[2] / "sample_projects"
    for demo in sorted(p for p in root.iterdir() if p.is_dir() and p.name.startswith("demo_")):
        analysis = analyze_project(demo, retain_parsed=True)
        summary = detect_api_endpoints(analysis.parsed_modules)
        assert summary.total_endpoints == 0, demo.name
        assert summary.files_scanned == sum(1 for m in analysis.parsed_modules if m.language in api._SUPPORTED_LANGUAGES)


# ======================= the additive engine hook (existing behaviour intact)


def test_parsed_modules_are_not_retained_by_default(tmp_path):
    (tmp_path / "a.py").write_text("def f():\n    return 1\n")
    assert analyze_project(tmp_path).parsed_modules == []


def test_retaining_parsed_modules_exposes_source_and_changes_no_output(tmp_path):
    (tmp_path / "a.py").write_text("def f():\n    return 1\n")
    (tmp_path / "B.java").write_text("public class B {}\n")
    plain = analyze_project(tmp_path)
    retained = analyze_project(tmp_path, retain_parsed=True)
    assert retained.to_dict() == plain.to_dict()  # identical serialized analysis
    assert retained == plain  # parsed_modules is excluded from equality
    assert {(m.source_file.relative_path, m.language) for m in retained.parsed_modules} == {("a.py", "python"), ("B.java", "java")}
    assert all(m.source_text for m in retained.parsed_modules)
    assert "parsed_modules" not in repr(retained)
    assert set(retained.to_dict()) == {"project", "summary", "modules"}
