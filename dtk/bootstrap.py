"""First-run data bootstrap: download a published release into the local data dir."""
from __future__ import annotations

import hashlib
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

from .config import load_config
from .release import check_update

Progress = Callable[[str], None]

_PLACE = {
    "dtk.db": "db_path",
    "dtk_vectors.npz": "vectors_path",
    "research_vectors.npz": "research_vectors_path",
}


def _download(url: str, destination: Path, expected_sha256: str, progress: Progress) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "dtk-bootstrap"})
    with urllib.request.urlopen(request, timeout=120) as response, destination.open("wb") as out:
        total = int(response.headers.get("Content-Length") or 0)
        digest = hashlib.sha256()
        done = 0
        while True:
            chunk = response.read(1 << 20)
            if not chunk:
                break
            out.write(chunk)
            digest.update(chunk)
            done += len(chunk)
            if total:
                progress(f"下载 {destination.name} {done * 100 // total}%（{done >> 20}/{total >> 20} MB）")
    if expected_sha256 and digest.hexdigest() != expected_sha256:
        destination.unlink(missing_ok=True)
        raise ValueError(f"checksum mismatch: {destination.name}")


def _checksums(assets: dict[str, dict]) -> dict[str, str]:
    asset = assets.get("SHA256SUMS.txt")
    if asset is None:
        return {}
    request = urllib.request.Request(asset["url"], headers={"User-Agent": "dtk-bootstrap"})
    with urllib.request.urlopen(request, timeout=60) as response:
        lines = response.read().decode("utf-8").splitlines()
    return {line.split()[1]: line.split()[0] for line in lines if len(line.split()) == 2}


def bootstrap_data(
    repo: str | None = None,
    include_vectors: bool = True,
    progress: Progress = print,
    force: bool = False,
) -> dict:
    """Download the latest data release into the configured db/vectors paths."""
    config = load_config()
    repo = repo or config["release_repo"]
    if not repo:
        raise ValueError("release repo not configured (release_repo in data/dtk.json)")
    db_path = Path(config["db_path"])
    if db_path.is_file() and not force:
        progress(f"数据已存在：{db_path}（force 重新下载）")
        return {"skipped": True}

    info = check_update(repo, current="")
    assets = {asset["name"]: asset for asset in info["assets"]}
    wanted = [name for name in assets if name.startswith("dtk-db-")]
    if include_vectors:
        wanted += [name for name in assets if name.startswith("dtk-vectors-")]
    missing = [name for name in wanted if name not in assets]
    if missing:
        raise FileNotFoundError(f"release {info['latest']} missing assets: {missing}")
    sums = _checksums(assets)

    extracted: list[str] = []
    with tempfile.TemporaryDirectory(prefix="dtk-bootstrap-") as raw:
        work = Path(raw)
        for name in wanted:
            _download(assets[name]["url"], work / name, sums.get(name, ""), progress)
        for name in wanted:
            progress(f"解压 {name}")
            with zipfile.ZipFile(work / name) as bundle:
                for member in bundle.namelist():
                    if member == "manifest.json" or member not in _PLACE:
                        continue
                    out_path = Path(config[_PLACE[member]])
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    with bundle.open(member) as src, out_path.open("wb") as dst:
                        shutil.copyfileobj(src, dst)
                    extracted.append(str(out_path))
    progress(f"数据就绪（{info['latest']}）")
    return {"version": info["latest"], "files": extracted}
