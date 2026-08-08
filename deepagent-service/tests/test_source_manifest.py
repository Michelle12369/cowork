from app.engine.duck import Source, open_locked_connection
from app.engine.source_manifest import (
    SchemaChange,
    SourceRecord,
    SourcesDiff,
    build_manifest,
    diff_manifests,
    load_manifest,
    save_manifest,
)
from app.engine.workspace import prepare_local_layout


def _connection_with_orders_csv(tmp_path, columns_csv: str):
    csv_path = tmp_path / "orders.csv"
    csv_path.write_text(columns_csv, encoding="utf-8")
    return open_locked_connection([Source("orders", str(csv_path), "csv")])


# -- build_manifest ------------------------------------------------------------------------


def test_build_manifest_reads_columns_from_connection(tmp_path) -> None:
    connection = _connection_with_orders_csv(tmp_path, "system,tickets\nCRM,42\n")
    manifest = build_manifest(connection, [("orders", "/uploads/sess-1/uuid1_orders.csv")])

    assert set(manifest) == {"orders"}
    record = manifest["orders"]
    assert record.alias == "orders"
    assert record.kind == "file"
    assert record.version_id == "/uploads/sess-1/uuid1_orders.csv"
    assert record.columns == (("system", "VARCHAR"), ("tickets", "BIGINT"))


def test_build_manifest_covers_multiple_sources(tmp_path) -> None:
    orders_csv = tmp_path / "orders.csv"
    orders_csv.write_text("system\nCRM\n", encoding="utf-8")
    usage_csv = tmp_path / "usage_log.csv"
    usage_csv.write_text("event\nlogin\n", encoding="utf-8")
    connection = open_locked_connection(
        [Source("orders", str(orders_csv), "csv"), Source("usage_log", str(usage_csv), "csv")]
    )

    manifest = build_manifest(
        connection,
        [("orders", "/uploads/sess-1/orders.csv"), ("usage_log", "/uploads/sess-1/usage.csv")],
    )

    assert set(manifest) == {"orders", "usage_log"}
    assert manifest["orders"].columns == (("system", "VARCHAR"),)
    assert manifest["usage_log"].columns == (("event", "VARCHAR"),)


# -- save_manifest / load_manifest roundtrip ------------------------------------------------


def test_save_and_load_manifest_roundtrips(tmp_path) -> None:
    workspace = prepare_local_layout(tmp_path / "ws", "user-1", "sess-1")
    manifest = {
        "orders": SourceRecord(
            alias="orders",
            kind="file",
            version_id="/uploads/sess-1/orders.csv",
            columns=(("system", "VARCHAR"), ("tickets", "BIGINT")),
        )
    }

    save_manifest(workspace, manifest)
    loaded = load_manifest(workspace)

    assert loaded == manifest


def test_load_manifest_returns_none_when_file_absent(tmp_path) -> None:
    workspace = prepare_local_layout(tmp_path / "ws", "user-1", "sess-1")
    assert load_manifest(workspace) is None


def test_load_manifest_tolerates_unknown_keys(tmp_path) -> None:
    """未來可能加的欄位(fetched_at/params)不該讓舊 manifest 檔讀取失敗——只認得目前定義的
    欄位,其餘忽略。"""
    workspace = prepare_local_layout(tmp_path / "ws", "user-1", "sess-1")
    workspace.sources_manifest_path.write_text(
        '{"orders": {"kind": "file", "version_id": "/uploads/orders.csv", '
        '"columns": [["system", "VARCHAR"]], "fetched_at": "2026-08-08T00:00:00Z", '
        '"params": {"foo": "bar"}}}',
        encoding="utf-8",
    )

    loaded = load_manifest(workspace)

    assert loaded == {
        "orders": SourceRecord(
            alias="orders",
            kind="file",
            version_id="/uploads/orders.csv",
            columns=(("system", "VARCHAR"),),
        )
    }


# -- diff_manifests -------------------------------------------------------------------------


