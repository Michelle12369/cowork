"""quickjs sandbox context 建構——size/time 上限常數、假 `__ERD_RESULTS__`/`__erd_known_element_ids__`
種子資料組裝，以及 `_build_sandbox_context` 本身。"""

import json
import re

from app.engine.html_guard import js_runtime

from .prelude import _SANDBOX_PRELUDE

# quickjs Context 每次 eval() 的 CPU time budget（每段 script 各自重新計時）。
_SANDBOX_TIME_LIMIT_SECONDS = 2.0
_SANDBOX_ERROR_MESSAGE_MAX_LENGTH = 150

# 沒有這道上限時,一個不斷配置陣列的無窮迴圈可以在被 `_SANDBOX_TIME_LIMIT_SECONDS` 攔下前
# 撐爆行程記憶體(實測 4025MB peak RSS、8.39s)——在有記憶體限制的容器裡會 OOM-kill 整個
# process,拖垮所有並發 session。64MB 對真實 dashboard script(灌了截斷後的 seed 資料,見
# `_SANDBOX_SEED_ROW_LIMIT`)綽綽有餘,對失控配置則會快速丟 out-of-memory 例外。
_SANDBOX_MEMORY_LIMIT_BYTES = 64 * 1024 * 1024
# quickjs 預設 256kB;維持預設值即可攔住失控遞迴,這裡明確設定只是讓上限成為看得到的常數。
_SANDBOX_MAX_STACK_SIZE_BYTES = 256 * 1024

# 跨整個 `_execute_scripts_smoke` 呼叫(所有 script block、所有 ReferenceError 重試與 context
# 重建)的全域 wall-clock 上限。單一 block 的 `_SANDBOX_TIME_LIMIT_SECONDS` 各自重新計時,
# 無法擋住「多個 block 各自安全,但 ReferenceError 重試迴圈反覆重建 context、重放前面所有
# block」這種總時間不設限的情況(實測病態輸入跑到 56.93s)。超過此上限時優雅降級——記
# warning、回傳目前已收集到的結果,不 raise——與 html_guard 套件其他規則一致的哲學:
# 驗證器失敗不能擋 dashboard 送出。
_SANDBOX_GLOBAL_DEADLINE_SECONDS = 10.0

# 對每個 available_query_id 灌一份「真實形狀」的假資料：欄位/列都齊全，讓正常存取
# `.columns`/`.rows`/`.truncated` 的程式碼安全跑過，未宣告變數等錯誤依然如實炸出來。
# 這是沒有真實 `results` 時的 fallback（見 `_results_literal_for_sandbox`）。
_FAKE_RESULT_COLUMNS: tuple[str, ...] = ("__c0", "__c1")
_FAKE_RESULT_ROWS: tuple[tuple[object, ...], ...] = (("x", 1),)

# 真實 `results` 灌進 sandbox 時只取前幾列——夠讓欄位存在的閘門打開、`.rows[0]` 這類存取
# 有東西可讀，不需要整份資料拖慢每次 `_build_sandbox_context` 重建。
_SANDBOX_SEED_ROW_LIMIT = 3


def _results_literal_for_sandbox(
    available_query_ids: set[str], results: dict[str, dict] | None
) -> str:
    """建構灌進 sandbox `window.__ERD_RESULTS__` 的假資料 JSON。有真實 `results` 時用真實
    欄名與前幾列真實資料,讓按欄名查找的程式碼閘門真的打開;缺資料的 query_id 退回泛用假資料。
    """
    fake_results: dict[str, dict] = {}
    for query_id in available_query_ids:
        real_result = results.get(query_id) if results else None
        if real_result is not None and "columns" in real_result and "rows" in real_result:
            fake_results[query_id] = {
                "columns": real_result["columns"],
                "rows": real_result["rows"][:_SANDBOX_SEED_ROW_LIMIT],
                "truncated": bool(real_result.get("truncated", False)),
            }
        else:
            fake_results[query_id] = {
                "columns": list(_FAKE_RESULT_COLUMNS),
                "rows": [list(row) for row in _FAKE_RESULT_ROWS],
                "truncated": False,
            }
    return json.dumps(fake_results)


# 掃整份 HTML(markup 與 script 皆含)裡所有 `id="..."` 屬性字面值,餵給 sandbox 的
# `getElementById`/`querySelector('#id')` 做 id 擬真(見 `_SANDBOX_PRELUDE`)。不限定
# 標籤種類,多抓無害。動態拼接的 id 字串不需要 Python 端理解拼接邏輯——只要拼出來的字面值
# 本身在 HTML 某處真的存在,sandbox 執行期 `Set.has(...)` 就會命中。
_ELEMENT_ID_ATTRIBUTE_PATTERN = re.compile(r"""\bid\s*=\s*(["'])([^"']*)\1""")


def _extract_known_element_ids(html: str) -> set[str]:
    return {match.group(2) for match in _ELEMENT_ID_ATTRIBUTE_PATTERN.finditer(html)}


def _build_sandbox_context(
    available_query_ids: set[str],
    stub_variable_names: set[str],
    results: dict[str, dict] | None = None,
    known_element_ids: frozenset[str] = frozenset(),
) -> "js_runtime.quickjs.Context":
    """建一個全新 quickjs Context,灌入 prelude、假 `__ERD_RESULTS__`、已知 element id 與目前
    已收集的 stub 變數(各自指到 absorb-all proxy)。同時設 memory/stack 上限——只靠
    `set_time_limit` 攔不住迴圈在超時前先吃光記憶體。"""
    context = js_runtime.quickjs.Context()
    context.set_time_limit(_SANDBOX_TIME_LIMIT_SECONDS)
    context.set_memory_limit(_SANDBOX_MEMORY_LIMIT_BYTES)
    context.set_max_stack_size(_SANDBOX_MAX_STACK_SIZE_BYTES)
    context.eval(_SANDBOX_PRELUDE)
    context.eval(
        f"window.__ERD_RESULTS__ = {_results_literal_for_sandbox(available_query_ids, results)};"
    )
    context.eval(f"__erd_known_element_ids__ = new Set({json.dumps(sorted(known_element_ids))});")
    for variable_name in stub_variable_names:
        context.eval(f"globalThis.{variable_name} = __erdMakeAbsorb();")
    return context
