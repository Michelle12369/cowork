"""`__ERD_RESULTS__` contract guard——deterministic check that the model only ever reads query
results via a literal `__ERD_RESULTS__["qN"]` / `__ERD_RESULTS__['qN']` index。注入是字面掃描白名單,
非字面存取的 id 永遠不會被注入,此模組即為此形狀的確定性 gate。R4 額外擋圖表 option 把資料烤成
字面數字陣列。

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
# [min, max])容忍雜訊放行,外層/內層都容忍 trailing comma。已知範圍外(刻意不抓,誤報面大於
# 漏抓風險):字串陣列(類別標籤,如 ['A','B','C'])、省略前導 0 的小數(`.9`)、`+` 前綴數字
# (`+5`)、物件與數字混雜的陣列。
_LITERAL_NUMERIC_DATA_ARRAY_PATTERN = re.compile(
    r"""["']?\bdata\b["']?\s*:\s*\[\s*-?\d[\d.eE+-]*\s*(?:,\s*-?\d[\d.eE+-]*\s*){2,},?\s*\]"""
)

# 散點/氣泡圖常見的座標烤死形態:`data: [[1,2],[3,4],[5,6]]`——內層 2-3 個純數字、外層
# >=2 組才抓(單組 `[[1,2]]` 容忍,誤判面大於漏抓風險)。物件陣列(`[{value:[1,2]}]`)因為
# 外層 `[` 後接的是 `{` 不是 `[`,天然不會匹配此 pattern。
_NESTED_NUMERIC_PAIR_TUPLE = r"""\[\s*-?\d[\d.eE+-]*\s*(?:,\s*-?\d[\d.eE+-]*\s*){1,2},?\s*\]"""
_LITERAL_NESTED_NUMERIC_PAIR_ARRAY_PATTERN = re.compile(
    r'["\']?\bdata\b["\']?\s*:\s*\['
    rf"\s*{_NESTED_NUMERIC_PAIR_TUPLE}\s*(?:,\s*{_NESTED_NUMERIC_PAIR_TUPLE}\s*){{1,}}"
    r",?\s*\]"
)


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



def validate_results_contract(html: str, available_query_ids: set[str]) -> list[str]:
    """驗證 raw model-authored dashboard.html 對 `__ERD_RESULTS__` 的存取契約。回傳錯誤訊息
    list,空 list 代表通過。Never-raise:任何非預期輸入(None、非 str)一律 fail-closed(回傳
    一則錯誤),不讓 guard 本身的例外變成「靜默放行」。

    呼叫端請先用 `strip_injected_blocks` 剝掉本模組注入過的 `<script id="erd-results-data">`
    區塊再傳進來(那個區塊本身就含 `window.__ERD_RESULTS__ = {...}` 賦值,會誤觸 R1)。
    """
    try:
        if not isinstance(html, str):
            return ["results contract validation received non-string HTML; treating as invalid."]

        errors: list[str] = []
        referenced_ids: set[str] = set()

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

        for pair_array_match in _LITERAL_NESTED_NUMERIC_PAIR_ARRAY_PATTERN.finditer(html):
            errors.append(_r4_violation_message(html, pair_array_match.start()))

        if not referenced_ids:
            errors.append(_R3_MESSAGE)

        return errors
    except Exception:  # noqa: BLE001 -- never-raise contract; fail closed, not silently open.
        return [
            (
                "results contract validation encountered an internal error and could not "
                "complete; treating the HTML as failing the contract to be safe."
            )
        ]
