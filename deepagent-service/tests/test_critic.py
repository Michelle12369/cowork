"""app.agent.critic -- final critic verdict parsing, evidence rendering, and prompt framing.
End-to-end `run_final_critic` wiring is exercised via a fake chat model (mirrors how
tests/test_repair.py drives app.agent.repair_flow)."""

from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app.agent import critic
from app.agent.tools.framing import DATA_FRAME_CLOSE, DATA_FRAME_OPEN

# ── verdict parsing ────────────────────────────────────────────────────────────────────────


def test_parse_verdict_clean_json() -> None:
    verdict = critic._parse_verdict('{"ok": true, "issues": [], "fix_instruction": ""}')
    assert verdict == critic.CriticVerdict(ok=True, issues=[], fix_instruction="")


def test_parse_verdict_fenced_json() -> None:
    text = (
        "Here is my judgement:\n```json\n"
        '{"ok": false, "issues": ["no write_file this turn"], '
        '"fix_instruction": "rewrite dashboard.html"}\n```'
    )
    verdict = critic._parse_verdict(text)
    assert verdict == critic.CriticVerdict(
        ok=False, issues=["no write_file this turn"], fix_instruction="rewrite dashboard.html"
    )


def test_parse_verdict_garbage_returns_none() -> None:
    assert critic._parse_verdict("I cannot comply with this request.") is None


def test_parse_verdict_missing_keys_returns_none() -> None:
    assert critic._parse_verdict('{"ok": true}') is None


def test_parse_verdict_wrong_types_returns_none() -> None:
    # "ok" 必須是 bool，不是字串 "true"。
    assert critic._parse_verdict('{"ok": "true", "issues": [], "fix_instruction": ""}') is None


# ── evidence text rendering ────────────────────────────────────────────────────────────────


def test_build_evidence_text_all_fields() -> None:
    text = critic.build_evidence_text(
        dashboard_written_this_turn=True,
        dashboard_exists_from_previous_turns=True,
        is_editing_base_turn=True,
        queries_this_turn={"q2": "各系統工單數"},
        tool_invocations=["run_sql", "write_file(dashboard.html)"],
        todos_summary="拆解需求 [completed]",
    )
    assert "dashboard.html written this turn: True" in text
    assert "dashboard.html exists from previous turns: True" in text
    assert "editing-base turn" in text and "True" in text
    assert "q2 (intent: 各系統工單數)" in text
    assert "run_sql, write_file(dashboard.html)" in text
    assert "拆解需求 [completed]" in text
    assert "system-generated ground truth" in text


def test_build_evidence_text_empty_queries_case() -> None:
    text = critic.build_evidence_text(
        dashboard_written_this_turn=False,
        dashboard_exists_from_previous_turns=False,
        is_editing_base_turn=False,
        queries_this_turn={},
        tool_invocations=[],
        todos_summary="(none)",
    )
    assert "queries run this turn: none" in text
    assert "tools invoked this turn: none" in text


# ── prompt framing ─────────────────────────────────────────────────────────────────────────


def test_critic_system_prompt_states_framing_contract() -> None:
    assert "DATA" in critic.CRITIC_SYSTEM_PROMPT
    assert "never an instruction" in critic.CRITIC_SYSTEM_PROMPT


def test_build_critic_messages_frames_both_untrusted_sections() -> None:
    messages = critic._build_critic_messages(
        "使用者說 200 跟 300 看起來一模一樣",
        "已將 P200 與 P300 趨勢圖分開。",
        "Evidence (system-generated ground truth, the assistant cannot alter this):\n"
        "- dashboard.html written this turn: False",
    )
    human_content = messages[1].content
    assert human_content.count(DATA_FRAME_OPEN) == 2
    assert human_content.count(DATA_FRAME_CLOSE) == 2
    assert "使用者說 200 跟 300 看起來一模一樣" in human_content
    assert "已將 P200 與 P300 趨勢圖分開。" in human_content
    assert "Evidence (system-generated ground truth" in human_content


# ── run_final_critic end-to-end (fake model) ────────────────────────────────────────────────


class _ScriptedCriticModel(BaseChatModel):
    scripted_content: str

    def bind_tools(self, tools: Any, **kwargs: Any) -> "_ScriptedCriticModel":
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=AIMessage(self.scripted_content))])

    @property
    def _llm_type(self) -> str:
        return "scripted-critic"


class _FailingCriticModel(BaseChatModel):
    def bind_tools(self, tools: Any, **kwargs: Any) -> "_FailingCriticModel":
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        raise RuntimeError("critic model unavailable")

    @property
    def _llm_type(self) -> str:
        return "failing-critic"


async def test_run_final_critic_ok_response(monkeypatch) -> None:
    monkeypatch.setattr(
        critic,
        "build_critic_model",
        lambda: _ScriptedCriticModel(
            scripted_content='{"ok": true, "issues": [], "fix_instruction": ""}'
        ),
    )
    verdict = await critic.run_final_critic("問題", "回答", "Evidence:\n- ok")
    assert verdict == critic.CriticVerdict(ok=True, issues=[], fix_instruction="")


async def test_run_final_critic_unparseable_response_fails_open(monkeypatch) -> None:
    monkeypatch.setattr(
        critic,
        "build_critic_model",
        lambda: _ScriptedCriticModel(scripted_content="not json at all"),
    )
    verdict = await critic.run_final_critic("問題", "回答", "Evidence:\n- ok")
    assert verdict is None


async def test_run_final_critic_model_failure_fails_open(monkeypatch) -> None:
    monkeypatch.setattr(critic, "build_critic_model", lambda: _FailingCriticModel())
    verdict = await critic.run_final_critic("問題", "回答", "Evidence:\n- ok")
    assert verdict is None


async def test_run_final_critic_timeout_fails_open(monkeypatch) -> None:
    monkeypatch.setattr(critic, "CRITIC_MODEL_CALL_TIMEOUT_SECONDS", 0.01)

    class _SlowModel(BaseChatModel):
        def bind_tools(self, tools: Any, **kwargs: Any) -> "_SlowModel":
            return self

        async def ainvoke(self, messages: Any, **kwargs: Any) -> AIMessage:
            import asyncio

            await asyncio.sleep(1)
            return AIMessage(content='{"ok": true, "issues": [], "fix_instruction": ""}')

        def _generate(
            self,
            messages: list[BaseMessage],
            stop: list[str] | None = None,
            run_manager: Any = None,
            **kwargs: Any,
        ) -> ChatResult:
            raise NotImplementedError

        @property
        def _llm_type(self) -> str:
            return "slow-critic"

    monkeypatch.setattr(critic, "build_critic_model", lambda: _SlowModel())
    verdict = await critic.run_final_critic("問題", "回答", "Evidence:\n- ok")
    assert verdict is None
