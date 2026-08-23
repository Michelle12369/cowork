"""tests/test_connectors_endpoint.py -- `GET /connectors`(§11 Task 2):使用者端 connector
group 勾選清單來源。功能關閉(未設定 AGENT_CONNECTORS_FILE)時 MUST 回空 list、絕不 500,
與 build_agent 的空 registry 不變式呼應(見 test_graph.py)。"""

from fastapi.testclient import TestClient

from app.main import app

_GROUPED_CONNECTORS_YAML = """\
connector_groups:
  - name: mes
    display: "MES 製造執行系統"
    description: 產線良率、缺陷、產能
    members:
      - name: mes_yield
        kind: data
        description: 產線良率
        endpoint: http://connector.internal/yield
        method: GET
        params: {}
  - name: erp
    display: "ERP 企業資源規劃"
    description: 訂單、庫存
    members:
      - name: erp_orders
        kind: data
        description: 訂單清單
        endpoint: http://connector.internal/orders
        method: GET
        params: {}
"""


def test_connectors_endpoint_groupedConfig_returnsGroupList(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "connectors.yaml"
    config_path.write_text(_GROUPED_CONNECTORS_YAML, encoding="utf-8")
    monkeypatch.setenv("AGENT_CONNECTORS_FILE", str(config_path))

    client = TestClient(app)
    response = client.get("/connectors")

    assert response.status_code == 200
    assert response.json() == [
        {"name": "mes", "display": "MES 製造執行系統", "description": "產線良率、缺陷、產能"},
        {"name": "erp", "display": "ERP 企業資源規劃", "description": "訂單、庫存"},
    ]


def test_connectors_endpoint_noConfig_returnsEmptyListNoCrash(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_CONNECTORS_FILE", raising=False)

    client = TestClient(app)
    response = client.get("/connectors")

    assert response.status_code == 200
    assert response.json() == []


def test_connectors_endpoint_missingConfigFile_returnsEmptyListNoCrash(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("AGENT_CONNECTORS_FILE", str(tmp_path / "absent.yaml"))

    client = TestClient(app)
    response = client.get("/connectors")

    assert response.status_code == 200
    assert response.json() == []
