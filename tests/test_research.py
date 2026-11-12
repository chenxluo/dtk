import http.server
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from dtk.research.engine import run_research
from dtk.research.queue import build_research_queue
from dtk.research.tag_task import TagResearchTask
from dtk.store import build_database, open_db
from tests.fixtures import make_source


class ScriptedLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

    def chat(self, messages):
        content = self.replies[min(self.calls, len(self.replies) - 1)]
        self.calls += 1
        return content, {"in": 20, "out": 5}, False


class NoSearch:
    def search(self, query):
        return []


class FixedFetch:
    def __init__(self, text):
        self.text = text
        self.fetches = 0
        self.cache_hits = 0

    def fetch(self, url):
        self.fetches += 1
        return self.text


def completed_verdict(quote):
    return {
        "status": "completed",
        "definition_zh": "这是一个用于行为测试的中文定义，描述该标签的视觉含义。",
        "semantic_documents": [
            {"kind": "visual", "language": "zh", "text": "画面中出现测试标签描述的视觉特征。"}
        ],
        "evidence": [{"url": "https://example.com/tag", "quote": quote}],
        "unresolved_questions": [],
    }


class ResearchClientTests(unittest.TestCase):
    def test_searx_client_parses_json_results(self) -> None:
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = json.dumps(
                    {"results": [{"url": "https://example.com", "title": "Example", "content": "snippet"}]}
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):
                return

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            from dtk.research.searx import SearXNGClient

            client = SearXNGClient(
                f"http://127.0.0.1:{server.server_port}", qps=0
            )
            results = client.search("test query")
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

        self.assertEqual(results[0]["url"], "https://example.com")
        self.assertEqual(client.queries, 1)

    def test_parallel_client_posts_themed_search_and_caches(self) -> None:
        captured = {}
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                captured["path"] = self.path
                captured["key"] = self.headers.get("x-api-key")
                length = int(self.headers.get("Content-Length") or 0)
                captured["payload"] = json.loads(self.rfile.read(length))
                body = json.dumps(
                    {
                        "results": [
                            {
                                "url": "https://moegirl.org.cn/example",
                                "title": "萌娘百科条目",
                                "excerpts": ["第一段", "第二段"],
                            }
                        ]
                    }
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):
                return

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            from dtk.research.parallel import ParallelSearchClient

            client = ParallelSearchClient(
                "secret-key",
                base_url=f"http://127.0.0.1:{server.server_port}",
                qps=0,
                cache_dir=tmp.name,
            )
            results = client.search("oni_horns anime tag")
            again = client.search("oni_horns anime tag")
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

        self.assertEqual(captured["path"], "/v1/search")
        self.assertEqual(captured["key"], "secret-key")
        objective = captured["payload"]["objective"]
        self.assertIn("Danbooru", objective)
        self.assertIn("anime", objective)
        self.assertEqual(
            captured["payload"]["search_queries"], ["oni_horns anime tag"]
        )
        self.assertEqual(results[0]["url"], "https://moegirl.org.cn/example")
        self.assertEqual(results[0]["content"], "第一段\n第二段")
        self.assertEqual(client.queries, 2)
        self.assertEqual(client.cache_hits, 1)

    def test_fetcher_encodes_non_ascii_urls(self) -> None:
        from dtk.research.fetch import _encode_url

        self.assertEqual(
            _encode_url("https://zh.moegirl.org.cn/鬼角"),
            "https://zh.moegirl.org.cn/%E9%AC%BC%E8%A7%92",
        )
        self.assertEqual(
            _encode_url("https://e621.net/wiki_pages/oni_horns?page=2"),
            "https://e621.net/wiki_pages/oni_horns?page=2",
        )


    def test_parallel_mcp_client_searches_without_key(self) -> None:
        captured = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(length))
                captured.append(payload)

                def respond(body, session=None):
                    data = f"data: {json.dumps(body)}\n\n".encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Content-Length", str(len(data)))
                    if session:
                        self.send_header("Mcp-Session-Id", session)
                    self.end_headers()
                    self.wfile.write(data)

                if payload.get("method") == "initialize":
                    respond(
                        {"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}},
                        session="session-1",
                    )
                elif payload.get("method") == "tools/call":
                    respond(
                        {
                            "jsonrpc": "2.0",
                            "id": 2,
                            "result": {
                                "content": [
                                    {
                                        "type": "text",
                                        "text": json.dumps(
                                            {
                                                "results": [
                                                    {
                                                        "url": "https://danbooru.donmai.us/wiki_pages/x",
                                                        "title": "X Wiki",
                                                        "excerpts": ["ex1", "ex2"],
                                                    }
                                                ]
                                            }
                                        ),
                                    }
                                ]
                            },
                        }
                    )
                else:
                    respond({"jsonrpc": "2.0", "result": {}})

            def log_message(self, format, *args):
                return

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            from dtk.research.parallel import ParallelMCPSearchClient

            client = ParallelMCPSearchClient(
                endpoint=f"http://127.0.0.1:{server.server_port}/mcp", qps=0
            )
            results = client.search("oni horns anime tag")
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

        self.assertEqual(results[0]["url"], "https://danbooru.donmai.us/wiki_pages/x")
        self.assertEqual(results[0]["content"], "ex1\nex2")
        self.assertEqual(client.session_id, "session-1")
        methods = [payload.get("method") for payload in captured]
        self.assertIn("initialize", methods)
        self.assertIn("tools/call", methods)
        call = next(p for p in captured if p.get("method") == "tools/call")
        self.assertEqual(call["params"]["name"], "web_search")
        self.assertIn("Danbooru", call["params"]["arguments"]["objective"])
        self.assertEqual(
            call["params"]["arguments"]["search_queries"], ["oni horns anime tag"]
        )

    def test_llm_client_retries_rate_limit(self) -> None:
        state = {"calls": 0}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                state["calls"] += 1
                if state["calls"] == 1:
                    self.send_response(429)
                    self.end_headers()
                    return
                body = json.dumps(
                    {
                        "choices": [{"message": {"content": "{\"ok\":true}"}}],
                        "usage": {"prompt_tokens": 3, "completion_tokens": 2},
                    }
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):
                return

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            from dtk.research.llm import ResearchLLMClient

            client = ResearchLLMClient(
                f"http://127.0.0.1:{server.server_port}", "test-model"
            )
            content, usage, cached = client.chat([{"role": "user", "content": "hi"}])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

        self.assertEqual(content, '{"ok":true}')
        self.assertEqual(usage, {"in": 3, "out": 2})
        self.assertFalse(cached)
        self.assertEqual(state["calls"], 2)

    def test_llm_cache_does_not_cross_endpoints(self) -> None:
        state = {"calls": 0}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                state["calls"] += 1
                body = json.dumps(
                    {
                        "choices": [{"message": {"content": "{\"ok\":true}"}}],
                        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                    }
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):
                return

        servers = []
        threads = []
        try:
            for _ in range(2):
                server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
                thread = threading.Thread(target=server.serve_forever)
                thread.start()
                servers.append(server)
                threads.append(thread)
            with tempfile.TemporaryDirectory() as cache:
                from dtk.research.llm import ResearchLLMClient

                messages = [{"role": "user", "content": "hi"}]
                first = ResearchLLMClient(
                    f"http://127.0.0.1:{servers[0].server_port}",
                    "same-model",
                    cache_dir=cache,
                )
                second = ResearchLLMClient(
                    f"http://127.0.0.1:{servers[1].server_port}",
                    "same-model",
                    cache_dir=cache,
                )
                first.chat(messages)
                second.chat(messages)
        finally:
            for server, thread in zip(servers, threads):
                server.shutdown()
                server.server_close()
                thread.join()

        self.assertGreaterEqual(state["calls"], 2)


    def test_fetcher_extracts_html_and_uses_cache(self) -> None:
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = b"<html><script>ignore()</script><p>Visible evidence text.</p></html>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):
                return

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as cache:
                from dtk.research.fetch import Fetcher

                fetcher = Fetcher(cache_dir=cache, allow_private=True)
                url = f"http://127.0.0.1:{server.server_port}/tag"
                first = fetcher.fetch(url)
                second = fetcher.fetch(url)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

        self.assertEqual(first, "Visible evidence text.")
        self.assertEqual(second, first)
        self.assertEqual(fetcher.fetches, 2)
        self.assertEqual(fetcher.cache_hits, 1)

    def test_fetcher_rejects_localhost_without_request(self) -> None:
        from dtk.research.fetch import Fetcher

        self.assertIsNone(Fetcher().fetch("http://127.0.0.1:6001/v1/models"))

    def test_fetcher_rejects_private_and_metadata_addresses(self) -> None:
        from dtk.research.fetch import Fetcher

        fetcher = Fetcher()
        blocked = [
            "http://10.0.0.1/tag",
            "http://192.168.1.1/tag",
            "http://172.16.0.1/tag",
            "http://169.254.169.254/latest/meta-data/",
            "http://[::1]/tag",
        ]

        started = time.monotonic()
        results = [fetcher.fetch(url) for url in blocked]
        elapsed = time.monotonic() - started

        self.assertTrue(all(value is None for value in results))
        self.assertLess(elapsed, 1.0)

    def test_fetcher_allows_poisoned_dns_when_proxied(self) -> None:
        import socket
        from unittest import mock

        from dtk.research.fetch import Fetcher

        fetcher = Fetcher()
        poisoned = [(None, None, None, "", ("2001::dead:beef", 0))]
        proxies = {"https": "http://127.0.0.1:7890"}
        with mock.patch("urllib.request.getproxies", return_value=proxies), mock.patch(
            "socket.getaddrinfo", return_value=poisoned
        ):
            # the proxy resolves the hostname; local poisoned answers must not veto
            self.assertTrue(
                fetcher._url_allowed("https://danbooru.donmai.us/wiki_pages/x")
            )
            # literal private targets stay blocked even behind a proxy
            self.assertFalse(fetcher._url_allowed("https://192.168.1.1/tag"))

        with mock.patch("urllib.request.getproxies", return_value={}), mock.patch(
            "socket.getaddrinfo", return_value=poisoned
        ):
            # direct connection with a fully blocked resolution is rejected
            self.assertFalse(
                fetcher._url_allowed("https://danbooru.donmai.us/wiki_pages/x")
            )

    def test_fetcher_does_not_cache_transient_failure(self) -> None:
        state = {"calls": 0}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                state["calls"] += 1
                if state["calls"] == 1:
                    self.send_response(503)
                    self.end_headers()
                    return
                body = b"<html><p>Recovered evidence text.</p></html>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):
                return

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as cache:
                from dtk.research.fetch import Fetcher

                fetcher = Fetcher(cache_dir=cache, allow_private=True)
                url = f"http://127.0.0.1:{server.server_port}/tag"
                first = fetcher.fetch(url)
                second = fetcher.fetch(url)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

        self.assertIsNone(first)
        self.assertEqual(second, "Recovered evidence text.")
        self.assertEqual(fetcher.cache_hits, 0)

    def test_fetcher_does_not_cache_empty_page(self) -> None:
        state = {"calls": 0}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                state["calls"] += 1
                body = b"" if state["calls"] == 1 else b"<html><p>Later text.</p></html>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if body:
                    self.wfile.write(body)

            def log_message(self, format, *args):
                return

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as cache:
                from dtk.research.fetch import Fetcher

                fetcher = Fetcher(cache_dir=cache, allow_private=True)
                url = f"http://127.0.0.1:{server.server_port}/tag"
                first = fetcher.fetch(url)
                second = fetcher.fetch(url)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

        self.assertEqual(first, "")
        self.assertEqual(second, "Later text.")
        self.assertEqual(fetcher.cache_hits, 0)


class ResearchEngineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        source = root / "source.db"
        derived = root / "derived.db"
        make_source(source)
        build_database(source, derived)
        self.connection = open_db(derived)
        self.task = TagResearchTask()

    def test_unit_prompt_states_exact_output_field_shapes(self) -> None:
        unit = build_research_queue(self.connection, limit=1)[0]
        prompt = self.task.unit_prompt(unit)

        self.assertIn('"kind":"visual|synonym|contextual|disambiguation"', prompt)
        self.assertIn('"language":"zh|en"', prompt)
        self.assertIn('"text":"检索表达"', prompt)
        self.assertIn('"url":"已抓取URL"', prompt)
        self.assertIn('"quote":"页面正文连续原文"', prompt)

    def tearDown(self):
        self.connection.close()
        self.temporary.cleanup()

    def test_queue_selects_missing_wiki_by_usage(self):
        units = build_research_queue(self.connection, limit=3)

        self.assertEqual(
            [unit["tag"] for unit in units],
            ["hair_ornament", "hair_band"],
        )
        self.assertTrue(all(unit["wiki"] is None for unit in units))

    def test_queue_can_focus_on_general_tags(self):
        units = build_research_queue(self.connection, limit=5, types=("general",))

        self.assertEqual(
            [unit["tag"] for unit in units], ["hair_ornament", "hair_band"]
        )

    def test_queue_rejects_unknown_tag_type(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported research tag types"):
            build_research_queue(self.connection, types=("general", "artist"))

    def test_explicit_tag_bypasses_usage_threshold(self) -> None:
        from dtk.research.queue import explicit_research_units

        units = explicit_research_units(self.connection, ["hair_band"])

        self.assertEqual(len(units), 1)
        self.assertEqual(units[0]["tag"], "hair_band")
        self.assertEqual(units[0]["post_count"], 200)
        self.assertIsNone(units[0]["wiki"])

    def test_completed_research_writes_verifiable_evidence(self):
        unit = build_research_queue(self.connection, limit=1)[0]
        quote = "The test tag has a documented community meaning."
        output = Path(self.temporary.name) / "research.jsonl"
        llm = ScriptedLLM(
            [
                json.dumps({"fetch": ["https://example.com/tag"]}),
                json.dumps(completed_verdict(quote), ensure_ascii=False),
            ]
        )
        fetcher = FixedFetch(f"Surrounding text. {quote} More text.")

        stats = run_research(
            self.task,
            [unit],
            output,
            llm=llm,
            search=NoSearch(),
            fetcher=fetcher,
            max_acts=2,
            top_n=3,
        )

        lines = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(stats["units_done"], 1)
        self.assertEqual(stats["units_failed"], 0)
        self.assertEqual(lines[0]["status"], "completed")
        self.assertEqual(lines[0]["evidence_verified"], True)
        self.assertEqual(lines[0]["evidence_pages"][0]["url"], "https://example.com/tag")

    def test_research_resumes_by_skipping_completed_keys(self) -> None:
        unit = build_research_queue(self.connection, limit=1)[0]
        quote = "The test tag has a documented community meaning."
        output = Path(self.temporary.name) / "research.jsonl"

        run_research(
            self.task,
            [unit],
            output,
            llm=ScriptedLLM(
                [
                    json.dumps({"fetch": ["https://example.com/tag"]}),
                    json.dumps(completed_verdict(quote), ensure_ascii=False),
                ]
            ),
            search=NoSearch(),
            fetcher=FixedFetch(f"Body. {quote}"),
            max_acts=2,
            top_n=3,
        )

        class FailIfCalled:
            def chat(self, messages):
                raise AssertionError("completed unit must be skipped")

        stats = run_research(
            self.task,
            [unit],
            output,
            llm=FailIfCalled(),
            search=NoSearch(),
            fetcher=FixedFetch("unused"),
            max_acts=2,
            top_n=3,
        )
        lines = output.read_text(encoding="utf-8").splitlines()

        self.assertEqual(stats["units_skipped"], 1)
        self.assertEqual(stats["units_done"], 0)
        self.assertEqual(stats["units_failed"], 0)
        self.assertEqual(len(lines), 1)

    def test_quote_not_present_in_fetched_page_is_rejected_once(self):
        unit = build_research_queue(self.connection, limit=1)[0]
        valid_quote = "A verifiable sentence from the fetched page."
        output = Path(self.temporary.name) / "research.jsonl"
        llm = ScriptedLLM(
            [
                json.dumps({"fetch": ["https://example.com/tag"]}),
                json.dumps(completed_verdict("This quote was invented."), ensure_ascii=False),
                json.dumps(completed_verdict(valid_quote), ensure_ascii=False),
            ]
        )
        fetcher = FixedFetch(f"Page body. {valid_quote}")

        run_research(
            self.task,
            [unit],
            output,
            llm=llm,
            search=NoSearch(),
            fetcher=fetcher,
            max_acts=2,
            top_n=3,
        )

        lines = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(llm.calls, 3)
        self.assertEqual(lines[0]["evidence"][0]["quote"], valid_quote)
        self.assertEqual(lines[0]["evidence_verified"], True)

    def test_unknown_status_is_valid_without_invented_evidence(self):
        unit = build_research_queue(self.connection, limit=1)[0]
        verdict = {
            "status": "unknown",
            "definition_zh": "",
            "semantic_documents": [],
            "evidence": [],
            "unresolved_questions": ["No accessible source described this tag."],
        }
        output = Path(self.temporary.name) / "research.jsonl"
        llm = ScriptedLLM([json.dumps(verdict, ensure_ascii=False)])

        run_research(
            self.task,
            [unit],
            output,
            llm=llm,
            search=NoSearch(),
            fetcher=FixedFetch(""),
            max_acts=0,
            top_n=3,
        )

        lines = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(lines[0]["status"], "unknown")
        self.assertEqual(lines[0]["evidence_verified"], True)
        self.assertEqual(lines[0]["unresolved_questions"][0], "No accessible source described this tag.")

    def test_unknown_cannot_attach_unfetched_evidence(self) -> None:
        unit = build_research_queue(self.connection, limit=1)[0]
        verdict = {
            "status": "unknown",
            "definition_zh": "",
            "semantic_documents": [],
            "evidence": [
                {"url": "https://example.com/unfetched", "quote": "never fetched sentence"}
            ],
            "unresolved_questions": ["No source."],
        }
        output = Path(self.temporary.name) / "unknown-fake.jsonl"
        llm = ScriptedLLM([json.dumps(verdict), json.dumps(verdict)])

        stats = run_research(
            self.task,
            [unit],
            output,
            llm=llm,
            search=NoSearch(),
            fetcher=FixedFetch(""),
            max_acts=0,
            top_n=3,
        )

        self.assertEqual(stats["units_failed"], 1)
        self.assertFalse(output.exists())

    def test_failed_research_still_counts_token_cost(self) -> None:
        unit = build_research_queue(self.connection, limit=1)[0]
        output = Path(self.temporary.name) / "failed.jsonl"
        invalid = {"status": "invalid"}
        llm = ScriptedLLM([json.dumps(invalid), json.dumps(invalid)])

        stats = run_research(
            self.task,
            [unit],
            output,
            llm=llm,
            search=NoSearch(),
            fetcher=FixedFetch(""),
            max_acts=0,
            top_n=3,
        )

        self.assertEqual(stats["units_failed"], 1)
        self.assertEqual(stats["tokens_in"], 40)
        self.assertEqual(stats["tokens_out"], 10)
        self.assertFalse(output.exists())

    def test_engine_stops_zero_progress_action_loop(self) -> None:
        unit = build_research_queue(self.connection, limit=1)[0]
        output = Path(self.temporary.name) / "loop.jsonl"
        repeated_action = json.dumps({"fetch": ["https://example.com/tag"]})
        llm = ScriptedLLM([repeated_action])

        stats = run_research(
            self.task,
            [unit],
            output,
            llm=llm,
            search=NoSearch(),
            fetcher=FixedFetch(""),
            max_acts=0,
            top_n=3,
        )

        self.assertEqual(stats["units_failed"], 1)
        self.assertEqual(stats["tokens_in"], 120)
        self.assertEqual(stats["tokens_out"], 30)
        self.assertEqual(llm.calls, 6)
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
