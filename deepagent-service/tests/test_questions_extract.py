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


def test_json_tagged_fence_with_questions_shape_is_parsed_and_stripped() -> None:
    """誤標 ```json fence 救回:內容形狀像反問區塊時視同 ```questions 區塊處理。"""
    answer_text = (
        "開始分析前想先確認幾件事：\n"
        "```json\n"
        '[{"text": "要看哪個時間範圍?", "options": ["近7天", "近30天"], "multiSelect": false}]\n'
        "```"
    )
    stripped_text, questions = extract_questions_block(answer_text)

    assert stripped_text == "開始分析前想先確認幾件事："
    assert questions == [
        {"text": "要看哪個時間範圍?", "options": ["近7天", "近30天"], "multiSelect": False}
    ]


def test_bare_fence_with_questions_shape_is_parsed() -> None:
    """裸 fence(無語言標籤)內容形狀像反問區塊時同樣救回。"""
    answer_text = '```\n[{"text": "選哪個系統?", "options": [], "multiSelect": false}]\n```'
    stripped_text, questions = extract_questions_block(answer_text)

    assert stripped_text == ""
    assert questions == [{"text": "選哪個系統?", "options": [], "multiSelect": False}]


def test_json_tagged_fence_array_without_text_field_left_untouched() -> None:
    """陣列元素缺 text 鍵(例如資料列陣列)——形狀不合,原樣留在文字裡不被吃掉。"""
    answer_text = '```json\n[{"quantity": 12, "defect_count": 3}]\n```'
    stripped_text, questions = extract_questions_block(answer_text)

    assert stripped_text == answer_text
    assert questions is None


def test_json_tagged_fence_non_array_object_left_untouched() -> None:
    """非陣列 payload(例如 ECharts option object)——形狀不合,原樣留在文字裡不被吃掉。"""
    answer_text = '```json\n{"series": [{"type": "bar", "data": [1, 2, 3]}]}\n```'
    stripped_text, questions = extract_questions_block(answer_text)

    assert stripped_text == answer_text
    assert questions is None


def test_questions_fence_wins_over_earlier_json_tagged_questions_shaped_fence() -> None:
    """同時存在誤標與正確標記的區塊時,正確標記的 ```questions fence 優先。"""
    answer_text = (
        "```json\n"
        '[{"text": "誤標區塊的問題?", "options": [], "multiSelect": false}]\n'
        "```\n"
        "```questions\n"
        '[{"text": "正確標記的問題?", "options": [], "multiSelect": false}]\n'
        "```"
    )
    stripped_text, questions = extract_questions_block(answer_text)

    assert questions == [{"text": "正確標記的問題?", "options": [], "multiSelect": False}]
    assert '[{"text": "誤標區塊的問題?"' in stripped_text


def test_prose_questions_with_no_fence_left_untouched() -> None:
    """完全沒有 fence 的散文提問——維持既有行為,不解析、不改動文字。"""
    answer_text = "想先確認一下，你要看哪個時間範圍？近7天還是近30天呢？"
    stripped_text, questions = extract_questions_block(answer_text)

    assert stripped_text == answer_text
    assert questions is None


def test_malformed_questions_fence_blocks_fallback_to_shaped_json_fence():
    """正確標記但內容壞掉的 ```questions fence 直接定案為無反問——不落入 priority-2,
    即使後面有形狀合法的 ```json fence 也不救回(docstring 明定的 no-fallback 行為)。"""
    answer_text = (
        "前置說明\n```questions\n{broken json[\n```\n中間文字\n"
        '```json\n[{"text": "想看哪個欄位？", "options": ["a"], "multiSelect": false}]\n```\n結尾'
    )
    remaining_text, questions = extract_questions_block(answer_text)
    assert remaining_text == answer_text
    assert questions is None
