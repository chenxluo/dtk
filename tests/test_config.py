"""Layered config: defaults <- file <- env, plus masked UI writes."""
import json
import os
from pathlib import Path

from dtk.config import DEFAULTS, load_config, mask_secret, save_config


def test_defaults_when_no_file_or_env(tmp_path: Path) -> None:
    config = load_config(tmp_path / "missing.json")
    assert config == DEFAULTS


def test_file_overrides_defaults(tmp_path: Path) -> None:
    path = tmp_path / "dtk.json"
    path.write_text(json.dumps({"searx_url": "http://10.0.0.1:8989"}), encoding="utf-8")
    assert load_config(path)["searx_url"] == "http://10.0.0.1:8989"
    assert load_config(path)["db_path"] == DEFAULTS["db_path"]


def test_env_overrides_file(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "dtk.json"
    path.write_text(json.dumps({"searx_url": "http://file:8989"}), encoding="utf-8")
    monkeypatch.setenv("DTK_SEARX_URL", "http://env:8989")
    assert load_config(path)["searx_url"] == "http://env:8989"


def test_save_skips_masked_echo_and_persists(tmp_path: Path) -> None:
    path = tmp_path / "dtk.json"
    save_config({"embedding_api_key": "sk-real-key-123", "searx_url": "http://x:8989"}, path)
    save_config({"embedding_api_key": "••••-123"}, path)  # UI echoed the mask back
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["embedding_api_key"] == "sk-real-key-123"
    assert stored["searx_url"] == "http://x:8989"


def test_mask_secret_keeps_tail_only() -> None:
    assert mask_secret(None) is None
    assert mask_secret("") is None
    assert mask_secret("sk-abcd1234") == "••••1234"
    assert mask_secret("ab") == "••••"


def test_config_path_env_override(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DTK_CONFIG", str(tmp_path / "custom.json"))
    from dtk.config import config_file_path

    assert config_file_path() == tmp_path / "custom.json"
