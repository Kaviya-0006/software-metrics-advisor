"""
Phase 8C, part 2 — same-file mount-prefix resolution for the endpoint detector.

A router/blueprint's prefix is resolved when it is mounted by a literal in the
same file (Express `app.use`, FastAPI `include_router`, Flask
`register_blueprint`). Anything not provable is *not guessed*: the endpoint
keeps its path without that prefix and is reported at MEDIUM confidence.
"""

from pathlib import Path

from app.analyzer.api_endpoints import detect_api_endpoints
from app.analyzer.engine import analyze_project

HIGH, MEDIUM = "high", "medium"


def endpoints(tmp_path: Path, name: str, code: str):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(code)
    analysis = analyze_project(tmp_path, retain_parsed=True)
    return detect_api_endpoints(analysis.parsed_modules).endpoints


def pairs(found):
    return [(e.method, e.path) for e in found]


EXPRESS = 'const express = require("express");\nconst app = express();\n'


# ================================================================== Express


def test_express_literal_mount_prefix_is_applied(tmp_path):
    code = EXPRESS + (
        "const router = express.Router();\n"
        'router.get("/users", handler);\n'
        'router.post("/users", handler);\n'
        'app.use("/api", router);\n'
    )
    found = endpoints(tmp_path, "app.js", code)
    assert pairs(found) == [("GET", "/api/users"), ("POST", "/api/users")]
    assert {e.confidence for e in found} == {HIGH}  # a proven mount keeps the confidence


def test_express_mount_may_appear_before_or_after_the_routes(tmp_path):
    code = EXPRESS + (
        "const router = express.Router();\n"
        'app.use("/api", router);\n'
        'router.get("/late", handler);\n'
    )
    assert pairs(endpoints(tmp_path, "app.js", code)) == [("GET", "/api/late")]


def test_express_unmounted_router_keeps_its_declared_path(tmp_path):
    code = EXPRESS + "const router = express.Router();\nrouter.get('/users', h);\napp.get('/health', h);\n"
    assert pairs(endpoints(tmp_path, "app.js", code)) == [("GET", "/users"), ("GET", "/health")]


def test_express_root_mount_adds_no_prefix(tmp_path):
    code = EXPRESS + "const router = express.Router();\nrouter.get('/users', h);\napp.use(router);\n"
    assert pairs(endpoints(tmp_path, "app.js", code)) == [("GET", "/users")]


def test_express_array_of_literal_prefixes_yields_one_endpoint_per_prefix(tmp_path):
    code = EXPRESS + (
        "const router = express.Router();\nrouter.get('/users', h);\n"
        "app.use(['/v1', '/v2'], router);\n"
    )
    assert pairs(endpoints(tmp_path, "app.js", code)) == [("GET", "/v1/users"), ("GET", "/v2/users")]


def test_express_chained_mounts_compose_prefixes(tmp_path):
    code = EXPRESS + (
        "const v1 = express.Router();\nconst users = express.Router();\n"
        "users.get('/', h);\nusers.post('/:id', h);\n"
        "v1.use('/users', users);\napp.use('/api/v1', v1);\n"
    )
    assert pairs(endpoints(tmp_path, "app.js", code)) == [("GET", "/api/v1/users"), ("POST", "/api/v1/users/:id")]


def test_express_router_mounted_twice_has_both_paths(tmp_path):
    code = EXPRESS + (
        "const router = express.Router();\nrouter.get('/x', h);\n"
        "app.use('/a', router);\napp.use('/b', router);\n"
    )
    assert pairs(endpoints(tmp_path, "app.js", code)) == [("GET", "/a/x"), ("GET", "/b/x")]


def test_express_middleware_before_the_router_does_not_hide_the_mount(tmp_path):
    code = EXPRESS + (
        "const router = express.Router();\nrouter.get('/x', h);\n"
        "app.use('/api', authMiddleware, router);\n"
    )
    assert pairs(endpoints(tmp_path, "app.js", code)) == [("GET", "/api/x")]


def test_express_static_template_prefix_is_a_literal(tmp_path):
    code = EXPRESS + "const router = express.Router();\nrouter.get('/x', h);\napp.use(`/api`, router);\n"
    assert pairs(endpoints(tmp_path, "app.js", code)) == [("GET", "/api/x")]


def test_express_non_literal_prefix_is_not_guessed(tmp_path):
    code = EXPRESS + (
        "const router = express.Router();\nrouter.get('/x', h);\n"
        "app.use(PREFIX, router);\n"
    )
    found = endpoints(tmp_path, "app.js", code)
    assert pairs(found) == [("GET", "/x")]  # path as declared, prefix unresolved
    assert found[0].confidence == MEDIUM


