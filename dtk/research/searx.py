"""SearXNG JSON client with global pacing and non-empty-result disk cache."""
from __future__ import annotations

import hashlib
import json
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any
from .cache import write_json_atomic

BACKOFF = (0, 2, 10)


class SearXNGClient:
    def __init__(
        self,
        base_url: str,
        qps: float = 1.0,
        cache_dir: str | Path | None = None,
        timeout: float = 20.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.interval = 1.0 / qps if qps > 0 else 0.0
        self.cache_dir = Path(cache_dir) / "searx" if cache_dir else None
        self.timeout = timeout
        self._lock = threading.Lock()
        self._next_allowed = 0.0
        self.queries = 0
        self.cache_hits = 0

    def search(self, query: str) -> list[dict[str, str]]:
        cache_path: Path | None = None
        if self.cache_dir:
            digest = hashlib.sha256(query.encode("utf-8")).hexdigest()
            cache_path = self.cache_dir / f"{digest}.json"
            hit = self._read(cache_path)
            if hit is not None:
                with self._lock:
                    self.queries += 1
                    self.cache_hits += 1
                return hit

        params = urllib.parse.urlencode(
            {"q": query, "format": "json", "safesearch": 0}
        )
        request = urllib.request.Request(
            f"{self.base_url}/search?{params}",
            headers={"Accept": "application/json", "User-Agent": "DTK-Research/0.2"},
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
                        "content": item.get("content") or "",
                    }
                    for item in data.get("results") or []
                    if item.get("url")
                ][:10]
                if results and cache_path:
                    self._write(cache_path, results)
                with self._lock:
                    self.queries += 1
                return results
            except (urllib.error.URLError, TimeoutError, ValueError) as error:
                last_error = repr(error)
        print(
            f"[research] searx gave up on {query!r}: {last_error}",
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
