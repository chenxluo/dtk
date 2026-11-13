"""Threaded read-only HTTP interface for human tag lookup."""
from __future__ import annotations

import contextlib
import io
import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import sqlite3
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .search import get_tag, search, search_grouped, split_concepts
from .intent import IntentClassifier
from .research.audit import audit_research
from .research.engine import run_research
from .research.fetch import Fetcher
from .research.llm import ResearchLLMClient
from .research.publish import load_research_records, publish_research
from .research.queue import build_research_queue, explicit_research_units
from .research.searx import SearXNGClient
from .research.tag_task import TagResearchTask
from .store import open_db
from .vectors import (
    EmbeddingClient,
    VectorIndex,
    build_research_vector_index,
    default_lm_base_url,
)
from .config import EDITABLE_KEYS, load_config, mask_secret, save_config
from .release import check_update, local_version
from .store import rebuild_search_index
from .translations import fetch_ffdkj_sqlite, import_translations
from .upstream import import_implications, import_wikis, upstream_status

_RESEARCH_OUTPUT = Path("out/research/missing_wiki.jsonl")  # keep in sync with `dtk research --out`



@dataclass
class _Job:
    kind: str
    status: str  # running | done | error
    started_at: float
    finished_at: float | None = None
    error: str | None = None
    log: list[str] = field(default_factory=list)


class _LogTail(io.TextIOBase):
    """Collect printed lines into the job log, keeping only the tail."""

    def __init__(self, job: _Job, limit: int = 200) -> None:
        self.job = job
        self.limit = limit

    def write(self, text: str) -> int:
        for line in text.splitlines():
            if line.strip():
                self.job.log.append(line)
        del self.job.log[: -self.limit]
        return len(text)


def _job_research(server: "DTKWebServer", params: dict[str, Any]) -> None:
    limit = max(1, min(int(params.get("limit", 10)), 200))
    tags = [str(tag).strip() for tag in params.get("tags", []) if str(tag).strip()]
    connection = open_db(server.db_path)
    try:
        if tags:
            units = explicit_research_units(connection, tags)
        else:
            units = build_research_queue(connection, limit=limit)
    finally:
        connection.close()
    if not units:
        print("queue empty: nothing to research")
        return
    print(f"researching {len(units)} tags …")
    config = load_config()
    stats = run_research(
        TagResearchTask(),
        units,
        server.research_output_path,
        llm=ResearchLLMClient(
            config["embedding_base_url"],
            config["research_model"],
            api_key=config["research_llm_api_key"],
            cache_dir="cache/research",
        ),
        search=SearXNGClient(config["searx_url"], cache_dir="cache/research"),
        fetcher=Fetcher(cache_dir="cache/research"),
    )
    print(json.dumps(stats, ensure_ascii=False))


def _job_publish(server: "DTKWebServer", params: dict[str, Any]) -> None:
    accepted = {str(tag).strip() for tag in params.get("tags", []) if str(tag).strip()}
    if not accepted:
        raise ValueError("no tags selected")
    records = [
        record
        for record in load_research_records(server.research_output_path)
        if record.get("tag") in accepted
    ]
    missing = accepted - {record["tag"] for record in records}
    if missing:
        raise ValueError(f"records missing accepted tags: {sorted(missing)}")
    manifest = publish_research(server.db_path, records, accepted_tags=accepted)
    print(json.dumps(manifest, ensure_ascii=False))


def _job_embed_research(server: "DTKWebServer", params: dict[str, Any]) -> None:
    config = load_config()
    if not server.research_vectors_path:
        raise ValueError("research vectors path is not configured for this server")
    manifest = build_research_vector_index(
        server.db_path,
        server.research_vectors_path,
        EmbeddingClient(config["embedding_base_url"], config["embedding_model"]),
    )
    print(json.dumps(manifest, ensure_ascii=False))
    if Path(server.research_vectors_path).is_file():
        server.research_index = VectorIndex.load(Path(server.research_vectors_path))
        print("research vector index reloaded in place")

def _job_rebuild_fts(server: "DTKWebServer", params: dict[str, Any]) -> None:
    print(json.dumps(rebuild_search_index(server.db_path), ensure_ascii=False))


