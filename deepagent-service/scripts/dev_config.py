"""`scripts/dev_chat.py` 與 `spike/mcp-shell/bridge.py` 共用的 dev-only 設定讀取。

讀同一份 properties 檔(app.config 讀的那份, 路徑看 `ONE_PROPERTIES_PATH`, 預設相對 cwd 的
`one-local.properties`), 但只認 `DEV_` 開頭的 key——這些 key 不在 `app.config.Settings`
定義中, 服務本身不讀也不理會。這幾個 key 只來自那份 properties 檔, 再落回內建預設——同 app.config
一樣 NEVER 解析 dotenv 檔, 也 NEVER 讀 env var。理由是只有 dev 腳本讀這幾個 key, 刻意少一層,
NEVER 是因為 env 在 production 用不到——compose 的 deepagent-service 整包設定都走 env(沒掛
properties 檔), 測試也是靠 env。`dev_chat.py` 的 CLI flag 疊在 `load_dev_config()` 回傳值之上,
才是唯一的覆寫層。

官方 key(`AGENT_API_BEARER_TOKEN`、`SSO_TOKEN_HEADER`、`SSO_URL_HEADER`)不在這裡讀,
一律透過 `app.config.get_settings()`(env > 檔案 > 預設), 避免兩套解析邏輯各算各的。
"""

import ipaddress
import json
import os
import sys
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# 平常被 dev_chat.py/bridge.py import 時, 呼叫端已經把 service root 加進 sys.path;
# 但 `--shell-exports` 這個入口是直接 `uv run python scripts/dev_config.py` 執行, sys.path[0]
# 是 scripts/ 而不是 cwd, 要自己補上才 import 得到 app(與 env_to_properties.py 同一招,
# 對已經在 sys.path 上的呼叫端是無害的重複 insert)。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import ValidationError

from app.api.schemas import ConnectorSpec
from app.config import _parse_properties, _properties_path

DEV_DEEPAGENT_URL = "DEV_DEEPAGENT_URL"
DEV_SSO_TOKEN = "DEV_SSO_TOKEN"
DEV_SSO_URL = "DEV_SSO_URL"
DEV_CONNECTORS = "DEV_CONNECTORS"
DEV_KEYS: tuple[str, ...] = (DEV_DEEPAGENT_URL, DEV_SSO_TOKEN, DEV_SSO_URL, DEV_CONNECTORS)

_DEFAULT_DEEPAGENT_URL = "http://127.0.0.1:8000"
_DEFAULT_DEEPAGENT_PORT = 8000
_DEFAULT_WORKSPACE_ROOT = "/tmp/erd-spike-workspace"
_AGENT_WORKSPACE_ROOT_KEY = "AGENT_WORKSPACE_ROOT"
_ONE_PROPERTIES_PATH_KEY = "ONE_PROPERTIES_PATH"

# 設定值來源標籤(`dev_chat.py --verbose` 印的那欄). DEV_* key 只會是 properties/default(再由
# 呼叫端疊上 cli); 官方 Settings key 多一層 env.
SOURCE_CLI = "cli"
SOURCE_ENV = "env"
SOURCE_PROPERTIES = "properties"
SOURCE_DEFAULT = "default"


def _read_properties() -> dict[str, str]:
    """讀 `ONE_PROPERTIES_PATH` 指到的 properties 檔; 檔不存在就當全空."""
    properties_file = _properties_path()
    return _parse_properties(properties_file) if properties_file.exists() else {}


