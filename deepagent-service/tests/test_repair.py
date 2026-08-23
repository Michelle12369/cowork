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


async def test_repair_empty_model_response_returns_502_and_no_html(tmp_path, monkeypatch) -> None:
    """模型回空字串 → MUST 視同修復失敗(502),絕不回空 html 讓 Java 把 dashboard 清空。"""
    _seed_workspace_with_q1(tmp_path, monkeypatch)
    model = _RecordingChatModel([AIMessage(content="")])
    monkeypatch.setattr(repair_flow, "build_model", lambda: model)

    status_code, body = await _post_repair(["TypeError: x is undefined"])

    assert status_code == 502
    assert "html" not in body or not body.get("html")


async def test_repair_whitespace_only_fence_returns_502(tmp_path, monkeypatch) -> None:
    model = _RecordingChatModel([AIMessage(content=_fenced("   \n  "))])
    monkeypatch.setattr(repair_flow, "build_model", lambda: model)
    _seed_workspace_with_q1(tmp_path, monkeypatch)

    status_code, _ = await _post_repair(["TypeError: x is undefined"])

    assert status_code == 502


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
    # Repair must also re-inject the bind resolver -- without it, a repaired dashboard's
    # [data-bind] elements would render nothing at all (not even the "—" fallback).
    assert 'id="erd-bind-resolver"' in body["html"]
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


# ── results contract guard: a candidate breaking __ERD_RESULTS__ access fails the repair ──


async def test_repair_candidateReferencingMissingQueryId_failsResultsContract_returns502(
    tmp_path, monkeypatch
) -> None:
    """BROKEN_DASHBOARD_HTML_CONTENT references q9, which was never recorded -- results_guard's
    R2 rule now rejects this deterministically before injection, same failure semantics as an
    empty candidate (model_call_failed=True -> 502, no html shipped)."""
    _seed_workspace_with_q1(tmp_path, monkeypatch)
    model = _RecordingChatModel([AIMessage(content=_fenced(BROKEN_DASHBOARD_HTML_CONTENT))])
    monkeypatch.setattr(repair_flow, "build_model", lambda: model)

    status_code, body = await _post_repair(["ReferenceError: x is not defined"])

    assert status_code == 502
    assert "html" not in body or not body.get("html")
    # No retry -- a single model call regardless of what the candidate looks like.
    assert len(model.received_message_batches) == 1


async def test_repair_candidateWithStubAssignment_failsResultsContract_returns502(
    tmp_path, monkeypatch
) -> None:
    """Regression fixture modeled on the incident's repair-round fix attempt: the model "fixes"
    a crash by adding a `window.__ERD_RESULTS__ = {...}` stub assignment -- results_guard's R1
    rule rejects this (never assign/stub the injected object)."""
    stub_html = (
        '<html><head><script src="https://cdn.tailwindcss.com"></script></head>'
        '<body><div id="c"></div><script>'
        'window.__ERD_RESULTS__ = {"q1": {"columns": ["system"], "rows": [], "truncated": false}};'
        'const table = window.__ERD_RESULTS__["q1"];'
        "echarts.init(document.getElementById('c'), 'erd');"
        "</script></body></html>"
    )
    _seed_workspace_with_q1(tmp_path, monkeypatch)
    model = _RecordingChatModel([AIMessage(content=_fenced(stub_html))])
    monkeypatch.setattr(repair_flow, "build_model", lambda: model)

    status_code, body = await _post_repair(["TypeError: x is undefined"])

    assert status_code == 502
    assert "html" not in body or not body.get("html")


async def test_repair_candidateWithUnknownBindColumn_failsResultsContract_returns502(
    tmp_path, monkeypatch
) -> None:
    """R5 regression: q1 is real (seeded with only the "system" column), but the candidate's
    data-bind attribute references a column that was never selected. This only fails if run_repair
    actually threads `available_columns` (built from `load_all_results`) into
    validate_results_contract -- pins that wiring, not just the results_guard unit test."""
    unknown_bind_column_html = (
        '<html><head><script src="https://cdn.tailwindcss.com"></script></head>'
        '<body><div id="c" data-bind="q1.bogus_column"></div><script>'
        'const table = window.__ERD_RESULTS__["q1"];'
        "echarts.init(document.getElementById('c'), 'erd');"
        "</script></body></html>"
    )
    _seed_workspace_with_q1(tmp_path, monkeypatch)
    model = _RecordingChatModel([AIMessage(content=_fenced(unknown_bind_column_html))])
    monkeypatch.setattr(repair_flow, "build_model", lambda: model)

    status_code, body = await _post_repair(["TypeError: x is undefined"])

    assert status_code == 502
    assert "html" not in body or not body.get("html")


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
