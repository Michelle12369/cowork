"""Connector 定義的單一事實來源——YAML 載入+Pydantic 驗證+env 展開。config 缺席=空 registry
(功能整體關閉);validate_against 引用、重複名、缺 env var 一律啟動即失敗,NEVER 靜默。"""

import os
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

_ENV_PATTERN = re.compile(r"\$\{(\w+)\}")

# alias/識別字安全樣式,與 duck.py 的掛載驗證同一標準。
SAFE_IDENTIFIER_PATTERN = re.compile(r"^\w+$", re.UNICODE)

# 一輪對話內 fetch_api_data 的呼叫次數上限,避免模型迴圈式重抓耗盡 API 配額/時間。放這裡
# (而非 tools/data.py)是單一事實來源給 prompts.py 引用——prompts.py 是純文字模組,不能
# import tools/data.py(拖進 duckdb/langchain_core 等重依賴)。
MAX_FETCHES_PER_TURN = 6


class ConnectorConfigError(RuntimeError):
    pass


class ValidateAgainst(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connector: str
    column: str


class ConnectorParam(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["str", "int", "float", "date"]
    required: bool = False
    description: str = ""
    validate_against: ValidateAgainst | None = None


class ConnectorLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timeout_s: int = 30
    max_bytes: int = 50_000_000
    max_rows: int = 500_000


class ConnectorDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    kind: Literal["lookup", "data"]
    description: str
    endpoint: str
    method: Literal["GET", "POST"] = "GET"
    auth: str = ""  # "" 無認證;"bearer:ENV_NAME" 一期唯一模式,字串格式留擴充(user-token 等)
    params: dict[str, ConnectorParam] = Field(default_factory=dict)
    limits: ConnectorLimits = Field(default_factory=ConnectorLimits)


class ConnectorRegistry:
    def __init__(self, definitions: list[ConnectorDefinition]) -> None:
        self._by_name = {definition.name: definition for definition in definitions}

    def is_empty(self) -> bool:
        return not self._by_name

    def get(self, name: str) -> ConnectorDefinition | None:
        return self._by_name.get(name)

    def all(self) -> list[ConnectorDefinition]:
        return list(self._by_name.values())

    def data_connectors(self) -> list[ConnectorDefinition]:
        return [d for d in self._by_name.values() if d.kind == "data"]

    def lookup_connectors(self) -> list[ConnectorDefinition]:
        return [d for d in self._by_name.values() if d.kind == "lookup"]


def _expand_env(raw_value: str, context: str) -> str:
    def _replace(match: re.Match[str]) -> str:
        env_name = match.group(1)
        env_value = os.environ.get(env_name)
        if env_value is None:
            raise ConnectorConfigError(f"connector config {context}: env var {env_name} 未設定")
        return env_value

    return _ENV_PATTERN.sub(_replace, raw_value)


def load_connector_registry(config_path: Path | None) -> ConnectorRegistry:
    if config_path is None or not Path(config_path).is_file():
        return ConnectorRegistry([])
    raw_document = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
    raw_connectors = raw_document.get("connectors", [])
    definitions: list[ConnectorDefinition] = []
    for raw_connector in raw_connectors:
        try:
            definition = ConnectorDefinition.model_validate(raw_connector)
        except ValidationError as validation_error:
            raise ConnectorConfigError(f"connector config 驗證失敗: {validation_error}") from (
                validation_error
            )
        if not SAFE_IDENTIFIER_PATTERN.fullmatch(definition.name):
            raise ConnectorConfigError(f"connector 名稱非法識別字: {definition.name!r}")
        definition = definition.model_copy(
            update={"endpoint": _expand_env(definition.endpoint, definition.name)}
        )
        definitions.append(definition)
    names = [definition.name for definition in definitions]
    if len(names) != len(set(names)):
        raise ConnectorConfigError(f"connector config duplicate names: {names}")
    by_name = {definition.name: definition for definition in definitions}
    for definition in definitions:
        for param_name, param in definition.params.items():
            if param.validate_against and param.validate_against.connector not in by_name:
                raise ConnectorConfigError(
                    f"{definition.name}.{param_name} validate_against 引用不存在的 "
                    f"connector: {param.validate_against.connector}"
                )
    return ConnectorRegistry(definitions)
