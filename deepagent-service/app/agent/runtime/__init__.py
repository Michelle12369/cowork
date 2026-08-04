"""以 AGENT_RUNTIME 選擇 agent 建構層實作。internal 實作只存在於公司環境，
找不到時 MUST 啟動即失敗——靜默 fallback 回 deepagents 會讓公司跑在錯誤的 runtime 上而無人察覺。"""

import importlib
import logging
import os
from functools import lru_cache

from app.agent.runtime.base import AgentRuntime

logger = logging.getLogger(__name__)

_RUNTIME_TARGETS = {
    "deepagents": ("app.agent.runtime.deepagents_runtime", "DeepAgentsRuntime"),
    "internal": ("app.agent.runtime.internal_runtime", "InternalRuntime"),
}


@lru_cache(maxsize=1)
def load_runtime() -> AgentRuntime:
    runtimeName = os.environ.get("AGENT_RUNTIME", "deepagents")
    target = _RUNTIME_TARGETS.get(runtimeName)
    if target is None:
        raise RuntimeError(f"AGENT_RUNTIME={runtimeName!r} 無效；可選 {sorted(_RUNTIME_TARGETS)}")
    modulePath, className = target
    try:
        module = importlib.import_module(modulePath)
    except ModuleNotFoundError as error:
        # 缺的若是實作檔本身才是「公司未提供實作」；缺的是它的依賴時原始錯誤更有用，直接放行。
        if error.name != modulePath:
            raise
        raise RuntimeError(
            f"AGENT_RUNTIME={runtimeName} 但找不到 {modulePath}；"
            "公司環境 MUST 提供該實作檔，NEVER fallback 回 deepagents。"
        ) from error
    logger.info("agent runtime selected runtime=%s module=%s", runtimeName, modulePath)
    return getattr(module, className)()
