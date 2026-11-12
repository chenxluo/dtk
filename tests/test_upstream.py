import tempfile
import unittest
from pathlib import Path

from dtk.search import get_tag
from dtk.store import build_database, open_db
from dtk.upstream import import_implications, upstream_status
from tests.fixtures import make_source


def _page(rows, start_id=1):
    return [
        {
            "id": row_id,
            "antecedent_name": antecedent,
            "consequent_name": consequent,
            "status": "active",
            "created_at": "2026-11-12T00:00:00.000-05:00",
        }
        for row_id, (antecedent, consequent) in enumerate(rows, start_id)
    ]


class UpstreamTests(unittest.TestCase):
    def test_implications_import_is_paginated_and_queryable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.db"
            derived = Path(tmp) / "derived.db"
            make_source(source)
            build_database(source, derived)

            pages = [
                _page([("hair_band", "hair_ornament"), ("long_hair", "hair")]),
                _page([("looking_at_viewer", "eyes")], start_id=3),
            ]
            result = import_implications(
                derived,
                page_size=2,
                delay=0,
                fetch_json=lambda url, timeout=30.0: (
                    pages[0] if "page=2" not in url else pages[1]
                ),
            )
            self.assertEqual(result["imported"], 3)

            conn = open_db(derived)
            try:
                record = get_tag(conn, "hair_band")
                self.assertEqual(record["implications"], {"implies": ["hair_ornament"], "implied_by": []})
                info = {row[0]: row[1] for row in conn.execute("SELECT key, value FROM meta")}
                self.assertEqual(info["implication_count"], "3")
            finally:
                conn.close()

    def test_upstream_status_reports_how_far_snapshot_lags(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.db"
            derived = Path(tmp) / "derived.db"
            make_source(source)
            build_database(source, derived)

            def fake_fetch(url, timeout=30.0):
                if "/counts/posts.json" in url:
                    return {"counts": {"posts": 9_000_000}}
                return [{"id": 20, "created_at": "2026-11-12T00:00:00.000-05:00"}]

            conn = open_db(derived)
            try:
                status = upstream_status(conn, fetch_json=fake_fetch)
            finally:
                conn.close()
            self.assertEqual(status["upstream_latest_post_id"], 20)
            self.assertEqual(status["snapshot_latest_post_id"], 12)
            self.assertEqual(status["posts_behind"], 8)
            self.assertEqual(status["upstream_post_total"], 9_000_000)
