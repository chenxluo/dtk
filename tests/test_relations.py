import sqlite3
import tempfile
import unittest
from pathlib import Path

from dtk.relations import cooccurring_tags
from dtk.store import build_database, open_db


class RelationTests(unittest.TestCase):
    def test_cooccurrence_uses_lift_to_downrank_ubiquitous_tags(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.db"
            derived = root / "derived.db"
            conn = sqlite3.connect(source)
            conn.executescript(
                """
                CREATE TABLE tag_types (id INTEGER PRIMARY KEY, name TEXT);
                INSERT INTO tag_types VALUES (1, 'general');
                CREATE TABLE tags (id INTEGER PRIMARY KEY, tag TEXT, type_id INTEGER, category_id INTEGER, post_count INTEGER);
                INSERT INTO tags VALUES
                    (1, 'looking_at_viewer', 1, NULL, 100),
                    (2, '1girl', 1, NULL, 1000),
                    (3, 'wind_lift', 1, NULL, 100),
                    (4, 'smile', 1, NULL, 500);
                CREATE TABLE tag_wiki (id INTEGER PRIMARY KEY, tag_id INTEGER, wiki_content TEXT);
                CREATE TABLE tag_aliases (id INTEGER PRIMARY KEY, tag_id INTEGER, alias TEXT);
                CREATE TABLE main_table (post_id INTEGER PRIMARY KEY, created_at TEXT, updated_at TEXT);
                CREATE TABLE post_tags (post_id INTEGER, tag_id INTEGER);
                CREATE INDEX idx_post_tags_post_id ON post_tags(post_id);
                CREATE INDEX idx_post_tags_tag_id ON post_tags(tag_id);
                """
            )
            conn.executemany(
                "INSERT INTO main_table VALUES (?, '2026-01-01', '2026-01-01')",
                [(post_id,) for post_id in range(1, 1001)],
            )
            conn.executemany(
                "INSERT INTO post_tags VALUES (?, ?)",
                [(post_id, 1) for post_id in range(1, 101)]
                + [(post_id, 2) for post_id in range(1, 1001)]
                + [(post_id, 3) for post_id in range(1, 21)],
            )
            conn.commit()
            conn.close()
            build_database(source, derived)
            connection = open_db(derived)
            try:
                result = cooccurring_tags(
                    connection,
                    "looking_at_viewer",
                    limit=5,
                    min_count=20,
                    cache_dir=root / "cache",
                )
                cached = cooccurring_tags(
                    connection,
                    "looking_at_viewer",
                    limit=5,
                    min_count=20,
                    cache_dir=root / "cache",
                )
            finally:
                connection.close()

        self.assertEqual([item["tag"] for item in result["results"]], ["wind_lift", "1girl"])
        self.assertEqual(result["results"][0]["lift"], 2.0)
        self.assertEqual(result["results"][1]["lift"], 1.0)
        self.assertEqual(result["matched_by"], "cooccurrence")
        self.assertTrue(cached["cache_hit"])


if __name__ == "__main__":
    unittest.main()
