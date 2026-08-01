"""`/chat` SSE 端點 -- deepagents 版 wire orchestration，與 Java `LangGraphAnalysisProvider`
對接：workspace 準備 → duckdb 掛資料鎖門 → agent 組裝 → astream_events 消費（經 EventBridge
轉譯成 wire 事件）→ 首輪空回應重試 → dashboard.html 偵測與 guard 修復迴路 → 結果/主題注入 →
ANSWER。

`app.main` 這一層允許 import LLM 框架（deepagents/langchain/langgraph/langfuse），是唯一知道
LLM 存在的邊界之一（見 pyproject.toml 的 ruff TID251 per-file-ignores）。
"""

import asyncio
import logging
import os
import re
from collections.abc import AsyncIterable
from typing import Annotated, Any

import httpx
from fastapi import Body, FastAPI
from fastapi.responses import JSONResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.errors import GraphRecursionError
from pydantic import BaseModel

from app.agent import session_state
from app.agent.events import EventBridge, pump_agent_events
from app.agent.graph import build_agent, build_model
from app.agent.tools.recording import ToolResultRecorder
from app.engine.duck import Source, open_locked_connection
from app.engine.html_guard import check_dashboard_html
from app.engine.results import (
    inject_results,
    load_all_results,
    referenced_query_ids,
    strip_injected_blocks,
)
from app.engine.theme import inject_theme
from app.engine.workspace import (
    builtin_skills_dir,
    stage_skills,
    write_sources_doc,
)
from app.engine.workspace_factory import build_workspace_store

logger = logging.getLogger(__name__)

app = FastAPI(title="deepagent-service")

# Re-emit the in-progress step's RUNNING STEP every N seconds so the stream never goes silent.
# MUST stay well under Java's per-event inactivity timeout.
HEARTBEAT_INTERVAL_SECONDS = 15.0

# astream_events(..., config=run_config) falls back to langchain_core's default recursion limit
# (25) unless set explicitly here -- create_deep_agent's own binding isn't threaded through.
# 80 對齊 docker-compose 預設,留夠一輪標準 dashboard 任務的工具呼叫量。
AGENT_RECURSION_LIMIT = int(os.environ.get("AGENT_RECURSION_LIMIT", "80"))

# Surfaces GraphRecursionError as an actionable Traditional-Chinese message instead of
# LangGraph's raw English text leaking into the persisted chat reply.
GRAPH_RECURSION_ERROR_MESSAGE = "分析步驟過多而中止,請把需求拆小一點再試一次"

# dashboard.html 未過 check_dashboard_html 時，最多重試修復的輪數。
GUARD_REPAIR_MAX_RUNS = 2

# 本輪已完成分析步驟但沒有文字說明時的兜底文案；只在本輪未發出過 DASHBOARD_HTML 時使用
# ——見 DASHBOARD_UPDATED_FALLBACK_MESSAGE。
EMPTY_ANSWER_FALLBACK_MESSAGE = "本輪已完成分析步驟,但未產生文字說明——請再問一次或換個說法。"

# 本輪已成功發出 DASHBOARD_HTML(儀表板確實更新了)、但模型最終文字仍為空時的兜底文案 --
# 比 EMPTY_ANSWER_FALLBACK_MESSAGE 更準確:工作其實成功了,不該說「請再問一次」誤導使用者。
DASHBOARD_UPDATED_FALLBACK_MESSAGE = "儀表板已依你的需求更新,請查看右側預覽。"

# guard 終敗(修復輪跑完仍不過)時的 ANSWER 前綴——模型最終文字可能仍在講「已完成」,
# 這是假成功,用前綴戳破。獨立分支,不進下面 final_answer_text 空/非空的一般 fallback。
DASHBOARD_REJECTED_PREFIX = "⚠️ 本輪產生的儀表板未通過品質檢查,已退回不顯示。"

# pump 回報連線類例外(判定見 _is_transient_stream_error)時，同一輪最多自動重試的次數。
STREAM_RETRY_MAX_RUNS = 1


def _is_transient_stream_error(error: BaseException) -> bool:
    """判定例外是否屬傳輸層失敗（斷線、逾時），值得整輪自動重試，而非 model/graph 邏輯錯誤。

    命中 `httpx.HTTPError`/`ConnectionError`，或類名/訊息（不分大小寫）含
    "connection"、"network"、"timed out" 之一即算——涵蓋訊息為空但類名透露連線性質的情況。
    """
    if isinstance(error, (httpx.HTTPError, ConnectionError)):
        return True
    haystack = f"{type(error).__name__} {error}".lower()
    return any(keyword in haystack for keyword in ("connection", "network", "timed out"))


