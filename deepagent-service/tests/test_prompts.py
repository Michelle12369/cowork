from app.agent.prompts import SYSTEM_PROMPT, build_sources_manifest_note
from app.engine.source_manifest import SchemaChange, SourcesDiff


def test_build_sources_manifest_note_added_only() -> None:
    diff = SourcesDiff(added=("usage_log",), removed=(), version_changed=(), schema_changed=())
    note = build_sources_manifest_note(diff)
    assert "Added: `usage_log`." in note
    assert "Removed:" not in note
    assert "Re-uploaded" not in note
    assert "Schema changed" not in note
    assert "Call get_schema" in note


def test_build_sources_manifest_note_removed_only() -> None:
    diff = SourcesDiff(added=(), removed=("old_data",), version_changed=(), schema_changed=())
    note = build_sources_manifest_note(diff)
    assert "Removed: `old_data`." in note
    assert "Added:" not in note


def test_build_sources_manifest_note_version_changed_only() -> None:
    diff = SourcesDiff(added=(), removed=(), version_changed=("orders",), schema_changed=())
    note = build_sources_manifest_note(diff)
    assert "Re-uploaded with possibly different content: `orders`." in note
    assert "Added:" not in note
    assert "Schema changed" not in note


def test_build_sources_manifest_note_schema_changed_only() -> None:
    schema_change = SchemaChange(
        alias="orders",
        added_columns=("region",),
        removed_columns=("old_col",),
        type_changed_columns=("tickets",),
    )
    diff = SourcesDiff(added=(), removed=(), version_changed=(), schema_changed=(schema_change,))
    note = build_sources_manifest_note(diff)
    assert "Schema changed for `orders`:" in note
    assert "added columns `region`" in note
    assert "removed columns `old_col`" in note
    assert "changed type for `tickets`" in note
    assert "Re-uploaded" not in note


def test_build_sources_manifest_note_combined_sentence_groups() -> None:
    schema_change = SchemaChange(
        alias="usage_log",
        added_columns=("region",),
        removed_columns=(),
        type_changed_columns=(),
    )
    diff = SourcesDiff(
        added=("new_source",),
        removed=("old_source",),
        version_changed=("orders",),
        schema_changed=(schema_change,),
    )
    note = build_sources_manifest_note(diff)
    assert "Added: `new_source`." in note
    assert "Removed: `old_source`." in note
    assert "Re-uploaded with possibly different content: `orders`." in note
    assert "Schema changed for `usage_log`: added columns `region`." in note
    assert "Call get_schema to refresh the table structures before answering." in note


def test_system_prompt_contains_ambiguity_check_guidance():
    assert "Ambiguity check" in SYSTEM_PROMPT
    assert "WHICH COLUMN(S) to analyze" in SYSTEM_PROMPT
    assert "WHICH CHART TYPE" in SYSTEM_PROMPT
    assert "由系統依資料特性建議" in SYSTEM_PROMPT


def test_system_prompt_questions_fence_rule_is_exact():
    assert "EXACTLY `questions`" in SYSTEM_PROMPT
    assert '```questions\n[{"text": "想分析哪個欄位？"' in SYSTEM_PROMPT


def test_system_prompt_channel_is_universal_and_bans_plain_prose_questions():
    assert "ANY question to the user" in SYSTEM_PROMPT
    assert "NEVER write a question to the user as plain reply text" in SYSTEM_PROMPT
    assert "protocol violation" in SYSTEM_PROMPT


def test_system_prompt_standard_clarifications_are_downgraded_to_examples():
    assert "Typical clarifications include (not limited to):" in SYSTEM_PROMPT


def test_system_prompt_treats_fully_generic_request_as_must_ask():
    assert "fully generic request" in SYSTEM_PROMPT
    assert "根據資料特性產生適合的圖表並提供 insight" in SYSTEM_PROMPT
    assert "do NOT ask chart type" in SYSTEM_PROMPT
