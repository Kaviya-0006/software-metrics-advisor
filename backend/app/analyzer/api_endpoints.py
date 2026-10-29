"""
Project Architecture Intelligence — Phase 8B: API endpoint detection.

Finds the HTTP endpoints a project *declares*, by static analysis only. The
uploaded code is never executed, imported or contacted.

Frameworks and patterns
-----------------------
* **Python — FastAPI:** ``@app.get/post/put/patch/delete("/p")``,
  ``@router.get(...)``, ``@app.api_route("/p", methods=[...])``; a static
  ``APIRouter(prefix="/x")`` prefix is applied.
* **Python — Flask:** ``@app.route("/p")``, ``@bp.route("/p", methods=[...])``
  (no ``methods`` means GET, Flask's default), the Flask 2 shortcuts
  ``@app.get(...)``; a static ``Blueprint(..., url_prefix="/x")`` is applied.
* **Java — Spring:** ``@GetMapping/@PostMapping/@PutMapping/@PatchMapping/
  @DeleteMapping``, ``@RequestMapping(value=..., method=RequestMethod.X)``,
  with the class-level ``@RequestMapping("/api")`` prefix joined to the
  method-level path (``/api`` + ``/users`` = ``/api/users``).
* **JavaScript / TypeScript — Express:** ``app.get/post/put/patch/delete(
  "/p", handler)`` and the same on a ``Router``.

How source is accessed (and why)
--------------------------------
The input is the pipeline's own ``ParsedModule`` list — the representation
the metric calculators already use. Each carries the language chosen by the
scanner, the source text, the import list, the parse error (if any) and the
parse tree: Python's ``ast``, javalang's tree, or the JS/TS lexer's token
stream in which comments, strings and templates are already blanked. So:

* no second scanner and no second source loader;
* no regular expressions over raw text — a route-looking string, comment or
  test description can never be mistaken for a route, because the parsers
  have already separated code from everything else;
* nothing is parsed twice. The upload flow deletes the extracted files as
  soon as the request ends, so a later analysis cannot re-read them; the one
  parse the engine already performs must be reused. The engine therefore
  gained a single additive switch, ``analyze_project(root,
  retain_parsed=True)``, that keeps the parsed modules on
  ``ProjectAnalysis.parsed_modules`` (empty by default, never serialized).

Confidence
----------
* HIGH   — a clear, framework-specific static pattern whose framework is
  established (the receiver is built by ``FastAPI()`` / ``APIRouter()`` /
  ``Flask()`` / ``Blueprint()`` / ``express()`` / ``Router()``, or the file
  imports the framework), or a Spring mapping annotation with an explicit
  HTTP method.
* MEDIUM — the pattern is recognizable but something is assumed or
  unresolved: no framework evidence for a pattern shared by several
  frameworks (``@app.get`` is FastAPI *and* Flask 2), a Spring
  ``@RequestMapping`` without ``method`` (matches every method, reported as
  ``ANY``), a router prefix that is not a literal, a Spring interface.
* LOW    — only the receiver's conventional name (``app``/``router``) links a
  call to Express: ``app.get("/x", handler)`` with no ``express`` in sight.

Conservative by design
----------------------
Paths that are not static string literals (variables, f-strings,
concatenation, regexes) are *not* guessed; they are skipped and counted in
``dynamic_paths_skipped``. Calls that merely look similar are rejected:
Express's settings getter ``app.get("title")`` (no handler), HTTP *clients*
such as ``axios.get("/u", cfg)`` or ``request(app).get("/u")``, Spring
``@FeignClient`` interfaces (outgoing calls declared with the same
annotations), and files that use a different framework (Sanic, Koa,
Fastify, ...). When Phase 8A architecture information is supplied, files in
the frontend, tests, database, shared and configuration layers are not
searched; files in unknown layers *are* searched, and the endpoint records
their honest layer (``unknown``) instead of pretending it is backend.

Mount prefixes (same file only)
-------------------------------
A router's prefix is resolved when the mount is a literal in the same file as
the router: FastAPI ``app.include_router(router, prefix="/api")`` (joined
before the router's own prefix), Flask ``app.register_blueprint(bp,
url_prefix="/api")`` (replaces the blueprint's own prefix), and Express
``app.use("/api", router)`` (also arrays of literals and chains such as
``app.use("/api", v1); v1.use("/users", users)``). A router mounted under
several prefixes yields one endpoint per prefix. A mount whose prefix is not a
literal is *not* guessed: the endpoint keeps its path without that prefix and
is reported at MEDIUM confidence. Routers mounted from another file
(``app.use("/api", require("./routes"))``, an imported router passed to
``include_router``) are not followed.

Known limitations
-----------------
Chained ``router.route("/p").get(...)``, class inheritance of mappings,
WebFlux functional routes, NestJS/Koa/Fastify, nested Flask blueprints
registered through a blueprint imported from another file, cross-file mount
prefixes and dynamically generated routes are out of scope.
"""

