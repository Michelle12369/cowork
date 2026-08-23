import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

import app.agent.tools.data as data_module
from app.agent.tools.data import MAX_FETCHES_PER_TURN, build_data_tools
from app.agent.tools.framing import DATA_FRAME_CLOSE, DATA_FRAME_OPEN
from app.agent.tools.recording import ToolResultRecorder
from app.engine.api_fetch import FETCH_ERROR_PREFIX, load_fetch_records, snapshot_fingerprint
from app.engine.connectors import load_connector_registry
from app.engine.duck import Source, open_locked_connection
from app.engine.results import load_all_results
from app.engine.workspace import prepare_local_layout


@pytest.fixture()
def toolset(tmp_path):
    csv_path = tmp_path / "orders.csv"
    csv_path.write_text("system,tickets\nCRM,42\nERP,7\n", encoding="utf-8")
    connection = open_locked_connection([Source("orders", str(csv_path), "csv")])
    workspace = prepare_local_layout(tmp_path / "ws", "user-1", "sess-1")
    recorder = ToolResultRecorder()
    tools = {tool.name: tool for tool in build_data_tools(connection, workspace, recorder)}
    return tools, workspace, recorder


def test_get_schema_is_framed_and_lists_table(toolset) -> None:
    tools, _, _ = toolset
    output = tools["get_schema"].invoke({})
    assert output.startswith(DATA_FRAME_OPEN) and output.endswith(DATA_FRAME_CLOSE)
    assert "orders" in output and "tickets" in output


def test_run_sql_records_result_and_returns_table_id(toolset) -> None:
    tools, workspace, recorder = toolset
    run_id = uuid.uuid4()
    output = tools["run_sql"].invoke(
        {
            "sql": "SELECT system, tickets FROM orders ORDER BY tickets DESC",
            "intent": "各系統工單數",
        },
        config={"run_id": run_id},
    )
    assert output.startswith("tableId: q1\n\n")
    assert "CRM" in output
    stored = load_all_results(workspace)
    # 落檔的 rows 是以欄名為 key 的物件列,不是陣列列——見 record_query。
    assert stored["q1"]["rows"][0] == {"system": "CRM", "tickets": 42}
    # `.invoke(..., config={"run_id": ...})` threads that run_id through to the tool wrapper via
    # the injected `callbacks` param (see app.agent.tools.recording.tool_run_id) -- this is the
    # same correlation key EventBridge.on_tool_end pops with from the matching astream_events
    # on_tool_end event.
    record = recorder.pop(str(run_id))
    assert record is not None and record.query_id == "q1" and record.intent == "各系統工單數"


def test_run_sql_error_returns_unframed_error(toolset) -> None:
    tools, workspace, recorder = toolset
    run_id = uuid.uuid4()
    output = tools["run_sql"].invoke(
        {"sql": "SELECT * FROM missing", "intent": "x"}, config={"run_id": run_id}
    )
    assert output.startswith("SQL_ERROR:")
    assert load_all_results(workspace) == {}
    assert recorder.pop(str(run_id)) is None


def test_preview_data_rejects_bad_table_name(toolset) -> None:
    tools, _, _ = toolset
    output = tools["preview_data"].invoke({"table": "orders; DROP TABLE x"})
    assert "SQL_ERROR" in output or "無效" in output


def test_run_sql_on_date_column_records_without_raising(tmp_path) -> None:
    csv_path = tmp_path / "events.csv"
    csv_path.write_text("system,created\nCRM,2026-07-01\nERP,2026-07-02\n", encoding="utf-8")
    connection = open_locked_connection([Source("events", str(csv_path), "csv")])
    workspace = prepare_local_layout(tmp_path / "ws", "user-1", "sess-1")
    recorder = ToolResultRecorder()
    tools = {tool.name: tool for tool in build_data_tools(connection, workspace, recorder)}

    output = tools["run_sql"].invoke(
        {"sql": "SELECT system, created FROM events ORDER BY created", "intent": "各系統建立日期"}
    )
    assert output.startswith("tableId: q1\n\n")
    stored = load_all_results(workspace)
    assert stored["q1"]["rows"][0] == {"system": "CRM", "created": "2026-07-01"}


