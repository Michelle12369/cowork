"""`/chat`、`/repair` 的強制 inbound bearer 驗證——缺 header/錯 token/未加 `Bearer ` 前綴皆 401、
對 token通過、`/health` 豁免、token 未設定時 lifespan 啟動即炸。"""

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import AIMessage

from app import main as main_module
from app.agent import repair_flow
from app.config import get_settings
from tests.conftest import TEST_BEARER_TOKEN
from tests.fake_model import ScriptedChatModel

_REPAIR_PAYLOAD = {
    "sessionId": "sess-auth",
    "userId": "user-auth",
    "html": "<html></html>",
    "errors": [{"message": "TypeError: x is undefined"}],
}


async def _post_repair(headers: dict[str, str]) -> int:
    transport = ASGITransport(app=main_module.app)
    async with AsyncClient(transport=transport, base_url="http://test", headers=headers) as client:
        response = await client.post("/repair", json=_REPAIR_PAYLOAD)
    return response.status_code


async def test_repair_missing_authorization_header_returns_401() -> None:
    status_code = await _post_repair(headers={})
    assert status_code == 401


async def test_repair_wrong_token_returns_401() -> None:
    status_code = await _post_repair(headers={"Authorization": "Bearer wrong-token"})
    assert status_code == 401


async def test_repair_missing_bearer_prefix_returns_401() -> None:
    # header 存在但沒帶 "Bearer " 前綴——同樣視為未驗證,NEVER 寬鬆容忍。
    status_code = await _post_repair(headers={"Authorization": TEST_BEARER_TOKEN})
    assert status_code == 401


async def test_repair_valid_token_passes_auth(tmp_path, monkeypatch) -> None:
    # 淺層行為驗證:對的 token 一路通過 dependency 到 handler,拿到完整 200(而非被 401 擋下)。
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path / "ws"))
    monkeypatch.setattr(
        repair_flow,
        "build_model",
        lambda: ScriptedChatModel([AIMessage(content="```html\n<html></html>\n```")]),
    )
    status_code = await _post_repair(headers={"Authorization": f"Bearer {TEST_BEARER_TOKEN}"})
    assert status_code == 200


async def test_health_without_token_returns_200() -> None:
    transport = ASGITransport(app=main_module.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")
    assert response.status_code == 200


def test_missing_bearer_token_env_raises_on_lifespan_startup(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_API_BEARER_TOKEN", raising=False)
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="AGENT_API_BEARER_TOKEN"), TestClient(main_module.app):
        pass
