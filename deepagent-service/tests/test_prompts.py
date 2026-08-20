from app.agent.prompts import (
    SYSTEM_PROMPT,
    build_connector_prompt_section,
    build_sources_manifest_note,
)
from app.engine.connectors import (
    ConnectorDefinition,
    ConnectorLimits,
    ConnectorParam,
    ConnectorRegistry,
    ValidateAgainst,
)
from app.engine.source_manifest import SchemaChange, SourcesDiff

_LINE_LIST = ConnectorDefinition(
    name="line_list",
    kind="lookup",
    description="產線清單",
    endpoint="http://api.internal/lines",
    params={},
    limits=ConnectorLimits(),
)
_MES_YIELD = ConnectorDefinition(
    name="mes_yield",
    kind="data",
    description="產線良率",
    endpoint="http://api.internal/yield",
    method="POST",
    params={
        "line_id": ConnectorParam(
            type="str",
            required=True,
            validate_against=ValidateAgainst(connector="line_list", column="line_id"),
        ),
        "start_date": ConnectorParam(type="date", required=True),
    },
    limits=ConnectorLimits(),
)


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


def test_build_connector_prompt_section_emptyRegistry_returnsEmptyString() -> None:
    assert build_connector_prompt_section(ConnectorRegistry([])) == ""


def test_build_connector_prompt_section_listsDataConnectorsWithLookupPointers() -> None:
    section = build_connector_prompt_section(ConnectorRegistry([_LINE_LIST, _MES_YIELD]))
    assert "mes_yield" in section
    assert "line_id (values from line_list)" in section
    assert "line_list" in section
    assert "Parameter resolution, in order" in section
    assert "infer from conversation" in section
    assert "partial hints" in section
    assert "no hints" in section
    assert "<=10 enumerate as choices" in section
    assert "11-200" in section
    assert ">200" in section
    assert "alias = connector name" in section
    assert "At most 6 fetches per turn" in section
