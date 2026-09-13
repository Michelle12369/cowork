"""spike/mcp-shell/bridge.py: connector 轉送、INVALID_CALL、import-time guard。

`bridge.py` 讀 module-level 設定(DEV_CONNECTORS、AGENT_API_BEARER_TOKEN), 所以每個測試都用
importlib 重新載入一份乾淨的 module, 並確保 ONE_PROPERTIES_PATH 指到 tmp_path 下的檔案——絕不
讀真的 one-local.properties。DEV_CONNECTORS NEVER 讀 env(見 scripts/dev_config.py), 所以是寫
進這個 tmp 檔案, 不是 setenv。get_settings 是 process 級 lru_cache(AGENT_API_BEARER_TOKEN 仍走
env > 檔案 > 預設, 是官方 key, 不受這條規則影響), import 前後都清快取, 避免這裡設的 env 洩漏到
其他測試, 也避免讀到其他測試留下的快取值。
"""

import importlib.util
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings

BRIDGE_PATH = Path(__file__).resolve().parent.parent / "spike" / "mcp-shell" / "bridge.py"

TWO_CONNECTORS_JSON = (
    '[{"id":"sales","url":"http://127.0.0.1:8765/mcp"},'
    '{"id":"crm","url":"http://127.0.0.1:8766/mcp"}]'
)


def _load_bridge_module(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, dev_connectors: str
) -> object:
    properties_file = tmp_path / "one-local.properties"
    properties_file.write_text(f"DEV_CONNECTORS={dev_connectors}\n", encoding="utf-8")
    monkeypatch.setenv("ONE_PROPERTIES_PATH", str(properties_file))
    monkeypatch.setenv("AGENT_API_BEARER_TOKEN", "bridge-test-token")
    get_settings.cache_clear()
    module_spec = importlib.util.spec_from_file_location("spike_mcp_shell_bridge", BRIDGE_PATH)
    assert module_spec is not None and module_spec.loader is not None
    bridge_module = importlib.util.module_from_spec(module_spec)
    try:
        module_spec.loader.exec_module(bridge_module)
    finally:
        get_settings.cache_clear()
    return bridge_module


def test_import_devConnectorsEmpty_raisesRuntimeError(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with pytest.raises(RuntimeError, match="DEV_CONNECTORS"):
        _load_bridge_module(monkeypatch, tmp_path, dev_connectors="[]")


def test_call_mcp_tool_unknownConnector_returnsInvalidCallBody(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bridge_module = _load_bridge_module(monkeypatch, tmp_path, dev_connectors=TWO_CONNECTORS_JSON)
    client = TestClient(bridge_module.app)

    response = client.post("/api/mcp/call", json={"connector": "nope", "tool": "x", "args": {}})

    assert response.status_code == 200
    assert response.json() == {
        "error": {
            "code": "INVALID_CALL",
            "message": "connector 'nope' is not enabled for this session; allowed: crm, sales",
        }
    }


def test_call_mcp_tool_knownConnector_forwardsConnectorSpecToToolCall(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bridge_module = _load_bridge_module(monkeypatch, tmp_path, dev_connectors=TWO_CONNECTORS_JSON)
    captured_calls: list[dict] = []

    class _FakeToolCallResponse:
        status_code = 200

        def json(self) -> dict:
            return {"data": 1}

    async def fake_post(self, url, *, json=None, headers=None, **kwargs):
        captured_calls.append({"url": url, "json": json, "headers": headers})
        return _FakeToolCallResponse()

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    client = TestClient(bridge_module.app)
    response = client.post(
        "/api/mcp/call", json={"connector": "sales", "tool": "list_regions", "args": {"a": 1}}
    )

    assert response.status_code == 200
    assert response.json() == {"data": 1}
    assert len(captured_calls) == 1
    forwarded = captured_calls[0]
    assert forwarded["url"] == f"{bridge_module._DEEPAGENT_URL}/tool-call"
    assert forwarded["json"]["connector"] == {
        "id": "sales",
        "name": "Sales",
        "url": "http://127.0.0.1:8765/mcp",
        "bearerTokenKey": None,
    }
    assert forwarded["json"]["tool"] == "list_regions"
    assert forwarded["json"]["args"] == {"a": 1}
