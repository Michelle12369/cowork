import datetime
import decimal
import json
import threading

import duckdb
import pytest

from app.engine.api_snapshot import (
    EmptyLandingError,
    land_response,
    mount_json_file,
    unwrap_envelope,
)


@pytest.fixture()
def connection():
    live_connection = duckdb.connect(":memory:")
    yield live_connection
    live_connection.close()


@pytest.fixture()
def connection_lock():
    return threading.Lock()


def test_unwrap_envelope_plain_list_passes_through_with_no_envelope_fields() -> None:
    payload = [{"system": "CRM"}, {"system": "ERP"}]

    data, envelope_fields = unwrap_envelope(payload)

    assert data == payload
    assert envelope_fields == {}


def test_unwrap_envelope_dict_with_data_list_splits_out_other_top_level_fields() -> None:
    payload = {"data": [{"x": 1}], "errorCode": "", "requestId": "abc"}

    data, envelope_fields = unwrap_envelope(payload)

    assert data == [{"x": 1}]
    assert envelope_fields == {"errorCode": "", "requestId": "abc"}


def test_unwrap_envelope_non_envelope_shape_passes_through_unchanged() -> None:
    payload = {"metric": "yield", "value": 0.98}

    data, envelope_fields = unwrap_envelope(payload)

    assert data == payload
    assert envelope_fields == {}


def test_unwrap_envelope_fastmcp_result_wrapper_around_list_unwraps_to_rows() -> None:
    payload = {"result": [{"x": 1}, {"x": 2}]}

    data, envelope_fields = unwrap_envelope(payload)

    assert data == [{"x": 1}, {"x": 2}]
    assert envelope_fields == {}


def test_unwrap_envelope_fastmcp_result_wrapper_around_data_envelope_unwraps_both() -> None:
    payload = {"result": {"data": [{"x": 1}], "errorCode": ""}}

    data, envelope_fields = unwrap_envelope(payload)

    assert data == [{"x": 1}]
    assert envelope_fields == {"errorCode": ""}


def test_unwrap_envelope_fastmcp_result_wrapper_around_scalar_stays_single_row() -> None:
    payload = {"result": "hi"}

    data, envelope_fields = unwrap_envelope(payload)

    assert data == {"result": "hi"}
    assert envelope_fields == {}


def test_unwrap_envelope_dict_with_result_and_other_keys_is_not_treated_as_wrapper() -> None:
    payload = {"result": [{"x": 1}], "status": "ok"}

    data, envelope_fields = unwrap_envelope(payload)

    assert data == payload
    assert envelope_fields == {}


def test_land_response_flat_list_lands_rows_and_columns(
    tmp_path, connection, connection_lock
) -> None:
    payload = [{"system": "CRM", "tickets": 42}, {"system": "ERP", "tickets": 7}]

    result = land_response(connection, connection_lock, tmp_path, "tickets", payload)

    assert result.table_name == "tickets"
    assert result.columns == ["system", "tickets"]
    assert result.row_count == 2
    assert result.envelope_fields == {}
    rows = connection.execute('SELECT system, tickets FROM "tickets" ORDER BY tickets').fetchall()
    assert rows == [("ERP", 7), ("CRM", 42)]
    assert (tmp_path / "tickets.json").is_file()


def test_land_response_envelope_payload_lands_unwrapped_data_and_returns_other_fields(
    tmp_path, connection, connection_lock
) -> None:
    payload = {
        "data": [
            {"metric": "yield", "value": 0.98},
            {"metric": "yield", "value": 0.95},
        ],
        "errorCode": "",
    }

    result = land_response(connection, connection_lock, tmp_path, "quality_fab_a", payload)

    assert result.columns == ["metric", "value"]
    assert result.row_count == 2
    assert result.envelope_fields == {"errorCode": ""}
    described_columns = [
        row[0] for row in connection.execute('DESCRIBE "quality_fab_a"').fetchall()
    ]
    assert described_columns == ["metric", "value"]


