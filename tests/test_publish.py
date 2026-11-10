import tempfile
import numpy as np
import unittest
from pathlib import Path

from dtk.research.publish import publish_research
from dtk.search import get_tag
from dtk.store import build_database, open_db
from tests.fixtures import make_source


def completed_record(tag_id: int, tag: str, quote: str) -> dict:
    return {
        "key": tag,
        "tag_id": tag_id,
        "tag": tag,
        "status": "completed",
        "definition_zh": "这是一个用于发布测试的中文定义，描述该标签的视觉含义。",
        "semantic_documents": [
            {"kind": "visual", "language": "zh", "text": "画面中出现该标签描述的视觉特征。"}
        ],
        "evidence": [{"url": "https://example.com/tag", "quote": quote}],
        "evidence_verified": True,
        "evidence_pages": [
            {"url": "https://example.com/tag", "text": f"Page body. {quote} More text."}
        ],
        "unresolved_questions": [],
        "model_generated_at": "2026-11-11T00:00:00+00:00",
    }


class PublishResearchTests(unittest.TestCase):
    def test_accepted_completed_candidate_is_visible_and_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.db"
            derived = root / "derived.db"
            make_source(source)
            build_database(source, derived)
            record = completed_record(4, "hair_ornament", "A verbatim evidence sentence.")

            publish_research(derived, [record], accepted_tags={"hair_ornament"})
            publish_research(derived, [record], accepted_tags={"hair_ornament"})

            connection = open_db(derived)
            try:
                result = get_tag(connection, "hair_ornament")
            finally:
                connection.close()

        self.assertEqual(result["wiki"], "")  # official snapshot is never overwritten
        self.assertEqual(result["research"]["review_status"], "published")
        self.assertIn("视觉含义", result["research"]["definition_zh"])
        self.assertEqual(len(result["research"]["documents"]), 1)
        self.assertEqual(
            result["research"]["evidence"][0]["quote"],
            "A verbatim evidence sentence.",
        )

    def test_unaccepted_or_unverified_candidate_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.db"
            derived = root / "derived.db"
            make_source(source)
            build_database(source, derived)
            accepted = completed_record(4, "hair_ornament", "A verbatim evidence sentence.")
            unverified = completed_record(3, "hair_band", "Another sentence.")
            unverified["evidence_verified"] = False

            with self.assertRaisesRegex(ValueError, "not accepted for publication"):
                publish_research(derived, [accepted], accepted_tags=set())
            with self.assertRaisesRegex(ValueError, "evidence_verified"):
                publish_research(derived, [unverified], accepted_tags={"hair_band"})

            connection = open_db(derived)
            try:
                self.assertIsNone(get_tag(connection, "hair_ornament")["research"])
                self.assertIsNone(get_tag(connection, "hair_band")["research"])
            finally:
                connection.close()

    def test_published_documents_build_research_vector_index(self):
        class FixedEmbedding:
            model = "fixed-embedding"

            def embed(self, texts):
                vectors = np.array([[1.0, 0.1] for _ in texts], dtype=np.float32)
                return vectors / np.linalg.norm(vectors, axis=1)[:, None]

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.db"
            derived = root / "derived.db"
            vectors = root / "research.npz"
            make_source(source)
            build_database(source, derived)
            record = completed_record(4, "hair_ornament", "A verbatim evidence sentence.")
            publish_research(derived, [record], accepted_tags={"hair_ornament"})

            from dtk.vectors import VectorIndex, build_research_vector_index

            manifest = build_research_vector_index(derived, vectors, FixedEmbedding())
            index = VectorIndex.load(vectors)

        self.assertEqual(manifest["kind"], "published_research_documents_v1")
        self.assertEqual(manifest["document_count"], 1)
        self.assertEqual(manifest["tag_count"], 1)
        self.assertEqual(index.tag_ids.tolist(), [4])


if __name__ == "__main__":
    unittest.main()
