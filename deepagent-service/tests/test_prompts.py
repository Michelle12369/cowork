from app.agent.prompts import build_api_sources_context, build_sources_manifest_note
from app.engine.api_registry import API_REGISTRY
from app.engine.api_snapshot import SnapshotMeta
from app.engine.source_manifest import SchemaChange, SourcesDiff


def _orders_snapshot() -> SnapshotMeta:
    return SnapshotMeta(
        api_id="mock_orders",
        alias="api_orders",
        params={"date_range": "30d", "machines": ["M1", "M3"]},
        fetched_at="2026-08-12T09:00:00+00:00",
        schema=(("order_id", "BIGINT"),),
        row_count=2,
        truncated=False,
    )


def test_api_sources_context_empty_registry_returns_empty_string() -> None:
    assert build_api_sources_context({}, []) == ""


def test_api_sources_context_all_unfetched_lists_available_only() -> None:
    context = build_api_sources_context(API_REGISTRY, [])
    assert "Available API datasources" in context
    assert "`api_orders`" in context and "`api_machines`" in context
    assert "machines (required, multi)" in context
    assert "Fetched API datasources" not in context


def test_api_sources_context_mixed_lists_both_sections() -> None:
    context = build_api_sources_context(API_REGISTRY, [_orders_snapshot()])
    assert "Available API datasources" in context and "`api_machines`" in context
    assert "Fetched API datasources" in context
    assert "date_range=30d" in context and "machines=M1,M3" in context
    assert "- `api_orders` — fetched 2026-08-12T09:00:00+00:00" in context


def test_api_sources_context_all_fetched_omits_available_section() -> None:
    snapshots = [
        _orders_snapshot(),
        SnapshotMeta(
            api_id="mock_machines",
            alias="api_machines",
            params={"site": "TP"},
            fetched_at="2026-08-12T10:00:00+00:00",
            schema=(),
            row_count=0,
            truncated=False,
        ),
    ]
    context = build_api_sources_context(API_REGISTRY, snapshots)
    assert "Available API datasources" not in context


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