def test_express_template_with_an_expression_prefix_is_not_guessed(tmp_path):
    code = EXPRESS + "const router = express.Router();\nrouter.get('/x', h);\napp.use(`/v${version}`, router);\n"
    found = endpoints(tmp_path, "app.js", code)
    assert pairs(found) == [("GET", "/x")] and found[0].confidence == MEDIUM


def test_express_literal_and_unresolved_mounts_both_report(tmp_path):
    code = EXPRESS + (
        "const router = express.Router();\nrouter.get('/x', h);\n"
        "app.use('/api', router);\napp.use(OTHER, router);\n"
    )
    found = endpoints(tmp_path, "app.js", code)
    assert sorted((e.path, e.confidence) for e in found) == [("/api/x", HIGH), ("/x", MEDIUM)]


def test_express_router_from_another_file_is_not_followed(tmp_path):
    code = EXPRESS + "app.use('/api', require('./routes'));\napp.get('/health', h);\n"
    assert pairs(endpoints(tmp_path, "app.js", code)) == [("GET", "/health")]


def test_express_non_router_arguments_and_unknown_hosts_are_ignored(tmp_path):
    code = EXPRESS + (
        "const router = express.Router();\nrouter.get('/x', h);\n"
        "app.use(express.static('public'));\n"
        "app.use('/api', cors());\n"
        "server.use('/api', router);\n"  # `server` is not an Express receiver
    )
    assert pairs(endpoints(tmp_path, "app.js", code)) == [("GET", "/x")]


def test_express_mount_cycles_terminate(tmp_path):
    code = EXPRESS + (
        "const a = express.Router();\nconst b = express.Router();\n"
        "a.get('/x', h);\na.use('/y', b);\nb.use('/z', a);\n"
    )
    assert endpoints(tmp_path, "app.js", code)  # no infinite recursion


def test_express_mount_prefix_in_typescript(tmp_path):
    code = (
        'import express, { Router } from "express";\n'
        "const app = express();\nconst router: Router = Router();\n"
        'router.delete("/items/:id", h);\n'
        'app.use("/api", router);\n'
    )
    found = endpoints(tmp_path, "app.ts", code)
    assert pairs(found) == [("DELETE", "/api/items/:id")] and found[0].language == "typescript"


# ================================================================== FastAPI


FASTAPI = "from fastapi import FastAPI, APIRouter\napp = FastAPI()\n"


def test_fastapi_include_router_prefix_is_applied(tmp_path):
    code = FASTAPI + (
        "router = APIRouter()\n"
        '@router.get("/users")\ndef a(): ...\n'
        'app.include_router(router, prefix="/api")\n'
    )
    found = endpoints(tmp_path, "main.py", code)
    assert pairs(found) == [("GET", "/api/users")] and found[0].confidence == HIGH


def test_fastapi_mount_prefix_is_joined_before_the_routers_own_prefix(tmp_path):
    code = FASTAPI + (
        'users = APIRouter(prefix="/users")\n'
        '@users.get("/{id}")\ndef a(): ...\n'
        'app.include_router(users, prefix="/api")\n'
    )
    assert pairs(endpoints(tmp_path, "main.py", code)) == [("GET", "/api/users/{id}")]


def test_fastapi_nested_include_router_chain(tmp_path):
    code = FASTAPI + (
        'api = APIRouter(prefix="/v1")\nusers = APIRouter(prefix="/users")\n'
        '@users.get("/{id}")\ndef a(): ...\n'
        "api.include_router(users)\n"
        'app.include_router(api, prefix="/api")\n'
    )
    assert pairs(endpoints(tmp_path, "main.py", code)) == [("GET", "/api/v1/users/{id}")]


def test_fastapi_include_router_without_prefix_keeps_the_own_prefix(tmp_path):
    code = FASTAPI + 'r = APIRouter(prefix="/own")\n@r.get("/x")\ndef a(): ...\napp.include_router(r)\n'
    assert pairs(endpoints(tmp_path, "main.py", code)) == [("GET", "/own/x")]


def test_fastapi_non_literal_include_prefix_is_not_guessed(tmp_path):
    code = FASTAPI + (
        "r = APIRouter()\n"
        '@r.get("/u")\ndef a(): ...\n'
        "app.include_router(r, prefix=settings.PREFIX)\n"
    )
    found = endpoints(tmp_path, "main.py", code)
    assert pairs(found) == [("GET", "/u")] and found[0].confidence == MEDIUM


