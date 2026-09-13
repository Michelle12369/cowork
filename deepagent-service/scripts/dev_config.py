"""`scripts/dev_chat.py` 與 `spike/mcp-shell/bridge.py` 共用的 dev-only 設定讀取。

讀同一份 `one-local.properties`(app.config 讀的那份, 路徑看 `ONE_PROPERTIES_PATH`, 預設相對
cwd 的 `one-local.properties`), 但只認 `DEV_` 開頭的 key——這些 key 不在 `app.config.Settings`
定義中, 服務本身不讀也不理會。優先序: env var(非空) > 本檔(非空) > 預設值, 與 app.config 的
`env > properties 檔 > 欄位預設`同一套規則。

官方 key(`AGENT_API_BEARER_TOKEN`、`SSO_TOKEN_HEADER`、`SSO_URL_HEADER`)不在這裡讀,
一律透過 `app.config.get_settings()`, 避免兩套解析邏輯各算各的。
"""

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from app.api.schemas import ConnectorSpec
from app.config import _parse_properties, _properties_path

DEV_DEEPAGENT_URL = "DEV_DEEPAGENT_URL"
DEV_SSO_TOKEN = "DEV_SSO_TOKEN"
DEV_SSO_URL = "DEV_SSO_URL"
DEV_CONNECTORS = "DEV_CONNECTORS"
DEV_KEYS: tuple[str, ...] = (DEV_DEEPAGENT_URL, DEV_SSO_TOKEN, DEV_SSO_URL, DEV_CONNECTORS)

_DEFAULT_DEEPAGENT_URL = "http://127.0.0.1:8000"


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


def _resolve_value(
    key: str, environment: Mapping[str, str], properties: Mapping[str, str], default: str
) -> str:
    """單一 key 的優先序: env(非空) > properties 檔(非空) > default。"""
    env_value = environment.get(key)
    if env_value:
        return env_value
    file_value = properties.get(key)
    if file_value:
        return file_value
    return default


def load_dev_config(environment: Mapping[str, str] = os.environ) -> DevConfig:
    """讀 `one-local.properties`(檔不存在就當全空)與 `environment`, 依 DEV_* key 組出 DevConfig。"""
    properties_file = _properties_path()
    properties = _parse_properties(properties_file) if properties_file.exists() else {}

    deepagent_url = _resolve_value(
        DEV_DEEPAGENT_URL, environment, properties, _DEFAULT_DEEPAGENT_URL
    )
    sso_token = _resolve_value(DEV_SSO_TOKEN, environment, properties, "") or None
    sso_url = _resolve_value(DEV_SSO_URL, environment, properties, "") or None
    connectors_raw = environment.get(DEV_CONNECTORS) or properties.get(DEV_CONNECTORS) or ""
    connectors = parse_dev_connectors(connectors_raw) if connectors_raw else []

    return DevConfig(
        deepagent_url=deepagent_url, sso_token=sso_token, sso_url=sso_url, connectors=connectors
    )