def test_run_sql_pop_last_record_rows_are_json_safe_and_match_store(tmp_path) -> None:
    """ToolRunRecord.rows（事件層 wire 表示，陣列列）與 load_all_results（落檔，物件列）必須
    是同一份正規化後的資料，只是容器形狀分岔——回歸測試 Finding 1：record_query 曾經只正規化
    自己組的 payload，ToolRunRecord.rows 仍留著原始 DuckDB Decimal/date/datetime 物件，事件層
    json.dumps(record.rows) 會重現落檔曾經有過的 TypeError。"""
    csv_path = tmp_path / "events.csv"
    csv_path.write_text(
        "system,tickets,created\nCRM,42.5,2026-07-01\nERP,7,2026-07-02\n", encoding="utf-8"
    )
    connection = open_locked_connection([Source("events", str(csv_path), "csv")])
    workspace = prepare_local_layout(tmp_path / "ws", "user-1", "sess-1")
    recorder = ToolResultRecorder()
    tools = {tool.name: tool for tool in build_data_tools(connection, workspace, recorder)}

    run_id = uuid.uuid4()
    tools["run_sql"].invoke(
        {
            "sql": "SELECT system, tickets, created FROM events ORDER BY created",
            "intent": "各系統工單數與建立日期",
        },
        config={"run_id": run_id},
    )

    record = recorder.pop(str(run_id))
    assert record is not None
    assert record.rows[0] == ["CRM", 42.5, "2026-07-01"]
    # never raises: every cell is a JSON-native type after normalization.
    json.dumps(record.rows)

    stored = load_all_results(workspace)
    # 同一份正規化值,只是容器形狀分岔(wire 陣列列 vs. 落檔物件列)——用 columns 重新配對
    # 兩邊比對,而不是直接比較 record.rows == stored[...]["rows"]。
    expected_object_rows = [dict(zip(record.columns, row, strict=False)) for row in record.rows]
    assert expected_object_rows == stored["q1"]["rows"]


def test_two_recorders_do_not_see_each_other_records(tmp_path) -> None:
    """Regression for Finding 1 -- the old module-global `_last_record` slot meant a second
    concurrent `/chat` request's run_sql call could silently overwrite the first request's
    pending record before it was popped, leaking one tenant's query result into another's TABLE
    event. Two independently-built tool sets (each with its own `ToolResultRecorder`, exactly
    as `app.main.chat` builds one recorder per request) must never observe each other's records,
    regardless of call interleaving."""
    csv_path = tmp_path / "orders.csv"
    csv_path.write_text("system,tickets\nCRM,42\nERP,7\n", encoding="utf-8")
    connection = open_locked_connection([Source("orders", str(csv_path), "csv")])

    workspace_a = prepare_local_layout(tmp_path / "ws-a", "user-a", "sess-a")
    recorder_a = ToolResultRecorder()
    tools_a = {tool.name: tool for tool in build_data_tools(connection, workspace_a, recorder_a)}

    workspace_b = prepare_local_layout(tmp_path / "ws-b", "user-b", "sess-b")
    recorder_b = ToolResultRecorder()
    tools_b = {tool.name: tool for tool in build_data_tools(connection, workspace_b, recorder_b)}

    # Simulate interleaving: request B's run_sql executes (and records) before request A pops.
    # Both runs deliberately share the same run_id string -- a plain module-global slot (the
    # pre-fix design) would have no way to tell them apart even by key; each request's own
    # recorder instance is what actually provides the isolation.
    shared_run_id = uuid.uuid4()
    tools_a["run_sql"].invoke(
        {"sql": "SELECT system, tickets FROM orders WHERE system = 'CRM'", "intent": "A的查詢"},
        config={"run_id": shared_run_id},
    )
    tools_b["run_sql"].invoke(
        {"sql": "SELECT system, tickets FROM orders WHERE system = 'ERP'", "intent": "B的查詢"},
        config={"run_id": shared_run_id},
    )

    record_a = recorder_a.pop(str(shared_run_id))
    record_b = recorder_b.pop(str(shared_run_id))

    assert record_a is not None and record_a.intent == "A的查詢"
    assert record_a.rows == [["CRM", 42]]
    assert record_b is not None and record_b.intent == "B的查詢"
    assert record_b.rows == [["ERP", 7]]

    # Each recorder is now empty -- popping again (or from the other recorder) finds nothing
    # left over that could bleed into a later, unrelated tool call.
    assert recorder_a.pop(str(shared_run_id)) is None
    assert recorder_b.pop(str(shared_run_id)) is None


