import datetime
import decimal
import json

from app.engine.results import (
    build_results_script,
    format_wiring_manifest,
    inject_results,
    load_all_results,
    next_query_id,
    record_query,
    referenced_query_ids,
    strip_injected_blocks,
)
from app.engine.workspace import prepare_local_layout


def _workspace(tmp_path):
    return prepare_local_layout(tmp_path, "user-1", "sess-1")


def test_next_query_id_increments_across_existing_files(tmp_path) -> None:
    workspace = _workspace(tmp_path)
    assert next_query_id(workspace) == "q1"
    record_query(workspace, "q1", "SELECT 1", "測試", ["n"], [[1]], truncated=False)
    assert next_query_id(workspace) == "q2"


def test_record_and_load_roundtrip(tmp_path) -> None:
    workspace = _workspace(tmp_path)
    record_query(
        workspace,
        "q1",
        "SELECT 1",
        "各系統工單數",
        ["system", "tickets"],
        [["CRM", 42]],
        truncated=False,
    )
    loaded = load_all_results(workspace)
    assert loaded["q1"]["columns"] == ["system", "tickets"]
    # 落檔的 rows 是以欄名為 key 的物件列(dict(zip(columns, row))),不是陣列列。
    assert loaded["q1"]["rows"] == [{"system": "CRM", "tickets": 42}]
    assert (workspace.queries_dir / "q1.sql").read_text(encoding="utf-8") == "SELECT 1"


def test_record_query_caps_rows_at_store_max(tmp_path) -> None:
    workspace = _workspace(tmp_path)
    record_query(
        workspace, "q1", "SELECT 1", "x", ["n"], [[i] for i in range(21000)], truncated=False
    )
    loaded = load_all_results(workspace)
    assert len(loaded["q1"]["rows"]) == 20000
    assert loaded["q1"]["truncated"] is True


def test_record_query_persists_total_row_count_roundtrip(tmp_path) -> None:
    workspace = _workspace(tmp_path)
    record_query(
        workspace,
        "q1",
        "SELECT 1",
        "x",
        ["n"],
        [[1]],
        truncated=True,
        total_row_count=123456,
    )
    loaded = load_all_results(workspace)
    assert loaded["q1"]["total_row_count"] == 123456


def test_record_query_total_row_count_defaults_to_none(tmp_path) -> None:
    workspace = _workspace(tmp_path)
    record_query(workspace, "q1", "SELECT 1", "x", ["n"], [[1]], truncated=False)
    loaded = load_all_results(workspace)
    assert loaded["q1"]["total_row_count"] is None


def test_record_query_normalizes_decimal_date_datetime_cells(tmp_path) -> None:
    workspace = _workspace(tmp_path)
    rows = [
        [
            decimal.Decimal("1.5"),
            datetime.date(2026, 7, 29),
            datetime.datetime(2026, 7, 29, 12, 30, 0, tzinfo=datetime.UTC),
        ]
    ]
    record_query(
        workspace, "q1", "SELECT 1", "x", ["amount", "day", "moment"], rows, truncated=False
    )
    loaded = load_all_results(workspace)
    assert loaded["q1"]["rows"] == [
        {"amount": 1.5, "day": "2026-07-29", "moment": "2026-07-29T12:30:00+00:00"}
    ]


def test_load_all_results_skips_corrupt_json_and_keeps_valid_entries(tmp_path, caplog) -> None:
    """一份 truncated/corrupt 的 results/*.json(併發寫入或 process 被砍到一半的產物)不能讓
    整個 session 之後每一輪都炸——只跳過那一筆，其餘結果照常回傳。"""
    workspace = _workspace(tmp_path)
    record_query(workspace, "q1", "SELECT 1", "good", ["n"], [[1]], truncated=False)
    (workspace.results_dir / "q2.json").write_text(
        '{"columns": ["n"], "rows": [[1]]', encoding="utf-8"
    )

    loaded = load_all_results(workspace)

    assert set(loaded) == {"q1"}
    assert loaded["q1"]["columns"] == ["n"]
    assert loaded["q1"]["rows"] == [{"n": 1}]


def test_referenced_query_ids_finds_both_quote_styles() -> None:
    html = "a __ERD_RESULTS__[\"q1\"] b __ERD_RESULTS__['q2'] c"
    assert referenced_query_ids(html) == {"q1", "q2"}


