"""
Project identity (Phase 9A): deciding whether an uploaded ZIP is a new project
or a new version of an existing one.

Pure functions, no database and no I/O. The inputs are things the existing
pipeline already produced: the ZIP's file name, the relative paths of the
discovered source files and their languages.

Principles
----------
* **A file name alone never merges projects.** Matching a name is one signal;
  an automatic merge additionally requires the *structure* (the set of source
  file paths) to be similar. A name that is too generic to identify a project
  ("download", "project", ...) never auto-matches at all.
* **The user decides when the evidence is not clear.** Anything short of one
  unambiguous candidate becomes a *suggestion*; the caller creates a new project
  unless the user explicitly picked an existing one.
* **ZIP root-folder renames are neutral.** ``myapp-v1/src/a.py`` and
  ``myapp-v2/src/a.py`` are the same structure: a single shared top-level folder
  is ignored for structural comparison (both the stripped and the as-uploaded
  forms are compared and the better one counts), and the root folder's name is a
  second name signal next to the ZIP's file name.
* Everything here is deterministic and explainable: each candidate carries the
  reasons that put it in the list.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

# Auto-attach thresholds (documented in docs/architecture.md).
AUTO_MATCH_MIN_STRUCTURE = 0.4  # path Jaccard needed, in addition to a name match
SUGGEST_MIN_STRUCTURE = 0.5  # a structure-only candidate is suggested from here
SUGGEST_MIN_STRUCTURE_WITH_NAME = 0.2  # ... or from here when the name also matches
HIGH_CONFIDENCE_STRUCTURE = 0.6
MAX_FINGERPRINT_PATHS = 20000

GENERIC_NAMES = frozenset(
    {
        "download", "downloads", "archive", "project", "projects", "untitled",
        "source", "sources", "code", "upload", "uploads", "new", "copy",
        "final", "backup", "zip", "export", "repo", "release", "build",
        # Typical top-level folder names that say nothing about *which* project.
        "src", "lib", "app", "main", "master", "dist", "out", "bin",
        "backend", "frontend", "server", "client", "web",
    }
)

_SEP = r"[-_. ]"
_TRAILING_TOKEN = re.compile(
    rf"(?:"
    rf"\(\d+\)"  # "name (1)"
    rf"|{_SEP}*v\d+(?:\.\d+)*"  # "name-v2", "namev2", "name v2.1"
    rf"|{_SEP}+(?:ver|version|rev)?{_SEP}*\d+(?:\.\d+)*"  # "name-2", "name_version_3", "name 2024-10-22" (digits)
    rf"|{_SEP}+(?:final|copy|new|latest|old|backup|bak|updated|update|release|rc\d*|main|master|dev)"
    rf")$"
)


def normalize_name(raw: str) -> str:
    """
    Reduce a ZIP/folder name to the identity-bearing part: lower-case, with
    trailing version/copy markers removed ("My-App_v2 (1)" -> "my-app").
    Returns "" for an empty input.
    """
    name = (raw or "").strip().lower()
    if name.endswith(".zip"):
        name = name[:-4]
    previous = None
    while previous != name:
        previous = name
        name = _TRAILING_TOKEN.sub("", name).strip(" -_.")
    return re.sub(r"[\s_]+", "-", name).strip("-")


def is_generic_name(normalized: str) -> bool:
    """True when `normalized` cannot identify a project by itself."""
    return (not normalized) or normalized in GENERIC_NAMES or len(normalized) < 2


# --------------------------------------------------------------------------
# Structure fingerprint
# --------------------------------------------------------------------------


def _clean_paths(paths: Iterable[str]) -> list[str]:
    cleaned = {
        p.replace("\\", "/").strip("/").lower()
        for p in paths
        if p and p.replace("\\", "/").strip("/")
    }
    return sorted(cleaned)[:MAX_FINGERPRINT_PATHS]


def strip_common_root(paths: list[str]) -> tuple[list[str], str | None]:
    """
    Remove one shared top-level folder (``myapp-v1/``) when *every* path lives
    inside the same one. Returns (paths, removed folder name or None).
    """
    if not paths or any("/" not in p for p in paths):
        return list(paths), None
    first = paths[0].split("/", 1)[0]
    if all(p.split("/", 1)[0] == first for p in paths):
        return [p.split("/", 1)[1] for p in paths], first
    return list(paths), None


def build_fingerprint(
    paths: Iterable[str],
    languages: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    """
    A small, JSON-serializable description of a project's structure:
    the source-file paths as uploaded and with the shared root folder removed,
    the removed root folder's name, and the per-language file counts.
    """
    cleaned = _clean_paths(paths)
    stripped, root = strip_common_root(cleaned)
    return {
        "paths": cleaned,
        "stripped_paths": stripped,
        "root_folder": root,
        "languages": dict(sorted((languages or {}).items())),
    }


def _jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    set_a, set_b = set(a), set(b)
    if not set_a and not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)


def structure_similarity(a: Mapping[str, Any], b: Mapping[str, Any]) -> float:
    """
    Path-set Jaccard similarity of two fingerprints in [0, 1]. The best of the
    four as-uploaded / root-stripped combinations is used, so renaming or
    adding/removing the ZIP's root folder does not change the result.
    """
    variants_a = [a.get("paths") or [], a.get("stripped_paths") or []]
    variants_b = [b.get("paths") or [], b.get("stripped_paths") or []]
    return max(_jaccard(x, y) for x in variants_a for y in variants_b)


def language_overlap(a: Mapping[str, Any], b: Mapping[str, Any]) -> float:
    return _jaccard((a.get("languages") or {}).keys(), (b.get("languages") or {}).keys())


# --------------------------------------------------------------------------
# Candidates
# --------------------------------------------------------------------------


@dataclass
class Candidate:
    identity_id: int
    name: str
    version_count: int
    latest_version: int
    latest_scan_id: int | None
    structure_score: float
    language_score: float
    name_match: bool
    reasons: list[str] = field(default_factory=list)
    confidence: str = "low"  # high | medium | low
    auto_match_eligible: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "identity_id": self.identity_id,
            "name": self.name,
            "version_count": self.version_count,
            "latest_version": self.latest_version,
            "latest_scan_id": self.latest_scan_id,
            "structure_score": round(self.structure_score, 3),
            "language_score": round(self.language_score, 3),
            "name_match": self.name_match,
            "confidence": self.confidence,
            "auto_match_eligible": self.auto_match_eligible,
            "reasons": list(self.reasons),
        }


def name_keys_for(zip_name: str, root_folder: str | None) -> list[str]:
    """Identity-bearing, non-generic name keys of an upload (file name + root folder)."""
    keys: list[str] = []
    for raw in (zip_name, root_folder or ""):
        key = normalize_name(raw)
        if key and not is_generic_name(key) and key not in keys:
            keys.append(key)
    return keys


def rank_candidates(
    zip_name: str,
    fingerprint: Mapping[str, Any],
    identities: Iterable[Mapping[str, Any]],
) -> list[Candidate]:
    """
    Compare an upload with existing projects.

    `identities`: dicts with ``identity_id, name, name_keys, version_count,
    latest_version, latest_scan_id, fingerprint`` (the *latest* version's
    structure). Returns candidates worth showing, best first. A candidate is
    ``auto_match_eligible`` only when a non-generic name key matches **and**
    the structure similarity reaches `AUTO_MATCH_MIN_STRUCTURE`.
    """
    upload_keys = set(name_keys_for(zip_name, fingerprint.get("root_folder")))
    ranked: list[Candidate] = []
    for identity in identities:
        known_keys = set(identity.get("name_keys") or [])
        name_match = bool(upload_keys & known_keys)
        latest_fp = identity.get("fingerprint") or {}
        structure = structure_similarity(fingerprint, latest_fp) if latest_fp else 0.0
        languages = language_overlap(fingerprint, latest_fp) if latest_fp else 0.0

        if not (
            (name_match and structure >= SUGGEST_MIN_STRUCTURE_WITH_NAME)
            or structure >= SUGGEST_MIN_STRUCTURE
        ):
            continue

        reasons: list[str] = []
        if name_match:
            reasons.append(f"name matches ('{sorted(upload_keys & known_keys)[0]}')")
        reasons.append(f"{round(structure * 100)}% of source-file paths are shared with its latest version")
        if languages:
            reasons.append(f"languages overlap {round(languages * 100)}%")
        if fingerprint.get("root_folder") and structure > 0:
            reasons.append("ZIP root folder ignored when comparing structure")

        eligible = name_match and structure >= AUTO_MATCH_MIN_STRUCTURE
        if name_match and structure >= HIGH_CONFIDENCE_STRUCTURE:
            confidence = "high"
        elif eligible or (name_match and structure >= SUGGEST_MIN_STRUCTURE_WITH_NAME) or structure >= HIGH_CONFIDENCE_STRUCTURE:
            confidence = "medium"
        else:
            confidence = "low"
        if not name_match:
            reasons.append("name does not match - suggestion only, never merged automatically")

        ranked.append(
            Candidate(
                identity_id=int(identity["identity_id"]),
                name=str(identity["name"]),
                version_count=int(identity.get("version_count") or 0),
                latest_version=int(identity.get("latest_version") or 0),
                latest_scan_id=identity.get("latest_scan_id"),
                structure_score=structure,
                language_score=languages,
                name_match=name_match,
                reasons=reasons,
                confidence=confidence,
                auto_match_eligible=eligible,
            )
        )
    ranked.sort(key=lambda c: (not c.auto_match_eligible, -c.structure_score, -c.language_score, c.identity_id))
    return ranked


@dataclass
class Resolution:
    """Outcome of automatic resolution for an upload with no explicit choice."""

    identity_id: int | None  # set only when exactly one candidate qualifies
    reason: str
    ambiguous: bool = False


def auto_resolve(candidates: list[Candidate]) -> Resolution:
    eligible = [c for c in candidates if c.auto_match_eligible]
    if len(eligible) == 1:
        c = eligible[0]
        return Resolution(
            c.identity_id,
            f"Matched existing project '{c.name}': {c.reasons[0]} and {c.reasons[1]}.",
        )
    if len(eligible) > 1:
        return Resolution(
            None,
            "Several existing projects match this upload equally well, so it was saved as a new project. "
            "Choose the project explicitly to add a version to one of them.",
            ambiguous=True,
        )
    return Resolution(None, "No existing project matched by name and structure; created a new project.")