def test_run_sql_concurrent_calls_do_not_collide_on_query_id(toolset) -> None:
    """Regression for the production trace: a single qwen turn that emits multiple parallel
    `run_sql` tool calls gets them dispatched by LangGraph's ToolNode onto separate executor
    threads (sync `@tool` -> `run_in_executor`). Two threads racing inside `run_sql_tool`
    can both read `next_query_id` before either has written its `.sql`/`.json` files (the file
    count is non-atomic), so both get `q1`; their two-step `record_query` writes (.sql then
    .json) can then interleave, pairing thread A's SQL with thread B's result JSON. A
    `threading.Barrier` forces both threads to enter `run_sql` at the same instant so the race
    fires deterministically instead of relying on scheduler luck."""
    tools, workspace, recorder = toolset
    entry_barrier = threading.Barrier(2)

    def invoke_run_sql(sql: str, intent: str, run_id: uuid.UUID) -> str:
        entry_barrier.wait()
        return tools["run_sql"].invoke({"sql": sql, "intent": intent}, config={"run_id": run_id})

    run_id_crm = uuid.uuid4()
    run_id_erp = uuid.uuid4()
    with ThreadPoolExecutor(max_workers=2) as executor:
        future_crm = executor.submit(
            invoke_run_sql,
            "SELECT system, tickets FROM orders WHERE system = 'CRM'",
            "CRM 的工單數",
            run_id_crm,
        )
        future_erp = executor.submit(
            invoke_run_sql,
            "SELECT system, tickets FROM orders WHERE system = 'ERP'",
            "ERP 的工單數",
            run_id_erp,
        )
        output_crm = future_crm.result()
        output_erp = future_erp.result()

    table_id_crm = output_crm.split("\n", 1)[0].removeprefix("tableId: ")
    table_id_erp = output_erp.split("\n", 1)[0].removeprefix("tableId: ")
    assert table_id_crm != table_id_erp

    stored = load_all_results(workspace)
    assert len(stored) == 2
    assert set(stored) == {table_id_crm, table_id_erp}

    for query_id, expected_intent, expected_needle, expected_row in (
        (table_id_crm, "CRM 的工單數", "CRM", {"system": "CRM", "tickets": 42}),
        (table_id_erp, "ERP 的工單數", "ERP", {"system": "ERP", "tickets": 7}),
    ):
        sql_text = (workspace.queries_dir / f"{query_id}.sql").read_text(encoding="utf-8")
        assert expected_needle in sql_text
        assert stored[query_id]["intent"] == expected_intent
        assert stored[query_id]["rows"] == [expected_row]

    assert recorder.pop(str(run_id_crm)) is not None
    assert recorder.pop(str(run_id_erp)) is not None


FETCH_CONNECTORS_YAML = """\
connectors:
  - name: line_list
    kind: lookup
    description: 產線清單
    endpoint: ${TEST_API_BASE}/lines
    method: GET
    auth: bearer:TEST_API_TOKEN
    params: {}
  - name: mes_yield
    kind: data
    description: 產線良率
    endpoint: ${TEST_API_BASE}/yield
    method: POST
    auth: bearer:TEST_API_TOKEN
    params:
      line_id:
        type: str
        required: true
        validate_against: {connector: line_list, column: line_id}
      start_date: {type: date, required: true}
    limits: {timeout_s: 10, max_bytes: 1000000, max_rows: 50000}
  - name: tiny_rows
    kind: data
    description: max_rows 回滾測試專用(限 2 列)
    endpoint: ${TEST_API_BASE}/tiny
    method: GET
    auth: bearer:TEST_API_TOKEN
    params: {}
    limits: {timeout_s: 10, max_bytes: 1000000, max_rows: 2}
"""

LINE_LIST_PAYLOAD = json.dumps(
    [{"line_id": "AX-03"}, {"line_id": "AX-30"}, {"line_id": "BX-11"}]
).encode()

YIELD_PAYLOAD = json.dumps(
    [
        {"line_id": "AX-03", "yield": 0.95},
        {"line_id": "AX-03", "yield": 0.96},
        {"line_id": "AX-03", "yield": 0.94},
    ]
).encode()


class _FakeExecuteFetch:
    """`execute_fetch` 換身:記錄每次呼叫(connector 名+params)、按 connector 名回固定 payload,
    不碰網路。"""

    def __init__(self, payloads: dict[str, bytes]) -> None:
        self.payloads = payloads
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, definition, params: dict) -> bytes:
        self.calls.append((definition.name, dict(params)))
        return self.payloads[definition.name]


