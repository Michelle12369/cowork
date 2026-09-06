"""確定性改寫每個 echarts.init(...) 呼叫: 只有一個參數時補上 'erd' 主題, 其他情況原樣保留.

這是 engine 層, 只能用 stdlib, 不能 import 任何 LLM 框架(ruff 的 TID251 規則會擋下來). Java
端的 ArtifactAssembler 在組裝時會注入 registerTheme('erd') 這段腳本, 但圖表要用
echarts.init(el, 'erd') 呼叫才吃得到那份主題, 所以這道改寫要繼續獨立存在, 不能跟著確定性
檢查層一起移除.
"""

_ECHARTS_INIT_CALL_PREFIX = "echarts.init("


def _find_matching_close_paren(text: str, open_paren_index: int) -> int | None:
    """回傳 text[open_paren_index](一定是左括號)對應的右括號 index, 括號不平衡就回傳 None.

    引號內出現的括號字元不算數, 不會計入深度.
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
    """按最外層的逗號切開參數列表, 括號或引號裡面的逗號不算數."""
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
    """掃描每一個 echarts.init(...) 呼叫: 只有一個參數的就改寫成帶 'erd' 主題, 其他情況(已經
    有第二個參數, 或括號不平衡的畸形呼叫)一律原樣保留, 也不記錯誤, 因為這裡沒有 guard 層可以
    回報, 盡量改, 改不了就放過. 用括號深度平衡的方式掃描, 可以正確處理參數本身就帶括號的呼叫,
    例如 document.getElementById(...).
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
            # 括號不平衡, 這是個畸形呼叫, 原樣保留並跳過, 繼續往下掃描.
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
            # 已經有第二個參數了, 不管是不是 'erd', 都原樣保留, 不再判斷或記錯誤.
            output_parts.append(html[call_start : close_paren_index + 1])

        cursor = close_paren_index + 1

    return "".join(output_parts)
