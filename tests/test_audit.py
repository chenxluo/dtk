import tempfile
import unittest
from pathlib import Path

from dtk.research.audit import audit_research
from tests.test_publish import completed_record


class AuditResearchTests(unittest.TestCase):
    def test_audit_counts_records_tokens_and_bad_quotes(self):
        good = completed_record(4, "hair_ornament", "A verbatim evidence sentence.")
        bad = completed_record(3, "hair_band", "Not in the page.")
        bad["evidence_pages"][0]["text"] = "Different page text."

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "research.jsonl"
            path.write_text(
                json_line(good) + "\n" + json_line(bad) + "\n",
                encoding="utf-8",
            )
            result = audit_research(path)

        self.assertEqual(result["records"], 2)
        self.assertEqual(result["completed"], 2)
        self.assertEqual(result["unknown"], 0)
        self.assertEqual(result["invalid"], 1)
        self.assertEqual(result["tokens_in"], 0)
        self.assertEqual(result["tokens_out"], 0)
        self.assertEqual(result["issues"][0]["tag"], "hair_band")
        self.assertIn("quote", result["issues"][0]["errors"][0])


def json_line(record):
    import json

    return json.dumps(record, ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
