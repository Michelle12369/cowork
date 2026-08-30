"""Connector 供應層抽象(spec §5)——一個 connector 供應一組 tools(name／inputSchema／可呼叫體)
加一份劇本 skill_markdown。兩個實作(in-code 模擬版／MCP adapter)都產出這一組型別，chat_turn
等呼叫端只認這份抽象，不知道背後是哪個實作——平移到 MCP 版時這裡不變。

stdlib-only：這份抽象要能被 registry.py(in-code 模擬版)與未來的 MCP adapter 兩側共用，
不綁死任何 LLM 框架型別。
"""

from collections.abc import Callable
from dataclasses import dataclass


class ConnectorToolError(Exception):
    """Tool 呼叫失敗的可行動錯誤(spec §4-5)——缺參指名、值不合法/過期給候選。
    訊息直接進 agent context 供模型轉告使用者，NEVER 含 SSO token 或其他敏感值。"""


@dataclass(frozen=True)
class ConnectorTool:
    name: str  # 原名(未加前綴)；connector id 命名空間前綴由包裝層在掛載時加(spec §5)
    description: str
    input_schema: dict  # JSON Schema；Phase 1 僅頂層 scalar properties
    call: Callable[[dict], object]  # args -> 已解析 JSON；錯誤拋 ConnectorToolError(message)


@dataclass(frozen=True)
class Connector:
    connector_id: str  # safe identifier(^\\w+$)
    display_name: str
    tools: tuple[ConnectorTool, ...]
    skill_markdown: str  # 四段式劇本(tools 清單與語意／呼叫順序與相依／參數來源／範例)
