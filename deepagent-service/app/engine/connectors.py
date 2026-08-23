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
    group: str = "default"  # 載入時由所屬 group 填;舊扁平 config 一律落 "default"


class ConnectorGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    display: str
    description: str = ""
    members: list[ConnectorDefinition] = Field(default_factory=list)


class ConnectorRegistry:
    def __init__(
        self, definitions: list[ConnectorDefinition], groups: list[ConnectorGroup] | None = None
    ) -> None:
        self._by_name = {definition.name: definition for definition in definitions}
        self._groups = groups or []

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

    def groups(self) -> list[ConnectorGroup]:
        return list(self._groups)

    def filter_by_groups(self, selected: list[str]) -> "ConnectorRegistry":
        # 空 selected=使用者未圈範圍=全部可見(現況全域行為);非空才收斂,未知 group 名靜默
        # 忽略(前端傳來的選項可能已過期,不因此炸整個 chat 請求)。
        if not selected:
            return self
        selected_groups = set(selected)
        filtered_definitions = [
            definition
            for definition in self._by_name.values()
            if definition.group in selected_groups
        ]
        filtered_groups = [group for group in self._groups if group.name in selected_groups]
        return ConnectorRegistry(filtered_definitions, filtered_groups)


def _expand_env(raw_value: str, context: str) -> str:
    def _replace(match: re.Match[str]) -> str:
        env_name = match.group(1)
        env_value = os.environ.get(env_name)
        if env_value is None:
            raise ConnectorConfigError(f"connector config {context}: env var {env_name} 未設定")
        return env_value

    return _ENV_PATTERN.sub(_replace, raw_value)


def _parse_group(raw_group: dict) -> ConnectorGroup:
    try:
        group = ConnectorGroup.model_validate(raw_group)
    except ValidationError as validation_error:
        raise ConnectorConfigError(f"connector group 驗證失敗: {validation_error}") from (
            validation_error
        )
    processed_members: list[ConnectorDefinition] = []
    for member in group.members:
        if not SAFE_IDENTIFIER_PATTERN.fullmatch(member.name):
            raise ConnectorConfigError(f"connector 名稱非法識別字: {member.name!r}")
        processed_members.append(
            member.model_copy(
                update={
                    "endpoint": _expand_env(member.endpoint, member.name),
                    "group": group.name,
                }
            )
        )
    return group.model_copy(update={"members": processed_members})


def load_connector_registry(config_path: Path | None) -> ConnectorRegistry:
    if config_path is None or not Path(config_path).is_file():
        return ConnectorRegistry([])
    raw_document = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
    raw_groups_key = raw_document.get("connector_groups")
    if raw_groups_key is not None:
        # 分組 config:每個 group 的 members 即現況的 ConnectorDefinition,多一個 group 歸屬。
        raw_groups = raw_groups_key or []
    else:
        # 舊扁平 config 向後相容:`connectors:` 存在但值為 null(YAML 空值寫法)時 .get 拿到
        # None——`or []` 把 None 一併攤平成空 list,避免下面對 None 迭代拋 TypeError;非空時
        # 包成單一隱式 group "default",維持現況呼叫端(load_connector_registry(None) 等)不變。
        raw_connectors = raw_document.get("connectors") or []
        raw_groups = (
            [{"name": "default", "display": "資料源", "members": raw_connectors}]
            if raw_connectors
            else []
        )
    groups = [_parse_group(raw_group) for raw_group in raw_groups]
    definitions = [member for group in groups for member in group.members]
    names = [definition.name for definition in definitions]
    if len(names) != len(set(names)):
        duplicate_names = sorted({name for name in names if names.count(name) > 1})
        raise ConnectorConfigError(
            f"connector config duplicate names across groups: {duplicate_names}"
        )
    by_name = {definition.name: definition for definition in definitions}
    for definition in definitions:
        for param_name, param in definition.params.items():
            if param.validate_against and param.validate_against.connector not in by_name:
                raise ConnectorConfigError(
                    f"{definition.name}.{param_name} validate_against 引用不存在的 "
                    f"connector: {param.validate_against.connector}"
                )
    return ConnectorRegistry(definitions, groups)