from __future__ import annotations

import ast
import bisect
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable

import javalang

from app.analyzer.architecture import (
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
    LAYER_CONFIGURATION,
    LAYER_DATABASE,
    LAYER_FRONTEND,
    LAYER_SHARED,
    LAYER_TESTS,
    ArchitectureSummary,
)
from app.analyzer.parser import ParsedModule

FRAMEWORK_FASTAPI = "FastAPI"
FRAMEWORK_FLASK = "Flask"
FRAMEWORK_SPRING = "Spring"
FRAMEWORK_EXPRESS = "Express"

METHOD_ANY = "ANY"  # the endpoint answers every HTTP method

_CONFIDENCE_RANK = {CONFIDENCE_HIGH: 3, CONFIDENCE_MEDIUM: 2, CONFIDENCE_LOW: 1}

# Layers that are never searched when architecture information is supplied.
_EXCLUDED_LAYERS = frozenset(
    {LAYER_FRONTEND, LAYER_TESTS, LAYER_DATABASE, LAYER_SHARED, LAYER_CONFIGURATION}
)
_SUPPORTED_LANGUAGES = frozenset({"python", "java", "javascript", "typescript"})


# --------------------------------------------------------------------------
# Result model
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ApiEndpoint:
    """One HTTP endpoint declaration found in the source."""

    method: str  # GET, POST, PUT, PATCH, DELETE, HEAD, OPTIONS, TRACE or ANY
    path: str  # normalized: leading '/', no trailing '/', path variables kept as written
    source_file: str  # path relative to the project root, as in ModuleResult.file_path
    line_number: int  # 1-based line of the route declaration
    language: str
    framework: str  # FastAPI | Flask | Spring | Express
    confidence: str  # high | medium | low
    handler: str | None = None  # function/method name when statically known
    layer: str | None = None  # Phase 8A layer of the file; None when no architecture was given

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "path": self.path,
            "source_file": self.source_file,
            "line_number": self.line_number,
            "language": self.language,
            "framework": self.framework,
            "confidence": self.confidence,
            "handler": self.handler,
            "layer": self.layer,
        }


@dataclass
class ApiEndpointSummary:
    """All endpoints detected in a project, plus what was (not) examined."""

    endpoints: list[ApiEndpoint] = field(default_factory=list)
    files_scanned: int = 0  # supported-language, parsed files that were searched
    files_excluded_by_layer: int = 0  # supported-language files skipped due to their 8A layer
    dynamic_paths_skipped: int = 0  # route declarations whose path/methods are not literals

    @property
    def total_endpoints(self) -> int:
        return len(self.endpoints)

    def _count(self, attribute: str) -> dict[str, int]:
        counts = Counter(getattr(e, attribute) for e in self.endpoints)
        return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))

    @property
    def by_method(self) -> dict[str, int]:
        return self._count("method")

    @property
    def by_framework(self) -> dict[str, int]:
        return self._count("framework")

    @property
    def by_confidence(self) -> dict[str, int]:
        return self._count("confidence")

    def to_dict(self, include_endpoints: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "total_endpoints": self.total_endpoints,
            "by_method": self.by_method,
            "by_framework": self.by_framework,
            "by_confidence": self.by_confidence,
            "files_scanned": self.files_scanned,
            "files_excluded_by_layer": self.files_excluded_by_layer,
            "dynamic_paths_skipped": self.dynamic_paths_skipped,
        }
        if include_endpoints:
            data["endpoints"] = [e.to_dict() for e in self.endpoints]
        return data


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------


def join_paths(*parts: str | None) -> str:
    """
    Join path fragments into one normalized route: always a leading '/', no
    duplicate or trailing slashes (``"/api" + "/users/"`` -> ``/api/users``;
    ``"" + ""`` -> ``/``).
    """
    segments: list[str] = []
    for part in parts:
        segments.extend(seg for seg in (part or "").strip().split("/") if seg)
    return "/" + "/".join(segments)


def _lower_confidence(current: str, ceiling: str) -> str:
    return current if _CONFIDENCE_RANK[current] <= _CONFIDENCE_RANK[ceiling] else ceiling


# --------------------------------------------------------------------------
# Python: FastAPI and Flask (via the `ast` tree the parser already built)
# --------------------------------------------------------------------------

