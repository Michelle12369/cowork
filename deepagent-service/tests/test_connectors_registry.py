"""demo connector 形狀＋resolve_connectors 解析行為(spec §4/§5)——2 tools(list_fabs 無參、
get_quality 回信封)、未知 fab/connector id 皆拋可行動錯誤列出可用清單、skill_markdown 四段式。"""

import pytest

from app.agent.connectors.model import Connector, ConnectorTool, ConnectorToolError
from app.agent.connectors.registry import demo_connector, resolve_connectors


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


def test_get_quality_returns_envelope_with_nine_rows_and_nested_device_column() -> None:
    connector = demo_connector()
    get_quality = next(tool for tool in connector.tools if tool.name == "get_quality")
    fabs = next(tool for tool in connector.tools if tool.name == "list_fabs").call({})
    valid_fab_id = fabs[0]["id"]

    result = get_quality.call({"fab": valid_fab_id, "week": "2026-W32"})

    assert isinstance(result, dict)
    assert result["errorCode"] == ""
    assert isinstance(result["data"], list)
    assert len(result["data"]) == 9
    for row in result["data"]:
        assert isinstance(row, dict)
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


def test_skill_markdown_follows_four_section_template_and_mentions_land_as() -> None:
    connector = demo_connector()
    skill_markdown = connector.skill_markdown
    assert "tools 清單與語意" in skill_markdown or "工具清單與語意" in skill_markdown
    assert "呼叫順序與相依" in skill_markdown
    assert "參數來源" in skill_markdown
    assert "範例" in skill_markdown
    assert "land_as" in skill_markdown


def test_resolve_connectors_known_id_returns_matching_connector() -> None:
    resolved = resolve_connectors(["demo_quality"])
    assert len(resolved) == 1
    assert resolved[0].connector_id == "demo_quality"


def test_resolve_connectors_empty_selection_returns_empty_tuple() -> None:
    assert resolve_connectors([]) == ()


def test_resolve_connectors_duplicate_ids_dedupe_preserving_order() -> None:
    # 重複 id 只保留一個 Connector——防止掛載端對同一 connector 的 tools 重複命名注入
    # (下游 LangChain tool 撞名)。目錄目前只有 1 個示範 connector,先驗證最小情境。
    resolved = resolve_connectors(["demo_quality", "demo_quality"])
    assert len(resolved) == 1
    assert resolved[0].connector_id == "demo_quality"


def test_resolve_connectors_mixed_duplicate_ids_dedupe_preserving_first_occurrence_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 目錄只有 1 個示範 connector 無法測出「多個相異 id 的順序」,monkeypatch catalog 掛
    # 2 個假 connector,驗證 dedupe 保留每個 id 第一次出現的位置(而非排序或改成 set 的
    # 任意順序)。
    connector_beta = Connector(
        connector_id="beta", display_name="Beta", tools=(), skill_markdown="beta"
    )
    connector_alpha = Connector(
        connector_id="alpha", display_name="Alpha", tools=(), skill_markdown="alpha"
    )
    monkeypatch.setattr(
        "app.agent.connectors.catalog.load_connectors",
        lambda: (connector_beta, connector_alpha),
    )

    resolved = resolve_connectors(["beta", "alpha", "beta", "alpha", "beta"])

    assert [connector.connector_id for connector in resolved] == ["beta", "alpha"]


def test_resolve_connectors_unknown_id_raises_value_error_listing_available_ids() -> None:
    with pytest.raises(ValueError) as excinfo:
        resolve_connectors(["no_such_connector"])
    message = str(excinfo.value)
    assert "no_such_connector" in message
    assert "demo_quality" in message
