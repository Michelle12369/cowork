"""tests/test_api_fetch.py"""

import json

import httpx
import pytest

from app.engine.api_fetch import (
    ConnectorFetchError,
    execute_fetch,
    land_snapshot,
    load_fetch_records,
    quarantine_unmountable_snapshots,
    record_fetch,
    snapshot_fingerprint,
)
from app.engine.connectors import ConnectorDefinition
from app.engine.workspace import prepare_local_layout


def _definition(**overrides) -> ConnectorDefinition:
    base = {
        "name": "mes_yield",
        "kind": "data",
        "description": "良率",
        "endpoint": "http://api.internal/yield",
        "method": "POST",
        "auth": "bearer:TEST_TOKEN_ENV",
        "params": {},
        "limits": {"timeout_s": 5, "max_bytes": 1000, "max_rows": 100},
    }
    base.update(overrides)
    return ConnectorDefinition.model_validate(base)


def _make_workspace(tmp_path):
    return prepare_local_layout(tmp_path, "user-1", "sess-1")


def test_execute_fetch_success_sendsAuthHeaderAndParams(monkeypatch):
    monkeypatch.setenv("TEST_TOKEN_ENV", "secret-token")
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=[{"tool": "A"}])

    payload = execute_fetch(_definition(), {"line_id": "A"}, transport=httpx.MockTransport(handler))
    assert json.loads(payload) == [{"tool": "A"}]
    assert captured["auth"] == "Bearer secret-token"
    assert captured["body"] == {"line_id": "A"}


def test_execute_fetch_getMethod_paramsAsQuery(monkeypatch):
    monkeypatch.setenv("TEST_TOKEN_ENV", "t")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["line_id"] == "A"
        return httpx.Response(200, json=[])

    execute_fetch(
        _definition(method="GET"), {"line_id": "A"}, transport=httpx.MockTransport(handler)
    )


def test_execute_fetch_missingAuthEnv_raisesWithoutUrl(monkeypatch):
    monkeypatch.delenv("TEST_TOKEN_ENV", raising=False)
    with pytest.raises(ConnectorFetchError) as error_info:
        execute_fetch(
            _definition(), {}, transport=httpx.MockTransport(lambda r: httpx.Response(200))
        )
    assert "TEST_TOKEN_ENV" in str(error_info.value)
    assert "api.internal" not in str(error_info.value)


def test_execute_fetch_httpError_raisesActionable(monkeypatch):
    monkeypatch.setenv("TEST_TOKEN_ENV", "t")
    transport = httpx.MockTransport(lambda request: httpx.Response(503))
    with pytest.raises(ConnectorFetchError, match="mes_yield"):
        execute_fetch(_definition(), {}, transport=transport)


def test_execute_fetch_oversizedBody_raisesCapMessage(monkeypatch):
    monkeypatch.setenv("TEST_TOKEN_ENV", "t")
    big_payload = json.dumps([{"x": "y" * 50}] * 100).encode()
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=big_payload))
    with pytest.raises(ConnectorFetchError, match="max_bytes"):
        execute_fetch(_definition(), {}, transport=transport)


def test_snapshot_fingerprint_sameConnectorAndParams_sameFingerprint():
    first = snapshot_fingerprint("mes_yield", {"line_id": "A", "start_date": "2026-08-01"})
    second = snapshot_fingerprint("mes_yield", {"start_date": "2026-08-01", "line_id": "A"})
    assert first == second
    assert len(first) == 16


def test_snapshot_fingerprint_differentParams_differentFingerprint():
    first = snapshot_fingerprint("mes_yield", {"line_id": "A"})
    second = snapshot_fingerprint("mes_yield", {"line_id": "B"})
    assert first != second


def test_snapshot_fingerprint_differentConnector_differentFingerprint():
    first = snapshot_fingerprint("mes_yield", {"line_id": "A"})
    second = snapshot_fingerprint("line_list", {"line_id": "A"})
    assert first != second


