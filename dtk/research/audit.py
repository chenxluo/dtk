"""Mechanical audit of Research Candidate JSONL."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .publish import load_research_records
from .tag_task import TagResearchTask


def audit_research(path: str | Path) -> dict[str, Any]:
    """Recheck structure, duplicate keys, and exact evidence quotes on disk."""
    records = load_research_records(path)
    task = TagResearchTask()
    seen: set[str] = set()
    issues: list[dict[str, Any]] = []
    status_counts = {"completed": 0, "unknown": 0, "other": 0}
    tokens = {"in": 0, "out": 0}

    for record in records:
        key = str(record.get("key", ""))
        status = record.get("status")
        status_counts["completed" if status == "completed" else "unknown" if status == "unknown" else "other"] += 1
        tokens["in"] += record.get("cost_tokens", {}).get("in", 0)
        tokens["out"] += record.get("cost_tokens", {}).get("out", 0)

        errors: list[str] = []
        if key in seen:
            errors.append(f"duplicate key: {key}")
        seen.add(key)
        if record.get("evidence_verified") is not True:
            errors.append("evidence_verified is not true")
        pages = {
            page["url"]: page["text"]
            for page in record.get("evidence_pages", [])
        }
        errors.extend(task.validate_verdict(record, pages))
        if errors:
            issues.append({"tag": record.get("tag", key), "errors": errors})

    return {
        "records": len(records),
        **status_counts,
        "invalid": len(issues),
        "tokens_in": tokens["in"],
        "tokens_out": tokens["out"],
        "issues": issues,
    }
