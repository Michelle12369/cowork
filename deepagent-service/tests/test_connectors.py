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


GROUPED_YAML = """\
connector_groups:
  - name: mes
    display: "MES 製造執行系統"
    description: 產線良率、缺陷、產能
    members:
      - name: mes_line_list
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
            validate_against: {connector: mes_line_list, column: line_id}
          start_date: {type: date, required: true}
        limits: {timeout_s: 10, max_bytes: 1000000, max_rows: 50000}
  - name: erp
    display: "ERP 企業資源規劃"
    description: 訂單、庫存
    members:
      - name: erp_orders
        kind: data
        description: 訂單清單
        endpoint: ${TEST_API_BASE}/orders
        method: GET
        auth: bearer:TEST_API_TOKEN
        params: {}
"""


def test_load_registry_groupedConfig_membersCarryGroup(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_API_BASE", "http://api.internal")
    registry = load_connector_registry(_write_config(tmp_path, GROUPED_YAML))
    assert registry.get("mes_line_list").group == "mes"
    assert registry.get("mes_yield").group == "mes"
    assert registry.get("erp_orders").group == "erp"
    groups = {group.name: group for group in registry.groups()}
    assert groups["mes"].display == "MES 製造執行系統"
    assert groups["mes"].description == "產線良率、缺陷、產能"
    assert [member.name for member in groups["mes"].members] == ["mes_line_list", "mes_yield"]


def test_load_registry_legacyFlatConfig_wrapsAsDefaultGroup(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_API_BASE", "http://api.internal")
    registry = load_connector_registry(_write_config(tmp_path, VALID_YAML))
    assert registry.get("mes_yield").group == "default"
    groups = registry.groups()
    assert len(groups) == 1
    assert groups[0].name == "default"
    assert groups[0].display == "資料源"


def test_load_registry_crossGroupDuplicateName_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_API_BASE", "http://api.internal")
    duplicated_across_groups = GROUPED_YAML.replace("erp_orders", "mes_yield")
    with pytest.raises(ConnectorConfigError, match="mes_yield"):
        load_connector_registry(_write_config(tmp_path, duplicated_across_groups))


def test_filterByGroups_emptySelection_returnsSelf(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_API_BASE", "http://api.internal")
    registry = load_connector_registry(_write_config(tmp_path, GROUPED_YAML))
    assert registry.filter_by_groups([]) is registry


def test_filterByGroups_selectedGroup_returnsSubset(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_API_BASE", "http://api.internal")
    registry = load_connector_registry(_write_config(tmp_path, GROUPED_YAML))
    filtered = registry.filter_by_groups(["mes"])
    assert {definition.name for definition in filtered.all()} == {"mes_line_list", "mes_yield"}


def test_filterByGroups_unknownGroup_ignoredWithoutRaising(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_API_BASE", "http://api.internal")
    registry = load_connector_registry(_write_config(tmp_path, GROUPED_YAML))
    filtered = registry.filter_by_groups(["no_such_group"])
    assert filtered.is_empty()