def test_land_snapshot_and_record_fetch_roundTrip(tmp_path):
    workspace = _make_workspace(tmp_path)
    fingerprint = snapshot_fingerprint("mes_yield", {"line_id": "A"})
    snapshot_path = land_snapshot(workspace, fingerprint, b'[{"a":1}]')
    assert snapshot_path == workspace.api_snapshots_dir / f"{fingerprint}.json"
    assert snapshot_path.read_bytes() == b'[{"a":1}]'
    record_fetch(workspace, fingerprint, "yield_data", "mes_yield", {"line_id": "A"}, ["a"])
    records = load_fetch_records(workspace)
    assert records == [
        {
            "fingerprint": fingerprint,
            "alias": "yield_data",
            "connector": "mes_yield",
            "params": {"line_id": "A"},
            "columns": ["a"],
        }
    ]
    # 同指紋重抓(同 connector+同 params):snapshot 覆蓋、記錄以最後一筆為準
    land_snapshot(workspace, fingerprint, b'[{"a":2}]')
    record_fetch(workspace, fingerprint, "yield_data", "mes_yield", {"line_id": "A"}, ["a"])
    assert load_fetch_records(workspace)[-1]["params"] == {"line_id": "A"}


def test_load_fetch_records_corruptedFile_renamesAndReturnsEmpty(tmp_path):
    workspace = _make_workspace(tmp_path)
    workspace.fetches_path.write_text("{not valid json", encoding="utf-8")

    assert load_fetch_records(workspace) == []

    corrupt_path = workspace.fetches_path.with_suffix(".json.corrupt")
    assert corrupt_path.read_text(encoding="utf-8") == "{not valid json"
    assert not workspace.fetches_path.exists()

    fingerprint = snapshot_fingerprint("mes_yield", {"line_id": "A"})
    record_fetch(workspace, fingerprint, "yield_data", "mes_yield", {"line_id": "A"}, ["a"])
    assert load_fetch_records(workspace) == [
        {
            "fingerprint": fingerprint,
            "alias": "yield_data",
            "connector": "mes_yield",
            "params": {"line_id": "A"},
            "columns": ["a"],
        }
    ]


def test_record_fetch_atomicWrite_noTmpLeftover(tmp_path):
    workspace = _make_workspace(tmp_path)
    fingerprint = snapshot_fingerprint("mes_yield", {"line_id": "A"})
    record_fetch(workspace, fingerprint, "yield_data", "mes_yield", {"line_id": "A"}, ["a"])

    tmp_leftover = workspace.fetches_path.with_suffix(".json.tmp")
    assert not tmp_leftover.exists()
    assert workspace.fetches_path.exists()


def test_land_snapshot_atomicWrite_noTmpLeftover(tmp_path):
    workspace = _make_workspace(tmp_path)
    fingerprint = snapshot_fingerprint("mes_yield", {"line_id": "A"})
    snapshot_path = land_snapshot(workspace, fingerprint, b'[{"a":1}]')

    tmp_leftover = snapshot_path.with_suffix(".json.tmp")
    assert not tmp_leftover.exists()
    assert snapshot_path.exists()


def test_quarantine_unmountable_snapshots_corruptFile_renamedAndExcluded(tmp_path):
    """mid-write crash 留下的半寫壞檔——probe 用真正的 read_json_auto(不是 json.loads,語法
    接受面不同),失敗就改名 .corrupt(glob *.json 不再匹配),不進回傳清單。"""
    workspace = _make_workspace(tmp_path)
    good_fingerprint = snapshot_fingerprint("mes_yield", {"line_id": "A"})
    bad_fingerprint = snapshot_fingerprint("mes_yield", {"line_id": "broken"})
    good_path = land_snapshot(workspace, good_fingerprint, b'[{"crop": "corn", "yield_kg": 1}]')
    bad_path = land_snapshot(workspace, bad_fingerprint, b"{not valid json at all")

    mountable = quarantine_unmountable_snapshots([bad_path, good_path])

    assert mountable == [good_path]
    corrupt_path = bad_path.with_suffix(".json.corrupt")
    assert corrupt_path.exists()
    assert not bad_path.exists()
    assert corrupt_path.read_bytes() == b"{not valid json at all"


def test_quarantine_unmountable_snapshots_allValid_returnsUnchanged(tmp_path):
    workspace = _make_workspace(tmp_path)
    fingerprint = snapshot_fingerprint("mes_yield", {"line_id": "A"})
    snapshot_path = land_snapshot(workspace, fingerprint, b'[{"a": 1}]')

    mountable = quarantine_unmountable_snapshots([snapshot_path])

    assert mountable == [snapshot_path]
    assert snapshot_path.exists()
