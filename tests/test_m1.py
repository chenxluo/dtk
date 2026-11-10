import json
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from dtk.evaluation import evaluate_file
from dtk.search import get_tag, search
from dtk.store import build_database, open_db


def make_source(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE tag_types (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
        INSERT INTO tag_types VALUES (1, 'general'), (4, 'artist');

        CREATE TABLE tags (
            id INTEGER PRIMARY KEY,
            tag TEXT NOT NULL,
            type_id INTEGER NOT NULL,
            category_id INTEGER,
            post_count INTEGER NOT NULL
        );
        INSERT INTO tags VALUES
            (1, 'looking_at_viewer', 1, NULL, 100000),
            (2, 'long_hair', 1, NULL, 50000),
            (3, 'hair_band', 1, NULL, 200),
            (4, 'hair_ornament', 1, NULL, 300);

        CREATE TABLE tag_wiki (id INTEGER PRIMARY KEY, tag_id INTEGER, wiki_content TEXT);
        INSERT INTO tag_wiki VALUES
            (1, 1, 'A character looks directly at the viewer.'),
            (2, 2, 'Hair that extends well past the shoulders.');

        CREATE TABLE tag_aliases (id INTEGER PRIMARY KEY, tag_id INTEGER, alias TEXT);
        INSERT INTO tag_aliases VALUES
            (1, 2, 'very long hair'),
            (2, 3, 'hair accessory'),
            (3, 4, 'Hair Accessory');

        CREATE TABLE main_table (post_id INTEGER PRIMARY KEY, created_at TEXT, updated_at TEXT);
        INSERT INTO main_table VALUES (12, '2026-08-23T10:00:00Z', '2026-08-23T10:00:00Z');
        """
    )
    conn.commit()
    conn.close()


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
        self.assertEqual(result["results"][0]["tag"], "looking_at_viewer")
        self.assertGreaterEqual(result["results"][0]["match_score"], 0.0)

    def test_get_tag_returns_wiki_and_aliases(self) -> None:
        with self.derived_store() as conn:
            record = get_tag(conn, "long_hair")

        self.assertIsNotNone(record)
        self.assertEqual(record["tag"], "long_hair")
        self.assertIn("very long hair", record["aliases"])
        self.assertTrue(record["wiki"].startswith("Hair that extends"))

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
