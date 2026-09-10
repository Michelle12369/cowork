"""LangChain tool 包裝層測試——用示範 connector(`registry.demo_connector`)+ in-memory
DuckDB 連線演練「每次呼叫自動落表／落表名為參數雜湊(同參數同名覆蓋、不同參數不同名、平行
呼叫各自對應正確參數)／信封欄位透傳／每 turn 上限／ConnectorToolError 透傳／執行緒安全」
整條路徑。"""

import threading
from concurrent.futures import ThreadPoolExecutor

import duckdb
import pytest

from app.agent.connectors.model import Connector, ConnectorTool
from app.agent.connectors.registry import demo_connector
from app.agent.connectors.wrapper import build_connector_tools, connector_table_name
from app.agent.tools.framing import DATA_FRAME_CLOSE, DATA_FRAME_OPEN


@pytest.fixture()
def connection():
    live_connection = duckdb.connect(":memory:")
    yield live_connection
    live_connection.close()


@pytest.fixture()
def connection_lock():
    return threading.Lock()


def _tools_by_name(connectors, connection, connection_lock, landing_dir, **kwargs):
    return {
        tool.name: tool
        for tool in build_connector_tools(
            connectors, connection, connection_lock, landing_dir, **kwargs
        )
    }


def test_build_connector_tools_names_are_connector_id_prefixed(
    tmp_path, connection, connection_lock
) -> None:
    tools = _tools_by_name((demo_connector(),), connection, connection_lock, tmp_path)

    assert set(tools) == {"demo_quality_list_fabs", "demo_quality_get_quality"}
    connector = demo_connector()
    assert connector.display_name in tools["demo_quality_get_quality"].description


def test_args_schema_does_not_contain_land_as(tmp_path, connection, connection_lock) -> None:
    tools = _tools_by_name((demo_connector(),), connection, connection_lock, tmp_path)

    schema = tools["demo_quality_get_quality"].args_schema
    assert "land_as" not in schema.get("properties", {})


def test_call_auto_lands_table_and_feedback_has_expected_shape(
    tmp_path, connection, connection_lock
) -> None:
    tools = _tools_by_name((demo_connector(),), connection, connection_lock, tmp_path)
    args = {"fab": "FAB_A", "week": "2026-W32"}
    expected_table = connector_table_name("demo_quality", "get_quality", args)

    result = tools["demo_quality_get_quality"].invoke(args)

    assert result.startswith(f"Landed table {expected_table} (demo_quality.get_quality, args")
    assert "700 rows" in result
    assert "columns" in result
    assert "lot_id" in result
    assert "Other response fields: errorCode=" in result
    assert "Preview of the first 20 rows:" in result
    assert DATA_FRAME_OPEN in result and DATA_FRAME_CLOSE in result
    assert "| lot_id |" in result
    assert "(showing the first 20 of 700 rows)" in result
    assert (
        "This table lives only for the current turn; call the tool again next turn if needed."
        in result
    )
    row_count = connection.execute(f'SELECT COUNT(*) FROM "{expected_table}"').fetchone()[0]
    assert row_count == 700


def test_call_below_preview_cap_omits_truncation_note(
    tmp_path, connection, connection_lock
) -> None:
    tools = _tools_by_name((demo_connector(),), connection, connection_lock, tmp_path)

    result = tools["demo_quality_list_fabs"].invoke({})

    assert "Landed table demo_quality_list_fabs" in result
    assert "showing the first" not in result


def test_no_args_tool_gets_pure_base_name_without_hash(
    tmp_path, connection, connection_lock
) -> None:
    tools = _tools_by_name((demo_connector(),), connection, connection_lock, tmp_path)

    tools["demo_quality_list_fabs"].invoke({})

    tables = {row[0] for row in connection.execute("SHOW TABLES").fetchall()}
    assert "demo_quality_list_fabs" in tables