def test_land_response_non_envelope_dict_lands_as_single_row(
    tmp_path, connection, connection_lock
) -> None:
    payload = {"metric": "yield", "value": 0.98, "device": {"id": "DEV-01", "name": "Device Alpha"}}

    result = land_response(connection, connection_lock, tmp_path, "reading", payload)

    assert result.row_count == 1
    assert set(result.columns) == {"metric", "value", "device"}
    assert result.envelope_fields == {}


def test_land_response_empty_list_raises_actionable_error_and_writes_no_file(
    tmp_path, connection, connection_lock
) -> None:
    with pytest.raises(EmptyLandingError, match="quality_fab_a"):
        land_response(connection, connection_lock, tmp_path, "quality_fab_a", [])

    assert list(tmp_path.iterdir()) == []


def test_land_response_empty_envelope_data_raises_actionable_error(
    tmp_path, connection, connection_lock
) -> None:
    with pytest.raises(EmptyLandingError, match="quality_fab_a"):
        land_response(
            connection, connection_lock, tmp_path, "quality_fab_a", {"data": [], "errorCode": ""}
        )


def test_land_response_rejects_unsafe_table_name(tmp_path, connection, connection_lock) -> None:
    with pytest.raises(ValueError, match="unsafe"):
        land_response(connection, connection_lock, tmp_path, "bad-name", [{"x": 1}])


def test_land_response_preview_rows_capped_at_twenty_and_json_serializable(
    tmp_path, connection, connection_lock
) -> None:
    # ISO 日期字串——DuckDB read_json_auto 會推斷成 DATE 欄,驗證 preview 正規化把它轉回
    # JSON 相容的字串(payload 本身是已解析的 JSON 值,不含 python Decimal/date 物件)。
    payload = [{"index": row_index, "measured_at": "2026-01-01"} for row_index in range(30)]

    result = land_response(connection, connection_lock, tmp_path, "measurements", payload)

    assert result.row_count == 30
    assert len(result.preview_rows) == 20
    # normalize_rows 已把 date 轉成 JSON 相容型別——確認整份 preview 可以 json.dumps。
    json.dumps(result.preview_rows)


def test_land_response_preview_rows_normalize_date_and_decimal_types(
    tmp_path, connection, connection_lock
) -> None:
    connection.execute("CREATE TABLE source_dates (measured_at DATE, amount DECIMAL(10, 2))")
    connection.execute("INSERT INTO source_dates VALUES (DATE '2026-01-01', 1.50)")
    raw_rows = connection.execute("SELECT * FROM source_dates").fetchall()
    assert isinstance(raw_rows[0][0], datetime.date)
    assert isinstance(raw_rows[0][1], decimal.Decimal)

    payload = [{"metric": "x", "value": 1}]
    result = land_response(connection, connection_lock, tmp_path, "plain", payload)
    json.dumps(result.preview_rows)


def test_mount_json_file_mounts_existing_file(tmp_path, connection, connection_lock) -> None:
    json_path = tmp_path / "preexisting.json"
    json_path.write_text(json.dumps([{"x": 1}, {"x": 2}]), encoding="utf-8")

    columns, row_count = mount_json_file(connection, connection_lock, "preexisting", json_path)

    assert columns == ["x"]
    assert row_count == 2
    assert connection.execute('SELECT COUNT(*) FROM "preexisting"').fetchone()[0] == 2


def test_land_response_same_table_name_relanding_is_last_wins(
    tmp_path, connection, connection_lock
) -> None:
    land_response(connection, connection_lock, tmp_path, "tickets", [{"x": 1}, {"x": 2}])

    result = land_response(connection, connection_lock, tmp_path, "tickets", [{"x": 1}])

    assert result.row_count == 1
    assert connection.execute('SELECT COUNT(*) FROM "tickets"').fetchone()[0] == 1
