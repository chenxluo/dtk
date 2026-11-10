"""Build research queues from the read-only Derived Store."""
from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from typing import Any

RESEARCH_MIN_POST_COUNT = 100


def build_research_queue(
    connection: sqlite3.Connection,
    limit: int = 50,
    min_post_count: int = RESEARCH_MIN_POST_COUNT,
    types: Sequence[str] = ("general", "meta"),
) -> list[dict[str, Any]]:
    if limit <= 0:
        raise ValueError("limit must be positive")
    if min_post_count < 0:
        raise ValueError("min_post_count must not be negative")
    requested = tuple(type_name for type_name in types if isinstance(type_name, str))
    invalid = set(requested) - {"general", "meta"}
    if invalid:
        raise ValueError(f"unsupported research tag types: {sorted(invalid)}")
    valid_types = tuple(dict.fromkeys(requested))
    if not valid_types:
        raise ValueError("types must contain general and/or meta")

    placeholders = ",".join("?" for _ in valid_types)

    rows = connection.execute(
        f"""
        WITH queued AS (
            SELECT
                t.id,
                t.name,
                t.type,
                t.post_count,
                t.wiki
            FROM tags t
            WHERE t.type IN ({placeholders})
              AND t.wiki IS NULL
              AND t.post_count >= ?
            ORDER BY t.post_count DESC, t.name
            LIMIT ?
        )
        SELECT
            q.id,
            q.name,
            q.type,
            q.post_count,
            q.wiki,
            coalesce(
                (SELECT group_concat(a.alias, char(10))
                 FROM aliases a WHERE a.tag_id = q.id ORDER BY a.alias),
                ''
            ) AS aliases
        FROM queued q
        ORDER BY q.post_count DESC, q.name
        """,
        (*valid_types, min_post_count, limit),
    ).fetchall()

    return [
        {
            "key": row["name"],
            "tag_id": row["id"],
            "tag": row["name"],
            "type": row["type"],
            "post_count": row["post_count"],
            "wiki": row["wiki"],
            "aliases": row["aliases"].split("\n") if row["aliases"] else [],
        }
        for row in rows
    ]