# 首輪空回應（無文字、也沒有任何工具啟動）最多重新 invoke 的次數。
FIRST_ROUND_RETRY_MAX_RUNS = 2

# `previousDashboardHtml` 有值時，附加在本輪使用者訊息後，告知模型 dashboard.html 已是
# 使用者選定的歷史版本、本輪修改應以其為準。只影響本輪 run_input，不回頭改寫既有 checkpoint。
PREVIOUS_VERSION_SYSTEM_NOTE = (
    "\n\n(System note: the user has selected a historical dashboard version as the editing "
    "base for this turn. dashboard.html already contains that version's content -- please "
    "use it as the basis for your changes.)"
)


class HistoryItem(BaseModel):
    role: str
    text: str


class SourceItem(BaseModel):
    alias: str
    path: str
    fileType: str


class ChatRequest(BaseModel):
    sessionId: str
    userId: str
    message: str
    history: list[HistoryItem] = []
    sources: list[SourceItem] = []
    # 使用者選定歷史版本繼續編輯時帶上該版「注入後」rawHtml；沒選就沒有這個 key。
    # 基底重建見 `chat()` 內 mtime 快照之前那段。
    previousDashboardHtml: str | None = None


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _build_callbacks() -> list[Any]:
    """Langfuse tracing：未設 LANGFUSE_PUBLIC_KEY 即 no-op，不建 handler。"""
    if not os.environ.get("LANGFUSE_PUBLIC_KEY"):
        return []
    from langfuse.langchain import CallbackHandler

    return [CallbackHandler()]


def _seed_messages(request: ChatRequest) -> list[BaseMessage]:
    """checkpoint 已存在的 thread 只帶本次訊息（避免重複灌入歷史）；否則從 request.history 重建
    後 append 本次 message。`previousDashboardHtml` 有值時只在本輪 HumanMessage 附加
    `PREVIOUS_VERSION_SYSTEM_NOTE`，既往歷史訊息不受影響。
    """
    current_turn_message = request.message
    if request.previousDashboardHtml is not None:
        current_turn_message = f"{request.message}{PREVIOUS_VERSION_SYSTEM_NOTE}"

    if session_state.has_checkpoint(request.sessionId):
        return [HumanMessage(current_turn_message)]
    messages: list[BaseMessage] = [
        AIMessage(item.text) if item.role == "AI" else HumanMessage(item.text)
        for item in request.history
    ]
    messages.append(HumanMessage(current_turn_message))
    return messages


async def _stream_agent_turn(
    agent: Any, run_input: dict, run_config: dict, bridge: EventBridge
) -> AsyncIterable[dict]:
    """把一輪 `agent.astream_events` 經 `pump_agent_events`/`EventBridge` 轉譯成 wire 事件
    dict 逐一 yield。遇到不可恢復的 producer 例外時只 yield 一個 ERROR dict 後 return——
    呼叫端 MUST 把看到 ERROR 視為本輪最後一個事件。

    連線類例外（見 `_is_transient_stream_error`）改為在函式內部重建 event_queue/producer_task、
    以同一份 run_input/run_config 重新 astream，最多 `STREAM_RETRY_MAX_RUNS` 次，對呼叫端
    透明。checkpointer 已含失敗那次的部分進度，重試視為冪等追加。retry 用盡或非連線類例外，
    一律照原本的 ERROR 路徑處理。
    """
    stream_retry_runs = 0
    while True:
        event_queue: asyncio.Queue[Any] = asyncio.Queue()
        producer_task = asyncio.create_task(
            pump_agent_events(agent, run_input, run_config, event_queue)
        )
        retry_requested = False
        try:
            while True:
                try:
                    queue_item = await asyncio.wait_for(
                        event_queue.get(), timeout=HEARTBEAT_INTERVAL_SECONDS
                    )
                except TimeoutError:
                    heartbeat = bridge.heartbeat_event()
                    if heartbeat is not None:
                        yield heartbeat
                    continue
                if queue_item is None:
                    break
                if isinstance(queue_item, tuple) and queue_item[0] == "error":
                    error = queue_item[1]
                    if stream_retry_runs < STREAM_RETRY_MAX_RUNS and _is_transient_stream_error(
                        error
                    ):
                        stream_retry_runs += 1
                        logger.warning(
                            "transient stream error, retrying turn (%d/%d): %s",
                            stream_retry_runs,
                            STREAM_RETRY_MAX_RUNS,
                            error,
                        )
                        retry_requested = True
                        break
                    # str(exc) 可為空(某些連線中斷例外)——退回類名,比照 Java 端 requireNonNullElse
                    message = (
                        GRAPH_RECURSION_ERROR_MESSAGE
                        if isinstance(error, GraphRecursionError)
                        else (str(error) or type(error).__name__)
                    )
                    logger.exception("agent stream failed", exc_info=error)
                    yield {"type": "ERROR", "code": "AGENT_FAILURE", "message": message}
                    return
                for wire_event in bridge.handle(queue_item):
                    yield wire_event
        finally:
            await producer_task
        if not retry_requested:
            return


