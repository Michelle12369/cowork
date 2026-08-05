"""`/chat` 一輪的完整生命週期：workspace 準備 → duckdb 連線 → agent 組裝 → astream_events 經
EventBridge 轉譯成 wire 事件 → dashboard.html guard 修復迴路 → ANSWER。`app/main.py` 的 `/chat`
端點只負責把 `ChatTurn` 包進 `async with` 再轉成 SSE，本檔案才是實際流程。此層允許 import LLM
框架（deepagents/langchain/langgraph/langfuse）——見 pyproject.toml 的 ruff TID251 per-file-ignores。
"""

import asyncio
import logging
from collections.abc import AsyncIterable
from typing import Any, Self

import httpx
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.errors import GraphRecursionError

from app.agent import session_state, tracing
from app.agent.events import EventBridge, pump_agent_events
from app.agent.graph import build_agent, build_model
from app.agent.prompts import (
    PREVIOUS_VERSION_SYSTEM_NOTE,
)
from app.agent.tools.recording import ToolResultRecorder
from app.api.events import (
    AnswerEvent,
    DashboardHtmlEvent,
    ErrorEvent,
    StepEvent,
    TableEvent,
    TokenEvent,
)
from app.api.schemas import ChatRequest
from app.config import get_settings
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
    WorkspacePersistError,
    build_workspace_store,
    builtin_skills_dir,
    stage_skills,
    write_sources_doc,
)

logger = logging.getLogger(__name__)

# 一輪串流可能出現的事件型別（不含 ANSWER/DASHBOARD_HTML，那兩者只在 `finalize()` 尾端發出）。
StreamWireEvent = StepEvent | TokenEvent | TableEvent | ErrorEvent

# Re-emit the in-progress step's RUNNING STEP every N seconds so the stream never goes silent.
# MUST stay well under Java's per-event inactivity timeout.
HEARTBEAT_INTERVAL_SECONDS = 15.0

# astream_events(..., config=run_config) falls back to langchain_core's default recursion limit
# (25) unless set explicitly here -- create_deep_agent's own binding isn't threaded through.
# 80 對齊 docker-compose 預設,留夠一輪標準 dashboard 任務的工具呼叫量。
AGENT_RECURSION_LIMIT = get_settings().AGENT_RECURSION_LIMIT

# Surfaces GraphRecursionError as an actionable Traditional-Chinese message instead of
# LangGraph's raw English text leaking into the persisted chat reply.
GRAPH_RECURSION_ERROR_MESSAGE = "分析步驟過多而中止,請把需求拆小一點再試一次"

# dashboard.html 未過 check_dashboard_html 時，修復輪數的硬上限；實際停止時機看
# `_guard_repair_should_stop`（一筆錯誤都沒修掉就提前停）。
GUARD_REPAIR_MAX_RUNS = 5

# guard 一律執行(check_dashboard_html 不受影響,含 erd 主題套用);此開關只管修復迴圈要不要跑、
# 終敗時要不要擋下出貨。刻意只管 `/chat`——`/repair` 的重試成本低且是使用者主動觸發修復,
# 回未驗證的 HTML 特別誤導,不納入此開關,不要之後「順手」把它也接進來。
ERD_GUARD_BLOCKING = get_settings().ERD_GUARD_BLOCKING.strip().lower() != "false"


