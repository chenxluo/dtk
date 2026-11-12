"""Behavior tests for additive upstream Wiki / translation merge into search."""
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from dtk.search import get_tag, search
from dtk.store import build_database, open_db, rebuild_search_index
from dtk.translations import import_translations
from tests.fixtures import make_source


class MergedIndexTests(unittest.TestCase):
    @contextmanager
    def derived_store(self) -> Iterator[tuple[sqlite3.Connection, Path]]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.db"
            derived = root / "derived.db"
            make_source(source)
            build_database(source, derived)
            # hair_band (id 3) has no snapshot wiki: simulate upstream fill
            conn = sqlite3.connect(derived)
            conn.execute(
                """
                INSERT INTO upstream_wikis(
                    tag_id, title, body, other_names_json, updated_at, fetched_at
                ) VALUES (3, 'hair_band', 'A ribbon worn in the hair.',
                          '["发带", "髪飾り"]', '2026-11-01T00:00:00Z',
                          '2026-11-12T00:00:00Z')
                """
            )
            conn.execute(
                """
                INSERT INTO tag_translations(tag_id, cn_name, source, fetched_at)
                VALUES (3, '发带', 'ffdkj', '2026-11-12T00:00:00Z')
                """
            )
            conn.commit()
            conn.close()
            rebuild_search_index(derived)
            conn = open_db(derived)
            try:
                yield conn, derived
            finally:
                conn.close()

    def test_get_tag_merges_upstream_wiki_and_translation(self) -> None:
        with self.derived_store() as (conn, _):
            record = get_tag(conn, "hair_band")
            self.assertEqual(record["wiki"], "A ribbon worn in the hair.")
            self.assertEqual(record["wiki_source"], "upstream")
            self.assertEqual(record["cn_name"], "发带")
            self.assertIn("发带", record["other_names"])
            # snapshot wiki still wins for tags without upstream rows
            self.assertEqual(get_tag(conn, "long_hair")["wiki_source"], "snapshot")

    def test_full_text_matches_chinese_name(self) -> None:
        with self.derived_store() as (conn, _):
            result = search(conn, "发带", limit=5)

        tags = [item["tag"] for item in result["results"]]
        self.assertIn("hair_band", tags)
        hair_band = next(item for item in result["results"] if item["tag"] == "hair_band")
        self.assertEqual(hair_band["cn_name"], "发带")

    def test_rebuild_index_is_idempotent(self) -> None:
        with self.derived_store() as (_, derived):
            first = rebuild_search_index(derived)
            second = rebuild_search_index(derived)

        self.assertEqual(first["fts_rows"], second["fts_rows"])

    def test_snapshot_only_build_has_empty_cn_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.db"
            derived = root / "derived.db"
            make_source(source)
            build_database(source, derived)
            conn = open_db(derived)
            try:
                record = get_tag(conn, "long_hair")
            finally:
                conn.close()

        self.assertIsNone(record["cn_name"])
        self.assertEqual(record["wiki_source"], "snapshot")


class TranslationImportTests(unittest.TestCase):
    def test_import_translations_matches_by_lookup_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.db"
            derived = root / "derived.db"
            ffdkj = root / "ffdkj.sqlite"
            make_source(source)
            build_database(source, derived)
            external = sqlite3.connect(ffdkj)
            external.executescript(
                """
                CREATE TABLE tag (
                    name TEXT PRIMARY KEY,
                    category INTEGER,
                    cn_name TEXT,
                    post_count INTEGER
                );
                INSERT INTO tag VALUES
                    ('hair_band', 0, '发带', 200),
                    ('Long Hair', 0, '长发', 50000),
                    ('not_in_store', 0, '不存在', 5),
                    ('no_translation', 0, NULL, 1);
                """
            )
            external.commit()
            external.close()

            result = import_translations(derived, ffdkj)
            conn = open_db(derived)
            try:
                record = get_tag(conn, "hair_band")
            finally:
                conn.close()

        self.assertEqual(result["matched"], 2)
        self.assertEqual(record["cn_name"], "发带")
