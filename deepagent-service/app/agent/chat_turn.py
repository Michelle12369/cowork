"""`/chat` 一輪的完整生命週期：workspace 準備 → duckdb 連線 → agent 組裝 → astream_events 經
EventBridge 轉譯成 wire 事件 → dashboard.html 主題改寫＋結果注入 → ANSWER。`app/main.py` 的
`/chat` 端點只負責把 `ChatTurn` 包進 `async with` 再轉成 SSE，本檔案才是實際流程。此層允許 import
LLM 框架（deepagents/langchain/langgraph/langfuse）——見 pyproject.toml 的 ruff TID251
per-file-ignores。
"""

import logging
from collections.abc import AsyncIterable
from typing import Any, Self

import duckdb
import httpx
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.errors import GraphRecursionError

from app.agent import session_state, tracing
from app.agent.events import EventBridge
from app.agent.graph import build_agent, build_model
from app.agent.prompts import (
    PREVIOUS_VERSION_SYSTEM_NOTE,
    build_sources_manifest_note,
)
from app.agent.tools.recording import ToolResultRecorder
from app.api.events import (
    AnswerEvent,
    ClarifyingQuestion,
    DashboardHtmlEvent,
    ErrorEvent,
    QuestionEvent,
    StepEvent,
    TableEvent,
    TokenEvent,
)
from app.api.schemas import ChatRequest
from app.config import get_settings
from app.engine.duck import Source, open_locked_connection
from app.engine.questions_extract import extract_questions_block
from app.engine.results import (
    inject_results,
    load_all_results,
    referenced_query_ids,
    strip_injected_blocks,
)
from app.engine.source_cache import resolve_source_path
from app.engine.source_manifest import (
    build_manifest,
    diff_manifests,
    load_manifest,
    save_manifest,
)
from app.engine.theme_rewrite import apply_erd_theme
from app.engine.workspace import (
    SessionWorkspace,
    WorkspacePersistError,
    builtin_skills_dir,
    stage_skills,
)
from app.engine.workspace_store import build_workspace_store

logger = logging.getLogger(__name__)

StreamWireEvent = StepEvent | TokenEvent | TableEvent | ErrorEvent

AGENT_RECURSION_LIMIT = get_settings().AGENT_RECURSION_LIMIT

GRAPH_RECURSION_ERROR_MESSAGE = "分析步驟過多而中止,請把需求拆小一點再試一次"

EMPTY_ANSWER_FALLBACK_MESSAGE = "本輪已完成分析步驟,但未產生文字說明——請再問一次或換個說法。"

DASHBOARD_UPDATED_FALLBACK_MESSAGE = "儀表板已依你的需求更新,請查看右側預覽。"

CLARIFYING_QUESTIONS_FALLBACK_MESSAGE = "請回答以下問題以繼續。"

STREAM_RETRY_MAX_RUNS = 1


def _is_transient_stream_error(error: BaseException) -> bool:
    """判定例外是否屬傳輸層失敗（斷線、逾時），值得整輪自動重試，而非 model/graph 邏輯錯誤。
    命中 `httpx.HTTPError`/`ConnectionError`，或類名/訊息含 connection/network/timed out。"""
    if isinstance(error, (httpx.HTTPError, ConnectionError)):
        return True
    haystack = f"{type(error).__name__} {error}".lower()
    return any(keyword in haystack for keyword in ("connection", "network", "timed out"))


def _build_callbacks() -> list[Any]:
    """Langfuse tracing：gate 看 `tracing.is_tracing_enabled()`（在 lifespan 的
    `init_langfuse()` 設定），不再直接看 Settings 的 key——runtime 可能完整接管建構，
    client 不一定源自那兩個 key，未 enable 就不建 handler。"""
    if not tracing.is_tracing_enabled():
        return []
    from langfuse.langchain import CallbackHandler

    return [CallbackHandler()]


def _refresh_source_manifest(
    workspace: SessionWorkspace,
    connection: duckdb.DuckDBPyConnection,
    sources: list[tuple[str, str]],
) -> str | None:
    """本輪 manifest 與上一輪存檔做 diff,有變更時回傳 sources_changed_note(無上一輪基準或
    無變更則 None);本輪 manifest 一律存檔,下一輪才有基準可比。MUST 在連線鎖門後呼叫。"""
    previous_manifest = load_manifest(workspace)
    current_manifest = build_manifest(connection, sources)
    sources_changed_note = None
    if previous_manifest is not None:
        sources_diff = diff_manifests(previous_manifest, current_manifest)
        if not sources_diff.is_empty():
            sources_changed_note = build_sources_manifest_note(sources_diff)
    save_manifest(workspace, current_manifest)
    return sources_changed_note


