"""Stdio MCP adapter over the same read-only Tag retrieval interface."""
from __future__ import annotations

import json
import sys
from typing import Any, TextIO

from . import __version__
from .search import get_tag, search, search_grouped, split_concepts
from .store import open_db
from .vectors import EmbeddingClient, VectorIndex

TOOLS = [
    {
        "name": "search_tags",
        "description": "Resolve a natural-language visual description, Tag name, or Alias to canonical Danbooru Tags.",
        "inputSchema": {
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
        },
    },
    {
        "name": "get_tags",
        "description": "Read canonical Tags, official Wiki, aliases, and published research for one or more Tags.",
        "inputSchema": {
            "type": "object",
            "required": ["names"],
            "properties": {
                "names": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
]


def _tool_result(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(value, ensure_ascii=False, separators=(",", ":")),
            }
        ],
        "structuredContent": value,
    }


def _error_result(message: str) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": message}],
        "isError": True,
    }


class StdioMCPServer:
    def __init__(
        self,
        db_path: str,
        vector_index: VectorIndex | None = None,
        research_index: VectorIndex | None = None,
        embedding_client: EmbeddingClient | None = None,
        intent_parser: Any | None = None,
    ) -> None:
        self.db_path = db_path
        self.vector_index = vector_index
        self.research_index = research_index
        self.embedding_client = embedding_client
        self.intent_parser = intent_parser

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        request_id = message.get("id")
        method = message.get("method")
        if method == "notifications/initialized":
            return None
        if request_id is None:
            return None
        if method == "initialize":
            return self._response(
                request_id,
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "dtk", "version": __version__},
                },
            )
        if method == "ping":
            return self._response(request_id, {})
        if method == "tools/list":
            return self._response(request_id, {"tools": TOOLS})
        if method == "tools/call":
            params = message.get("params") or {}
            name = params.get("name")
            arguments = params.get("arguments") or {}
            if name == "search_tags":
                return self._response(request_id, _tool_result(self._search_tags(arguments)))
            if name == "get_tags":
                return self._response(request_id, _tool_result(self._get_tags(arguments)))
            return self._response(request_id, _error_result(f"unknown tool: {name}"))
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": f"method not found: {method}"},
        }

    def _search_tags(self, arguments: dict[str, Any]) -> dict[str, Any]:
        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("search_tags.query must be a non-empty string")
        limit = int(arguments.get("limit", 10))
        if not 1 <= limit <= 50:
            raise ValueError("search_tags.limit must be between 1 and 50")
        concepts = split_concepts(query)
        query_vector = None
        query_vectors = None
        if self.vector_index or self.research_index:
            client = self.embedding_client or EmbeddingClient()
            vectors = client.embed(concepts)
            if len(concepts) == 1:
                query_vector = vectors[0]
            else:
                query_vectors = dict(zip(concepts, vectors))
        connection = open_db(self.db_path)
        try:
            if len(concepts) == 1:
                return search(
                    connection,
                    query,
                    limit=limit,
                    vector_index=self.vector_index,
                    research_index=self.research_index,
                    intent_parser=self.intent_parser,
                    query_vector=query_vector,
                )
            return search_grouped(
                connection,
                query,
                limit=limit,
                vector_index=self.vector_index,
                research_index=self.research_index,
                intent_parser=self.intent_parser,
                query_vectors=query_vectors,
            )
        finally:
            connection.close()

    def _get_tags(self, arguments: dict[str, Any]) -> dict[str, Any]:
        names = arguments.get("names")
        if not isinstance(names, list) or not names or any(not isinstance(name, str) for name in names):
            raise ValueError("get_tags.names must be a non-empty string array")
        if len(names) > 50:
            raise ValueError("get_tags.names is limited to 50 Tags per call")
        connection = open_db(self.db_path)
        try:
            return {"tags": [get_tag(connection, name) for name in names]}
        finally:
            connection.close()

    @staticmethod
    def _response(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}


def run_stdio(
    server: StdioMCPServer,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
) -> None:
    """Run newline-delimited JSON-RPC until the client closes stdin."""
    input_stream = stdin or sys.stdin
    output_stream = stdout or sys.stdout
    for line in input_stream:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
            response = server.handle(message)
        except Exception as error:
            request_id = message.get("id") if isinstance(message, dict) else None
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32602, "message": str(error)},
            }
        if response is not None:
            output_stream.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
            output_stream.flush()
