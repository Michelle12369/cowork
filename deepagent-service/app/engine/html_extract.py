"""模型回應中的 ```html fenced block 抽取。

engine 層——stdlib only,禁止 import 任何 LLM 框架(ruff TID251 會擋)。
"""

import re

_HTML_FENCE_PATTERN = re.compile(r"```(?:html)?\s*\n(.*?)```", re.DOTALL)


def extract_html_block(model_response_text: str) -> str:
    """取出模型的 ```html 區塊;沒有 fence 時退回整段 strip 後的原文——never raise,
    交由下游(theme 改寫、結果注入)原樣處理。"""
    fence_match = _HTML_FENCE_PATTERN.search(model_response_text)
    return fence_match.group(1).strip() if fence_match else model_response_text.strip()