_PY_VERBS = frozenset({"get", "post", "put", "patch", "delete"})
# Receiver names accepted when nothing in the file establishes the framework
# (so `@mock.patch("os.path.join")` is never mistaken for `@app.patch("/x")`).
_PY_CONVENTIONAL_RECEIVERS = frozenset({"app", "router", "api", "bp", "blueprint", "server", "application"})
_PY_FASTAPI_CONSTRUCTORS = frozenset({"FastAPI", "APIRouter"})
_PY_FLASK_CONSTRUCTORS = frozenset({"Flask", "Blueprint"})
# Frameworks that reuse `@app.route` / `@app.get`; a file that uses one of them
# (and neither FastAPI nor Flask) must not be reported as FastAPI/Flask.
_PY_OTHER_FRAMEWORKS = frozenset(
    {
        "sanic", "bottle", "quart", "falcon", "pyramid", "tornado", "aiohttp",
        "starlette", "litestar", "django", "cherrypy", "responder", "hug", "klein", "web",
    }
)


@dataclass
class _PyReceiver:
    framework: str
    prefix: str = ""
    prefix_is_dynamic: bool = False


def _dotted_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted_name(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


def _str_constant(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _py_imported_roots(tree: ast.AST) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


def _py_receivers(tree: ast.AST) -> dict[str, _PyReceiver]:
    """Variables built by FastAPI()/APIRouter()/Flask()/Blueprint(), with a literal prefix if given."""
    receivers: dict[str, _PyReceiver] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            target, value = node.target, node.value
        else:
            continue
        if not isinstance(value, ast.Call):
            continue
        name = _dotted_name(target)
        constructor = _dotted_name(value.func)
        if not name or not constructor:
            continue
        last = constructor.split(".")[-1]
        if last in _PY_FASTAPI_CONSTRUCTORS:
            framework, prefix_keyword = FRAMEWORK_FASTAPI, "prefix"
        elif last in _PY_FLASK_CONSTRUCTORS:
            framework, prefix_keyword = FRAMEWORK_FLASK, ("url_prefix" if last == "Blueprint" else None)
        else:
            continue
        receiver = _PyReceiver(framework)
        if prefix_keyword:
            for keyword in value.keywords:
                if keyword.arg == prefix_keyword:
                    literal = _str_constant(keyword.value)
                    if literal is None:
                        receiver.prefix_is_dynamic = True
                    else:
                        receiver.prefix = literal
        receivers[name] = receiver
    return receivers


def _py_framework(
    receiver: _PyReceiver | None, roots: set[str], attribute: str, receiver_name: str
) -> tuple[str | None, bool]:
    """-> (framework or None when the file belongs to another framework, evidenced?)."""
    if receiver is not None:
        return receiver.framework, True
    has_fastapi, has_flask = "fastapi" in roots, "flask" in roots
    if has_fastapi and not has_flask:
        return FRAMEWORK_FASTAPI, True
    if has_flask and not has_fastapi:
        return FRAMEWORK_FLASK, True
    if has_fastapi and has_flask:
        return (FRAMEWORK_FLASK if attribute == "route" else FRAMEWORK_FASTAPI), False
    if roots & _PY_OTHER_FRAMEWORKS:
        return None, False
    # No evidence at all: only a conventionally named receiver is believed, and
    # the framework is a guess from the decorator (reported as MEDIUM).
    if receiver_name.split(".")[-1].lower() not in _PY_CONVENTIONAL_RECEIVERS:
        return None, False
    return (FRAMEWORK_FLASK if attribute == "route" else FRAMEWORK_FASTAPI), False


@dataclass
class _PyMount:
    """`host.include_router(child, prefix=...)` / `host.register_blueprint(child, url_prefix=...)`."""

    host: str
    prefix: str | None  # None: a prefix argument is present but not a literal
    explicit: bool  # a prefix argument was given at all


def _py_mounts(tree: ast.AST, receivers: dict[str, _PyReceiver]) -> dict[str, list[_PyMount]]:
    """Same-file mounts of routers/blueprints that were built in this file."""
    mounts: dict[str, list[_PyMount]] = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        attribute = node.func.attr
        if attribute not in ("include_router", "register_blueprint") or not node.args:
            continue
        host = _dotted_name(node.func.value)
        child = _dotted_name(node.args[0])
        info = receivers.get(child) if child else None
        if host is None or info is None:
            continue  # the router is not built in this file: nothing safe to resolve
        # include_router belongs to FastAPI, register_blueprint to Flask.
        if (attribute == "include_router") != (info.framework == FRAMEWORK_FASTAPI):
            continue
        keyword = "prefix" if attribute == "include_router" else "url_prefix"
        prefix: str | None = ""
        explicit = False
        for argument in node.keywords:
            if argument.arg == keyword:
                explicit = True
                prefix = _str_constant(argument.value)  # None when not a literal
        mounts.setdefault(child, []).append(_PyMount(host, prefix, explicit))
    return mounts


def _py_variants(
    name: str,
    receivers: dict[str, _PyReceiver],
    mounts: dict[str, list[_PyMount]],
    seen: frozenset[str] = frozenset(),
) -> list[tuple[str, bool]]:
    """
    Every prefix a router/blueprint is reachable under, as (prefix, unresolved).
    No same-file mount: its own constructor prefix. FastAPI joins the mount's
    prefix *before* the router's own prefix; Flask's `url_prefix` at
    registration *replaces* the blueprint's own. A non-literal prefix keeps the
    path without it and flags it unresolved — never a guess.
    """
    info = receivers[name]
    own = (info.prefix, info.prefix_is_dynamic)
    edges = mounts.get(name)
    if not edges or name in seen:
        return [own]
    found: list[tuple[str, bool]] = []
    for edge in edges:
        if edge.host in receivers and edge.host != name:
            host_variants = _py_variants(edge.host, receivers, mounts, seen | {name})
        else:
            host_variants = [("", False)]
        for host_prefix, host_unresolved in host_variants:
            if info.framework == FRAMEWORK_FASTAPI:
                if edge.prefix is None:
                    found.append((join_paths(host_prefix, info.prefix), True))
                else:
                    found.append((join_paths(host_prefix, edge.prefix, info.prefix), host_unresolved or info.prefix_is_dynamic))
            else:  # Flask
                if edge.explicit and edge.prefix is None:
                    found.append((host_prefix, True))
                elif edge.explicit:
                    found.append((join_paths(host_prefix, edge.prefix), host_unresolved))
                else:
                    found.append((join_paths(host_prefix, info.prefix), host_unresolved or info.prefix_is_dynamic))
    return list(dict.fromkeys(found))


def _py_methods(call: ast.Call) -> list[str] | None:
    """Literal `methods=[...]` -> upper-cased list; no keyword -> []; non-literal -> None."""
    for keyword in call.keywords:
        if keyword.arg == "methods":
            if not isinstance(keyword.value, (ast.List, ast.Tuple, ast.Set)):
                return None
            methods = []
            for element in keyword.value.elts:
                literal = _str_constant(element)
                if literal is None:
                    return None
                methods.append(literal.upper())
            return methods
    return []


def _detect_python(module: ParsedModule) -> tuple[list[ApiEndpoint], int]:
    tree = module.raw_tree
    if not isinstance(tree, ast.AST):
        return [], 0

    roots = _py_imported_roots(tree)
    receivers = _py_receivers(tree)
    mounts = _py_mounts(tree, receivers)
    path_ = module.source_file.relative_path
    endpoints: list[ApiEndpoint] = []
    dynamic = 0

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)):
                continue
            attribute = decorator.func.attr
            if attribute not in _PY_VERBS and attribute not in ("route", "api_route"):
                continue

            receiver_name = _dotted_name(decorator.func.value)
            if receiver_name is None:
                continue
            receiver = receivers.get(receiver_name)
            framework, evidenced = _py_framework(receiver, roots, attribute, receiver_name)
            if framework is None:
                continue
            # `route` is Flask's; `api_route` is FastAPI's; verbs exist in both.
            if (framework == FRAMEWORK_FASTAPI and attribute == "route") or (
                framework == FRAMEWORK_FLASK and attribute == "api_route"
            ):
                continue

            path_node = decorator.args[0] if decorator.args else None
            if path_node is None:
                for keyword in decorator.keywords:
                    if keyword.arg in ("path", "rule"):
                        path_node = keyword.value
            if path_node is None:
                continue  # `@app.get()` declares nothing we can name
            route_path = _str_constant(path_node)
            if route_path is None:
                dynamic += 1
                continue
            # FastAPI and Flask both require a route to start with "/" (an empty
            # path is only valid on a router that has a prefix). Anything else,
            # such as `@mock.patch("os.path.join")`, is not a route.
            if not (route_path.startswith("/") or (route_path == "" and receiver is not None and receiver.prefix)):
                continue

            if attribute in _PY_VERBS:
                methods = [attribute.upper()]
            else:
                declared = _py_methods(decorator)
                if declared is None:
                    dynamic += 1
                    continue
                methods = declared or ["GET"]  # Flask / FastAPI default when omitted

            variants = _py_variants(receiver_name, receivers, mounts) if receiver is not None else [("", False)]
            for prefix, unresolved in variants:
                confidence = CONFIDENCE_HIGH if evidenced else CONFIDENCE_MEDIUM
                if unresolved:
                    confidence = _lower_confidence(confidence, CONFIDENCE_MEDIUM)  # prefix unresolved
                full_path = join_paths(prefix, route_path)
                for method in methods:
                    endpoints.append(
                        ApiEndpoint(
                            method=method,
                            path=full_path,
                            source_file=path_,
                            line_number=decorator.lineno,
                            language="python",
                            framework=framework,
                            confidence=confidence,
                            handler=node.name,
                        )
                    )
    return endpoints, dynamic


