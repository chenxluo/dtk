"""Build and open the Derived Store from an immutable Source Snapshot."""
from __future__ import annotations

import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .enrich import ENRICHMENT_SCHEMA
from .translations import TRANSLATIONS_SCHEMA
from .upstream import UPSTREAM_SCHEMA

SCHEMA_VERSION = 2
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
"""

_FTS_SCHEMA = """
CREATE VIRTUAL TABLE tag_fts USING fts5(
    name,
    cn_name,
    aliases,
    other_names,
    terms,
    wiki,
    tag_id UNINDEXED
);
"""

_TAG_DETAILS_VIEW = """
CREATE VIEW IF NOT EXISTS tag_details AS
SELECT
    t.id,
    t.name,
    t.lookup_key,
    t.type,
    t.post_count,
    coalesce(u.body, t.wiki) AS wiki,
    CASE
        WHEN u.body IS NOT NULL THEN 'upstream'
        WHEN t.wiki IS NOT NULL AND trim(t.wiki) <> '' THEN 'snapshot'
    END AS wiki_source,
    tr.cn_name AS cn_name,
    u.other_names_json AS other_names_json,
    e.cn_definition AS cn_definition,
    e.terms_json AS terms_json
FROM tags t
LEFT JOIN upstream_wikis u ON u.tag_id = t.id
LEFT JOIN tag_translations tr ON tr.tag_id = t.id
LEFT JOIN tag_enrichment e ON e.tag_id = t.id;
"""

_ADDITIVE_SCHEMA = (
    UPSTREAM_SCHEMA + TRANSLATIONS_SCHEMA + ENRICHMENT_SCHEMA + _TAG_DETAILS_VIEW
)

_FTS_INSERT = """
INSERT INTO tag_fts(tag_id, name, cn_name, aliases, other_names, terms, wiki)
WITH alias_text(tag_id, aliases) AS (
    SELECT tag_id, group_concat(replace(alias, '_', ' '), char(10))
    FROM aliases
    GROUP BY tag_id
),
other_text(tag_id, names) AS (
    SELECT u.tag_id, group_concat(replace(j.value, '_', ' '), ' ')
    FROM upstream_wikis u, json_each(u.other_names_json) j
    GROUP BY u.tag_id
)
SELECT
    t.id,
    replace(t.name, '_', ' '),
    coalesce(tr.cn_name, ''),
    coalesce(a.aliases, ''),
    coalesce(o.names, ''),
    coalesce(e.terms_json, ''),
    coalesce(u.body, t.wiki, '')
FROM tags t
LEFT JOIN alias_text a ON a.tag_id = t.id
LEFT JOIN other_text o ON o.tag_id = t.id
LEFT JOIN upstream_wikis u ON u.tag_id = t.id
LEFT JOIN tag_translations tr ON tr.tag_id = t.id
LEFT JOIN tag_enrichment e ON e.tag_id = t.id
"""


def _uri(path: Path, readonly: bool = False) -> str:
    uri = "file:" + path.resolve().as_posix()
    return uri + "?mode=ro" if readonly else uri


def _set_meta(conn: sqlite3.Connection, values: dict[str, Any]) -> None:
    conn.executemany(
        """
        INSERT INTO meta(key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """,
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

        conn.executescript(_SCHEMA + _ADDITIVE_SCHEMA + _FTS_SCHEMA)
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
        conn.execute(_FTS_INSERT)

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


def rebuild_search_index(db_path: str | Path) -> dict[str, Any]:
    """Recreate the FTS index from snapshot plus additive upstream/translation data.

    Run after `import-wikis` / `import-translations` so full-text search sees
    merged Wiki bodies, Chinese names, and official other-names.
    """
    database = Path(db_path).resolve()
    if not database.is_file():
        raise FileNotFoundError(f"derived store not found: {database}")
    conn = sqlite3.connect(database, timeout=120.0)
    try:
        conn.executescript(_ADDITIVE_SCHEMA)
        conn.execute("DROP TABLE IF EXISTS tag_fts")
        conn.executescript(_FTS_SCHEMA)
        conn.execute(_FTS_INSERT)
        _set_meta(
            conn,
            {
                "fts_index_built_at": datetime.now(timezone.utc).isoformat(),
                "fts_row_count": conn.execute("SELECT COUNT(*) FROM tag_fts").fetchone()[0],
            },
        )
        conn.commit()
        return {"fts_rows": conn.execute("SELECT COUNT(*) FROM tag_fts").fetchone()[0]}
    finally:
        conn.close()


def database_info(conn: sqlite3.Connection) -> dict[str, str]:
    """Return the snapshot and import manifest stored in the Derived Store."""
    return {row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM meta")}