def _build_fetch_toolset(tmp_path, monkeypatch, extra_sources=None):
    monkeypatch.setenv("TEST_API_BASE", "http://api.internal")
    monkeypatch.setenv("TEST_API_TOKEN", "secret-token")
    config_path = tmp_path / "connectors.yaml"
    config_path.write_text(FETCH_CONNECTORS_YAML, encoding="utf-8")
    registry = load_connector_registry(config_path)
    workspace = prepare_local_layout(tmp_path / "ws", "user-1", "sess-1")
    connection = open_locked_connection(
        extra_sources or [], api_snapshots_dir=workspace.api_snapshots_dir
    )
    recorder = ToolResultRecorder()
    tools = {
        tool.name: tool
        for tool in build_data_tools(connection, workspace, recorder, connectors=registry)
    }
    return tools, workspace, connection


def test_fetch_api_data_success_mountsTableAndReturnsFramedSchema(tmp_path, monkeypatch) -> None:
    tools, workspace, connection = _build_fetch_toolset(tmp_path, monkeypatch)
    fake_fetch = _FakeExecuteFetch({"line_list": LINE_LIST_PAYLOAD, "mes_yield": YIELD_PAYLOAD})
    monkeypatch.setattr(data_module, "execute_fetch", fake_fetch)

    # lookup 一律先掛(alias = connector 名,同 Task 6 prompt 約定)才能通過 validate_against。
    tools["fetch_api_data"].invoke({"connector": "line_list", "params": {}, "alias": "line_list"})
    output = tools["fetch_api_data"].invoke(
        {
            "connector": "mes_yield",
            "params": {"line_id": "AX-03", "start_date": "2026-08-01"},
            "alias": "yield_data",
        }
    )

    # 落檔身分改指紋(§12.4):檔名不再是 alias,而是 (connector, params) 的內容指紋。
    yield_fingerprint = snapshot_fingerprint(
        "mes_yield", {"line_id": "AX-03", "start_date": "2026-08-01"}
    )
    assert (workspace.api_snapshots_dir / f"{yield_fingerprint}.json").exists()
    assert connection.execute("SELECT * FROM yield_data").fetchall() != []
    assert output.startswith("table yield_data mounted (3 rows)")
    assert DATA_FRAME_OPEN in output and DATA_FRAME_CLOSE in output
    assert "line_id" in output and "yield" in output
    records = load_fetch_records(workspace)
    assert {
        "fingerprint": yield_fingerprint,
        "alias": "yield_data",
        "connector": "mes_yield",
        "params": {"line_id": "AX-03", "start_date": "2026-08-01"},
    } in records


def test_fetch_api_data_unknownConnector_listsAvailable(tmp_path, monkeypatch) -> None:
    tools, _, _ = _build_fetch_toolset(tmp_path, monkeypatch)
    fake_fetch = _FakeExecuteFetch({})
    monkeypatch.setattr(data_module, "execute_fetch", fake_fetch)

    output = tools["fetch_api_data"].invoke(
        {"connector": "no_such_connector", "params": {}, "alias": "whatever"}
    )

    assert output.startswith(FETCH_ERROR_PREFIX)
    assert "line_list" in output and "mes_yield" in output
    assert fake_fetch.calls == []


def test_fetch_api_data_missingRequiredParam_namesParamAndType(tmp_path, monkeypatch) -> None:
    tools, _, _ = _build_fetch_toolset(tmp_path, monkeypatch)
    fake_fetch = _FakeExecuteFetch({})
    monkeypatch.setattr(data_module, "execute_fetch", fake_fetch)

    output = tools["fetch_api_data"].invoke(
        {"connector": "mes_yield", "params": {"line_id": "AX-03"}, "alias": "yield_data"}
    )

    assert output.startswith(FETCH_ERROR_PREFIX)
    assert "start_date" in output and "date" in output
    assert fake_fetch.calls == []


def test_fetch_api_data_invalidAlias_rejected(tmp_path, monkeypatch) -> None:
    tools, _, _ = _build_fetch_toolset(tmp_path, monkeypatch)
    fake_fetch = _FakeExecuteFetch({})
    monkeypatch.setattr(data_module, "execute_fetch", fake_fetch)

    output = tools["fetch_api_data"].invoke(
        {"connector": "mes_yield", "params": {}, "alias": "bad-name;drop"}
    )

    assert output.startswith(FETCH_ERROR_PREFIX)
    assert "底線" in output
    assert fake_fetch.calls == []


