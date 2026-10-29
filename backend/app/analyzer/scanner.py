"""
Project scanner.

Two responsibilities:

1. `safe_extract_zip` — extract an uploaded ZIP to disk defensively. This is
   untrusted input, so we guard against path traversal ("zip-slip"), zip
   bombs (entry count and uncompressed-size caps), and symlink tricks.
2. `discover_source_files` — walk the extracted tree and find the source
   files we know how to analyze (Python, Java, C and C++), skipping
   dependency/build/VCS directories.

Adding a new language later means: add its extension to `LANGUAGE_BY_EXT`
and add a parser for it in parser.py — nothing here needs to change.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path

from app.analyzer.cfamily import looks_like_cpp
from app.config import settings

# Extension -> language name. This is the single place that defines which
# languages the scanner recognizes.
LANGUAGE_BY_EXT: dict[str, str] = {
    ".py": "python",
    ".java": "java",
    ".c": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    # `.h` is ambiguous (C or C++); it is resolved per file by content in
    # `_resolve_language` below. "c" is only the fallback.
    ".h": "c",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
}


class UnsafeZipError(ValueError):
    """Raised when a ZIP fails a safety check and must not be extracted."""


@dataclass
class SourceFile:
    """A single discovered source file, ready to be parsed."""

    absolute_path: Path
    relative_path: str  # path relative to the project root, for display
    file_name: str
    language: str
    size_bytes: int


def _is_within_directory(base: Path, target: Path) -> bool:
    """True if `target` resolves to a location inside `base`."""
    try:
        target.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def safe_extract_zip(zip_path: Path, destination: Path) -> Path:
    """
    Safely extract `zip_path` into `destination` and return the extraction
    root. Raises UnsafeZipError if the archive is malformed, oversized, or
    contains entries that would escape the destination directory.
    """
    destination.mkdir(parents=True, exist_ok=True)
    max_bytes = settings.MAX_EXTRACTED_SIZE_MB * 1024 * 1024

    if not zipfile.is_zipfile(zip_path):
        raise UnsafeZipError("File is not a valid ZIP archive.")

    with zipfile.ZipFile(zip_path) as archive:
        infolist = archive.infolist()

        if len(infolist) > settings.MAX_ZIP_ENTRIES:
            raise UnsafeZipError(
                f"ZIP has too many entries (> {settings.MAX_ZIP_ENTRIES})."
            )

        total_uncompressed = sum(info.file_size for info in infolist)
        if total_uncompressed > max_bytes:
            raise UnsafeZipError(
                "ZIP's uncompressed size exceeds the allowed limit "
                f"({settings.MAX_EXTRACTED_SIZE_MB} MB)."
            )

        for info in infolist:
            # Reject absolute paths and parent-directory traversal
            # ("zip-slip") before ever touching the filesystem.
            member_path = Path(info.filename)
            if member_path.is_absolute() or ".." in member_path.parts:
                raise UnsafeZipError(
                    f"Unsafe path in ZIP entry: {info.filename!r}"
                )

            resolved_target = destination / member_path
            if not _is_within_directory(destination, resolved_target):
                raise UnsafeZipError(
                    f"ZIP entry would extract outside the target directory: "
                    f"{info.filename!r}"
                )

        # All entries passed validation — extract for real.
        archive.extractall(destination)

    return destination


def _should_ignore_dir(dir_name: str) -> bool:
    return dir_name in settings.IGNORED_DIRECTORIES or dir_name.startswith(".")


def _looks_binary(path: Path) -> bool:
    """True if the first 4 KB contain a NUL byte (text source never does)."""
    try:
        with path.open("rb") as handle:
            return b"\x00" in handle.read(4096)
    except OSError:
        return False


def _resolve_language(path: Path) -> str | None:
    """Language for a file from its extension. Only `.h` needs a look at
    the content: it is C++ if it uses C++-only syntax, otherwise C."""
    ext = path.suffix.lower()
    language = LANGUAGE_BY_EXT.get(ext)
    if language is not None and path.name.lower().endswith(".min.js"):
        return None  # minified bundle: generated output, not authored source
    if language is not None and ext == ".ts" and _looks_binary(path):
        return None  # `.ts` is also the MPEG transport-stream video extension
    if language is not None and ext == ".h":
        try:
            if looks_like_cpp(path.read_text(encoding="utf-8", errors="replace")):
                return "cpp"
        except OSError:
            pass
    return language


def discover_source_files(project_root: Path) -> list[SourceFile]:
    """
    Walk `project_root` and return every supported source file found,
    skipping ignored directories (node_modules, .git, build output, etc).
    """
    discovered: list[SourceFile] = []

    for path in sorted(project_root.rglob("*")):
        if not path.is_file():
            continue

        # Skip anything inside an ignored directory at any depth.
        if any(_should_ignore_dir(part) for part in path.relative_to(project_root).parts[:-1]):
            continue

        language = _resolve_language(path)
        if language is None:
            continue  # unsupported file type — not source code we analyze

        try:
            size_bytes = path.stat().st_size
        except OSError:
            continue

        discovered.append(
            SourceFile(
                absolute_path=path,
                relative_path=str(path.relative_to(project_root)),
                file_name=path.name,
                language=language,
                size_bytes=size_bytes,
            )
        )

    return discovered
