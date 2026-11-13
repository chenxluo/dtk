import http.client
import json
import os
import tempfile
import threading
import unittest
import time
from pathlib import Path

from dtk.store import build_database
from dtk.web import create_server
from tests.fixtures import make_source


class WebApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        source = root / "source.db"
        derived = root / "derived.db"
        make_source(source)
        build_database(source, derived)
        self.server = create_server(
            "127.0.0.1",
            0,
            db_path=derived,
            vectors_path=root / "missing.npz",
            research_output_path=root / "research.jsonl",
        )
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temporary.cleanup()

    def request(
        self, path: str, method: str = "GET", body: str | None = None
    ) -> tuple[int, str]:
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        connection.request(method, path, body=body)
        response = connection.getresponse()
        payload = response.read().decode("utf-8")
        connection.close()
        return response.status, payload

    def test_search_endpoint_uses_public_search_interface(self) -> None:
        status, body = self.request("/api/search?query=Looking%20At%20Viewer")
        payload = json.loads(body)

        self.assertEqual(status, 200)
        self.assertEqual(payload["matched_by"], "exact_name")
        self.assertEqual(payload["results"][0]["tag"], "looking_at_viewer")

    def test_multi_concept_query_returns_independent_groups(self) -> None:
        status, body = self.request(
            "/api/search?query=Looking%20At%20Viewer%EF%BC%8Cvery%20long%20hair"
        )
        payload = json.loads(body)

        self.assertEqual(status, 200)
        self.assertEqual(payload["matched_by"], "grouped")
        self.assertEqual(len(payload["groups"]), 2)
        self.assertEqual(payload["groups"][0]["results"][0]["tag"], "looking_at_viewer")
        self.assertEqual(payload["groups"][1]["results"][0]["tag"], "long_hair")

    def test_tag_endpoint_returns_published_research(self) -> None:
        from dtk.research.publish import publish_research
        from tests.test_publish import completed_record

        record = completed_record(4, "hair_ornament", "A verbatim evidence sentence.")
        publish_research(
            self.server.db_path,
            [record],
            accepted_tags={"hair_ornament"},
        )
        status, body = self.request("/api/tag?name=hair_ornament")
        payload = json.loads(body)

        self.assertEqual(status, 200)
        self.assertEqual(payload["research"]["review_status"], "published")
        self.assertIn("视觉含义", payload["research"]["definition_zh"])

    def test_home_page_serves_query_interface(self) -> None:
        status, body = self.request("/")

        self.assertEqual(status, 200)
        self.assertIn("Danbooru Tag Knowledge", body)
        self.assertIn("角色直视观众", body)

    def test_health_reports_embedding_config(self) -> None:
        status, body = self.request("/health")
        payload = json.loads(body)

        self.assertEqual(status, 200)
        self.assertFalse(payload["semantic"])
        self.assertIn("://", payload["embedding_base_url"])
        self.assertTrue(payload["embedding_model"])

    def test_home_page_script_parses(self) -> None:
        """One JS syntax error kills every listener (stuck '正在连接本地知识库…')."""
        import re
        import shutil
        import subprocess

        if shutil.which("node") is None:
            self.skipTest("node not available")
        self._assert_scripts_parse(self.request("/")[1])

    def test_lab_page_script_parses(self) -> None:
        import re
        import shutil
        import subprocess

        if shutil.which("node") is None:
            self.skipTest("node not available")
        self._assert_scripts_parse(self.request("/lab")[1])

    def _assert_scripts_parse(self, body: str) -> None:
        import re
        import tempfile
        import subprocess

        scripts = re.findall(r"<script>(.*?)</script>", body, re.S)
        self.assertGreaterEqual(len(scripts), 1)
        for script in scripts:
            with tempfile.NamedTemporaryFile(
                "w", suffix=".js", delete=False, encoding="utf-8"
            ) as handle:
                handle.write(script)
                script_path = handle.name
            try:
                completed = subprocess.run(
                    ["node", "--check", script_path], capture_output=True, text=True
                )
            finally:
                Path(script_path).unlink(missing_ok=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_search_degrades_to_fts_when_embedding_service_down(self) -> None:
        from dtk.vectors import EmbeddingClient
        from dtk.web import DTKWebServer

        class _StubIndex:
            metadata = {"model": "stub", "tag_count": 1}

        server = DTKWebServer(
            ("127.0.0.1", 0),
            self.server.db_path,
            _StubIndex(),
            None,
            EmbeddingClient("http://127.0.0.1:9", timeout=0.5),
            None,
            None,
            None,
        )
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
            connection.request("GET", "/api/search?query=very%20long%20hair")
            response = connection.getresponse()
            payload = json.loads(response.read().decode("utf-8"))
            connection.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

        self.assertEqual(response.status, 200)
        self.assertTrue(payload["degraded"])
        self.assertTrue(payload["results"])

    def test_config_get_masks_secrets_and_put_roundtrips(self) -> None:
        config_path = Path(self.temporary.name) / "dtk.json"
        previous = os.environ.get("DTK_CONFIG")
        os.environ["DTK_CONFIG"] = str(config_path)
        try:
            status, body = self.request(
                "/api/config",
                method="PUT",
                body=json.dumps({"embedding_api_key": "sk-test-12345"}),
            )
            self.assertEqual(status, 200)
            self.assertTrue(json.loads(body)["restart_required"])
            stored = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(stored["embedding_api_key"], "sk-test-12345")

            status, body = self.request("/api/config")
            payload = json.loads(body)
            self.assertEqual(status, 200)
            self.assertEqual(payload["embedding_api_key"], "••••2345")

            status, _ = self.request(
                "/api/config", method="PUT", body=json.dumps({"db_path": "x"})
            )
            self.assertEqual(status, 400)  # non-editable key rejected
        finally:
            if previous is None:
                os.environ.pop("DTK_CONFIG", None)
            else:
                os.environ["DTK_CONFIG"] = previous

    def test_lab_page_serves_and_links_back(self) -> None:
        status, body = self.request("/lab")
        self.assertEqual(status, 200)
        self.assertIn("DTK", body)
        self.assertIn("/api/stats", body)
        self.assertIn('href="/"', body)

    def test_shared_stylesheet_served(self) -> None:
        status, body = self.request("/assets/app.css")
        self.assertEqual(status, 200)
        self.assertIn("--color-paper", body)

    def test_stats_endpoint_reports_categories_and_assets(self) -> None:
        status, body = self.request("/api/stats")
        payload = json.loads(body)

        self.assertEqual(status, 200)
        types = {row["type"] for row in payload["categories"]}
        self.assertTrue(types, "fixture should expose at least one category")
        for row in payload["categories"]:
            self.assertGreaterEqual(row["with_wiki"], 0)
            self.assertLessEqual(row["with_wiki"], row["tags"])
            self.assertLessEqual(row["with_translation"], row["tags"])
        for key in ("aliases", "translations", "research_publications"):
            self.assertIn(key, payload["assets"])
        self.assertTrue(payload["files"])
        self.assertTrue(any(f["name"] == "dtk.db" for f in payload["files"]))

    def test_job_lifecycle_and_rejects_unknown_kind(self) -> None:
        import unittest.mock

        import dtk.web as web_module

        def _stub(server, params):  # noqa: ANN001 - matches job handler shape
            print("stub job ran")
            print(f"params: {params.get('note', '')}")

        with unittest.mock.patch.dict(web_module._JOB_HANDLERS, {"research": _stub}):
            status, body = self.request(
                "/api/jobs",
                method="POST",
                body=json.dumps({"kind": "research", "params": {"note": "hi"}}),
            )
            self.assertEqual(status, 201)
            job_id = json.loads(body)["id"]

            detail = {}
            for _ in range(100):
                status, body = self.request(f"/api/jobs/{job_id}")
                detail = json.loads(body)
                if detail["status"] != "running":
                    break
                time.sleep(0.05)
            self.assertEqual(detail["status"], "done")
            self.assertIn("stub job ran", detail["log"][-5:])

            status, body = self.request("/api/jobs")
            self.assertEqual(status, 200)
            self.assertTrue(any(j["id"] == job_id for j in json.loads(body)["jobs"]))

        status, _ = self.request(
            "/api/jobs", method="POST", body=json.dumps({"kind": "bogus"})
        )
        self.assertEqual(status, 400)

        status, _ = self.request("/api/jobs/does-not-exist")
        self.assertEqual(status, 404)

    def test_research_queue_lists_missing_wiki_tags(self) -> None:
        status, body = self.request("/api/research/queue?limit=5")
        payload = json.loads(body)
        self.assertEqual(status, 200)
        tags = [unit["tag"] for unit in payload["units"]]
        self.assertIn("hair_ornament", tags)  # fixture tag without wiki

    def test_research_candidates_missing_file(self) -> None:
        status, body = self.request("/api/research/candidates")
        payload = json.loads(body)
        self.assertEqual(status, 200)
        self.assertFalse(payload["exists"])
        self.assertEqual(payload["records"], [])

    def test_update_check_unconfigured_reports_current(self) -> None:
        previous = os.environ.get("DTK_CONFIG")
        config_path = Path(self.temporary.name) / "empty.json"
        config_path.write_text("{}", encoding="utf-8")
        os.environ["DTK_CONFIG"] = str(config_path)
        try:
            status, body = self.request("/api/update/check")
            payload = json.loads(body)
            self.assertEqual(status, 200)
            self.assertFalse(payload["configured"])
            self.assertRegex(payload["current"], r"data-v")
        finally:
            if previous is None:
                os.environ.pop("DTK_CONFIG", None)
            else:
                os.environ["DTK_CONFIG"] = previous

    def test_rebuild_fts_job_runs_to_done(self) -> None:
        status, body = self.request(
            "/api/jobs", method="POST", body=json.dumps({"kind": "rebuild_fts"})
        )
        self.assertEqual(status, 201)
        job_id = json.loads(body)["id"]
        detail = {}
        for _ in range(200):
            status, body = self.request(f"/api/jobs/{job_id}")
            detail = json.loads(body)
            if detail["status"] != "running":
                break
            time.sleep(0.05)
        self.assertEqual(detail["status"], "done", detail)


if __name__ == "__main__":
    unittest.main()
