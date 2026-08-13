"""模型最終回答中的 ```questions fenced block 抽取——解析成反問卡片清單。

engine 層——stdlib only,禁止 import 任何 LLM 框架(ruff TID251 會擋)。
"""

import json
import re

_QUESTIONS_FENCE_PATTERN = re.compile(r"```questions\s*\n(.*?)```", re.DOTALL)


def _normalize_question(raw_question: object) -> dict | None:
    """單一問題物件正規化;`text` 缺漏或非非空字串視為整塊無效(回傳 None)。"""
    if not isinstance(raw_question, dict):
        return None
    text = raw_question.get("text")
    if not isinstance(text, str) or not text.strip():
        return None
    options = raw_question.get("options")
    if not isinstance(options, list):
        options = []
    multi_select = raw_question.get("multiSelect")
    if not isinstance(multi_select, bool):
        multi_select = False
    return {"text": text, "options": options, "multiSelect": multi_select}


def extract_questions_block(answer_text: str) -> tuple[str, list[dict] | None]:
    """取出模型最終回答中第一個 ```questions 區塊,解析成反問卡片清單。

    成功時回傳(去除該區塊、首尾空白整理過的文字, 問題 dict 清單);沒有區塊、JSON 解析失敗、
    或任一問題物件形狀不合法(缺 `text`)時,回傳(原文字未經修改, None)——never raise,
    交由呼叫端(finalize())原樣處理。
    """
    fence_match = _QUESTIONS_FENCE_PATTERN.search(answer_text)
    if fence_match is None:
        return answer_text, None

    try:
        parsed = json.loads(fence_match.group(1))
    except json.JSONDecodeError:
        return answer_text, None

    if not isinstance(parsed, list) or not parsed:
        return answer_text, None

    questions: list[dict] = []
    for raw_question in parsed:
        normalized = _normalize_question(raw_question)
        if normalized is None:
            return answer_text, None
        questions.append(normalized)

    remaining_text = (answer_text[: fence_match.start()] + answer_text[fence_match.end() :]).strip()
    return remaining_text, questions