def test_build_results_script_escapes_closing_tag() -> None:
    script = build_results_script(
        {"q1": {"columns": ["x"], "rows": [{"x": "</script>"}], "truncated": False}}
    )
    assert "</script>" not in script.removeprefix('<script id="erd-results-data">').removesuffix(
        "</script>"
    )


def test_build_results_script_carries_id_marker() -> None:
    script = build_results_script({})
    assert script.startswith('<script id="erd-results-data">')


def _extract_json_literal(script: str) -> str:
    """抽出 JSON literal 本體——`rindex(";</script>")` 在 rows Proxy 包裝碼加進同一個
    script 標籤後不再可靠(最後一個 ";</script>" 落在包裝碼尾端,不是 JSON 賦值句尾),改用
    `raw_decode` 只解析開頭那個 JSON 物件、忽略其後的包裝碼,取得它實際佔用的字元範圍。"""
    prefix = "window.__ERD_RESULTS__ = "
    start = script.index(prefix) + len(prefix)
    _, end = json.JSONDecoder().raw_decode(script, start)
    return script[start:end]


def test_build_results_script_escapes_html_comment_open() -> None:
    """`<!--` 讓 HTML5 tokenizer 進入 script-data escaped state -- 不逃 `<` 本身，之後一個
    沒有斜線的 `<script` 就能存活到 double-escaped state，讓後面的 `</script>` 失效。"""
    script = build_results_script(
        {"q1": {"columns": ["x"], "rows": [{"x": "<!-- x"}], "truncated": False}}
    )
    assert "<!--" not in script


def test_build_results_script_escapes_bare_script_open_tag() -> None:
    script = build_results_script(
        {"q1": {"columns": ["x"], "rows": [{"x": "<script foo"}], "truncated": False}}
    )
    assert "<script foo" not in script


def test_build_results_script_json_roundtrips_original_cell_values() -> None:
    """逃脫手法不能改變資料本身——把注入區塊的 JSON literal 抽出來重新 json.loads，
    必須拿回原始 cell 值(含 `</script>`、`<!--`、`<script` 這些危險字元本身)。"""
    original_cell_values = ["</script>", "<!-- x", "<script foo", "plain"]
    script = build_results_script(
        {
            "q1": {
                "columns": ["x"],
                "rows": [{"x": value} for value in original_cell_values],
                "truncated": False,
            }
        }
    )
    json_literal = _extract_json_literal(script)
    decoded = json.loads(json_literal)
    assert [row["x"] for row in decoded["q1"]["rows"]] == original_cell_values


def test_build_results_script_carries_truncated_and_total_row_count() -> None:
    """`build_results_script` just serializes the stored dict, so a truncated result's
    `truncated`/`total_row_count` must round-trip into the injected payload the browser reads."""
    script = build_results_script(
        {
            "q1": {
                "columns": ["n"],
                "rows": [{"n": 1}],
                "truncated": True,
                "total_row_count": 123456,
            }
        }
    )
    json_literal = _extract_json_literal(script)
    decoded = json.loads(json_literal)
    assert decoded["q1"]["truncated"] is True
    assert decoded["q1"]["total_row_count"] == 123456


def test_build_results_script_includes_proxy_wrapper_for_unknown_column_access() -> None:
    """物件列包一層 Proxy——存取未知欄名(含 index 存取)直接 throw,而不是安靜回傳
    undefined/NaN,讓綁錯欄的錯誤能被 onerror 修復鏈路接住。"""
    script = build_results_script(
        {"q1": {"columns": ["x"], "rows": [{"x": 1}], "truncated": False}}
    )
    assert "new Proxy(row" in script
    assert "row has no column" in script


def test_record_query_duplicate_columns_deduped_no_silent_loss(tmp_path) -> None:
    """SELECT * join 同名欄 → dict(zip) 會靜默丟欄且 Proxy 攔不到——去重加後綴,零遺失。"""
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")
    record_query(
        workspace,
        "q1",
        "SELECT 1",
        "join 測試",
        ["a", "a", "b"],
        [[1, 2, 3]],
        truncated=False,
    )
    stored = json.loads((workspace.results_dir / "q1.json").read_text(encoding="utf-8"))
    assert stored["columns"] == ["a", "a_2", "b"]
    assert stored["rows"] == [{"a": 1, "a_2": 2, "b": 3}]


