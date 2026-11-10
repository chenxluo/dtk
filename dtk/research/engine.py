"""Clean-context act loop for offline Tag research.

The model never writes to the Derived Store. It can only request searches and
page fetches, then return a verdict validated by the task and the engine.
A completed verdict must quote fetched text verbatim; insufficient evidence is
recorded as `unknown`, never forced into a plausible definition.
"""
from __future__ import annotations

import contextlib
import json
import msvcrt
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from .tag_task import TagResearchTask, validate_schema

ACT_PROTOCOL = """每次只输出一个 JSON 对象，三选一：
{{"search": ["query1", "query2"]}}   # 最多 2 条搜索，消耗 1 个动作
{{"fetch": ["https://...", ...]}}     # 每个 URL 消耗 1 个动作
最终结论：{{"status": "completed|unknown", "definition_zh": "...", "semantic_documents": [...], "evidence": [...], "unresolved_questions": [...]}}

剩余动作：{left}
{seed}不要输出 Markdown 或解释；只输出 JSON。"""

REASK = "输出不符合 schema 或证据规则。只重新输出一个合法结论 JSON 对象。错误：{errors}"
FORCED_FINAL = (
    "动作预算已耗尽。不能继续 search/fetch。只输出最终结论 JSON；"
    "若没有可逐字引用的网页证据，必须输出 status=unknown，不得猜测。"
)


class ChatClient(Protocol):
    def chat(self, messages: list[dict[str, str]]) -> tuple[str, dict[str, int], bool]:
        ...


class SearchClient(Protocol):
    def search(self, query: str) -> list[dict[str, str]]:
        ...


class FetchClient(Protocol):
    def fetch(self, url: str) -> str | None:
        ...


def extract_json(text: str) -> dict[str, Any] | None:
    """First balanced JSON object, tolerating Qwen thinking blocks and fences."""
    if not isinstance(text, str):
        return None
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    if "<think>" in cleaned:
        cleaned = cleaned.split("<think>", 1)[0]
    cleaned = cleaned.replace("```json", "\n").replace("```", "\n")

    for start, character in enumerate(cleaned):
        if character != "{":
            continue
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(cleaned)):
            char = cleaned[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        value = json.loads(cleaned[start : index + 1])
                        return value if isinstance(value, dict) else None
                    except ValueError:
                        break
        # ponytail: O(n²) rescan is bounded by short model replies.
    return None


def _append_line(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False) + "\n")
        handle.flush()


def _load_keys(path: Path) -> set[str]:
    if not path.exists():
        return set()
    keys: set[str] = set()
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict) and record.get("key") is not None:
                keys.add(record["key"])
    return keys


@contextlib.contextmanager
def _output_lock(destination: Path):
    destination.parent.mkdir(parents=True, exist_ok=True)
    lock_path = destination.with_name(destination.name + ".lock")
    with lock_path.open("a+") as handle:
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            raise RuntimeError(
                f"another research run owns {destination}: {lock_path}"
            ) from error
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def _observation(protocol: str, body: str) -> dict[str, str]:
    return {"role": "user", "content": body + "\n\n" + protocol}