# --------------------------------------------------------------------------
# Java: Spring (via the javalang tree the parser already built)
# --------------------------------------------------------------------------

_JAVA_VERB_ANNOTATIONS = {
    "GetMapping": "GET",
    "PostMapping": "POST",
    "PutMapping": "PUT",
    "PatchMapping": "PATCH",
    "DeleteMapping": "DELETE",
}
_JAVA_HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "TRACE"})
_JAVA_CONTROLLER_ANNOTATIONS = frozenset({"RestController", "Controller"})


def _java_strings(node: Any) -> list[str] | None:
    """Literal string(s) of an annotation value; None when not statically a string/array of strings."""
    if isinstance(node, javalang.tree.Literal):
        value = node.value
        if (
            isinstance(value, str)
            and len(value) >= 2
            and value[0] == '"'
            and value[-1] == '"'
            and not node.prefix_operators
            and not node.postfix_operators
            and not node.selectors
        ):
            return [value[1:-1]]
        return None
    if isinstance(node, javalang.tree.ElementArrayValue):
        collected: list[str] = []
        for item in node.values or []:
            sub = _java_strings(item)
            if sub is None:
                return None
            collected.extend(sub)
        return collected
    return None


def _java_attribute(annotation: Any, names: tuple[str, ...]) -> tuple[bool, Any]:
    """The value of the first of `names` in the annotation; the single-element
    shorthand `@X("v")` counts as `value`."""
    element = annotation.element
    if element is None:
        return False, None
    if isinstance(element, list):
        for pair in element:
            if pair.name in names:
                return True, pair.value
        return False, None
    return ("value" in names), (element if "value" in names else None)


