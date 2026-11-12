"""Guard: every CLI command's external entry points must be importable.

Regression: an import line overwritten during edits left `run_research`
undefined and the research command crashed at call time (night 2026-11-12).
mock.patch fails loudly when a symbol is missing from dtk.cli.
"""
import unittest
from unittest import mock

from dtk import cli


class CliDispatchSmokeTests(unittest.TestCase):
    def test_research_command_dispatches_to_engine(self) -> None:
        with mock.patch("dtk.cli.run_research", return_value={}) as engine:
            code = cli.main(
                [
                    "research",
                    "--tag",
                    "looking_at_viewer",
                    "--search-provider",
                    "parallel-mcp",
                    "--dry-run",
                ]
            )
        self.assertEqual(code, 0)
        # dry-run returns before the engine; the patch proves the symbol exists
        self.assertFalse(engine.called)

    def test_enrich_command_symbol_exists(self) -> None:
        with mock.patch("dtk.cli.enrich_tags", return_value={"enriched": 0, "failed": 0, "candidates": 0, "tokens_in": 0, "tokens_out": 0}) as enrich:
            code = cli.main(["enrich", "--limit", "0", "--json"])
        self.assertEqual(code, 0)
        self.assertTrue(enrich.called)

    def test_wiki_import_symbol_exists(self) -> None:
        with mock.patch("dtk.cli.import_wikis", return_value={"candidates": 0, "hits": 0, "misses": 0}) as imp:
            code = cli.main(["import-wikis", "--json"])
        self.assertEqual(code, 0)
        self.assertTrue(imp.called)

    def test_translations_import_symbol_exists(self) -> None:
        with mock.patch("dtk.cli.import_translations", return_value={"source_rows": 0, "matched": 0, "label": "x"}) as imp:
            with mock.patch("dtk.cli.Path"):
                code = cli.main(["import-translations", "--json"])
        self.assertEqual(code, 0)
