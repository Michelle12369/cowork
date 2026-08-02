from app.agent.tools.clarify import (
    ASK_USER_TOOL_RESULT,
    ClarifyQuestion,
    QuestionHolder,
    build_ask_user_tool,
)


def _question(text: str) -> dict:
    return {"text": text, "options": ["選項A", "選項B"], "multi_select": False}


def test_ask_user_records_questions_and_returns_stop_instruction() -> None:
    holder = QuestionHolder()
    ask_user = build_ask_user_tool(holder)

    result = ask_user.invoke({"questions": [_question("想分析哪個指標?")]})

    assert result == ASK_USER_TOOL_RESULT
    recorded = holder.questions()
    assert len(recorded) == 1
    assert recorded[0].text == "想分析哪個指標?"
    assert recorded[0].options == ["選項A", "選項B"]
    assert recorded[0].multi_select is False


def test_holder_caps_total_questions_at_three() -> None:
    holder = QuestionHolder()
    ask_user = build_ask_user_tool(holder)

    ask_user.invoke({"questions": [_question("Q1"), _question("Q2")]})
    ask_user.invoke({"questions": [_question("Q3"), _question("Q4")]})

    assert [question.text for question in holder.questions()] == ["Q1", "Q2", "Q3"]


def test_to_wire_uses_camel_case_multi_select() -> None:
    question = ClarifyQuestion(text="範圍?", options=["全部"], multi_select=True)
    assert question.to_wire() == {"text": "範圍?", "options": ["全部"], "multiSelect": True}


def test_options_default_to_empty_list() -> None:
    question = ClarifyQuestion(text="請說明需求")
    assert question.options == []
    assert question.to_wire()["options"] == []
