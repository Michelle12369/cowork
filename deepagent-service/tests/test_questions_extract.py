from app.engine.questions_extract import extract_questions_block


def test_valid_block_returns_stripped_text_and_parsed_questions() -> None:
    answer_text = (
        "請確認以下問題:\n"
        "```questions\n"
        '[{"text": "要看哪個時間範圍?", "options": ["近7天", "近30天"], "multiSelect": false}]\n'
        "```"
    )
    stripped_text, questions = extract_questions_block(answer_text)

    assert stripped_text == "請確認以下問題:"
    assert questions == [
        {"text": "要看哪個時間範圍?", "options": ["近7天", "近30天"], "multiSelect": False}
    ]


def test_surrounding_prose_before_and_after_block_preserved() -> None:
    answer_text = (
        "前言文字。\n"
        "```questions\n"
        '[{"text": "選哪個系統?", "options": [], "multiSelect": false}]\n'
        "```\n"
        "後記文字。"
    )
    stripped_text, questions = extract_questions_block(answer_text)

    assert stripped_text == "前言文字。\n\n後記文字。"
    assert questions is not None


def test_malformed_json_returns_unchanged_text_and_none() -> None:
    answer_text = "```questions\nnot json at all\n```"
    stripped_text, questions = extract_questions_block(answer_text)

    assert stripped_text == answer_text
    assert questions is None


def test_no_block_returns_unchanged_text_and_none() -> None:
    answer_text = "沒有任何反問區塊的一般回答。"
    stripped_text, questions = extract_questions_block(answer_text)

    assert stripped_text == answer_text
    assert questions is None


def test_only_first_of_two_blocks_parsed() -> None:
    answer_text = (
        "```questions\n"
        '[{"text": "第一個問題?", "options": [], "multiSelect": false}]\n'
        "```\n"
        "```questions\n"
        '[{"text": "第二個問題?", "options": [], "multiSelect": false}]\n'
        "```"
    )
    stripped_text, questions = extract_questions_block(answer_text)

    assert questions == [{"text": "第一個問題?", "options": [], "multiSelect": False}]
    # 只有第一個區塊被移除;第二個區塊(含其 fence)原樣留在剩餘文字裡,未被觸碰。
    assert stripped_text == (
        '```questions\n[{"text": "第二個問題?", "options": [], "multiSelect": false}]\n```'
    )


def test_missing_text_field_makes_whole_block_invalid() -> None:
    answer_text = '```questions\n[{"options": ["A", "B"], "multiSelect": false}]\n```'
    stripped_text, questions = extract_questions_block(answer_text)

    assert stripped_text == answer_text
    assert questions is None


def test_empty_text_field_makes_whole_block_invalid() -> None:
    answer_text = '```questions\n[{"text": "   ", "options": [], "multiSelect": false}]\n```'
    stripped_text, questions = extract_questions_block(answer_text)

    assert stripped_text == answer_text
    assert questions is None


def test_options_and_multi_select_defaults_applied_when_absent() -> None:
    answer_text = '```questions\n[{"text": "要繼續嗎?"}]\n```'
    _, questions = extract_questions_block(answer_text)

    assert questions == [{"text": "要繼續嗎?", "options": [], "multiSelect": False}]


def test_non_list_options_and_non_bool_multi_select_coerced_to_defaults() -> None:
    answer_text = (
        '```questions\n[{"text": "要繼續嗎?", "options": "not a list", "multiSelect": "yes"}]\n```'
    )
    _, questions = extract_questions_block(answer_text)

    assert questions == [{"text": "要繼續嗎?", "options": [], "multiSelect": False}]


def test_non_list_json_payload_is_invalid() -> None:
    answer_text = '```questions\n{"text": "not wrapped in an array"}\n```'
    stripped_text, questions = extract_questions_block(answer_text)

    assert stripped_text == answer_text
    assert questions is None


def test_empty_array_payload_is_invalid() -> None:
    answer_text = "```questions\n[]\n```"
    stripped_text, questions = extract_questions_block(answer_text)

    assert stripped_text == answer_text
    assert questions is None


def test_deeply_nested_array_payload_does_not_raise_recursion_error() -> None:
    """CPython json 解析器對巢狀陣列採遞迴下降;60000 層深度會觸發 RecursionError——
    never-raise 契約下 MUST 視同解析失敗,回傳原文字與 None,而非往上拋例外。"""
    answer_text = "```questions\n" + "[" * 60000 + "]" * 60000 + "\n```"
    stripped_text, questions = extract_questions_block(answer_text)

    assert stripped_text == answer_text
    assert questions is None


def test_oversized_number_literal_does_not_raise_value_error() -> None:
    """超過 4300 位數的數字字面量在轉換為 int 時觸發 ValueError(CPython 整數字串轉換
    位數上限防護)——never-raise 契約下 MUST 視同解析失敗,回傳原文字與 None。"""
    answer_text = '```questions\n[{"text": ' + "9" * 5000 + "}]\n```"
    stripped_text, questions = extract_questions_block(answer_text)

    assert stripped_text == answer_text
    assert questions is None
