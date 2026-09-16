"""`scripts/dev_chat.py` 與 `scripts/mcp-shell/bridge.py` 共用的 dev-only 設定讀取。

每個 key 都用同一條規則解析(`resolve()`):

    CLI flag  >  env var  >  properties 檔  >  內建預設
   (有 flag 的 key 才有這層)

properties 檔就是 app.config 讀的那份(路徑看 `ONE_PROPERTIES_PATH`, 預設相對 cwd 的
`one-local.properties`)。唯一的例外是 `ONE_PROPERTIES_PATH` 本身: 它決定讀哪個檔, 所以不可能
從檔裡來, 只有 env > 預設兩層。

兩類 key:
- dev-only 的 `DEV_*` key 不在 `app.config.Settings` 定義中, 服務本身不讀也不理會; 值在這裡
  自己算(空的 env/檔案值視為未設, 同 Settings 的 `env_ignore_empty`)。
- 官方 key(`AGENT_API_BEARER_TOKEN`、`SSO_TOKEN_HEADER`、`SSO_URL_HEADER`)的值一律取自
  `app.config.get_settings()`, 這裡只算「來源」——dev 腳本的工作是預測服務會讀到什麼, 自己再
  解析一次只會多一套可能算錯的邏輯。

同 app.config 一樣 NEVER 解析 dotenv 檔。值 NEVER 印出: `DevSetting.secret` 只決定顯示方式,
從不決定要不要讀。
"""

import ipaddress
import json
import os
import urllib.parse
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from app.api.schemas import ConnectorSpec
from app.config import _parse_properties, _properties_path, get_settings

DEV_DEEPAGENT_URL = "DEV_DEEPAGENT_URL"
DEV_SSO_TOKEN = "DEV_SSO_TOKEN"
DEV_SSO_URL = "DEV_SSO_URL"
DEV_CONNECTORS = "DEV_CONNECTORS"
DEV_KEYS: tuple[str, ...] = (DEV_DEEPAGENT_URL, DEV_SSO_TOKEN, DEV_SSO_URL, DEV_CONNECTORS)

AGENT_API_BEARER_TOKEN = "AGENT_API_BEARER_TOKEN"
SSO_TOKEN_HEADER = "SSO_TOKEN_HEADER"
SSO_URL_HEADER = "SSO_URL_HEADER"
ONE_PROPERTIES_PATH = "ONE_PROPERTIES_PATH"

_DEFAULT_DEEPAGENT_URL = "http://127.0.0.1:8000"
_DEFAULT_DEEPAGENT_PORT = 8000

# 設定值來源標籤(`dev_chat.py --verbose` 印的那欄).
SOURCE_CLI = "cli"
SOURCE_ENV = "env"
SOURCE_PROPERTIES = "properties"
SOURCE_DEFAULT = "default"


@dataclass(frozen=True)
class _KeySpec:
    """`resolve()` 迴圈用的 key 描述: 只驅動迴圈, 不生成 dataclass 也不生成 argparse。
    `official=True` 的 key 值取自 `get_settings()`, `default` 不用(Settings 自己有預設)。"""

    key: str
    attribute: str
    secret: bool
    default: str | None = None
    official: bool = False


# 順序就是 `DevConfig.settings` 的順序, 也就是 --verbose 表的列順序。新增一個 dev key: 這裡一筆,
# 加 `DevConfig` 一個欄位, 其餘(dev_chat 的 flag 與 _cli_overrides)見 dev_chat.py。
_KEY_SPECS: tuple[_KeySpec, ...] = (
    _KeySpec(DEV_DEEPAGENT_URL, "deepagent_url", secret=False, default=_DEFAULT_DEEPAGENT_URL),
    _KeySpec(AGENT_API_BEARER_TOKEN, "bearer_token", secret=True, official=True),
    _KeySpec(DEV_SSO_TOKEN, "sso_token", secret=True),
    _KeySpec(DEV_SSO_URL, "sso_url", secret=True),
    # 值是含 url 的 JSON(query string 可能藏 token): 不算 secret, 但顯示端只印 id, NEVER 印值。
    _KeySpec(DEV_CONNECTORS, "connectors", secret=False),
    _KeySpec(SSO_TOKEN_HEADER, "sso_token_header", secret=False, official=True),
    _KeySpec(SSO_URL_HEADER, "sso_url_header", secret=False, official=True),
)


@dataclass(frozen=True)
class DevSetting:
    """一個 key 解析後的結果: 值與它實際來自哪一層。`secret` 只影響顯示, NEVER 影響讀取。"""

    key: str
    value: str | None
    source: str  # SOURCE_CLI | SOURCE_ENV | SOURCE_PROPERTIES | SOURCE_DEFAULT
    secret: bool


@dataclass(frozen=True)
class DevConfig:
    deepagent_url: str
    sso_token: str | None
    sso_url: str | None
    connectors: list[dict[str, str | None]]
    bearer_token: str | None
    sso_token_header: str
    sso_url_header: str
    settings: tuple[DevSetting, ...]  # 顯示順序, 給 --verbose 用; 第一列是 ONE_PROPERTIES_PATH

    def setting(self, key: str) -> DevSetting:
        for setting in self.settings:
            if setting.key == key:
                return setting
        raise KeyError(key)


