"""
Project Architecture Intelligence — Phase 8C (part 1): frontend API calls.

Finds the HTTP calls a JavaScript/TypeScript frontend *makes*, by static
analysis only (nothing is executed, imported or contacted). It is the
consumer-side counterpart of `api_endpoints.py`; `api_matching.py` joins the
two.

Libraries and patterns
----------------------
* ``fetch(url)`` (GET), ``fetch(url, { method: "POST" })`` — also
  ``window.fetch`` / ``globalThis.fetch``.
* ``axios.get/post/put/patch/delete/head/options(url, ...)``,
  ``axios({ url, method })``, ``axios.request({ ... })`` and axios *instances*
  (``const api = axios.create({ baseURL: "/api" })`` then ``api.get("/u")``).
* ``xhr.open("GET", url)``.
* ``library = "unknown"`` (always LOW): a conventionally named client wrapper
  such as ``api.get("/users")`` / ``http.post("/users")`` whose implementation
  lives in another file.

How source is accessed
----------------------
Like Phase 8B it reads the pipeline's own `ParsedModule`: the JS/TS lexer's
token stream, in which comments, strings and templates are already separated
from code, so a call inside a comment or an ordinary string can never match.
No second scanner, no re-reading of files, no regular expression over raw
source (the only regular expression below tidies an already-extracted URL
string).

URLs and base URLs
------------------
A URL is accepted only when it can be reconstructed statically from:
string literals; template literals (each ``${expr}`` becomes the path
parameter ``{}``, so ``/users/${id}`` is ``/users/{}``); ``+`` concatenation;
same-file ``const`` string constants (``const API = "/api"``;
``fetch(API + "/users")``; ``fetch(`${API}/users`)``); and the very common
"environment override with a literal fallback"
(``const BASE = import.meta.env.VITE_API || "http://localhost:8000"``), which
resolves to the literal at MEDIUM confidence because the override could
differ. Anything else — a variable, a call, ``"/users/" + id`` — is *skipped
and counted* in ``dynamic_urls_skipped``, never guessed. The query string and
fragment are dropped. An absolute URL keeps only its path (no host is stored);
if the host is not local (localhost, 127.0.0.1, ...) the call is flagged
``external`` at LOW confidence because it may target a third-party service.

Confidence
----------
HIGH   — a literal URL in a standard client call (fetch, axios, XHR created in
         the file) made from a file the architecture identifies as frontend.
MEDIUM — the URL used a same-file constant / env fallback / axios ``baseURL``,
         or there is no frontend evidence for the file (unknown layer, or no
         architecture given), or the library is only identified by name.
LOW    — an unidentified client wrapper (``unknown``), or an external host.

Layers and server-side code
---------------------------
With Phase 8A architecture information, only frontend, shared and unknown
files are searched (backend, api, database, tests and configuration files are
not). Outside a clearly frontend file, a file that imports a server framework
or Node-only HTTP client (express, koa, node-fetch, fs, ...) is skipped as
server-side: ``axios.get`` in backend code is not a frontend call.

Known limitations
-----------------
Redirects, interceptors, proxies, GraphQL, websockets, ``$.ajax``/jQuery,
``ky``/``got``/``superagent``, ``fetch(request)`` with a ``Request`` object,
a non-literal ``method``, and URLs assembled at runtime are not resolved.
"""

from __future__ import annotations

import bisect
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable

from app.analyzer.architecture import (
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
    LAYER_API,
    LAYER_BACKEND,
    LAYER_CONFIGURATION,
    LAYER_DATABASE,
    LAYER_FRONTEND,
    LAYER_TESTS,
    ArchitectureSummary,
)
from app.analyzer.parser import ParsedModule

LIBRARY_FETCH = "fetch"
LIBRARY_AXIOS = "axios"
LIBRARY_XHR = "XMLHttpRequest"
LIBRARY_UNKNOWN = "unknown"

