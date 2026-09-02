from collections.abc import Callable
from dataclasses import dataclass


class ConnectorToolError(Exception):
    """Tool 呼叫失敗的可行動錯誤"""


@dataclass(frozen=True)
class ConnectorTool:
    name: str
    description: str
    input_schema: dict  # JSON Schema
    call: Callable[[dict], object]


@dataclass(frozen=True)
class Connector:
    connector_id: str
    display_name: str
    tools: tuple[ConnectorTool, ...]
    skills: dict[
        str, dict[str, str]
    ]
