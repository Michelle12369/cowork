"""POST /repair — browser-error-driven single-call HTML fix (deepagent-service counterpart to
Java's AnalysisBrowserRepairClient / ArtifactRepairer analysis-mode path)."""

from httpx import ASGITransport, AsyncClient
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from app import main as main_module
from app.agent import repair_flow
from app.engine.results import record_query
from app.engine.workspace_store import build_workspace_store
from tests.test_chat import BROKEN_DASHBOARD_HTML_CONTENT, DASHBOARD_HTML_CONTENT


class _CleanupTrackingStore:
    """包一個真的 store(local 模式現在也是 WorkspaceStore),只加 cleanup_scratch() 呼叫
    計數——驗證 run_repair 的 try/finally 在成功與模型呼叫失敗兩種結果下都會清 per-turn
    scratch(/repair 只 prepare 不 persist,s3 模式下不會有其他人幫忙清)。"""

    def __init__(self, delegate) -> None:
        self._delegate = delegate
        self.cleanup_scratch_calls = 0

    def prepare(self, user_id: str, session_id: str):
        return self._delegate.prepare(user_id, session_id)

    def persist(self, workspace) -> None:
        self._delegate.persist(workspace)

    def cleanup_scratch(self) -> None:
        self.cleanup_scratch_calls += 1


class _RecordingChatModel(BaseChatModel):
    """Like tests.fake_model.ScriptedChatModel, but also records every message batch it was
    invoked with -- needed to assert the model never sees the injected __ERD_RESULTS__/theme
    <script> blocks (strip_injected_blocks effectiveness)."""

    scripted_messages: list[AIMessage]
    received_message_batches: list[list[BaseMessage]] = Field(default_factory=list)

    def __init__(self, scripted_messages: list[AIMessage], **kwargs: object) -> None:
        super().__init__(scripted_messages=list(scripted_messages), **kwargs)

    def bind_tools(self, tools: object, **kwargs: object) -> "_RecordingChatModel":
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: object = None,
        **kwargs: object,
    ) -> ChatResult:
        self.received_message_batches.append(list(messages))
        if self.scripted_messages:
            message = self.scripted_messages.pop(0)
        else:
            message = AIMessage(content="")
        return ChatResult(generations=[ChatGeneration(message=message)])

    @property
    def _llm_type(self) -> str:
        return "recording"


def _fenced(html: str) -> str:
    return f"```html\n{html}\n```"


# Mirrors test_chat.py's PREVIOUS_VERSION_DASHBOARD_HTML_CONTENT shape: an already-injected
# artifact rawHtml (含 id 標記的 __ERD_RESULTS__ script), as Java would forward it.
INJECTED_BROKEN_HTML = (
    '<html><head><script src="https://cdn.tailwindcss.com"></script>'
    '<script id="erd-results-data">window.__ERD_RESULTS__ = {"q1": '
    '{"columns": ["system"], "rows": [["CRM"]], "truncated": false}};</script>'
    "</head><body>"
    '<div id="c"></div><script>window.__ERD_RESULTS__["q1"].boom();</script>'
    "</body></html>"
)


def _seed_workspace_with_q1(tmp_path, monkeypatch) -> None:
    """local 模式現在也走 WorkspaceStore 的 generation 快照模型——先 prepare+persist 一輪,
    讓 run_repair 內部的 build_workspace_store().prepare() 能拉到這份 q1 結果,而不是直寫
    session 目錄(persist 之前對讀方不可見)。"""
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path / "ws"))
    store = build_workspace_store()
    workspace = store.prepare("user-1", "sess-1")
    record_query(
        workspace,
        "q1",
        "SELECT system FROM orders",
        "各系統",
        ["system"],
        [["CRM"]],
        truncated=False,
    )
    store.persist(workspace)


