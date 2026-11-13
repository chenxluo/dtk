import http.client
import json
import tempfile
import threading
import unittest
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
        )
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temporary.cleanup()

    def request(self, path: str) -> tuple[int, dict | str]:
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        connection.request("GET", path)
        response = connection.getresponse()
        body = response.read().decode("utf-8")
        connection.close()
        return response.status, body

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

    def test_home_page_script_parses(self) -> None:
        """One JS syntax error kills every listener (stuck '正在连接本地知识库…')."""
        import re
        import shutil
        import subprocess

        if shutil.which("node") is None:
            self.skipTest("node not available")
        _, body = self.request("/")
        script = re.search(r"<script>(.*)</script>", body, re.S).group(1)
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as handle:
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


if __name__ == "__main__":
    unittest.main()
