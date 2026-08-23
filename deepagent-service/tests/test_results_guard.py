"""app/engine/results_guard.py 的 `__ERD_RESULTS__` 契約護欄測試——涵蓋五條規則,以及動態存取、
賦值樁、移除字面引用、字面資料陣列等違規形狀的迴歸 fixture。"""

from app.engine.results_guard import validate_results_contract


def _has_message_containing(errors: list[str], text: str) -> bool:
    return any(text in error for error in errors)


# -- R1: 字面存取 only ---------------------------------------------------------------------


def test_valid_double_quoted_literal_access_passes() -> None:
    html = '<script>const t = window.__ERD_RESULTS__["q1"];</script>'
    errors = validate_results_contract(html, {"q1"})
    assert errors == []


def test_valid_single_quoted_literal_access_passes() -> None:
    html = "<script>const t = window.__ERD_RESULTS__['q1'];</script>"
    errors = validate_results_contract(html, {"q1"})
    assert errors == []


def test_dynamic_variable_index_fails_r1() -> None:
    html = "<script>const t = window.__ERD_RESULTS__[tblId];</script>"
    errors = validate_results_contract(html, {"q1"})
    assert _has_message_containing(errors, "only")
    assert _has_message_containing(errors, "literal")


def test_template_literal_index_fails_r1() -> None:
    html = "<script>const t = window.__ERD_RESULTS__[`${id}`];</script>"
    errors = validate_results_contract(html, {"q1"})
    assert len(errors) >= 1
    assert _has_message_containing(errors, "literal")


def test_assignment_fails_r1() -> None:
    html = "<script>window.__ERD_RESULTS__ = {q1: {rows: []}};</script>"
    errors = validate_results_contract(html, {"q1"})
    assert _has_message_containing(errors, "literal")


def test_whole_object_aliasing_fails_r1() -> None:
    html = "<script>const results = window.__ERD_RESULTS__; use(results);</script>"
    errors = validate_results_contract(html, {"q1"})
    assert _has_message_containing(errors, "literal")


def test_object_keys_over_whole_object_fails_r1() -> None:
    html = "<script>Object.keys(window.__ERD_RESULTS__).forEach(k => use(k));</script>"
    errors = validate_results_contract(html, {"q1"})
    assert _has_message_containing(errors, "literal")


def test_whitespace_between_marker_and_bracket_fails_r1() -> None:
    """R1 特別聲明空白後接任何東西都算違規——即使後面接的是原本合法的引號索引。"""
    html = '<script>const t = window.__ERD_RESULTS__ ["q1"];</script>'
    errors = validate_results_contract(html, {"q1"})
    assert _has_message_containing(errors, "literal")


def test_error_message_states_allowed_form_and_why() -> None:
    html = "<script>const t = window.__ERD_RESULTS__[tblId];</script>"
    errors = validate_results_contract(html, {"q1"})
    combined = " ".join(errors)
    assert '__ERD_RESULTS__["qN"]' in combined
    assert "whitelist" in combined


# -- R2: 引用必須存在 -----------------------------------------------------------------------


def test_missing_query_id_fails_r2_and_lists_available() -> None:
    html = '<script>const t = window.__ERD_RESULTS__["q9"];</script>'
    errors = validate_results_contract(html, {"q1", "q2"})
    assert _has_message_containing(errors, "q9")
    combined = " ".join(errors)
    assert "q1" in combined
    assert "q2" in combined


def test_missing_query_id_availables_summary_sorted_and_capped_at_20() -> None:
    available = {f"q{index}" for index in range(1, 26)}
    html = '<script>const t = window.__ERD_RESULTS__["q999"];</script>'
    errors = validate_results_contract(html, available)
    combined = " ".join(errors)
    assert "q1, q10, q11" in combined  # 字串排序,不是數值排序
    assert "more" in combined


