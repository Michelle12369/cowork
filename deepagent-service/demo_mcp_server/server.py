"""合成品質資料 MCP server——dev/demo 用（`docker-compose.app.yml` optional profile
`demo-mcp`），讓開發者可以在 UI 上實際玩一輪「選 connector→ lookup → ask_user → data →
落表」的完整純 MCP 路線，而不只是 pytest 內的 in-process fixture。

**不是 production 資產**：`mcp` SDK目前只是 `deepagent-service` 的 dev 依賴（見
pyproject.toml `[dependency-groups] dev`，正常只供 `tests/test_mcp_adapter.py` 起本地
fixture server），本模組同樣只在 dev/demo 情境跑，因此走獨立的 `Dockerfile.demo-mcp`
（另外裝 `mcp`，NOT 進 `deepagent-service/Dockerfile` 的乾淨 runtime image）。

**邏輯全部重用既有測試 fixture**：兩個 tool（`list_fabs`/`get_quality`）與四段式劇本
`skill_markdown` 都原樣借用 `app.agent.connectors.registry.demo_connector()`——本檔只是
把它的 `ConnectorTool.call` 包成真正的 MCP tool（`stateless_http=True`），並把
`skill_markdown` 掛成 MCP resource `skill://usage`，讓 `load_mcp_connector`（純 MCP
adapter）可以像對 internal 真實 MCP server 一樣對它打 `tools/list`/`tools/call`/
`resources/read`。同一份合成資料因此有兩個入口：pytest 走 `registry.demo_connector()`
直組物件（免網路），本地/dev UI 走這個真正的 HTTP MCP server——兩者永遠同步，不會分岔。

啟動與掛目錄見 `docker-compose.app.yml` 的 `demo-mcp` service 註解。
"""

import json

from mcp.server.fastmcp import FastMCP

from app.agent.connectors.registry import demo_connector

_connector = demo_connector()
_tools_by_name = {tool.name: tool for tool in _connector.tools}

mcp_server = FastMCP("demo-quality-mcp", stateless_http=True)

# `structured_output=False`＋回傳已 json.dumps 過的字串：FastMCP 對 list/dict 回傳值的自動
# structured-output 推斷會把裸 list 拆成多個 content block（`load_mcp_connector` 的
# `_extract_tool_payload` 退回 text 解析路徑時會炸 JSONDecodeError——見本模組加這條測試前的
# 除錯過程），改自己序列化成單一字串，交由 adapter 的既有 fallback 解析路徑解回同一份資料。


@mcp_server.tool(name="list_fabs", structured_output=False)
def list_fabs() -> str:
    """列出可查詢的 fab 清單(id/name/region)，無參數；用於取得 get_quality 的 fab 候選。"""
    return json.dumps(_tools_by_name["list_fabs"].call({}))


@mcp_server.tool(name="get_quality", structured_output=False)
def get_quality(fab: str, week: str) -> str:
    """取得指定 fab/week 的品質量測資料(9 列合成資料)，回傳信封 {data, errorCode}。"""
    return json.dumps(_tools_by_name["get_quality"].call({"fab": fab, "week": week}))


@mcp_server.resource("skill://usage")
def skill_usage() -> str:
    return _connector.skill_markdown


app = mcp_server.streamable_http_app()
