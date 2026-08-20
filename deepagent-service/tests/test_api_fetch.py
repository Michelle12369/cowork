"""tests/test_api_fetch.py"""

import json

import httpx
import pytest

from app.engine.api_fetch import (
    ConnectorFetchError,
    execute_fetch,
    land_snapshot,
    load_fetch_records,
    record_fetch,
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


def test_land_snapshot_and_record_fetch_roundTrip(tmp_path):
    workspace = _make_workspace(tmp_path)
    snapshot_path = land_snapshot(workspace, "yield_data", b'[{"a":1}]')
    assert snapshot_path == workspace.api_snapshots_dir / "yield_data.json"
    assert snapshot_path.read_bytes() == b'[{"a":1}]'
    record_fetch(workspace, "yield_data", "mes_yield", {"line_id": "A"})
    records = load_fetch_records(workspace)
    assert records == [
        {"alias": "yield_data", "connector": "mes_yield", "params": {"line_id": "A"}}
    ]
    # 同 alias 重抓:snapshot 覆蓋、記錄以最後一筆為準
    land_snapshot(workspace, "yield_data", b'[{"a":2}]')
    record_fetch(workspace, "yield_data", "mes_yield", {"line_id": "B"})
    assert load_fetch_records(workspace)[-1]["params"] == {"line_id": "B"}
