"""`__ERD_RESULTS__` contract guard——deterministic check that the model only ever reads query
results via a literal `__ERD_RESULTS__["qN"]` / `__ERD_RESULTS__['qN']` index。注入是字面掃描白名單,
非字面存取的 id 永遠不會被注入,此模組即為此形狀的確定性 gate。R4 額外擋圖表 option 把資料烤成
字面數字陣列;R5 額外驗 data-bind/data-bind-row 綁定路徑(qN 存在、欄位存在、row 是整數)。

engine 層——stdlib only(ruff TID251 會擋 LLM 框架 import)。
"""

import re

_MARKER = "__ERD_RESULTS__"
_MARKER_PATTERN = re.compile(re.escape(_MARKER))
# 字面存取的唯一合法形狀:緊接在 marker 後面、無任何空白、雙引號或單引號包住的 id、右方括號
# 收尾。(?P=quote) 反向參照確保左右引號成對(不接受 ["q1']這種混搭)。
_LITERAL_ACCESS_PATTERN = re.compile(
    re.escape(_MARKER) + r"""\[(?P<quote>["'])(?P<query_id>\w+)(?P=quote)\]"""
)

_AVAILABLE_IDS_DISPLAY_LIMIT = 20
_CONTEXT_SNIPPET_RADIUS = 24

# R4:圖表 option 把資料烤成字面數字陣列(裸鍵或 JSON 引號鍵皆抓),門檻 >=3 項——2 項(如
# [min, max])容忍雜訊放行。只抓純數字;字串陣列(類別標籤,如 ['A','B','C'])v1 先不抓,
# 誤報面大於漏抓的風險,已知範圍外。
_LITERAL_NUMERIC_DATA_ARRAY_PATTERN = re.compile(
    r"""["']?\bdata\b["']?\s*:\s*\[\s*-?\d[\d.eE+-]*\s*(?:,\s*-?\d[\d.eE+-]*\s*){2,}\]"""
)

# R5:data-bind/data-bind-row 綁定路徑——與 PR #62(data-bind runtime,未 merge)的屬性格式
# 對齊,但本模組獨立定義、純 regex 掃描,不 import 該 PR 的任何東西。
_DATA_BIND_PATTERN = re.compile(
    r"""data-bind\s*=\s*(?P<quote>["'])(?P<query_id>\w+)\.(?P<column>\w+)(?P=quote)"""
)
_DATA_BIND_ROW_PATTERN = re.compile(
    r"""data-bind-row\s*=\s*(?P<quote>["'])(?P<query_id>\w+):(?P<row_index>[^"']*)(?P=quote)"""
)
_INTEGER_ROW_INDEX_PATTERN = re.compile(r"^\d+$")


def _context_snippet(html: str, position: int) -> str:
    """violation 位置附近的一小段原文(換行攤平成空白),幫模型定位是哪一處寫錯。"""
    start = max(0, position - _CONTEXT_SNIPPET_RADIUS)
    end = min(len(html), position + len(_MARKER) + _CONTEXT_SNIPPET_RADIUS)
    snippet = html[start:end].replace("\n", " ").replace("\r", " ")
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(html) else ""
    return f"{prefix}{snippet}{suffix}"


def _summarize_available_ids(available_query_ids: set[str]) -> str:
    sorted_ids = sorted(available_query_ids)
    if not sorted_ids:
        return "(none -- no run_sql results recorded yet)"
    shown = sorted_ids[:_AVAILABLE_IDS_DISPLAY_LIMIT]
    summary = ", ".join(shown)
    remaining = len(sorted_ids) - len(shown)
    if remaining > 0:
        summary += f", ... ({remaining} more)"
    return summary


def _r1_violation_message(html: str, position: int) -> str:
    return (
        f'Invalid __ERD_RESULTS__ access near "{_context_snippet(html, position)}": the only '
        "allowed form is a literal __ERD_RESULTS__[\"qN\"] or __ERD_RESULTS__['qN'] index "
        "(no whitespace, no variable, no template literal, no assignment, no aliasing the whole "
        "object). Injection is a literal-scan whitelist -- only ids written exactly this way in "
        "the HTML get injected into window.__ERD_RESULTS__; any other access pattern (dynamic "
        "index, `window.__ERD_RESULTS__ = ...` assignment, `const r = window.__ERD_RESULTS__;` "
        "aliasing, Object.keys(...), etc.) reads undefined at runtime and crashes. Rewrite this "
        'access as a literal __ERD_RESULTS__["qN"] index using the actual query id.'
    )


def _r2_missing_ids_message(missing_ids: set[str], available_query_ids: set[str]) -> str:
    missing_list = ", ".join(sorted(missing_ids))
    available_summary = _summarize_available_ids(available_query_ids)
    return (
        f"Referenced query id(s) not found in available results: {missing_list}. Available "
        f"query ids (sorted): {available_summary}. Only reference an id that came from an actual "
        "run_sql call in this session -- check the wiring manifest and fix the binding instead of "
        "guessing a q-number from memory."
    )


