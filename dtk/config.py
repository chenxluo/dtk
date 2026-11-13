"""Layered runtime configuration: defaults <- data/dtk.json <- environment.

One seam for every DTK_LLM_* / DTK_RESEARCH_* / path knob so the CLI, the
web server and the future UI editor agree on a single source of truth.
The file is optional; without it behaviour is exactly the old env-only one.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DEFAULTS: dict[str, Any] = {
    "db_path": "data/dtk.db",
    "source_db_path": None,
    "vectors_path": "data/dtk_vectors.npz",
    "research_vectors_path": "data/research_vectors.npz",
    "embedding_base_url": "http://127.0.0.1:6001/v1",
    "embedding_model": "text-embedding-qwen3-embedding-4b",
    "embedding_api_key": None,
    "research_model": "qwen3.8-27b",
    "research_llm_api_key": None,
    "research_llm_headers": {},
    "research_llm_body": {},
    "searx_url": "http://127.0.0.1:8989",
    "release_repo": None,
    "release_out": "dist",
    "parallel_api_key": None,
}

# file key -> environment variable that overrides it
ENV_MAP: dict[str, str] = {
    "db_path": "DTK_DB",
    "source_db_path": "DTK_SOURCE_DB",
    "vectors_path": "DTK_VECTORS",
    "research_vectors_path": "DTK_RESEARCH_VECTORS",
    "embedding_base_url": "DTK_LLM_BASE_URL",
    "release_repo": "DTK_RELEASE_REPO",
    "release_out": "DTK_RELEASE_OUT",
    "embedding_model": "DTK_EMBEDDING_MODEL",
    "embedding_api_key": "DTK_LLM_API_KEY",
    "research_model": "DTK_RESEARCH_MODEL",
    "research_llm_api_key": "DTK_RESEARCH_LLM_API_KEY",
    "research_llm_headers": "DTK_RESEARCH_LLM_HEADERS",
    "research_llm_body": "DTK_RESEARCH_LLM_BODY",
    "searx_url": "DTK_SEARX_URL",
    "parallel_api_key": "PARALLEL_API_KEY",
}

# keys the web UI may edit; everything else is display-only
EDITABLE_KEYS = (
    "embedding_base_url",
    "embedding_model",
    "embedding_api_key",
    "research_model",
    "research_llm_api_key",
    "searx_url",
)

_MASK = "••••"


def config_file_path() -> Path:
    override = os.environ.get("DTK_CONFIG")
    if override:
        return Path(override)
    return Path("data/dtk.json")


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """Merge defaults, the optional config file, then environment variables."""
    config = dict(DEFAULTS)
    file_path = Path(path) if path is not None else config_file_path()
    if file_path.is_file():
        try:
            stored = json.loads(file_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            stored = {}
        if isinstance(stored, dict):
            for key in DEFAULTS:
                if key in stored and stored[key] is not None:
                    config[key] = stored[key]
    for key, env in ENV_MAP.items():
        raw = os.environ.get(env)
        if raw:
            if isinstance(DEFAULTS[key], dict):
                try:
                    config[key] = json.loads(raw)
                except ValueError:
                    pass
            else:
                config[key] = raw
    return config


def save_config(updates: dict[str, Any], path: str | Path | None = None) -> dict[str, Any]:
    """Write changed editable keys back to the config file; returns the stored file content."""
    file_path = Path(path) if path is not None else config_file_path()
    current = load_config(file_path)
    stored: dict[str, Any] = {}
    if file_path.is_file():
        try:
            loaded = json.loads(file_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                stored = loaded
        except (OSError, ValueError):
            stored = {}
    for key in EDITABLE_KEYS:
        value = updates.get(key)
        if value is None or (isinstance(value, str) and value.startswith(_MASK)):
            continue  # masked echo from the UI: keep whatever is already set
        stored[key] = value
    file_path.parent.mkdir(parents=True, exist_ok=True)
    temp = file_path.with_suffix(".tmp")
    temp.write_text(json.dumps(stored, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, file_path)
    return stored


def mask_secret(value: Any) -> Any:
    """Render a secret for display: None stays None, anything else shows its tail."""
    if not value:
        return None
    text = str(value)
    return _MASK + text[-4:] if len(text) > 4 else _MASK
