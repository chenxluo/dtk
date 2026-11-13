"""First-run bootstrap: file placement and skip semantics (network paths are
exercised end-to-end against the real release, not in unit tests)."""
import json
from pathlib import Path

from dtk.bootstrap import _PLACE, bootstrap_data


def test_place_maps_archive_members_to_config_keys() -> None:
    assert _PLACE["dtk.db"] == "db_path"
    assert _PLACE["dtk_vectors.npz"] == "vectors_path"
    assert _PLACE["research_vectors.npz"] == "research_vectors_path"


def test_bootstrap_skips_when_data_exists(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "dtk.json"
    db = tmp_path / "data" / "dtk.db"
    db.parent.mkdir(parents=True)
    db.write_bytes(b"existing")
    config_path.write_text(json.dumps({
        "db_path": str(db),
        "vectors_path": str(tmp_path / "v.npz"),
        "research_vectors_path": str(tmp_path / "r.npz"),
        "release_repo": "example/example",
    }), encoding="utf-8")
    monkeypatch.setenv("DTK_CONFIG", str(config_path))

    result = bootstrap_data(progress=lambda text: None)
    assert result == {"skipped": True}
    assert db.read_bytes() == b"existing"  # untouched


def test_bootstrap_requires_repo(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "dtk.json"
    config_path.write_text(json.dumps({
        "db_path": str(tmp_path / "missing.db"),
        "vectors_path": str(tmp_path / "v.npz"),
        "research_vectors_path": str(tmp_path / "r.npz"),
    }), encoding="utf-8")
    monkeypatch.setenv("DTK_CONFIG", str(config_path))

    try:
        bootstrap_data(progress=lambda text: None)
    except ValueError as exc:
        assert "release repo not configured" in str(exc)
    else:
        raise AssertionError("expected ValueError")
