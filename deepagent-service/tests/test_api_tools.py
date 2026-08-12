"""tests/test_api_tools.py——httpx.MockTransport,不起真 server。"""

import dataclasses
import json

import duckdb
import httpx
import pytest
from app.agent.tools.api_data import build_api_tools

from app.agent.tools.framing import DATA_FRAME_OPEN
from app.engine.api_registry import API_REGISTRY
from app.engine.workspace import SessionWorkspace

ORDER_ROWS = [
    {"order_id": 1, "machine": "M1", "amount": 120.5},
    {"order_id": 2, "machine": "M3", "amount": 88.0},
]


@pytest.fixture(autouse=True)
def _default_mock_base_url(monkeypatch):
    # httpx.Client 對 scheme-less base_url("")在 cookie 萃取階段一律炸(urllib "unknown url
    # type")——與是否有 transport 覆寫無關。這裡給全檔測試一個預設合法 base_url,個別測試(如
    # test_fetch_success)仍可再 setenv 覆寫成別的值,兩者皆合法不衝突。
    monkeypatch.setenv("API_MOCK_BASE_URL", "http://mock-api")


def _make_tool(tmp_path, handler, connection=None, registry=None):
    if connection is None:
        connection = duckdb.connect(":memory:")
        connection.execute("SET enable_external_access = false")
        connection.execute("SET lock_configuration = true")
    workspace = SessionWorkspace(root=tmp_path)
    transport = httpx.MockTransport(handler)
    (fetch_tool,) = build_api_tools(
        connection, workspace, registry or API_REGISTRY, transport=transport
    )
    return fetch_tool, connection, workspace


def _ok_handler(request: httpx.Request) -> httpx.Response:
    assert request.url.params["machines"] == "M1,M3"  # multi→逗號串
    return httpx.Response(200, json=ORDER_ROWS)


def test_fetch_success_mounts_table_queryable_on_locked_connection(tmp_path, monkeypatch):
    monkeypatch.setenv("API_MOCK_BASE_URL", "http://mock-api")
    fetch_tool, connection, workspace = _make_tool(tmp_path, _ok_handler)
    result = fetch_tool.invoke(
        {"source_id": "mock_orders", "params": {"date_range": "30d", "machines": ["M1", "M3"]}}
    )
    assert "api_orders" in result and DATA_FRAME_OPEN in result
    assert connection.execute('SELECT count(*) FROM "api_orders"').fetchone()[0] == 2
    assert (workspace.api_dir / "api_orders.meta.json").is_file()
    assert (workspace.api_dir / "api_orders.raw.json").is_file()


def test_unknown_source_id_returns_param_error(tmp_path):
    fetch_tool, _, _ = _make_tool(tmp_path, _ok_handler)
    result = fetch_tool.invoke({"source_id": "nope", "params": {}})
    assert result.startswith("PARAM_ERROR:") and "mock_orders" in result


def test_invalid_params_returns_param_error(tmp_path):
    fetch_tool, _, _ = _make_tool(tmp_path, _ok_handler)
    result = fetch_tool.invoke({"source_id": "mock_orders", "params": {"date_range": "365d"}})
    assert result.startswith("PARAM_ERROR:")


def test_alias_collision_with_uploaded_file_returns_param_error(tmp_path):
    connection = duckdb.connect(":memory:")
    connection.execute('CREATE TABLE "api_orders" (existing INTEGER)')
    connection.execute("SET enable_external_access = false")
    connection.execute("SET lock_configuration = true")
    fetch_tool, _, _ = _make_tool(tmp_path, _ok_handler, connection=connection)
    result = fetch_tool.invoke(
        {"source_id": "mock_orders", "params": {"date_range": "7d", "machines": ["M1"]}}
    )
    assert result.startswith("PARAM_ERROR:") and "api_orders" in result


def test_http_500_and_bad_shape_return_api_error(tmp_path):
    fetch_tool, _, _ = _make_tool(tmp_path, lambda request: httpx.Response(500))
    result = fetch_tool.invoke(
        {"source_id": "mock_orders", "params": {"date_range": "7d", "machines": ["M1"]}}
    )
    assert result.startswith("API_ERROR:")

    fetch_tool, _, _ = _make_tool(
        tmp_path, lambda request: httpx.Response(200, json={"not": "an array"})
    )
    result = fetch_tool.invoke(
        {"source_id": "mock_orders", "params": {"date_range": "7d", "machines": ["M1"]}}
    )
    assert result.startswith("API_ERROR:") and "shape" in result


def test_refetch_overwrites_snapshot_and_table(tmp_path):
    responses = iter([ORDER_ROWS, ORDER_ROWS[:1]])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=next(responses))

    fetch_tool, connection, workspace = _make_tool(tmp_path, handler)
    request_params = {
        "source_id": "mock_orders",
        "params": {"date_range": "7d", "machines": ["M1"]},
    }
    fetch_tool.invoke(request_params)
    fetch_tool.invoke(request_params)
    assert connection.execute('SELECT count(*) FROM "api_orders"').fetchone()[0] == 1
    meta = json.loads((workspace.api_dir / "api_orders.meta.json").read_text(encoding="utf-8"))
    assert meta["row_count"] == 1


def test_response_truncated_to_max_rows(tmp_path):
    small_registry = {"mock_orders": dataclasses.replace(API_REGISTRY["mock_orders"], max_rows=3)}
    over_limit_rows = [
        {"order_id": index, "machine": "M1", "amount": float(index)} for index in range(5)
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=over_limit_rows)

    fetch_tool, connection, workspace = _make_tool(tmp_path, handler, registry=small_registry)
    result = fetch_tool.invoke(
        {"source_id": "mock_orders", "params": {"date_range": "7d", "machines": ["M1"]}}
    )
    assert connection.execute('SELECT count(*) FROM "api_orders"').fetchone()[0] == 3
    assert "truncated" in result
    meta = json.loads((workspace.api_dir / "api_orders.meta.json").read_text(encoding="utf-8"))
    assert meta["truncated"] is True
    assert meta["row_count"] == 3


def test_connect_timeout_returns_api_error(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    fetch_tool, _, _ = _make_tool(tmp_path, handler)
    result = fetch_tool.invoke(
        {"source_id": "mock_orders", "params": {"date_range": "7d", "machines": ["M1"]}}
    )
    assert result.startswith("API_ERROR:")


def test_missing_base_url_returns_api_error(tmp_path, monkeypatch):
    monkeypatch.setenv("API_MOCK_BASE_URL", "")
    fetch_tool, _, _ = _make_tool(tmp_path, _ok_handler)
    result = fetch_tool.invoke(
        {"source_id": "mock_orders", "params": {"date_range": "7d", "machines": ["M1"]}}
    )
    assert result == "API_ERROR: API_MOCK_BASE_URL not configured"