@app.post("/chat", response_class=EventSourceResponse)
async def chat(request: Annotated[ChatRequest, Body()]) -> AsyncIterable[ServerSentEvent]:
    # NEVER log prompt/資料內容 -- 只記摘要。
    logger.info(
        "chat request sessionId=%s message_length=%d source_count=%d",
        request.sessionId,
        len(request.message),
        len(request.sources),
    )

    # AGENT_WORKSPACE_ROOT 每個 request 現讀一次（build_workspace_store 內部讀 env）--
    # 建成 module 層單例會在 import 時就凍結 env 值，測試用 monkeypatch.setenv 會失效。
    workspace_store = build_workspace_store()
    workspace = workspace_store.prepare(request.userId, request.sessionId)
    write_sources_doc(workspace, [(item.alias, item.fileType) for item in request.sources])
    staged_skill_paths = stage_skills(
        workspace, builtin_skills_dir(), workspace.root.parents[1] / "skills"
    )
    connection = open_locked_connection(
        [Source(item.alias, item.path, item.fileType) for item in request.sources]
    )
    try:
        model = build_model()
        # per-request recorder, baked into this request's tool closures via build_agent --
        # keeps run_sql's TABLE-eligible results scoped to this request only, so a concurrent
        # /chat request can never overwrite them before this request's on_tool_end pops them.
        recorder = ToolResultRecorder()
        agent = build_agent(model, connection, workspace, staged_skill_paths, recorder)
        run_config: dict[str, Any] = {
            "configurable": {"thread_id": request.sessionId},
            "recursion_limit": AGENT_RECURSION_LIMIT,
            "callbacks": _build_callbacks(),
        }

        # Built once and reused as-is (never rebuilt) across the first-round retry loop below --
        # LangGraph's `add_messages` reducer dedups by `message.id`, so re-invoking with this
        # exact same list of HumanMessage objects is a safe no-op replay for the checkpointer;
        # constructing fresh HumanMessage objects with new ids would silently double-append them
        # to the persisted thread history instead.
        run_input = {"messages": _seed_messages(request)}

        # 選定歷史版本繼續編輯：剝掉注入的 __ERD_RESULTS__/主題 script 後寫回 workspace 當
        # 這一輪的編輯基底。MUST 在 mtime 快照之前完成,否則本輪未改動也會被誤判成「有改」。
        if request.previousDashboardHtml is not None:
            workspace.dashboard_path.write_text(
                strip_injected_blocks(request.previousDashboardHtml), encoding="utf-8"
            )

        dashboard_mtime_before = (
            workspace.dashboard_path.stat().st_mtime if workspace.dashboard_path.exists() else None
        )

        bridge = EventBridge(recorder)
        async for wire_event in _stream_agent_turn(agent, run_input, run_config, bridge):
            yield ServerSentEvent(data=wire_event)
            if wire_event["type"] == "ERROR":
                return

        # 首輪空回應重試：final_answer 為空且本輪完全沒有工具啟動時，重新 invoke 同一份
        # run_input，至多 FIRST_ROUND_RETRY_MAX_RUNS 輪。
        retry_runs = 0
        while (
            not bridge.final_answer().strip()
            and not bridge.tool_started
            and retry_runs < FIRST_ROUND_RETRY_MAX_RUNS
        ):
            retry_runs += 1
            bridge = EventBridge(recorder)
            async for wire_event in _stream_agent_turn(agent, run_input, run_config, bridge):
                yield ServerSentEvent(data=wire_event)
                if wire_event["type"] == "ERROR":
                    return

        # Dashboard 收尾：只有 dashboard.html 存在且 mtime 有變才進入 guard 檢查（本輪確實
        # 寫過檔案，不是沿用前一輪殘留檔）。
        dashboard_html_emitted = False
        dashboard_guard_failed = False
        dashboard_mtime_after = (
            workspace.dashboard_path.stat().st_mtime if workspace.dashboard_path.exists() else None
        )
        if dashboard_mtime_after is not None and dashboard_mtime_after != dashboard_mtime_before:
            html = workspace.dashboard_path.read_text(encoding="utf-8")
            results = load_all_results(workspace)
            report = check_dashboard_html(html, set(results), results)

            repair_runs = 0
            while not report.ok and repair_runs < GUARD_REPAIR_MAX_RUNS:
                repair_runs += 1
                repair_message = HumanMessage(
                    "Dashboard failed quality checks. Fix dashboard.html with edit_file:\n- "
                    + "\n- ".join(report.errors)
                )
                repair_bridge = EventBridge(recorder)
                repair_input = {"messages": [repair_message]}
                async for wire_event in _stream_agent_turn(
                    agent, repair_input, run_config, repair_bridge
                ):
                    yield ServerSentEvent(data=wire_event)
                    if wire_event["type"] == "ERROR":
                        return
                # 修復輪跑完 -- 重讀 dashboard.html、重新讀結果、重新 check。
                html = workspace.dashboard_path.read_text(encoding="utf-8")
                results = load_all_results(workspace)
                report = check_dashboard_html(html, set(results), results)

            if not report.ok:
                dashboard_guard_failed = True
                # guard 修復輪跑完仍不過時記一筆 warning,供監測失敗率;只記錯誤摘要,NEVER log HTML。
                error_summary = "; ".join(report.errors)[:200]
                logger.warning(
                    "dashboard guard failed session=%s round=%d errors=%d: %s",
                    request.sessionId,
                    repair_runs,
                    len(report.errors),
                    error_summary,
                )
                yield ServerSentEvent(
                    data={
                        "type": "STEP",
                        "stepKey": "dashboard_guard",
                        "title": "dashboard 製作失敗",
                        "status": "ERROR",
                    }
                )
            else:
                referenced_results = {
                    query_id: results[query_id] for query_id in referenced_query_ids(report.html)
                }
                final_html = inject_theme(inject_results(report.html, referenced_results))
                dashboard_html_emitted = True
                yield ServerSentEvent(data={"type": "DASHBOARD_HTML", "html": final_html})

        # 刻意仍讀 pre-repair 的 `bridge`(非 `repair_bridge`):修復輪只透過 edit_file 改
        # dashboard.html,不帶自己的說明文字,ANSWER 沿用原本分析輪的文字。
        final_answer_text = bridge.final_answer().strip()
        if dashboard_guard_failed:
            # guard 終敗:模型文字可能仍在講「已完成」,那是假成功,用警示句戳破。獨立分支。
            answer_text = (
                f"{DASHBOARD_REJECTED_PREFIX}\n\n{final_answer_text}"
                if final_answer_text
                else DASHBOARD_REJECTED_PREFIX
            )
        elif final_answer_text:
            answer_text = final_answer_text
        elif dashboard_html_emitted:
            # 空文字兜底依「本輪是否已發出 DASHBOARD_HTML」二選一。
            answer_text = DASHBOARD_UPDATED_FALLBACK_MESSAGE
        else:
            answer_text = EMPTY_ANSWER_FALLBACK_MESSAGE
        yield ServerSentEvent(data={"type": "ANSWER", "text": answer_text})
    finally:
        workspace_store.persist(workspace)
        connection.close()


