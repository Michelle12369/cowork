import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.engine.replay_manifest import (
    landing_hashes,
    load_landings,
    record_landing,
    record_tool_audit,
    schema_hash,
)
from app.engine.workspace import prepare_local_layout


def _land(workspace, *, land_as: str, snapshot_sha256: str = "a" * 64) -> None:
    record_landing(
        workspace,
        connector_id="demo-connector",
        tool_name="list_orders",
        args={"status": "open"},
        land_as=land_as,
        observed_columns=["id", "status"],
        input_schema_hash=schema_hash({"type": "object", "properties": {"status": {}}}),
        snapshot_sha256=snapshot_sha256,
    )


def test_record_landing_twice_load_returns_two_in_order(tmp_path: Path) -> None:
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")

    _land(workspace, land_as="orders", snapshot_sha256="a" * 64)
    _land(workspace, land_as="customers", snapshot_sha256="b" * 64)

    landings = load_landings(workspace)

    assert len(landings) == 2
    assert landings[0]["land_as"] == "orders"
    assert landings[0]["snapshot_sha256"] == "a" * 64
    assert landings[1]["land_as"] == "customers"
    assert landings[1]["snapshot_sha256"] == "b" * 64
    assert landings[0]["connector_id"] == "demo-connector"
    assert landings[0]["tool_name"] == "list_orders"
    assert landings[0]["args"] == {"status": "open"}
    assert landings[0]["observed_columns"] == ["id", "status"]


def test_schema_hash_is_key_order_insensitive() -> None:
    first = schema_hash({"type": "object", "properties": {"a": {}, "b": {}}})
    second = schema_hash({"properties": {"b": {}, "a": {}}, "type": "object"})

    assert first == second
    assert len(first) == 16


def test_schema_hash_differs_for_different_schemas() -> None:
    first = schema_hash({"type": "object"})
    second = schema_hash({"type": "array"})

    assert first != second


def test_schema_hash_is_key_order_insensitive_for_nested_dicts() -> None:
    first = schema_hash(
        {
            "type": "object",
            "properties": {
                "a": {"type": "string", "format": "date"},
                "b": {"format": "int32", "type": "number"},
            },
        }
    )
    second = schema_hash(
        {
            "properties": {
                "b": {"type": "number", "format": "int32"},
                "a": {"format": "date", "type": "string"},
            },
            "type": "object",
        }
    )

    assert first == second


def test_record_tool_audit_writes_to_separate_file(tmp_path: Path) -> None:
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")

    _land(workspace, land_as="orders")
    record_tool_audit(
        workspace,
        connector_id="demo-connector",
        tool_name="lookup_status_codes",
        args={},
        landed=False,
    )

    landings = load_landings(workspace)
    assert len(landings) == 1

    audit_path = workspace.replay_dir / "audit.jsonl"
    assert audit_path.is_file()
    landings_path = workspace.replay_dir / "landings.jsonl"
    assert audit_path != landings_path

    audit_lines = audit_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(audit_lines) == 1


def test_load_landings_skips_corrupted_line(tmp_path: Path) -> None:
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")

    _land(workspace, land_as="orders")
    with (workspace.replay_dir / "landings.jsonl").open("a", encoding="utf-8") as landings_file:
        landings_file.write("{not valid json\n")
    _land(workspace, land_as="customers")

    landings = load_landings(workspace)

    assert len(landings) == 2
    assert [landing["land_as"] for landing in landings] == ["orders", "customers"]


def test_load_landings_missing_file_returns_empty_list(tmp_path: Path) -> None:
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")

    assert load_landings(workspace) == []


def test_landing_hashes_last_wins_per_alias(tmp_path: Path) -> None:
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")

    _land(workspace, land_as="orders", snapshot_sha256="a" * 64)
    _land(workspace, land_as="orders", snapshot_sha256="c" * 64)

    hashes = landing_hashes(workspace)

    assert hashes == {"orders": "c" * 64}


def test_landing_hashes_multi_alias(tmp_path: Path) -> None:
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")

    _land(workspace, land_as="orders", snapshot_sha256="a" * 64)
    _land(workspace, land_as="customers", snapshot_sha256="b" * 64)
    _land(workspace, land_as="orders", snapshot_sha256="d" * 64)

    hashes = landing_hashes(workspace)

    assert hashes == {"orders": "d" * 64, "customers": "b" * 64}


def test_record_tool_audit_concurrent_appends_never_produce_torn_lines(
    tmp_path: Path,
) -> None:
    """8 threads 同時 append 一筆含 ~30KB args 的稽核記錄——每一行都要能 `json.loads`
    成功,且筆數剛好等於執行緒數,不多不少不斷行。"""
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")
    thread_count = 8
    large_payload = "x" * 30_000

    def _record(thread_index: int) -> None:
        record_tool_audit(
            workspace,
            connector_id="demo-connector",
            tool_name="bulk_export",
            args={"thread_index": thread_index, "payload": large_payload},
            landed=False,
        )

    with ThreadPoolExecutor(max_workers=thread_count) as executor:
        list(executor.map(_record, range(thread_count)))

    audit_path = workspace.replay_dir / "audit.jsonl"
    lines = audit_path.read_text(encoding="utf-8").splitlines()

    assert len(lines) == thread_count
    parsed_records = [json.loads(line) for line in lines]
    observed_indices = sorted(record["args"]["thread_index"] for record in parsed_records)
    assert observed_indices == list(range(thread_count))
    assert all(record["args"]["payload"] == large_payload for record in parsed_records)
