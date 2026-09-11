from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

ConnectorToolErrorKind = Literal["transport", "http", "tool", "no_structured_content", "config"]


class ConnectorToolError(Exception):
    """Tool 呼叫失敗時拋出. 訊息(str(error))給 chat mode 直接回給模型; 其餘屬性給 view-time
    端點分類成錯誤 code, 不必解析訊息文字.

    kind      哪一層失敗:
              transport             連不上／逾時／協定錯誤(重試後仍失敗)
              http                  MCP server 回非 2xx
              tool                  server 回 is_error(tool 自己回報的錯)
              no_structured_content server 回應沒有 structuredContent
              config                bearer key 在部署設定裡查無值
    status    HTTP 狀態碼; 只有 kind == "http" 時有值
    attempts  _call 放棄前一共嘗試了幾次
    detail    該 kind 需要的那一個額外字串, 其他 kind 為 None:
              tool      → server 自己的錯誤文字(原樣)
              config    → 查無值的 bearer key 名
              transport → 底層 cause 的例外 class 名稱
    """

    def __init__(
        self,
        message: str,
        *,
        kind: ConnectorToolErrorKind = "transport",
        status: int | None = None,
        attempts: int | None = None,
        detail: str | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.status = status
        self.attempts = attempts
        self.detail = detail


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
