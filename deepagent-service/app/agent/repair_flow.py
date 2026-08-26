"""`/repair` workflow: browser-error-driven single-call HTML fix (deepagent-service counterpart
to Java's AnalysisBrowserRepairClient / ArtifactRepairer analysis-mode path). Not the /chat agent
loop -- this is "照已知錯誤改一份現成 HTML" 的窄任務,一次 system+user 訊息呼叫更快、更確定性。

Returns a `RepairOutcome` instead of an HTTP response -- this layer stays HTTP-agnostic; the
`/repair` endpoint in main.py maps the outcome to status codes and response bodies.
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

# 單次模型呼叫的逾時秒數——沒有 agent 迴圈的逐事件 heartbeat,這是唯一的逾時防線,
# 逾時視同模型呼叫失敗(502)。
REPAIR_MODEL_CALL_TIMEOUT_SECONDS = get_settings().REPAIR_MODEL_CALL_TIMEOUT_SECONDS


@dataclass(frozen=True)
class RepairOutcome:
    """`/repair` 工作流程的結果——HTTP 層不知道的中性結構。不驗證候選 HTML,失敗只有一種:
    模型呼叫失敗時 `model_call_failed=True`;否則 `html` 有值。"""

    html: str | None
    model_call_failed: bool = False


async def _invoke_repair_model(model: Any, messages: list[BaseMessage], session_id: str) -> str:
    # 與 /chat 同組 Langfuse handler;run_name=repair 供辨識、session metadata 供分組。
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


async def run_repair(request: RepairRequest) -> RepairOutcome:
    # /repair 本身不解密(無 resolve_source_path),但與 /chat 統一設定身分——decrypt_upload
    # 深處的 require_user_id() 活測試不該因走哪條路徑而有不同前提。
    identity_tokens = set_request_identity(request.userId, request.sessionId)
    store = build_workspace_store()
    workspace = store.prepare(request.userId, request.sessionId)
    try:
        # previousDashboardHtml 的鏡射:Java 端送來的 html 是「注入後」的 artifact rawHtml,
        # 剝掉本服務注入的 __ERD_RESULTS__/主題 script,模型只看乾淨骨架。
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

        # 不驗證候選 HTML——確定性檢查層已移除,只做 theme 改寫＋結果注入這兩件事。
        candidate_html = extract_html_block(model_response_text)
        # 空候選寫入等於清空 dashboard——視同修復失敗。
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
        # run_repair 只 prepare 不 persist(窄任務,不寫回 workspace)——s3 模式的 per-turn
        # scratch 永遠不會被 persist() 清掉,MUST 在這裡自己清,否則每次 /repair 都洩漏一份。
        store.cleanup_scratch()
        reset_request_identity(identity_tokens)
