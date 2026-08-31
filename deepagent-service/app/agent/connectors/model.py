"""Connector 供應層抽象——一個 connector 供應一組 tools(name／inputSchema／可呼叫體)加一組
劇本 skills(名稱 → markdown，可多份)。兩個實作(in-code 模擬版／MCP adapter)都產出這一組
型別，chat_turn 等呼叫端只認這份抽象，不知道背後是哪個實作。

stdlib-only：這份抽象要能被 registry.py(in-code 模擬版)與 MCP adapter 兩側共用，不綁死
任何 LLM 框架型別。
"""

from collections.abc import Callable
from dataclasses import dataclass


class ConnectorToolError(Exception):
    """Tool 呼叫失敗的可行動錯誤——缺參指名、值不合法/過期給候選。訊息直接進 agent
    context 供模型轉告使用者，NEVER 含 SSO token 或其他敏感值。"""


@dataclass(frozen=True)
class ConnectorTool:
    name: str  # 原名(未加前綴)；命名空間前綴由包裝層在掛載時加
    description: str
    input_schema: dict  # JSON Schema；現僅支援頂層 scalar properties
    call: Callable[[dict], object]  # args -> 已解析 JSON；錯誤拋 ConnectorToolError(message)


@dataclass(frozen=True)
class Connector:
    connector_id: str  # safe identifier(^\\w+$)
    display_name: str
    tools: tuple[ConnectorTool, ...]
    skills: dict[str, str]  # skill 名稱 → 劇本 markdown；一個 connector 可供多份劇本