def test_same_args_called_twice_keeps_same_table_name_and_replaces_content(
    tmp_path, connection, connection_lock
) -> None:
    """同參數重複呼叫落到同一張表(last-wins),用呼叫次數決定回應內容驗證表已被取代
    而非並存成另一張表。"""
    call_count = {"value": 0}

    def _grows_with_each_call(args: dict) -> object:
        call_count["value"] += 1
        return [{"x": index} for index in range(call_count["value"])]

    connector = Connector(
        connector_id="growing",
        display_name="Growing",
        tools=(
            ConnectorTool(
                name="fetch",
                description="row count grows with each call for same args",
                input_schema={
                    "type": "object",
                    "properties": {"scope": {"type": "string"}},
                    "required": ["scope"],
                },
                call=_grows_with_each_call,
            ),
        ),
        skills={"usage": {"SKILL.md": "# growing\n"}},
    )
    tools = _tools_by_name((connector,), connection, connection_lock, tmp_path)
    tool = tools["growing_fetch"]
    args = {"scope": "all"}
    expected_table = connector_table_name("growing", "fetch", args)

    first_result = tool.invoke(args)
    second_result = tool.invoke(args)

    assert f"Landed table {expected_table} (" in first_result
    assert f"Landed table {expected_table} (" in second_result
    tables = [row[0] for row in connection.execute("SHOW TABLES").fetchall()]
    assert tables == [expected_table]
    row_count = connection.execute(f'SELECT COUNT(*) FROM "{expected_table}"').fetchone()[0]
    assert row_count == 2


def test_different_args_get_different_table_names_and_both_persist(
    tmp_path, connection, connection_lock
) -> None:
    tools = _tools_by_name((demo_connector(),), connection, connection_lock, tmp_path)
    tool = tools["demo_quality_get_quality"]
    args_fab_a = {"fab": "FAB_A", "week": "2026-W32"}
    args_fab_b = {"fab": "FAB_B", "week": "2026-W32"}
    expected_table_a = connector_table_name("demo_quality", "get_quality", args_fab_a)
    expected_table_b = connector_table_name("demo_quality", "get_quality", args_fab_b)

    first_result = tool.invoke(args_fab_a)
    second_result = tool.invoke(args_fab_b)

    assert expected_table_a != expected_table_b
    assert f"Landed table {expected_table_a} (" in first_result
    assert f"Landed table {expected_table_b} (" in second_result
    tables = {row[0] for row in connection.execute("SHOW TABLES").fetchall()}
    assert {expected_table_a, expected_table_b} <= tables


def test_arg_key_order_does_not_change_table_name(tmp_path, connection, connection_lock) -> None:
    tools = _tools_by_name((demo_connector(),), connection, connection_lock, tmp_path)
    tool = tools["demo_quality_get_quality"]

    first_result = tool.invoke({"fab": "FAB_A", "week": "2026-W32"})
    second_result = tool.invoke({"week": "2026-W32", "fab": "FAB_A"})

    expected_table = connector_table_name(
        "demo_quality", "get_quality", {"fab": "FAB_A", "week": "2026-W32"}
    )
    assert f"Landed table {expected_table} (" in first_result
    assert f"Landed table {expected_table} (" in second_result
    tables = [row[0] for row in connection.execute("SHOW TABLES").fetchall()]
    assert tables == [expected_table]


def test_different_tools_each_get_own_base_name(tmp_path, connection, connection_lock) -> None:
    tools = _tools_by_name((demo_connector(),), connection, connection_lock, tmp_path)

    tools["demo_quality_list_fabs"].invoke({})
    tools["demo_quality_get_quality"].invoke({"fab": "FAB_A", "week": "2026-W32"})

    expected_table = connector_table_name(
        "demo_quality", "get_quality", {"fab": "FAB_A", "week": "2026-W32"}
    )
    tables = {row[0] for row in connection.execute("SHOW TABLES").fetchall()}
    assert {"demo_quality_list_fabs", expected_table} <= tables


def test_non_word_characters_in_connector_or_tool_name_become_underscores(
    tmp_path, connection, connection_lock
) -> None:
    connector = Connector(
        connector_id="my-connector.v2",
        display_name="Weird",
        tools=(
            ConnectorTool(
                name="do-thing",
                description="tool with punctuation in its name",
                input_schema={"type": "object", "properties": {}, "required": []},
                call=lambda args: [{"x": 1}],
            ),
        ),
        skills={"usage": {"SKILL.md": "# weird\n"}},
    )
    tools = _tools_by_name((connector,), connection, connection_lock, tmp_path)

    result = tools["my-connector.v2_do-thing"].invoke({})

    assert "Landed table my_connector_v2_do_thing (" in result
    tables = {row[0] for row in connection.execute("SHOW TABLES").fetchall()}
    assert "my_connector_v2_do_thing" in tables


