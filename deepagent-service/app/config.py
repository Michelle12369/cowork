"""集中設定. 有 one.properties 檔(路徑看 ONE_PROPERTIES_PATH)就當基底層, 再由 env var 覆寫.
優先序是 env 大於 properties 檔大於欄位預設值."""

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

    # SSO header 名稱, 入站(main.py 讀取)與出站(轉送給 MCP server)用同一組.
    # 值一律放在 header 裡傳遞, 不要放進 JSON body.
    SSO_TOKEN_HEADER: str = "X-SSO-Token"
    SSO_URL_HEADER: str = "X-SSO-Url"

    # connector tools 每一輪呼叫次數的上限, 所有 connector tools 共用同一個計數器.
    CONNECTOR_CALL_BUDGET: int = 12

    # 每次 MCP 請求(tools/list, tools/call, skill 列舉與下載)的逾時秒數.
    CONNECTOR_REQUEST_TIMEOUT_SECONDS: float = 30.0

    # 連線層暫時性失敗時, 首次失敗後最多再試幾次. 0 代表不重試.
    CONNECTOR_CALL_RETRIES: int = 1

    # bearerTokenKey 對 service token 的 JSON 對照表, 多個 connector 可共用同一把 key.
    # 空字串代表都不需要認證. 存成 str 是因為 properties 檔不會預先解碼 JSON.
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
