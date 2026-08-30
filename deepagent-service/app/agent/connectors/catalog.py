"""Connector 目錄。**internal 環境整檔複寫此檔**(列於 scripts/internal-owned-paths.txt)提供
真正的 connectors——MCP 版(接上 internal 自寫自養的 MCP server)或 in-code 過渡版(直接在 code
裡把真 API 註冊成 tools，見 spec §5 雙實作)，介面(本檔的 `load_connectors`)維持不變。

**`load_connectors()` 本身 MUST 為靜態設定讀取(id／display_name／base_url 等中繼資料)，
NEVER 打 MCP server**——`GET /connectors`(`app/main.py`)在請求脈絡外也會呼叫這條路徑
列目錄(僅供使用者選取用，不需要也不該連線)，而 `mcp_adapter.load_mcp_connector` 內部
`tools/list`／`resources/read` 每次呼叫都經 `require_sso_token()` 現取 token，缺身分時
fail loud(`LookupError`，見 mcp_adapter.py 模組 docstring)。若 `load_connectors()` 直接
呼叫 `load_mcp_connector` 去打真正的 MCP server 列舉 tools，`/connectors` 這條路徑會因為
沒有 `set_request_identity` 而炸成未接住的 500。

**正確用法**：`load_connectors()` 只回傳靜態目錄項(id／display_name／base_url)供列舉／選
擇 UI 使用；`mcp_adapter.load_mcp_connector` 只能在**已 `set_request_identity` 的請求脈絡
內**呼叫——即 `resolve_connectors`(依 session 鎖定的 connector id 子集實際掛載 tools)那條
路徑，發生在真正的 `/chat` turn 裡，而非目錄列舉時。**Phase 2 可選的結構性防呆**：把
`load_connectors()` 回傳型別改成不含已掛載 tools 的輕量中繼資料(例如另開一個
`ConnectorMetadata` record)，`load_mcp_connector` 的呼叫延後到 `resolve_connectors` 內部
才發生——這樣型別本身就防止目錄列舉誤用連線版本，不必只靠文件約束；本次 Phase 1 不動
`Connector`/`load_connectors` 的既有形狀，先以文件約束訂正。

repo 版＝dev/CI 用的示範目錄，只掛 `registry.demo_connector()`(合成資料、無網路呼叫)，供
「選 connector→lookup→ask_user→data→落表→recipe」整條管線在沒有真 MCP server 時也能開發與
測試。目錄內容、憑證、真實連線位址由 internal 版自理。
"""

from app.agent.connectors.model import Connector
from app.agent.connectors.registry import demo_connector


def load_connectors() -> tuple[Connector, ...]:
    return (demo_connector(),)
