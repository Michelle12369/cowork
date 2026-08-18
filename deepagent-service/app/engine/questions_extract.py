"""模型最終回答中的 ```questions fenced block 抽取——解析成反問卡片清單。

engine 層——stdlib only,禁止 import 任何 LLM 框架(ruff TID251 會擋)。
"""

import json
import re

_QUESTIONS_FENCE_PATTERN = re.compile(r"```questions\s*\n(.*?)```", re.DOTALL)
# 誤標 fence 救回用——抓任何語言標籤(含空標籤＝裸 fence)的 fenced block,逐一檢查內容是否
# 「形狀像反問區塊」;只在找不到正確標記的 ```questions fence 時才啟用(見下方 priority 2)。
_GENERIC_FENCE_PATTERN = re.compile(r"```(\w*)\s*\n(.*?)```", re.DOTALL)


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


def _is_questions_shaped(parsed: object) -> bool:
    """非空陣列、且每個元素都是含非空字串 `text` 的 object,才算「形狀像反問區塊」——
    用來從誤標 fence 救回,同時嚴格排除資料列陣列(元素無 text 鍵)與 ECharts option(非陣列)。"""
    if not isinstance(parsed, list) or not parsed:
        return False
    for raw_question in parsed:
        if not isinstance(raw_question, dict):
            return False
        text = raw_question.get("text")
        if not isinstance(text, str) or not text.strip():
            return False
    return True


def _parse_questions_payload(fence_content: str) -> list[dict] | None:
    """單一 fence 內容解析成問題清單;JSON 解析失敗或形狀不合法一律回傳 None(never raise)。"""
    try:
        parsed = json.loads(fence_content)
    except (json.JSONDecodeError, RecursionError, ValueError):
        # RecursionError:深度巢狀陣列(CPython json 解析器遞迴下降實作)。
        # ValueError:超長數字字面量(int/float 轉換超過 sys.get_int_max_str_digits 上限)。
        return None
    if not _is_questions_shaped(parsed):
        return None

    questions: list[dict] = []
    for raw_question in parsed:
        normalized = _normalize_question(raw_question)
        if normalized is None:
            return None
        questions.append(normalized)
    return questions


def extract_questions_block(answer_text: str) -> tuple[str, list[dict] | None]:
    """取出模型最終回答中的反問區塊,解析成反問卡片清單。

    Priority 1:第一個 ```questions fence——找到就用它(不論解析成敗都不再往下找),語言標籤
    正確標記是常態路徑。Priority 2:找不到正確標記的 ```questions fence 時,依序掃描其餘所有
    fence(```json、裸 fence 等),取第一個「形狀像反問區塊」的救回——模型偶爾把 fence 語言
    標錯,內容本身仍是合法問題 JSON。

    成功時回傳(去除該區塊、首尾空白整理過的文字, 問題 dict 清單);兩種 priority 都沒有匹配、
    JSON 解析失敗、或問題物件形狀不合法(缺 `text`)時,回傳(原文字未經修改, None)——
    never raise,交由呼叫端(finalize())原樣處理。
    """
    fence_match = _QUESTIONS_FENCE_PATTERN.search(answer_text)
    if fence_match is not None:
        questions = _parse_questions_payload(fence_match.group(1))
        if questions is None:
            return answer_text, None
        remaining_text = (
            answer_text[: fence_match.start()] + answer_text[fence_match.end() :]
        ).strip()
        return remaining_text, questions

    for generic_match in _GENERIC_FENCE_PATTERN.finditer(answer_text):
        if generic_match.group(1) == "questions":
            continue
        questions = _parse_questions_payload(generic_match.group(2))
        if questions is None:
            continue
        remaining_text = (
            answer_text[: generic_match.start()] + answer_text[generic_match.end() :]
        ).strip()
        return remaining_text, questions

    return answer_text, None
