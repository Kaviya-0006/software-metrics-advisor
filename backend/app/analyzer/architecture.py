"""
Project Architecture Intelligence — Phase 8A foundation.

A real-world upload is rarely "a Java project" or "a TypeScript project": it
is a full-stack system whose frontend, backend, tests and configuration live
in different directories and, often, in different languages. This module
turns the flat list of analyzed files into an *architecture view*: which
layers the project appears to have, which languages each layer is written in,
how large each layer is, and how much confidence the evidence deserves.

Design rules
------------
* **No second scanner.** Languages are never detected here. The input is the
  output of the existing pipeline (`ModuleResult` objects, their stored
  `to_dict()` form, or the scanner's own `SourceFile` records), each of which
  already carries the language chosen by `scanner.LANGUAGE_BY_EXT`. This
  module only looks at *where* a file lives and *what it is called*.
* **Conservative.** A file is only assigned to a layer when there is
  evidence, and the strength of that evidence is recorded. Weak or
  contradictory evidence yields a lower confidence, or no classification at
  all (`unknown`) — never a confident guess.
* **A partition.** Every file is assigned to exactly one layer, so
  `files_by_layer` always sums to `total_files` and the per-layer LOC sums to
  the project's `total_loc` (same definition: `loc.total`).
* **Additive and pure.** Nothing here touches the metric pipeline, scoring,
  risk, the database or the API response. Calling it has no side effects.

Layers
------
``frontend``, ``backend``, ``api``, ``database``, ``shared``, ``tests``,
``configuration`` and ``unknown``. ``api`` is a backend-side layer; it is
reported separately because it is the input to the later API-surface analysis.

Evidence and confidence (per file)
----------------------------------
Three kinds of evidence are used, strongest first:

1. **Strong directory name** — an exact path segment such as ``frontend``,
   ``backend``, ``tests`` (matched as a whole segment, case-insensitively,
   never as a substring). Confidence HIGH when the file's language is
   consistent with the layer, MEDIUM when it is not (for example a Python file
   inside ``frontend/``).
2. **Weak directory name** — an ambiguous segment such as ``web``, ``ui``,
   ``app``, ``services``, ``api``, ``lib``. Only counts when the language is
   consistent with the layer, and is at most MEDIUM.
3. **Filename convention** — ``*.test.ts``, ``test_*.py``, ``*Test.java``,
   ``*.config.js``, ``*Controller.java``, ``*.tsx`` ... Always LOW on its own.

A file with none of these is ``unknown``.

Layer and project confidence are derived from the per-file levels (see
`ArchitectureSummary` and the thresholds below).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping
import re

# --------------------------------------------------------------------------
# Vocabulary
# --------------------------------------------------------------------------

LAYER_FRONTEND = "frontend"
LAYER_BACKEND = "backend"
LAYER_API = "api"
LAYER_DATABASE = "database"
LAYER_SHARED = "shared"
LAYER_TESTS = "tests"
LAYER_CONFIGURATION = "configuration"
LAYER_UNKNOWN = "unknown"

# Display / serialization order of layers.
LAYER_ORDER: tuple[str, ...] = (
    LAYER_FRONTEND,
    LAYER_BACKEND,
    LAYER_API,
    LAYER_DATABASE,
    LAYER_SHARED,
    LAYER_TESTS,
    LAYER_CONFIGURATION,
    LAYER_UNKNOWN,
)

CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"
_CONFIDENCE_SCORE = {CONFIDENCE_HIGH: 3, CONFIDENCE_MEDIUM: 2, CONFIDENCE_LOW: 1}

# Aggregation thresholds (documented in docs/architecture.md).
LAYER_HIGH_MEAN = 2.6  # mean per-file score at/above which a layer is HIGH
LAYER_MEDIUM_MEAN = 1.8  # ... MEDIUM (below it: LOW)
PROJECT_HIGH_COVERAGE = 0.75  # share of files classified needed for a HIGH project
PROJECT_MEDIUM_COVERAGE = 0.40  # ... for a MEDIUM project

# Languages (names exactly as the scanner reports them) that are consistent
# with each side of a typical system.
_WEB_LANGUAGES = frozenset({"javascript", "typescript"})
_SERVER_LANGUAGES = frozenset({"python", "java", "c", "cpp"})

# Whole-segment directory names that strongly indicate a layer.
_STRONG_DIRS: dict[str, frozenset[str]] = {
    LAYER_FRONTEND: frozenset({"frontend", "front-end", "client"}),
    LAYER_BACKEND: frozenset({"backend", "back-end", "server"}),
    LAYER_DATABASE: frozenset({"db", "database", "migrations", "migration"}),
    LAYER_CONFIGURATION: frozenset({"config", "configs", "configuration", "conf", "settings"}),
    LAYER_SHARED: frozenset({"shared", "common"}),
    LAYER_TESTS: frozenset({"test", "tests", "__tests__", "spec", "specs", "e2e"}),
}

# Ambiguous directory names: they hint at a layer but are routinely used for
# something else (`app/` in Django or Next.js, `api/` for a client SDK, ...).
_WEAK_DIRS: dict[str, frozenset[str]] = {
    LAYER_FRONTEND: frozenset({"web", "webapp", "ui", "app"}),
    LAYER_BACKEND: frozenset({"services", "service"}),
    LAYER_API: frozenset({"api", "apis", "routes", "routers", "endpoints", "controllers"}),
    LAYER_DATABASE: frozenset({"repositories", "repository", "dao"}),
    LAYER_SHARED: frozenset({"lib", "libs", "utils", "util", "helpers"}),
}

_SIDE_LAYERS = frozenset({LAYER_FRONTEND, LAYER_BACKEND})

# Priority when several *filename* conventions match the same file.
_NAME_PRIORITY = (LAYER_API, LAYER_DATABASE, LAYER_CONFIGURATION, LAYER_FRONTEND)

_CONFIG_STEMS = frozenset({"config", "configuration", "settings", "conf"})
_ROUTE_STEMS = frozenset({"routes", "router", "routers", "endpoints"})
_CONFIG_FILE_RE = re.compile(r".+\.config\.[a-z0-9]+$")
_RC_FILE_RE = re.compile(r"^\.[a-z0-9]+rc(\.[a-z0-9]+)?$")


# --------------------------------------------------------------------------
# Result model
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class FileClassification:
    """The layer assigned to one file, with the evidence that justified it."""

    path: str
    language: str
    loc: int
    layer: str
    confidence: str
    evidence: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "language": self.language,
            "loc": self.loc,
            "layer": self.layer,
            "confidence": self.confidence,
            "evidence": list(self.evidence),
        }


@dataclass
class LayerSummary:
    """Aggregate view of one layer."""

    layer: str
    files: int
    loc: int
    languages: dict[str, int]  # language -> number of files
    confidence: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "languages": dict(self.languages),
            "files": self.files,
            "loc": self.loc,
            "confidence": self.confidence,
        }


@dataclass
class ArchitectureSummary:
    """
    Architecture view of one analyzed project.

    `layers` only contains layers that received at least one file; files with
    no evidence are collected under ``unknown`` so that totals always
    reconcile. `detected_layers` lists the real layers (never ``unknown``).

    Confidence
    ----------
    * A *layer's* confidence is the mean of its files' confidence scores
      (HIGH=3, MEDIUM=2, LOW=1): HIGH at >= 2.6, MEDIUM at >= 1.8, else LOW.
    * The *project's* confidence also accounts for how much of the project
      could be classified at all. With `m` the mean score over classified
      files and `c` the share of files that are classified:
      HIGH if m >= 2.6 and c >= 0.75; MEDIUM if m >= 1.8 and c >= 0.40;
      otherwise LOW — including when nothing could be classified.
    """

    total_files: int
    total_loc: int
    confidence: str
    layers: dict[str, LayerSummary]
    languages: dict[str, int]
    classifications: list[FileClassification] = field(default_factory=list)

    @property
    def detected_layers(self) -> list[str]:
        return [layer for layer in LAYER_ORDER if layer != LAYER_UNKNOWN and layer in self.layers]

    @property
    def languages_by_layer(self) -> dict[str, dict[str, int]]:
        return {layer: dict(summary.languages) for layer, summary in self.layers.items()}

    @property
    def files_by_layer(self) -> dict[str, int]:
        return {layer: summary.files for layer, summary in self.layers.items()}

    @property
    def is_multi_layer(self) -> bool:
        """True when at least two distinct real layers were detected."""
        return len(self.detected_layers) >= 2

    def layer_of(self, path: str) -> str | None:
        """Layer assigned to `path` (None if the path was not analyzed)."""
        for item in self.classifications:
            if item.path == path:
                return item.layer
        return None

    def to_dict(self, include_files: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "layers": {layer: self.layers[layer].to_dict() for layer in LAYER_ORDER if layer in self.layers},
            "detected_layers": self.detected_layers,
            "total_files": self.total_files,
            "total_loc": self.total_loc,
            "languages": dict(self.languages),
            "confidence": self.confidence,
        }
        if include_files:
            data["files"] = [item.to_dict() for item in self.classifications]
        return data


# --------------------------------------------------------------------------
# Per-file classification
# --------------------------------------------------------------------------


@dataclass
class _Candidate:
    layer: str
    tier: str  # "strong" | "weak" | "name"
    depth: int  # index of the directory segment; -1 for filename evidence
    confidence: str
    reason: str


def _split_path(path: str) -> tuple[list[str], str]:
    """Normalize separators and split into (directory segments, file name)."""
    parts = [p for p in path.replace("\\", "/").split("/") if p and p != "."]
    if not parts:
        return [], ""
    return parts[:-1], parts[-1]


def _strong_candidate(layer: str, depth: int, segment: str, language: str) -> _Candidate:
    confidence = CONFIDENCE_HIGH
    note = f"directory '{segment}' strongly indicates {layer}"
    if layer == LAYER_FRONTEND and language not in _WEB_LANGUAGES:
        # A frontend folder holding Python/Java/C/C++: partial evidence only.
        confidence = CONFIDENCE_MEDIUM
        note += f", but {language} is not a typical frontend language"
    return _Candidate(layer, "strong", depth, confidence, note)


def _weak_candidate(layer: str, depth: int, segment: str, language: str) -> _Candidate | None:
    note = f"directory '{segment}' weakly suggests {layer}"
    if layer == LAYER_FRONTEND:
        if language in _WEB_LANGUAGES:
            return _Candidate(layer, "weak", depth, CONFIDENCE_MEDIUM, f"{note} ({language} supports it)")
        return None  # `app/main.py` is not evidence of a frontend
    if layer in (LAYER_BACKEND, LAYER_API):
        if language in _SERVER_LANGUAGES:
            return _Candidate(layer, "weak", depth, CONFIDENCE_MEDIUM, f"{note} ({language} supports it)")
        if language in _WEB_LANGUAGES:
            # Could equally be client-side code (`services/`, `api/` in a SPA).
            return _Candidate(layer, "weak", depth, CONFIDENCE_LOW, f"{note} (ambiguous for {language})")
        return None
    return _Candidate(layer, "weak", depth, CONFIDENCE_LOW, note)


def _name_candidates(file_name: str) -> list[_Candidate]:
    """Layers suggested by the file name alone (always LOW confidence)."""
    lowered = file_name.lower()
    stem = file_name.rsplit(".", 1)[0] if "." in file_name.lstrip(".") else file_name
    stem_lower = stem.lower()
    ext = lowered.rsplit(".", 1)[-1] if "." in lowered.lstrip(".") else ""
    found: list[_Candidate] = []

    def add(layer: str, reason: str) -> None:
        found.append(_Candidate(layer, "name", -1, CONFIDENCE_LOW, reason))

    if (
        stem_lower.startswith("test_")
        or stem_lower.endswith("_test")
        or stem_lower.endswith((".test", ".spec"))
        or stem_lower == "conftest"
        or (stem.endswith(("Test", "Tests")) and len(stem) > 5)
    ):
        add(LAYER_TESTS, f"file name '{file_name}' follows a test naming convention")
    if _CONFIG_FILE_RE.match(lowered) or _RC_FILE_RE.match(lowered) or stem_lower in _CONFIG_STEMS:
        add(LAYER_CONFIGURATION, f"file name '{file_name}' looks like a configuration file")
    if (stem.endswith("Controller") and len(stem) > 10) or stem_lower in _ROUTE_STEMS:
        add(LAYER_API, f"file name '{file_name}' looks like a controller/router")
    if stem.endswith(("Repository", "Dao", "DAO")) and len(stem) > 3:
        add(LAYER_DATABASE, f"file name '{file_name}' looks like a data-access class")
    if ext in ("jsx", "tsx"):
        add(LAYER_FRONTEND, f"'.{ext}' files contain UI markup")
    return found


def _innermost(candidates: list[_Candidate]) -> _Candidate:
    return max(candidates, key=lambda c: c.depth)


def classify_file(path: str, language: str, loc: int = 0) -> FileClassification:
    """
    Assign one file to a layer from its path, file name and (already
    detected) language. Pure function; see the module docstring for the rules.
    """
    directories, file_name = _split_path(path)
    candidates: list[_Candidate] = []

    for depth, segment in enumerate(directories):
        lowered = segment.lower()
        for layer, names in _STRONG_DIRS.items():
            if lowered in names:
                candidates.append(_strong_candidate(layer, depth, segment, language))
        for layer, names in _WEAK_DIRS.items():
            if lowered in names:
                weak = _weak_candidate(layer, depth, segment, language)
                if weak is not None:
                    candidates.append(weak)

    name_candidates = _name_candidates(file_name) if file_name else []
    strong = [c for c in candidates if c.tier == "strong"]
    weak = [c for c in candidates if c.tier == "weak"]

    def result(chosen: _Candidate, confidence: str | None = None, extra: str | None = None) -> FileClassification:
        evidence = [chosen.reason]
        if extra:
            evidence.append(extra)
        return FileClassification(
            path=path,
            language=language,
            loc=loc,
            layer=chosen.layer,
            confidence=confidence or chosen.confidence,
            evidence=tuple(evidence),
        )

    # 1. Tests: a strong test directory always wins (tests live inside both
    #    frontend and backend trees); a test *file name* wins too, but with
    #    reduced confidence because the surrounding directory may disagree.
    strong_tests = [c for c in strong if c.layer == LAYER_TESTS]
    if strong_tests:
        return result(_innermost(strong_tests), CONFIDENCE_HIGH)
    test_names = [c for c in name_candidates if c.layer == LAYER_TESTS]
    if test_names:
        if candidates:
            return result(test_names[0], CONFIDENCE_MEDIUM, "a directory name suggests a different layer")
        return result(test_names[0], CONFIDENCE_LOW)

    # 2. Strong directory evidence: the innermost directory decides, so
    #    `backend/config/x.py` is configuration and `frontend/shared/x.ts`
    #    is shared. Both a frontend and a backend directory on the same path
    #    is contradictory evidence and caps the confidence.
    if strong:
        chosen = _innermost(strong)
        confidence = chosen.confidence
        extra = None
        sides = {c.layer for c in strong if c.layer in _SIDE_LAYERS}
        if sides == _SIDE_LAYERS:
            confidence = min(confidence, CONFIDENCE_MEDIUM, key=_CONFIDENCE_SCORE.get)
            extra = "path contains both a frontend and a backend directory"
        return result(chosen, confidence, extra)

    # 3. Weak directory evidence: take the most trustworthy, then innermost.
    if weak:
        best = max(weak, key=lambda c: (_CONFIDENCE_SCORE[c.confidence], c.depth))
        return result(best)

    # 4. Filename evidence only.
    for layer in _NAME_PRIORITY:
        for candidate in name_candidates:
            if candidate.layer == layer:
                return result(candidate)

    return FileClassification(
        path=path,
        language=language,
        loc=loc,
        layer=LAYER_UNKNOWN,
        confidence=CONFIDENCE_LOW,
        evidence=("no conventional directory or file-name evidence",),
    )


# --------------------------------------------------------------------------
# Whole-project analysis
# --------------------------------------------------------------------------


def _coerce(entry: Any) -> tuple[str, str, int]:
    """
    Read (path, language, loc) from any of the project's file records:
    a `ModuleResult`, its stored `to_dict()` form, or the scanner's
    `SourceFile` (which has no LOC; it counts as 0).
    """
    if isinstance(entry, Mapping):
        path = entry.get("file_path") or entry.get("relative_path") or entry.get("path")
        language = entry.get("language")
        loc: Any = entry.get("loc", 0)
    else:
        path = (
            getattr(entry, "file_path", None)
            or getattr(entry, "relative_path", None)
            or getattr(entry, "path", None)
        )
        language = getattr(entry, "language", None)
        loc = getattr(entry, "loc", 0)

    if isinstance(loc, Mapping):
        loc = loc.get("total", 0)
    elif loc is not None and not isinstance(loc, (int, float)):
        loc = getattr(loc, "total", 0)

    if not path or not language:
        raise ValueError(f"cannot classify {entry!r}: it needs a path and a language")
    return str(path), str(language), int(loc or 0)


def _level_from_mean(mean: float) -> str:
    if mean >= LAYER_HIGH_MEAN:
        return CONFIDENCE_HIGH
    if mean >= LAYER_MEDIUM_MEAN:
        return CONFIDENCE_MEDIUM
    return CONFIDENCE_LOW


def _project_confidence(classified: list[FileClassification], total_files: int) -> str:
    if not classified or total_files == 0:
        return CONFIDENCE_LOW
    mean = sum(_CONFIDENCE_SCORE[c.confidence] for c in classified) / len(classified)
    coverage = len(classified) / total_files
    if mean >= LAYER_HIGH_MEAN and coverage >= PROJECT_HIGH_COVERAGE:
        return CONFIDENCE_HIGH
    if mean >= LAYER_MEDIUM_MEAN and coverage >= PROJECT_MEDIUM_COVERAGE:
        return CONFIDENCE_MEDIUM
    return CONFIDENCE_LOW


def analyze_architecture(files: Iterable[Any]) -> ArchitectureSummary:
    """
    Build the architecture view of a project from its analyzed files.

    `files` may contain `ModuleResult` objects (``analysis.modules``), their
    ``to_dict()`` form (as stored in the database), or the scanner's
    `SourceFile` records. The result does not depend on the order of `files`.
    """
    classifications = [classify_file(*_coerce(entry)) for entry in files]
    classifications.sort(key=lambda c: c.path)

    grouped: dict[str, list[FileClassification]] = {}
    for item in classifications:
        grouped.setdefault(item.layer, []).append(item)

    layers: dict[str, LayerSummary] = {}
    for layer in LAYER_ORDER:
        members = grouped.get(layer)
        if not members:
            continue
        counts = Counter(m.language for m in members)
        if layer == LAYER_UNKNOWN:
            confidence = CONFIDENCE_LOW
        else:
            confidence = _level_from_mean(
                sum(_CONFIDENCE_SCORE[m.confidence] for m in members) / len(members)
            )
        layers[layer] = LayerSummary(
            layer=layer,
            files=len(members),
            loc=sum(m.loc for m in members),
            languages=dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))),
            confidence=confidence,
        )

    classified = [c for c in classifications if c.layer != LAYER_UNKNOWN]
    overall = Counter(c.language for c in classifications)
    return ArchitectureSummary(
        total_files=len(classifications),
        total_loc=sum(c.loc for c in classifications),
        confidence=_project_confidence(classified, len(classifications)),
        layers=layers,
        languages=dict(sorted(overall.items(), key=lambda kv: (-kv[1], kv[0]))),
        classifications=classifications,
    )