def _run_unit(
    task: TagResearchTask,
    unit: dict[str, Any],
    output_path: Path,
    llm: ChatClient,
    search: SearchClient,
    fetcher: FetchClient,
    max_acts: int,
    top_n: int,
) -> dict[str, Any] | None:
    budget = max_acts
    max_turns = max(max_acts * 3, 6)
    turns = 0
    queries_used: list[str] = []
    pages: dict[str, str] = {}
    tokens = {"in": 0, "out": 0}
    seed = task.seed_query(unit)
    protocol = ACT_PROTOCOL.format(
        left=budget, seed=f"建议首选查询（可不用）：{seed}\n" if seed else ""
    )
    messages: list[dict[str, str]] = [
        {"role": "system", "content": task.system_prompt},
        {"role": "user", "content": task.unit_prompt(unit) + "\n\n" + protocol},
    ]

    def chat() -> str:
        content, usage, _ = llm.chat(messages)
        tokens["in"] += usage.get("in", 0)
        tokens["out"] += usage.get("out", 0)
        return content

    while True:
        turns += 1
        if turns > max_turns:
            print(
                f"[research] FAIL tag={unit['tag']!r}: exceeded {max_turns} act-loop turns",
                file=sys.stderr,
            )
            return {
                "status": "failed",
                "errors": [f"exceeded {max_turns} act-loop turns"],
                "cost_tokens": dict(tokens),
                "acts": max_acts - budget,
            }

        content = chat()
        action = extract_json(content)
        if action is None:
            action = {}
        messages.append({"role": "assistant", "content": content})
        tail = ACT_PROTOCOL.format(left=max(budget, 0), seed="")

        search_queries = action.get("search")
        if budget > 0 and isinstance(search_queries, list) and search_queries:
            queries = [query for query in search_queries if isinstance(query, str) and query.strip()][:2]
            budget -= 1
            lines: list[str] = []
            for query in queries:
                queries_used.append(query)
                results = search.search(query)
                if not results:
                    lines.append(f'搜索 "{query}" 无结果')
                for result in results[:top_n]:
                    snippet = (result.get("content") or "").strip().replace("\n", " ")[:200]
                    lines.append(
                        f"{result['url']} — {(result.get('title') or '')[:80]}\n  摘要：{snippet}"
                    )
            messages.append(_observation(tail, "## 搜索结果\n" + "\n".join(lines)))
            continue

        fetch_urls = action.get("fetch")
        if budget > 0 and isinstance(fetch_urls, list) and fetch_urls:
            urls = [
                url
                for url in fetch_urls
                if isinstance(url, str) and url.startswith(("http://", "https://")) and url not in pages
            ][:budget]
            budget -= len(urls)
            parts: list[str] = []
            for url in urls:
                text = fetcher.fetch(url)
                if text:
                    pages[url] = text
                    parts.append(f"{url}\n{text}")
                else:
                    parts.append(f"{url} 抓取失败（超时、屏蔽或非文本），换下一个来源")
            messages.append(_observation(tail, "## 抓取正文\n" + "\n\n".join(parts)))
            continue

        if budget <= 0 and (search_queries or fetch_urls):
            messages.append(_observation(tail, FORCED_FINAL))
            continue

        verdict = action
        errors = validate_schema(verdict, task.verdict_schema)
        if not errors:
            errors = task.validate_verdict(verdict, pages)
        if errors:
            messages.append(
                {
                    "role": "user",
                    "content": REASK.format(errors="; ".join(errors[:6])),
                }
            )
            retry_content = chat()
            retry = extract_json(retry_content)
            messages.append({"role": "assistant", "content": retry_content})
            verdict = retry if isinstance(retry, dict) else {}
            errors = validate_schema(verdict, task.verdict_schema)
            if not errors:
                errors = task.validate_verdict(verdict, pages)
        if errors:
            print(
                f"[research] FAIL tag={unit['tag']!r}: {'; '.join(errors[:6])}",
                file=sys.stderr,
            )
            return {
                "status": "failed",
                "errors": errors,
                "cost_tokens": dict(tokens),
                "acts": max_acts - budget,
            }

        evidence_urls = list(dict.fromkeys(item["url"] for item in verdict["evidence"]))
        record = {
            **verdict,
            "key": unit["key"],
            "tag_id": unit["tag_id"],
            "tag": unit["tag"],
            "type": unit["type"],
            "post_count": unit["post_count"],
            "official_wiki": unit["wiki"],
            "aliases": unit["aliases"],
            "evidence_verified": True,
            "evidence_pages": [{"url": url, "text": pages[url]} for url in evidence_urls],
            "queries_used": queries_used,
            "acts": max_acts - budget,
            "cost_tokens": dict(tokens),
            "model_generated_at": datetime.now(timezone.utc).isoformat(),
        }
        _append_line(output_path, record)
        print(
            f"[research] ok tag={unit['tag']!r} status={record['status']} "
            f"acts={record['acts']} tokens={tokens['in']}+{tokens['out']}",
            file=sys.stderr,
        )
        return record


def run_research(
    task: TagResearchTask,
    units: list[dict[str, Any]],
    output_path: str | Path,
    *,
    llm: ChatClient,
    search: SearchClient,
    fetcher: FetchClient,
    max_acts: int = 6,
    top_n: int = 5,
) -> dict[str, Any]:
    """Run queued units sequentially and resume from existing JSONL keys."""
    if max_acts < 0:
        raise ValueError("max_acts must not be negative")
    if top_n <= 0:
        raise ValueError("top_n must be positive")

    destination = Path(output_path)
    with _output_lock(destination):
        done = _load_keys(destination)
        todo = [unit for unit in units if unit.get("key") not in done]
        stats: dict[str, Any] = {
            "units_total": len(units),
            "units_skipped": len(units) - len(todo),
            "units_done": 0,
            "units_unknown": 0,
            "units_failed": 0,
            "tokens_in": 0,
            "tokens_out": 0,
            "wall_s": 0.0,
        }
        started = time.monotonic()
        for unit in todo:
            record = _run_unit(
                task,
                unit,
                destination,
                llm,
                search,
                fetcher,
                max_acts=max_acts,
                top_n=top_n,
            )
            if record is None or record.get("status") == "failed":
                stats["units_failed"] += 1
            else:
                stats["units_done"] += 1
                if record["status"] == "unknown":
                    stats["units_unknown"] += 1
            stats["tokens_in"] += record["cost_tokens"]["in"]
            stats["tokens_out"] += record["cost_tokens"]["out"]
        stats["wall_s"] = round(time.monotonic() - started, 3)
        return stats
