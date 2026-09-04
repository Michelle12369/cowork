"""LangChain tool 包裝層測試——用示範 connector(`registry.demo_connector`)+ in-memory
DuckDB 連線演練「land_as 落表／不帶 land_as 回 lookup JSON／壞 alias 可行動錯誤／每 turn
上限／ConnectorToolError 透傳／執行緒安全」整條路徑。"""

import threading

import duckdb
import pytest

from app.agent.connectors.model import Connector, ConnectorTool
from app.agent.connectors.registry import demo_connector
from app.agent.connectors.wrapper import build_connector_tools
from app.engine.replay_manifest import load_landings
from app.engine.workspace import prepare_local_layout


def _workspace(tmp_path):
    return prepare_local_layout(tmp_path, "user-1", "sess-1")


@pytest.fixture()
def connection():
    live_connection = duckdb.connect(":memory:")
    yield live_connection
    live_connection.close()


@pytest.fixture()
def connection_lock():
    return threading.Lock()


def _tools_by_name(connectors, connection, connection_lock, workspace, **kwargs):
    return {
        tool.name: tool
        for tool in build_connector_tools(
            connectors, connection, connection_lock, workspace, **kwargs
        )
    }


def test_build_connector_tools_names_are_connector_id_prefixed(
    tmp_path, connection, connection_lock
) -> None:
    workspace = _workspace(tmp_path)
    tools = _tools_by_name((demo_connector(),), connection, connection_lock, workspace)

    assert set(tools) == {"demo_quality_list_fabs", "demo_quality_get_quality"}
    connector = demo_connector()
    assert connector.display_name in tools["demo_quality_get_quality"].description


def test_land_as_lands_table_and_records_replay_manifest(
    tmp_path, connection, connection_lock
) -> None:
    workspace = _workspace(tmp_path)
    tools = _tools_by_name((demo_connector(),), connection, connection_lock, workspace)

    result = tools["demo_quality_get_quality"].invoke(
        {"fab": "FAB_A", "week": "2026-W32", "land_as": "quality_fab_a"}
    )

    assert result.startswith("已落表 quality_fab_a：")
    # envelope payload {"data": [...9 列...], "errorCode": ""} 寬鬆落表成單列表——data 欄
    # 整包變成 STRUCT/LIST 欄,不拆封,故列數是 1 而非 9。
    assert "1 列" in result
    assert "data" in result and "errorCode" in result
    row_count = connection.execute('SELECT COUNT(*) FROM "quality_fab_a"').fetchone()[0]
    assert row_count == 1

    landings = load_landings(workspace)
    assert len(landings) == 1
    landing = landings[0]
    assert landing["connector_id"] == "demo_quality"
    assert landing["tool_name"] == "get_quality"
    assert landing["land_as"] == "quality_fab_a"
    assert landing["snapshot_sha256"]
    assert landing["input_schema_hash"]
    assert landing["args"] == {"fab": "FAB_A", "week": "2026-W32"}


def test_no_land_as_returns_json_without_landing(tmp_path, connection, connection_lock) -> None:
    workspace = _workspace(tmp_path)
    tools = _tools_by_name((demo_connector(),), connection, connection_lock, workspace)

    result = tools["demo_quality_list_fabs"].invoke({})

    assert "FAB_A" in result
    assert "id" in result and "name" in result
    # 未落表——不該有任何 DuckDB 表被建立,也不留任何 manifest 記錄。
    tables = connection.execute("SHOW TABLES").fetchall()
    assert tables == []
    assert load_landings(workspace) == []


def test_bad_land_as_alias_returns_actionable_error_without_landing(
    tmp_path, connection, connection_lock
) -> None:
    workspace = _workspace(tmp_path)
    tools = _tools_by_name((demo_connector(),), connection, connection_lock, workspace)

    result = tools["demo_quality_get_quality"].invoke(
        {"fab": "FAB_A", "week": "2026-W32", "land_as": "bad-alias"}
    )

    assert "unsafe" in result
    assert "bad-alias" in result
    assert load_landings(workspace) == []
    tables = connection.execute("SHOW TABLES").fetchall()
    assert tables == []


def test_connector_tool_error_passthrough_verbatim(tmp_path, connection, connection_lock) -> None:
    workspace = _workspace(tmp_path)
    tools = _tools_by_name((demo_connector(),), connection, connection_lock, workspace)

    result = tools["demo_quality_get_quality"].invoke({"fab": "NOT_A_FAB", "week": "2026-W32"})

    assert "未知的 fab" in result
    assert "NOT_A_FAB" in result
    assert load_landings(workspace) == []


def test_unexpected_exception_is_wrapped_and_never_raises(
    tmp_path, connection, connection_lock
) -> None:
    workspace = _workspace(tmp_path)

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
    tools = _tools_by_name((connector,), connection, connection_lock, workspace)

    result = tools["flaky_explode"].invoke({})

    assert result == "connector 呼叫失敗：RuntimeError"
    assert "boom" not in result