def _connector_from_entry(entry_index: int, entry: Any) -> dict[str, str | None]:
    """單筆 DEV_CONNECTORS 項目轉成 ChatRequest.connectors 形狀的 dict; name 缺省時比照
    `dev_chat.parse_connector` 用 id.title() 補上。錯誤訊息只點出欄位名, NEVER 帶原始值
    (url 裡可能藏 token)。"""
    if not isinstance(entry, dict):
        # ValueError(不是 TRY004 建議的 TypeError)是刻意的: DEV_CONNECTORS 內容不合法一律用
        # ValueError, 與下面的 JSON/list 檢查及 pydantic 驗證失敗同一種例外.
        raise ValueError(f"{DEV_CONNECTORS}[{entry_index}] must be a JSON object")  # noqa: TRY004
    entry_with_defaults = dict(entry)
    connector_id = entry_with_defaults.get("id")
    if "name" not in entry_with_defaults and isinstance(connector_id, str) and connector_id:
        entry_with_defaults["name"] = connector_id.title()
    try:
        connector_spec = ConnectorSpec.model_validate(entry_with_defaults)
    except ValidationError as validation_error:
        invalid_fields = ", ".join(
            ".".join(str(location_part) for location_part in error["loc"])
            for error in validation_error.errors()
        )
        # `from None`(不是 `from validation_error`): pydantic 的例外文字內嵌
        # `input_value={...}`, 含 url/bearerTokenKey 等可能藏 token 的原始值; 串上它會讓
        # traceback 印出來, 只帶欄位名清單, NEVER 把 pydantic 例外本身接進因果鏈.
        raise ValueError(
            f"{DEV_CONNECTORS}[{entry_index}] is invalid (fields: {invalid_fields})"
        ) from None
    return connector_spec.model_dump()


def parse_dev_connectors(raw_value: str) -> list[dict[str, str | None]]:
    """`DEV_CONNECTORS` 的單行 JSON list 轉成 ChatRequest.connectors 形狀的 dict list。
    JSON 壞掉、不是 list、或其中一筆不合法都拋 ValueError 指名 `DEV_CONNECTORS`
    (與必要時的項目索引), NEVER 把整串原始值(可能含 token)放進錯誤訊息。"""
    try:
        parsed_value = json.loads(raw_value)
    except json.JSONDecodeError as decode_error:
        raise ValueError(f"{DEV_CONNECTORS} is not valid JSON") from decode_error
    if not isinstance(parsed_value, list):
        raise ValueError(f"{DEV_CONNECTORS} must be a JSON list")  # noqa: TRY004 -- 見上方註解
    return [
        _connector_from_entry(entry_index, entry) for entry_index, entry in enumerate(parsed_value)
    ]


@dataclass(frozen=True)
class DevConfig:
    deepagent_url: str
    sso_token: str | None
    sso_url: str | None
    connectors: list[dict[str, str | None]]


def load_dev_config() -> DevConfig:
    """讀 `one-local.properties`(檔不存在就當全空), 依 DEV_* key 組出 DevConfig; 這幾個 key
    NEVER 讀 env var——呼叫端(CLI flag)自己疊在回傳值上。"""
    properties = _read_properties()

    deepagent_url = properties.get(DEV_DEEPAGENT_URL) or _DEFAULT_DEEPAGENT_URL
    sso_token = properties.get(DEV_SSO_TOKEN) or None
    sso_url = properties.get(DEV_SSO_URL) or None
    connectors_raw = properties.get(DEV_CONNECTORS) or ""
    connectors = parse_dev_connectors(connectors_raw) if connectors_raw else []

    return DevConfig(
        deepagent_url=deepagent_url, sso_token=sso_token, sso_url=sso_url, connectors=connectors
    )


def dev_key_sources() -> dict[str, str]:
    """每個 DEV_* key 目前的值來源: 檔案有非空值就是 `properties`, 否則 `default`。DEV_* NEVER 讀
    env, 所以這裡永遠不會出現 `env`; CLI flag 這一層由呼叫端(`dev_chat.py`)自己判斷疊上去。
    只回報來源, NEVER 回傳值。"""
    properties = _read_properties()
    return {key: SOURCE_PROPERTIES if properties.get(key) else SOURCE_DEFAULT for key in DEV_KEYS}


