import datetime
import decimal

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
from app.engine.workspace import LocalWorkspaceStore


def _workspace(tmp_path):
    return LocalWorkspaceStore(tmp_path).prepare("user-1", "sess-1")


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
    assert loaded["q1"]["rows"] == [["CRM", 42]]
    assert (workspace.queries_dir / "q1.sql").read_text(encoding="utf-8") == "SELECT 1"


def test_record_query_caps_rows_at_store_max(tmp_path) -> None:
    workspace = _workspace(tmp_path)
    record_query(
        workspace, "q1", "SELECT 1", "x", ["n"], [[i] for i in range(6000)], truncated=False
    )
    loaded = load_all_results(workspace)
    assert len(loaded["q1"]["rows"]) == 5000
    assert loaded["q1"]["truncated"] is True


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
    assert loaded["q1"]["rows"] == [[1.5, "2026-07-29", "2026-07-29T12:30:00+00:00"]]


def test_referenced_query_ids_finds_both_quote_styles() -> None:
    html = "a __ERD_RESULTS__[\"q1\"] b __ERD_RESULTS__['q2'] c"
    assert referenced_query_ids(html) == {"q1", "q2"}


def test_build_results_script_escapes_closing_tag() -> None:
    script = build_results_script(
        {"q1": {"columns": ["x"], "rows": [["</script>"]], "truncated": False}}
    )
    assert "</script>" not in script.removeprefix('<script id="erd-results-data">').removesuffix(
        "</script>"
    )


def test_build_results_script_carries_id_marker() -> None:
    script = build_results_script({})
    assert script.startswith('<script id="erd-results-data">')


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


def test_strip_injected_blocks_removes_results_and_theme_scripts() -> None:
    html = (
        "<html><head>"
        '<script id="erd-results-data">window.__ERD_RESULTS__ = {"q1": {}};</script>'
        '<script id="erd-theme">(function(){registerErdTheme();})();</script>'
        "</head><body><div>content</div></body></html>"
    )
    stripped = strip_injected_blocks(html)
    assert "erd-results-data" not in stripped
    assert "erd-theme" not in stripped
    assert "<div>content</div>" in stripped


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
