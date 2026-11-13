"""Release artifacts: version stamping, db zip + optional vectors zip, checksums."""
import json
import re
import zipfile
from pathlib import Path

from dtk.release import build_release, local_version

from tests.fixtures import make_source


def _fixture_db(root: Path) -> Path:
    source = root / "source.db"
    derived = root / "derived.db"
    make_source(source)
    from dtk.store import build_database

    build_database(source, derived)
    return derived


def test_local_version_stamped_from_import(tmp_path: Path) -> None:
    derived = _fixture_db(tmp_path)
    version = local_version(derived)
    assert re.fullmatch(r"data-v\d{4}\.\d{2}\.\d{2}-\d{4}", version), version


def test_build_release_packs_db_and_checksums(tmp_path: Path) -> None:
    derived = _fixture_db(tmp_path)
    out = tmp_path / "dist"
    manifest = build_release(
        out, db_path=derived, vectors_path=tmp_path / "missing.npz"
    )

    assert manifest["tag_count"] >= 1
    assert re.fullmatch(r"data-v[\d.\-]+", manifest["version"])
    names = [item["name"] for item in manifest["artifacts"]]
    assert len(names) == 1  # vectors missing -> db zip only
    assert names[0].startswith("dtk-db-")

    db_zip = out / names[0]
    with zipfile.ZipFile(db_zip) as bundle:
        assert bundle.namelist() == ["dtk.db", "manifest.json"]
        inner = json.loads(bundle.read("manifest.json"))
        assert inner["version"] == manifest["version"]

    sums = (out / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines()
    assert len(sums) == 1
    assert sums[0].split()[1] == names[0]
    assert re.fullmatch(r"[0-9a-f]{64}", sums[0].split()[0])