PARAMETER_PLACEHOLDER = "{}"

_CONFIDENCE_RANK = {CONFIDENCE_HIGH: 3, CONFIDENCE_MEDIUM: 2, CONFIDENCE_LOW: 1}
_CALL_LANGUAGES = frozenset({"javascript", "typescript"})

# Files in these layers are not searched for frontend API calls.
_EXCLUDED_LAYERS = frozenset({LAYER_BACKEND, LAYER_API, LAYER_DATABASE, LAYER_TESTS, LAYER_CONFIGURATION})

_HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})
_VERB_NAMES = frozenset({"get", "post", "put", "patch", "delete", "head", "options"})
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "[::1]"})
# Conventional names of API-client wrappers whose implementation is elsewhere.
_WRAPPER_RECEIVERS = frozenset({"api", "http", "client", "request", "apiclient", "httpclient"})
_GLOBAL_OBJECTS = frozenset({"window", "globalthis", "self", "global"})
# A file importing any of these is server-side (or a Node script), not a browser frontend.
_SERVER_SIDE_IMPORTS = frozenset(
    {
        "express", "koa", "koa-router", "@koa/router", "fastify", "restify", "hono", "@hapi/hapi",
        "@nestjs/common", "node-fetch", "got", "undici", "child_process", "fs", "fs/promises", "http",
        "https", "node:fs", "node:http", "node:https", "node:child_process",
    }
)

_IDENTIFIER = re.compile(r"[A-Za-z_$][\w$]*")


# --------------------------------------------------------------------------
# Result model
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ApiCall:
    """One HTTP call made by the frontend, as far as it can be read statically."""

    method: str  # GET, POST, PUT, PATCH, DELETE, HEAD or OPTIONS
    path: str  # URL path only: leading '/', no host, no query, parameters as `{}`
    source_file: str
    line_number: int
    language: str
    library: str  # fetch | axios | XMLHttpRequest | unknown
    confidence: str
    layer: str | None = None  # Phase 8A layer of the file; None when no architecture was given
    external: bool = False  # the URL was absolute and its host is not local

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "path": self.path,
            "source_file": self.source_file,
            "line_number": self.line_number,
            "language": self.language,
            "library": self.library,
            "confidence": self.confidence,
            "layer": self.layer,
            "external": self.external,
        }


@dataclass
class ApiCallSummary:
    """All frontend API calls detected in a project, plus what was (not) examined."""

    calls: list[ApiCall] = field(default_factory=list)
    files_scanned: int = 0
    files_excluded_by_layer: int = 0  # JS/TS files skipped because of their 8A layer
    files_skipped_server_side: int = 0  # JS/TS files that look like server-side code
    dynamic_urls_skipped: int = 0  # call-like expressions whose URL or method was not static

    @property
    def total_calls(self) -> int:
        return len(self.calls)

    def _count(self, attribute: str) -> dict[str, int]:
        counts = Counter(getattr(c, attribute) for c in self.calls)
        return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))

    @property
    def calls_by_method(self) -> dict[str, int]:
        return self._count("method")

    @property
    def calls_by_library(self) -> dict[str, int]:
        return self._count("library")

    @property
    def calls_by_language(self) -> dict[str, int]:
        return self._count("language")

    @property
    def calls_by_confidence(self) -> dict[str, int]:
        return self._count("confidence")

    def to_dict(self, include_calls: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "total_calls": self.total_calls,
            "calls_by_method": self.calls_by_method,
            "calls_by_library": self.calls_by_library,
            "calls_by_language": self.calls_by_language,
            "calls_by_confidence": self.calls_by_confidence,
            "files_scanned": self.files_scanned,
            "files_excluded_by_layer": self.files_excluded_by_layer,
            "files_skipped_server_side": self.files_skipped_server_side,
            "dynamic_urls_skipped": self.dynamic_urls_skipped,
        }
        if include_calls:
            data["calls"] = [c.to_dict() for c in self.calls]
        return data


