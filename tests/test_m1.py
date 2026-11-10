import json
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import numpy as np

from dtk.evaluation import evaluate_file
from dtk.search import get_tag, search
from dtk.store import build_database, open_db
from dtk.vectors import VectorIndex, build_vector_index
from tests.fixtures import make_source



class ImportAndSearchTests(unittest.TestCase):
    @contextmanager
    def derived_store(self) -> Iterator[sqlite3.Connection]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.db"
            derived = root / "derived.db"
            make_source(source)
            build_database(source, derived)
            conn = open_db(derived)
            try:
                yield conn
            finally:
                conn.close()

    def test_space_and_case_tolerant_exact_lookup(self) -> None:
        with self.derived_store() as conn:
            result = search(conn, "Looking At Viewer")

        self.assertEqual(result["matched_by"], "exact_name")
        self.assertEqual(len(result["results"]), 1)
        self.assertEqual(result["results"][0]["tag"], "looking_at_viewer")
        self.assertEqual(result["results"][0]["type"], "general")
        self.assertEqual(result["results"][0]["post_count"], 100000)

    def test_ambiguous_alias_returns_every_candidate_by_usage(self) -> None:
        with self.derived_store() as conn:
            result = search(conn, "hair accessory")

        self.assertEqual(result["matched_by"], "alias")
        self.assertEqual(
            [item["tag"] for item in result["results"]],
            ["hair_ornament", "hair_band"],
        )
        self.assertTrue(all(item["matched_by"] == "alias" for item in result["results"]))

    def test_full_text_query_finds_tag_from_wiki_wording(self) -> None:
        with self.derived_store() as conn:
            result = search(conn, "character directly viewer", limit=3)

        self.assertEqual(result["matched_by"], "full_text")
        self.assertIn("looking_at_viewer", [item["tag"] for item in result["results"]])
        self.assertGreaterEqual(result["results"][0]["match_score"], 0.0)

    def test_vector_recall_promotes_correct_tag_over_fts_noise(self) -> None:
        index = VectorIndex(
            tag_ids=np.array([1, 2, 3, 4, 5], dtype=np.int64),
            matrix=np.array(
                [
                    [1.0, 0.0],
                    [0.0, 1.0],
                    [0.0, 1.0],
                    [0.0, 1.0],
                    [0.0, 1.0],
                ],
                dtype=np.float32,
            ),
        )
        with self.derived_store() as conn:
            result = search(
                conn,
                "character directly viewer",
                limit=3,
                vector_index=index,
                query_vector=np.array([0.99, 0.02], dtype=np.float32),
            )

        self.assertEqual(result["matched_by"], "hybrid")
        self.assertEqual(result["results"][0]["tag"], "looking_at_viewer")
        self.assertEqual(result["results"][0]["matched_by"], "hybrid")

    def test_vector_recall_handles_query_language_without_fts_match(self) -> None:
        index = VectorIndex(
            tag_ids=np.array([1, 2], dtype=np.int64),
            matrix=np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32),
        )
        with self.derived_store() as conn:
            result = search(
                conn,
                "角色直视观众",
                limit=3,
                vector_index=index,
                query_vector=np.array([1.0, 0.0], dtype=np.float32),
            )

        self.assertEqual(result["matched_by"], "vector")
        self.assertEqual(result["results"][0]["tag"], "looking_at_viewer")
        self.assertGreater(result["results"][0]["vector_score"], 0.9)

    def test_build_vector_index_persists_searchable_metadata(self) -> None:
        class FixedClient:
            model = "fixed-embedding"

            def embed(self, texts):
                vectors = np.array(
                    [[1.0, 0.1] if "viewer" in text else [0.1, 1.0] for text in texts],
                    dtype=np.float32,
                )
                return vectors / np.linalg.norm(vectors, axis=1)[:, None]

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.db"
            derived = Path(tmp) / "derived.db"
            vectors = Path(tmp) / "vectors.npz"
            make_source(source)
            build_database(source, derived)
            manifest = build_vector_index(
                derived, vectors, FixedClient(), batch_size=2, min_post_count=100
            )
            index = VectorIndex.load(vectors)
            hits = index.find(np.array([1.0, 0.1], dtype=np.float32), limit=1)

        self.assertEqual(manifest["tag_count"], 5)
        self.assertEqual(manifest["model"], "fixed-embedding")
        self.assertEqual(index.metadata["dimensions"], 2)
        self.assertEqual(hits[0][1] > 0.99, True)

    def test_get_tag_returns_wiki_and_aliases(self) -> None:
        with self.derived_store() as conn:
            record = get_tag(conn, "long_hair")

        self.assertIsNotNone(record)
        self.assertEqual(record["tag"], "long_hair")
        self.assertIn("very long hair", record["aliases"])
        self.assertTrue(record["wiki"].startswith("Hair that extends"))

    def test_research_vector_index_improves_semantic_recall(self) -> None:
        official = VectorIndex(
            tag_ids=np.array([2], dtype=np.int64),
            matrix=np.array([[0.0, 1.0]], dtype=np.float32),
        )
        research = VectorIndex(
            tag_ids=np.array([1], dtype=np.int64),
            matrix=np.array([[1.0, 0.0]], dtype=np.float32),
        )
        with self.derived_store() as conn:
            result = search(
                conn,
                "眨眼并张嘴笑",
                limit=3,
                vector_index=official,
                query_vector=np.array([1.0, 0.0], dtype=np.float32),
                research_index=research,
            )

        self.assertEqual(result["matched_by"], "vector")
        self.assertEqual(result["results"][0]["tag"], "looking_at_viewer")
        self.assertGreater(result["results"][0]["vector_score"], 0.99)

    def test_low_similarity_research_vectors_do_not_pollute_results(self) -> None:
        official = VectorIndex(
            tag_ids=np.array([2], dtype=np.int64),
            matrix=np.array([[0.0, 1.0]], dtype=np.float32),
        )
        research = VectorIndex(
            tag_ids=np.array([1], dtype=np.int64),
            matrix=np.array([[1.0, 0.0]], dtype=np.float32),
        )
        with self.derived_store() as conn:
            result = search(
                conn,
                "无关的视觉描述",
                limit=3,
                vector_index=official,
                query_vector=np.array([0.0, 1.0], dtype=np.float32),
                research_index=research,
            )

        self.assertEqual(result["results"][0]["tag"], "long_hair")
        self.assertNotIn("looking_at_viewer", [item["tag"] for item in result["results"]])

    def test_optional_intent_gate_rejects_nonvisual_query(self) -> None:
        class NoVisualIntent:
            def classify(self, query):
                return "no_match"

        with self.derived_store() as conn:
            rejected = search(conn, "zzqq_vorpal_nonexistent_tag", intent_parser=NoVisualIntent())
            exact = search(conn, "looking_at_viewer", intent_parser=NoVisualIntent())

        self.assertEqual(rejected["matched_by"], "no_match")
        self.assertEqual(rejected["results"], [])
        self.assertEqual(exact["matched_by"], "exact_name")
        self.assertEqual(exact["results"][0]["tag"], "looking_at_viewer")

    def test_no_match_benchmark_is_scored_separately(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            benchmark = Path(tmp) / "benchmark.jsonl"
            benchmark.write_text(
                json.dumps(
                    {
                        "id": "nonsense-001",
                        "query": "zzqq_vorpal_nonexistent_tag",
                        "expected": [],
                        "category": "no_match",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            with self.derived_store() as conn:
                metrics, failures = evaluate_file(conn, benchmark, ks=(1, 5))

        self.assertEqual(metrics["queries"], 1)
        self.assertEqual(metrics["retrieval_queries"], 0)
        self.assertEqual(metrics["no_match_total"], 1)
        self.assertEqual(metrics["no_match_correct"], 1)
        self.assertEqual(metrics["no_match_accuracy"], 1.0)
        self.assertEqual(failures, [])

    def test_benchmark_evaluation_reports_hits_and_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            benchmark = Path(tmp) / "benchmark.jsonl"
            benchmark.write_text(
                json.dumps(
                    {
                        "id": "viewer-001",
                        "query": "character directly viewer",
                        "expected": ["looking_at_viewer"],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            with self.derived_store() as conn:
                metrics, failures = evaluate_file(conn, benchmark, ks=(1, 5))

        self.assertEqual(metrics["queries"], 1)
        self.assertEqual(metrics["hit@1"], 1.0)
        self.assertEqual(metrics["mrr"], 1.0)
        self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main()
