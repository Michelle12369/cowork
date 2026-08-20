import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

import app.agent.tools.data as data_module
from app.agent.tools.data import MAX_FETCHES_PER_TURN, build_data_tools
from app.agent.tools.framing import DATA_FRAME_CLOSE, DATA_FRAME_OPEN
from app.agent.tools.recording import ToolResultRecorder
from app.engine.api_fetch import FETCH_ERROR_PREFIX, load_fetch_records
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

    assert (workspace.api_snapshots_dir / "yield_data.json").exists()
    assert connection.execute("SELECT * FROM yield_data").fetchall() != []
    assert output.startswith("table yield_data mounted (3 rows)")
    assert DATA_FRAME_OPEN in output and DATA_FRAME_CLOSE in output
    assert "line_id" in output and "yield" in output
    records = load_fetch_records(workspace)
    assert {
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
