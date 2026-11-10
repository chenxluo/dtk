"""On-demand statistical Tag relations from the immutable post snapshot."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from .research.cache import write_json_atomic

from .search import normalize_name
from .store import database_info


def cooccurring_tags(
    connection: sqlite3.Connection,
    tag: str,
    limit: int = 20,
    min_count: int = 20,
    cache_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Find General/Meta co-occurrences ranked by Lift, not raw popularity."""
    if limit <= 0 or min_count < 1:
        raise ValueError("limit and min_count must be positive")

    lookup_key = normalize_name(tag)
    target = connection.execute(
        """
        SELECT id, name, post_count
        FROM tags
        WHERE lookup_key = ?
        """,
        (lookup_key,),
    ).fetchone()
    if target is None:
        return {"tag": tag, "matched_by": "no_match", "results": []}

    source_path = database_info(connection).get("source_path")
    if not source_path:
        raise ValueError("Derived Store has no Source Snapshot path")
    source = Path(source_path).resolve()
    if not source.is_file():
        raise FileNotFoundError(f"source snapshot not found: {source}")

    cache_path: Path | None = None
    if cache_dir:
        info = database_info(connection)
        cache_key = json.dumps(
            {
                "tag": target["name"],
                "limit": limit,
                "min_count": min_count,
                "source_path": str(source),
                "source_size_bytes": info.get("source_size_bytes"),
                "source_mtime": info.get("source_mtime"),
            },
            sort_keys=True,
        )
        cache_path = Path(cache_dir) / (hashlib.sha256(cache_key.encode()).hexdigest() + ".json")
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cached = None
        if isinstance(cached, dict):
            cached["cache_hit"] = True
            return cached

    connection.execute(
        "ATTACH DATABASE ? AS post_snapshot",
        (f"file:{source.as_posix()}?mode=ro",),
    )
    try:
        total_posts = connection.execute(
            "SELECT COUNT(*) FROM post_snapshot.main_table"
        ).fetchone()[0]
        if total_posts == 0:
            return {"tag": target["name"], "matched_by": "no_match", "results": []}
        rows = connection.execute(
            """
            SELECT
                other.id AS tag_id,
                other.name AS tag,
                other.post_count AS post_count,
                COUNT(*) AS cooccurrence_count
            FROM post_snapshot.post_tags p
            JOIN post_snapshot.post_tags q ON q.post_id = p.post_id
            JOIN tags other ON other.id = q.tag_id
            WHERE p.tag_id = ?
              AND q.tag_id != ?
              AND other.type IN ('general', 'meta')
            GROUP BY q.tag_id, other.name, other.post_count
            HAVING COUNT(*) >= ?
            """,
            (target["id"], target["id"], min_count),
        ).fetchall()
    finally:
        connection.execute("DETACH DATABASE post_snapshot")

    results = []
    for row in rows:
        if row["post_count"] <= 0 or row["cooccurrence_count"] <= 0:
            continue
        lift = (
            row["cooccurrence_count"] * total_posts
            / (target["post_count"] * row["post_count"])
        )
        results.append(
            {
                "tag": row["tag"],
                "post_count": row["post_count"],
                "cooccurrence_count": row["cooccurrence_count"],
                "lift": lift,
                "provenance": "statistical_cooccurrence",
            }
        )
    results.sort(
        key=lambda item: (-item["lift"], -item["cooccurrence_count"], item["tag"])
    )
    result = {
        "tag": target["name"],
        "matched_by": "cooccurrence",
        "total_posts": total_posts,
        "min_count": min_count,
        "cache_hit": False,
        "results": results[:limit],
    }
    if cache_path:
        write_json_atomic(cache_path, result)
    return result