def _read_properties() -> dict[str, str]:
    """讀 `ONE_PROPERTIES_PATH` 指到的 properties 檔; 檔不存在就當全空."""
    properties_file = _properties_path()
    return _parse_properties(properties_file) if properties_file.exists() else {}


def _layered_value(
    key: str, cli_overrides: dict[str, str | None], properties: dict[str, str]
) -> tuple[str | None, str]:
    """單一規則: cli > env > properties > default。CLI flag 有給(即使是空字串)就是 cli; env 與
    檔案的空值視為未設(同 Settings 的 `env_ignore_empty` 與 `PropertiesFileSource`)。落到
    default 時值回 None, 由呼叫端補預設。"""
    cli_value = cli_overrides.get(key)
    if cli_value is not None:
        return cli_value, SOURCE_CLI
    if os.environ.get(key):
        return os.environ[key], SOURCE_ENV
    if properties.get(key):
        return properties[key], SOURCE_PROPERTIES
    return None, SOURCE_DEFAULT


def key_source(key: str, cli_value: str | None = None) -> str:
    """`key` 目前的值來源(cli/env/properties/default), 用 `resolve()` 同一條規則。只回報來源,
    NEVER 回傳值。`ONE_PROPERTIES_PATH` 只有 env > default 兩層(它決定讀哪個檔)。"""
    if key == ONE_PROPERTIES_PATH:
        return SOURCE_ENV if os.environ.get(ONE_PROPERTIES_PATH) else SOURCE_DEFAULT
    return _layered_value(key, {key: cli_value}, _read_properties())[1]


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


def resolve(cli_overrides: dict[str, str | None] | None = None) -> DevConfig:
    """依模組 docstring 的單一規則解析每個 key。`cli_overrides` 以 key 名對值, None 表示該 flag
    沒給。`DEV_CONNECTORS` 不合法時拋 ValueError(訊息只點出欄位名, 見 parse_dev_connectors)。"""
    overrides = cli_overrides or {}
    properties = _read_properties()
    settings = get_settings()

    properties_path_setting = DevSetting(
        ONE_PROPERTIES_PATH, str(_properties_path()), key_source(ONE_PROPERTIES_PATH), secret=False
    )
    resolved: list[DevSetting] = [properties_path_setting]
    attributes: dict[str, Any] = {}
    for spec in _KEY_SPECS:
        value, source = _layered_value(spec.key, overrides, properties)
        if spec.official and source != SOURCE_CLI:
            # 官方 key 的值以服務自己的解析為準(空字串同樣視為未設).
            value = getattr(settings, spec.key) or None
        # DevSetting.value 是生效值(落到 default 時就是預設值本身), source 才說它從哪來.
        effective_value = value if value is not None else spec.default
        resolved.append(DevSetting(spec.key, effective_value, source, spec.secret))
        attributes[spec.attribute] = effective_value

    connectors_raw = attributes["connectors"]
    attributes["connectors"] = parse_dev_connectors(connectors_raw) if connectors_raw else []
    # `--base-url ""` 之類的空 CLI 值算 cli 來源, 但位址本身還是要有值可用.
    attributes["deepagent_url"] = attributes["deepagent_url"] or _DEFAULT_DEEPAGENT_URL
    # 官方 header 名有 Settings 預設, 只有 CLI 才可能給 None(目前沒有這種 flag), 保險起見補回.
    attributes["sso_token_header"] = attributes["sso_token_header"] or settings.SSO_TOKEN_HEADER
    attributes["sso_url_header"] = attributes["sso_url_header"] or settings.SSO_URL_HEADER
    return DevConfig(settings=tuple(resolved), **attributes)


def env_shadowed_keys(config: DevConfig) -> list[str]:
    """來源是 env、而 properties 檔也有非空值的 key: env 把檔案的值蓋掉了。dev 期間檔案才是
    權威來源, 這幾乎都是上一個 session 留下的 `export`。檔案沒設的 key 不算——那時 env 是唯一
    來源(容器裡只有 env 的情境), 不是蓋掉誰。`ONE_PROPERTIES_PATH` 永遠不算, 它不可能在檔裡。"""
    properties = _read_properties()
    return [
        setting.key
        for setting in config.settings
        if setting.source == SOURCE_ENV and properties.get(setting.key)
    ]


def env_shadow_warning_lines(config: DevConfig) -> list[str]:
    """`env_shadowed_keys()` 的人話版, 給 dev_chat.py 印、bridge.py 記 log; 沒有就回空 list。
    只提 key 名, NEVER 帶值。"""
    shadowed_keys = env_shadowed_keys(config)
    if not shadowed_keys:
        return []
    properties_file = _properties_path()
    return [
        f"⚠️  {', '.join(shadowed_keys)} 來自 env, 不是 {properties_file}",
        "    dev 期間檔案才是權威來源; 這通常是上一個 session 留下的 export",
    ]


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
