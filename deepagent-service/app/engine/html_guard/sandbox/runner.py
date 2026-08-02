"""Level 2 sandbox 執行迴圈——把每段 inline script 真的 eval 一次，抓 parse-only 檢查不到
的 runtime 錯誤；quickjs 不可用或全域 deadline 超時時優雅降級，不擋主流程。"""

import contextlib
import logging
import time

from app.engine.html_guard import js_runtime
from app.engine.results import referenced_query_ids

from . import context as sandbox_context
from .console import (
    _check_column_not_found_warnings,
    _check_swallowed_chart_errors,
    _read_collected_console_errors,
    _read_collected_console_warnings,
)
from .errors import (
    _JS_IDENTIFIER_PATTERN,
    _MAX_REFERENCE_ERROR_RETRIES_PER_BLOCK,
    _REFERENCE_ERROR_VAR_PATTERN,
    _format_execution_error,
    _resolve_error_frames,
)

logger = logging.getLogger(__name__)


def execute_scripts_smoke(
    script_blocks_with_lines: list[tuple[str, int]],
    available_query_ids: set[str],
    results: dict[str, dict] | None = None,
    known_element_ids: frozenset[str] = frozenset(),
    html: str = "",
) -> list[str]:
    """Level 2:在 quickjs sandbox 內真的執行(不只 parse)每段 inline script,抓 Level 1
    看不到的 runtime 錯誤;quickjs 不可用時記 warning、跳過。`results` 提供時灌真實欄名/
    資料,讓按欄名查找的閘門真的打開;`known_element_ids` 讓引用不存在的 id 如實回傳 `null`。
    sandbox 只灌 production 實際會注入的子集(`referenced_query_ids(html)`),不是完整的
    `available_query_ids`——否則 regex 抓不到、production 也沒注入資料的寫法(例如 dot
    access 或動態 key)會在 sandbox 裡意外拿到資料、guard 誤判過關。

    單一 block 拋 ReferenceError 時記錄該錯誤、把變數 stub 成 absorb-all proxy、重建全新
    context 並靜默重放之前所有 block,再重跑這個 block,直到不再拋新的 ReferenceError 或達
    `_MAX_REFERENCE_ERROR_RETRIES_PER_BLOCK` 次;非 ReferenceError 的例外記錄但不重試。

    所有 block 跑完後,額外把被 chart try/catch 擋下的執行期錯誤(`console.error` 收集器)
    與 getCol 找不到欄位的訊號(`console.warn` 收集器)轉成 guard error。
    """
    if not js_runtime.QUICKJS_AVAILABLE:
        logger.warning("html_guard: quickjs 未安裝，跳過 JS 執行檢查")
        return []

    errors: list[str] = []
    stub_variable_names: set[str] = set()
    html_line_count = len(html.splitlines())
    # 只灌 production 實際會注入的子集(見上方函式說明),不是完整的 available_query_ids。
    seeded_query_ids = referenced_query_ids(html)
    deadline_start_time = time.monotonic()
    deadline_exceeded = False

    try:
        context = sandbox_context._build_sandbox_context(
            seeded_query_ids, stub_variable_names, results, known_element_ids
        )
    except Exception as sandbox_init_error:  # noqa: BLE001 -- 驗證器掛掉不擋主流程
        logger.warning("html_guard: sandbox 初始化失敗，跳過 JS 執行檢查: %s", sandbox_init_error)
        return []

    for script_index, (script_content, html_start_line) in enumerate(script_blocks_with_lines):
        if deadline_exceeded:
            break
        retry_count = 0
        while True:
            if (
                time.monotonic() - deadline_start_time
                > sandbox_context._SANDBOX_GLOBAL_DEADLINE_SECONDS
            ):
                logger.warning(
                    "html_guard: Level 2 sandbox 執行超過全域 deadline(%.0fs)，提前結束、"
                    "回傳目前已收集到的結果（驗證器降級不擋主流程）",
                    sandbox_context._SANDBOX_GLOBAL_DEADLINE_SECONDS,
                )
                deadline_exceeded = True
                break
            try:
                context.eval(f"__erd_block_start_line__ = {html_start_line};")
                context.eval(script_content)
                break
            except js_runtime.quickjs.JSException as runtime_error:
                message = str(runtime_error)
                first_line = message.splitlines()[0] if message else message

                if "interrupted" in first_line.lower():
                    errors.append(
                        f"script#{script_index} execution timed out (possible infinite loop)"
                    )
                    break

                frames = _resolve_error_frames(message, html_start_line, html_line_count)
                errors.append(_format_execution_error(frames, script_index, first_line, html))

                variable_match = _REFERENCE_ERROR_VAR_PATTERN.search(first_line)
                undeclared_variable = variable_match.group(1) if variable_match else None
                can_retry_with_new_stub = (
                    undeclared_variable is not None
                    and undeclared_variable not in stub_variable_names
                    and _JS_IDENTIFIER_PATTERN.fullmatch(undeclared_variable) is not None
                    and retry_count < _MAX_REFERENCE_ERROR_RETRIES_PER_BLOCK
                )
                if not can_retry_with_new_stub:
                    break

                # 重建 context + 重放前面所有 block 是這個迴圈最貴的一步(見模組上方對
                # `execute_scripts_smoke` 的說明:總耗時沒有上限的病態情況就是這裡)——開始
                # 之前再檢查一次 deadline,不要讓一次重放本身就把 wall clock 燒穿。
                if (
                    time.monotonic() - deadline_start_time
                    > sandbox_context._SANDBOX_GLOBAL_DEADLINE_SECONDS
                ):
                    logger.warning(
                        "html_guard: Level 2 sandbox 重試迴圈超過全域 deadline(%.0fs)，"
                        "放棄剩餘重試、回傳目前已收集到的結果（驗證器降級不擋主流程）",
                        sandbox_context._SANDBOX_GLOBAL_DEADLINE_SECONDS,
                    )
                    deadline_exceeded = True
                    break

                stub_variable_names.add(undeclared_variable)
                retry_count += 1
                try:
                    context = sandbox_context._build_sandbox_context(
                        seeded_query_ids, stub_variable_names, results, known_element_ids
                    )
                    for earlier_content, earlier_start_line in script_blocks_with_lines[
                        :script_index
                    ]:
                        # 只求重建到「當前 block 前」該有的宣告狀態——這些 block 自己的
                        # 錯誤已在第一輪掃描時記錄過，重放時如實重現也不重複記錄。base
                        # line 也要重放，否則重放期間觸發的 warn 會帶著錯誤的 base。
                        with contextlib.suppress(Exception):
                            context.eval(f"__erd_block_start_line__ = {earlier_start_line};")
                            context.eval(earlier_content)
                except Exception as rebuild_error:  # noqa: BLE001 -- 驗證器掛掉不擋主流程
                    logger.warning(
                        "html_guard: quickjs 重建 sandbox context 失敗，跳過後續執行檢查: %s",
                        rebuild_error,
                    )
                    return errors
                # continue -- 用重建後的 context 重跑同一個 block
            except Exception as unexpected_error:  # noqa: BLE001 -- 驗證器掛掉不擋主流程
                logger.warning(
                    "html_guard: quickjs 執行檢查 script#%d 時發生非預期例外，跳過該段檢查: %s",
                    script_index,
                    unexpected_error,
                )
                break

    errors.extend(_check_swallowed_chart_errors(_read_collected_console_errors(context)))
    # 只有整份 results 都是真實欄名時才判定 getCol miss——退回泛用假欄名(__c0/__c1)時
    # 每個 getCol 都會 miss，轉成 error 會全是誤報。
    if results is not None and available_query_ids <= set(results):
        errors.extend(
            _check_column_not_found_warnings(
                _read_collected_console_warnings(context), results, html.splitlines()
            )
        )
    return errors
