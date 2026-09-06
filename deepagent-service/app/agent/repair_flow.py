"""This is the /repair workflow: a browser-error-driven single-call HTML fix, the
deepagent-service counterpart to Java's AnalysisBrowserRepairClient and ArtifactRepairer
analysis-mode path. It is not the /chat agent loop; this is a narrow task that just fixes an
existing HTML file against known errors, so one system+user message call is faster and more
deterministic.

It returns a RepairOutcome instead of an HTTP response, so this layer stays HTTP-agnostic; the
/repair endpoint in main.py maps the outcome to status codes and response bodies.
"""

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
        # /repair 本身不解密, 但還是跟 /chat 統一設定身分, 因為 decrypt_upload 深處的
        # require_user_id() 這個前提不該因為走哪條路徑而不同. 這段放在 try 裡的第一行,
        # 確保上面 prepare() 失敗不會導致 identity 洩漏, 因為 finally 涵蓋不到 try 外面的
        # 賦值. sso_token 和 sso_url 是 main.py 的 /repair handler 從 header 讀出來再傳
        # 進來的 kwargs, 不會走 RepairRequest 的 body 欄位.
        identity_tokens = set_request_identity(
            request.userId, request.sessionId, sso_token, sso_url
        )
        # 這是 previousDashboardHtml 的鏡射: Java 端送來的 html 是已經注入過的 artifact
        # rawHtml, 這裡要把這個服務自己注入的 __ERD_RESULTS__ 和主題 script 剝掉, 讓模型
        # 只看到乾淨的骨架.
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
        logger.info("repair passed sessionId=%s", request.sessionId)
        return RepairOutcome(html=final_html)
    finally:
        # run_repair 只 prepare 不 persist, 因為這是窄任務, 不需要寫回 workspace; s3 模式
        # 下 per-turn 的 scratch 永遠不會被 persist() 清掉, 所以要在這裡自己清乾淨, 不然
        # 每次 /repair 都會洩漏一份.
        store.cleanup_scratch()
        reset_request_identity(identity_tokens)