_R3_MESSAGE = (
    'Dashboard must read data via literal __ERD_RESULTS__["qN"] references; none found. Every '
    'chart\'s data must come from window.__ERD_RESULTS__["<tableId>"] for a query id produced by '
    "run_sql -- add at least one literal reference instead of discovering results at runtime."
)


def _r4_violation_message(html: str, position: int) -> str:
    return (
        f'Chart data is baked as a literal numeric array near "{_context_snippet(html, position)}". '
        "Chart data MUST be mapped from query results at render time -- e.g. "
        '__ERD_RESULTS__["qN"].rows.map(r => r.col) -- never typed as literal numbers. '
        "Literal arrays silently freeze stale/wrong values and break data replay."
    )


def _r5_unknown_query_id_message(query_id: str, available_query_ids: set[str]) -> str:
    available_summary = _summarize_available_ids(available_query_ids)
    return (
        f'data-bind references unknown query id "{query_id}". Available query ids (sorted): '
        f"{available_summary}. Only bind to an id that came from an actual run_sql call in this "
        "session -- check the wiring manifest and fix the binding instead of guessing a "
        "q-number from memory."
    )


def _r5_unknown_column_message(
    query_id: str, column: str, available_columns_for_id: list[str]
) -> str:
    columns_summary = ", ".join(available_columns_for_id) if available_columns_for_id else "(none)"
    return (
        f'data-bind references unknown column "{column}" on query id "{query_id}". Actual '
        f"columns for {query_id}: {columns_summary}. Fix the binding to use one of these column "
        "names."
    )


def _r5_non_integer_row_index_message(query_id: str, row_index: str) -> str:
    return (
        f'data-bind-row on query id "{query_id}" has a non-integer row index "{row_index}". The '
        f'row index MUST be a decimal integer, e.g. data-bind-row="{query_id}:0".'
    )


def validate_results_contract(
    html: str,
    available_query_ids: set[str],
    available_columns: dict[str, list[str]] | None = None,
) -> list[str]:
    """驗證 raw model-authored dashboard.html 對 `__ERD_RESULTS__` 的存取契約。回傳錯誤訊息
    list,空 list 代表通過。Never-raise:任何非預期輸入(None、非 str)一律 fail-closed(回傳
    一則錯誤),不讓 guard 本身的例外變成「靜默放行」。

    `available_columns`(qN → 欄名清單)為 None 時,R5 只驗 qN 存在,欄位驗證整段跳過(呼叫端
    沒給欄位資訊就降級,不因此多擋)。R3「至少一處引用」把合法/非法的 data-bind 綁定都算作
    wiring 的一種——只要頁面出現任何 data-bind 屬性就滿足 R3,綁定本身是否有效由 R5 各自報錯。

    呼叫端請先用 `strip_injected_blocks` 剝掉本模組注入過的 `<script id="erd-results-data">`
    區塊再傳進來(那個區塊本身就含 `window.__ERD_RESULTS__ = {...}` 賦值,會誤觸 R1)。
    """
    try:
        if not isinstance(html, str):
            return ["results contract validation received non-string HTML; treating as invalid."]

        errors: list[str] = []
        referenced_ids: set[str] = set()
        bind_referenced_ids: set[str] = set()

        for marker_match in _MARKER_PATTERN.finditer(html):
            position = marker_match.start()
            literal_match = _LITERAL_ACCESS_PATTERN.match(html, position)
            if literal_match:
                referenced_ids.add(literal_match.group("query_id"))
            else:
                errors.append(_r1_violation_message(html, position))

        missing_ids = referenced_ids - set(available_query_ids)
        if missing_ids:
            errors.append(_r2_missing_ids_message(missing_ids, set(available_query_ids)))

        for array_match in _LITERAL_NUMERIC_DATA_ARRAY_PATTERN.finditer(html):
            errors.append(_r4_violation_message(html, array_match.start()))

        for bind_match in _DATA_BIND_PATTERN.finditer(html):
            query_id = bind_match.group("query_id")
            bind_referenced_ids.add(query_id)
            if query_id not in available_query_ids:
                errors.append(_r5_unknown_query_id_message(query_id, set(available_query_ids)))
            elif available_columns is not None:
                columns_for_id = available_columns.get(query_id)
                if columns_for_id is not None and bind_match.group("column") not in columns_for_id:
                    errors.append(
                        _r5_unknown_column_message(
                            query_id, bind_match.group("column"), columns_for_id
                        )
                    )

        for bind_row_match in _DATA_BIND_ROW_PATTERN.finditer(html):
            query_id = bind_row_match.group("query_id")
            bind_referenced_ids.add(query_id)
            if query_id not in available_query_ids:
                errors.append(_r5_unknown_query_id_message(query_id, set(available_query_ids)))
            row_index = bind_row_match.group("row_index")
            if not _INTEGER_ROW_INDEX_PATTERN.match(row_index):
                errors.append(_r5_non_integer_row_index_message(query_id, row_index))

        if not referenced_ids and not bind_referenced_ids:
            errors.append(_R3_MESSAGE)

        return errors
    except Exception:  # noqa: BLE001 -- never-raise contract; fail closed, not silently open.
        return [
            (
                "results contract validation encountered an internal error and could not "
                "complete; treating the HTML as failing the contract to be safe."
            )
        ]
