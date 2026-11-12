"""Parallel Search API client with a built-in anime/ACG objective tilt.

Same `search(query) -> [{url, title, content}]` shape as SearXNGClient so the
research act loop can swap providers. API: POST /v1/search with x-api-key.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from .cache import write_json_atomic

BACKOFF = (0, 2, 10)

DEFAULT_BASE_URL = "https://api.parallel.ai"

# Docs recommend steering source preference through the objective rather than
# hard include_domains filters, so the ACG tilt lives here by default.
ACG_OBJECTIVE = (
    "Determine what this Danbooru tag means. Danbooru is a Japanese-style "
    "anime/illustration image board: prefer the anime, manga, game, or "
    "illustration sense of the term and treat it as a visual depiction cue; "
    "for polysemous words use the ACG sense, not the general dictionary sense. "
    "Prefer Danbooru wiki, Moegirlpedia (moegirl.org.cn), Pixiv encyclopedia "
    "(dic.pixiv.net), e621 wiki, and anime fandom wikis. Query: {query}"
)


class ParallelSearchClient:
    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        mode: str = "fast",
        objective_template: str = ACG_OBJECTIVE,
        include_domains: list[str] | None = None,
        max_results: int = 8,
        qps: float = 1.0,
        cache_dir: str | Path | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.api_key = api_key or os.environ.get("PARALLEL_API_KEY")
        self.base_url = base_url.rstrip("/")
        self.mode = mode
        self.objective_template = objective_template
        self.include_domains = list(include_domains) if include_domains else None
        self.max_results = max_results
        self.interval = 1.0 / qps if qps > 0 else 0.0
        self.cache_dir = Path(cache_dir) / "parallel" if cache_dir else None
        self.timeout = timeout
        self._lock = threading.Lock()
        self._next_allowed = 0.0
        self.queries = 0
        self.cache_hits = 0

    def search(self, query: str) -> list[dict[str, str]]:
        cache_path: Path | None = None
        if self.cache_dir:
            material = {"query": query, "mode": self.mode, "domains": self.include_domains}
            digest = hashlib.sha256(
                json.dumps(material, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest()
            cache_path = self.cache_dir / f"{digest}.json"
            hit = self._read(cache_path)
            if hit is not None:
                with self._lock:
                    self.queries += 1
                    self.cache_hits += 1
                return hit

        payload: dict[str, Any] = {
            "objective": self.objective_template.format(query=query),
            "search_queries": [query],
            "mode": self.mode,
        }
        advanced: dict[str, Any] = {"max_results": self.max_results}
        if self.include_domains:
            advanced["source_policy"] = {"include_domains": self.include_domains}
        payload["advanced_settings"] = advanced

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["x-api-key"] = self.api_key

        request = urllib.request.Request(
            f"{self.base_url}/v1/search",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        last_error = "no attempt"
        for delay in BACKOFF:
            if delay:
                time.sleep(delay)
            self._pace()
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    data = json.load(response)
                results = [
                    {
                        "url": item.get("url") or "",
                        "title": item.get("title") or "",
                        "content": "\n".join(item.get("excerpts") or []),
                    }
                    for item in data.get("results") or []
                    if item.get("url")
                ][: self.max_results]
                if results and cache_path:
                    self._write(cache_path, results)
                with self._lock:
                    self.queries += 1
                return results
            except urllib.error.HTTPError as error:
                detail = error.read().decode("utf-8", errors="replace")[:200]
                last_error = f"HTTP {error.code}: {detail}"
                if error.code in (408, 429) or 500 <= error.code < 600:
                    continue
                break  # bad request or auth: retrying cannot help
            except (urllib.error.URLError, TimeoutError, ValueError) as error:
                last_error = repr(error)
        print(
            f"[research] parallel gave up on {query!r}: {last_error}",
            file=sys.stderr,
        )
        with self._lock:
            self.queries += 1
        return []

    def _pace(self) -> None:
        # ponytail: global clock; per-query locks if research throughput matters
        with self._lock:
            now = time.monotonic()
            if self._next_allowed > now:
                time.sleep(self._next_allowed - now)
            self._next_allowed = time.monotonic() + self.interval

    @staticmethod
    def _read(path: Path) -> list[dict[str, str]] | None:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, list) else None
        except (OSError, ValueError):
            return None

    def _write(self, path: Path, value: list[dict[str, str]]) -> None:
        with self._lock:
            write_json_atomic(path, value)


def load_parallel_key(path: str | Path = "key/parallel.txt") -> str | None:
    """Read the first non-empty line of a key file, if present."""
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                return line
    except OSError:
        return None
    return None
