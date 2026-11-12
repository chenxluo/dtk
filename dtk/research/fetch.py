"""Defensive text fetcher with bounded reads and disk cache."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from .cache import write_json_atomic

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
BINARY_CONTENT = ("image/", "video/", "audio/", "pdf", "octet-stream", "zip")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _blocked_ip(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return bool(
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    )



class _TextHTML(HTMLParser):
    DROP = {"script", "style", "noscript", "svg"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []
        self._depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.DROP:
            self._depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in self.DROP and self._depth:
            self._depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._depth:
            self.chunks.append(data)


def html_text(raw: str) -> str:
    parser = _TextHTML()
    try:
        parser.feed(raw)
        parser.close()
    except Exception:
        pass
    return " ".join(" ".join(parser.chunks).split())

def _encode_url(url: str) -> str:
    """Percent-encode non-ASCII URL characters (e.g. Chinese moegirl paths)."""
    return urllib.parse.quote(url, safe="/%:=&?~#+$,;'@()*[]!.")


class Fetcher:
    def __init__(
        self,
        timeout: float = 12.0,
        page_chars: int = 12000,
        cache_dir: str | Path | None = None,
        allow_private: bool = False,
    ) -> None:
        self.timeout = timeout
        self.page_chars = page_chars
        self.cache_dir = Path(cache_dir) / "fetch" if cache_dir else None
        self.allow_private = allow_private
        self._lock = threading.Lock()
        self.fetches = 0
        self.cache_hits = 0

    def fetch(self, url: str) -> str | None:
        cache_path: Path | None = None
        if self.cache_dir:
            digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
            cache_path = self.cache_dir / f"{digest}.json"
            hit = self._read(cache_path)
            hit_text = hit.get("text") if hit is not None else None
            if hit_text is not None:
                with self._lock:
                    self.fetches += 1
                    self.cache_hits += 1
                return hit_text

        text = self._get(url)
        if cache_path and text:
            self._write(cache_path, {"url": url, "text": text})
        with self._lock:
            self.fetches += 1
        return text

    def _url_allowed(self, url: str) -> bool:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return False
        return self.allow_private or self._host_allowed(parsed.hostname or "")

    @staticmethod
    def _host_allowed(hostname: str) -> bool:
        try:
            addresses = [ipaddress.ip_address(hostname.strip("[]"))]
        except ValueError:
            try:
                # ponytail: resolve-then-fetch guards normal redirects and DNS;
                # a dedicated transport is needed to eliminate DNS rebinding.
                addresses = [
                    ipaddress.ip_address(info[4][0])
                    for info in socket.getaddrinfo(hostname, None)
                ]
            except (socket.gaierror, ValueError):
                return False
        return not any(_blocked_ip(address) for address in addresses)

    def _get(self, url: str) -> str | None:
        if not self._url_allowed(url):
            return None

        current_url = url
        opener = urllib.request.build_opener(_NoRedirect)
        for _ in range(5):
            request = urllib.request.Request(
                _encode_url(current_url),
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en,zh-CN;q=0.8",
                    "Accept-Encoding": "identity",
                },
            )
            try:
                with opener.open(request, timeout=self.timeout) as response:
                    if response.status != 200:
                        return None
                    content_type = (response.headers.get("Content-Type") or "").lower()
                    if any(marker in content_type for marker in BINARY_CONTENT):
                        return None
                    raw = response.read(self.page_chars * 4 + 1024).decode(
                        "utf-8", errors="replace"
                    )
            except urllib.error.HTTPError as error:
                if error.code not in {301, 302, 303, 307, 308}:
                    return None
                location = error.headers.get("Location")
                if not location:
                    return None
                current_url = urllib.parse.urljoin(current_url, location)
                if not self._url_allowed(current_url):
                    return None
                continue
            except (urllib.error.URLError, TimeoutError, OSError):
                return None

            head = raw[:2000].lower()
            if (
                "html" in content_type
                or "<html" in head
                or "<!doctype" in head
                or "<body" in head
                or "<div" in head
            ):
                return html_text(raw)[: self.page_chars]
            return raw[: self.page_chars]
        return None

    @staticmethod
    def _read(path: Path) -> dict[str, str | None] | None:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else None
        except (OSError, ValueError):
            return None

    def _write(self, path: Path, value: dict[str, str | None]) -> None:
        with self._lock:
            write_json_atomic(path, value)