def test_fetch_api_data_aliasCollidesWithMountedTable_rejected(tmp_path, monkeypatch) -> None:
    csv_path = tmp_path / "orders.csv"
    csv_path.write_text("system,tickets\nCRM,42\n", encoding="utf-8")
    tools, _, _ = _build_fetch_toolset(
        tmp_path, monkeypatch, extra_sources=[Source("orders", str(csv_path), "csv")]
    )
    fake_fetch = _FakeExecuteFetch({})
    monkeypatch.setattr(data_module, "execute_fetch", fake_fetch)

    output = tools["fetch_api_data"].invoke(
        {"connector": "mes_yield", "params": {}, "alias": "orders"}
    )

    assert output.startswith(FETCH_ERROR_PREFIX)
    assert "orders" in output
    assert fake_fetch.calls == []


def test_fetch_api_data_validateAgainst_valueMissing_returnsNearestCandidates(
    tmp_path, monkeypatch
) -> None:
    tools, _, _ = _build_fetch_toolset(tmp_path, monkeypatch)
    fake_fetch = _FakeExecuteFetch({"line_list": LINE_LIST_PAYLOAD, "mes_yield": YIELD_PAYLOAD})
    monkeypatch.setattr(data_module, "execute_fetch", fake_fetch)
    tools["fetch_api_data"].invoke({"connector": "line_list", "params": {}, "alias": "line_list"})

    output = tools["fetch_api_data"].invoke(
        {
            "connector": "mes_yield",
            "params": {"line_id": "AX-3", "start_date": "2026-08-01"},
            "alias": "yield_data",
        }
    )

    assert output.startswith(FETCH_ERROR_PREFIX)
    assert "AX-03" in output
    assert "line_list" in output
    # 只呼叫過 line_list 一次;驗證失敗擋在 execute_fetch(mes_yield) 之前。
    assert fake_fetch.calls == [("line_list", {})]


def test_fetch_api_data_validateAgainst_lookupNotFetched_redirectsToLookup(
    tmp_path, monkeypatch
) -> None:
    tools, _, _ = _build_fetch_toolset(tmp_path, monkeypatch)
    fake_fetch = _FakeExecuteFetch({"mes_yield": YIELD_PAYLOAD})
    monkeypatch.setattr(data_module, "execute_fetch", fake_fetch)

    output = tools["fetch_api_data"].invoke(
        {
            "connector": "mes_yield",
            "params": {"line_id": "AX-03", "start_date": "2026-08-01"},
            "alias": "yield_data",
        }
    )

    assert output.startswith(FETCH_ERROR_PREFIX)
    assert "fetch_api_data" in output and "line_list" in output
    assert fake_fetch.calls == []


def test_fetch_api_data_perTurnCap_exceeded_returnsGuidance(tmp_path, monkeypatch) -> None:
    tools, _, _ = _build_fetch_toolset(tmp_path, monkeypatch)
    fake_fetch = _FakeExecuteFetch({"line_list": LINE_LIST_PAYLOAD})
    monkeypatch.setattr(data_module, "execute_fetch", fake_fetch)

    for call_index in range(MAX_FETCHES_PER_TURN):
        output = tools["fetch_api_data"].invoke(
            {"connector": "line_list", "params": {}, "alias": f"line_list_{call_index}"}
        )
        assert not output.startswith(FETCH_ERROR_PREFIX)

    output = tools["fetch_api_data"].invoke(
        {"connector": "line_list", "params": {}, "alias": "one_too_many"}
    )

    assert output.startswith(FETCH_ERROR_PREFIX)
    assert str(MAX_FETCHES_PER_TURN) in output
    assert len(fake_fetch.calls) == MAX_FETCHES_PER_TURN


def test_fetch_api_data_zeroRows_statesEmptyExplicitly(tmp_path, monkeypatch) -> None:
    tools, _, _ = _build_fetch_toolset(tmp_path, monkeypatch)
    fake_fetch = _FakeExecuteFetch({"line_list": b"[]"})
    monkeypatch.setattr(data_module, "execute_fetch", fake_fetch)

    output = tools["fetch_api_data"].invoke(
        {"connector": "line_list", "params": {}, "alias": "empty_lines"}
    )

    assert output.startswith("table empty_lines mounted (0 rows)")
    assert "0 rows" in output


