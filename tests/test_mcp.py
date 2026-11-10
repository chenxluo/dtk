import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from dtk.store import build_database
from tests.fixtures import make_source


class MCPTests(unittest.TestCase):
    def test_stdio_mcp_lists_and_calls_read_only_tools(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.db"
            derived = root / "derived.db"
            make_source(source)
            build_database(source, derived)
            process = subprocess.Popen(
                [
                    "python",
                    "-m",
                    "dtk",
                    "mcp",
                    "--db",
                    str(derived),
                    "--no-vectors",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                bufsize=1,
            )

            def call(message):
                process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
                process.stdin.flush()
                response = process.stdout.readline()
                self.assertTrue(response)
                return json.loads(response)

            try:
                initialized = call({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
                self.assertEqual(initialized["result"]["serverInfo"]["name"], "dtk")

                listed = call({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
                names = [tool["name"] for tool in listed["result"]["tools"]]
                self.assertEqual(names, ["search_tags", "get_tags"])

                found = call(
                    {
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "tools/call",
                        "params": {"name": "search_tags", "arguments": {"query": "Looking At Viewer"}},
                    }
                )
                self.assertEqual(found["result"]["structuredContent"]["results"][0]["tag"], "looking_at_viewer")

                details = call(
                    {
                        "jsonrpc": "2.0",
                        "id": 4,
                        "method": "tools/call",
                        "params": {"name": "get_tags", "arguments": {"names": ["long_hair"]}},
                    }
                )
                self.assertEqual(details["result"]["structuredContent"]["tags"][0]["tag"], "long_hair")

                unknown = call({"jsonrpc": "2.0", "id": 5, "method": "unknown/method", "params": {}})
                self.assertEqual(unknown["error"]["code"], -32601)
            finally:
                process.stdin.close()
                process.terminate()
                process.wait(timeout=10)
                process.stderr.close()
                process.stdout.close()


if __name__ == "__main__":
    unittest.main()
