"""Connector 目錄。**internal 環境整檔複寫此檔**(列於 scripts/internal-owned-paths.txt)提供
真正的 connectors(MCP 版或 in-code 過渡版)，介面(本檔的 `load_connectors`)維持不變。

**`load_connectors()` MUST 為靜態設定讀取(id／display_name／base_url)，NEVER 打 MCP
server**——`GET /connectors`(`app/main.py`)在請求脈絡外也會呼叫這條路徑列目錄；
`mcp_adapter.load_mcp_connector` 只能在已 `set_request_identity` 的請求脈絡內呼叫(即
`resolve_connectors`，發生在 `/chat` turn 裡)，否則 `require_sso_token()` 會 fail loud
把目錄列舉端點炸成未接住的例外。

repo 版為 dev/CI 示範目錄，只掛 `registry.demo_connector()`(合成資料、無網路呼叫)，供
「選 connector→lookup→ask_user→data→落表→replay manifest」整條管線在沒有真 MCP server 時也能開發與
測試。目錄內容、憑證、真實連線位址由 internal 版自理。
"""

from app.agent.connectors.model import Connector
from app.agent.connectors.registry import demo_connector


def load_connectors() -> tuple[Connector, ...]:
    return (demo_connector(),)
