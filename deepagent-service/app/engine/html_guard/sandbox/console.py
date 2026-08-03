"""sandbox `console.error`/`console.warn` 收集器讀出與轉譯——被 chart try/catch 擋下的執行期
錯誤、getCol 找不到欄位的警告，都在這裡轉成餵回模型的 guard error。"""

import json
import logging
import re

from app.engine.html_guard import js_runtime

from .context import _SANDBOX_ERROR_MESSAGE_MAX_LENGTH
from .errors import _resolve_stack_call_site_line

logger = logging.getLogger(__name__)

# skill 規定的 chart try/catch 範本固定寫法:`console.error('[ERD] chart <名稱> failed:',
# error)`。`.+?` 非貪婪比對名稱(可能含空格/連字號),`re.DOTALL` 讓底層錯誤訊息可跨行。
_CHART_CONSOLE_ERROR_PATTERN = re.compile(r"^\[ERD\] chart (.+?) failed:\s*(.*)$", re.DOTALL)


def _read_collected_console_errors(context: "js_runtime.quickjs.Context") -> list[str]:
    """讀出 sandbox `console.error` 收集器(見 `_SANDBOX_PRELUDE`)目前累積的訊息列表。

    只在所有 block 跑完後呼叫一次,讀的是最終那個 context(重試時重建的舊 context 執行
    紀錄從未被讀取,不會重複計入)。讀取失敗記 warning、回空列表,不擋主流程。
    """
    try:
        serialized_errors = context.eval("JSON.stringify(__erd_console_errors__)")
        return json.loads(serialized_errors)
    except Exception as read_error:  # noqa: BLE001 -- 驗證器掛掉不擋主流程
        logger.warning(
            "html_guard: 讀取 sandbox console.error 收集結果失敗，跳過偵測: %s", read_error
        )
        return []


def _check_swallowed_chart_errors(console_error_messages: list[str]) -> list[str]:
    """把符合 `[ERD] chart <名稱> failed: ...` 格式的 `console.error` 訊息轉成 guard
    error——這些是被 try/catch 擋下、不會冒出 quickjs.JSException 的執行期錯誤,不轉成
    guard error 就永遠不會被攔到。非此格式的 `console.error` 一律忽略,不誤傷。
    """
    errors: list[str] = []
    for message in console_error_messages:
        match = _CHART_CONSOLE_ERROR_PATTERN.match(message)
        if match is None:
            continue
        chart_name = match.group(1)
        underlying_error = match.group(2)[:_SANDBOX_ERROR_MESSAGE_MAX_LENGTH]
        errors.append(
            f"Chart '{chart_name}' threw at runtime (caught by its try/catch): "
            f"{underlying_error}. Fix the underlying error — the try/catch is damage "
            "control, not a fix."
        )
    return errors


# getCol 樣板的固定寫法:`console.warn('[ERD] column not found:', candidates)`;candidates 是
# 陣列,`String(array)` 會變成逗號串接的字串。
_COLUMN_NOT_FOUND_PATTERN = re.compile(r"^\[ERD\] column not found:\s*(.*)$", re.DOTALL)

# 一次退貨最多列幾條 getCol miss——修復 prompt 不能無限長,超出的用一行摘要帶過。
_MAX_REPORTED_COLUMN_MISSES = 8


def _owning_query_ids_for_column(column_name: str, results: dict[str, dict]) -> list[str]:
    """哪些 query result 真的有這個欄位——讓退貨訊息能直接寫出「該欄位存在於 qN」。"""
    return sorted(
        query_id
        for query_id, result in results.items()
        if column_name in (result.get("columns") or [])
    )


def _read_collected_console_warnings(context: "js_runtime.quickjs.Context") -> list[dict]:
    """讀出 sandbox `console.warn` 收集器(見 `_SANDBOX_PRELUDE`)目前累積的紀錄。讀取
    失敗記 warning、回空列表,不擋主流程。"""
    try:
        serialized_warnings = context.eval("JSON.stringify(__erd_console_warnings__)")
        return json.loads(serialized_warnings)
    except Exception as read_error:  # noqa: BLE001 -- 驗證器掛掉不擋主流程
        logger.warning(
            "html_guard: 讀取 sandbox console.warn 收集結果失敗，跳過偵測: %s", read_error
        )
        return []


def _check_column_not_found_warnings(
    collected_warnings: list[dict], results: dict[str, dict], html_lines: list[str]
) -> list[str]:
    """把 `[ERD] column not found: ...` 的 warn 轉成 guard error。

    行號取 stack 的呼叫點 frame(見 `_resolve_stack_call_site_line`);候選欄位再回頭比對
    真實 `results`,算出「該欄位其實在哪個 qN」,讓模型一輪修完而不是猜。
    """
    errors: list[str] = []
    seen_call_sites: set[tuple[int, str]] = set()
    for warning in collected_warnings:
        message_match = _COLUMN_NOT_FOUND_PATTERN.match(str(warning.get("message", "")))
        if message_match is None:
            continue
        candidate_columns = [
            part.strip() for part in message_match.group(1).split(",") if part.strip()
        ]
        if not candidate_columns:
            continue

        block_start_line = int(warning.get("base", 1))
        html_line = _resolve_stack_call_site_line(str(warning.get("stack", "")), block_start_line)

        deduplication_key = (html_line or -1, ",".join(candidate_columns))
        if deduplication_key in seen_call_sites:
            continue
        seen_call_sites.add(deduplication_key)

        location_hint = f"Line {html_line}: " if html_line is not None else ""
        source_line = (
            html_lines[html_line - 1].strip()[:120]
            if html_line is not None and 0 < html_line <= len(html_lines)
            else ""
        )
        owning_hints = []
        for candidate_column in candidate_columns:
            owning_query_ids = _owning_query_ids_for_column(candidate_column, results)
            if owning_query_ids:
                owning_hints.append(f"'{candidate_column}' exists in {', '.join(owning_query_ids)}")
        owning_text = (
            " ".join(owning_hints)
            if owning_hints
            else "None of these columns exist in any query result -- run the query you actually need."
        )
        errors.append(
            f"{location_hint}getCol found none of {candidate_columns} in the columns passed here, "
            f"so it returned -1 and this block renders blank/undefined/NaN. {owning_text}. "
            f"Bind the correct query id here. Source: {source_line}"
        )

    if len(errors) > _MAX_REPORTED_COLUMN_MISSES:
        hidden_count = len(errors) - _MAX_REPORTED_COLUMN_MISSES
        errors = errors[:_MAX_REPORTED_COLUMN_MISSES]
        errors.append(f"... and {hidden_count} more getCol misses with the same root cause.")
    return errors