def _java_paths(annotation: Any) -> tuple[list[str], bool]:
    """-> (paths, path_is_dynamic). No path means the mapping applies to the base path."""
    found, node = _java_attribute(annotation, ("value", "path"))
    if not found:
        return [""], False
    values = _java_strings(node)
    if values is None:
        return [""], True
    return (values or [""]), False


def _java_request_methods(annotation: Any) -> list[str] | None:
    """`method = RequestMethod.X` / `{RequestMethod.X, ...}` -> names; None when absent/unrecognized."""
    found, node = _java_attribute(annotation, ("method",))
    if not found:
        return None
    nodes = node.values if isinstance(node, javalang.tree.ElementArrayValue) else [node]
    methods: list[str] = []
    for item in nodes or []:
        if isinstance(item, javalang.tree.MemberReference) and item.member in _JAVA_HTTP_METHODS:
            methods.append(item.member)
        else:
            return None
    return methods or None


def _annotation_line(annotation: Any, fallback: Any) -> int:
    position = getattr(annotation, "position", None) or getattr(fallback, "position", None)
    return position.line if position else 0


def _detect_java(module: ParsedModule) -> tuple[list[ApiEndpoint], int]:
    tree = module.raw_tree
    if not isinstance(tree, javalang.tree.CompilationUnit):
        return [], 0

    path_ = module.source_file.relative_path
    endpoints: list[ApiEndpoint] = []
    dynamic = 0

    for kind in (javalang.tree.ClassDeclaration, javalang.tree.InterfaceDeclaration):
        for _, declaration in tree.filter(kind):
            annotation_names = {a.name for a in declaration.annotations}
            if "FeignClient" in annotation_names:
                continue  # declares *outgoing* calls with the same annotations

            base_confidence = CONFIDENCE_HIGH
            if kind is javalang.tree.InterfaceDeclaration:
                base_confidence = CONFIDENCE_MEDIUM

            prefixes, prefix_dynamic = [""], False
            for annotation in declaration.annotations:
                if annotation.name == "RequestMapping":
                    prefixes, prefix_dynamic = _java_paths(annotation)
            if prefix_dynamic:
                dynamic += 1
                base_confidence = _lower_confidence(base_confidence, CONFIDENCE_MEDIUM)

            for method in declaration.methods:
                for annotation in method.annotations:
                    verb = _JAVA_VERB_ANNOTATIONS.get(annotation.name)
                    if verb is None and annotation.name != "RequestMapping":
                        continue

                    paths, path_dynamic = _java_paths(annotation)
                    if path_dynamic:
                        dynamic += 1
                        continue

                    confidence = base_confidence
                    if verb is not None:
                        methods = [verb]
                    else:
                        declared = _java_request_methods(annotation)
                        if declared is None:
                            methods = [METHOD_ANY]  # no `method`: matches every HTTP method
                            confidence = _lower_confidence(confidence, CONFIDENCE_MEDIUM)
                        else:
                            methods = declared

                    line = _annotation_line(annotation, method)
                    for prefix in prefixes:
                        for sub_path in paths:
                            for http_method in methods:
                                endpoints.append(
                                    ApiEndpoint(
                                        method=http_method,
                                        path=join_paths(prefix, sub_path),
                                        source_file=path_,
                                        line_number=line,
                                        language="java",
                                        framework=FRAMEWORK_SPRING,
                                        confidence=confidence,
                                        handler=method.name,
                                    )
                                )
    return endpoints, dynamic


