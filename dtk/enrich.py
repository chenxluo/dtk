"""LLM enrichment: associative retrieval terms + Chinese definitions.

One flash call per tag produces zh/en search phrasings, an optional facet map,
and a Chinese definition. Results land in the additive `tag_enrichment` table
and feed the FTS/vector retrieval layers (ADR 0008).
"""
from __future__ import annotations

import concurrent.futures
import json
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .research.llm import ResearchLLMClient

ENRICHMENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS tag_enrichment (
    tag_id INTEGER PRIMARY KEY,
    cn_definition TEXT,
    terms_json TEXT NOT NULL,
    facets_json TEXT,
    model TEXT NOT NULL,
    generated_at TEXT NOT NULL
);
"""

ENRICH_SYSTEM = """你是 Danbooru 标签检索增强助手。对给定的二次元/ACG 标签,只输出一个 JSON 对象(不要 markdown 代码块,不要多余文字):
{"cn_definition": "一句话中文定义(不超过80字),说明该标签在二次元绘图语境的含义",
 "zh_terms": ["3-8个中文联想说法:口语同义词、组合描述、上位概念"],
 "en_terms": ["2-6个英文联想说法"],
 "facets": {"可选:从 color/garment/expression/pose/composition/hair/animal/object 里选适用键,值为英文词列表"}}
联想说法是给中文检索用的查询变体,不是标签同义词列表;标签本身已有别名,不需要重复。"""

_MAX_DEFINITION = 120
_MAX_TERMS = 12
_MAX_TERM_LENGTH = 40


def _extract_json(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"```(?:json)?|```", "", text).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in response")
    return json.loads(cleaned[start : end + 1])


def _normalize(payload: dict[str, Any]) -> dict[str, Any]:
    definition = str(payload.get("cn_definition") or "").strip()[:_MAX_DEFINITION]

    def clean_terms(key: str) -> list[str]:
        values = payload.get(key) or []
        if not isinstance(values, list):
            return []
        seen: list[str] = []
        for value in values:
            item = str(value).strip().replace("_", " ")[:_MAX_TERM_LENGTH]
            if item and item not in seen:
                seen.append(item)
        return seen[:_MAX_TERMS]

    zh_terms = clean_terms("zh_terms")
    en_terms = clean_terms("en_terms")
    facets_raw = payload.get("facets") or {}
    facets = {
        str(key): [str(v) for v in values if str(v).strip()][:6]
        for key, values in facets_raw.items()
        if isinstance(values, list) and values
    } if isinstance(facets_raw, dict) else {}
    if not zh_terms and not en_terms:
        raise ValueError("no usable terms in response")
    return {
        "cn_definition": definition,
        "terms": zh_terms + en_terms,
        "facets": facets,
    }


def enrich_tags(
    db_path: str | Path,
    llm: ResearchLLMClient,
    *,
    min_post_count: int = 10000,
    types: tuple[str, ...] = ("general", "meta"),
    limit: int | None = None,
    workers: int = 2,
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """Generate enrichment rows for high-usage tags (idempotent, resumable)."""
    if workers <= 0:
        raise ValueError("workers must be positive")
    database = Path(db_path).resolve()
    if not database.is_file():
        raise FileNotFoundError(f"derived store not found: {database}")

    connection = sqlite3.connect(database, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.executescript(ENRICHMENT_SCHEMA)
    placeholders = ",".join("?" for _ in types)
    rows = connection.execute(
        f"""
        SELECT t.id, t.name, t.wiki, t.cn_name
        FROM tag_details t
        WHERE t.type IN ({placeholders}) AND t.post_count >= ?
            AND t.id NOT IN (SELECT tag_id FROM tag_enrichment)
        ORDER BY t.post_count DESC, t.id
        """,
        (*types, min_post_count),
    ).fetchall()
    if limit:
        rows = rows[:limit]
    total = len(rows)
    lock = threading.Lock()
    state = {"done": 0, "failed": 0, "tokens_in": 0, "tokens_out": 0}

    def run_one(row: sqlite3.Row) -> None:
        user = f"标签: {row['name']}\n中文名: {row['cn_name'] or '无'}\nWiki摘录: {(row['wiki'] or '')[:1200] or '无'}"
        text, tokens, _cached = llm.chat(
            [{"role": "system", "content": ENRICH_SYSTEM}, {"role": "user", "content": user}]
        )
        normalized = _normalize(_extract_json(text))
        with lock:
            connection.execute(
                """
                INSERT OR REPLACE INTO tag_enrichment(
                    tag_id, cn_definition, terms_json, facets_json, model, generated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    row["id"],
                    normalized["cn_definition"],
                    json.dumps(normalized["terms"], ensure_ascii=False),
                    json.dumps(normalized["facets"], ensure_ascii=False),
                    llm.model,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            state["tokens_in"] += tokens.get("in", 0) if isinstance(tokens, dict) else 0
            state["tokens_out"] += tokens.get("out", 0) if isinstance(tokens, dict) else 0

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(run_one, row): row for row in rows}
            for future in concurrent.futures.as_completed(futures):
                try:
                    future.result()
                except Exception as error:  # noqa: BLE001 - per-tag isolation
                    state["failed"] += 1
                    if progress:
                        progress(state["done"], total, f"fail {futures[future]['name']}: {error}")
                state["done"] += 1
                if state["done"] % 25 == 0 or state["done"] == total:
                    connection.commit()
                    if progress:
                        progress(state["done"], total, "")
        connection.commit()
        return {
            "candidates": total,
            "enriched": total - state["failed"],
            "failed": state["failed"],
            "tokens_in": state["tokens_in"],
            "tokens_out": state["tokens_out"],
        }
    finally:
        connection.close()