async def _post_repair(errors: list[str], html: str = INJECTED_BROKEN_HTML) -> tuple[int, dict]:
    payload = {
        "sessionId": "sess-1",
        "userId": "user-1",
        "html": html,
        "errors": [{"message": message} for message in errors],
    }
    transport = ASGITransport(app=main_module.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/repair", json=payload)
    return response.status_code, response.json()


# ── success roundtrip ─────────────────────────────────────────────────────────


# ── error line quoting ────────────────────────────────────────────────────────


MULTILINE_INJECTED_HTML = (
    "<html><head>\n"
    '<script id="erd-results-data">window.__ERD_RESULTS__ = {"q1": '
    '{"columns": ["system"], "rows": [{"system": "CRM"}], "truncated": false}};</script>\n'
    "</head><body>\n"
    '<div id="c"></div>\n'
    "<script>\n"
    'const first = window.__ERD_RESULTS__["q1"].rows[0];\n'
    "first.boom();\n"
    "</script>\n"
    "</body></html>"
)


def test_describe_errors_quotes_source_line_present_in_clean_html() -> None:
    """Java 抽出的 sourceLine 存在於骨架 → 附上引用,模型可全文搜尋定位。"""
    from app.api.schemas import RepairErrorItem
    from app.engine.results import strip_injected_blocks

    clean_html = strip_injected_blocks(MULTILINE_INJECTED_HTML)
    described = repair_flow._describe_errors(
        clean_html,
        [RepairErrorItem(
            message="TypeError: first.boom is not a function",
            line=7,
            sourceLine="first.boom();",
        )],
    )
    assert described == ["TypeError: first.boom is not a function (at: `first.boom();`)"]


def test_describe_errors_skips_source_line_absent_from_skeleton() -> None:
    """sourceLine 落在注入區塊(骨架裡不存在)→ 引用只會誤導,退回純 message。"""
    from app.api.schemas import RepairErrorItem
    from app.engine.results import strip_injected_blocks

    clean_html = strip_injected_blocks(MULTILINE_INJECTED_HTML)
    described = repair_flow._describe_errors(
        clean_html,
        [RepairErrorItem(
            message='Error: [ERD] q1 row has no column "boom"',
            line=2,
            sourceLine="throw new Error('[ERD] ' + queryId + ' row has no column",
        )],
    )
    assert described == ['Error: [ERD] q1 row has no column "boom"']


def test_describe_errors_empty_source_line_passes_through() -> None:
    """sourceLine 空(Java 端行號未知/超界)→ 純 message,行號本身不再被 Python 使用。"""
    from app.api.schemas import RepairErrorItem
    from app.engine.results import strip_injected_blocks

    clean_html = strip_injected_blocks(MULTILINE_INJECTED_HTML)
    described = repair_flow._describe_errors(
        clean_html,
        [RepairErrorItem(message="a"), RepairErrorItem(message="b", line=999)],
    )
    assert described == ["a", "b"]


async def test_repair_prompt_carries_quoted_error_line(tmp_path, monkeypatch) -> None:
    """端到端:帶行號的錯誤進 /repair,模型收到的 prompt MUST 含行原文引用。"""
    _seed_workspace_with_q1(tmp_path, monkeypatch)
    model = _RecordingChatModel([AIMessage(content=_fenced(DASHBOARD_HTML_CONTENT))])
    monkeypatch.setattr(repair_flow, "build_model", lambda: model)

    payload = {
        "sessionId": "sess-1",
        "userId": "user-1",
        "html": MULTILINE_INJECTED_HTML,
        "errors": [{
            "message": "TypeError: first.boom is not a function",
            "line": 7,
            "col": 1,
            "sourceLine": "first.boom();",
        }],
    }
    transport = ASGITransport(app=main_module.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/repair", json=payload)

    assert response.status_code == 200
    human_message = model.received_message_batches[0][1]
    assert "(at: `first.boom();`)" in human_message.content


async def test_repair_model_call_fires_langfuse_callbacks(tmp_path, monkeypatch) -> None:
    """callbacks MUST 真的掛上模型呼叫(on_chat_model_start 被觸發),不是只建構不傳遞——
    測試若只斷言 _build_callbacks 有被呼叫,接線斷了也照樣綠。"""
    from langchain_core.callbacks import BaseCallbackHandler

    class _CountingHandler(BaseCallbackHandler):
        def __init__(self) -> None:
            self.chat_model_start_count = 0

        def on_chat_model_start(self, serialized, messages, **kwargs):
            self.chat_model_start_count += 1

    handler = _CountingHandler()
    _seed_workspace_with_q1(tmp_path, monkeypatch)
    model = _RecordingChatModel([AIMessage(content=_fenced(DASHBOARD_HTML_CONTENT))])
    monkeypatch.setattr(repair_flow, "build_model", lambda: model)
    monkeypatch.setattr(repair_flow, "_build_callbacks", lambda: [handler])

    status_code, _ = await _post_repair(["TypeError: x is undefined"])

    assert status_code == 200
    assert handler.chat_model_start_count == 1


async def test_repair_success_injectsResults(tmp_path, monkeypatch) -> None:
    _seed_workspace_with_q1(tmp_path, monkeypatch)
    model = _RecordingChatModel([AIMessage(content=_fenced(DASHBOARD_HTML_CONTENT))])
    monkeypatch.setattr(repair_flow, "build_model", lambda: model)

    status_code, body = await _post_repair(["TypeError: x is undefined"])

    assert status_code == 200
    assert "window.__ERD_RESULTS__" in body["html"]
    # Theme injection now happens in the Java backend, not in the deepagent.
    assert "registerTheme('erd'" not in body["html"]
    assert len(model.received_message_batches) == 1


async def test_repair_success_stripsInjectedBlocksBeforeSendingToModel(
    tmp_path, monkeypatch
) -> None:
    _seed_workspace_with_q1(tmp_path, monkeypatch)
    model = _RecordingChatModel([AIMessage(content=_fenced(DASHBOARD_HTML_CONTENT))])
    monkeypatch.setattr(repair_flow, "build_model", lambda: model)

    await _post_repair(["TypeError: x is undefined"], html=INJECTED_BROKEN_HTML)

    assert len(model.received_message_batches) == 1
    sent_messages = model.received_message_batches[0]
    sent_text = "\n".join(str(message.content) for message in sent_messages)
    assert 'id="erd-results-data"' not in sent_text
    # The un-injected content the model needs to actually fix must still be present.
    assert 'window.__ERD_RESULTS__["q1"].boom()' in sent_text


async def test_repair_success_promptIncludesErrorMessage(tmp_path, monkeypatch) -> None:
    _seed_workspace_with_q1(tmp_path, monkeypatch)
    model = _RecordingChatModel([AIMessage(content=_fenced(DASHBOARD_HTML_CONTENT))])
    monkeypatch.setattr(repair_flow, "build_model", lambda: model)

    await _post_repair(["TypeError: boom is not a function"])

    sent_messages = model.received_message_batches[0]
    human_message_text = str(sent_messages[-1].content)
    assert "TypeError: boom is not a function" in human_message_text


# ── no validation layer: a candidate that used to fail the guard now just ships ──────────


async def test_repair_candidateReferencingMissingQueryId_stillShips_returns200(
    tmp_path, monkeypatch
) -> None:
    """BROKEN_DASHBOARD_HTML_CONTENT references q9, which was never recorded -- the old guard
    rejected this deterministically. There's no validation layer anymore: the candidate ships
    as-is, single call only, and the missing id is simply absent from the injected results."""
    _seed_workspace_with_q1(tmp_path, monkeypatch)
    model = _RecordingChatModel([AIMessage(content=_fenced(BROKEN_DASHBOARD_HTML_CONTENT))])
    monkeypatch.setattr(repair_flow, "build_model", lambda: model)

    status_code, body = await _post_repair(["ReferenceError: x is not defined"])

    assert status_code == 200
    assert "html" in body
    # q9 was never recorded -- filtered out of the injected results payload (no `"q9":` key),
    # even though the candidate markup still references it via window.__ERD_RESULTS__["q9"].
    assert '"q9":' not in body["html"]
    assert 'window.__ERD_RESULTS__["q9"]' in body["html"]
    # No retry -- a single model call regardless of what the candidate looks like.
    assert len(model.received_message_batches) == 1


# ── model call failure/timeout → 502 ──────────────────────────────────────────


async def test_repair_modelCallRaises_returns502(tmp_path, monkeypatch) -> None:
    _seed_workspace_with_q1(tmp_path, monkeypatch)

    class _FailingModel(_RecordingChatModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            raise RuntimeError("upstream connection reset")

    monkeypatch.setattr(repair_flow, "build_model", lambda: _FailingModel([]))

    status_code, body = await _post_repair(["TypeError: x is undefined"])

    assert status_code == 502
    assert "error" in body


async def test_repair_modelCallTimesOut_returns502(tmp_path, monkeypatch) -> None:
    _seed_workspace_with_q1(tmp_path, monkeypatch)
    monkeypatch.setattr(repair_flow, "REPAIR_MODEL_CALL_TIMEOUT_SECONDS", 0.01)

    class _SlowModel(_RecordingChatModel):
        async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
            import asyncio

            await asyncio.sleep(1)
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="too late"))])

    monkeypatch.setattr(repair_flow, "build_model", lambda: _SlowModel([]))

    status_code, body = await _post_repair(["TypeError: x is undefined"])

    assert status_code == 502
    assert "error" in body