# -- POST /repair: browser-error-driven single-call HTML fix --------------------------------
#
# 前端 RepairOfferCard 在 langgraph-analysis provider 下的落點。不重用 /chat 的 agent 迴圈:
# 這是「照已知錯誤改一份現成 HTML」的窄任務,一次 system+user 訊息呼叫更快、更確定性。

# 單次修復請求最多納入的瀏覽器錯誤數,避免超長 prompt。
REPAIR_MAX_BROWSER_ERRORS = 10

# guard 不過時，最多重試次數（首次呼叫之外再一次；總計最多 2 次模型呼叫）。
REPAIR_GUARD_RETRY_MAX_RUNS = 1

# 單次模型呼叫的逾時秒數——沒有 agent 迴圈的逐事件 heartbeat,這是唯一的逾時防線,
# 逾時視同模型呼叫失敗(502)。
REPAIR_MODEL_CALL_TIMEOUT_SECONDS = float(os.environ.get("REPAIR_MODEL_CALL_TIMEOUT_SECONDS", "60"))

REPAIR_SYSTEM_PROMPT = (
    "You are repairing a self-contained HTML dashboard (Tailwind CSS + ECharts, no external "
    "data files) that produced runtime JavaScript errors in the browser. You will be given the "
    "current HTML and the browser's error messages. Fix ONLY what is necessary to resolve the "
    "reported errors -- keep everything else (markup, data references, styling, other charts) "
    "verbatim. Do not add commentary or explanation. Respond with the complete corrected HTML "
    "wrapped in a single ```html fenced code block, and nothing else."
)

