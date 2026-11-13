"""Desktop shell: a pywebview window over the in-process web server.

Thinnest possible GUI: same HTML, same server, random loopback port.
First run without local data opens a bootstrap screen that downloads the
published release before swapping in the main window.
Install with `pip install dtk[gui]`.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from .config import load_config
from .web import create_server

_TITLE = "Danbooru Tag Knowledge（DTK）"

_BOOTSTRAP_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>DTK 首次使用</title>
<style>
  body { margin: 0; display: grid; place-items: center; min-height: 100vh;
         background: #fcfbf9; color: #101010;
         font-family: "Segoe UI", "Microsoft YaHei", system-ui, sans-serif; }
  main { width: min(30rem, 90vw); }
  h1 { font-size: 1.2rem; margin: 0 0 .4rem; }
  p { color: #555; font-size: .9rem; line-height: 1.6; margin: 0 0 1.2rem; }
  button { display: block; width: 100%; min-height: 2.6rem; margin-bottom: .6rem;
           font-size: .95rem; cursor: pointer; border-radius: 10px;
           border: 1px solid #e3e1dc; background: #fff; color: #101010; }
  button:hover { background: #f4f2ef; }
  button:disabled { opacity: .5; cursor: not-allowed; }
  button.primary { background: #101010; border-color: #101010; color: #fcfbf9; font-weight: 600; }
  #progress { font-size: .85rem; color: #555; min-height: 1.4em; margin-top: .8rem; }
</style></head>
<body><main>
  <h1>DTK 首次使用</h1>
  <p>本地还没有数据。从 GitHub Release 下载官方数据包；向量包可选（仅在使用相同嵌入模型时有意义，之后也可自行重建）。</p>
  <button class="primary" id="with-vectors" onclick="start(true)">下载数据 + 向量包（约 270 MB）</button>
  <button id="db-only" onclick="start(false)">仅下载数据（约 165 MB）</button>
  <div id="progress"></div>
</main>
<script>
  function progress(text) { document.getElementById('progress').textContent = text; }
  function finish() {
    document.getElementById('progress').textContent = '数据就绪，正在打开 DTK …';
    document.querySelectorAll('button').forEach((b) => (b.disabled = true));
  }
  function fail(message) {
    document.getElementById('progress').textContent = '下载失败：' + message + '（可重试）';
    document.querySelectorAll('button').forEach((b) => (b.disabled = false));
  }
  function start(includeVectors) {
    document.querySelectorAll('button').forEach((b) => (b.disabled = true));
    progress('联系 GitHub …');
    pywebview.api.download(includeVectors);
  }
</script>
</body></html>
"""


def _open_main_window(server_args: dict[str, Any], replace_window: Any = None) -> None:
    """Create the server-backed main window (runs on a worker thread)."""
    import webview

    server = create_server("127.0.0.1", 0, **server_args)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"DTK gui: http://127.0.0.1:{server.server_port}/", flush=True)
    webview.create_window(_TITLE, f"http://127.0.0.1:{server.server_port}/", width=1280, height=860, min_size=(960, 640))
    if replace_window is not None:
        replace_window.destroy()


def launch(
    db_path: str | None = None,
    vectors_path: str | None = None,
    research_vectors_path: str | None = None,
    embedding_model: str | None = None,
    lm_base_url: str | None = None,
) -> None:
    import webview  # deferred: only the gui entry point needs it

    config = load_config()
    server_args: dict[str, Any] = {
        "db_path": db_path or config["db_path"],
        "vectors_path": vectors_path if vectors_path is not None else config["vectors_path"],
        "research_vectors_path": (
            research_vectors_path
            if research_vectors_path is not None
            else config["research_vectors_path"]
        ),
        "embedding_model": embedding_model,
        "lm_base_url": lm_base_url,
    }

    if Path(server_args["db_path"]).is_file():
        _open_main_window(server_args)
        webview.start()
        return

    # First run: bootstrap screen downloads data, then swaps in the main window.
    class _BootstrapApi:
        window: Any = None

        def download(self, include_vectors: bool) -> None:
            from .bootstrap import bootstrap_data

            window = self.window

            def progress(text: str) -> None:
                window.evaluate_js(f"progress({json.dumps(text, ensure_ascii=False)})")

            try:
                bootstrap_data(include_vectors=bool(include_vectors), progress=progress)
                window.evaluate_js("finish()")
                threading.Thread(target=_open_main_window, args=(server_args, window)).start()
            except Exception as exc:  # noqa: BLE001 - surfaced to the bootstrap page
                window.evaluate_js(f"fail({json.dumps(str(exc), ensure_ascii=False)})")

    api = _BootstrapApi()
    api.window = webview.create_window(
        "DTK 首次使用", html=_BOOTSTRAP_HTML, js_api=api, width=560, height=440, resizable=False
    )
    webview.start()
