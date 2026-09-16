"""spike/mcp-shell/bridge.py: connector 轉送、INVALID_CALL、import-time guard。

`bridge.py` 讀 module-level 設定(DEV_CONNECTORS、AGENT_API_BEARER_TOKEN), 所以每個測試都用
importlib 重新載入一份乾淨的 module, 並確保 ONE_PROPERTIES_PATH 指到 tmp_path 下的檔案——絕不
讀真的 one-local.properties。DEV_CONNECTORS 寫進這個 tmp 檔案(env 也可以, 兩者同一條規則;
conftest 已清掉開發者 shell 裡的 DEV_* env)。get_settings 是 process 級 lru_cache, import
前後都清快取, 避免這裡設的 env 洩漏到其他測試, 也避免讀到其他測試留下的快取值。
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
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    dev_connectors: str,
    extra_properties: str = "",
) -> object:
    properties_file = tmp_path / "one-local.properties"
    properties_file.write_text(
        f"DEV_CONNECTORS={dev_connectors}\n{extra_properties}", encoding="utf-8"
    )
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


def test_import_remoteConnectorWithoutSso_raisesRuntimeError(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """非 loopback 的 connector + 沒設 SSO 值 = 假憑證會送到真的 MCP server, import 就要擋下來,
    而不是讓它變成 dashboard 裡一張 AUTH 卡。"""
    with pytest.raises(RuntimeError, match="loopback"):
        _load_bridge_module(
            monkeypatch,
            tmp_path,
            dev_connectors='[{"id":"mes","url":"https://mes.example/mcp"}]',
        )


def test_import_remoteConnectorWithSso_loadsNormally(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bridge_module = _load_bridge_module(
        monkeypatch,
        tmp_path,
        dev_connectors='[{"id":"mes","url":"https://mes.example/mcp"}]',
        extra_properties="DEV_SSO_TOKEN=real-token\nDEV_SSO_URL=https://sso.example\n",
    )

    assert sorted(bridge_module._CONNECTORS_BY_ID) == ["mes"]


def test_import_loopbackConnectorsWithoutSso_loadsWithPlaceholders(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bridge_module = _load_bridge_module(monkeypatch, tmp_path, dev_connectors=TWO_CONNECTORS_JSON)

    assert bridge_module._DEV_SSO_TOKEN == "spike"
    assert bridge_module._DEV_SSO_URL == "http://spike.invalid"


def test_bridge_import_envShadowsFile_logsWarningNamingKeyOnly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """檔案有 DEV_SSO_TOKEN、env 也有(env 贏): 啟動時記一條 warning 指名 key, NEVER 帶值。"""
    monkeypatch.setenv("DEV_SSO_TOKEN", "env-sso-SECRET")
    with caplog.at_level("WARNING", logger="bridge"):
        _load_bridge_module(
            monkeypatch,
            tmp_path,
            dev_connectors=TWO_CONNECTORS_JSON,
            extra_properties="DEV_SSO_TOKEN=file-sso-SECRET\n",
        )

    warning_messages = [record.getMessage() for record in caplog.records]
    assert any("DEV_SSO_TOKEN 來自 env" in message for message in warning_messages)
    assert "SECRET" not in "\n".join(warning_messages)
