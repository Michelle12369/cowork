"""以 AGENT_RUNTIME 選擇 agent 建構層實作。internal 實作只存在於 internal 環境,
找不到時 MUST 啟動即失敗——靜默 fallback 回 deepagents 會讓 internal 端跑在錯誤的 runtime 上而無人察覺。"""

import importlib
import logging
from functools import lru_cache

from app.agent.runtime.base import AgentRuntime
from app.config import get_settings

logger = logging.getLogger(__name__)

_RUNTIME_TARGETS = {
    "deepagents": ("app.agent.runtime.deepagents_runtime", "DeepAgentsRuntime"),
    "internal": ("app.agent.runtime.internal_runtime", "InternalRuntime"),
}


@lru_cache(maxsize=1)
def load_runtime() -> AgentRuntime:
    runtimeName = get_settings().AGENT_RUNTIME
    target = _RUNTIME_TARGETS.get(runtimeName)
    if target is None:
        raise RuntimeError(
            f"AGENT_RUNTIME={runtimeName!r} is invalid; choices are {sorted(_RUNTIME_TARGETS)}"
        )
    modulePath, className = target
    try:
        module = importlib.import_module(modulePath)
    except ModuleNotFoundError as error:
        # 缺的若是實作檔本身才是「internal 未提供實作」;缺的是它的依賴時原始錯誤更有用,直接放行。
        if error.name != modulePath:
            raise
        raise RuntimeError(
            f"AGENT_RUNTIME={runtimeName} but could not find {modulePath}; "
            "the internal environment MUST provide this implementation file, NEVER fall back to deepagents."
        ) from error
    logger.info("agent runtime selected runtime=%s module=%s", runtimeName, modulePath)
    return getattr(module, className)()
