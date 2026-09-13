"""SkillsMiddleware 子類別, 讓 skill 清單每輪重新掃描而不是只掃第一輪. 見同檔案 class docstring."""

from typing import Any

from deepagents.middleware.skills import (
    SkillsMiddleware,
    SkillsState,
    SkillsStateUpdate,
)
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime

_RESCAN_KEYS = ("skills_metadata", "skills_load_errors")


class RescanSkillsMiddleware(SkillsMiddleware):
    """每輪都重新掃描 skill 目錄, 不沿用上一輪快照. 因為呼叫端(`chat_turn.py`)每輪都重新
    stage skills, 目錄內容可能已改變, 而父類別只在第一輪掃描一次."""

    def before_agent(
        self, state: SkillsState, runtime: Runtime, config: RunnableConfig
    ) -> SkillsStateUpdate | None:
        stripped_state = _strip_rescan_keys(state)
        update = super().before_agent(stripped_state, runtime, config)
        return _with_reset_errors(update)

    async def abefore_agent(
        self, state: SkillsState, runtime: Runtime, config: RunnableConfig
    ) -> SkillsStateUpdate | None:
        stripped_state = _strip_rescan_keys(state)
        update = await super().abefore_agent(stripped_state, runtime, config)
        return _with_reset_errors(update)


def _strip_rescan_keys(state: SkillsState) -> SkillsState:
    """複製 state 並拿掉上一輪留下的 skills_metadata / skills_load_errors, 讓父類別的
    early-return 檢查判定為未掃描過, 因而真的重新掃描一次."""
    stripped: dict[str, Any] = dict(state)
    for key in _RESCAN_KEYS:
        stripped.pop(key, None)
    return stripped  # type: ignore[return-value]


def _with_reset_errors(update: SkillsStateUpdate | None) -> SkillsStateUpdate | None:
    """父類別沒回傳 skills_load_errors 時補一份空清單, 避免上一輪的錯誤殘留在 state 裡."""
    if update is None:
        return None
    if "skills_load_errors" not in update:
        update["skills_load_errors"] = []
    return update