def _job_import_implications(server: "DTKWebServer", params: dict[str, Any]) -> None:
    print(json.dumps(import_implications(server.db_path), ensure_ascii=False))


def _job_import_wikis(server: "DTKWebServer", params: dict[str, Any]) -> None:
    limit = int(params.get("limit", 0)) or None
    print(json.dumps(import_wikis(server.db_path, limit=limit), ensure_ascii=False))


def _job_import_translations(server: "DTKWebServer", params: dict[str, Any]) -> None:
    source = Path("cache") / "ffdkj-tag.sqlite"
    source.parent.mkdir(parents=True, exist_ok=True)
    fetch_ffdkj_sqlite(source)
    print(json.dumps(import_translations(server.db_path, source), ensure_ascii=False))


def _job_check_upstream(server: "DTKWebServer", params: dict[str, Any]) -> None:
    connection = open_db(server.db_path)
    try:
        report = upstream_status(connection)
    finally:
        connection.close()
    print(json.dumps(report, ensure_ascii=False))



_JOB_HANDLERS = {
    "research": _job_research,
    "publish": _job_publish,
    "embed_research": _job_embed_research,
    "rebuild_fts": _job_rebuild_fts,
    "import_implications": _job_import_implications,
    "import_wikis": _job_import_wikis,
    "import_translations": _job_import_translations,
    "check_upstream": _job_check_upstream,
}


def _start_job(server: "DTKWebServer", kind: str, params: dict[str, Any]) -> tuple[str | None, int, str]:
    handler = _JOB_HANDLERS.get(kind)
    if handler is None:
        return None, 400, f"unknown job kind: {kind}"
    with server.jobs_lock:
        running = [job for job in server.jobs.values() if job.status == "running"]
        if running:
            return None, 409, f"a job is already running: {running[0].kind}"
        job_id = uuid.uuid4().hex[:8]
        job = _Job(kind=kind, status="running", started_at=time.time())
        server.jobs[job_id] = job

    def worker() -> None:
        try:
            with contextlib.redirect_stdout(_LogTail(job)):
                handler(server, params)
            job.status = "done"
        except Exception as exc:  # noqa: BLE001 - job boundary reports, not raises
            job.status = "error"
            job.error = f"{type(exc).__name__}: {exc}"
            job.log.append(job.error)
        finally:
            job.finished_at = time.time()

    threading.Thread(target=worker, daemon=True).start()
    return job_id, 200, ""

def _public_config() -> dict[str, Any]:
    config = load_config()
    return {
        "embedding_base_url": config["embedding_base_url"],
        "embedding_model": config["embedding_model"],
        "research_model": config["research_model"],
        "searx_url": config["searx_url"],
        "embedding_api_key": mask_secret(config["embedding_api_key"]),
        "research_llm_api_key": mask_secret(config["research_llm_api_key"]),
    }


_WEB_DIR = Path(__file__).resolve().parents[1] / "web"
_STATIC_ROUTES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/lab": ("lab.html", "text/html; charset=utf-8"),
    "/assets/app.css": ("app.css", "text/css; charset=utf-8"),
    "/favicon.svg": ("icon.svg", "image/svg+xml"),
}

