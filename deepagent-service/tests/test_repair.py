"""POST /repair — browser-error-driven single-call HTML fix (deepagent-service counterpart to
Java's AnalysisBrowserRepairClient / ArtifactRepairer analysis-mode path)."""

from httpx import ASGITransport, AsyncClient
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from app import main as main_module
from app.engine.results import record_query
from app.engine.workspace import LocalWorkspaceStore
from tests.test_chat import BROKEN_DASHBOARD_HTML_CONTENT, DASHBOARD_HTML_CONTENT


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
# artifact rawHtml (含 id 標記的 __ERD_RESULTS__/主題 script), as Java would forward it.
INJECTED_BROKEN_HTML = (
    '<html><head><script src="https://cdn.tailwindcss.com"></script>'
    '<script id="erd-results-data">window.__ERD_RESULTS__ = {"q1": '
    '{"columns": ["system"], "rows": [["CRM"]], "truncated": false}};</script>'
    '<script id="erd-theme">(function(){registerErdTheme();})();</script>'
    "</head><body>"
    '<div id="c"></div><script>window.__ERD_RESULTS__["q1"].boom();</script>'
    "</body></html>"
)


def _seed_workspace_with_q1(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path / "ws"))
    workspace = LocalWorkspaceStore(tmp_path / "ws").prepare("user-1", "sess-1")
    record_query(
        workspace,
        "q1",
        "SELECT system FROM orders",
        "各系統",
        ["system"],
        [["CRM"]],
        truncated=False,
    )


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


async def test_repair_success_roundtrip_injectsResultsAndTheme(tmp_path, monkeypatch) -> None:
    _seed_workspace_with_q1(tmp_path, monkeypatch)
    model = _RecordingChatModel([AIMessage(content=_fenced(DASHBOARD_HTML_CONTENT))])
    monkeypatch.setattr(main_module, "build_model", lambda: model)

    status_code, body = await _post_repair(["TypeError: x is undefined"])

    assert status_code == 200
    assert "window.__ERD_RESULTS__" in body["html"]
    assert "registerTheme('erd'" in body["html"]
    assert len(model.received_message_batches) == 1


async def test_repair_success_stripsInjectedBlocksBeforeSendingToModel(
    tmp_path, monkeypatch
) -> None:
    _seed_workspace_with_q1(tmp_path, monkeypatch)
    model = _RecordingChatModel([AIMessage(content=_fenced(DASHBOARD_HTML_CONTENT))])
    monkeypatch.setattr(main_module, "build_model", lambda: model)

    await _post_repair(["TypeError: x is undefined"], html=INJECTED_BROKEN_HTML)

    assert len(model.received_message_batches) == 1
    sent_messages = model.received_message_batches[0]
    sent_text = "\n".join(str(message.content) for message in sent_messages)
    assert 'id="erd-results-data"' not in sent_text
    assert 'id="erd-theme"' not in sent_text
    # The un-injected content the model needs to actually fix must still be present.
    assert 'window.__ERD_RESULTS__["q1"].boom()' in sent_text


async def test_repair_success_promptIncludesErrorMessage(tmp_path, monkeypatch) -> None:
    _seed_workspace_with_q1(tmp_path, monkeypatch)
    model = _RecordingChatModel([AIMessage(content=_fenced(DASHBOARD_HTML_CONTENT))])
    monkeypatch.setattr(main_module, "build_model", lambda: model)

    await _post_repair(["TypeError: boom is not a function"])

    sent_messages = model.received_message_batches[0]
    human_message_text = str(sent_messages[-1].content)
    assert "TypeError: boom is not a function" in human_message_text


# ── guard rejects both attempts → 422 ─────────────────────────────────────────


