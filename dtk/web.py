"""Threaded read-only HTTP interface for human tag lookup."""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .search import get_tag, search, search_grouped, split_concepts
from .store import open_db
from .vectors import EmbeddingClient, VectorIndex

_INDEX_PAGE = Path(__file__).resolve().parents[1] / "web" / "index.html"


class _Handler(BaseHTTPRequestHandler):
    server: "DTKWebServer"

    def do_GET(self) -> None:  # noqa: N802 - stdlib HTTP API
        route = urlparse(self.path)
        if route.path == "/":
            self._send_bytes(self._index_html(), "text/html; charset=utf-8")
        elif route.path == "/health":
            self._send_json({"status": "ok", **self.server.health()})
        elif route.path == "/api/search":
            self._api_search(parse_qs(route.query))
        elif route.path == "/api/tag":
            self._api_tag(parse_qs(route.query))
        else:
            self._send_json({"error": "not_found"}, status=404)

    def _index_html(self) -> bytes:
        if not hasattr(self.server, "index_html"):
            self.server.index_html = _INDEX_PAGE.read_bytes()
        return self.server.index_html

    def _api_search(self, params: dict[str, list[str]]) -> None:
        queries = params.get("query", [])
        if not queries or not queries[0].strip():
            self._send_json({"error": "query is required"}, status=400)
            return
        try:
            limit = min(max(int(params.get("limit", ["10"])[0]), 1), 50)
        except ValueError:
            self._send_json({"error": "limit must be an integer"}, status=400)
            return

        query = queries[0].strip()
        vector_index = self.server.vector_index
        query_vector = None
        query_vectors = None
        concepts = split_concepts(query)
        if vector_index is not None:
            vectors = self.server.embedding_client.embed(concepts)
            if len(concepts) == 1:
                query_vector = vectors[0]
            else:
                query_vectors = dict(zip(concepts, vectors))
        try:
            connection = open_db(self.server.db_path)
            try:
                if len(concepts) == 1:
                    result = search(
                        connection,
                        query,
                        limit=limit,
                        vector_index=vector_index,
                        query_vector=query_vector,
                    )
                else:
                    result = search_grouped(
                        connection,
                        query,
                        limit=limit,
                        vector_index=vector_index,
                        query_vectors=query_vectors,
                    )
            finally:
                connection.close()
        except ValueError as error:
            self._send_json({"error": str(error)}, status=400)
            return
        except Exception as error:
            self._send_json({"error": f"search failed: {error}"}, status=500)
            return
        self._send_json(result)

    def _api_tag(self, params: dict[str, list[str]]) -> None:
        names = params.get("name", [])
        if not names or not names[0].strip():
            self._send_json({"error": "name is required"}, status=400)
            return
        connection = open_db(self.server.db_path)
        try:
            record = get_tag(connection, names[0])
        finally:
            connection.close()
        if record is None:
            self._send_json({"error": "tag_not_found"}, status=404)
            return
        self._send_json(record)

    def _send_bytes(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, value: Any, status: int = 200) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self._send_bytes(body, "application/json; charset=utf-8", status)

    def log_message(self, format: str, *args: Any) -> None:
        # Keep the local service quiet; errors are returned through the API.
        return


class DTKWebServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        db_path: Path,
        vector_index: VectorIndex | None,
        embedding_client: EmbeddingClient,
    ) -> None:
        super().__init__(address, _Handler)
        self.db_path = db_path
        self.vector_index = vector_index
        self.embedding_client = embedding_client

    def health(self) -> dict[str, Any]:
        return {
            "semantic": self.vector_index is not None,
            "model": (
                self.vector_index.metadata.get("model") if self.vector_index else None
            ),
            "indexed_tags": (
                self.vector_index.metadata.get("tag_count") if self.vector_index else 0
            ),
        }


def create_server(
    host: str,
    port: int,
    db_path: str | Path,
    vectors_path: str | Path | None = None,
    embedding_model: str | None = None,
    lm_base_url: str | None = None,
) -> DTKWebServer:
    """Create a read-only local web server; caller owns serve_forever lifecycle."""
    database = Path(db_path).resolve()
    if not database.is_file():
        raise FileNotFoundError(f"derived store not found: {database}")

    vector_index = None
    if vectors_path:
        vector_path = Path(vectors_path)
        if vector_path.is_file():
            vector_index = VectorIndex.load(vector_path)

    return DTKWebServer(
        (host, port),
        database,
        vector_index,
        EmbeddingClient(lm_base_url, embedding_model),
    )