def test_fetch_api_data_lookupDroppedAfterFetch_redirectsToRefetchLookup(
    tmp_path, monkeypatch
) -> None:
    """Regression: run_sql allows arbitrary DDL, so a lookup table fetched earlier this turn
    can be gone (`DROP TABLE`) by the time a later fetch_api_data call needs it. The stale
    fetch-records fallback used to hand back that now-missing alias unchecked, and the
    validate_against COUNT query against it raised an uncaught duckdb.CatalogException --
    breaking the tool's never-raise/FETCH_ERROR contract instead of redirecting the model."""
    tools, _, _ = _build_fetch_toolset(tmp_path, monkeypatch)
    fake_fetch = _FakeExecuteFetch({"line_list": LINE_LIST_PAYLOAD, "mes_yield": YIELD_PAYLOAD})
    monkeypatch.setattr(data_module, "execute_fetch", fake_fetch)
    tools["fetch_api_data"].invoke({"connector": "line_list", "params": {}, "alias": "line_list"})
    tools["run_sql"].invoke({"sql": "DROP TABLE line_list", "intent": "清掉 lookup 表"})

    output = tools["fetch_api_data"].invoke(
        {
            "connector": "mes_yield",
            "params": {"line_id": "AX-03", "start_date": "2026-08-01"},
            "alias": "yield_data",
        }
    )

    assert output.startswith(FETCH_ERROR_PREFIX)
    assert "fetch_api_data" in output and "line_list" in output
    # 沒有真的去呼叫 mes_yield 的 execute_fetch -- 驗證擋在執行之前。
    assert fake_fetch.calls == [("line_list", {})]


def test_fetch_api_data_malformedJsonResponse_cleansUpSnapshot(tmp_path, monkeypatch) -> None:
    tools, workspace, _ = _build_fetch_toolset(tmp_path, monkeypatch)
    fake_fetch = _FakeExecuteFetch({"line_list": b"not json"})
    monkeypatch.setattr(data_module, "execute_fetch", fake_fetch)

    output = tools["fetch_api_data"].invoke(
        {"connector": "line_list", "params": {}, "alias": "broken_lines"}
    )

    broken_fingerprint = snapshot_fingerprint("line_list", {})
    assert output.startswith(FETCH_ERROR_PREFIX)
    assert not (workspace.api_snapshots_dir / f"{broken_fingerprint}.json").exists()
    assert load_fetch_records(workspace) == []


def test_fetch_api_data_sameParamsSameAlias_refreshesInsteadOfRejecting(
    tmp_path, monkeypatch
) -> None:
    """M9 化解:同 (connector, params) 重抓、同 alias 不再退貨,而是覆蓋刷新。"""
    tools, workspace, connection = _build_fetch_toolset(tmp_path, monkeypatch)
    fake_fetch = _FakeExecuteFetch({"line_list": LINE_LIST_PAYLOAD})
    monkeypatch.setattr(data_module, "execute_fetch", fake_fetch)

    first_output = tools["fetch_api_data"].invoke(
        {"connector": "line_list", "params": {}, "alias": "line_list"}
    )
    # 換一批新資料模擬同源重抓(同 connector/params,但上游回應內容已更新)。
    fake_fetch.payloads["line_list"] = json.dumps([{"line_id": "ZZ-99"}]).encode()
    second_output = tools["fetch_api_data"].invoke(
        {"connector": "line_list", "params": {}, "alias": "line_list"}
    )

    assert not first_output.startswith(FETCH_ERROR_PREFIX)
    assert not second_output.startswith(FETCH_ERROR_PREFIX)
    assert second_output.startswith("table line_list mounted (1 rows)")
    assert connection.execute("SELECT line_id FROM line_list").fetchall() == [("ZZ-99",)]
    fingerprint = snapshot_fingerprint("line_list", {})
    assert (workspace.api_snapshots_dir / f"{fingerprint}.json").exists()
    assert fake_fetch.calls == [("line_list", {}), ("line_list", {})]
    records = load_fetch_records(workspace)
    assert len(records) == 2
    assert records[0]["fingerprint"] == records[1]["fingerprint"] == fingerprint


def test_fetch_api_data_sameParamsDifferentAlias_shareSameFingerprintFile(
    tmp_path, monkeypatch
) -> None:
    tools, workspace, connection = _build_fetch_toolset(tmp_path, monkeypatch)
    fake_fetch = _FakeExecuteFetch({"line_list": LINE_LIST_PAYLOAD})
    monkeypatch.setattr(data_module, "execute_fetch", fake_fetch)

    tools["fetch_api_data"].invoke({"connector": "line_list", "params": {}, "alias": "lines_a"})
    output = tools["fetch_api_data"].invoke(
        {"connector": "line_list", "params": {}, "alias": "lines_b"}
    )

    assert not output.startswith(FETCH_ERROR_PREFIX)
    fingerprint = snapshot_fingerprint("line_list", {})
    snapshot_files = list(workspace.api_snapshots_dir.glob(f"{fingerprint}*.json"))
    assert len(snapshot_files) == 1  # 不重複落檔,同指紋只有一份 snapshot。
    assert (
        connection.execute("SELECT * FROM lines_a").fetchall()
        == connection.execute("SELECT * FROM lines_b").fetchall()
    )


