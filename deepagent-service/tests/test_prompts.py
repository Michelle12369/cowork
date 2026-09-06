from app.agent.prompts import (
    CONNECTOR_MODE_SYSTEM_SECTION,
    CONNECTOR_TABLES_RESET_NOTE,
    SYSTEM_PROMPT,
    build_sources_manifest_note,
)
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


def test_connector_mode_system_section_has_no_land_as_and_describes_auto_landing() -> None:
    """`land_as` 已拆除——connector 模式改成每次呼叫自動落表,静態段須改講這件事。"""
    assert "land_as" not in CONNECTOR_MODE_SYSTEM_SECTION
    assert "automatically lands" in CONNECTOR_MODE_SYSTEM_SECTION
    assert "current turn" in CONNECTOR_MODE_SYSTEM_SECTION


def test_connector_mode_system_section_has_naming_bridge_and_join_guardrail() -> None:
    assert "This session uses API connectors as its data source" in CONNECTOR_MODE_SYSTEM_SECTION
    assert "mounted with the" in CONNECTOR_MODE_SYSTEM_SECTION
    assert "ask_user" in CONNECTOR_MODE_SYSTEM_SECTION
    assert "never guess argument values" in CONNECTOR_MODE_SYSTEM_SECTION
    assert "join key" in CONNECTOR_MODE_SYSTEM_SECTION


def test_connector_mode_system_section_warns_against_guessing_table_names() -> None:
    assert "NEVER guess or assemble a table name" in CONNECTOR_MODE_SYSTEM_SECTION


def test_connector_mode_system_section_has_no_per_connector_index() -> None:
    """connector→skill 對應已交由 deepagents 的 SkillsMiddleware 索引承載,這段常數
    不再逐 connector 列 id/名稱/skill 清單,避免與 skills 索引重複。"""
    assert "available skill" not in CONNECTOR_MODE_SYSTEM_SECTION
    assert "connector_id" not in CONNECTOR_MODE_SYSTEM_SECTION
    assert "display_name" not in CONNECTOR_MODE_SYSTEM_SECTION


def test_connector_tables_reset_note_mentions_reload_instruction() -> None:
    assert "unloaded" in CONNECTOR_TABLES_RESET_NOTE
    assert "Call the corresponding" in CONNECTOR_TABLES_RESET_NOTE


def test_connector_tables_reset_note_says_qn_results_still_valid() -> None:
    assert "remain valid" in CONNECTOR_TABLES_RESET_NOTE
    assert "do not call connector tools again" in CONNECTOR_TABLES_RESET_NOTE