_HTML_FENCE_PATTERN = re.compile(r"```(?:html)?\s*\n(.*?)```", re.DOTALL)


class RepairErrorItem(BaseModel):
    message: str


class RepairRequest(BaseModel):
    sessionId: str
    userId: str
    html: str
    errors: list[RepairErrorItem]


def _extract_html_block(model_response_text: str) -> str:
    """Pulls the HTML out of the model's fenced ```html block; falls back to the raw response
    (stripped) if the model didn't fence it as instructed -- never raise on a malformed response,
    let check_dashboard_html's structure check reject it deterministically instead."""
    fence_match = _HTML_FENCE_PATTERN.search(model_response_text)
    return fence_match.group(1).strip() if fence_match else model_response_text.strip()


def _build_repair_user_message(html: str, errors: list[RepairErrorItem]) -> str:
    capped_errors = errors[:REPAIR_MAX_BROWSER_ERRORS]
    error_lines = "\n".join(f"- {error.message}" for error in capped_errors)
    return (
        "The following self-contained HTML dashboard produced these runtime JavaScript errors "
        f"in the browser:\n\n{error_lines}\n\nHTML:\n{html}"
    )


def _build_repair_retry_user_message(previous_html: str, guard_errors: list[str]) -> str:
    error_lines = "\n".join(f"- {message}" for message in guard_errors)
    return (
        "The previous fix failed these validation checks:\n"
        f"{error_lines}\n\n"
        "Please produce a corrected, complete HTML dashboard that resolves these issues. "
        "Respond only with a single ```html fenced code block.\n\n"
        f"HTML:\n{previous_html}"
    )


async def _invoke_repair_model(model: Any, messages: list[BaseMessage]) -> str:
    response = await asyncio.wait_for(
        model.ainvoke(messages), timeout=REPAIR_MODEL_CALL_TIMEOUT_SECONDS
    )
    content = response.content
    return content if isinstance(content, str) else str(content)


@app.post("/repair")
async def repair(request: Annotated[RepairRequest, Body()]) -> JSONResponse:
    # NEVER log html content -- only a summary, same rule as /chat above.
    logger.info("repair request sessionId=%s errorCount=%d", request.sessionId, len(request.errors))

    workspace_store = build_workspace_store()
    workspace = workspace_store.prepare(request.userId, request.sessionId)
    # previousDashboardHtml 的鏡射:Java 端送來的 html 是「注入後」的 artifact rawHtml,剝掉
    # 本服務注入的 __ERD_RESULTS__/主題 script,模型只看乾淨骨架。
    clean_html = strip_injected_blocks(request.html)
    all_results = load_all_results(workspace)
    available_query_ids = set(all_results)

    messages: list[BaseMessage] = [
        SystemMessage(REPAIR_SYSTEM_PROMPT),
        HumanMessage(_build_repair_user_message(clean_html, request.errors)),
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
        return JSONResponse(status_code=502, content={"error": "repair model call failed"})

    candidate_html = _extract_html_block(model_response_text)
    report = check_dashboard_html(candidate_html, available_query_ids, all_results)

    retry_runs = 0
    while not report.ok and retry_runs < REPAIR_GUARD_RETRY_MAX_RUNS:
        retry_runs += 1
        retry_messages: list[BaseMessage] = [
            SystemMessage(REPAIR_SYSTEM_PROMPT),
            HumanMessage(_build_repair_retry_user_message(candidate_html, report.errors)),
        ]
        try:
            model_response_text = await _invoke_repair_model(model, retry_messages)
        except Exception as model_error:  # noqa: BLE001 -- any model-call failure maps to 502
            logger.warning(
                "repair model retry call failed sessionId=%s: %s",
                request.sessionId,
                type(model_error).__name__,
            )
            return JSONResponse(status_code=502, content={"error": "repair model call failed"})
        candidate_html = _extract_html_block(model_response_text)
        report = check_dashboard_html(candidate_html, available_query_ids, all_results)

    if not report.ok:
        logger.info(
            "repair guard failed sessionId=%s errorCount=%d", request.sessionId, len(report.errors)
        )
        return JSONResponse(status_code=422, content={"errors": report.errors})

    referenced_results = {
        query_id: all_results[query_id] for query_id in referenced_query_ids(report.html)
    }
    final_html = inject_theme(inject_results(report.html, referenced_results))
    logger.info("repair passed sessionId=%s", request.sessionId)
    return JSONResponse(status_code=200, content={"html": final_html})