# --------------------------------------------------------------------------
# JavaScript / TypeScript: Express (via the lexer's token stream)
# --------------------------------------------------------------------------

_JS_VERBS = frozenset({"get", "post", "put", "patch", "delete"})
_JS_CONVENTIONAL_RECEIVERS = frozenset({"app", "router"})
_JS_OTHER_FRAMEWORKS = frozenset(
    {
        "koa", "koa-router", "@koa/router", "fastify", "restify", "hono", "@hapi/hapi", "hapi",
        "polka", "@nestjs/common", "itty-router", "h3",
    }
)


def _js_express_receivers(tree: Any) -> dict[str, str]:
    """Variables assigned from express() / express.Router() / Router() / require('express')()."""
    texts, kinds, starts, ends = tree.texts, tree.kinds, tree.starts, tree.ends
    n = len(texts)
    found: dict[str, str] = {}
    for j in range(n):
        if kinds[j] != "punct" or texts[j] != "=":
            continue
        k = j + 1
        if k < n and texts[k] == "new":
            k += 1
        constructor = None
        if k + 1 < n and texts[k] == "express" and texts[k + 1] == "(":
            constructor = "express"
        elif k + 3 < n and texts[k : k + 4] == ["express", ".", "Router", "("]:
            constructor = "express.Router"
        elif k + 1 < n and texts[k] == "Router" and texts[k + 1] == "(":
            constructor = "Router"
        elif (
            k + 4 < n
            and texts[k] == "require"
            and texts[k + 1] == "("
            and kinds[k + 2] == "str"
            and tree.no_comments[starts[k + 2] + 1 : ends[k + 2] - 1] == "express"
            and texts[k + 3] == ")"
            and texts[k + 4] == "("
        ):
            constructor = "express"
        if constructor is None:
            continue
        m = j - 1  # the declared name, skipping an optional `: Type` annotation
        if m >= 2 and texts[m - 1] == ":":
            m -= 2
        if m >= 0 and kinds[m] == "id":
            found[texts[m]] = constructor
    return found


def _js_has_following_argument(tree: Any, k: int) -> bool:
    """True if the argument starting at token k is followed by a depth-0 comma
    (i.e. the call has a handler); False if it is the only argument."""
    texts, kinds = tree.texts, tree.kinds
    depth = 0
    for index in range(k, len(texts)):
        if kinds[index] != "punct":
            continue
        text = texts[index]
        if text in ("(", "[", "{"):
            depth += 1
        elif text in (")", "]", "}"):
            if depth == 0:
                return False
            depth -= 1
        elif text == "," and depth == 0:
            return True
    return False


def _js_first_argument(tree: Any, k: int) -> tuple[list[str] | None, str]:
    """
    Read the route path starting at token k. Returns (paths, status):
    status "static" with the literal path(s); "dynamic" for a route-looking
    call whose path is not a literal; "none" when the call has no handler
    argument (Express settings getter `app.get("title")`) or is not route-like.
    """
    texts, kinds, starts = tree.texts, tree.kinds, tree.starts
    n = len(texts)
    if k >= n:
        return None, "none"

    def followed_by_handler(end: int) -> bool:
        return end < n and texts[end] == "," and end + 1 < n and texts[end + 1] != ")"

    if kinds[k] == "str" and texts[k] and texts[k][0] in "\"'":
        if followed_by_handler(k + 1):
            return [tree.no_comments[starts[k] + 1 : tree.ends[k] - 1]], "static"
    elif kinds[k] == "str" and texts[k] == "`":
        close = k + 1
        if close < n and texts[close] == "`" and kinds[close] == "str":
            if followed_by_handler(close + 1):  # no ${...}: a static template
                return [tree.no_comments[starts[k] + 1 : starts[close]]], "static"
        elif _js_has_following_argument(tree, k):
            return None, "dynamic"
        return None, "none"
    elif texts[k] == "[" and kinds[k] == "punct":
        paths: list[str] = []
        index = k + 1
        while index < n:
            if kinds[index] == "str" and texts[index] and texts[index][0] in "\"'":
                paths.append(tree.no_comments[starts[index] + 1 : tree.ends[index] - 1])
                index += 1
                if index < n and texts[index] == ",":
                    index += 1
                    continue
                if index < n and texts[index] == "]" and followed_by_handler(index + 1):
                    return paths, "static"
            break
        if _js_has_following_argument(tree, k):
            return None, "dynamic"
        return None, "none"

    # Anything else (identifier, call, concatenation, regex, ...): dynamic only
    # if it really looks like a route call, i.e. a handler argument follows.
    if _js_has_following_argument(tree, k):
        return None, "dynamic"
    return None, "none"


