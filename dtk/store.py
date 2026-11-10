"""Build and open the Derived Store from an immutable Source Snapshot."""
from __future__ import annotations

import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
_REQUIRED_SOURCE_TABLES = {"tags", "tag_types", "tag_wiki", "tag_aliases", "main_table"}

_SCHEMA = """
CREATE TABLE meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE tags (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    lookup_key TEXT NOT NULL UNIQUE,
    type TEXT NOT NULL,
    post_count INTEGER NOT NULL,
    wiki TEXT
);

CREATE INDEX idx_tags_post_count ON tags(post_count DESC);

CREATE TABLE aliases (
    lookup_key TEXT NOT NULL,
    alias TEXT NOT NULL,
    tag_id INTEGER NOT NULL REFERENCES tags(id)
);

CREATE INDEX idx_aliases_lookup ON aliases(lookup_key, tag_id);
CREATE INDEX idx_aliases_tag ON aliases(tag_id);

CREATE VIRTUAL TABLE tag_fts USING fts5(
    name,
    aliases,
    wiki,
    tag_id UNINDEXED
);
"""


def _uri(path: Path, readonly: bool = False) -> str:
    uri = "file:" + path.resolve().as_posix()
    return uri + "?mode=ro" if readonly else uri


def _set_meta(conn: sqlite3.Connection, values: dict[str, Any]) -> None:
    conn.executemany(
        "INSERT INTO meta(key, value) VALUES (?, ?)",
        [(key, str(value)) for key, value in values.items()],
    )


def build_database(source: str | Path, destination: str | Path) -> dict[str, Any]:
    """Create a new Derived Store from a read-only Source Snapshot.

    The destination is replaced atomically. The source database is attached with
    SQLite read-only mode and is never written.
    """
    source_path = Path(source).resolve()
    destination_path = Path(destination).resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"source snapshot not found: {source_path}")
    if source_path == destination_path:
        raise ValueError("destination must differ from the source snapshot")

    destination_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = destination_path.with_name(destination_path.name + ".tmp")
    temporary_path.unlink(missing_ok=True)

    source_stat = source_path.stat()
    started = time.monotonic()
    started_at = datetime.now(timezone.utc).isoformat()
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(_uri(temporary_path), uri=True)
        conn.execute("PRAGMA journal_mode=OFF")
        conn.execute("PRAGMA synchronous=OFF")
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute("PRAGMA cache_size=-262144")
        conn.execute("ATTACH DATABASE ? AS src", (_uri(source_path, readonly=True),))

        missing = _REQUIRED_SOURCE_TABLES - {
            row[0]
            for row in conn.execute(
                "SELECT name FROM src.sqlite_master WHERE type='table'"
            )
        }
        if missing:
            raise ValueError(f"source snapshot missing tables: {sorted(missing)}")

        conn.executescript(_SCHEMA)
        conn.execute("BEGIN")

        conn.execute(
            """
            INSERT INTO tags(id, name, lookup_key, type, post_count, wiki)
            SELECT
                s.id,
                s.tag,
                lower(replace(s.tag, ' ', '_')),
                coalesce(tt.name, 'unknown'),
                coalesce(s.post_count, 0),
                w.wiki_content
            FROM src.tags s
            LEFT JOIN src.tag_types tt ON tt.id = s.type_id
            LEFT JOIN src.tag_wiki w ON w.tag_id = s.id
            """
        )
        conn.execute(
            """
            INSERT INTO aliases(lookup_key, alias, tag_id)
            SELECT lower(replace(a.alias, ' ', '_')), a.alias, a.tag_id
            FROM src.tag_aliases a
            JOIN tags t ON t.id = a.tag_id
            """
        )
        conn.execute(
            """
            INSERT INTO tag_fts(tag_id, name, aliases, wiki)
            WITH alias_text(tag_id, aliases) AS (
                SELECT tag_id, group_concat(replace(alias, '_', ' '), char(10))
                FROM aliases
                GROUP BY tag_id
            )
            SELECT
                t.id,
                replace(t.name, '_', ' '),
                coalesce(a.aliases, ''),
                coalesce(t.wiki, '')
            FROM tags t
            LEFT JOIN alias_text a ON a.tag_id = t.id
            """
        )

        counts = {
            "source_tag_rows": conn.execute("SELECT COUNT(*) FROM src.tags").fetchone()[0],
            "source_wiki_rows": conn.execute(
                "SELECT COUNT(*) FROM src.tag_wiki"
            ).fetchone()[0],
            "source_alias_rows": conn.execute(
                "SELECT COUNT(*) FROM src.tag_aliases"
            ).fetchone()[0],
            "derived_tag_rows": conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0],
            "derived_wiki_rows": conn.execute(
                "SELECT COUNT(*) FROM tags WHERE wiki IS NOT NULL"
            ).fetchone()[0],
            "derived_alias_rows": conn.execute(
                "SELECT COUNT(*) FROM aliases"
            ).fetchone()[0],
        }
        mismatch = {
            key: (counts[f"source_{key}"], counts[f"derived_{key}"])
            for key in ("tag_rows", "alias_rows")
            if counts[f"source_{key}"] != counts[f"derived_{key}"]
        }
        if mismatch:
            raise RuntimeError(f"import count mismatch: {mismatch}")

        latest = conn.execute(
            """
            SELECT post_id, created_at
            FROM src.main_table
            ORDER BY post_id DESC
            LIMIT 1
            """
        ).fetchone()
        _set_meta(
            conn,
            {
                "dtk_schema_version": SCHEMA_VERSION,
                "source_path": str(source_path),
                "source_size_bytes": source_stat.st_size,
                "source_mtime": datetime.fromtimestamp(
                    source_stat.st_mtime, tz=timezone.utc
                ).isoformat(),
                "source_open_mode": "readonly",
                "source_latest_post_id": latest[0] if latest else "",
                "source_latest_post_created_at": latest[1] if latest else "",
                "import_started_at": started_at,
                "import_completed_at": datetime.now(timezone.utc).isoformat(),
                "import_duration_seconds": round(time.monotonic() - started, 3),
            },
        )
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        conn.commit()
        conn.execute("DETACH DATABASE src")
        conn.execute("PRAGMA optimize")
        conn.close()
        conn = None

        os.replace(temporary_path, destination_path)
        return {"destination": str(destination_path), **counts}
    except Exception:
        if conn is not None:
            conn.close()
        temporary_path.unlink(missing_ok=True)
        raise


def open_db(path: str | Path) -> sqlite3.Connection:
    """Open a Derived Store read-only for query operations."""
    db_path = Path(path).resolve()
    if not db_path.is_file():
        raise FileNotFoundError(f"derived store not found: {db_path}; run `dtk import` first")
    conn = sqlite3.connect(_uri(db_path, readonly=True), uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def database_info(conn: sqlite3.Connection) -> dict[str, str]:
    """Return the snapshot and import manifest stored in the Derived Store."""
    return {row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM meta")}
