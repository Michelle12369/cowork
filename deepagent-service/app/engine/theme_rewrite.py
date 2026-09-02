"""`echarts.init(...)` 呼叫的確定性主題改寫——單參數補上 `'erd'`,其餘原樣保留。

engine 層——stdlib only,禁止 import 任何 LLM 框架(ruff TID251 會擋)。Java 端 ArtifactAssembler
在 assemble 時注入 `registerTheme('erd')` 腳本,圖表只有 `echarts.init(el, 'erd')` 才吃得到那份
主題,故這道改寫仍要獨立存在(不隨確定性檢查層一起移除)。
"""

_ECHARTS_INIT_CALL_PREFIX = "echarts.init("


def _find_matching_close_paren(text: str, open_paren_index: int) -> int | None:
    """回傳 `text[open_paren_index]`(必為 `"("`)對應的閉括號 index;不平衡則回傳 None。

    對字串字面值中的括號免疫(`"("`/`)"` 出現在引號內不計入深度)。
    """
    depth = 0
    quote_char: str | None = None
    index = open_paren_index
    text_length = len(text)
    while index < text_length:
        character = text[index]
        if quote_char is not None:
            if character == "\\":
                index += 2
                continue
            if character == quote_char:
                quote_char = None
            index += 1
            continue
        if character in ("'", '"'):
            quote_char = character
        elif character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return None


def _split_top_level_arguments(argument_text: str) -> list[str]:
    """依「最外層逗號」切引數(括號/引號內的逗號不算數)。"""
    if not argument_text.strip():
        return []

    arguments: list[str] = []
    current_argument_characters: list[str] = []
    depth = 0
    quote_char: str | None = None
    for character in argument_text:
        if quote_char is not None:
            current_argument_characters.append(character)
            if character == quote_char:
                quote_char = None
            continue
        if character in ("'", '"'):
            quote_char = character
            current_argument_characters.append(character)
        elif character in "([{":
            depth += 1
            current_argument_characters.append(character)
        elif character in ")]}":
            depth -= 1
            current_argument_characters.append(character)
        elif character == "," and depth == 0:
            arguments.append("".join(current_argument_characters).strip())
            current_argument_characters = []
        else:
            current_argument_characters.append(character)
    arguments.append("".join(current_argument_characters).strip())
    return arguments


def apply_erd_theme(html: str) -> str:
    """掃描每個 `echarts.init(...)` 呼叫:單參數改寫為帶 `'erd'` 主題;其餘呼叫(已帶第二參數、
    或括號不平衡的畸形呼叫)原樣保留,不記錯誤——沒有 guard 層可回報,盡力改寫、改不了就放過。
    用括號深度平衡掃描,可正確處理引數本身含括號的呼叫(例如 `document.getElementById(...)`)。
    """
    output_parts: list[str] = []
    cursor = 0
    while True:
        call_start = html.find(_ECHARTS_INIT_CALL_PREFIX, cursor)
        if call_start == -1:
            output_parts.append(html[cursor:])
            break

        open_paren_index = call_start + len(_ECHARTS_INIT_CALL_PREFIX) - 1
        close_paren_index = _find_matching_close_paren(html, open_paren_index)
        if close_paren_index is None:
            # 括號不平衡(畸形呼叫),原樣保留、跳過此次呼叫繼續掃描。
            output_parts.append(html[cursor : open_paren_index + 1])
            cursor = open_paren_index + 1
            continue

        output_parts.append(html[cursor:call_start])
        inner_text = html[open_paren_index + 1 : close_paren_index]
        arguments = _split_top_level_arguments(inner_text)

        if len(arguments) <= 1:
            element_argument = arguments[0] if arguments else ""
            output_parts.append(f"echarts.init({element_argument}, 'erd')")
        else:
            # 已有第二參數(無論是不是 'erd')——原樣保留,不再判斷/記錯誤。
            output_parts.append(html[call_start : close_paren_index + 1])

        cursor = close_paren_index + 1

    return "".join(output_parts)