def _js_call_arguments(tree: Any, open_index: int) -> tuple[list[tuple[int, int]], int]:
    """Token ranges [start, end) of the depth-0 arguments of the call whose `(` is at open_index."""
    texts, kinds = tree.texts, tree.kinds
    arguments: list[tuple[int, int]] = []
    depth = 0
    start = open_index + 1
    for index in range(open_index, len(texts)):
        if kinds[index] != "punct":
            continue
        text = texts[index]
        if text in ("(", "[", "{"):
            depth += 1
        elif text in (")", "]", "}"):
            depth -= 1
            if depth == 0:
                if index > start:
                    arguments.append((start, index))
                return arguments, index
        elif text == "," and depth == 1:
            arguments.append((start, index))
            start = index + 1
    return arguments, len(texts)


def _js_literal_paths(tree: Any, start: int, end: int) -> list[str] | None:
    """A single string, a static template literal, or an array of strings; None otherwise."""
    texts, kinds, starts, ends = tree.texts, tree.kinds, tree.starts, tree.ends

    def quoted(k: int) -> bool:
        return kinds[k] == "str" and bool(texts[k]) and texts[k][0] in "\"'"

    if end - start == 1 and quoted(start):
        return [tree.no_comments[starts[start] + 1 : ends[start] - 1]]
    if end - start == 2 and texts[start] == "`" and texts[start + 1] == "`":
        return [tree.no_comments[starts[start] + 1 : starts[start + 1]]]
    if end - start >= 3 and texts[start] == "[" and texts[end - 1] == "]":
        paths: list[str] = []
        index = start + 1
        while index < end - 1:
            if not quoted(index):
                return None
            paths.append(tree.no_comments[starts[index] + 1 : ends[index] - 1])
            index += 1
            if index < end - 1:
                if texts[index] != ",":
                    return None
                index += 1
        return paths
    return None


def _js_mounts(tree: Any, receivers: dict[str, str]) -> dict[str, list[tuple[str, list[str | None]]]]:
    """
    Same-file `host.use("/prefix", router)` mounts of routers built in this file:
    child -> [(host, prefixes)], a prefix of None meaning "present but not a literal".
    """
    texts, kinds = tree.texts, tree.kinds
    n = len(texts)
    routers = {name for name, constructor in receivers.items() if constructor in ("express.Router", "Router")}
    mounts: dict[str, list[tuple[str, list[str | None]]]] = {}
    for i in range(2, n):
        if kinds[i] != "id" or texts[i] != "use" or texts[i - 1] != "." or i + 1 >= n or texts[i + 1] != "(":
            continue
        if kinds[i - 2] != "id" or (i - 3 >= 0 and texts[i - 3] == "."):
            continue
        host = texts[i - 2]
        if host not in receivers and host not in _JS_CONVENTIONAL_RECEIVERS:
            continue
        arguments, _ = _js_call_arguments(tree, i + 1)
        if not arguments:
            continue
        children = [
            texts[a] for a, b in arguments if b - a == 1 and kinds[a] == "id" and texts[a] in routers and texts[a] != host
        ]
        if not children:
            continue  # e.g. `app.use("/api", require("./routes"))`: another file, not resolvable here
        first_start, first_end = arguments[0]
        if first_end - first_start == 1 and texts[first_start] in children:
            prefixes: list[str | None] = [""]  # `app.use(router)`: mounted at the root
        else:
            literal = _js_literal_paths(tree, first_start, first_end)
            prefixes = list(literal) if literal is not None else [None]
        for child in children:
            mounts.setdefault(child, []).append((host, prefixes))
    return mounts


def _js_variants(
    name: str,
    mounts: dict[str, list[tuple[str, list[str | None]]]],
    seen: frozenset[str] = frozenset(),
) -> list[tuple[str, bool]]:
    """Every prefix an Express router is reachable under, as (prefix, unresolved)."""
    edges = mounts.get(name)
    if not edges or name in seen:
        return [("", False)]
    found: list[tuple[str, bool]] = []
    for host, prefixes in edges:
        host_variants = _js_variants(host, mounts, seen | {name}) if host in mounts else [("", False)]
        for host_prefix, host_unresolved in host_variants:
            for prefix in prefixes:
                if prefix is None:
                    found.append((host_prefix, True))
                else:
                    found.append((join_paths(host_prefix, prefix), host_unresolved))
    return list(dict.fromkeys(found))