def _record(version_id: str, columns: tuple[tuple[str, str], ...]) -> SourceRecord:
    return SourceRecord(alias="orders", kind="file", version_id=version_id, columns=columns)


def test_diff_manifests_added_source() -> None:
    previous: dict[str, SourceRecord] = {}
    current = {"orders": _record("/v1/orders.csv", (("system", "VARCHAR"),))}

    diff = diff_manifests(previous, current)

    assert diff == SourcesDiff(added=("orders",), removed=(), version_changed=(), schema_changed=())


def test_diff_manifests_removed_source() -> None:
    previous = {"orders": _record("/v1/orders.csv", (("system", "VARCHAR"),))}
    current: dict[str, SourceRecord] = {}

    diff = diff_manifests(previous, current)

    assert diff == SourcesDiff(added=(), removed=("orders",), version_changed=(), schema_changed=())


def test_diff_manifests_same_version_never_reported() -> None:
    record = _record("/v1/orders.csv", (("system", "VARCHAR"),))
    previous = {"orders": record}
    current = {"orders": record}

    diff = diff_manifests(previous, current)

    assert diff.is_empty()


def test_diff_manifests_version_changed_same_schema() -> None:
    columns = (("system", "VARCHAR"), ("tickets", "BIGINT"))
    previous = {"orders": _record("/v1/orders.csv", columns)}
    current = {"orders": _record("/v2/orders.csv", columns)}

    diff = diff_manifests(previous, current)

    assert diff == SourcesDiff(added=(), removed=(), version_changed=("orders",), schema_changed=())


def test_diff_manifests_schema_changed_added_column() -> None:
    previous = {"orders": _record("/v1/orders.csv", (("system", "VARCHAR"),))}
    current = {"orders": _record("/v2/orders.csv", (("system", "VARCHAR"), ("region", "VARCHAR")))}

    diff = diff_manifests(previous, current)

    assert diff.version_changed == ()
    assert diff.schema_changed == (
        SchemaChange(
            alias="orders",
            added_columns=("region",),
            removed_columns=(),
            type_changed_columns=(),
        ),
    )


def test_diff_manifests_schema_changed_removed_column() -> None:
    previous = {
        "orders": _record("/v1/orders.csv", (("system", "VARCHAR"), ("old_col", "VARCHAR")))
    }
    current = {"orders": _record("/v2/orders.csv", (("system", "VARCHAR"),))}

    diff = diff_manifests(previous, current)

    assert diff.schema_changed == (
        SchemaChange(
            alias="orders",
            added_columns=(),
            removed_columns=("old_col",),
            type_changed_columns=(),
        ),
    )


def test_diff_manifests_schema_changed_type_changed() -> None:
    previous = {"orders": _record("/v1/orders.csv", (("tickets", "BIGINT"),))}
    current = {"orders": _record("/v2/orders.csv", (("tickets", "VARCHAR"),))}

    diff = diff_manifests(previous, current)

    assert diff.schema_changed == (
        SchemaChange(
            alias="orders",
            added_columns=(),
            removed_columns=(),
            type_changed_columns=("tickets",),
        ),
    )


def test_diff_manifests_version_and_schema_changed_goes_to_schema_changed_only() -> None:
    """換版本又剛好 schema 也變了——schema 訊息已經涵蓋「換了底層檔案」這件事,不該在
    version_changed 重複列一次。"""
    previous = {"orders": _record("/v1/orders.csv", (("system", "VARCHAR"),))}
    current = {"orders": _record("/v2/orders.csv", (("system", "VARCHAR"), ("region", "VARCHAR")))}

    diff = diff_manifests(previous, current)

    assert diff.version_changed == ()
    assert len(diff.schema_changed) == 1
    assert diff.schema_changed[0].alias == "orders"


def test_diff_manifests_empty_when_nothing_changed() -> None:
    record = _record("/v1/orders.csv", (("system", "VARCHAR"),))
    diff = diff_manifests({"orders": record}, {"orders": record})

    assert diff.is_empty()
