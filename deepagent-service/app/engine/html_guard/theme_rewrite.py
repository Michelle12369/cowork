"""`echarts.init(...)` 呼叫的確定性主題改寫——單參數補上 `'erd'`,雙參數非 'erd' 記錯誤。

只在內嵌 `<script>` 區塊內掃描(`js_lexer.extract_inline_script_spans`),且區塊內文先過
`js_lexer.mask_strings_and_comments` 才找呼叫點與配對括號——HTML body 可見文字裡剛好寫到
`echarts.init(` 這幾個字(說明文案)、或 JS 字串字面值/註解裡的假呼叫都不會被誤觸動,
真正的呼叫也不會因為引數或旁邊註解含撇號而被誤判為「括號不平衡」(遮罩把字串/註解內文
換成空白,不會殘留假的引號狀態)。遮罩後 index 與原文一比一對應,配對到的位置可以直接
切原文,重建輸出時字串引數/註解的原始內容一律保留不遮罩。
"""

from . import js_lexer
from .error_cap import cap_reported_messages
from .rules import _ECHARTS_INIT_CALL_PREFIX

_UNBALANCED_CALL_ERROR = (
    "Found an echarts.init( call whose parentheses never balance -- could not determine where "
    "the call ends, so the 'erd' theme could not be applied. Please fix the call's syntax."
)

# M3: 一次退貨最多列幾條「第二引數不是 'erd'」違規(實測 42 個 init = 5986 字元)。畸形呼叫
# (`_UNBALANCED_CALL_ERROR`)是不同的錯誤類別,量通常很小,不受這個上限影響。
_MAX_REPORTED_NON_ERD_THEME_VIOLATIONS = 8


def _find_matching_close_paren(masked_text: str, open_paren_index: int) -> int | None:
    """回傳 `masked_text[open_paren_index]`（必為 `"("`）對應的閉括號 index；不平衡則回傳
    None。呼叫端 MUST 傳入已跑過 `mask_strings_and_comments` 的文字——字串字面值/註解裡的
    括號在遮罩階段已被換成空白,這裡不需要(也不再)自己追蹤引號狀態。
    """
    depth = 0
    index = open_paren_index
    text_length = len(masked_text)
    while index < text_length:
        character = masked_text[index]
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return None


def _split_top_level_arguments(argument_text: str) -> list[str]:
    """依「最外層逗號」切引數（括號/引號內的逗號不算數）。"""
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


def _rewrite_script_segment(
    segment: str, errors: list[str], non_erd_theme_violations: list[str]
) -> str:
    """對單一內嵌 `<script>` 區塊的原文做 `echarts.init(...)` 改寫。呼叫點搜尋與括號配對都
    在 `mask_strings_and_comments(segment)` 上做(index 與 `segment` 一比一對應),重建輸出時
    再切回 `segment` 本身,字串引數/註解的原始內容一律保留。

    M3:非 'erd' 第二引數的違規另外收進 `non_erd_theme_violations`(由 `_apply_erd_theme`
    在跑完所有區塊後統一截斷+摘要),不直接進 `errors`——畸形呼叫(`_UNBALANCED_CALL_ERROR`)
    是不同的錯誤類別,量通常很小,繼續原樣直接寫進 `errors`,不受這個上限影響。
    """
    masked_segment = js_lexer.mask_strings_and_comments(segment)
    output_parts: list[str] = []
    cursor = 0
    while True:
        call_start = masked_segment.find(_ECHARTS_INIT_CALL_PREFIX, cursor)
        if call_start == -1:
            output_parts.append(segment[cursor:])
            break

        open_paren_index = call_start + len(_ECHARTS_INIT_CALL_PREFIX) - 1
        close_paren_index = _find_matching_close_paren(masked_segment, open_paren_index)
        if close_paren_index is None:
            # 括號不平衡（畸形呼叫）——不再靜默略過:回報錯誤,讓 ok 反映「主題其實沒套上」
            # 的事實,而不是蓋章通過一個看似成功、實則跳過的改寫。
            errors.append(_UNBALANCED_CALL_ERROR)
            output_parts.append(segment[cursor : open_paren_index + 1])
            cursor = open_paren_index + 1
            continue

        output_parts.append(segment[cursor:call_start])
        inner_text = segment[open_paren_index + 1 : close_paren_index]
        arguments = _split_top_level_arguments(inner_text)

        if len(arguments) <= 1:
            element_argument = arguments[0] if arguments else ""
            output_parts.append(f"echarts.init({element_argument}, 'erd')")
        else:
            theme_argument = arguments[1]
            if theme_argument in ("'erd'", '"erd"'):
                output_parts.append(segment[call_start : close_paren_index + 1])
            else:
                non_erd_theme_violations.append(
                    f"echarts.init's second argument must be the 'erd' theme, but is currently "
                    f"{theme_argument}. Please remove the custom theme argument or change it to 'erd'."
                )
                output_parts.append(segment[call_start : close_paren_index + 1])

        cursor = close_paren_index + 1

    return "".join(output_parts)


def _apply_erd_theme(html: str, errors: list[str]) -> str:
    """掃描每個內嵌 `<script>` 區塊裡的 `echarts.init(...)` 呼叫:單參數改寫為帶 `'erd'`
    主題;雙參數且第二參數非 'erd' 則記錄 error、原樣保留。掃描範圍限制在
    `js_lexer.extract_inline_script_spans` 找到的區塊內——HTML body 的可見文字(說明文案等)
    即便字面上寫著 `echarts.init(` 也不受影響;`src=` 外部 script 一律跳過。

    M3:非 'erd' 第二引數的違規全文件收集完才截斷(見 `_rewrite_script_segment`),避免每個
    區塊各自截斷導致總數超過上限——例如 5 個區塊各 3 條違規,若逐區塊截斷在上限 8 以下永遠
    不會觸發摘要句,實際卻已經是 15 條。
    """
    output_parts: list[str] = []
    cursor = 0
    non_erd_theme_violations: list[str] = []
    for content_start, content_end in js_lexer.extract_inline_script_spans(html):
        output_parts.append(html[cursor:content_start])
        output_parts.append(
            _rewrite_script_segment(
                html[content_start:content_end], errors, non_erd_theme_violations
            )
        )
        cursor = content_end
    output_parts.append(html[cursor:])
    errors.extend(
        cap_reported_messages(
            non_erd_theme_violations,
            _MAX_REPORTED_NON_ERD_THEME_VIOLATIONS,
            "echarts.init calls with a non-'erd' theme argument, same fix (use 'erd')",
        )
    )
    return "".join(output_parts)