def _detect_javascript(module: ParsedModule) -> tuple[list[ApiEndpoint], int]:
    tree = module.raw_tree
    if not hasattr(tree, "texts") or not hasattr(tree, "no_comments"):
        return [], 0

    imports = set(tree.imports)
    has_express = "express" in imports
    if imports & _JS_OTHER_FRAMEWORKS and not has_express:
        return [], 0  # a different framework uses the same call shape

    texts, kinds = tree.texts, tree.kinds
    n = len(texts)
    receivers = _js_express_receivers(tree)
    mounts = _js_mounts(tree, receivers)
    newline_positions = [i for i, ch in enumerate(tree.clean) if ch == "\n"]
    path_ = module.source_file.relative_path
    endpoints: list[ApiEndpoint] = []
    dynamic = 0

    for i in range(2, n):
        if kinds[i] != "id" or texts[i] not in _JS_VERBS or texts[i - 1] != ".":
            continue
        if i + 1 >= n or texts[i + 1] != "(" or kinds[i - 2] != "id":
            continue
        receiver = texts[i - 2]
        if i - 3 >= 0 and texts[i - 3] == ".":
            if not (i - 4 >= 0 and texts[i - 4] == "this"):
                continue  # `req.app.get(...)`, `a.b.get(...)`: chains are not tracked

        constructor = receivers.get(receiver)
        if constructor is not None:
            confidence = CONFIDENCE_HIGH
            if constructor == "Router" and not has_express:
                confidence = CONFIDENCE_MEDIUM
        elif receiver in _JS_CONVENTIONAL_RECEIVERS:
            confidence = CONFIDENCE_MEDIUM if has_express else CONFIDENCE_LOW
        else:
            continue  # axios.get, api.get, supertest agents, ...

        paths, status = _js_first_argument(tree, i + 2)
        if status == "dynamic":
            dynamic += 1
            continue
        if status != "static" or not paths:
            continue

        line = bisect.bisect_left(newline_positions, tree.starts[i]) + 1
        variants = _js_variants(receiver, mounts) if receiver in mounts else [("", False)]
        for prefix, unresolved in variants:
            variant_confidence = _lower_confidence(confidence, CONFIDENCE_MEDIUM) if unresolved else confidence
            for route_path in paths:
                endpoints.append(
                    ApiEndpoint(
                        method=texts[i].upper(),
                        path=join_paths(prefix, route_path),
                        source_file=path_,
                        line_number=line,
                        language=module.language,
                        framework=FRAMEWORK_EXPRESS,
                        confidence=variant_confidence,
                    )
                )
    return endpoints, dynamic


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

_DETECTORS = {
    "python": _detect_python,
    "java": _detect_java,
    "javascript": _detect_javascript,
    "typescript": _detect_javascript,
}


def detect_endpoints_in_module(module: ParsedModule) -> tuple[list[ApiEndpoint], int]:
    """
    Endpoints declared in one parsed module, and how many route declarations
    were skipped because their path/methods are not literals. Files that failed
    to parse, and languages other than Python/Java/JavaScript/TypeScript, yield
    nothing. Never raises on malformed input.
    """
    detector = _DETECTORS.get(module.language)
    if detector is None or module.parse_error is not None or module.raw_tree is None:
        return [], 0
    try:
        return detector(module)
    except Exception:  # a heuristic detector must never take the analysis down
        return [], 0


def detect_api_endpoints(
    modules: Iterable[ParsedModule],
    architecture: ArchitectureSummary | None = None,
) -> ApiEndpointSummary:
    """
    Detect the HTTP endpoints declared across `modules` (typically
    ``analyze_project(root, retain_parsed=True).parsed_modules``).

    With a Phase 8A `architecture`, files whose layer is frontend, tests,
    database, shared or configuration are not searched, and every endpoint
    records its file's layer. Files in the backend, api or *unknown* layers are
    searched; an unknown file is reported as unknown, never relabelled backend.
    Without `architecture`, every supported file is searched.
    """
    layer_by_path = (
        {item.path: item.layer for item in architecture.classifications} if architecture else {}
    )
    summary = ApiEndpointSummary()
    collected: list[ApiEndpoint] = []

    for module in modules:
        if module.language not in _SUPPORTED_LANGUAGES:
            continue
        relative_path = module.source_file.relative_path
        layer = layer_by_path.get(relative_path)
        if layer in _EXCLUDED_LAYERS:
            summary.files_excluded_by_layer += 1
            continue
        if module.parse_error is not None:
            continue
        summary.files_scanned += 1
        found, dynamic = detect_endpoints_in_module(module)
        summary.dynamic_paths_skipped += dynamic
        if layer is not None:
            found = [ApiEndpoint(**{**e.__dict__, "layer": layer}) for e in found]
        collected.extend(found)

    collected.sort(key=lambda e: (e.source_file, e.line_number, e.method, e.path))
    summary.endpoints = collected
    return summary