async def test_repair_guardFailsBothAttempts_returns422WithErrors(tmp_path, monkeypatch) -> None:
    _seed_workspace_with_q1(tmp_path, monkeypatch)
    # BROKEN_DASHBOARD_HTML_CONTENT references q9, which was never recorded -- a deterministic,
    # LLM-output-independent guard failure (missing referenced query id), so both the first call
    # and the retry fail identically without relying on scripted randomness.
    model = _RecordingChatModel(
        [
            AIMessage(content=_fenced(BROKEN_DASHBOARD_HTML_CONTENT)),
            AIMessage(content=_fenced(BROKEN_DASHBOARD_HTML_CONTENT)),
        ]
    )
    monkeypatch.setattr(main_module, "build_model", lambda: model)

    status_code, body = await _post_repair(["ReferenceError: x is not defined"])

    assert status_code == 422
    assert "errors" in body
    assert body["errors"]
    # Exactly one retry: the initial call plus REPAIR_GUARD_RETRY_MAX_RUNS(=1) more.
    assert len(model.received_message_batches) == 2


async def test_repair_guardFailsBothAttempts_retryMessageCarriesGuardErrors(
    tmp_path, monkeypatch
) -> None:
    _seed_workspace_with_q1(tmp_path, monkeypatch)
    model = _RecordingChatModel(
        [
            AIMessage(content=_fenced(BROKEN_DASHBOARD_HTML_CONTENT)),
            AIMessage(content=_fenced(BROKEN_DASHBOARD_HTML_CONTENT)),
        ]
    )
    monkeypatch.setattr(main_module, "build_model", lambda: model)

    await _post_repair(["ReferenceError: x is not defined"])

    retry_human_message = str(model.received_message_batches[1][-1].content)
    assert "previous fix failed" in retry_human_message.lower()
    assert "q9" in retry_human_message  # the missing-query-id guard error text


async def test_repair_guardPassesOnRetry_returns200(tmp_path, monkeypatch) -> None:
    _seed_workspace_with_q1(tmp_path, monkeypatch)
    model = _RecordingChatModel(
        [
            AIMessage(content=_fenced(BROKEN_DASHBOARD_HTML_CONTENT)),
            AIMessage(content=_fenced(DASHBOARD_HTML_CONTENT)),
        ]
    )
    monkeypatch.setattr(main_module, "build_model", lambda: model)

    status_code, body = await _post_repair(["ReferenceError: x is not defined"])

    assert status_code == 200
    assert "html" in body
    assert len(model.received_message_batches) == 2


# ── model call failure/timeout → 502 ──────────────────────────────────────────


async def test_repair_modelCallRaises_returns502(tmp_path, monkeypatch) -> None:
    _seed_workspace_with_q1(tmp_path, monkeypatch)

    class _FailingModel(_RecordingChatModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            raise RuntimeError("upstream connection reset")

    monkeypatch.setattr(main_module, "build_model", lambda: _FailingModel([]))

    status_code, body = await _post_repair(["TypeError: x is undefined"])

    assert status_code == 502
    assert "error" in body


async def test_repair_modelCallTimesOut_returns502(tmp_path, monkeypatch) -> None:
    _seed_workspace_with_q1(tmp_path, monkeypatch)
    monkeypatch.setattr(main_module, "REPAIR_MODEL_CALL_TIMEOUT_SECONDS", 0.01)

    class _SlowModel(_RecordingChatModel):
        async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
            import asyncio

            await asyncio.sleep(1)
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="too late"))])

    monkeypatch.setattr(main_module, "build_model", lambda: _SlowModel([]))

    status_code, body = await _post_repair(["TypeError: x is undefined"])

    assert status_code == 502
    assert "error" in body


# ── request shape: only `message` is required per error item ─────────────────


async def test_repair_errorItemOnlyRequiresMessage(tmp_path, monkeypatch) -> None:
    _seed_workspace_with_q1(tmp_path, monkeypatch)
    model = _RecordingChatModel([AIMessage(content=_fenced(DASHBOARD_HTML_CONTENT))])
    monkeypatch.setattr(main_module, "build_model", lambda: model)

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
