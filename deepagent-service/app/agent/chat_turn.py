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

from app.agent import critic, session_state, tracing
from app.agent.events import EventBridge, pump_agent_events
from app.agent.graph import build_agent, build_model
from app.agent.prompts import (
    PREVIOUS_VERSION_SYSTEM_NOTE,
    build_critic_corrective_message,
    build_sources_manifest_note,
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
from app.engine.source_cache import resolve_source_path
from app.engine.source_manifest import (
    build_manifest,
    diff_manifests,
    load_manifest,
    save_manifest,
)
from app.engine.workspace import (
    WorkspacePersistError,
    builtin_skills_dir,
    stage_skills,
    write_sources_doc,
)
from app.engine.workspace_store import build_workspace_store

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

# final critic 修正輪跑完仍判不一致時的 ANSWER 前綴——與 DASHBOARD_REJECTED_PREFIX 同一種
# 「戳破假成功」用途,但成因不同(guard 是 HTML 沒過驗證;這裡是回答內容跟證據對不上),
# 獨立分支,刻意不與 dashboard_guard_failed 合併判斷。
CRITIC_INCONSISTENT_PREFIX = "⚠️ 系統覆核發現回答與實際執行狀態可能不一致,請以畫面實際內容為準。"


# ERD_GUARD_BLOCKING 是 import 時凍結的模組常數(見上);ERD_FINAL_CRITIC 刻意每次呼叫都重讀
# get_settings()——測試要能用 monkeypatch.setenv("ERD_FINAL_CRITIC", ...) 在單一測試檔內切換,
# 而 get_settings() 本身有 process 級 lru_cache,配合 conftest 的 `_reset_settings_cache`
# autouse fixture(每個測試前後清快取)就能即時反映 env 變更,不需要 importlib.reload。
def _final_critic_enabled() -> bool:
    return get_settings().ERD_FINAL_CRITIC.strip().lower() != "false"


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


async def _todos_summary(agent: Any, run_config: dict) -> str:
    """final critic 的證據之一——`write_todos` 工具寫入 LangGraph state 的 `todos`
    (deepagents 內建 planning middleware 的 state key)。這是 best-effort 讀取:沒有
    checkpointer、狀態還沒寫入、或任何其他失敗都不該讓證據蒐集本身變成本輪的失敗點,
    一律回 "(unavailable)"。"""
    try:
        state = await agent.aget_state(run_config)
        todos = (state.values or {}).get("todos") or []
    except Exception:  # noqa: BLE001 -- evidence collection must never break the turn
        return "(unavailable)"
    if not todos:
        return "(none)"
    return "; ".join(f"{todo.get('content', '')} [{todo.get('status', '')}]" for todo in todos)


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
        # final critic 證據之一(見 app.agent.critic):本輪新增了哪些 qN——跟這份「輪前快照」
        # 比對差集才知道,MUST 在任何工具有機會呼叫 run_sql 之前拍下。
        self._query_ids_before = set(load_all_results(self._workspace))
        write_sources_doc(
            self._workspace, [(item.alias, item.fileType) for item in request.sources]
        )
        staged_skill_paths = stage_skills(
            self._workspace, builtin_skills_dir(), self._workspace.root.parents[1] / "skills"
        )
        self._connection = open_locked_connection(
            [
                Source(item.alias, resolve_source_path(item.path), item.fileType)
                for item in request.sources
            ]
        )
        # __aexit__ only runs once __aenter__ has returned -- anything raised past this point
        # (build_agent, _seed_messages, dashboard_path IO, ...) would otherwise leak the
        # connection since `async with` never considers the block entered. Mirrors the old
        # try/finally, which covered everything after the connection was acquired. Catches
        # BaseException (not Exception) so client-disconnect CancelledError still closes it.
        try:
            self._recorder = ToolResultRecorder()
            # manifest 需要 DESCRIBE 掛載後的資料表,MUST 在連線鎖門後才能建;讀回上一輪(若有)
            # 存的 manifest、跟本輪 manifest 做 diff,決定要不要附加 sources_changed_note。
            # previous_manifest 為 None 代表沒有基準可比(session 首輪,或舊 session 在這個
            # 功能上線前就已存在)——兩種情況都沒有「上一輪」的意義,不生變更提示。
            previous_manifest = load_manifest(self._workspace)
            current_manifest = build_manifest(
                self._connection, [(item.alias, item.path) for item in request.sources]
            )
            sources_changed_note = None
            if previous_manifest is not None:
                sources_diff = diff_manifests(previous_manifest, current_manifest)
                if not sources_diff.is_empty():
                    sources_changed_note = build_sources_manifest_note(sources_diff)
            # 一律存(即使首輪),下一輪才有基準可比;與 generation 快照同放 workspace root,
            # 不需要額外接線就會隨 persist 一起推走。
            save_manifest(self._workspace, current_manifest)
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
            # __aexit__ 不會被呼叫(__aenter__ 尚未成功 return self)——這裡是這條路徑上唯一
            # 能清 per-turn scratch(s3 模式)的地方,否則 build_agent 等失敗會直接洩漏。
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

    def _dashboard_mtime(self) -> float | None:
        return (
            self._workspace.dashboard_path.stat().st_mtime
            if self._workspace.dashboard_path.exists()
            else None
        )

    async def _run_dashboard_section(
        self, mtime_before: float | None
    ) -> AsyncIterable[StreamWireEvent | DashboardHtmlEvent]:
        """Dashboard 收尾單輪：mtime-changed 檢查 → guard 修復迴圈 → inject → 發出
        DASHBOARD_HTML(或 dashboard_guard 失敗 STEP)。抽成獨立方法讓 final critic 判定不一致、
        跑完修正輪之後能重跑同一段邏輯——修正輪的 write 也 MUST 過同一道 guard,不能因為是
        「補救」就放行未驗證的 HTML。

        副作用（呼叫端讀這三個 self 屬性拿結果，async generator 沒有好用的回傳值管道）：
        `self._dashboard_html_emitted`、`self._dashboard_guard_failed`——本次呼叫的判定結果；
        `self._dashboard_section_changed`——本次呼叫是否偵測到 mtime 變動（沒變動時前兩者固定
        是 False，呼叫端據此決定要不要拿這次結果覆蓋前一次，而不是誤把「這輪沒再寫」當成
        「guard 過了」）。
        """
        request = self._request
        self._dashboard_html_emitted = False
        self._dashboard_guard_failed = False
        self._dashboard_section_changed = False
        dashboard_mtime_after = self._dashboard_mtime()
        if dashboard_mtime_after is None or dashboard_mtime_after == mtime_before:
            return
        self._dashboard_section_changed = True

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
            self._dashboard_guard_failed = True
            yield StepEvent(stepKey="dashboard_guard", title="dashboard 製作失敗", status="ERROR")
        else:
            # 非阻擋且 guard 未過時,report.html 可能引用不存在的 query id(_check_referenced_
            # query_ids 沒過就是這個原因)——`report.ok` 為真時 guard 已保證這裡都存在,但
            # 非阻擋失敗時不能假設,用 `if query_id in results` 濾掉,避免 KeyError。
            referenced_results = {
                query_id: results[query_id]
                for query_id in referenced_query_ids(report.html)
                if query_id in results
            }
            # 主題不在此注入——交由 Java ArtifactAssembler 在 assemble 時統一注入 'erd'
            # 主題(head-inject.vm),避免同一份 HTML 出現兩份 registerTheme。
            final_html = inject_results(report.html, referenced_results)
            self._dashboard_html_emitted = True
            yield DashboardHtmlEvent(html=final_html)

    async def _build_evidence_text(
        self, *, dashboard_written_this_turn: bool, tool_invocations: list[str]
    ) -> str:
        """把 primitives 蒐集齊、交給 `app.agent.critic.build_evidence_text` 排版——判斷邏輯
        (什麼算「本輪」、query id 怎麼比對前後快照)留在這裡，critic 模組只管格式化。"""
        request = self._request
        all_results = load_all_results(self._workspace)
        queries_this_turn = {
            query_id: all_results[query_id].get("intent", "")
            for query_id in all_results
            if query_id not in self._query_ids_before
        }
        todos_summary = await _todos_summary(self._agent, self._run_config)
        return critic.build_evidence_text(
            dashboard_written_this_turn=dashboard_written_this_turn,
            dashboard_exists_from_previous_turns=self._dashboard_mtime_before is not None,
            is_editing_base_turn=request.previousDashboardHtml is not None,
            queries_this_turn=queries_this_turn,
            tool_invocations=tool_invocations,
            todos_summary=todos_summary,
        )

    async def finalize(
        self,
    ) -> AsyncIterable[StreamWireEvent | DashboardHtmlEvent | AnswerEvent]:
        request = self._request

        async for event in self._run_dashboard_section(self._dashboard_mtime_before):
            yield event
            if isinstance(event, ErrorEvent):
                return
        dashboard_html_emitted = self._dashboard_html_emitted
        dashboard_guard_failed = self._dashboard_guard_failed
        dashboard_written_this_turn = self._dashboard_section_changed

        # 刻意仍讀 pre-repair 的 `bridge`(非 `repair_bridge`):修復輪只透過 write_file 整份重寫
        # dashboard.html,不帶自己的說明文字,ANSWER 沿用原本分析輪的文字。
        final_answer_text = self.bridge.final_answer().strip()
        critic_inconsistent = False

        if _final_critic_enabled():
            evidence_text = await self._build_evidence_text(
                dashboard_written_this_turn=dashboard_written_this_turn,
                tool_invocations=self.bridge.tool_invocations,
            )
            verdict = await critic.run_final_critic(
                request.message, final_answer_text, evidence_text
            )
            if verdict is not None and not verdict.ok:
                yield StepEvent(stepKey="final_critic", title="覆核回答", status="RUNNING")
                corrective_bridge = EventBridge(self._recorder)
                corrective_input = {
                    "messages": [
                        HumanMessage(
                            build_critic_corrective_message(
                                verdict.issues,
                                verdict.fix_instruction,
                                dashboard_missing=not dashboard_written_this_turn,
                            )
                        )
                    ]
                }
                mtime_before_corrective = self._dashboard_mtime()
                async for wire_event in stream_agent_turn(
                    self._agent, corrective_input, self._run_config, corrective_bridge
                ):
                    yield wire_event
                    if isinstance(wire_event, ErrorEvent):
                        return

                async for event in self._run_dashboard_section(mtime_before_corrective):
                    yield event
                    if isinstance(event, ErrorEvent):
                        return
                if self._dashboard_section_changed:
                    dashboard_html_emitted = self._dashboard_html_emitted
                    dashboard_guard_failed = self._dashboard_guard_failed
                    dashboard_written_this_turn = True

                # 修正輪不一定產生新文字(例如只補了 write_file)——照 guard 修復輪的先例,
                # 空文字時沿用原本的 draft,不是每次修正都要換掉 ANSWER 文字。
                corrective_answer_text = corrective_bridge.final_answer().strip()
                if corrective_answer_text:
                    final_answer_text = corrective_answer_text

                second_evidence_text = await self._build_evidence_text(
                    dashboard_written_this_turn=dashboard_written_this_turn,
                    tool_invocations=self.bridge.tool_invocations
                    + corrective_bridge.tool_invocations,
                )
                second_verdict = await critic.run_final_critic(
                    request.message, final_answer_text, second_evidence_text
                )
                if second_verdict is not None and not second_verdict.ok:
                    critic_inconsistent = True

        if dashboard_guard_failed:
            # guard 終敗:模型文字可能仍在講「已完成」,那是假成功,用警示句戳破。獨立分支。
            answer_text = (
                f"{DASHBOARD_REJECTED_PREFIX}\n\n{final_answer_text}"
                if final_answer_text
                else DASHBOARD_REJECTED_PREFIX
            )
        elif critic_inconsistent:
            # final critic 修正輪跑完仍判不一致:獨立分支,不與 dashboard_guard_failed 合併
            # ——guard 是 HTML 本身沒過驗證,這裡是回答內容跟證據對不上,成因不同。
            answer_text = (
                f"{CRITIC_INCONSISTENT_PREFIX}\n\n{final_answer_text}"
                if final_answer_text
                else CRITIC_INCONSISTENT_PREFIX
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
