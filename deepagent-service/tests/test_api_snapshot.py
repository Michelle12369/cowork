"""tests/test_api_snapshot.py"""


import pytest

from app.engine.api_snapshot import (
    SnapshotMeta,
    infer_column_types,
    normalize_json_array,
    sanitize_column_names,
    scan_snapshots,
    write_snapshot,
)
from app.engine.workspace import SessionWorkspace


def _snapshot_meta(alias: str = "api_orders") -> SnapshotMeta:
    return SnapshotMeta(
        api_id="mock_orders",
        alias=alias,
        params={"date_range": "30d"},
        fetched_at="2026-08-12T09:00:00Z",
        schema=(("order_id", "BIGINT"), ("amount", "DOUBLE")),
        row_count=2,
        truncated=False,
    )


def test_normalize_json_array_unions_keys_and_fills_missing_with_none():
    columns, rows = normalize_json_array(
        [{"order_id": 1, "amount": 5.0}, {"order_id": 2, "site": "TP"}]
    )
    assert columns == ["order_id", "amount", "site"]
    assert rows == [[1, 5.0, None], [2, None, "TP"]]


def test_normalize_json_array_rejects_non_array_and_non_object_rows():
    with pytest.raises(ValueError):
        normalize_json_array({"data": []})
    with pytest.raises(ValueError):
        normalize_json_array([1, 2])


def test_sanitize_column_names_replaces_unsafe_dedupes_and_names_empty():
    assert sanitize_column_names(["a b", "a.b", "a_b", ""]) == ["a_b", "a_b_2", "a_b_3", "column_4"]


def test_infer_column_types_per_column():
    columns = ["flag", "count", "ratio", "label", "empty"]
    rows = [[True, 1, 1.5, "x", None], [False, 2, 2, None, None]]
    assert infer_column_types(columns, rows) == (
        ("flag", "BOOLEAN"),
        ("count", "BIGINT"),
        ("ratio", "DOUBLE"),
        ("label", "VARCHAR"),
        ("empty", "VARCHAR"),
    )


def test_write_then_scan_roundtrip(tmp_path):
    workspace = SessionWorkspace(root=tmp_path)
    meta = _snapshot_meta()
    write_snapshot(workspace, meta, ["order_id", "amount"], [[1, 5.0], [2, 7.5]], '[{"raw": 1}]')
    assert (workspace.api_dir / "api_orders.csv").is_file()
    assert (workspace.api_dir / "api_orders.raw.json").read_text(encoding="utf-8") == '[{"raw": 1}]'
    assert not list(workspace.api_dir.glob("*.part"))
    scanned = scan_snapshots(workspace)
    assert scanned == [meta]


def test_scan_skips_broken_snapshot_missing_csv(tmp_path, caplog):
    workspace = SessionWorkspace(root=tmp_path)
    write_snapshot(workspace, _snapshot_meta(), ["order_id"], [[1]], None)
    (workspace.api_dir / "api_orders.csv").unlink()
    assert scan_snapshots(workspace) == []


def test_scan_empty_when_api_dir_absent(tmp_path):
    assert scan_snapshots(SessionWorkspace(root=tmp_path)) == []