def _seed_messages(
    request: ChatRequest, sources_changed_note: str | None = None
) -> list[BaseMessage]:
    """checkpoint 已存在的 thread 只帶本次訊息（避免重複灌入歷史）；否則從 request.history 重建
    後 append 本次 message。`previousDashboardHtml` 有值時在本輪 HumanMessage 附加
    `PREVIOUS_VERSION_SYSTEM_NOTE`;`sources_changed_note` 非 None 時再接著附加——兩者都是
    only-current-turn 的提示,MUST 在 checkpoint-exists 分支(只帶當輪訊息)與重建分支都生效,
    mid-session 上傳新檔正是 checkpoint 已存在的情境,是這個修正要覆蓋的關鍵路徑。"""
    current_turn_message = request.message
    if request.previousDashboardHtml is not None:
        current_turn_message = f"{current_turn_message}{PREVIOUS_VERSION_SYSTEM_NOTE}"
    if sources_changed_note is not None:
        current_turn_message = f"{current_turn_message}{sources_changed_note}"

    if session_state.has_checkpoint(request.sessionId):
        return [HumanMessage(current_turn_message)]
    messages: list[BaseMessage] = [
        AIMessage(item.text) if item.role.lower() == "assistant" else HumanMessage(item.text)
        for item in request.history
    ]
    messages.append(HumanMessage(current_turn_message))
    return messages


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
        staged_skill_paths = stage_skills(
            self._workspace, builtin_skills_dir(), self._workspace.root.parents[1] / "skills"
        )
        self._connection = open_locked_connection(
            [
                Source(item.alias, resolve_source_path(item.path), item.fileType)
                for item in request.sources
            ]
        )
        try:
            self._recorder = ToolResultRecorder()
            self._agent = build_agent(
                build_model(),
                self._connection,
                self._workspace,
                staged_skill_paths,
                self._recorder,
            )
            self._run_config: RunnableConfig = {
                "configurable": {"thread_id": request.sessionId},
                "recursion_limit": AGENT_RECURSION_LIMIT,
                "callbacks": _build_callbacks(),
                "metadata": {
                    "langfuse_user_id": request.userId,
                    "langfuse_session_id": request.sessionId,
                },
            }
            sources_changed_note = _refresh_source_manifest(
                self._workspace,
                self._connection,
                [(item.alias, item.path) for item in request.sources],
            )
            self._run_input = {"messages": _seed_messages(request, sources_changed_note)}
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
            self._store.cleanup_scratch()
            raise
        return self

    async def __aexit__(self, *exception_info: object) -> None:
        if self._connection is not None:
            self._connection.close()
        # 涵蓋 stream()/finalize() 以 ErrorEvent 提前 return、persist 失敗、以及正常完成
        # ——`async with` 保證無論哪種退出方式都會執行到這裡。s3 模式下清 per-turn scratch;
        # local 模式為 no-op。
        self._store.cleanup_scratch()

    async def stream(self) -> AsyncIterable[StreamWireEvent]:
        self.bridge = EventBridge(self._recorder)
        for run_index in range(STREAM_RETRY_MAX_RUNS + 1):
            try:
                async for agent_event in self._agent.astream_events(
                    self._run_input, config=self._run_config, version="v2"
                ):
                    for wire_event in self.bridge.handle(agent_event):
                        yield wire_event
                return
            except Exception as error:
                if run_index < STREAM_RETRY_MAX_RUNS and _is_transient_stream_error(error):
                    logger.warning(
                        "transient stream error, retrying turn (%d/%d): %s",
                        run_index + 1,
                        STREAM_RETRY_MAX_RUNS,
                        error,
                    )
                    continue
                message = (
                    GRAPH_RECURSION_ERROR_MESSAGE
                    if isinstance(error, GraphRecursionError)
                    else (str(error) or type(error).__name__)
                )
                logger.exception("agent stream failed", exc_info=error)
                yield ErrorEvent(code="AGENT_FAILURE", message=message)
                return

    async def finalize(
        self,
    ) -> AsyncIterable[StreamWireEvent | DashboardHtmlEvent | AnswerEvent | QuestionEvent]:
        request = self._request
        # Dashboard 收尾：mtime 有變（本輪確實寫過檔）才做主題改寫＋結果注入。
        dashboard_html_emitted = False
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
            themed_html = apply_erd_theme(html)
            # 濾掉引用不存在 query id 的筆誤,避免 KeyError。
            referenced_results = {
                query_id: results[query_id]
                for query_id in referenced_query_ids(themed_html)
                if query_id in results
            }
            final_html = inject_results(themed_html, referenced_results)
            dashboard_html_emitted = True
            yield DashboardHtmlEvent(html=final_html)

        final_answer_text = self.bridge.final_answer().strip()
        stripped_answer_text, clarifying_questions = extract_questions_block(final_answer_text)
        if clarifying_questions is not None:
            yield QuestionEvent(
                questions=[ClarifyingQuestion(**question) for question in clarifying_questions]
            )
            answer_text = stripped_answer_text.strip() or CLARIFYING_QUESTIONS_FALLBACK_MESSAGE
        elif final_answer_text:
            answer_text = final_answer_text
        elif dashboard_html_emitted:
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
