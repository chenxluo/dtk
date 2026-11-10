"""Danbooru Tag research task: strict schema plus verbatim-evidence checks."""
from __future__ import annotations

from typing import Any, Mapping

VERDICT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "status",
        "definition_zh",
        "semantic_documents",
        "evidence",
        "unresolved_questions",
    ],
    "properties": {
        "status": {"type": "string", "enum": ["completed", "unknown"]},
        "definition_zh": {"type": "string"},
        "semantic_documents": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["kind", "language", "text"],
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["visual", "synonym", "contextual", "disambiguation"],
                    },
                    "language": {"type": "string", "enum": ["zh", "en"]},
                    "text": {"type": "string", "minLength": 4},
                },
            },
        },
        "evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["url", "quote"],
                "properties": {
                    "url": {"type": "string", "minLength": 8},
                    "quote": {"type": "string", "minLength": 12},
                },
            },
        },
        "unresolved_questions": {
            "type": "array",
            "items": {"type": "string", "minLength": 4},
        },
    },
}


def validate_schema(instance: Any, schema: dict[str, Any]) -> list[str]:
    """Validate the small JSON-schema subset used by research verdicts."""
    errors: list[str] = []
    expected = schema.get("type")
    checks = {
        "object": lambda value: isinstance(value, dict),
        "array": lambda value: isinstance(value, list),
        "string": lambda value: isinstance(value, str),
    }
    if expected:
        check = checks.get(expected)
        if check is None:
            errors.append(f"unsupported schema type {expected!r}")
        elif not check(instance):
            errors.append(f"expected {expected}, got {type(instance).__name__}")
            return errors
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"value {instance!r} not in enum {schema['enum']}")

    if isinstance(instance, dict):
        for key in schema.get("required", []):
            if key not in instance:
                errors.append(f"missing required key {key!r}")
        for key, child in schema.get("properties", {}).items():
            if key in instance:
                errors.extend(f"{key}: {error}" for error in validate_schema(instance[key], child))
    if isinstance(instance, list):
        minimum = schema.get("minItems")
        if minimum is not None and len(instance) < minimum:
            errors.append(f"needs >= {minimum} items, got {len(instance)}")
        child = schema.get("items")
        if child:
            for index, item in enumerate(instance):
                errors.extend(f"[{index}]: {error}" for error in validate_schema(item, child))
    if isinstance(instance, str):
        minimum = schema.get("minLength")
        if minimum is not None and len(instance) < minimum:
            errors.append(f"string too short ({len(instance)} < {minimum})")
    return errors


class TagResearchTask:
    """Research one missing-Wiki Tag and emit a Research Candidate."""

    system_prompt = (
        "你是 Danbooru Tag 研究员。官方快照数据绝对优先；不能用模型记忆覆盖官方 Wiki。"
        "结论必须来自本次抓取到的网页原文。引用 quote 必须逐字复制网页正文中的连续片段，"
        "不得改写、拼接或翻译。资料不足时必须输出 status=unknown，并说明未解决问题；"
        "禁止为了完成任务而编造定义或证据。不要发明不存在的 Tag。"
    )

    verdict_schema = VERDICT_SCHEMA

    def unit_prompt(self, unit: dict[str, Any]) -> str:
        official = {
            "tag": unit["tag"],
            "type": unit["type"],
            "post_count": unit["post_count"],
            "official_wiki": unit["wiki"],
            "aliases": unit["aliases"],
        }
        return (
            "研究以下 Danbooru Tag 的视觉含义，产出中文定义和中文/英文检索表达。\n"
            "输出 JSON：status=completed 仅当有可逐字引用的外部证据；否则 status=unknown。\n"
            f"可能的 Danbooru Wiki URL（本站可能不可用）：https://danbooru.donmai.us/wiki_pages/{unit['tag']}\n"
            f"可参考的跨站 Wiki URL（非官方权威，需判断语义是否一致）：https://e621.net/wiki_pages/{unit['tag']}\n"
             "completed 要求：definition_zh 至少 20 个字符；semantic_documents 至少 1 条；"
             "evidence 至少 1 条且 quote 逐字来自已抓取网页。"
            "semantic_documents 每项必须恰好使用这些字段："
            '{"kind":"visual|synonym|contextual|disambiguation","language":"zh|en","text":"检索表达"}； '
            "不要放 URL、title、note、网页原文。evidence 每项必须恰好使用："
            '{"url":"已抓取URL","quote":"页面正文连续原文"}。 '
            "status=unknown 时 semantic_documents 和 evidence 都应为空数组。"
        )

    def seed_query(self, unit: dict[str, Any]) -> str:
        return f"danbooru {unit['tag']} tag meaning"

    def validate_verdict(
        self, verdict: dict[str, Any], pages: Mapping[str, str]
    ) -> list[str]:
        errors = validate_schema(verdict, self.verdict_schema)
        if errors:
            return errors

        for index, item in enumerate(verdict["evidence"]):
            url = item["url"]
            if url not in pages:
                errors.append(f"evidence[{index}]: url was not fetched in this session")
                continue
            if item["quote"] not in pages[url]:
                errors.append(f"evidence[{index}]: quote is not a verbatim page substring")

        if verdict["status"] == "completed":
            if len(verdict["definition_zh"]) < 20:
                errors.append("completed definition_zh needs at least 20 characters")
            if not verdict["semantic_documents"]:
                errors.append("completed verdict needs at least one semantic document")
            if not verdict["evidence"]:
                errors.append("completed verdict needs verbatim external evidence")
        elif not verdict["unresolved_questions"]:
            errors.append("unknown verdict must explain what is missing")
        return errors
