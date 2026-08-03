"""quickjs 執行期錯誤→修復 prompt 用文字的轉換：stack frame 解析、行號換算、shared helper
呼叫點提示。訊息文字係 frozen 契約——直接餵回模型，格式不可變動。"""

import re

from app.engine.html_guard.js_lexer import mask_strings_and_comments

from .context import _SANDBOX_ERROR_MESSAGE_MAX_LENGTH

# ReferenceError 訊息長這樣:"ReferenceError: 'timeout' is not defined"——抓變數名出來
# 加進 stub 集合。白名單校驗(_JS_IDENTIFIER_PATTERN)是防禦性的:抓到的變數名要拼進
# `globalThis.<name> = ...` 源碼字串,驗證失敗就退化成一般例外處理、不重試。
_REFERENCE_ERROR_VAR_PATTERN = re.compile(r"ReferenceError: '([^']+)' is not defined")
_JS_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")

# 單一 block 內「發現新未宣告變數→重建 context→重試」的次數上限,防止 stub 機制失靈時
# 陷入無窮重試迴圈。
_MAX_REFERENCE_ERROR_RETRIES_PER_BLOCK = 8


# `_SANDBOX_PRELUDE` 是獨立 eval 的,它自己內部函式(名稱一律 `__erd` 前綴)以及補上的
# DOM/timer stub 在 stack 裡的行號是相對 prelude 原始碼算的,不是相對 HTML——一旦這類
# frame 混進 `_resolve_error_frames`/`_stack_frame_lines` 的結果,換算出的「HTML 行號」
# 就是憑空捏造的。兩處共用同一份名單,不重複寫字面值。
_SANDBOX_INTERNAL_FRAME_NAME_PREFIX = "__erd"
_SANDBOX_INTERNAL_FRAME_NAMES: frozenset[str] = frozenset(
    {"warn", "setTimeout", "clearTimeout", "setInterval", "clearInterval", "Event", "CustomEvent"}
)


def _is_sandbox_internal_frame_name(function_name: str) -> bool:
    return function_name.startswith(_SANDBOX_INTERNAL_FRAME_NAME_PREFIX) or (
        function_name in _SANDBOX_INTERNAL_FRAME_NAMES
    )


def _resolve_error_frames(
    message: str, html_start_line: int, html_line_count: int
) -> list[tuple[str, int]]:
    """把 quickjs 執行期錯誤的 stack 換算成 [(函式名, HTML 絕對行號)],由深到淺;沒有行號的
    frame 略過,純語法錯誤(無 stack)回空列表。跳過 `_SANDBOX_PRELUDE` 內部 frame 與換算後
    超出 `html_line_count` 的行號——兩者都不對應真實 HTML 位置,寧可少報一層呼叫也不亂報行號。
    """
    frames: list[tuple[str, int]] = []
    for frame_match in _STACK_FRAME_PATTERN.finditer(message):
        if frame_match.group(2) is None:
            continue
        function_name = frame_match.group(1)
        if _is_sandbox_internal_frame_name(function_name):
            continue
        resolved_line = html_start_line + int(frame_match.group(2)) - 1
        if resolved_line > html_line_count:
            continue
        frames.append((function_name, resolved_line))
    return frames


# 共用 helper 的呼叫點:`name(` 出現處,排除 `function name(` 這個定義本身——後者是宣告,
# 不是呼叫,不該被列進「這裡也可能綁錯」的清單。掃描前先用 `mask_strings_and_comments`
# 遮罩,避免註解或字串裡提到的 helper 名稱(例如 `// call getCol(...)`)被誤判成真呼叫點
# ——那種行號寫進修復 prompt 只會叫模型去改文字說明,幫不上忙。
def _helper_call_site_lines(html: str, helper_name: str) -> list[int]:
    call_pattern = re.compile(rf"(?<![\w$.]){re.escape(helper_name)}\s*\(")
    definition_pattern = re.compile(rf"function\s+{re.escape(helper_name)}\s*\(")
    call_site_lines: list[int] = []
    for line_index, line_text in enumerate(mask_strings_and_comments(html).splitlines(), start=1):
        if definition_pattern.search(line_text):
            continue
        if call_pattern.search(line_text):
            call_site_lines.append(line_index)
    return call_site_lines


def _format_execution_error(
    frames: list[tuple[str, int]], script_index: int, first_line: str, html: str
) -> str:
    """Builds the error message fed to the repair prompt. Top-level throws report the throw
    line as-is; throws inside a shared helper (e.g. `getCol`, called from every chart block)
    report the call site instead and list every other call site of that helper, since the same
    defect usually hits all of them. No stack at all falls back to the generic
    `script#N execution error` format.
    """
    if not frames:
        truncated_message = first_line[:_SANDBOX_ERROR_MESSAGE_MAX_LENGTH]
        return f"script#{script_index} execution error: {truncated_message}"

    throwing_function_name, throw_line = frames[0]

    variable_match = _REFERENCE_ERROR_VAR_PATTERN.search(first_line)
    headline = (
        f"ReferenceError '{variable_match.group(1)}' is not defined"
        if variable_match
        else first_line[:_SANDBOX_ERROR_MESSAGE_MAX_LENGTH]
    )

    if len(frames) < 2 or throwing_function_name == "<eval>":
        return f"Line {throw_line}: {headline}"

    # Call-site substitution is only correct when the throwing function is genuinely shared --
    # otherwise a function called from exactly one place would have its real bug line replaced
    # by the blameless invocation line. Reuse the same call-site count that gates the hint below.
    call_site_lines = _helper_call_site_lines(html, throwing_function_name)
    if len(call_site_lines) < 2:
        return f"Line {throw_line}: {headline}"

    call_site_line = frames[1][1]
    shared_helper_hint = (
        f" `{throwing_function_name}` is a shared helper called at lines "
        f"{', '.join(str(line) for line in call_site_lines)} -- the same defect very likely "
        "affects every one of them; fix them all in this round."
    )
    return (
        f"Line {call_site_line}: {headline} (thrown inside `{throwing_function_name}` "
        f"at line {throw_line}).{shared_helper_hint}"
    )


# `(new Error()).stack` 的單一 frame:`    at <name> (<input>[:line])`。
_STACK_FRAME_PATTERN = re.compile(r"^\s*at\s+(\S+)\s+\(<input>(?::(\d+))?\)", re.MULTILINE)


def _stack_frame_lines(stack_text: str) -> list[int]:
    """回傳 stack 由深到淺、帶行號的 frame 行號列表,略過 sandbox 自己的 frame(見
    `_is_sandbox_internal_frame_name`)。quickjs 對部分 frame 不給行號,那些一律略過。"""
    frame_lines: list[int] = []
    for frame_match in _STACK_FRAME_PATTERN.finditer(stack_text):
        if _is_sandbox_internal_frame_name(frame_match.group(1)):
            continue
        if frame_match.group(2) is None:
            continue
        frame_lines.append(int(frame_match.group(2)))
    return frame_lines


def _resolve_stack_call_site_line(stack_text: str, block_start_line: int) -> int | None:
    """把 `(new Error()).stack` 換算成呼叫點的 HTML 絕對行號——stack 第一筆帶行號的 frame
    是 getCol 內部的 `console.warn`,第二筆才是真正呼叫點;只有一筆時防禦性地退回用那一筆。
    """
    frame_lines = _stack_frame_lines(stack_text)
    if len(frame_lines) >= 2:
        relative_line = frame_lines[1]
    elif frame_lines:
        relative_line = frame_lines[0]
    else:
        return None
    return block_start_line + relative_line - 1
