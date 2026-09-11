from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

ConnectorToolErrorKind = Literal["transport", "http", "tool", "no_structured_content", "config"]


class ConnectorToolError(Exception):
    """Raised when a connector tool call fails.

    str(error) is the message chat mode hands back to the model. The attributes let the
    view-time endpoint map the failure to an error code without parsing that text.

    kind      which layer failed:
              transport             could not connect, timed out, or protocol error (after retries)
              http                  the MCP server answered with a non-2xx status
              tool                  the server returned is_error (the tool itself reported the error)
              no_structured_content the server response carries no structuredContent
              config                the bearer key is not configured in this deployment
    status    HTTP status code; set only when kind == "http"
    attempts  how many attempts _call made before giving up
    detail    the one extra string a kind needs, None for the others:
              tool      -> the server's own error text, verbatim
              config    -> the name of the bearer key that has no value
              transport -> the class name of the underlying cause exception
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
