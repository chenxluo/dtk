"""Read-only Tag lookup and retrieval."""
from __future__ import annotations

import json
import math
import re
import sqlite3

import numpy as np

from .vectors import VectorIndex


# ponytail: fixed usage prior tuned on eval/benchmark.jsonl; make it a CLI
# argument only if a future model/index needs a different value.
POPULARITY_PRIOR = 0.01
RESEARCH_VECTOR_THRESHOLD = 0.65



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
        "cn_name": row["cn_name"] if "cn_name" in row.keys() else None,
        "wiki_excerpt": _excerpt(row["wiki"]),
    }
    result.update(extra)
    return result


def _fts_rows(
    conn: sqlite3.Connection, expression: str, limit: int
) -> list[sqlite3.Row]:
    # column weights: name/cn_name strongest, aliases/other_names medium, wiki weak
    return conn.execute(
        """
        SELECT
            t.id,
            t.name,
            t.type,
            t.post_count,
            t.wiki,
            t.cn_name,
            bm25(tag_fts, 4.0, 4.0, 2.0, 2.0, 3.0, 0.2) AS bm25_score
        FROM tag_fts
        JOIN tag_details t ON t.id = tag_fts.tag_id
        WHERE tag_fts MATCH ?
        ORDER BY bm25_score, t.post_count DESC, t.name
        LIMIT ?
        """,
        (expression, limit),
    ).fetchall()


def _tags_by_ids(conn: sqlite3.Connection, tag_ids: list[int]) -> dict[int, sqlite3.Row]:
    if not tag_ids:
        return {}
    placeholders = ",".join("?" for _ in tag_ids)
    rows = conn.execute(
        f"""
        SELECT id, name, type, post_count, wiki, cn_name
        FROM tag_details
        WHERE id IN ({placeholders})
        """,
        tag_ids,
    ).fetchall()
    by_id = {row["id"]: row for row in rows}
    missing = set(tag_ids) - set(by_id)
    if missing:
        raise ValueError(f"vector index references unknown tag IDs: {sorted(missing)}")
    return by_id