def test_record_query_dedupe_suffix_collision_stays_unique(tmp_path) -> None:
    """原本就有 `a_2` 欄時,後綴繼續遞增,不得再撞名。"""
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")
    record_query(
        workspace,
        "q1",
        "SELECT 1",
        "後綴碰撞",
        ["a", "a", "a_2"],
        [[1, 2, 3]],
        truncated=False,
    )
    stored = json.loads((workspace.results_dir / "q1.json").read_text(encoding="utf-8"))
    # 原本就存在的 a_2 保留原名,重複的 a 讓位改用 a_3——後綴不偷走原始欄名。
    assert stored["columns"] == ["a", "a_3", "a_2"]
    assert stored["rows"] == [{"a": 1, "a_3": 2, "a_2": 3}]


def test_inject_results_before_head_close() -> None:
    html = "<html><head><title>t</title></head><body></body></html>"
    injected = inject_results(html, {"q1": {"columns": [], "rows": [], "truncated": False}})
    assert injected.index("__ERD_RESULTS__") < injected.index("</head>")


def test_inject_results_after_body_open_when_no_head_close() -> None:
    html = '<body class="x"><div>content</div></body>'
    injected = inject_results(html, {"q1": {"columns": [], "rows": [], "truncated": False}})
    body_open_end = injected.index('<body class="x">') + len('<body class="x">')
    # No </head> present, so the script is inserted right after the <body ...> open tag --
    # everything up to and including <body class="x"> is untouched, and the injected <script>
    # comes immediately after it, before the original body content.
    assert injected.startswith(html[:body_open_end])
    assert injected.index('<script id="erd-results-data">') == body_open_end
    assert injected.index("__ERD_RESULTS__") < injected.index("<div>content</div>")


def test_inject_results_prepended_when_no_head_or_body() -> None:
    html = "<div>content</div>"
    injected = inject_results(html, {"q1": {"columns": [], "rows": [], "truncated": False}})
    # Neither </head> nor <body ...> present, so the script is simply prepended.
    assert injected.index('<script id="erd-results-data">') == 0
    assert injected.endswith(html)


def test_strip_injected_blocks_removes_results_script() -> None:
    html = (
        "<html><head>"
        '<script id="erd-results-data">window.__ERD_RESULTS__ = {"q1": {}};</script>'
        "</head><body><div>content</div>"
        '<script>echarts.init(document.getElementById("c"));</script>'
        "</body></html>"
    )
    stripped = strip_injected_blocks(html)
    assert "erd-results-data" not in stripped
    assert "__ERD_RESULTS__" not in stripped
    assert "<div>content</div>" in stripped
    assert '<script>echarts.init(document.getElementById("c"));</script>' in stripped


def test_strip_injected_blocks_returns_unchanged_when_absent() -> None:
    html = "<html><head></head><body><div>content</div></body></html>"
    assert strip_injected_blocks(html) == html


def test_strip_injected_blocks_is_idempotent() -> None:
    html = (
        "<html><head>"
        '<script id="erd-results-data">window.__ERD_RESULTS__ = {"q1": {}};</script>'
        '<script id="erd-theme">(function(){})();</script>'
        "</head><body></body></html>"
    )
    once = strip_injected_blocks(html)
    assert strip_injected_blocks(once) == once


def test_format_wiring_manifest_lists_intent_and_columns() -> None:
    manifest = format_wiring_manifest(
        {
            "q2": {"intent": "各情感分佈", "columns": ["sentiment", "count"], "rows": []},
            "q1": {
                "intent": "各功能使用次數",
                "columns": ["feature_name", "usage_count"],
                "rows": [],
            },
        }
    )

    assert "q1" in manifest and "各功能使用次數" in manifest and "feature_name" in manifest
    # 依 qid 排序，不是 dict 順序——避免同一輪內順序抖動讓 prompt 前綴每次都不同。
    assert manifest.index("q1") < manifest.index("q2")


def test_format_wiring_manifest_empty_results_is_empty_string() -> None:
    assert format_wiring_manifest({}) == ""
