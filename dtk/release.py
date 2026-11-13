"""Build and publish DTK data release artifacts.

Two artifacts per release so users can skip the vectors pack when they run a
different embedding model:
- dtk-db-<version>.zip               the Derived Store (required)
- dtk-vectors-<version>-<model>.zip  semantic indexes (optional)
"""
from __future__ import annotations

import hashlib
import json
import re
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import load_config
from .store import open_db


def _model_slug(model: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", model.lower()).strip("-")


def local_version(db_path: str | Path) -> str:
    """Version the data by its import timestamp, e.g. data-v2026.11.10-1530."""
    meta = _db_meta(db_path)
    try:
        stamp = datetime.fromisoformat(meta.get("import_completed_at") or "")
    except ValueError:
        return "data-v0.unknown"
    return f"data-v{stamp:%Y.%m.%d-%H%M}"


def _db_meta(db_path: str | Path) -> dict[str, str]:
    connection = open_db(db_path)
    try:
        return dict(connection.execute("SELECT key, value FROM meta"))
    finally:
        connection.close()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_json(manifest: dict[str, Any]) -> bytes:
    return json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")


def _write_zip(destination: Path, entries: dict[str, Path | bytes]) -> None:
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for name, payload in entries.items():
            if isinstance(payload, bytes):
                bundle.writestr(name, payload)
            else:
                bundle.write(payload, name)


def build_release(
    out_dir: str | Path,
    db_path: str | Path | None = None,
    vectors_path: str | Path | None = None,
    research_vectors_path: str | Path | None = None,
) -> dict[str, Any]:
    config = load_config()
    db = Path(db_path or config["db_path"])
    vectors = Path(vectors_path or config["vectors_path"])
    research_vectors = Path(research_vectors_path or config["research_vectors_path"])
    if not db.is_file():
        raise FileNotFoundError(f"derived store not found: {db}")

    meta = _db_meta(db)
    connection = open_db(db)
    try:
        tag_count = int(connection.execute("SELECT COUNT(*) FROM tag_details").fetchone()[0])
    finally:
        connection.close()

    version = local_version(db)
    manifest: dict[str, Any] = {
        "version": version,
        "built_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "dtk_schema_version": meta.get("dtk_schema_version"),
        "tag_count": tag_count,
        "import_completed_at": meta.get("import_completed_at"),
    }

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    db_zip = out / f"dtk-db-{version}.zip"
    _write_zip(db_zip, {"dtk.db": db, "manifest.json": _manifest_json(manifest)})
    artifacts: list[Path] = [db_zip]

    if vectors.is_file():
        from .vectors import VectorIndex

        model = VectorIndex.load(vectors).metadata.get("model", "unknown")
        manifest["embedding_model"] = model
        entries: dict[str, Path | bytes] = {
            "dtk_vectors.npz": vectors,
            "manifest.json": _manifest_json(manifest),
        }
        if research_vectors.is_file():
            entries["research_vectors.npz"] = research_vectors
        vectors_zip = out / f"dtk-vectors-{version}-{_model_slug(model)}.zip"
        _write_zip(vectors_zip, entries)
        artifacts.append(vectors_zip)

    manifest["artifacts"] = [
        {"name": path.name, "bytes": path.stat().st_size, "sha256": _sha256(path)}
        for path in artifacts
    ]
    (out / "SHA256SUMS.txt").write_text(
        "\n".join(f"{_sha256(path)}  {path.name}" for path in artifacts) + "\n",
        encoding="utf-8",
    )
    return manifest


def check_update(repo: str, current: str, timeout: float = 10.0) -> dict[str, Any]:
    """Compare the local data version with the latest GitHub release."""
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/releases/latest",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "dtk-release"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.load(response)
    latest = str(data.get("tag_name") or "")
    return {
        "current": current,
        "latest": latest,
        "update_available": bool(latest) and latest != current,
        "assets": [
            {
                "name": asset.get("name"),
                "bytes": asset.get("size"),
                "url": asset.get("browser_download_url"),
            }
            for asset in data.get("assets", [])
        ],
    }
