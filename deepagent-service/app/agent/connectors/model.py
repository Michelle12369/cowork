from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

ConnectorToolErrorKind = Literal["transport", "http", "tool", "no_structured_content", "config"]


class ConnectorToolError(Exception):
    """Tool 呼叫失敗時拋出; kind 標明是哪一層失敗, 讓呼叫端不必解析訊息文字就能行動——
    chat mode 仍只讀 str(error)."""

    def __init__(
        self,
        message: str,
        *,
        kind: ConnectorToolErrorKind = "transport",
        status: int | None = None,
        attempts: int | None = None,
        cause_name: str | None = None,
        detail: str | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.status = status  # kind == "http" 時的 HTTP 狀態碼
        self.attempts = attempts  # _call 放棄前一共嘗試了幾次
        self.cause_name = cause_name  # 底層 cause 的 class 名稱, 不帶其文字內容
        self.detail = detail  # kind == "tool" 時, MCP server 自己的錯誤文字


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
