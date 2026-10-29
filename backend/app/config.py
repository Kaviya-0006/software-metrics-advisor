"""
Application configuration.

Loads settings from environment variables (via a local .env file in
development). No secrets are hard-coded here — see .env.example for the
variables this application expects.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Load variables from a .env file in the backend/ directory, if present.
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


class Settings:
    """Central place for all configuration values."""

    # General
    APP_NAME: str = "AI-Assisted Software Metrics Advisor"
    SERVICE_ID: str = "software-metrics-advisor"
    ENV: str = os.getenv("ENV", "development")

    # Database
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL", "sqlite:///./software_metrics.db"
    )

    # CORS — comma-separated list of allowed origins
    CORS_ORIGINS: list[str] = [
        origin.strip()
        for origin in os.getenv(
            "CORS_ORIGINS", "http://localhost:5173,http://localhost:3000"
        ).split(",")
        if origin.strip()
    ]

    # LLM API key placeholder for later phases (AI Advisor).
    # Not used in Phase 1. Never hard-code a real key here.
    LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")

    # --- Phase 2: upload / scanner settings -------------------------------

    # Where uploaded project ZIPs are extracted to (one sub-folder per
    # upload, named with a UUID). Relative to the backend/ working dir.
    UPLOAD_DIR: Path = BASE_DIR / os.getenv("UPLOAD_DIR", "../uploads")

    # Reject ZIP uploads larger than this (compressed size, as reported by
    # the HTTP request) to avoid disk exhaustion from a hostile upload.
    MAX_UPLOAD_SIZE_MB: int = int(os.getenv("MAX_UPLOAD_SIZE_MB", "50"))

    # Safety cap on the *uncompressed* size we are willing to write to disk
    # while extracting a ZIP, independent of the compressed upload size.
    # Protects against zip-bomb style uploads (small file, huge extracted
    # content).
    MAX_EXTRACTED_SIZE_MB: int = int(os.getenv("MAX_EXTRACTED_SIZE_MB", "200"))

    # Reject ZIPs with more than this many entries (another zip-bomb guard).
    MAX_ZIP_ENTRIES: int = int(os.getenv("MAX_ZIP_ENTRIES", "5000"))

    # Directories that are skipped during scanning — dependency/build/VCS
    # output that is never source code we want to score. Configurable via
    # env as a comma-separated list; falls back to a sensible default.
    IGNORED_DIRECTORIES: set[str] = {
        d.strip()
        for d in os.getenv(
            "IGNORED_DIRECTORIES",
            "node_modules,.git,__pycache__,target,build,dist,venv,.venv,"
            ".idea,.vscode,.pytest_cache,egg-info,.mypy_cache,out,bin,obj",
        ).split(",")
        if d.strip()
    }

    # --- Phase 6: AI Advisor provider settings -----------------------------
    #
    # "none" (the default) means the AI Advisor is disabled — the endpoint
    # returns a clean {"available": false, ...} response rather than
    # failing, and every other part of the application (upload, metrics,
    # health score, risk detection, dashboard) works exactly the same
    # whether or not this is configured.
    AI_PROVIDER: str = os.getenv("AI_PROVIDER", "none")

    # Model identifier string, not a secret — safe to have a default.
    AI_MODEL: str = os.getenv("AI_MODEL", "claude-haiku-4-5-20251001")

    # Falls back to the older LLM_API_KEY name above for continuity with
    # the Phase 1 .env.example placeholder; either env var works.
    AI_API_KEY: str = os.getenv("AI_API_KEY", LLM_API_KEY)

    # How long to wait for the AI provider before treating it as failed.
    AI_REQUEST_TIMEOUT_SECONDS: int = int(os.getenv("AI_REQUEST_TIMEOUT_SECONDS", "30"))

    # Base URL of a locally-running Ollama server, only used when
    # AI_PROVIDER=ollama. Ollama is unauthenticated by design (it's a
    # local server), so no API key is needed for this provider — see
    # is_ai_configured() in app/ai/advisor.py.
    OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")


settings = Settings()
