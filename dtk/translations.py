"""Import community Chinese tag-name translations (ffdkj table, MIT).

Translations land in an additive Derived Store table keyed by tag id; snapshot
columns are never touched. Source: ffdkj/ffdkj-Danbooru_Tag-Chinese-English-Translation-Table.
"""
from __future__ import annotations

import json
import sqlite3
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

FFDKJ_URL = (
    "https://raw.githubusercontent.com/ffdkj/"
    "ffdkj-Danbooru_Tag-Chinese-English-Translation-Table/main/tag.sqlite"
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tag_translations (
    tag_id INTEGER PRIMARY KEY,
    cn_name TEXT NOT NULL,
    source TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);
"""

TRANSLATIONS_SCHEMA = _SCHEMA

_SQLITE_MAGIC = b"SQLite format 3\x00"


def fetch_ffdkj_sqlite(destination: str | Path, *, url: str = FFDKJ_URL, timeout: float = 120.0) -> Path:
    """Download the ffdkj tag.sqlite (LFS-backed; media URL serves real bytes)."""
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(
        url, headers={"User-Agent": "DTK-research/0.3"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = response.read()
    if not data.startswith(_SQLITE_MAGIC):
        raise ValueError(f"downloaded file is not SQLite (got {len(data)} bytes): {url}")
    path.write_bytes(data)
    return path


def import_translations(
    db_path: str | Path,
    source_path: str | Path,
    *,
    source_label: str = "ffdkj",
) -> dict[str, Any]:
    """Merge Chinese tag names into the additive tag_translations table."""
    database = Path(db_path).resolve()
    if not database.is_file():
        raise FileNotFoundError(f"derived store not found: {database}")
    src = Path(source_path).resolve()
    if not src.is_file():
        raise FileNotFoundError(f"translation table not found: {src}")

    fetched_at = datetime.now(timezone.utc).isoformat()
    connection = sqlite3.connect(database)
    try:
        connection.executescript(_SCHEMA)
        connection.execute("ATTACH DATABASE ? AS tl", (str(src),))
        rows = connection.execute(
            """
            SELECT t.id, tl.cn_name
            FROM tl.tag tl
            JOIN tags t ON t.lookup_key = lower(replace(tl.name, ' ', '_'))
            WHERE tl.cn_name IS NOT NULL AND trim(tl.cn_name) <> ''
            """
        ).fetchall()
        connection.executemany(
            """
            INSERT OR REPLACE INTO tag_translations(tag_id, cn_name, source, fetched_at)
            VALUES (?, ?, ?, ?)
            """,
            [(tag_id, cn_name.strip(), source_label, fetched_at) for tag_id, cn_name in rows],
        )
        total = connection.execute("SELECT COUNT(*) FROM tl.tag").fetchone()[0]
        # no DETACH needed: closing the connection releases the attached db
        connection.execute(
            """
            INSERT INTO meta(key, value) VALUES ('translations_source', ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (json.dumps({"label": source_label, "file": str(src), "rows": total}),),
        )
        connection.execute(
            """
            INSERT INTO meta(key, value) VALUES ('translations_fetched_at', ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (fetched_at,),
        )
        connection.commit()
        return {
            "source_rows": total,
            "matched": len(rows),
            "label": source_label,
        }
    finally:
        connection.close()
