"""OpenAI-compatible chat client for the research kernel.

Standard-library HTTP only. Responses are cached by endpoint plus the exact
request payload so a failed batch can resume without paying twice.
"""
from __future__ import annotations

import hashlib
import json
import os
import ssl
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from .cache import write_json_atomic

RETRY_DELAYS = (0, 2, 10)


class ResearchLLMClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None = None,
        cache_dir: str | Path | None = None,
        timeout: float = 600.0,
        disable_thinking: bool = True,
        verify_tls: bool = True,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key or os.environ.get("DTK_RESEARCH_LLM_API_KEY")
        self.cache_dir = Path(cache_dir) / "llm" if cache_dir else None
        self.timeout = timeout
        self.disable_thinking = disable_thinking
        # ponytail: opt-in TLS bypass for LAN servers with self-signed certs only
        self.ssl_context = None if verify_tls else ssl._create_unverified_context()
        self._lock = threading.Lock()
        self.calls = 0
        self.cache_hits = 0
        self.tokens = {"in": 0, "out": 0}

    def chat(
        self, messages: list[dict[str, str]]
    ) -> tuple[str, dict[str, int], bool]:
        headers = {"Content-Type": "application/json"}
        try:
            headers.update(json.loads(os.environ.get("DTK_RESEARCH_LLM_HEADERS") or "{}"))
        except ValueError:
            pass
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.2,
            "stream": False,
        }
        if self.disable_thinking:
            payload.setdefault("chat_template_kwargs", {"enable_thinking": False})
        try:
            payload.update(json.loads(os.environ.get("DTK_RESEARCH_LLM_BODY") or "{}"))
        except ValueError:
            pass

        cache_path: Path | None = None
        if self.cache_dir:
            cache_material = {
                "base_url": self.base_url,
                "payload": payload,
            }
            digest = hashlib.sha256(
                json.dumps(cache_material, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            cache_path = self.cache_dir / f"{digest}.json"
            hit = self._read(cache_path)
            if hit is not None:
                with self._lock:
                    self.calls += 1
                    self.cache_hits += 1
                    self.tokens["in"] += hit["usage"]["in"]
                    self.tokens["out"] += hit["usage"]["out"]
                return hit["content"], hit["usage"], True

        last_error = "no attempt"
        for delay in RETRY_DELAYS:
            if delay:
                time.sleep(delay)
            request = urllib.request.Request(
                f"{self.base_url}/chat/completions",
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers=headers,
            )
            try:
                with urllib.request.urlopen(
                    request, timeout=self.timeout, context=self.ssl_context
                ) as response:
                    data = json.load(response)
                choices = data.get("choices") or []
                if not choices:
                    raise RuntimeError(f"LLM returned no choices: {str(data)[:300]}")
                content = choices[0].get("message", {}).get("content") or ""
                usage_raw = data.get("usage") or {}
                usage = {
                    "in": int(usage_raw.get("prompt_tokens") or 0),
                    "out": int(usage_raw.get("completion_tokens") or 0),
                }
                if cache_path:
                    self._write(cache_path, {"content": content, "usage": usage})
                with self._lock:
                    self.calls += 1
                    self.tokens["in"] += usage["in"]
                    self.tokens["out"] += usage["out"]
                return content, usage, False
            except urllib.error.HTTPError as error:
                detail = error.read().decode("utf-8", errors="replace")[:300]
                if error.code in (408, 429) or 500 <= error.code < 600:
                    last_error = f"HTTP {error.code}: {detail}"
                    continue
                raise RuntimeError(f"LLM HTTP {error.code}: {detail}") from error
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
                last_error = repr(error)
        raise RuntimeError(f"LLM request failed after {len(RETRY_DELAYS)} attempts: {last_error}")

    @staticmethod
    def _read(path: Path) -> dict[str, Any] | None:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _write(self, path: Path, value: dict[str, Any]) -> None:
        with self._lock:
            write_json_atomic(path, value)
