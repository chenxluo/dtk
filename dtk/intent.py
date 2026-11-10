"""Optional LLM intent gate: classify visual queries, never invent Tags."""
from __future__ import annotations

from typing import Any

from .research.engine import extract_json
from .research.llm import ResearchLLMClient

INTENT_PROMPT = """判断输入是否在请求图像/视觉标签。只输出 JSON：{"intent":"visual"} 或 {"intent":"no_match"}。
visual：明确画面内容、视觉特征、角色、服饰、表情、构图，或已知 Tag/别名。
no_match：与视觉无关、无意义字符串、故意声明不存在的标签、纯程序/数学/生活问题。
不要生成 Tag，不要解释。"""


class IntentClassifier:
    def __init__(self, client: ResearchLLMClient) -> None:
        self.client = client

    def classify(self, query: str) -> str:
        content, _, _ = self.client.chat(
            [
                {"role": "system", "content": INTENT_PROMPT},
                {"role": "user", "content": query},
            ]
        )
        result = extract_json(content)
        intent = result.get("intent") if result else None
        if intent not in {"visual", "no_match"}:
            raise RuntimeError(f"invalid intent response: {content[:120]!r}")
        return intent
