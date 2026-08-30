"""`GET /connectors`——目錄端點：回傳 load_connectors() 的 id/name 清單、與其他端點同一套
bearer auth(缺/錯 token 一律 401)。"""

from httpx import ASGITransport, AsyncClient

from app import main as main_module
from app.agent.connectors.registry import demo_connector
from tests.conftest import TEST_BEARER_TOKEN


async def _get_connectors(headers: dict[str, str]):
    transport = ASGITransport(app=main_module.app)
    async with AsyncClient(transport=transport, base_url="http://test", headers=headers) as client:
        return await client.get("/connectors")


async def test_connectors_valid_token_returns_demo_connector() -> None:
    response = await _get_connectors(headers={"Authorization": f"Bearer {TEST_BEARER_TOKEN}"})
    assert response.status_code == 200
    expected_connector = demo_connector()
    assert response.json() == [
        {"id": expected_connector.connector_id, "name": expected_connector.display_name}
    ]


async def test_connectors_missing_authorization_header_returns_401() -> None:
    response = await _get_connectors(headers={})
    assert response.status_code == 401


async def test_connectors_wrong_token_returns_401() -> None:
    response = await _get_connectors(headers={"Authorization": "Bearer wrong-token"})
    assert response.status_code == 401
