import hashlib
import threading

import duckdb
import pytest

from app.engine.api_snapshot import (
    EmptyLandingError,
    SnapshotIntegrityError,
    land_snapshot,
    remount_snapshots,
)
from app.engine.workspace import prepare_local_layout


def _workspace(tmp_path):
    return prepare_local_layout(tmp_path, "user-1", "sess-1")


@pytest.fixture()
def connection():
    live_connection = duckdb.connect(":memory:")
    yield live_connection
    live_connection.close()


@pytest.fixture()
def connection_lock():
    return threading.Lock()


def test_land_snapshot_envelope_payload_lands_struct_column(
    tmp_path, connection, connection_lock
) -> None:
    """demo connector 同形信封——寬鬆模式下整包落成單列表,`data` 欄經 DuckDB 推斷成
    STRUCT(...)[]。"""
    workspace = _workspace(tmp_path)
    payload = {
        "data": [
            {
                "metric": "yield",
                "value": 0.98,
                "device": {"id": "DEV-01", "name": "Device Alpha"},
            },
            {
                "metric": "yield",
                "value": 0.95,
                "device": {"id": "DEV-02", "name": "Device Beta"},
            },
        ],
        "errorCode": "",
    }

    result = land_snapshot(connection, connection_lock, workspace, "quality_fab_a", payload)

    assert result.columns == ["data", "errorCode"]
    assert result.row_count == 1
    described_columns = connection.execute('DESCRIBE "quality_fab_a"').fetchall()
    data_column_type = next(row[1] for row in described_columns if row[0] == "data")
    assert "STRUCT" in data_column_type
    snapshot_path = workspace.api_snapshots_dir / "quality_fab_a.json"
    assert snapshot_path.is_file()


def test_land_snapshot_flat_list_payload_lands_rows_and_columns(
    tmp_path, connection, connection_lock
) -> None:
    workspace = _workspace(tmp_path)
    payload = [{"system": "CRM", "tickets": 42}, {"system": "ERP", "tickets": 7}]

    result = land_snapshot(connection, connection_lock, workspace, "tickets", payload)

    assert result.columns == ["system", "tickets"]
    assert result.row_count == 2
    rows = connection.execute('SELECT system, tickets FROM "tickets" ORDER BY tickets').fetchall()
    assert rows == [("ERP", 7), ("CRM", 42)]


def test_land_snapshot_sha256_matches_written_file_bytes(
    tmp_path, connection, connection_lock
) -> None:
    """回傳的 sha256 MUST 是實際落地檔案 bytes 的雜湊——remount 的完整性驗證完全靠這個
    值,寫入與雜湊算的必須是同一份 bytes。"""
    workspace = _workspace(tmp_path)

    result = land_snapshot(connection, connection_lock, workspace, "tickets", [{"x": 1}])

    snapshot_path = workspace.api_snapshots_dir / "tickets.json"
    expected_hash = hashlib.sha256(snapshot_path.read_bytes()).hexdigest()
    assert result.sha256 == expected_hash


def test_land_snapshot_empty_list_raises_actionable_error(
    tmp_path, connection, connection_lock
) -> None:
    workspace = _workspace(tmp_path)

    with pytest.raises(EmptyLandingError, match="quality_fab_a"):
        land_snapshot(connection, connection_lock, workspace, "quality_fab_a", [])


def test_land_snapshot_rejects_unsafe_alias(tmp_path, connection, connection_lock) -> None:
    workspace = _workspace(tmp_path)

    with pytest.raises(ValueError, match="unsafe"):
        land_snapshot(connection, connection_lock, workspace, "bad-name", [{"x": 1}])


def test_land_snapshot_same_alias_relanding_is_last_wins(
    tmp_path, connection, connection_lock
) -> None:
    workspace = _workspace(tmp_path)
    land_snapshot(connection, connection_lock, workspace, "tickets", [{"x": 1}, {"x": 2}])

    result = land_snapshot(connection, connection_lock, workspace, "tickets", [{"x": 1}])

    assert result.row_count == 1
    assert connection.execute('SELECT COUNT(*) FROM "tickets"').fetchone()[0] == 1


def test_remount_snapshots_mounts_only_expected_hash_aliases(
    tmp_path, connection, connection_lock
) -> None:
    workspace = _workspace(tmp_path)
    alpha = land_snapshot(connection, connection_lock, workspace, "alpha", [{"x": 1}])
    beta = land_snapshot(connection, connection_lock, workspace, "beta", [{"y": 2}])

    fresh_connection = duckdb.connect(":memory:")
    try:
        mounted = remount_snapshots(
            fresh_connection,
            connection_lock,
            workspace,
            expected_hashes={"alpha": alpha.sha256, "beta": beta.sha256},
        )

        assert sorted(mounted) == ["alpha", "beta"]
        assert fresh_connection.execute('SELECT x FROM "alpha"').fetchall() == [(1,)]
        assert fresh_connection.execute('SELECT y FROM "beta"').fetchall() == [(2,)]
    finally:
        fresh_connection.close()


def test_remount_snapshots_ignores_extra_file_not_in_expected_hashes(
    tmp_path, connection, connection_lock
) -> None:
    """白名單目錄同時可寫——目錄裡多出來、呼叫端沒有記雜湊的檔案一律視為不可信,略過
    不掛,不靠掃目錄決定要掛哪些表。"""
    workspace = _workspace(tmp_path)
    alpha = land_snapshot(connection, connection_lock, workspace, "alpha", [{"x": 1}])
    # 白名單目錄可寫——模擬一份 run_sql 事後種進來、未被 land_snapshot 記過雜湊的檔案。
    (workspace.api_snapshots_dir / "planted.json").write_text('[{"z": 1}]', encoding="utf-8")

    mounted = remount_snapshots(
        connection, connection_lock, workspace, expected_hashes={"alpha": alpha.sha256}
    )

    assert mounted == ["alpha"]


def test_remount_snapshots_raises_when_expected_file_missing(
    tmp_path, connection, connection_lock
) -> None:
    workspace = _workspace(tmp_path)

    with pytest.raises(SnapshotIntegrityError, match="missing"):
        remount_snapshots(
            connection,
            connection_lock,
            workspace,
            expected_hashes={"alpha": "0" * 64},
        )


def test_remount_snapshots_raises_when_file_tampered_after_landing(
    tmp_path, connection, connection_lock
) -> None:
    """`run_sql` 在白名單目錄可寫,落表後把 snapshot 檔案覆寫掉——remount 前雜湊核對
    必須抓到這個竄改,拒絕重掛而不是悄悄吃下被動過的資料。"""
    workspace = _workspace(tmp_path)
    landing = land_snapshot(connection, connection_lock, workspace, "alpha", [{"x": 1}])

    snapshot_path = workspace.api_snapshots_dir / "alpha.json"
    snapshot_path.write_text('[{"x": 999}]', encoding="utf-8")  # 模擬被覆寫/竄改

    with pytest.raises(SnapshotIntegrityError, match="alpha"):
        remount_snapshots(
            connection,
            connection_lock,
            workspace,
            expected_hashes={"alpha": landing.sha256},
        )


def test_remount_snapshots_rejects_unsafe_alias_key(tmp_path, connection, connection_lock) -> None:
    workspace = _workspace(tmp_path)

    with pytest.raises(ValueError, match="unsafe"):
        remount_snapshots(
            connection,
            connection_lock,
            workspace,
            expected_hashes={"bad-name": "0" * 64},
        )