class _Handler(BaseHTTPRequestHandler):
    server: "DTKWebServer"

    def do_GET(self) -> None:  # noqa: N802 - stdlib HTTP API
        route = urlparse(self.path)
        static = _STATIC_ROUTES.get(route.path)
        if static is not None:
            self._send_bytes(self._static_file(static[0]), static[1])
        elif route.path == "/health":
            self._send_json({"status": "ok", **self.server.health()})
        elif route.path == "/api/research/queue":
            self._api_research_queue(parse_qs(route.query))
        elif route.path == "/api/research/candidates":
            self._api_research_candidates()
        elif route.path.startswith("/api/jobs/"):
            self._api_job_detail(route.path.rsplit("/", 1)[-1])
        elif route.path == "/api/jobs":
            self._api_jobs_list()
        elif route.path == "/api/search":
            self._api_search(parse_qs(route.query))
        elif route.path == "/api/tag":
            self._api_tag(parse_qs(route.query))
        elif route.path == "/api/config":
            self._send_json(_public_config())
        elif route.path == "/api/stats":
            self._api_stats()
        elif route.path == "/api/update/check":
            self._api_update_check()
        else:
            self._send_json({"error": "not_found"}, status=404)

    def do_PUT(self) -> None:  # noqa: N802 - stdlib HTTP API
        route = urlparse(self.path)
        if route.path == "/api/config":
            try:
                length = int(self.headers.get("Content-Length") or 0)
                updates = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                self._send_json({"error": "invalid_json"}, status=400)
                return
            if not isinstance(updates, dict) or any(k not in EDITABLE_KEYS for k in updates):
                self._send_json({"error": "unknown_key"}, status=400)
                return
            save_config(updates)
            self._send_json({"status": "saved", "restart_required": True, "config": _public_config()})
        else:
            self._send_json({"error": "not_found"}, status=404)


    def do_POST(self) -> None:  # noqa: N802 - stdlib HTTP API
        route = urlparse(self.path)
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            self._send_json({"error": "invalid_json"}, status=400)
            return
        if not isinstance(body, dict):
            self._send_json({"error": "invalid_body"}, status=400)
            return
        if route.path == "/api/embedding/test":
            self._api_embedding_test(body)
            return
        if route.path != "/api/jobs":
            self._send_json({"error": "not_found"}, status=404)
            return
        job_id, status, message = _start_job(
            self.server, str(body.get("kind", "")), body.get("params") or {}
        )
        if job_id is None:
            self._send_json({"error": message}, status=status)
            return
        self._send_json({"id": job_id, "kind": body.get("kind")}, status=201)

    def _api_embedding_test(self, body: dict[str, Any]) -> None:
        """Probe the embedding service; accepts typed-but-unsaved form values."""
        base_url = str(body.get("base_url") or "").strip() or None
        model = str(body.get("model") or "").strip() or None
        api_key = str(body.get("api_key") or "").strip() or None
        client = EmbeddingClient(base_url, model, timeout=8)
        if api_key:
            client.api_key = api_key
        start = time.perf_counter()
        try:
            vector = client.embed(["连接测试"])
        except Exception as exc:  # noqa: BLE001 - probe reports, never raises
            self._send_json({"ok": False, "error": f"{type(exc).__name__}: {exc}"[:200]})
            return
        self._send_json({
            "ok": True,
            "latency_ms": round((time.perf_counter() - start) * 1000),
            "dimension": int(vector.shape[1]),
            "base_url": client.base_url,
            "model": client.model,
        })

    def _api_jobs_list(self) -> None:
        jobs = [
            {
                "id": job_id,
                "kind": job.kind,
                "status": job.status,
                "started_at": job.started_at,
                "finished_at": job.finished_at,
                "error": job.error,
            }
            for job_id, job in reversed(list(self.server.jobs.items()))
        ][:10]
        self._send_json({"jobs": jobs})

    def _api_job_detail(self, job_id: str) -> None:
        job = self.server.jobs.get(job_id)
        if job is None:
            self._send_json({"error": "job_not_found"}, status=404)
            return
        self._send_json({
            "id": job_id,
            "kind": job.kind,
            "status": job.status,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "error": job.error,
            "log": job.log[-40:],
        })

    def _api_research_queue(self, params: dict[str, list[str]]) -> None:
        try:
            limit = min(max(int(params.get("limit", ["20"])[0]), 1), 50)
        except ValueError:
            limit = 20
        connection = open_db(self.server.db_path)
        try:
            units = build_research_queue(connection, limit=limit)
        finally:
            connection.close()
        self._send_json({
            "limit": limit,
            "units": [
                {"tag": unit["tag"], "type": unit["type"], "post_count": unit["post_count"]}
                for unit in units
            ],
        })

    def _api_research_candidates(self) -> None:
        path = Path(self.server.research_output_path)
        if not path.is_file():
            self._send_json({"exists": False, "records": [], "audit": None})
            return
        audit = audit_research(path)
        issues_by_key: dict[str, list[str]] = {}
        for issue in audit.get("issues", []):
            issues_by_key.setdefault(str(issue.get("key", "")), []).extend(issue.get("errors", []))
        records = [
            {
                "tag": record.get("tag", record.get("key", "")),
                "status": record.get("status"),
                "evidence_verified": record.get("evidence_verified") is True,
                "definition_zh": (record.get("definition_zh") or record.get("definition") or "")[:160],
                "issues": issues_by_key.get(str(record.get("key", "")), []),
            }
            for record in load_research_records(path)
        ]
        self._send_json({"exists": True, "records": records, "audit": {
            "status_counts": audit.get("status_counts", {}),
            "issue_count": len(audit.get("issues", [])),
        }})

    def _static_file(self, name: str) -> bytes:
        # mtime invalidation: editing web/ no longer needs a server restart.
        path = _WEB_DIR / name
        cache = self.server.static_cache
        mtime = path.stat().st_mtime_ns
        if cache.get(name, (0, b""))[0] != mtime:
            cache[name] = (mtime, path.read_bytes())
        return cache[name][1]

    def _api_stats(self) -> None:
        connection = open_db(self.server.db_path)
        try:
            payload = _stats_payload(connection, self.server)
        finally:
            connection.close()
        self._send_json(payload)

    def _api_update_check(self) -> None:
        repo = load_config()["release_repo"]
        current = local_version(self.server.db_path)
        if not repo:
            self._send_json({"configured": False, "current": current})
            return
        try:
            self._send_json({"configured": True, **check_update(repo, current)})
        except (OSError, ValueError) as exc:
            self._send_json(
                {"configured": True, "current": current, "error": f"{type(exc).__name__}: {exc}"},
                status=502,
            )

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
        research_index = self.server.research_index
        query_vector = None
        query_vectors = None
        concepts = split_concepts(query)
        degraded = False
        if vector_index is not None or research_index is not None:
            try:
                vectors = self.server.embedding_client.embed(concepts)
            except Exception:
                # Embedding service down: degrade this request to FTS instead of
                # dropping the connection; retries next request (self-healing).
                vector_index = None
                research_index = None
                degraded = True
            else:
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
                        research_index=research_index,
                        intent_parser=self.server.intent_parser,
                        query_vector=query_vector,
                    )
                else:
                    result = search_grouped(
                        connection,
                        query,
                        limit=limit,
                        vector_index=vector_index,
                        research_index=research_index,
                        intent_parser=self.server.intent_parser,
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
        if degraded:
            result["degraded"] = True
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
    # Windows 的 SO_REUSEADDR 允许两个服务静默双绑同一端口（旧进程未死时
    # 新进程照常启动，请求被旧代码接走）；POSIX 保留它以快速重绑 TIME_WAIT。
    allow_reuse_address = sys.platform != "win32"

    def __init__(
        self,
        address: tuple[str, int],
        db_path: Path,
        vector_index: VectorIndex | None,
        research_index: VectorIndex | None,
        embedding_client: EmbeddingClient,
        intent_parser: IntentClassifier | None,
        vectors_path: Path | None,
        research_vectors_path: Path | None,
        research_output_path: Path | None = None,
    ) -> None:
        super().__init__(address, _Handler)
        self.db_path = db_path
        self.vector_index = vector_index
        self.research_index = research_index
        self.embedding_client = embedding_client
        self.intent_parser = intent_parser
        self.static_cache: dict[str, tuple[int, bytes]] = {}
        self.vectors_path = vectors_path
        self.research_vectors_path = research_vectors_path
        self.jobs: dict[str, _Job] = {}
        self.jobs_lock = threading.Lock()
        self.research_output_path = research_output_path or _RESEARCH_OUTPUT

    def health(self) -> dict[str, Any]:
        return {
            "semantic": self.vector_index is not None or self.research_index is not None,
            "model": (
                self.vector_index.metadata.get("model") if self.vector_index else None
            ),
            "indexed_tags": (
                self.vector_index.metadata.get("tag_count") if self.vector_index else 0
            ),
            "research_documents": (
                self.research_index.metadata.get("document_count") if self.research_index else 0
            ),
            "embedding_base_url": self.embedding_client.base_url,
            "embedding_model": self.embedding_client.model,
        }


def create_server(
    host: str,
    port: int,
    db_path: str | Path,
    vectors_path: str | Path | None = None,
    research_vectors_path: str | Path | None = None,
    embedding_model: str | None = None,
    intent: bool = False,
    intent_model: str | None = None,
    lm_base_url: str | None = None,
    research_output_path: str | Path | None = None,
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

    research_index = None
    if research_vectors_path:
        research_vector_path = Path(research_vectors_path)
        if research_vector_path.is_file():
            research_index = VectorIndex.load(research_vector_path)

    intent_parser = None
    if intent:
        intent_parser = IntentClassifier(
            ResearchLLMClient(
                lm_base_url or default_lm_base_url(),
                intent_model or "qwen3.8-27b",
                cache_dir="cache/intent",
            )
        )

    return DTKWebServer(
        (host, port),
        database,
        vector_index,
        research_index,
        # Short timeout: interactive queries must fall back to FTS quickly when
        # the embedding service is unreachable (index builds use the long default).
        # ponytail: fixed 10s; make it configurable if slow remote endpoints appear.
        EmbeddingClient(lm_base_url, embedding_model, timeout=10.0),
        intent_parser,
        vectors_path,
        research_vectors_path,
        Path(research_output_path) if research_output_path else None,
    )


_META_KEYS = (
    "dtk_schema_version",
    "import_started_at",
    "import_completed_at",
    "source_latest_post_id",
    "source_latest_post_created_at",
    "implications_fetched_at",
    "upstream_wikis_fetched_at",
)


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone() is not None


def _count(connection: sqlite3.Connection, table: str) -> int:
    if not _table_exists(connection, table):
        return 0
    return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _stats_payload(connection: sqlite3.Connection, server: "DTKWebServer") -> dict[str, Any]:
    categories = [
        {"type": row[0], "tags": int(row[1]), "with_wiki": int(row[2]), "with_cn_name": int(row[3])}
        for row in connection.execute(
            """
            SELECT type, COUNT(*),
                   SUM(CASE WHEN wiki IS NOT NULL AND wiki != '' THEN 1 ELSE 0 END),
                   SUM(CASE WHEN cn_name IS NOT NULL AND cn_name != '' THEN 1 ELSE 0 END)
            FROM tag_details GROUP BY type ORDER BY COUNT(*) DESC
            """
        )
    ]
    translated = {
        row[0]: int(row[1])
        for row in connection.execute(
            """
            SELECT td.type, COUNT(DISTINCT tt.tag_id)
            FROM tag_translations tt JOIN tag_details td ON td.id = tt.tag_id
            GROUP BY td.type
            """
        )
    }
    for category in categories:
        category["with_translation"] = translated.get(category["type"], 0)

    meta = {row["key"]: row["value"] for row in connection.execute("SELECT key, value FROM meta")}

    files: list[dict[str, Any]] = [
        {"name": "dtk.db", "bytes": Path(server.db_path).stat().st_size, "detail": "Derived Store"}
    ]
    if server.vectors_path:
        path = Path(server.vectors_path)
        files.append({
            "name": path.name,
            "bytes": path.stat().st_size if path.is_file() else 0,
            "detail": (
                f"{server.vector_index.metadata.get('model', '')} · "
                f"{server.vector_index.metadata.get('tag_count', 0)} Tag"
                if server.vector_index else "未加载"
            ),
        })
    if server.research_vectors_path:
        path = Path(server.research_vectors_path)
        files.append({
            "name": path.name,
            "bytes": path.stat().st_size if path.is_file() else 0,
            "detail": (
                f"{server.research_index.metadata.get('document_count', 0)} 研究文档"
                if server.research_index else "未加载"
            ),
        })

    return {
        "categories": categories,
        "assets": {
            "aliases": _count(connection, "aliases"),
            "translations": _count(connection, "tag_translations"),
            "implications": _count(connection, "tag_implications"),
            "upstream_wikis": _count(connection, "upstream_wikis"),
            "enrichment": _count(connection, "tag_enrichment"),
            "research_publications": _count(connection, "research_publications"),
            "research_documents": _count(connection, "research_documents"),
            "research_evidence": _count(connection, "research_evidence"),
        },
        "meta": {key: meta.get(key) for key in _META_KEYS},
        "files": files,
    }