# --------------------------------------------------------------------------
# Static URL evaluation over the token stream
# --------------------------------------------------------------------------


@dataclass
class _Eval:
    value: str  # URL text; `${expr}` already replaced by `{}`
    end: int  # index of the first token after the expression
    indirect: bool  # went through a same-file constant or an env-with-fallback


class _Resolver:
    """Evaluates the small string-expression language used for URLs."""

    def __init__(self, tree: Any) -> None:
        self.tree = tree
        self.texts, self.kinds, self.starts, self.ends = tree.texts, tree.kinds, tree.starts, tree.ends
        self.n = len(self.texts)
        self.newlines = [i for i, ch in enumerate(tree.clean) if ch == "\n"]
        self.consts: dict[str, str] = {}
        self._collect_constants()

    # -- helpers ---------------------------------------------------------
    def line_of(self, token: int) -> int:
        return bisect.bisect_left(self.newlines, self.starts[token]) + 1

    def _quoted(self, k: int) -> bool:
        return k < self.n and self.kinds[k] == "str" and bool(self.texts[k]) and self.texts[k][0] in "\"'"

    def _raw_string(self, k: int) -> str:
        return self.tree.no_comments[self.starts[k] + 1 : self.ends[k] - 1]

    # -- expression grammar ----------------------------------------------
    def _template(self, k: int) -> _Eval | None:
        close = next((j for j in range(k + 1, self.n) if self.kinds[j] == "str" and self.texts[j] == "`"), None)
        if close is None:
            return None
        raw = self.tree.no_comments[self.starts[k] + 1 : self.starts[close]]
        pieces: list[str] = []
        indirect = False
        index = 0
        while index < len(raw):
            if raw.startswith("${", index):
                depth, scan = 1, index + 2
                while scan < len(raw) and depth:
                    depth += {"{": 1, "}": -1}.get(raw[scan], 0)
                    scan += 1
                if depth:
                    return None  # unbalanced: a nested template literal we do not follow
                inner = raw[index + 2 : scan - 1].strip()
                if "`" in inner:
                    return None
                if _IDENTIFIER.fullmatch(inner) and inner in self.consts:
                    pieces.append(self.consts[inner])
                    indirect = True
                else:
                    pieces.append(PARAMETER_PLACEHOLDER)
                index = scan
            else:
                pieces.append(raw[index])
                index += 1
        return _Eval("".join(pieces), close + 1, indirect)

    def _operand(self, k: int) -> _Eval | None:
        if k >= self.n:
            return None
        if self._quoted(k):
            return _Eval(self._raw_string(k), k + 1, False)
        if self.kinds[k] == "str" and self.texts[k] == "`":
            return self._template(k)
        if self.kinds[k] == "id" and self.texts[k] in self.consts:
            following = self.texts[k + 1] if k + 1 < self.n else ""
            if following in (".", "?.", "(", "["):
                return None  # a property or call on the constant, not the constant itself
            return _Eval(self.consts[self.texts[k]], k + 1, True)
        return None

    def _term(self, k: int) -> _Eval | None:
        known = self._operand(k)
        if known is not None:
            return known
        # `env.VAR || "literal"` / `a.b ?? "literal"`: an unknown member chain
        # with a literal fallback resolves to the fallback (flagged indirect).
        if k < self.n and self.kinds[k] == "id":
            j = k + 1
            while j + 1 < self.n and self.texts[j] in (".", "?.") and self.kinds[j + 1] == "id":
                j += 2
            if j + 1 < self.n and self.texts[j] in ("||", "??"):
                fallback = self._operand(j + 1)
                if fallback is not None:
                    return _Eval(fallback.value, fallback.end, True)
        return None

    def evaluate(self, k: int) -> _Eval | None:
        term = self._term(k)
        if term is None:
            return None
        value, end, indirect = term.value, term.end, term.indirect
        while end < self.n and self.kinds[end] == "punct" and self.texts[end] == "+":
            nxt = self._term(end + 1)
            if nxt is None:
                return None
            value, end, indirect = value + nxt.value, nxt.end, indirect or nxt.indirect
        return _Eval(value, end, indirect)

    def _collect_constants(self) -> None:
        ambiguous: set[str] = set()
        for i in range(self.n - 3):
            if self.texts[i] != "const" or self.kinds[i + 1] != "id":
                continue
            name = self.texts[i + 1]
            j = i + 2
            if j < self.n and self.texts[j] == ":":  # TypeScript annotation
                while j < self.n and self.texts[j] not in ("=", ";"):
                    j += 1
            if j >= self.n or self.texts[j] != "=":
                continue
            result = self.evaluate(j + 1)
            if result is None:
                continue
            ends_statement = result.end >= self.n or self.texts[result.end] in (";", ",") or (
                self.line_of(result.end) > self.line_of(result.end - 1)
            )
            if not ends_statement:
                continue
            if name in self.consts and self.consts[name] != result.value:
                ambiguous.add(name)
            self.consts[name] = result.value
        for name in ambiguous:
            self.consts.pop(name, None)  # defined twice with different values: do not guess

    # -- call arguments / object literals ---------------------------------
    def arguments(self, open_index: int) -> tuple[list[tuple[int, int]], int]:
        arguments: list[tuple[int, int]] = []
        depth = 0
        start = open_index + 1
        for index in range(open_index, self.n):
            if self.kinds[index] != "punct":
                continue
            text = self.texts[index]
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
        return arguments, self.n

    def object_entries(self, start: int, end: int) -> list[tuple[str | None, int | None, int | None]] | None:
        """Entries of the object literal in [start, end): (key, value_start, value_end).
        key None marks a spread; value_start None marks a shorthand/method entry."""
        if not (end - start >= 2 and self.texts[start] == "{" and self.texts[end - 1] == "}"):
            return None
        pieces: list[tuple[int, int]] = []
        depth, piece_start = 0, start + 1
        for index in range(start + 1, end - 1):
            if self.kinds[index] != "punct":
                continue
            text = self.texts[index]
            if text in ("(", "[", "{"):
                depth += 1
            elif text in (")", "]", "}"):
                depth -= 1
            elif text == "," and depth == 0:
                pieces.append((piece_start, index))
                piece_start = index + 1
        if piece_start < end - 1:
            pieces.append((piece_start, end - 1))
        entries: list[tuple[str | None, int | None, int | None]] = []
        for a, b in pieces:
            if self.texts[a] == "...":
                entries.append((None, None, None))
                continue
            key = self._raw_string(a) if self._quoted(a) else (self.texts[a] if self.kinds[a] == "id" else "")
            if a + 1 < b and self.texts[a + 1] == ":":
                entries.append((key, a + 2, b))
            else:
                entries.append((key, None, None))
        return entries

    def whole_expression(self, start: int, end: int) -> _Eval | None:
        """Evaluate [start, end) as exactly one URL expression."""
        result = self.evaluate(start)
        return result if result is not None and result.end == end else None