def test_fetch_api_data_differentParamsSameAlias_rejectsTableNameCollision(
    tmp_path, monkeypatch
) -> None:
    tools, _, connection = _build_fetch_toolset(tmp_path, monkeypatch)
    fake_fetch = _FakeExecuteFetch({"line_list": LINE_LIST_PAYLOAD, "mes_yield": YIELD_PAYLOAD})
    monkeypatch.setattr(data_module, "execute_fetch", fake_fetch)
    tools["fetch_api_data"].invoke({"connector": "line_list", "params": {}, "alias": "line_list"})
    tools["fetch_api_data"].invoke(
        {
            "connector": "mes_yield",
            "params": {"line_id": "AX-03", "start_date": "2026-08-01"},
            "alias": "yield_data",
        }
    )

    output = tools["fetch_api_data"].invoke(
        {
            "connector": "mes_yield",
            "params": {"line_id": "AX-03", "start_date": "2026-08-02"},  # 換 params,同 alias
            "alias": "yield_data",
        }
    )

    assert output.startswith(FETCH_ERROR_PREFIX)
    assert "yield_data" in output
    # 退貨擋在 execute_fetch 之前——只有前兩次成功呼叫,沒有第三次。
    assert fake_fetch.calls == [
        ("line_list", {}),
        ("mes_yield", {"line_id": "AX-03", "start_date": "2026-08-01"}),
    ]
    # 原表資料未被沖掉。
    assert connection.execute("SELECT COUNT(*) FROM yield_data").fetchone()[0] == 3


def test_fetch_api_data_maxRowsExceeded_rollbackDeletesFingerprintFile(
    tmp_path, monkeypatch
) -> None:
    tools, workspace, _ = _build_fetch_toolset(tmp_path, monkeypatch)
    # tiny_rows 限 2 列,LINE_LIST_PAYLOAD 有 3 列,觸發 max_rows 回滾。
    fake_fetch = _FakeExecuteFetch({"tiny_rows": LINE_LIST_PAYLOAD})
    monkeypatch.setattr(data_module, "execute_fetch", fake_fetch)

    output = tools["fetch_api_data"].invoke(
        {"connector": "tiny_rows", "params": {}, "alias": "tiny_table"}
    )

    fingerprint = snapshot_fingerprint("tiny_rows", {})
    assert output.startswith(FETCH_ERROR_PREFIX)
    assert not (workspace.api_snapshots_dir / f"{fingerprint}.json").exists()
    assert load_fetch_records(workspace) == []


def test_fetch_api_data_legacyFetchRecordMissingFingerprint_noKeyError(
    tmp_path, monkeypatch
) -> None:
    """§12 review finding 1:pre-§12 的 fetches.json 記錄沒有 fingerprint 欄——直接
    `record["fingerprint"]` 對這種舊格式紀錄會 KeyError,炸出 fetch_api_data_tool 沒有外層
    try 接住的 never-raise 契約。`.get()` 兩欄都要有才算一筆有效紀錄,缺一律當沒紀錄,落回
    既有的表名衝突退貨,而不是未捕捉例外。"""
    csv_path = tmp_path / "orders.csv"
    csv_path.write_text("system\nCRM\n", encoding="utf-8")
    tools, workspace, _ = _build_fetch_toolset(
        tmp_path, monkeypatch, extra_sources=[Source("orders", str(csv_path), "csv")]
    )
    fake_fetch = _FakeExecuteFetch({})
    monkeypatch.setattr(data_module, "execute_fetch", fake_fetch)
    workspace.api_snapshots_dir.mkdir(parents=True, exist_ok=True)
    workspace.fetches_path.write_text(
        json.dumps([{"alias": "orders", "connector": "legacy_connector", "params": {}}]),
        encoding="utf-8",
    )

    output = tools["fetch_api_data"].invoke(
        {"connector": "mes_yield", "params": {}, "alias": "orders"}
    )

    assert output.startswith(FETCH_ERROR_PREFIX)
    assert "orders" in output
    assert fake_fetch.calls == []


