"""Publish human-accepted Research Candidates into the Derived Store.

Publication is explicit and additive: official Wiki columns are never changed.
Until a separate semantic index is built from these rows, published research
improves explanations only, not retrieval ranking.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def load_research_records(path: str | Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with Path(path).open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict) or "tag" not in value:
                raise ValueError(f"{path}:{line_number} is not a research record")
            records.append(value)
    return records

_SCHEMA = """
CREATE TABLE IF NOT EXISTS research_publications (
    tag_id INTEGER PRIMARY KEY,
    definition_zh TEXT NOT NULL,
    source_model_generated_at TEXT NOT NULL,
    published_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS research_documents (
    tag_id INTEGER NOT NULL REFERENCES tags(id),
    kind TEXT NOT NULL,
    language TEXT NOT NULL,
    text TEXT NOT NULL,
    UNIQUE(tag_id, kind, language, text)
);
CREATE INDEX IF NOT EXISTS idx_research_documents_tag ON research_documents(tag_id);

CREATE TABLE IF NOT EXISTS research_evidence (
    tag_id INTEGER NOT NULL REFERENCES tags(id),
    url TEXT NOT NULL,
    quote TEXT NOT NULL,
    page_text TEXT NOT NULL,
    UNIQUE(tag_id, url, quote)
);
CREATE INDEX IF NOT EXISTS idx_research_evidence_tag ON research_evidence(tag_id);
"""


def _validate(record: dict[str, Any], accepted_tags: set[str], connection: sqlite3.Connection) -> None:
    tag = record.get("tag")
    if tag not in accepted_tags:
        raise ValueError(f"tag {tag!r} is not accepted for publication")
    if record.get("status") != "completed":
        raise ValueError(f"tag {tag!r}: only completed candidates can be published")
    if record.get("evidence_verified") is not True:
        raise ValueError(f"tag {tag!r}: evidence_verified must be true")
    if len(record.get("definition_zh", "")) < 20:
        raise ValueError(f"tag {tag!r}: definition_zh is too short")

    tag_row = connection.execute(
        "SELECT id FROM tags WHERE id = ? AND name = ?",
        (record.get("tag_id"), tag),
    ).fetchone()
    if tag_row is None:
        raise ValueError(f"tag {tag!r} does not match the Derived Store tag_id")

    pages = {page["url"]: page["text"] for page in record.get("evidence_pages", [])}
    for evidence in record.get("evidence", []):
        quote = evidence.get("quote", "")
        if evidence.get("url") not in pages or quote not in pages.get(evidence.get("url"), ""):
            raise ValueError(f"tag {tag!r}: evidence quote is not verifiable")
    if not record.get("evidence"):
        raise ValueError(f"tag {tag!r}: completed candidate has no evidence")
    for document in record.get("semantic_documents", []):
        if not document.get("text") or document.get("kind") not in {
            "visual", "synonym", "contextual", "disambiguation"
        } or document.get("language") not in {"zh", "en"}:
            raise ValueError(f"tag {tag!r}: invalid semantic document")


def publish_research(
    db_path: str | Path,
    records: list[dict[str, Any]],
    *,
    accepted_tags: set[str],
) -> dict[str, int]:
    """Publish only explicitly accepted, verified candidates; idempotent."""
    database = Path(db_path).resolve()
    if not database.is_file():
        raise FileNotFoundError(f"derived store not found: {database}")

    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        for record in records:
            _validate(record, accepted_tags, connection)
        connection.executescript(_SCHEMA)
        connection.execute("BEGIN")
        for record in records:
            tag_id = record["tag_id"]
            connection.execute(
                """
                INSERT INTO research_publications(
                    tag_id, definition_zh, source_model_generated_at, published_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(tag_id) DO UPDATE SET
                    definition_zh=excluded.definition_zh,
                    source_model_generated_at=excluded.source_model_generated_at,
                    published_at=excluded.published_at
                """,
                (
                    tag_id,
                    record["definition_zh"],
                    record["model_generated_at"],
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            connection.execute("DELETE FROM research_documents WHERE tag_id = ?", (tag_id,))
            connection.execute("DELETE FROM research_evidence WHERE tag_id = ?", (tag_id,))
            connection.executemany(
                """
                INSERT OR IGNORE INTO research_documents(tag_id, kind, language, text)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (tag_id, document["kind"], document["language"], document["text"])
                    for document in record["semantic_documents"]
                ],
            )
            connection.executemany(
                """
                INSERT OR IGNORE INTO research_evidence(tag_id, url, quote, page_text)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (tag_id, evidence["url"], evidence["quote"], page["text"])
                    for evidence in record["evidence"]
                    for page in record["evidence_pages"]
                    if page["url"] == evidence["url"]
                ],
            )
        connection.commit()
        return {
            "published_tags": len(records),
            "documents": sum(len(record["semantic_documents"]) for record in records),
            "evidence_rows": sum(len(record["evidence"]) for record in records),
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
