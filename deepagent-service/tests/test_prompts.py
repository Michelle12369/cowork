from app.agent.prompts import (
    CONNECTOR_MODE_SYSTEM_SECTION,
    SYSTEM_PROMPT,
    build_snapshot_heal_note,
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


def test_build_snapshot_heal_note_contains_required_elements() -> None:
    skipped_landings = [
        {
            "connector_id": "demo_quality",
            "tool_name": "get_quality",
            "args": {"fab": "FAB_A", "week": "2026-W32"},
            "land_as": "fab_a_w32",
            "observed_columns": ["fab", "week"],
            "input_schema_hash": "irrelevant",
            "snapshot_sha256": "irrelevant",
        }
    ]

    note = build_snapshot_heal_note(skipped_landings)

    assert "fab_a_w32" in note
    assert "demo_quality_get_quality" in note
    assert '{"fab": "FAB_A", "week": "2026-W32"}' in note
    assert 'land_as="fab_a_w32"' in note
    assert "不需徵詢使用者" in note
    assert "NEVER 自行變更參數值" in note


def test_connector_mode_system_section_has_naming_bridge_and_land_as_guidance() -> None:
    """搬家後的靜態段(見 prompts.py 常數註解)取代舊版 build_connector_prompt_note——
    內容涵蓋命名橋接、land_as 時機、lookup→ask_user 銜接、join 護欄,且開頭標明本 session
    以 API connector 為資料源。"""
    assert "本 session 以 API connector 為資料源" in CONNECTOR_MODE_SYSTEM_SECTION
    assert "前綴掛載" in CONNECTOR_MODE_SYSTEM_SECTION
    assert "land_as" in CONNECTOR_MODE_SYSTEM_SECTION
    assert "ask_user" in CONNECTOR_MODE_SYSTEM_SECTION
    assert "不要自行猜測參數值" in CONNECTOR_MODE_SYSTEM_SECTION
    assert "join key" in CONNECTOR_MODE_SYSTEM_SECTION


def test_connector_mode_system_section_has_no_per_connector_index() -> None:
    """connector→skill 對應已交由 deepagents 的 SkillsMiddleware 索引承載,這段常數
    不再逐 connector 列 id/名稱/skill 清單,避免與 skills 索引重複。"""
    assert "可用 skill" not in CONNECTOR_MODE_SYSTEM_SECTION
    assert "connector_id" not in CONNECTOR_MODE_SYSTEM_SECTION
    assert "display_name" not in CONNECTOR_MODE_SYSTEM_SECTION


def test_build_snapshot_heal_note_multiple_aliases_each_get_own_line() -> None:
    skipped_landings = [
        {
            "connector_id": "demo_quality",
            "tool_name": "get_quality",
            "args": {"fab": "FAB_A"},
            "land_as": "fab_a",
        },
        {
            "connector_id": "demo_quality",
            "tool_name": "list_fabs",
            "args": {},
            "land_as": "fab_list",
        },
    ]

    note = build_snapshot_heal_note(skipped_landings)

    assert "fab_a:demo_quality_get_quality" in note
    assert "fab_list:demo_quality_list_fabs" in note