def test_table_name_with_args_hash_passes_alias_validation(
    tmp_path, connection, connection_lock
) -> None:
    """雜湊後綴為純 hex,base 已 sanitize 成合法識別字——組出的表名必過 `_validate_alias`,
    以能成功建表(而非拋 ValueError)驗證。"""
    connector = Connector(
        connector_id="my-connector.v2",
        display_name="Weird",
        tools=(
            ConnectorTool(
                name="do-thing",
                description="tool with punctuation in its name and args",
                input_schema={
                    "type": "object",
                    "properties": {"scope": {"type": "string"}},
                    "required": ["scope"],
                },
                call=lambda args: [{"x": 1}],
            ),
        ),
        skills={"usage": {"SKILL.md": "# weird\n"}},
    )
    tools = _tools_by_name((connector,), connection, connection_lock, tmp_path)
    args = {"scope": "all"}
    expected_table = connector_table_name("my-connector.v2", "do-thing", args)

    result = tools["my-connector.v2_do-thing"].invoke(args)

    assert f"Landed table {expected_table} (" in result
    tables = {row[0] for row in connection.execute("SHOW TABLES").fetchall()}
    assert expected_table in tables


def test_empty_response_returns_actionable_message_without_landing(
    tmp_path, connection, connection_lock
) -> None:
    connector = Connector(
        connector_id="empty",
        display_name="Empty",
        tools=(
            ConnectorTool(
                name="nothing",
                description="always returns an empty list",
                input_schema={"type": "object", "properties": {}, "required": []},
                call=lambda args: [],
            ),
        ),
        skills={"usage": {"SKILL.md": "# empty\n"}},
    )
    tools = _tools_by_name((connector,), connection, connection_lock, tmp_path)

    result = tools["empty_nothing"].invoke({})

    assert "cannot land empty response" in result
    assert "empty_nothing" in result
    tables = connection.execute("SHOW TABLES").fetchall()
    assert tables == []


def test_connector_tool_error_passthrough_verbatim(
    tmp_path, connection, connection_lock, caplog
) -> None:
    """ConnectorToolError 的文字原樣回給模型, 不加第二層前綴; wrapper 本身不重複記
    log(connector 層已經記過)。"""
    tools = _tools_by_name((demo_connector(),), connection, connection_lock, tmp_path)

    with caplog.at_level("WARNING"):
        result = tools["demo_quality_get_quality"].invoke({"fab": "NOT_A_FAB", "week": "2026-W32"})

    assert "未知的 fab" in result
    assert "NOT_A_FAB" in result
    tables = connection.execute("SHOW TABLES").fetchall()
    assert tables == []
    assert caplog.records == []


def test_unexpected_exception_is_wrapped_and_never_raises(
    tmp_path, connection, connection_lock
) -> None:
    def _boom(args: dict) -> object:
        raise RuntimeError("boom")

    connector = Connector(
        connector_id="flaky",
        display_name="Flaky",
        tools=(
            ConnectorTool(
                name="explode",
                description="always raises",
                input_schema={"type": "object", "properties": {}, "required": []},
                call=_boom,
            ),
        ),
        skills={"usage": {"SKILL.md": "# flaky\n"}},
    )
    tools = _tools_by_name((connector,), connection, connection_lock, tmp_path)

    result = tools["flaky_explode"].invoke({})

    assert result == "Connector call failed: RuntimeError"
    assert "boom" not in result


def test_call_budget_refuses_after_limit_without_invoking_tool(
    tmp_path, connection, connection_lock
) -> None:
    call_count = {"value": 0}

    def _counted(args: dict) -> object:
        call_count["value"] += 1
        return [{"x": 1}]

    connector = Connector(
        connector_id="counted",
        display_name="Counted",
        tools=(
            ConnectorTool(
                name="ping",
                description="counts invocations",
                input_schema={"type": "object", "properties": {}, "required": []},
                call=_counted,
            ),
        ),
        skills={"usage": {"SKILL.md": "# counted\n"}},
    )
    tools = _tools_by_name((connector,), connection, connection_lock, tmp_path, call_budget=1)

    tools["counted_ping"].invoke({})
    second_result = tools["counted_ping"].invoke({})

    assert call_count["value"] == 1
    assert "exhausted" in second_result
    assert "1" in second_result


