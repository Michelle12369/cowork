"""Level 1:parse-only JS 語法檢查——把每段 inline script 丟給 quickjs eval,只解析不執行。"""

import logging
import re

from . import js_runtime
from .js_lexer import extract_inline_scripts_with_lines

logger = logging.getLogger(__name__)

# quickjs 的錯誤訊息帶行號,但格式依「是否有呼叫堆疊」而不同:純語法錯誤(parse 階段)無堆疊,
# 執行期錯誤有堆疊、可能多層——多層時「最深」那筆排最前面,故用 `search` 找第一筆而非要求
# 固定前綴(見 _resolve_error_frames)。
_QUICKJS_ERROR_LOCATION_PATTERN = re.compile(r"<input>:(\d+)")
# check_js_syntax 把每段 script 內容包進 `(function(){\n<content>\n})` 再丟給 quickjs
# eval——只是「定義」這個函式表達式(不呼叫),JS 引擎仍會對函式本體做完整語法解析、但
# 不執行內容,等同 parse-only。包裝多出的這一行前綴要從回報的行號扣掉。
_JS_SYNTAX_CHECK_WRAPPER_LINE_OFFSET = 1


def check_js_syntax(html: str, errors: list[str]) -> None:
    """Level 1:每段 script 包進 `(function(){...})` 丟給 quickjs eval,只解析不執行,
    只抓 SyntaxError。quickjs 不可用時記 warning、跳過此規則(驗證器掛掉不擋主流程)。
    """
    if not js_runtime.QUICKJS_AVAILABLE:
        logger.warning("html_guard: quickjs 未安裝，跳過 JS 語法檢查")
        return

    for script_index, (script_content, html_start_line) in enumerate(
        extract_inline_scripts_with_lines(html)
    ):
        wrapped_source = f"(function(){{\n{script_content}\n}})"
        try:
            js_runtime.quickjs.Context().eval(wrapped_source)
        except js_runtime.quickjs.JSException as syntax_error:
            message = str(syntax_error)
            location_match = _QUICKJS_ERROR_LOCATION_PATTERN.search(message)
            if location_match:
                script_relative_line = max(
                    int(location_match.group(1)) - _JS_SYNTAX_CHECK_WRAPPER_LINE_OFFSET, 1
                )
                html_line = html_start_line + script_relative_line - 1
            else:
                html_line = html_start_line
            first_line = message.splitlines()[0] if message else message
            errors.append(f"script#{script_index} line {html_line} JS syntax error: {first_line}")
        except Exception as unexpected_error:  # noqa: BLE001 -- 驗證器掛掉不擋主流程
            logger.warning(
                "html_guard: quickjs 檢查 script#%d 時發生非預期例外，跳過該段檢查: %s",
                script_index,
                unexpected_error,
            )
