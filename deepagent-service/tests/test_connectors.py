"""tests/test_connectors.py"""

from pathlib import Path

import pytest

from app.engine.connectors import ConnectorConfigError, load_connector_registry

VALID_YAML = """\
connectors:
  - name: line_list
    kind: lookup
    description: 產線清單
    endpoint: ${TEST_API_BASE}/lines
    method: GET
    auth: bearer:TEST_API_TOKEN
    params: {}
  - name: mes_yield
    kind: data
    description: 產線良率
    endpoint: ${TEST_API_BASE}/yield
    method: POST
    auth: bearer:TEST_API_TOKEN
    params:
      line_id:
        type: str
        required: true
        validate_against: {connector: line_list, column: line_id}
      start_date: {type: date, required: true}
    limits: {timeout_s: 10, max_bytes: 1000000, max_rows: 50000}
"""


def _write_config(tmp_path: Path, text: str) -> Path:
    config_path = tmp_path / "connectors.yaml"
    config_path.write_text(text, encoding="utf-8")
    return config_path


def test_load_registry_validConfig_parsesDefinitions(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_API_BASE", "http://api.internal")
    registry = load_connector_registry(_write_config(tmp_path, VALID_YAML))
    assert not registry.is_empty()
    yield_definition = registry.get("mes_yield")
    assert yield_definition.kind == "data"
    assert yield_definition.endpoint == "http://api.internal/yield"
    assert yield_definition.params["line_id"].validate_against.connector == "line_list"
    assert [d.name for d in registry.lookup_connectors()] == ["line_list"]


def test_load_registry_missingFile_returnsEmptyRegistry(tmp_path):
    assert load_connector_registry(tmp_path / "absent.yaml").is_empty()
    assert load_connector_registry(None).is_empty()


def test_load_registry_unknownValidateAgainstConnector_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_API_BASE", "http://api.internal")
    broken = VALID_YAML.replace("connector: line_list", "connector: no_such")
    with pytest.raises(ConnectorConfigError, match="no_such"):
        load_connector_registry(_write_config(tmp_path, broken))


def test_load_registry_missingEnvVar_raisesWithVarName(tmp_path):
    with pytest.raises(ConnectorConfigError, match="TEST_API_BASE"):
        load_connector_registry(_write_config(tmp_path, VALID_YAML))


def test_load_registry_nullConnectorsKey_returnsEmptyRegistry(tmp_path):
    # `connectors:` key 存在但值為 null(YAML 空值寫法)時 raw_document.get("connectors")
    # 拿到 None,不是缺席時的預設值——必須攤平成空 registry,不得對 None 迭代拋 TypeError。
    assert load_connector_registry(_write_config(tmp_path, "connectors:\n")).is_empty()


def test_load_registry_duplicateName_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_API_BASE", "http://api.internal")
    duplicated = VALID_YAML + VALID_YAML.split("connectors:\n")[1]
    with pytest.raises(ConnectorConfigError, match="duplicate"):
        load_connector_registry(_write_config(tmp_path, duplicated))