def _finalize_url(value: str) -> tuple[str | None, bool]:
    """
    -> (normalized path or None when not a usable API path, external?).
    Strips scheme/host (never stored), query and fragment; collapses slashes.
    """
    value = value.strip()
    external = False
    if value.startswith(("http://", "https://")) or value.startswith("//"):
        remainder = value.split("//", 1)[1]
        authority, slash, rest = remainder.partition("/")
        authority = authority.split("?")[0].split("#")[0]
        if PARAMETER_PLACEHOLDER in authority:
            return None, False  # the host itself is a variable
        host = authority.rsplit("@", 1)[-1]
        host = host.rsplit(":", 1)[0] if not host.startswith("[") else host.split("]")[0] + "]"
        external = host.lower() not in _LOCAL_HOSTS
        value = ("/" + rest) if slash else "/"
    if not value.startswith("/"):
        return None, False  # relative or placeholder-led: the base is not known
    value = value.split("#")[0].split("?")[0]
    segments = [s for s in value.split("/") if s]
    return "/" + "/".join(segments), external


# --------------------------------------------------------------------------
# Detection
# --------------------------------------------------------------------------


@dataclass
class _Instance:
    base: str | None  # evaluated baseURL ("" when none); None when present but not static
    indirect: bool


def _cap(confidence: str, ceiling: str) -> str:
    return confidence if _CONFIDENCE_RANK[confidence] <= _CONFIDENCE_RANK[ceiling] else ceiling


