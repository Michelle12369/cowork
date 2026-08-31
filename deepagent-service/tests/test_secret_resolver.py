"""`secret_resolver` 接縫的 repo 預設實作(環境變數查找)——正常解析、缺值/空值 fail-loud
且訊息只含參照名不含值。"""

import pytest

from app.engine.secret_resolver import SecretResolutionError, resolve_secret


def test_resolve_secret_envVarSet_returnsValue(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_CONNECTOR_TOKEN", "shh-secret-value")

    assert resolve_secret("MY_CONNECTOR_TOKEN") == "shh-secret-value"


def test_resolve_secret_envVarMissing_raisesWithRefNameNoValue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MISSING_CONNECTOR_TOKEN", raising=False)

    with pytest.raises(SecretResolutionError) as error_info:
        resolve_secret("MISSING_CONNECTOR_TOKEN")

    assert "MISSING_CONNECTOR_TOKEN" in str(error_info.value)


def test_resolve_secret_envVarEmpty_raisesWithRefNameNoValue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EMPTY_CONNECTOR_TOKEN", "")

    with pytest.raises(SecretResolutionError) as error_info:
        resolve_secret("EMPTY_CONNECTOR_TOKEN")

    assert "EMPTY_CONNECTOR_TOKEN" in str(error_info.value)
