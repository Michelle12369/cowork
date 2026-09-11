"""/repair workflow: a single-call HTML fix against known browser errors, not the /chat agent loop.
Returns a RepairOutcome instead of an HTTP response; main.py maps it to status codes."""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from app.agent.chat_turn import _build_callbacks
from app.agent.graph import build_model
from app.agent.prompts import REPAIR_SYSTEM_PROMPT, build_repair_user_message
from app.api.schemas import RepairRequest
from app.config import get_settings
from app.engine.html_extract import extract_html_block
from app.engine.request_context import reset_request_identity, set_request_identity
from app.engine.results import (
    has_mcp_runtime,
    inject_mcp_runtime,
    inject_results,
    load_all_results,
    referenced_query_ids,
    strip_injected_blocks,
)
from app.engine.theme_rewrite import apply_erd_theme
from app.engine.workspace_store import build_workspace_store

logger = logging.getLogger(__name__)

# 這是單次模型呼叫的逾時秒數. 這裡沒有 agent 迴圈那種逐事件的 heartbeat, 所以這是唯一的
# 逾時防線, 逾時就視同模型呼叫失敗, 回應 502.
REPAIR_MODEL_CALL_TIMEOUT_SECONDS = get_settings().REPAIR_MODEL_CALL_TIMEOUT_SECONDS


@dataclass(frozen=True)
class RepairOutcome:
    """這是 /repair 工作流程的結果, 是 HTTP 層不需要知道細節的中性結構. 這裡不驗證候選
    HTML, 失敗只有一種情況: 模型呼叫失敗時 model_call_failed 是 True, 其他情況 html 都
    會有值."""

    html: str | None
    model_call_failed: bool = False


async def _invoke_repair_model(model: Any, messages: list[BaseMessage], session_id: str) -> str:
    # 這裡用跟 /chat 同一組 Langfuse handler; run_name=repair 方便辨識, session metadata
    # 方便分組.
    invoke_config = {
        "callbacks": _build_callbacks(),
        "run_name": "repair",
        "metadata": {"langfuse_session_id": session_id},
    }
    response = await asyncio.wait_for(
        model.ainvoke(messages, config=invoke_config),
        timeout=REPAIR_MODEL_CALL_TIMEOUT_SECONDS,
    )
    content = response.content
    return content if isinstance(content, str) else str(content)


async def run_repair(
    request: RepairRequest,
    *,
    sso_token: str | None = None,
    sso_url: str | None = None,
) -> RepairOutcome:
    store = build_workspace_store()
    workspace = store.prepare(request.userId, request.sessionId)
    try:
        # 跟 /chat 統一設定身分. 放在 try 的第一行: prepare() 失敗時 identity 還沒設, finally 就沒東西要清.
        # sso_token 和 sso_url 由 main.py 的 handler 從 header 讀出來傳入, 不走 body 欄位.
        identity_tokens = set_request_identity(
            request.userId, request.sessionId, sso_token, sso_url
        )
        # 傳進來的 html 已經注入過 __ERD_RESULTS__ 和主題 script, 這裡剝掉讓模型只看到乾淨骨架.
        # connector 模式的 mcp() prelude 也在剝除範圍內, 有沒有帶過先記住, 修復完再補回去.
        had_mcp_runtime = has_mcp_runtime(request.html)
        clean_html = strip_injected_blocks(request.html)
        all_results = load_all_results(workspace)

        messages: list[BaseMessage] = [
            SystemMessage(REPAIR_SYSTEM_PROMPT),
            HumanMessage(
                build_repair_user_message(clean_html, [error.message for error in request.errors])
            ),
        ]

        model = build_model()
        try:
            model_response_text = await _invoke_repair_model(model, messages, request.sessionId)
        except Exception as model_error:  # noqa: BLE001 -- any model-call failure maps to 502
            logger.warning(
                "repair model call failed sessionId=%s: %s",
                request.sessionId,
                type(model_error).__name__,
            )
            return RepairOutcome(html=None, model_call_failed=True)

        # 這裡不驗證候選 HTML, 因為確定性檢查層已經移除, 只做 theme 改寫和結果注入這兩件事.
        candidate_html = extract_html_block(model_response_text)
        # 空的候選內容如果寫入就等於清空 dashboard, 這裡視同修復失敗.
        if not candidate_html.strip():
            logger.warning("repair model returned empty html sessionId=%s", request.sessionId)
            return RepairOutcome(html=None, model_call_failed=True)
        themed_html = apply_erd_theme(candidate_html)
        referenced_results = {
            query_id: all_results[query_id]
            for query_id in referenced_query_ids(themed_html)
            if query_id in all_results
        }
        final_html = inject_results(themed_html, referenced_results)
        if had_mcp_runtime:
            final_html = inject_mcp_runtime(final_html)
        logger.info("repair passed sessionId=%s", request.sessionId)
        return RepairOutcome(html=final_html)
    finally:
        # run_repair 只 prepare 不 persist, 要自己清 scratch.
        # s3 模式下 scratch 不會被 persist() 清掉, 不清就會每次 /repair 都留下暫存.
        store.cleanup_scratch()
        reset_request_identity(identity_tokens)
