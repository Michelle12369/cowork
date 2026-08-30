"""Connector 目錄。**internal 環境整檔複寫此檔**(列於 scripts/internal-owned-paths.txt)提供
真正的 connectors——MCP 版(接上 internal 自寫自養的 MCP server，逐一呼叫
`mcp_adapter.load_mcp_connector(connector_id, display_name, base_url)` 組出 `Connector`；
每個 base_url 對應目錄裡一筆設定)或 in-code 過渡版(直接在 code 裡把真 API 註冊成 tools，見
spec §5 雙實作)，介面(本檔的 `load_connectors`)維持不變。

repo 版＝dev/CI 用的示範目錄，只掛 `registry.demo_connector()`(合成資料、無網路呼叫)，供
「選 connector→lookup→ask_user→data→落表→recipe」整條管線在沒有真 MCP server 時也能開發與
測試。目錄內容、憑證、真實連線位址由 internal 版自理。
"""

from app.agent.connectors.model import Connector
from app.agent.connectors.registry import demo_connector


def load_connectors() -> tuple[Connector, ...]:
    return (demo_connector(),)
