"""Official Danbooru upstream sync: Tag Implications and freshness checks.

Implications come from the live Danbooru JSON API and land in an additive
Derived Store table. Official snapshot Wiki columns are never touched.
"""
from __future__ import annotations

import json
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

DEFAULT_BASE_URL = "https://danbooru.donmai.us"
USER_AGENT = "DTK-upstream/0.3"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tag_implications (
    id INTEGER PRIMARY KEY,
    antecedent TEXT NOT NULL,
    consequent TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_implications_antecedent ON tag_implications(antecedent);
CREATE INDEX IF NOT EXISTS idx_implications_consequent ON tag_implications(consequent);
"""


def _fetch_json(url: str, timeout: float = 30.0) -> Any:
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    last_error = "no attempt"
    for delay in (0, 2, 10):
        if delay:
            time.sleep(delay)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if error.code in (408, 429) or 500 <= error.code < 600:
                last_error = f"HTTP {error.code}"
                continue
            raise
        except (urllib.error.URLError, TimeoutError, ValueError) as error:
            last_error = repr(error)
    raise RuntimeError(f"upstream fetch failed for {url}: {last_error}")


def _implication_pages(
    base_url: str,
    status: str,
    page_size: int,
    delay: float,
    timeout: float,
    fetch_json: Callable[..., Any],
):
    page = 1
    while True:
        params = urllib.parse.urlencode(
            {"limit": page_size, "page": page, "search[status]": status}
        )
        rows = fetch_json(f"{base_url}/tag_implications.json?{params}", timeout=timeout)
        if not isinstance(rows, list) or not rows:
            break
        yield rows
        if len(rows) < page_size:
            break
        page += 1
        time.sleep(delay)


def _set_meta(connection: sqlite3.Connection, values: dict[str, Any]) -> None:
    connection.executemany(
        """
        INSERT INTO meta(key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """,
        [(key, str(value)) for key, value in values.items()],
    )


def import_implications(
    db_path: str | Path,
    *,
    base_url: str = DEFAULT_BASE_URL,
    status: str = "active",
    page_size: int = 1000,
    delay: float = 0.7,
    timeout: float = 30.0,
    fetch_json: Callable[..., Any] = _fetch_json,
) -> dict[str, Any]:
    """Paginate the official Tag Implication API into an additive table."""
    database = Path(db_path).resolve()
    if not database.is_file():
        raise FileNotFoundError(f"derived store not found: {database}")
    imported = 0
    pages = 0
    connection = sqlite3.connect(database)
    try:
        connection.executescript(_SCHEMA)
        for rows in _implication_pages(base_url, status, page_size, delay, timeout, fetch_json):
            connection.executemany(
                """
                INSERT OR REPLACE INTO tag_implications(
                    id, antecedent, consequent, status, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        row["id"],
                        row["antecedent_name"],
                        row["consequent_name"],
                        row["status"],
                        row.get("created_at"),
                    )
                    for row in rows
                ],
            )
            imported += len(rows)
            pages += 1
            connection.commit()
        _set_meta(
            connection,
            {
                "implications_source": f"{base_url}/tag_implications.json",
                "implications_status": status,
                "implications_fetched_at": datetime.now(timezone.utc).isoformat(),
                "implication_count": connection.execute(
                    "SELECT COUNT(*) FROM tag_implications"
                ).fetchone()[0],
            },
        )
        connection.commit()
        return {"imported": imported, "pages": pages, "status": status}
    finally:
        connection.close()


def upstream_status(
    conn: sqlite3.Connection,
    *,
    base_url: str = DEFAULT_BASE_URL,
    timeout: float = 30.0,
    fetch_json: Callable[..., Any] = _fetch_json,
) -> dict[str, Any]:
    """Compare live Danbooru post counters with the local Source Snapshot."""
    posts = fetch_json(f"{base_url}/posts.json?limit=1", timeout=timeout)
    counts = fetch_json(f"{base_url}/counts/posts.json", timeout=timeout)
    info = {row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM meta")}
    latest = posts[0] if isinstance(posts, list) and posts else {}
    snapshot_id = int(info.get("source_latest_post_id") or 0)
    return {
        "upstream_latest_post_id": latest.get("id"),
        "upstream_latest_post_created_at": latest.get("created_at"),
        "upstream_post_total": (counts.get("counts") or {}).get("posts"),
        "snapshot_latest_post_id": snapshot_id,
        "snapshot_latest_post_created_at": info.get("source_latest_post_created_at"),
        "posts_behind": (latest.get("id") or 0) - snapshot_id
        if latest.get("id")
        else None,
    }
