import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.agent.tools.data import build_data_tools
from app.agent.tools.framing import DATA_FRAME_CLOSE, DATA_FRAME_OPEN
from app.engine.duck import Source, open_locked_connection
from app.engine.results import load_all_results
from app.engine.workspace import prepare_local_layout


@pytest.fixture()
def toolset(tmp_path):
    csv_path = tmp_path / "orders.csv"
    csv_path.write_text("system,tickets\nCRM,42\nERP,7\n", encoding="utf-8")
    connection = open_locked_connection([Source("orders", str(csv_path), "csv")])
    workspace = prepare_local_layout(tmp_path / "ws", "user-1", "sess-1")
    tools = {tool.name: tool for tool in build_data_tools(connection, workspace)}
    return tools, workspace


def test_get_schema_is_framed_and_lists_table(toolset) -> None:
    tools, _ = toolset
    output = tools["get_schema"].invoke({})
    assert output.startswith(DATA_FRAME_OPEN) and output.endswith(DATA_FRAME_CLOSE)
    assert "orders" in output and "tickets" in output


def test_run_sql_records_result_and_returns_table_id(toolset) -> None:
    tools, workspace = toolset
    output = tools["run_sql"].invoke(
        {
            "sql": "SELECT system, tickets FROM orders ORDER BY tickets DESC",
            "intent": "各系統工單數",
        }
    )
    assert output.startswith("tableId: q1\n\n")
    assert "CRM" in output
    stored = load_all_results(workspace)
    # 落檔的 rows 是以欄名為 key 的物件列,不是陣列列——見 record_query。
    assert stored["q1"]["rows"][0] == {"system": "CRM", "tickets": 42}


def test_run_sql_error_returns_unframed_error(toolset) -> None:
    tools, workspace = toolset
    output = tools["run_sql"].invoke({"sql": "SELECT * FROM missing", "intent": "x"})
    assert output.startswith("SQL_ERROR:")
    assert load_all_results(workspace) == {}


def test_preview_data_rejects_bad_table_name(toolset) -> None:
    tools, _ = toolset
    output = tools["preview_data"].invoke({"table": "orders; DROP TABLE x"})
    assert "SQL_ERROR" in output or "無效" in output


def test_run_sql_on_date_column_records_without_raising(tmp_path) -> None:
    csv_path = tmp_path / "events.csv"
    csv_path.write_text("system,created\nCRM,2026-07-01\nERP,2026-07-02\n", encoding="utf-8")
    connection = open_locked_connection([Source("events", str(csv_path), "csv")])
    workspace = prepare_local_layout(tmp_path / "ws", "user-1", "sess-1")
    tools = {tool.name: tool for tool in build_data_tools(connection, workspace)}

    output = tools["run_sql"].invoke(
        {"sql": "SELECT system, created FROM events ORDER BY created", "intent": "各系統建立日期"}
    )
    assert output.startswith("tableId: q1\n\n")
    stored = load_all_results(workspace)
    assert stored["q1"]["rows"][0] == {"system": "CRM", "created": "2026-07-01"}


def test_run_sql_concurrent_calls_do_not_collide_on_query_id(toolset) -> None:
    """Regression for the production trace: a single qwen turn that emits multiple parallel
    `run_sql` tool calls gets them dispatched by LangGraph's ToolNode onto separate executor
    threads (sync `@tool` -> `run_in_executor`). Two threads racing inside `run_sql_tool`
    can both read `next_query_id` before either has written its `.sql`/`.json` files (the file
    count is non-atomic), so both get `q1`; their two-step `record_query` writes (.sql then
    .json) can then interleave, pairing thread A's SQL with thread B's result JSON. A
    `threading.Barrier` forces both threads to enter `run_sql` at the same instant so the race
    fires deterministically instead of relying on scheduler luck."""
    tools, workspace = toolset
    entry_barrier = threading.Barrier(2)

    def invoke_run_sql(sql: str, intent: str) -> str:
        entry_barrier.wait()
        return tools["run_sql"].invoke({"sql": sql, "intent": intent})

    with ThreadPoolExecutor(max_workers=2) as executor:
        future_crm = executor.submit(
            invoke_run_sql,
            "SELECT system, tickets FROM orders WHERE system = 'CRM'",
            "CRM 的工單數",
        )
        future_erp = executor.submit(
            invoke_run_sql,
            "SELECT system, tickets FROM orders WHERE system = 'ERP'",
            "ERP 的工單數",
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
