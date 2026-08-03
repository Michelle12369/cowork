"""`/repair` workflow: browser-error-driven single-call HTML fix (deepagent-service counterpart
to Java's AnalysisBrowserRepairClient / ArtifactRepairer analysis-mode path). Not the /chat agent
loop -- this is "照已知錯誤改一份現成 HTML" 的窄任務,一次 system+user 訊息呼叫更快、更確定性。

Returns a `RepairOutcome` instead of an HTTP response -- this layer stays HTTP-agnostic; the
`/repair` endpoint in main.py maps the outcome to status codes and response bodies.
"""

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from app.agent.graph import build_model
from app.agent.prompts import (
    REPAIR_SYSTEM_PROMPT,
    build_repair_retry_user_message,
    build_repair_user_message,
)
from app.api.schemas import RepairRequest
from app.engine.html_extract import extract_html_block
from app.engine.html_guard import check_dashboard_html
from app.engine.results import (
    inject_results,
    load_all_results,
    referenced_query_ids,
    strip_injected_blocks,
)
from app.engine.theme import inject_theme
from app.engine.workspace import prepare_workspace

logger = logging.getLogger(__name__)

# guard 不過時，最多重試次數（首次呼叫之外再一次；總計最多 2 次模型呼叫）。
REPAIR_GUARD_RETRY_MAX_RUNS = 1

# 單次模型呼叫的逾時秒數——沒有 agent 迴圈的逐事件 heartbeat,這是唯一的逾時防線,
# 逾時視同模型呼叫失敗(502)。
REPAIR_MODEL_CALL_TIMEOUT_SECONDS = float(os.environ.get("REPAIR_MODEL_CALL_TIMEOUT_SECONDS", "60"))


@dataclass(frozen=True)
class RepairOutcome:
    """`/repair` 工作流程的結果——HTTP 層不知道的中性結構。三種結果互斥:模型呼叫失敗時
    `model_call_failed=True`;guard 未過時 `guard_errors` 非空;成功時 `html` 有值。"""

    html: str | None
    guard_errors: list[str] = field(default_factory=list)
    model_call_failed: bool = False


async def _invoke_repair_model(model: Any, messages: list[BaseMessage]) -> str:
    response = await asyncio.wait_for(
        model.ainvoke(messages), timeout=REPAIR_MODEL_CALL_TIMEOUT_SECONDS
    )
    content = response.content
    return content if isinstance(content, str) else str(content)


async def run_repair(request: RepairRequest) -> RepairOutcome:
    workspace = prepare_workspace(request.userId, request.sessionId)
    # previousDashboardHtml 的鏡射:Java 端送來的 html 是「注入後」的 artifact rawHtml,剝掉
    # 本服務注入的 __ERD_RESULTS__/主題 script,模型只看乾淨骨架。
    clean_html = strip_injected_blocks(request.html)
    all_results = load_all_results(workspace)
    available_query_ids = set(all_results)

    messages: list[BaseMessage] = [
        SystemMessage(REPAIR_SYSTEM_PROMPT),
        HumanMessage(
            build_repair_user_message(clean_html, [error.message for error in request.errors])
        ),
    ]

    model = build_model()
    try:
        model_response_text = await _invoke_repair_model(model, messages)
    except Exception as model_error:  # noqa: BLE001 -- any model-call failure maps to 502
        logger.warning(
            "repair model call failed sessionId=%s: %s",
            request.sessionId,
            type(model_error).__name__,
        )
        return RepairOutcome(html=None, model_call_failed=True)

    candidate_html = extract_html_block(model_response_text)
    report = check_dashboard_html(candidate_html, available_query_ids, all_results)

    retry_runs = 0
    while not report.ok and retry_runs < REPAIR_GUARD_RETRY_MAX_RUNS:
        retry_runs += 1
        retry_messages: list[BaseMessage] = [
            SystemMessage(REPAIR_SYSTEM_PROMPT),
            HumanMessage(build_repair_retry_user_message(candidate_html, report.errors)),
        ]
        try:
            model_response_text = await _invoke_repair_model(model, retry_messages)
        except Exception as model_error:  # noqa: BLE001 -- any model-call failure maps to 502
            logger.warning(
                "repair model retry call failed sessionId=%s: %s",
                request.sessionId,
                type(model_error).__name__,
            )
            return RepairOutcome(html=None, model_call_failed=True)
        candidate_html = extract_html_block(model_response_text)
        report = check_dashboard_html(candidate_html, available_query_ids, all_results)

    if not report.ok:
        logger.info(
            "repair guard failed sessionId=%s errorCount=%d", request.sessionId, len(report.errors)
        )
        return RepairOutcome(html=None, guard_errors=report.errors)

    referenced_results = {
        query_id: all_results[query_id] for query_id in referenced_query_ids(report.html)
    }
    final_html = inject_theme(inject_results(report.html, referenced_results))
    logger.info("repair passed sessionId=%s", request.sessionId)
    return RepairOutcome(html=final_html)
