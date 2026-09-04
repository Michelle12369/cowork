"""demo connector 形狀——純測試 fixture(見 registry.py 模組 docstring),2 tools(list_fabs
無參、get_quality 回信封)、未知 fab 拋可行動錯誤列出可用清單、skills["usage"] 四段式。
production wire 路徑不經本模組,見 tests/test_mcp_adapter.py。"""

import pytest

from app.agent.connectors.model import Connector, ConnectorTool, ConnectorToolError
from app.agent.connectors.registry import demo_connector


def test_demo_connector_shape() -> None:
    connector = demo_connector()
    assert isinstance(connector, Connector)
    assert connector.connector_id == "demo_quality"
    assert connector.display_name
    tool_names = [tool.name for tool in connector.tools]
    assert tool_names == ["list_fabs", "get_quality"]
    assert all(isinstance(tool, ConnectorTool) for tool in connector.tools)


def test_list_fabs_returns_plain_list_no_params() -> None:
    connector = demo_connector()
    list_fabs = next(tool for tool in connector.tools if tool.name == "list_fabs")
    assert list_fabs.input_schema.get("properties") == {}
    assert list_fabs.input_schema.get("required", []) == []
    fabs = list_fabs.call({})
    assert isinstance(fabs, list)
    assert len(fabs) >= 1
    for fab in fabs:
        assert isinstance(fab, dict)
        assert "id" in fab
        assert "name" in fab


def test_get_quality_returns_envelope_filtered_by_fab_week_with_nested_device_column() -> None:
    connector = demo_connector()
    get_quality = next(tool for tool in connector.tools if tool.name == "get_quality")
    fabs = next(tool for tool in connector.tools if tool.name == "list_fabs").call({})
    valid_fab_id = fabs[0]["id"]

    result = get_quality.call({"fab": valid_fab_id, "week": "2026-W32"})

    assert isinstance(result, dict)
    assert result["errorCode"] == ""
    assert isinstance(result["data"], list)
    assert len(result["data"]) == 700
    for row in result["data"]:
        assert isinstance(row, dict)
        assert row["fab"] == valid_fab_id
        assert row["week"] == "2026-W32"
    # 至少一列含淺巢狀欄 device: {"id", "name"}——刻意演練寬鬆落表(read_json_auto 吞 STRUCT)。
    nested_device_rows = [row for row in result["data"] if isinstance(row.get("device"), dict)]
    assert nested_device_rows
    for row in nested_device_rows:
        assert "id" in row["device"]
        assert "name" in row["device"]


def test_get_quality_is_deterministic_across_calls() -> None:
    connector = demo_connector()
    get_quality = next(tool for tool in connector.tools if tool.name == "get_quality")
    fabs = next(tool for tool in connector.tools if tool.name == "list_fabs").call({})
    valid_fab_id = fabs[0]["id"]

    first_call = get_quality.call({"fab": valid_fab_id, "week": "2026-W32"})
    second_call = get_quality.call({"fab": valid_fab_id, "week": "2026-W32"})

    assert first_call == second_call


def test_get_quality_unknown_fab_raises_actionable_connector_tool_error() -> None:
    connector = demo_connector()
    get_quality = next(tool for tool in connector.tools if tool.name == "get_quality")
    fabs = next(tool for tool in connector.tools if tool.name == "list_fabs").call({})
    valid_fab_ids = [fab["id"] for fab in fabs]

    with pytest.raises(ConnectorToolError) as excinfo:
        get_quality.call({"fab": "NO_SUCH_FAB", "week": "2026-W32"})

    message = str(excinfo.value)
    for valid_fab_id in valid_fab_ids:
        assert valid_fab_id in message


def test_get_quality_unknown_week_raises_actionable_error_listing_available_weeks() -> None:
    connector = demo_connector()
    get_quality = next(tool for tool in connector.tools if tool.name == "get_quality")

    with pytest.raises(ConnectorToolError) as excinfo:
        get_quality.call({"fab": "FAB_A", "week": "2026-W99"})

    assert "2026-W32" in str(excinfo.value)


def test_full_dataset_has_at_least_8000_rows_with_fab_week_in_every_row() -> None:
    from app.agent.connectors.registry import _DEMO_QUALITY_ROWS

    assert len(_DEMO_QUALITY_ROWS) >= 8000
    for row in _DEMO_QUALITY_ROWS:
        assert row["fab"] in {"FAB_A", "FAB_B", "FAB_C"}
        assert row["week"].startswith("2026-W")


def test_skill_markdown_follows_four_section_template_and_mentions_land_as() -> None:
    connector = demo_connector()
    assert set(connector.skills) == {"usage"}
    assert set(connector.skills["usage"]) == {"SKILL.md"}
    skill_markdown = connector.skills["usage"]["SKILL.md"]
    assert "tools 清單與語意" in skill_markdown or "工具清單與語意" in skill_markdown
    assert "呼叫順序與相依" in skill_markdown
    assert "參數來源" in skill_markdown
    assert "範例" in skill_markdown
    assert "land_as" in skill_markdown