def search(
    conn: sqlite3.Connection,
    query: str,
    limit: int = 10,
    vector_index: VectorIndex | None = None,
    query_vector: np.ndarray | None = None,
    research_index: VectorIndex | None = None,
    intent_parser: Any | None = None,
) -> dict[str, Any]:
    """Resolve exact names, aliases, and semantic text to Canonical Tags."""
    query = query.strip()
    if not query:
        raise ValueError("query must not be empty")
    if query_vector is None and (vector_index is not None or research_index is not None):
        raise ValueError("query_vector is required when a vector index is provided")

    lookup_key = normalize_name(query)
    row = conn.execute(
        """
        SELECT id, name, type, post_count, wiki, cn_name
        FROM tag_details
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
            t.cn_name,
            (
                SELECT min(a.alias)
                FROM aliases a
                WHERE a.lookup_key = ? AND a.tag_id = t.id
            ) AS matched_alias
        FROM tag_details t
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

    if intent_parser is not None and intent_parser.classify(query) == "no_match":
        return {"query": query, "matched_by": "no_match", "intent": "no_match", "results": []}

    candidate_limit = max(limit * 10, 100)
    expression = _fts_expression(query)
    full_text_rows = _fts_rows(conn, expression, candidate_limit) if expression else []
    vector_hits = []
    if research_index is not None:
        vector_hits.extend(
            (tag_id, score)
            for tag_id, score in research_index.find(query_vector, 10)
            if score >= RESEARCH_VECTOR_THRESHOLD
        )
    if vector_index is not None:
        vector_hits.extend(vector_index.find(query_vector, candidate_limit))
    if not full_text_rows and not vector_hits:
        return {"query": query, "matched_by": "no_match", "results": []}

    if not vector_hits:
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
                for rank, row in enumerate(full_text_rows[:limit], 1)
            ],
        }

    rrf_scores: dict[int, float] = {}
    fts_by_id: dict[int, sqlite3.Row] = {}
    vector_score_by_id: dict[int, float] = {}
    for rank, fts_row in enumerate(full_text_rows, 1):
        tag_id = fts_row["id"]
        fts_by_id[tag_id] = fts_row
        rrf_scores[tag_id] = rrf_scores.get(tag_id, 0.0) + 1.0 / (60 + rank)
    for rank, (tag_id, score) in enumerate(vector_hits, 1):
        vector_score_by_id[tag_id] = max(vector_score_by_id.get(tag_id, score), score)
        rrf_scores[tag_id] = rrf_scores.get(tag_id, 0.0) + 1.0 / (60 + rank)

    rows = _tags_by_ids(conn, list(rrf_scores))
    max_post_count = max(row["post_count"] for row in rows.values())
    rank_scores = {
        tag_id: score
        + POPULARITY_PRIOR
        * math.log10(rows[tag_id]["post_count"] + 1)
        / math.log10(max_post_count + 1)
        for tag_id, score in rrf_scores.items()
    }
    ordered_ids = sorted(
        rank_scores,
        key=lambda tag_id: (
            -rank_scores[tag_id],
            -vector_score_by_id.get(tag_id, -1.0),
            tag_id,
        ),
    )[:limit]
    results: list[dict[str, Any]] = []
    for rank, tag_id in enumerate(ordered_ids, 1):
        candidate_row = rows[tag_id]
        in_fts = tag_id in fts_by_id
        in_vector = tag_id in vector_score_by_id
        matched_by = "hybrid" if in_fts and in_vector else ("vector" if in_vector else "full_text")
        extra: dict[str, Any] = {"match_score": rank_scores[tag_id]}
        if in_fts:
            extra["bm25"] = fts_by_id[tag_id]["bm25_score"]
        if in_vector:
            extra["vector_score"] = vector_score_by_id[tag_id]
        results.append(_result(candidate_row, matched_by, rank, **extra))

    source = "hybrid" if fts_by_id and vector_score_by_id else "vector"
    return {"query": query, "matched_by": source, "results": results}

def split_concepts(query: str, max_concepts: int = 8) -> list[str]:
    """Split explicitly separated query concepts without linguistic guessing."""
    concepts = [part.strip() for part in re.split(r"[，,、;；\r\n]+", query) if part.strip()]
    if len(concepts) > max_concepts:
        raise ValueError(f"a query can contain at most {max_concepts} concepts")
    return concepts or [query.strip()]


def search_grouped(
    conn: sqlite3.Connection,
    query: str,
    limit: int = 10,
    vector_index: VectorIndex | None = None,
    query_vectors: Mapping[str, np.ndarray] | None = None,
    research_index: VectorIndex | None = None,
    intent_parser: Any | None = None,
) -> dict[str, Any]:
    """Search explicitly separated concepts independently and preserve grouping."""
    concepts = split_concepts(query)
    groups = [
        search(
            conn,
            concept,
            limit=limit,
            vector_index=vector_index,
            research_index=research_index,
            intent_parser=intent_parser,
            query_vector=query_vectors.get(concept) if query_vectors else None,
        )
        for concept in concepts
    ]
    return {
        "query": query,
        "matched_by": "grouped",
        "groups": groups,
    }



def _research_record(conn: sqlite3.Connection, tag_id: int) -> dict[str, Any] | None:
    exists = conn.execute(
        """
        SELECT 1 FROM sqlite_master
        WHERE type = 'table' AND name = 'research_publications'
        """
    ).fetchone()
    if exists is None:
        return None
    publication = conn.execute(
        """
        SELECT definition_zh, source_model_generated_at, published_at
        FROM research_publications WHERE tag_id = ?
        """,
        (tag_id,),
    ).fetchone()
    if publication is None:
        return None
    documents = [
        {"kind": row[0], "language": row[1], "text": row[2]}
        for row in conn.execute(
            """
            SELECT kind, language, text
            FROM research_documents WHERE tag_id = ?
            ORDER BY kind, language, text
            """,
            (tag_id,),
        )
    ]
    evidence = [
        {"url": row[0], "quote": row[1]}
        for row in conn.execute(
            """
            SELECT url, quote
            FROM research_evidence WHERE tag_id = ?
            ORDER BY url, quote
            """,
            (tag_id,),
        )
    ]
    return {
        "definition_zh": publication[0],
        "review_status": "published",
        "source_model_generated_at": publication[1],
        "published_at": publication[2],
        "documents": documents,
        "evidence": evidence,
    }


def _implication_record(conn: sqlite3.Connection, name: str) -> dict[str, Any] | None:
    exists = conn.execute(
        """
        SELECT 1 FROM sqlite_master
        WHERE type = 'table' AND name = 'tag_implications'
        """
    ).fetchone()
    if exists is None:
        return None
    outgoing = [
        row[0]
        for row in conn.execute(
            """
            SELECT consequent FROM tag_implications
            WHERE antecedent = ? AND status = 'active'
            ORDER BY consequent
            """,
            (name,),
        )
    ]
    incoming = [
        row[0]
        for row in conn.execute(
            """
            SELECT antecedent FROM tag_implications
            WHERE consequent = ? AND status = 'active'
            ORDER BY antecedent
            """,
            (name,),
        )
    ]
    if not outgoing and not incoming:
        return None
    return {"implies": outgoing, "implied_by": incoming}

def get_tag(conn: sqlite3.Connection, name: str) -> dict[str, Any] | None:
    """Return one canonical Tag record, or None when it does not exist."""
    row = conn.execute(
        """
        SELECT id, name, type, post_count, wiki, wiki_source, cn_name,
               other_names_json, cn_definition, terms_json
        FROM tag_details
        WHERE lookup_key = ?
        """,
        (normalize_name(name),),
    ).fetchone()
    if row is None:
        return None

    aliases = [
        item["alias"]
        for item in conn.execute(
            "SELECT alias FROM aliases WHERE tag_id = ? ORDER BY alias",
            (row["id"],),
        )
    ]
    try:
        other_names = json.loads(row["other_names_json"] or "[]")
    except (TypeError, ValueError):
        other_names = []
    try:
        terms = json.loads(row["terms_json"] or "[]")
    except (TypeError, ValueError):
        terms = []
    return {
        "tag": row["name"],
        "tag_id": row["id"],
        "type": row["type"],
        "post_count": row["post_count"],
        "wiki": row["wiki"] or "",
        "wiki_excerpt": _excerpt(row["wiki"]),
        "wiki_source": row["wiki_source"],
        "cn_name": row["cn_name"],
        "cn_definition": row["cn_definition"],
        "aliases": aliases,
        "other_names": other_names,
        "search_terms": terms,
        "research": _research_record(conn, row["id"]),
        "implications": _implication_record(conn, row["name"]),
    }