def official_key_source(key: str) -> str:
    """官方 Settings key 的值來源, 鏡射 `app.config` 的優先序 env > properties 檔 > 欄位預設
    (空字串視為未設, 同 Settings 的 `env_ignore_empty` 與 `PropertiesFileSource` 的非空判斷)。
    只回報來源, NEVER 回傳值。"""
    if os.environ.get(key):
        return SOURCE_ENV
    if _read_properties().get(key):
        return SOURCE_PROPERTIES
    return SOURCE_DEFAULT


def properties_path_source() -> str:
    """`ONE_PROPERTIES_PATH` 本身是官方 env var: 有設就是 `env`, 否則 `default`(cwd 下的
    `one-local.properties`)。"""
    return SOURCE_ENV if os.environ.get(_ONE_PROPERTIES_PATH_KEY) else SOURCE_DEFAULT


def _is_loopback_host(connector_url: str) -> bool:
    """connector URL 的 host 是不是本機(loopback)。`hostname` 會轉小寫、去掉 port, IPv6 也會
    去掉中括號, 所以 IP 字面值直接丟給 `ipaddress` 判斷就好。

    NEVER 為了判斷去做 DNS 查詢: 一個解析到 127.0.0.1 的主機名會被當成遠端, 方向是安全的那邊
    (要求真值, 而不是默默送假值出去)。"""
    host = urllib.parse.urlsplit(connector_url).hostname
    if not host:
        return False
    # RFC 6761: localhost 與其子網域一律解析到本機.
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def connectors_needing_real_sso(connectors: list[dict[str, str | None]]) -> list[str]:
    """host 不在本機的 connector id 清單(順序照傳入順序)。

    SSO 值沒設時 `dev_chat.py`/`bridge.py` 會頂上看得出是假的值——對著本機 mock server 無所謂
    (它不檢查), 但對真的 MCP server 就是把假憑證送出去, 失敗會出現在 MCP 呼叫深處變成一張
    AUTH 卡, 而不是一句「請設這個 key」。呼叫端拿這個清單來決定要不要早退。"""
    return [
        str(connector["id"])
        for connector in connectors
        if not _is_loopback_host(str(connector["url"]))
    ]


def resolve_shell_exports() -> dict[str, str]:
    """`spike/mcp-shell/run-deepagent.sh` 要用 shell 變數餵 uvicorn 的 port 與 workspace 目錄;
    兩者都從同一份 properties 檔算出來, 用跟 `app.config`/`load_dev_config()` 一致的解析器,
    保證跟服務本身讀到的一致。回傳恰好這兩個 key, NEVER 帶檔案裡其他任何 key 或值。

    - `DEEPAGENT_PORT`: 從 `DEV_DEEPAGENT_URL` 解析(檔案缺這個 key, 或 URL 沒帶 port, 都落回
      `_DEFAULT_DEEPAGENT_PORT`)。這不是一個 Settings key, 純粹是給這支腳本自己用的 shell 變數。
    - `AGENT_WORKSPACE_ROOT`: 檔案裡的值(這是官方 Settings key)為準, 檔案沒設才用 spike 專用
      的預設值。呼叫端可以放心把回傳值原樣 export 回去: 檔案有值時這裡回傳的就是那個值, 用同一個
      值蓋自己不算「蓋掉」; 只有檔案沒設時, export 才真的在補一個服務本身不會用的 spike 預設。"""
    config = load_dev_config()
    port = urllib.parse.urlsplit(config.deepagent_url).port or _DEFAULT_DEEPAGENT_PORT

    workspace_root = _read_properties().get(_AGENT_WORKSPACE_ROOT_KEY) or _DEFAULT_WORKSPACE_ROOT

    return {"DEEPAGENT_PORT": str(port), _AGENT_WORKSPACE_ROOT_KEY: workspace_root}


def _print_shell_exports() -> None:
    for key, value in resolve_shell_exports().items():
        print(f"{key}={value}")


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "--shell-exports":
        _print_shell_exports()
    else:
        sys.exit(f"usage: uv run python {sys.argv[0]} --shell-exports")