# ── request shape: only `message` is required per error item ─────────────────


async def test_repair_errorItemOnlyRequiresMessage(tmp_path, monkeypatch) -> None:
    _seed_workspace_with_q1(tmp_path, monkeypatch)
    model = _RecordingChatModel([AIMessage(content=_fenced(DASHBOARD_HTML_CONTENT))])
    monkeypatch.setattr(repair_flow, "build_model", lambda: model)

    payload = {
        "sessionId": "sess-1",
        "userId": "user-1",
        "html": INJECTED_BROKEN_HTML,
        "errors": [{"message": "TypeError: x is undefined"}],
    }
    transport = ASGITransport(app=main_module.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/repair", json=payload)

    assert response.status_code == 200


# ── run_repair 清 per-turn scratch(只 prepare 不 persist,終審修正點)───────────


async def test_repair_success_calls_cleanup_scratch(tmp_path, monkeypatch) -> None:
    _seed_workspace_with_q1(tmp_path, monkeypatch)
    model = _RecordingChatModel([AIMessage(content=_fenced(DASHBOARD_HTML_CONTENT))])
    monkeypatch.setattr(repair_flow, "build_model", lambda: model)
    tracking_store = _CleanupTrackingStore(build_workspace_store())
    monkeypatch.setattr(repair_flow, "build_workspace_store", lambda: tracking_store)

    status_code, _body = await _post_repair(["TypeError: x is undefined"])

    assert status_code == 200
    assert tracking_store.cleanup_scratch_calls == 1


async def test_repair_modelCallFails_stillCallsCleanupScratch(tmp_path, monkeypatch) -> None:
    _seed_workspace_with_q1(tmp_path, monkeypatch)

    class _FailingModel(_RecordingChatModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            raise RuntimeError("upstream connection reset")

    monkeypatch.setattr(repair_flow, "build_model", lambda: _FailingModel([]))
    tracking_store = _CleanupTrackingStore(build_workspace_store())
    monkeypatch.setattr(repair_flow, "build_workspace_store", lambda: tracking_store)

    status_code, _body = await _post_repair(["TypeError: x is undefined"])

    assert status_code == 502
    # 模型呼叫失敗是 run_repair 內部一個 early return -- try/finally MUST 仍然清 scratch。
    assert tracking_store.cleanup_scratch_calls == 1