def test_referenced_id_present_in_available_does_not_trigger_r2() -> None:
    html = '<script>const t = window.__ERD_RESULTS__["q1"];</script>'
    errors = validate_results_contract(html, {"q1"})
    assert not _has_message_containing(errors, "not found")


# -- R3: 至少一個引用 -----------------------------------------------------------------------


def test_zero_references_fails_r3() -> None:
    html = "<html><body><p>no data binding here</p></body></html>"
    errors = validate_results_contract(html, {"q1"})
    assert _has_message_containing(errors, "none found")


def test_at_least_one_valid_reference_passes_r3() -> None:
    html = '<script>const t = window.__ERD_RESULTS__["q1"];</script>'
    errors = validate_results_contract(html, {"q1"})
    assert not _has_message_containing(errors, "none found")


# -- multiple violations reported together -----------------------------------------------


def test_multiple_violations_all_reported() -> None:
    html = (
        "<script>"
        "const a = window.__ERD_RESULTS__[tblId];"
        'const b = window.__ERD_RESULTS__["q9"];'
        "</script>"
    )
    errors = validate_results_contract(html, {"q1"})
    assert _has_message_containing(errors, "literal")  # dynamic access on `a`
    assert _has_message_containing(errors, "q9")  # missing id on `b`
    assert len(errors) >= 2


# -- never-raise ---------------------------------------------------------------------------


def test_none_html_never_raises_and_fails_closed() -> None:
    errors = validate_results_contract(None, {"q1"})  # type: ignore[arg-type]
    assert errors != []


def test_non_string_html_never_raises_and_fails_closed() -> None:
    errors = validate_results_contract(12345, {"q1"})  # type: ignore[arg-type]
    assert errors != []


def test_empty_html_never_raises() -> None:
    errors = validate_results_contract("", {"q1"})
    assert errors != []


# -- regression fixtures: violation shapes the literal-scan whitelist must reject ----------


def test_incident_dynamic_sort_access_fails_r1() -> None:
    """regression: 動態索引存取 `__ERD_RESULTS__[tblId]` 被 R1 拒絕——id 未被字面寫出,永遠不會被注入。"""
    html = (
        "<script>"
        "let state = {};"
        "state.rows = window.__ERD_RESULTS__[tblId].rows;"
        "state.rows.sort((a, b) => a.value - b.value);"
        "</script>"
    )
    errors = validate_results_contract(html, {"q1"})
    assert _has_message_containing(errors, "literal")


def test_incident_head_stub_assignment_fails_r1() -> None:
    """regression: stub 賦值被 R1 拒絕——`window.__ERD_RESULTS__ = {...}` 是整個物件重新賦值,
    不是字面 index 存取,會覆蓋系統注入的內容。"""
    html = "<head><script>window.__ERD_RESULTS__ = {q1: {rows: []}};</script></head>"
    errors = validate_results_contract(html, {"q1"})
    assert _has_message_containing(errors, "literal")


def test_incident_runtime_discovery_pattern_fails_r3() -> None:
    """regression: 零字面引用被 R3 拒絕——僅用 runtime-discovery(如 findResult 輔助函式)存取,
    掃描規則抓不到任何 qN,注入內容會變空。"""
    html = (
        "<script>"
        "function findResult(name) {"
        "  return Object.values(window.__ERD_RESULTS__).find(r => r.intent === name);"
        "}"
        "const table = findResult('各系統工單數');"
        "</script>"
    )
    errors = validate_results_contract(html, {"q1"})
    assert _has_message_containing(errors, "none found")


# -- R4: 圖表 option 不得含字面數字資料陣列 ---------------------------------------------------


def test_validate_results_contract_literalNumericDataArray_flagged() -> None:
    html = (
        '<script>const table = window.__ERD_RESULTS__["q1"];'
        "const option = { series: [{ type: 'bar', data: [12, 45, 78] }] };</script>"
    )
    errors = validate_results_contract(html, {"q1"})
    assert _has_message_containing(errors, "literal numeric array")
    assert _has_message_containing(errors, "data: [12")


