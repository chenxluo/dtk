"""Read-only Tag lookup and retrieval."""
from __future__ import annotations

import re
import sqlite3
from typing import Any


def normalize_name(value: str) -> str:
    """Normalize user input for Danbooru name lookup."""
    collapsed = re.sub(r"\s+", " ", value.strip())
    return collapsed.replace(" ", "_").lower()


def _excerpt(text: str | None, limit: int = 180) -> str:
    if not text:
        return ""
    collapsed = re.sub(r"\s+", " ", text).strip()
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 1] + "…"


def _fts_expression(query: str) -> str:
    terms = re.findall(r"\w+", query.replace("_", " ").lower())
    if not terms:
        return ""
    return " OR ".join(f'"{term}"' for term in terms)


def _result(row: sqlite3.Row, matched_by: str, rank: int, **extra: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "tag": row["name"],
        "tag_id": row["id"],
        "type": row["type"],
        "post_count": row["post_count"],
        "matched_by": matched_by,
        "rank": rank,
        "wiki_excerpt": _excerpt(row["wiki"]),
    }
    result.update(extra)
    return result


def search(conn: sqlite3.Connection, query: str, limit: int = 10) -> dict[str, Any]:
    """Resolve a query through exact Canonical Tag and Alias lookup."""
    query = query.strip()
    if not query:
        raise ValueError("query must not be empty")

    lookup_key = normalize_name(query)
    row = conn.execute(
        """
        SELECT id, name, type, post_count, wiki
        FROM tags
        WHERE lookup_key = ?
        """,
        (lookup_key,),
    ).fetchone()
    if row is not None:
        return {
            "query": query,
            "matched_by": "exact_name",
            "results": [_result(row, "exact_name", 1)],
        }

    alias_rows = conn.execute(
        """
        SELECT
            t.id,
            t.name,
            t.type,
            t.post_count,
            t.wiki,
            (
                SELECT min(a.alias)
                FROM aliases a
                WHERE a.lookup_key = ? AND a.tag_id = t.id
            ) AS matched_alias
        FROM tags t
        WHERE EXISTS (
            SELECT 1 FROM aliases a
            WHERE a.lookup_key = ? AND a.tag_id = t.id
        )
        ORDER BY t.post_count DESC, t.name
        LIMIT ?
        """,
        (lookup_key, lookup_key, limit),
    ).fetchall()
    if alias_rows:
        return {
            "query": query,
            "matched_by": "alias",
            "results": [
                _result(row, "alias", rank, matched_alias=row["matched_alias"])
                for rank, row in enumerate(alias_rows, 1)
            ],
        }

    expression = _fts_expression(query)
    if not expression:
        return {"query": query, "matched_by": "no_match", "results": []}

    fts_rows = conn.execute(
        """
        SELECT
            t.id,
            t.name,
            t.type,
            t.post_count,
            t.wiki,
            bm25(tag_fts) AS bm25_score
        FROM tag_fts
        JOIN tags t ON t.id = tag_fts.tag_id
        WHERE tag_fts MATCH ?
        ORDER BY bm25(tag_fts), t.post_count DESC, t.name
        LIMIT ?
        """,
        (expression, limit),
    ).fetchall()
    if not fts_rows:
        return {"query": query, "matched_by": "no_match", "results": []}

    return {
        "query": query,
        "matched_by": "full_text",
        "results": [
            _result(
                row,
                "full_text",
                rank,
                bm25=row["bm25_score"],
                match_score=-row["bm25_score"],
            )
            for rank, row in enumerate(fts_rows, 1)
        ],
    }


def get_tag(conn: sqlite3.Connection, name: str) -> dict[str, Any] | None:
    """Return one canonical Tag record, or None when it does not exist."""
    row = conn.execute(
        """
        SELECT id, name, type, post_count, wiki
        FROM tags
        WHERE lookup_key = ?
        """,
        (normalize_name(name),),
    ).fetchone()
    if row is None:
        return None

    aliases = [
        row["alias"]
        for row in conn.execute(
            "SELECT alias FROM aliases WHERE tag_id = ? ORDER BY alias",
            (row["id"],),
        )
    ]
    return {
        "tag": row["name"],
        "tag_id": row["id"],
        "type": row["type"],
        "post_count": row["post_count"],
        "wiki": row["wiki"] or "",
        "wiki_excerpt": _excerpt(row["wiki"]),
        "aliases": aliases,
    }
