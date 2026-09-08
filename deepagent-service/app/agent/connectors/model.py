from collections.abc import Callable
from dataclasses import dataclass


class ConnectorToolError(Exception):
    """Tool 呼叫失敗時拋出, 訊息內容要讓呼叫端知道下一步能做什麼."""


@dataclass(frozen=True)
class ConnectorTool:
    name: str
    description: str
    input_schema: dict  # 這是一份 JSON Schema
    call: Callable[[dict], object]


@dataclass(frozen=True)
class Connector:
    connector_id: str
    display_name: str
    tools: tuple[ConnectorTool, ...]
    skills: dict[str, dict[str, str]]
