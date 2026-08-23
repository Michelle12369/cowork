from app.engine.narrative_bind import RESOLVER_SCRIPT_ID, inject_bind_resolver
from app.engine.results import strip_injected_blocks

_HTML_WITH_BOUND_SPAN = (
    '<html><head></head><body><span data-bind="q1.worst_tool"></span></body></html>'
)


def test_inject_bind_resolver_fills_span_from_results() -> None:
    # 不引入瀏覽器依賴——以腳本內容斷言釘住 resolver 的關鍵行為片段:querySelectorAll
    # 選取 [data-bind]、"." 切 path、找不到值時 fallback 到「—」。
    injected = inject_bind_resolver(_HTML_WITH_BOUND_SPAN)
    assert f'<script id="{RESOLVER_SCRIPT_ID}">' in injected
    assert 'document.querySelectorAll("[data-bind]")' in injected
    assert 'path.split(".")' in injected
    assert '"—"' in injected
    # resolver 必須插在 </body> 前,晚於原本的 data-bind 元素,而非文件前段。
    assert injected.index(f'id="{RESOLVER_SCRIPT_ID}"') > injected.index(
        'data-bind="q1.worst_tool"'
    )


def test_inject_bind_resolver_reads_object_rows_by_column_name() -> None:
    # __ERD_RESULTS__[qid].rows 是以欄名為 key 的物件列(見 results.record_query),不是
    # 陣列列——resolver 必須直接用欄名索引,不能假設有 columns/columnIndex 這條路。
    injected = inject_bind_resolver(_HTML_WITH_BOUND_SPAN)
    assert "parts[1] in row" in injected
    assert "row[parts[1]]" in injected
    assert "columnIndex" not in injected


def test_inject_bind_resolver_idempotent_second_call_no_duplicate() -> None:
    once = inject_bind_resolver(_HTML_WITH_BOUND_SPAN)
    twice = inject_bind_resolver(once)
    assert twice == once
    assert once.count(f'id="{RESOLVER_SCRIPT_ID}"') == 1


def test_inject_bind_resolver_appended_when_no_body_close() -> None:
    html = "<div>content</div>"
    injected = inject_bind_resolver(html)
    assert injected.startswith(html)
    assert f'id="{RESOLVER_SCRIPT_ID}"' in injected


def test_inject_bind_resolver_stripped_by_strip_injected_blocks() -> None:
    # 注入 -> strip -> 逐字回到未注入的原始 html,與 inject_results 共用同一套剝除機制。
    injected = inject_bind_resolver(_HTML_WITH_BOUND_SPAN)
    stripped = strip_injected_blocks(injected)
    assert stripped == _HTML_WITH_BOUND_SPAN
    assert RESOLVER_SCRIPT_ID not in stripped


def test_inject_bind_resolver_strip_roundtrip_is_idempotent() -> None:
    injected = inject_bind_resolver(_HTML_WITH_BOUND_SPAN)
    once = strip_injected_blocks(injected)
    assert strip_injected_blocks(once) == once