def _guard_repair_should_stop(previous_errors: set[str], current_errors: set[str]) -> bool:
    """比較前後兩輪 `report.errors` 的集合差,不比數量,判斷修復迴圈該不該停。數量會被
    `html_guard` 的錯誤 clamp 上限騙過(真實問題數降了,但回報筆數因 clamp 維持不變);
    整份 write_file 重寫下,數量上升也不再代表退步——修掉兩個問題順帶多帶出一個是正常
    進度。停止條件改為 `previous_errors - current_errors` 是否為空:有任何一筆真的消失
    就算有進展,值得再修一輪。"""
    return not (previous_errors - current_errors)


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
    命中 `httpx.HTTPError`/`ConnectionError`，或類名/訊息含 connection/network/timed out。"""
    if isinstance(error, (httpx.HTTPError, ConnectionError)):
        return True
    haystack = f"{type(error).__name__} {error}".lower()
    return any(keyword in haystack for keyword in ("connection", "network", "timed out"))


# 首輪空回應（無文字、也沒有任何工具啟動）最多重新 invoke 的次數。
FIRST_ROUND_RETRY_MAX_RUNS = 2


def _build_callbacks() -> list[Any]:
    """Langfuse tracing：gate 看 `tracing.is_tracing_enabled()`（在 lifespan 的
    `init_langfuse()` 設定），不再直接看 Settings 的 key——runtime 可能完整接管建構，
    client 不一定源自那兩個 key，未 enable 就不建 handler。"""
    if not tracing.is_tracing_enabled():
        return []
    from langfuse.langchain import CallbackHandler

    return [CallbackHandler()]


def _seed_messages(request: ChatRequest) -> list[BaseMessage]:
    """checkpoint 已存在的 thread 只帶本次訊息（避免重複灌入歷史）；否則從 request.history 重建
    後 append 本次 message。`previousDashboardHtml` 有值時只在本輪 HumanMessage 附加
    `PREVIOUS_VERSION_SYSTEM_NOTE`。"""
    current_turn_message = request.message
    if request.previousDashboardHtml is not None:
        current_turn_message = f"{request.message}{PREVIOUS_VERSION_SYSTEM_NOTE}"

    if session_state.has_checkpoint(request.sessionId):
        return [HumanMessage(current_turn_message)]
    # Java 端 LangGraphAnalysisProvider 把 Sender enum 映成 OpenAI 角色詞彙,AI 一律送
    # "assistant"(它的 Javadoc 明講這是為了不讓歷史被誤植);"AI" 從未真的送過,只是便宜的
    # 額外容錯。case-insensitive 比對,兩者都視為 AI 角色。
    messages: list[BaseMessage] = [
        AIMessage(item.text)
        if item.role.lower() in ("assistant", "ai")
        else HumanMessage(item.text)
        for item in request.history
    ]
    messages.append(HumanMessage(current_turn_message))
    return messages


async def stream_agent_turn(
    agent: Any, run_input: dict, run_config: dict, bridge: EventBridge
) -> AsyncIterable[StreamWireEvent]:
    """把一輪 astream_events 經 EventBridge 轉譯成 wire 事件逐一 yield;不可恢復例外只 yield
    一個 ErrorEvent 後 return,呼叫端 MUST 視為本輪最後一個事件。連線類例外(見
    `_is_transient_stream_error`)在函式內部重建 queue/producer_task 以同一份 run_input
    重試,最多 `STREAM_RETRY_MAX_RUNS` 次,對呼叫端透明;retry 用盡或非連線類例外一律走
    ERROR 路徑。"""
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
                    yield ErrorEvent(code="AGENT_FAILURE", message=message)
                    return
                for wire_event in bridge.handle(queue_item):
                    yield wire_event
        finally:
            await producer_task
        if not retry_requested:
            return


class ChatTurn:
    """non-bean: instantiate per /chat request."""

    def __init__(self, request: ChatRequest) -> None:
        self._request = request
        self._connection = None
        self.bridge: EventBridge | None = None

    async def __aenter__(self) -> Self:
        request = self._request
        self._store = build_workspace_store()
        self._workspace = self._store.prepare(request.userId, request.sessionId)
        write_sources_doc(
            self._workspace, [(item.alias, item.fileType) for item in request.sources]
        )
        staged_skill_paths = stage_skills(
            self._workspace, builtin_skills_dir(), self._workspace.root.parents[1] / "skills"
        )
        self._connection = open_locked_connection(
            [Source(item.alias, item.path, item.fileType) for item in request.sources]
        )
        # __aexit__ only runs once __aenter__ has returned -- anything raised past this point
        # (build_agent, _seed_messages, dashboard_path IO, ...) would otherwise leak the
        # connection since `async with` never considers the block entered. Mirrors the old
        # try/finally, which covered everything after the connection was acquired. Catches
        # BaseException (not Exception) so client-disconnect CancelledError still closes it.
        try:
            self._recorder = ToolResultRecorder()
            self._agent = build_agent(
                build_model(),
                self._connection,
                self._workspace,
                staged_skill_paths,
                self._recorder,
            )
            self._run_config = {
                "configurable": {"thread_id": request.sessionId},
                "recursion_limit": AGENT_RECURSION_LIMIT,
                "callbacks": _build_callbacks(),
            }
            # 刻意建一次、跨 `stream()` 的首輪重試迴圈重複沿用:LangGraph `add_messages`
            # reducer 以 `message.id` 去重,同一批 HumanMessage 物件重放是安全的;每次都
            # 重新建構則會把同一句話悄悄疊加進 persisted thread 兩次。
            self._run_input = {"messages": _seed_messages(request)}
            if request.previousDashboardHtml is not None:
                # MUST 在下面的 dashboard mtime 快照之前寫入,否則沒改動的一輪會被誤判成
                # 「改過 dashboard」;快照另一半(`dashboard_mtime_after`)在 `finalize()`。
                self._workspace.dashboard_path.write_text(
                    strip_injected_blocks(request.previousDashboardHtml), encoding="utf-8"
                )
            self._dashboard_mtime_before = (
                self._workspace.dashboard_path.stat().st_mtime
                if self._workspace.dashboard_path.exists()
                else None
            )
        except BaseException:
            self._connection.close()
            raise
        return self

    async def __aexit__(self, *exception_info: object) -> None:
        if self._connection is not None:
            self._connection.close()

    async def stream(self) -> AsyncIterable[StreamWireEvent]:
        self.bridge = EventBridge(self._recorder)
        async for wire_event in stream_agent_turn(
            self._agent, self._run_input, self._run_config, self.bridge
        ):
            yield wire_event
            if isinstance(wire_event, ErrorEvent):
                return
        retry_runs = 0
        while (
            not self.bridge.final_answer().strip()
            and not self.bridge.tool_started
            and retry_runs < FIRST_ROUND_RETRY_MAX_RUNS
        ):
            retry_runs += 1
            self.bridge = EventBridge(self._recorder)
            async for wire_event in stream_agent_turn(
                self._agent, self._run_input, self._run_config, self.bridge
            ):
                yield wire_event
                if isinstance(wire_event, ErrorEvent):
                    return

    async def finalize(
        self,
    ) -> AsyncIterable[StreamWireEvent | DashboardHtmlEvent | AnswerEvent]:
        request = self._request
        # Dashboard 收尾：只有 dashboard.html 存在且 mtime 有變才進入 guard 檢查（本輪確實
        # 寫過檔案，不是沿用前一輪殘留檔）。
        dashboard_html_emitted = False
        dashboard_guard_failed = False
        dashboard_mtime_after = (
            self._workspace.dashboard_path.stat().st_mtime
            if self._workspace.dashboard_path.exists()
            else None
        )
        if (
            dashboard_mtime_after is not None
            and dashboard_mtime_after != self._dashboard_mtime_before
        ):
            html = self._workspace.dashboard_path.read_text(encoding="utf-8")
            results = load_all_results(self._workspace)
            report = check_dashboard_html(html, set(results), results)

            repair_runs = 0
            previous_errors = set(report.errors)
            while ERD_GUARD_BLOCKING and not report.ok and repair_runs < GUARD_REPAIR_MAX_RUNS:
                repair_runs += 1
                repair_message = HumanMessage(
                    "Dashboard failed quality checks. Rewrite dashboard.html in full with a "
                    "single write_file call, fixing:\n- " + "\n- ".join(report.errors)
                )
                repair_bridge = EventBridge(self._recorder)
                repair_input = {"messages": [repair_message]}
                async for wire_event in stream_agent_turn(
                    self._agent, repair_input, self._run_config, repair_bridge
                ):
                    yield wire_event
                    if isinstance(wire_event, ErrorEvent):
                        return
                # 修復輪跑完 -- 重讀 dashboard.html、重新讀結果、重新 check。
                html = self._workspace.dashboard_path.read_text(encoding="utf-8")
                results = load_all_results(self._workspace)
                report = check_dashboard_html(html, set(results), results)
                if report.ok:
                    break
                current_errors = set(report.errors)
                if _guard_repair_should_stop(previous_errors, current_errors):
                    logger.info(
                        "dashboard guard repair stalled session=%s round=%d errors=%d->%d",
                        request.sessionId,
                        repair_runs,
                        len(previous_errors),
                        len(current_errors),
                    )
                    break
                previous_errors = current_errors

            if not report.ok:
                # guard 修復輪跑完仍不過時記一筆 warning,供監測失敗率;只記錯誤摘要,NEVER log HTML。
                # 無論是否阻擋都要記——非阻擋模式下開發者仍要能從 server log 查到這輪其實沒過。
                error_summary = "; ".join(report.errors)[:200]
                logger.warning(
                    "dashboard guard failed session=%s round=%d errors=%d: %s",
                    request.sessionId,
                    repair_runs,
                    len(report.errors),
                    error_summary,
                )

            if not report.ok and ERD_GUARD_BLOCKING:
                dashboard_guard_failed = True
                yield StepEvent(
                    stepKey="dashboard_guard", title="dashboard 製作失敗", status="ERROR"
                )
            else:
                # 非阻擋且 guard 未過時,report.html 可能引用不存在的 query id(_check_referenced_
                # query_ids 沒過就是這個原因)——`report.ok` 為真時 guard 已保證這裡都存在,但
                # 非阻擋失敗時不能假設,用 `if query_id in results` 濾掉,避免 KeyError。
                referenced_results = {
                    query_id: results[query_id]
                    for query_id in referenced_query_ids(report.html)
                    if query_id in results
                }
                final_html = inject_theme(inject_results(report.html, referenced_results))
                dashboard_html_emitted = True
                yield DashboardHtmlEvent(html=final_html)

        # 刻意仍讀 pre-repair 的 `bridge`(非 `repair_bridge`):修復輪只透過 write_file 整份重寫
        # dashboard.html,不帶自己的說明文字,ANSWER 沿用原本分析輪的文字。
        final_answer_text = self.bridge.final_answer().strip()
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
        yield AnswerEvent(text=answer_text)

        # stream() 若以 ErrorEvent 提前終止,呼叫端不會走到 finalize() ——刻意不 persist:
        # 前一輪完整 generation 才是一致的回復點,半成品輪不該覆蓋過去。
        try:
            self._store.persist(self._workspace)
        except WorkspacePersistError:
            logger.exception("workspace persist failed session=%s", request.sessionId)
            yield ErrorEvent(
                code="WORKSPACE_PERSIST_FAILED",
                message="本輪結果未能寫入儲存空間,下一輪可能拿不到這次的變更。",
            )