def test_fetch_api_data_refreshOversized_preservesOldTableAndSnapshot(
    tmp_path, monkeypatch
) -> None:
    """§12 review finding 3(stage-then-swap):同指紋刷新這次回應超過 max_rows,舊表、舊指紋
    檔在驗証通過前完全不動——不是先 CREATE OR REPLACE 真表+落正式檔再回滾(舊序會先把好資料
    沖掉才發現要回滾)。"""
    tools, workspace, connection = _build_fetch_toolset(tmp_path, monkeypatch)
    fake_fetch = _FakeExecuteFetch({"tiny_rows": json.dumps([{"line_id": "AX-03"}]).encode()})
    monkeypatch.setattr(data_module, "execute_fetch", fake_fetch)

    first_output = tools["fetch_api_data"].invoke(
        {"connector": "tiny_rows", "params": {}, "alias": "tiny_table"}
    )
    assert not first_output.startswith(FETCH_ERROR_PREFIX)
    fingerprint = snapshot_fingerprint("tiny_rows", {})
    snapshot_path = workspace.api_snapshots_dir / f"{fingerprint}.json"
    old_bytes = snapshot_path.read_bytes()

    # tiny_rows 上限 2 列,LINE_LIST_PAYLOAD 有 3 列,同指紋刷新觸發 max_rows 回滾。
    fake_fetch.payloads["tiny_rows"] = LINE_LIST_PAYLOAD
    second_output = tools["fetch_api_data"].invoke(
        {"connector": "tiny_rows", "params": {}, "alias": "tiny_table"}
    )

    assert second_output.startswith(FETCH_ERROR_PREFIX)
    assert connection.execute("SELECT COUNT(*) FROM tiny_table").fetchone()[0] == 1
    assert connection.execute("SELECT line_id FROM tiny_table").fetchall() == [("AX-03",)]
    assert snapshot_path.exists()
    assert snapshot_path.read_bytes() == old_bytes
    assert len(load_fetch_records(workspace)) == 1  # 失敗那次不新增紀錄
    assert list(workspace.api_snapshots_dir.glob("*.json.stage")) == []


def test_fetch_api_data_refreshMalformed_preservesOldTableAndSnapshot(
    tmp_path, monkeypatch
) -> None:
    """同上,回應改成無法解析的 JSON 的變體。"""
    tools, workspace, connection = _build_fetch_toolset(tmp_path, monkeypatch)
    fake_fetch = _FakeExecuteFetch({"line_list": LINE_LIST_PAYLOAD})
    monkeypatch.setattr(data_module, "execute_fetch", fake_fetch)

    first_output = tools["fetch_api_data"].invoke(
        {"connector": "line_list", "params": {}, "alias": "line_list"}
    )
    assert not first_output.startswith(FETCH_ERROR_PREFIX)
    fingerprint = snapshot_fingerprint("line_list", {})
    snapshot_path = workspace.api_snapshots_dir / f"{fingerprint}.json"
    old_bytes = snapshot_path.read_bytes()

    fake_fetch.payloads["line_list"] = b"not json"
    second_output = tools["fetch_api_data"].invoke(
        {"connector": "line_list", "params": {}, "alias": "line_list"}
    )

    assert second_output.startswith(FETCH_ERROR_PREFIX)
    assert connection.execute("SELECT COUNT(*) FROM line_list").fetchone()[0] == 3
    assert snapshot_path.exists()
    assert snapshot_path.read_bytes() == old_bytes
    assert len(load_fetch_records(workspace)) == 1
    assert list(workspace.api_snapshots_dir.glob("*.json.stage")) == []


def test_build_data_tools_noRegistry_returnsThreeToolsOnly(tmp_path) -> None:
    csv_path = tmp_path / "orders.csv"
    csv_path.write_text("system,tickets\nCRM,42\n", encoding="utf-8")
    connection = open_locked_connection([Source("orders", str(csv_path), "csv")])
    workspace = prepare_local_layout(tmp_path / "ws", "user-1", "sess-1")
    recorder = ToolResultRecorder()

    tools_without_arg = {tool.name for tool in build_data_tools(connection, workspace, recorder)}
    tools_with_none = {
        tool.name for tool in build_data_tools(connection, workspace, recorder, connectors=None)
    }

    assert tools_without_arg == tools_with_none == {"get_schema", "run_sql", "preview_data"}