def test_fastapi_router_included_twice_has_both_paths(tmp_path):
    code = FASTAPI + (
        "r = APIRouter()\n"
        '@r.get("/x")\ndef a(): ...\n'
        'app.include_router(r, prefix="/a")\napp.include_router(r, prefix="/b")\n'
    )
    assert pairs(endpoints(tmp_path, "main.py", code)) == [("GET", "/a/x"), ("GET", "/b/x")]


def test_fastapi_router_imported_from_another_file_is_not_followed(tmp_path):
    code = FASTAPI + (
        "from .users import router\n"
        '@app.get("/health")\ndef h(): ...\n'
        'app.include_router(router, prefix="/api")\n'
    )
    assert pairs(endpoints(tmp_path, "main.py", code)) == [("GET", "/health")]


# =================================================================== Flask


FLASK = "from flask import Flask, Blueprint\napp = Flask(__name__)\n"


def test_flask_register_blueprint_url_prefix_is_applied(tmp_path):
    code = FLASK + (
        'bp = Blueprint("api", __name__)\n'
        '@bp.route("/users")\ndef a(): ...\n'
        'app.register_blueprint(bp, url_prefix="/api")\n'
    )
    found = endpoints(tmp_path, "app.py", code)
    assert pairs(found) == [("GET", "/api/users")] and found[0].confidence == HIGH


def test_flask_registration_prefix_replaces_the_blueprints_own_prefix(tmp_path):
    # Flask: `url_prefix` given at registration overrides the one on the Blueprint
    code = FLASK + (
        'bp = Blueprint("api", __name__, url_prefix="/own")\n'
        '@bp.route("/a")\ndef a(): ...\n'
        'app.register_blueprint(bp, url_prefix="/api")\n'
    )
    assert pairs(endpoints(tmp_path, "app.py", code)) == [("GET", "/api/a")]


def test_flask_registration_without_prefix_keeps_the_blueprints_own_prefix(tmp_path):
    code = FLASK + (
        'bp = Blueprint("api", __name__, url_prefix="/own")\n'
        '@bp.route("/a")\ndef a(): ...\n'
        "app.register_blueprint(bp)\n"
    )
    assert pairs(endpoints(tmp_path, "app.py", code)) == [("GET", "/own/a")]


def test_flask_non_literal_registration_prefix_is_not_guessed(tmp_path):
    code = FLASK + (
        'bp = Blueprint("api", __name__, url_prefix="/own")\n'
        '@bp.route("/a")\ndef a(): ...\n'
        "app.register_blueprint(bp, url_prefix=CFG.PREFIX)\n"
    )
    found = endpoints(tmp_path, "app.py", code)
    assert pairs(found) == [("GET", "/a")] and found[0].confidence == MEDIUM  # neither prefix is assumed


def test_flask_blueprint_registered_twice_has_both_paths(tmp_path):
    code = FLASK + (
        'bp = Blueprint("api", __name__)\n'
        '@bp.route("/a")\ndef a(): ...\n'
        'app.register_blueprint(bp, url_prefix="/v1")\napp.register_blueprint(bp, url_prefix="/v2")\n'
    )
    assert pairs(endpoints(tmp_path, "app.py", code)) == [("GET", "/v1/a"), ("GET", "/v2/a")]


def test_flask_blueprint_from_another_file_is_not_followed(tmp_path):
    code = FLASK + (
        "from .views import bp\n"
        '@app.route("/health")\ndef h(): ...\n'
        'app.register_blueprint(bp, url_prefix="/api")\n'
    )
    assert pairs(endpoints(tmp_path, "app.py", code)) == [("GET", "/health")]


def test_nested_flask_blueprints_compose(tmp_path):
    code = FLASK + (
        'parent = Blueprint("p", __name__)\nchild = Blueprint("c", __name__)\n'
        '@child.route("/leaf")\ndef leaf(): ...\n'
        'parent.register_blueprint(child, url_prefix="/child")\n'
        'app.register_blueprint(parent, url_prefix="/parent")\n'
    )
    assert pairs(endpoints(tmp_path, "app.py", code)) == [("GET", "/parent/child/leaf")]


def test_mount_calls_of_the_wrong_framework_are_ignored(tmp_path):
    code = FLASK + (
        'bp = Blueprint("api", __name__)\n'
        '@bp.route("/a")\ndef a(): ...\n'
        'app.include_router(bp, prefix="/wrong")\n'  # include_router belongs to FastAPI
    )
    assert pairs(endpoints(tmp_path, "app.py", code)) == [("GET", "/a")]
