"""Retrieval benchmark evaluation for Semantic Retrieval."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from .search import search
from .vectors import VectorIndex


def load_benchmark(path: str | Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    with Path(path).open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            entry = json.loads(line)
            missing = {"id", "query", "expected"} - entry.keys()
            if missing:
                raise ValueError(f"{path}:{line_number} missing {sorted(missing)}")
            if not isinstance(entry["expected"], list):
                raise ValueError(f"{path}:{line_number} expected must be a list")
            entries.append(entry)
    return entries


def evaluate_file(
    conn: sqlite3.Connection,
    path: str | Path,
    ks: tuple[int, ...] = (5, 10),
    vector_index: VectorIndex | None = None,
    research_index: VectorIndex | None = None,
    query_vectors: Mapping[str, np.ndarray] | None = None,
    intent_parser: Any | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Evaluate benchmark queries through the public search seam."""
    entries = load_benchmark(path)
    max_k = max(ks, default=1)
    hit_counts = {k: 0 for k in ks}
    recall_totals = {k: 0.0 for k in ks}
    matched_by: dict[str, int] = {}
    reciprocal_ranks: list[float] = []
    failures: list[dict[str, Any]] = []

    retrieval_count = 0
    no_match_total = 0
    no_match_correct = 0


    for entry in entries:
        query_vector = query_vectors.get(entry["query"]) if query_vectors else None
        if (vector_index is not None or research_index is not None) and query_vector is None:
            raise ValueError(f"missing query vector for benchmark id {entry['id']}")
        result = search(
            conn,
            entry["query"],
            limit=max_k,
            vector_index=vector_index,
            research_index=research_index,
            intent_parser=intent_parser,
            query_vector=query_vector,
        )
        matched_by[result["matched_by"]] = matched_by.get(result["matched_by"], 0) + 1
        returned = [item["tag"] for item in result["results"]]
        expected = set(entry["expected"])

        if not expected:
            no_match_total += 1
            if not returned:
                no_match_correct += 1
            else:
                failures.append(
                    {
                        "id": entry["id"],
                        "query": entry["query"],
                        "expected": [],
                        "returned": returned,
                        "matched_by": result["matched_by"],
                    }
                )
            continue

        retrieval_count += 1
        first_rank = next(
            (rank for rank, tag in enumerate(returned, 1) if tag in expected), None
        )
        reciprocal_ranks.append(0.0 if first_rank is None else 1.0 / first_rank)

        for k in ks:
            top = set(returned[:k])
            if top & expected:
                hit_counts[k] += 1
            recall_totals[k] += len(top & expected) / len(expected)

        if not set(returned) & expected:
            failures.append(
                {
                    "id": entry["id"],
                    "query": entry["query"],
                    "expected": entry["expected"],
                    "returned": returned,
                    "matched_by": result["matched_by"],
                }
            )

    metrics: dict[str, Any] = {
        "queries": len(entries),
        "retrieval_queries": retrieval_count,
        "no_match_total": no_match_total,
        "no_match_correct": no_match_correct,
        "no_match_accuracy": no_match_correct / no_match_total if no_match_total else 1.0,
        "mrr": sum(reciprocal_ranks) / retrieval_count if retrieval_count else 0.0,
        "matched_by": matched_by,
    }
    for k in ks:
        metrics[f"hit@{k}"] = hit_counts[k] / retrieval_count if retrieval_count else 0.0
        metrics[f"recall@{k}"] = recall_totals[k] / retrieval_count if retrieval_count else 0.0
    return metrics, failures