def detect_calls_in_module(module: ParsedModule, layer: str | None = None) -> tuple[list[ApiCall], int]:
    """
    Frontend API calls in one parsed JS/TS module, and how many call-like
    expressions were skipped because their URL or method is not static.
    `layer` is the module's Phase 8A layer (None when unknown/unsupplied); it
    only moves confidence and is recorded on each call. Never raises.
    """
    try:
        return _detect(module, layer)
    except Exception:  # a heuristic detector must never take the analysis down
        return [], 0


def _detect(module: ParsedModule, layer: str | None) -> tuple[list[ApiCall], int]:
    tree = module.raw_tree
    if module.language not in _CALL_LANGUAGES or module.parse_error is not None:
        return [], 0
    if not hasattr(tree, "texts") or not hasattr(tree, "no_comments"):
        return [], 0

    resolver = _Resolver(tree)
    texts, kinds = resolver.texts, resolver.kinds
    n = resolver.n
    imports = set(tree.imports)
    path_ = module.source_file.relative_path
    layer_is_frontend = layer == LAYER_FRONTEND
    calls: list[ApiCall] = []
    skipped = 0

    # axios instances:  const api = axios.create({ baseURL: ... })
    instances: dict[str, _Instance] = {}
    for i in range(n - 6):
        if texts[i] != "=" or kinds[i] != "punct":
            continue
        if not (texts[i + 1 : i + 4] == ["axios", ".", "create"] and texts[i + 4] == "("):
            continue
        m = i - 1
        if m >= 2 and texts[m - 1] == ":":
            m -= 2
        if m < 0 or kinds[m] != "id":
            continue
        arguments, _ = resolver.arguments(i + 4)
        base: str | None = ""
        indirect = False
        if arguments:
            entries = resolver.object_entries(*arguments[0])
            if entries is None:
                base = None
            else:
                for key, v_start, v_end in entries:
                    if key == "baseURL":
                        evaluated = resolver.whole_expression(v_start, v_end) if v_start is not None else None
                        base = evaluated.value if evaluated else None
                        indirect = bool(evaluated and evaluated.indirect)
        instances[texts[m]] = _Instance(base, indirect)

    xhr_receivers = {
        texts[j - 1]
        for j in range(1, n - 3)
        if texts[j] == "=" and texts[j + 1 : j + 3] == ["new", "XMLHttpRequest"] and kinds[j - 1] == "id"
    }
    express_receivers = {
        texts[j - 1]
        for j in range(1, n - 2)
        if texts[j] == "=" and texts[j + 1] in ("express", "Router") and kinds[j - 1] == "id"
    }

    def emit(method: str, url: _Eval | None, base: str | None, base_indirect: bool, token: int,
             library: str, base_confidence: str) -> None:
        nonlocal skipped
        if url is None or base is None:
            skipped += 1
            return
        value = url.value
        absolute = value.strip().startswith(("http://", "https://", "//"))
        if not absolute and base:
            value = base.rstrip("/") + "/" + value.lstrip("/")
        path, external = _finalize_url(value)
        if path is None:
            skipped += 1
            return
        confidence = base_confidence
        if url.indirect or (base_indirect and not absolute) or bool(base and not absolute):
            confidence = _cap(confidence, CONFIDENCE_MEDIUM)
        if not layer_is_frontend:
            confidence = _cap(confidence, CONFIDENCE_MEDIUM)  # no frontend evidence for the file
        if external:
            confidence = CONFIDENCE_LOW
        calls.append(
            ApiCall(
                method=method, path=path, source_file=path_, line_number=resolver.line_of(token),
                language=module.language, library=library, confidence=confidence, layer=layer,
                external=external,
            )
        )

    def config_object(range_: tuple[int, int], base: str | None, base_indirect: bool, token: int,
                      library: str, confidence: str) -> None:
        """`axios({ url, method, baseURL })` / `axios.request({...})`."""
        nonlocal skipped
        entries = resolver.object_entries(*range_)
        if entries is None or any(key is None for key, _, _ in entries):
            skipped += 1
            return
        fields: dict[str, tuple[int | None, int | None]] = {k: (a, b) for k, a, b in entries}
        if "url" not in fields or fields["url"][0] is None:
            skipped += 1
            return
        url = resolver.whole_expression(*fields["url"])  # type: ignore[arg-type]
        method = "GET"
        if "method" in fields:
            a, b = fields["method"]
            if a is None or b is None or b - a != 1 or not resolver._quoted(a):
                skipped += 1
                return
            method = resolver._raw_string(a).upper()
            if method not in _HTTP_METHODS:
                skipped += 1
                return
        if "baseURL" in fields:
            a, b = fields["baseURL"]
            evaluated = resolver.whole_expression(a, b) if a is not None and b is not None else None
            if evaluated is None:
                skipped += 1
                return
            base, base_indirect = evaluated.value, evaluated.indirect
        emit(method, url, base, base_indirect, token, library, confidence)

    axios_confidence = CONFIDENCE_HIGH if "axios" in imports else CONFIDENCE_MEDIUM

    for i in range(n):
        if kinds[i] != "id":
            continue
        text = texts[i]
        previous = texts[i - 1] if i > 0 else ""

        # ---------------- fetch(url, options?) --------------------------------
        if text == "fetch" and i + 1 < n and texts[i + 1] == "(":
            if previous == ".":
                if not (i >= 2 and texts[i - 2].lower() in _GLOBAL_OBJECTS):
                    continue  # `this.fetch(...)`, `cache.fetch(...)`: not the fetch API
            elif previous in ("function", "async", "get", "set", "static", "const", "let", "var"):
                continue  # a definition named fetch
            arguments, close = resolver.arguments(i + 1)
            if close + 1 < n and texts[close + 1] == "{":
                continue  # `fetch(url) { ... }`: a method definition
            if not arguments:
                continue
            url = resolver.whole_expression(*arguments[0])
            method = "GET"
            if len(arguments) >= 2:
                entries = resolver.object_entries(*arguments[1])
                if entries is None or any(key is None for key, _, _ in entries):
                    skipped += 1  # options are not an object literal (or contain a spread)
                    continue
                for key, v_start, v_end in entries:
                    if key == "method":
                        if v_start is None or v_end - v_start != 1 or not resolver._quoted(v_start):
                            method = ""
                        else:
                            method = resolver._raw_string(v_start).upper()
                if method not in _HTTP_METHODS:
                    skipped += 1
                    continue
            emit(method, url, "", False, i, LIBRARY_FETCH, CONFIDENCE_HIGH)
            continue

        # ---------------- axios / instances / wrappers ------------------------
        if i + 1 < n and texts[i + 1] == "(" and text in ("axios", *instances) and previous != ".":
            arguments, _ = resolver.arguments(i + 1)  # `axios({...})` / `api({...})`
            if arguments and texts[arguments[0][0]] == "{":
                instance = instances.get(text)
                config_object(arguments[0], instance.base if instance else "", bool(instance and instance.indirect),
                              i, LIBRARY_AXIOS, axios_confidence)
            continue

        if previous == "." and i >= 2 and kinds[i - 2] == "id" and i + 1 < n and texts[i + 1] == "(":
            receiver = texts[i - 2]
            if i >= 3 and texts[i - 3] == "." and not (i >= 4 and texts[i - 4] == "this"):
                continue  # member chains are not tracked (`this.http.get(...)` is allowed)
            is_axios = receiver == "axios"
            instance = instances.get(receiver)

            if (is_axios or instance is not None) and text in _VERB_NAMES | {"request"}:
                arguments, _ = resolver.arguments(i + 1)
                if not arguments:
                    continue
                base = "" if is_axios else instance.base  # type: ignore[union-attr]
                base_indirect = bool(instance and instance.indirect)
                if text == "request":
                    if texts[arguments[0][0]] == "{":
                        config_object(arguments[0], base, base_indirect, i, LIBRARY_AXIOS, axios_confidence)
                    continue
                emit(text.upper(), resolver.whole_expression(*arguments[0]), base, base_indirect, i,
                     LIBRARY_AXIOS, axios_confidence)
                continue

            # xhr.open("GET", url)
            if text == "open":
                arguments, _ = resolver.arguments(i + 1)
                if len(arguments) >= 2 and arguments[0][1] - arguments[0][0] == 1 and resolver._quoted(arguments[0][0]):
                    verb = resolver._raw_string(arguments[0][0]).upper()
                    if verb in _HTTP_METHODS:
                        confidence = CONFIDENCE_HIGH if receiver in xhr_receivers else CONFIDENCE_MEDIUM
                        emit(verb, resolver.whole_expression(*arguments[1]), "", False, i, LIBRARY_XHR, confidence)
                continue

            # api.get("/users"): an unidentified client wrapper, library unknown (LOW)
            if (
                text in _VERB_NAMES
                and receiver.lower() in _WRAPPER_RECEIVERS
                and receiver not in express_receivers
                and receiver != "axios"
            ):
                arguments, _ = resolver.arguments(i + 1)
                if not arguments:
                    continue
                url = resolver.whole_expression(*arguments[0])
                if url is not None and _finalize_url(url.value)[0] is not None:
                    emit(text.upper(), url, "", False, i, LIBRARY_UNKNOWN, CONFIDENCE_LOW)
                continue

    return calls, skipped


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


