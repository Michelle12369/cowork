"""依 AGENT_RUNTIME 設定選擇要用哪個 agent 建構層實作. internal 版實作只存在於 internal 環境,
找不到就要讓啟動直接失敗, 不要悄悄 fallback 回 deepagents, 否則 internal 端會跑在錯的 runtime 上卻沒人發現."""

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
        # 只有在缺的是實作檔本身時才視為 internal 沒提供實作; 如果缺的是它的依賴, 原始錯誤更有參考價值, 就直接往外拋.
        if error.name != modulePath:
            raise
        raise RuntimeError(
            f"AGENT_RUNTIME={runtimeName} but could not find {modulePath}; "
            "the internal environment MUST provide this implementation file, NEVER fall back to deepagents."
        ) from error
    logger.info("agent runtime selected runtime=%s module=%s", runtimeName, modulePath)
    return getattr(module, className)()