def test_call_budget_shared_across_tools_from_same_build_call(
    tmp_path, connection, connection_lock
) -> None:
    tools = _tools_by_name(
        (demo_connector(),), connection, connection_lock, tmp_path, call_budget=1
    )

    first_result = tools["demo_quality_list_fabs"].invoke({})
    second_result = tools["demo_quality_get_quality"].invoke({"fab": "FAB_A", "week": "2026-W32"})

    assert "Landed table" in first_result
    assert "exhausted" in second_result


def test_invalid_arg_value_passes_through_to_connector_actionable_error(
    tmp_path, connection, connection_lock
) -> None:
    """dict args_schema 模式下 LangChain 不做型別驗證——不合法的參數值原樣進 connector,
    由 connector/server 端以可行動錯誤拒絕(模型看得到未降級的完整 schema,源頭犯錯率
    本身較低);wrapper 維持 never-raise,錯誤以字串回傳。"""
    tools = _tools_by_name((demo_connector(),), connection, connection_lock, tmp_path)

    result = tools["demo_quality_get_quality"].invoke(
        {"fab": {"nested": "object"}, "week": "2026-W32"}
    )

    assert isinstance(result, str)
    assert "未知的 fab" in result


def test_missing_required_arg_caught_locally_with_field_name(
    tmp_path, connection, connection_lock
) -> None:
    """schema 的 required 欄位缺席時,本層驗證在發請求前攔下,訊息指名缺的欄位——
    NEVER 漏給 server 端炸回 pydantic 原始多行格式。"""
    tools = _tools_by_name((demo_connector(),), connection, connection_lock, tmp_path)

    result = tools["demo_quality_get_quality"].invoke({"fab": "FAB_A"})

    assert isinstance(result, str)
    assert result.startswith("Argument validation failed -- ")
    assert "week" in result
    tables = connection.execute("SHOW TABLES").fetchall()
    assert tables == []