def test_call_budget_refuses_after_limit_without_invoking_tool(
    tmp_path, connection, connection_lock
) -> None:
    workspace = _workspace(tmp_path)
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
    tools = _tools_by_name((connector,), connection, connection_lock, workspace, call_budget=1)

    tools["counted_ping"].invoke({})
    second_result = tools["counted_ping"].invoke({})

    assert call_count["value"] == 1
    assert "已達上限" in second_result
    assert "1" in second_result


def test_call_budget_shared_across_tools_from_same_build_call(
    tmp_path, connection, connection_lock
) -> None:
    workspace = _workspace(tmp_path)
    tools = _tools_by_name(
        (demo_connector(),), connection, connection_lock, workspace, call_budget=1
    )

    first_result = tools["demo_quality_list_fabs"].invoke({})
    second_result = tools["demo_quality_get_quality"].invoke({"fab": "FAB_A", "week": "2026-W32"})

    assert "FAB_A" in first_result
    assert "已達上限" in second_result


def test_invalid_arg_value_passes_through_to_connector_actionable_error(
    tmp_path, connection, connection_lock
) -> None:
    """dict args_schema 模式下 LangChain 不做型別驗證——不合法的參數值原樣進 connector,
    由 connector/server 端以可行動錯誤拒絕(模型看得到未降級的完整 schema,源頭犯錯率
    本身較低);wrapper 維持 never-raise,錯誤以字串回傳。"""
    workspace = _workspace(tmp_path)
    tools = _tools_by_name((demo_connector(),), connection, connection_lock, workspace)

    result = tools["demo_quality_get_quality"].invoke(
        {"fab": {"nested": "object"}, "week": "2026-W32"}
    )

    assert isinstance(result, str)
    assert "未知的 fab" in result
    assert load_landings(workspace) == []


def test_missing_required_arg_caught_locally_with_field_name(
    tmp_path, connection, connection_lock
) -> None:
    """schema 的 required 欄位缺席時,本層驗證在發請求前攔下,訊息指名缺的欄位——
    NEVER 漏給 server 端炸回 pydantic 原始多行格式。"""
    workspace = _workspace(tmp_path)
    tools = _tools_by_name((demo_connector(),), connection, connection_lock, workspace)

    result = tools["demo_quality_get_quality"].invoke({"fab": "FAB_A"})

    assert isinstance(result, str)
    assert result.startswith("參數驗證失敗——")
    assert "week" in result
    assert load_landings(workspace) == []


def test_record_landing_failure_after_successful_landing_is_non_fatal(
    tmp_path, connection, connection_lock, monkeypatch
) -> None:
    """同上一則測試的理由,但改打斷 record_landing 本身。"""
    workspace = _workspace(tmp_path)
    tools = _tools_by_name((demo_connector(),), connection, connection_lock, workspace)

    def _raise_disk_full(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("app.agent.connectors.wrapper.record_landing", _raise_disk_full)

    result = tools["demo_quality_get_quality"].invoke(
        {"fab": "FAB_A", "week": "2026-W32", "land_as": "quality_fab_a"}
    )

    assert result.startswith("已落表 quality_fab_a：")
    row_count = connection.execute('SELECT COUNT(*) FROM "quality_fab_a"').fetchone()[0]
    assert row_count == 1
    # landings.jsonl 沒有記到(record_landing 本身失敗了),但呼叫端(agent)仍看到成功摘要
    # ——table 存在、摘要誠實反映存在,只有 replay manifest 這筆缺記錄(non-fatal 代價,已記警告)。
    assert load_landings(workspace) == []


def test_reserved_land_as_property_name_raises_at_build_time(
    tmp_path, connection, connection_lock
) -> None:
    """connector tool 若自帶名為 land_as 的頂層參數,不該被包裝層靜默蓋掉;掛載時就
    fail loud,讓撰寫 skill/inputSchema 時立刻發現命名衝突。"""
    workspace = _workspace(tmp_path)
    connector = Connector(
        connector_id="clashing",
        display_name="Clashing",
        tools=(
            ConnectorTool(
                name="conflicting_tool",
                description="declares a land_as property itself",
                input_schema={
                    "type": "object",
                    "properties": {"land_as": {"type": "string"}},
                    "required": [],
                },
                call=lambda args: [{"x": 1}],
            ),
        ),
        skills={"usage": {"SKILL.md": "# clashing\n"}},
    )

    with pytest.raises(ValueError, match="land_as"):
        build_connector_tools((connector,), connection, connection_lock, workspace)


def test_call_budget_thread_safety_smoke(tmp_path, connection, connection_lock) -> None:
    """budget 允許時兩個平行呼叫都照常執行——鎖只保護計數器本身。"""
    workspace = _workspace(tmp_path)
    tools = _tools_by_name(
        (demo_connector(),), connection, connection_lock, workspace, call_budget=10
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
    assert all("FAB_A" in result for result in results)