def test_validate_results_contract_markLineObjectArray_notFlagged() -> None:
    html = (
        '<script>const table = window.__ERD_RESULTS__["q1"];'
        "const option = { markLine: { data: [{yAxis: 106}] } };</script>"
    )
    errors = validate_results_contract(html, {"q1"})
    assert not _has_message_containing(errors, "literal numeric array")


def test_validate_results_contract_mappedDataExpression_notFlagged() -> None:
    html = (
        '<script>const q4 = window.__ERD_RESULTS__["q1"];'
        "const option = { series: [{ data: q4.rows.map(r => r.value) }] };</script>"
    )
    errors = validate_results_contract(html, {"q1"})
    assert not _has_message_containing(errors, "literal numeric array")


def test_validate_results_contract_twoItemArray_notFlagged() -> None:
    """2 項容忍雜訊(如 [min, max] 一類的短陣列)——判定門檻是 >=3 項,見 brief。"""
    html = (
        '<script>const table = window.__ERD_RESULTS__["q1"];'
        "const option = { series: [{ data: [1, 2] }] };</script>"
    )
    errors = validate_results_contract(html, {"q1"})
    assert not _has_message_containing(errors, "literal numeric array")


def test_validate_results_contract_quotedDataKey_flagged() -> None:
    """JSON 形(`"data": [...]`)也要抓——模型有時把 option 寫成 JSON 字面量再 parse。"""
    html = (
        '<script>const table = window.__ERD_RESULTS__["q1"];'
        'const option = { "series": [{ "data": [0.95,0.87,1.2,1.05] }] };</script>'
    )
    errors = validate_results_contract(html, {"q1"})
    assert _has_message_containing(errors, "literal numeric array")


def test_validate_results_contract_nestedNumericPairArray_flagged() -> None:
    """散點/氣泡圖常見的座標烤死形態——`[[x,y],[x,y],...]`,外層 >=2 組即抓。"""
    html = (
        '<script>const table = window.__ERD_RESULTS__["q1"];'
        "const option = { series: [{ type: 'scatter', "
        "data: [[1,2],[3,4],[5,6]] }] };</script>"
    )
    errors = validate_results_contract(html, {"q1"})
    assert _has_message_containing(errors, "literal numeric array")


def test_validate_results_contract_nestedSinglePair_notFlagged() -> None:
    """只有 1 組 pair 容忍(門檻是外層 >=2 組),避免對稀疏資料點誤判。"""
    html = (
        '<script>const table = window.__ERD_RESULTS__["q1"];'
        "const option = { series: [{ data: [[1,2]] }] };</script>"
    )
    errors = validate_results_contract(html, {"q1"})
    assert not _has_message_containing(errors, "literal numeric array")


def test_validate_results_contract_objectArrayWithNestedNumbers_notFlagged() -> None:
    """物件陣列(即使物件內部藏著數字陣列)不算字面數字陣列——`[{value:[1,2]}]` 這種形狀。"""
    html = (
        '<script>const table = window.__ERD_RESULTS__["q1"];'
        "const option = { series: [{ data: [{value: [1, 2]}, "
        "{value: [3, 4]}, {value: [5, 6]}] }] };</script>"
    )
    errors = validate_results_contract(html, {"q1"})
    assert not _has_message_containing(errors, "literal numeric array")


def test_validate_results_contract_trailingCommaArray_flagged() -> None:
    """flat 陣列容忍 trailing comma(模型常見的格式化風格)。"""
    html = (
        '<script>const table = window.__ERD_RESULTS__["q1"];'
        "const option = { series: [{ data: [12, 45, 78,] }] };</script>"
    )
    errors = validate_results_contract(html, {"q1"})
    assert _has_message_containing(errors, "literal numeric array")
