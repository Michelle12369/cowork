import json
import re

_QUESTIONS_FENCE_PATTERN = re.compile(r"```questions\s*\n(.*?)```", re.DOTALL)


def _normalize_question(raw_question: object) -> dict | None:
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
    fence_match = _QUESTIONS_FENCE_PATTERN.search(answer_text)
    if fence_match is None:
        return answer_text, None

    try:
        parsed = json.loads(fence_match.group(1))
    except (json.JSONDecodeError, RecursionError, ValueError):
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