def detect_api_calls(
    modules: Iterable[ParsedModule],
    architecture: ArchitectureSummary | None = None,
) -> ApiCallSummary:
    """
    Detect the HTTP calls made by the frontend across `modules` (typically
    ``analyze_project(root, retain_parsed=True).parsed_modules``).

    With a Phase 8A `architecture`, files in the backend, api, database, tests
    and configuration layers are not searched; frontend, shared and unknown
    files are. Without it every JS/TS file is searched. Outside a clearly
    frontend file, a file that imports a server framework or a Node-only HTTP
    client is skipped as server-side code.
    """
    layer_by_path = (
        {item.path: item.layer for item in architecture.classifications} if architecture else {}
    )
    summary = ApiCallSummary()
    collected: list[ApiCall] = []

    for module in modules:
        if module.language not in _CALL_LANGUAGES:
            continue
        layer = layer_by_path.get(module.source_file.relative_path)
        if layer in _EXCLUDED_LAYERS:
            summary.files_excluded_by_layer += 1
            continue
        if module.parse_error is not None:
            continue
        tree = module.raw_tree
        if layer != LAYER_FRONTEND and set(getattr(tree, "imports", ())) & _SERVER_SIDE_IMPORTS:
            summary.files_skipped_server_side += 1
            continue
        summary.files_scanned += 1
        found, skipped = detect_calls_in_module(module, layer)
        summary.dynamic_urls_skipped += skipped
        collected.extend(found)

    collected.sort(key=lambda c: (c.source_file, c.line_number, c.method, c.path))
    summary.calls = collected
    return summary
