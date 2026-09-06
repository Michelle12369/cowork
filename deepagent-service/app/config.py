"""這裡是集中設定. 如果 one.properties 存在(路徑看 ONE_PROPERTIES_PATH, 預設是目前目錄下的
one-local.properties)就當作設定的基底層, 再由 env var 逐欄位覆寫; 不存在時只讀 env, 優先序是
env 大於 properties 檔大於欄位預設值. 本機在 deepagent-service/ 目錄啟動會自動吃到 repo 內
gitignored 的 one-local.properties(進版控的 one.properties 只是範本, secrets 留空), internal
環境掛載的是有實際值的版本, 記得要設定 ONE_PROPERTIES_PATH 指向掛載路徑."""

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

_DEFAULT_PROPERTIES_PATH = "one-local.properties"


def _properties_path() -> Path:
    return Path(os.environ.get("ONE_PROPERTIES_PATH", _DEFAULT_PROPERTIES_PATH))


def _parse_properties(properties_file: Path) -> dict[str, str]:
    """解析 Java 風格的 KEY=value 設定檔: 跳過空行與 # 開頭的註解, 用第一個 = 切開後
    再去除頭尾空白. 一行沒有 = 又不是空行就是設定錯誤, 要讓啟動直接失敗, 不要悄悄跳過."""
    parsed: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        properties_file.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise RuntimeError(
                f"one.properties line {line_number} missing '=' separator: {raw_line!r} ({properties_file})"
            )
        key, _, value = line.partition("=")
        parsed[key.strip()] = value.strip()
    return parsed


class PropertiesFileSource(PydanticBaseSettingsSource):
    def __init__(self, settings_cls: type[BaseSettings], properties_file: Path):
        super().__init__(settings_cls)
        self._values = _parse_properties(properties_file)

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
        return self._values.get(field_name), field_name, False

    def __call__(self) -> dict[str, Any]:
        return {
            field_name: self._values[field_name]
            for field_name in self.settings_cls.model_fields
            if field_name in self._values
        }


class Settings(BaseSettings):
    model_config = SettingsConfigDict(case_sensitive=True)

    # 打進 /chat 與 /repair 用的固定 bearer token(Java 端的 ERD_AGENT_ANALYSIS_BEARER_TOKEN
    # 對應同一個值). 空字串會在啟動時直接失敗, 不要悄悄放行沒驗證過的請求.
    AGENT_API_BEARER_TOKEN: str = ""
    AGENT_AUTH_MODE: str = "bearer"
    AGENT_TOKEN_EXCHANGE_URL: str = ""
    AGENT_TOKEN_HEADER: str = ""
    AGENT_TOKEN_TTL: int = 300
    AGENT_SERVICE_ACCOUNT_KEY: str | None = None
    AGENT_SERVICE_ACCOUNT_KEY_FILE: str | None = None
    REPAIR_MODEL_CALL_TIMEOUT_SECONDS: float = 180.0
    AGENT_RUNTIME: str = "deepagents"
    AGENT_RECURSION_LIMIT: int = 80
    AGENT_MAX_TOKENS: int = 32768
    AGENT_REASONING_MAX_TOKENS: int = 8192
    AGENT_PROVIDER_SORT: str = ""
    AGENT_PROVIDER_IGNORE: str = ""
    AGENT_PROVIDER_REQUIRE_PARAMETERS: str = "true"
    AGENT_MODEL: str = "qwen3.6-35b"
    OPENAI_BASE_URL: str | None = None
    OPENAI_API_KEY: str = "unused"
    AGENT_WORKSPACE_ROOT: str = "/data/workspace"
    AGENT_BUILTIN_SKILLS_DIR: str | None = None
    STORAGE_BACKEND: str = "local"
    S3_ENDPOINT: str = ""
    S3_BUCKET: str = "erd-cowork"
    S3_ACCESS_KEY: str = ""
    S3_SECRET_KEY: str = ""
    S3_KEY_PREFIX: str = ""
    LANGFUSE_PUBLIC_KEY: str | None = None
    LANGFUSE_SECRET_KEY: str | None = None
    LANGFUSE_HOST: str | None = None

    # SSO header 的名稱: 入站(main.py 的 /chat, /repair 讀取)與出站(mcp_adapter.py 轉送給
    # MCP server)用同一組名稱. 這兩個名稱固定不變, 做成可設定只是為了不讓 internal 的 header
    # 名稱進版控; 值一律放在 header 裡傳遞, 不要放進 JSON body.
    SSO_TOKEN_HEADER: str = "X-SSO-Token"
    SSO_URL_HEADER: str = "X-SSO-Url"

    # connector tools 每一輪呼叫次數的上限, 所有 connector tools 共用同一個計數器.
    CONNECTOR_CALL_BUDGET: int = 12

    # 這是一份 token key 對 service token 的對照表, 用 JSON 字串存. 這裡的 key 是 catalog 裡
    # 每個 connector entry 自己宣告的 bearerTokenKey(不是 connectorId), 多個 connector 可以
    # 共用同一把 key, 例如共用同一個 gateway token 的情境. 空字串代表所有 connector 都不需要
    # 認證. 型別故意宣告成 str 不是 dict, 因為 PropertiesFileSource 不像 env source 那樣會先
    # 把 JSON 字串解碼, 宣告成 dict 會在走 properties 檔那條路徑時讓 validation 直接失敗;
    # 細節看 connector_bearer_token().
    CONNECTOR_BEARER_TOKENS: str = ""

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        properties_file = _properties_path()
        if properties_file.exists():
            return (
                init_settings,
                env_settings,
                PropertiesFileSource(settings_cls, properties_file),
            )
        return (init_settings, env_settings)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


class SecretResolutionError(Exception):
    """CONNECTOR_BEARER_TOKENS 設定不合法時拋出, 錯誤訊息絕不能包含任何 token 值."""


def connector_bearer_token(token_key: str) -> str | None:
    raw_mapping = get_settings().CONNECTOR_BEARER_TOKENS
    if not raw_mapping:
        return None
    try:
        mapping = json.loads(raw_mapping)
    except json.JSONDecodeError as decode_error:
        raise SecretResolutionError("CONNECTOR_BEARER_TOKENS is not valid JSON") from decode_error
    if not isinstance(mapping, dict):
        raise SecretResolutionError("CONNECTOR_BEARER_TOKENS must be a JSON dict")
    token_value = mapping.get(token_key)
    if token_value is None:
        return None
    if not isinstance(token_value, str):
        raise SecretResolutionError("CONNECTOR_BEARER_TOKENS token value must be a string")
    return token_value or None