def test_call_budget_thread_safety_smoke(tmp_path, connection, connection_lock) -> None:
    """budget 允許時兩個平行呼叫都照常執行——鎖只保護計數器本身;兩次呼叫參數皆為空,
    落到同一張表(last-wins),表仍成功建立。"""
    tools = _tools_by_name(
        (demo_connector(),), connection, connection_lock, tmp_path, call_budget=10
    )
    tool = tools["demo_quality_list_fabs"]

    results: list[str] = []

    def _invoke() -> None:
        results.append(tool.invoke({}))

    threads = [threading.Thread(target=_invoke) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(results) == 2
    assert all("Landed table demo_quality_list_fabs" in result for result in results)
    tables = {row[0] for row in connection.execute("SHOW TABLES").fetchall()}
    assert tables == {"demo_quality_list_fabs"}


def _single_tool_connector(connector_id: str, tool_name: str, response) -> Connector:
    return Connector(
        connector_id=connector_id,
        display_name=connector_id.title(),
        tools=(
            ConnectorTool(
                name=tool_name,
                description="fixture tool",
                input_schema={
                    "type": "object",
                    "properties": {"days": {"type": "integer"}},
                    "required": [],
                },
                call=lambda args: response,
            ),
        ),
        skills={},
    )


def test_feedback_fastmcp_result_wrapper_tells_model_to_read_r_data_result(
    tmp_path, connection, connection_lock
) -> None:
    connector = _single_tool_connector("sales", "list_orders", {"result": [{"a": 1}, {"a": 2}]})
    tools = _tools_by_name((connector,), connection, connection_lock, tmp_path)

    result = tools["sales_list_orders"].invoke({"days": 30})

    assert "Raw response shape: object with keys [result]." in result
    assert "The table was built from response.result (an array of 2 objects)" in result
    assert (
        "r = {data: <this raw response>} on success or r = {error: {code, message}} on failure"
        in result
    )
    assert "check r.error first" in result
    assert "read the rows with `r.data.result` -- not `r.data`" in result


def test_feedback_plain_array_says_r_data_is_already_the_array(
    tmp_path, connection, connection_lock
) -> None:
    connector = _single_tool_connector("sales", "list_orders", [{"a": 1}])
    tools = _tools_by_name((connector,), connection, connection_lock, tmp_path)

    result = tools["sales_list_orders"].invoke({})

    assert "Raw response shape: array of 1 object." in result
    assert (
        "r = {data: <this raw response>} on success or r = {error: {code, message}} on failure"
        in result
    )
    assert "check r.error first" in result
    assert "r.data is already the array" in result


def test_feedback_data_envelope_names_other_fields_location(
    tmp_path, connection, connection_lock
) -> None:
    connector = _single_tool_connector(
        "sales", "list_orders", {"data": [{"a": 1}], "errorCode": ""}
    )
    tools = _tools_by_name((connector,), connection, connection_lock, tmp_path)

    result = tools["sales_list_orders"].invoke({})

    assert "Raw response shape: object with keys [data, errorCode]." in result
    assert (
        "r = {data: <this raw response>} on success or r = {error: {code, message}} on failure"
        in result
    )
    assert "check r.error first" in result
    assert "read the rows with `r.data.data` -- not `r.data`" in result
    assert (
        "Other fields beside the rows (errorCode) were not landed; in the dashboard they are at "
        "r.data.errorCode" in result
    )


def test_feedback_non_envelope_dict_says_read_fields_directly(
    tmp_path, connection, connection_lock
) -> None:
    connector = _single_tool_connector("sales", "summary", {"fab": "A", "yield": 0.97})
    tools = _tools_by_name((connector,), connection, connection_lock, tmp_path)

    result = tools["sales_summary"].invoke({})

    assert "Raw response shape: object with keys [fab, yield]; landed as a single row." in result
    assert (
        "r = {data: <this raw response>} on success or r = {error: {code, message}} on failure"
        in result
    )
    assert "check r.error first" in result
    assert "read fields directly (r.data.fab)" in result


def test_landing_feedback_never_says_handler_receives_raw_response_directly(
    tmp_path, connection, connection_lock
) -> None:
    connector = _single_tool_connector("sales", "list_orders", {"result": [{"a": 1}, {"a": 2}]})
    tools = _tools_by_name((connector,), connection, connection_lock, tmp_path)

    result = tools["sales_list_orders"].invoke({"days": 30})

    assert "hands your handler the raw response as r.data" not in result


def test_parallel_calls_with_distinct_args_map_to_correct_own_table(
    tmp_path, connection, connection_lock
) -> None:
    """平行呼叫下每個執行緒的參數必須落到自己的表——用真執行緒池驗證,以表內容(而非
    計時)確認每張表對應到正確的呼叫參數,不會像序號命名那樣互相對錯表。"""
    tools = _tools_by_name((demo_connector(),), connection, connection_lock, tmp_path)
    tool = tools["demo_quality_get_quality"]
    fab_ids = ("FAB_A", "FAB_B", "FAB_C")

    def _invoke(fab_id: str) -> str:
        return tool.invoke({"fab": fab_id, "week": "2026-W32"})

    with ThreadPoolExecutor(max_workers=len(fab_ids)) as executor:
        results = list(executor.map(_invoke, fab_ids))

    assert all("Landed table" in result for result in results)
    for fab_id in fab_ids:
        expected_table = connector_table_name(
            "demo_quality", "get_quality", {"fab": fab_id, "week": "2026-W32"}
        )
        distinct_fabs = {
            row[0]
            for row in connection.execute(f'SELECT DISTINCT fab FROM "{expected_table}"').fetchall()
        }
        assert distinct_fabs == {fab_id}


def test_empty_response_feedback_still_describes_raw_shape(
    tmp_path, connection, connection_lock
) -> None:
    """0 列不落表, 但呼叫成功: 模型仍要拿到 Raw response shape 才知道 dashboard 讀哪一層."""
    connector = _single_tool_connector("sales", "list_orders", {"data": [], "errorCode": "E1"})
    tools = _tools_by_name((connector,), connection, connection_lock, tmp_path)

    result = tools["sales_list_orders"].invoke({"days": 30})

    assert "cannot land empty response" in result
    assert "Raw response shape: object with keys [data, errorCode]." in result
    assert "No table was landed because response.data is empty" in result
    assert "was built" not in result
    assert (
        "r = {data: <this raw response>} on success or r = {error: {code, message}} on failure"
        in result
    )
    assert "check r.error first" in result
    assert "read the rows with `r.data.data` -- not `r.data`" in result


def test_empty_non_envelope_object_feedback_does_not_invent_a_field(
    tmp_path, connection, connection_lock
) -> None:
    connector = _single_tool_connector("sales", "summary", {})
    tools = _tools_by_name((connector,), connection, connection_lock, tmp_path)

    result = tools["sales_summary"].invoke({})

    assert "cannot land empty response" in result
    assert "object with keys []; it is empty, so no table was landed" in result
    assert "read fields directly" not in result
