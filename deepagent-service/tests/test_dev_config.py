"""scripts/dev_config.py: DEV_* key 讀取(env > one-local.properties > 預設)與 DEV_CONNECTORS
的 JSON 驗證。conftest.py 的 `_isolate_one_properties` autouse fixture 已把 ONE_PROPERTIES_PATH
指到不存在的路徑, 這裡要測檔案行為的測試自行 setenv 覆寫指向 tmp_path 下的檔案。"""

import traceback
from pathlib import Path

import pytest

from scripts.dev_config import DEV_CONNECTORS, DevConfig, load_dev_config, parse_dev_connectors


def test_load_dev_config_missingFile_usesDefaults() -> None:
    config = load_dev_config({})

    assert config == DevConfig(
        deepagent_url="http://127.0.0.1:8000", sso_token=None, sso_url=None, connectors=[]
    )


def test_load_dev_config_fileValues_areUsed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    properties_file = tmp_path / "one-local.properties"
    properties_file.write_text(
        "DEV_DEEPAGENT_URL=http://127.0.0.1:9000\nDEV_SSO_TOKEN=file-token\n", encoding="utf-8"
    )
    monkeypatch.setenv("ONE_PROPERTIES_PATH", str(properties_file))

    config = load_dev_config({})

    assert config.deepagent_url == "http://127.0.0.1:9000"
    assert config.sso_token == "file-token"
    assert config.sso_url is None


def test_load_dev_config_envOverridesFile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    properties_file = tmp_path / "one-local.properties"
    properties_file.write_text("DEV_DEEPAGENT_URL=http://127.0.0.1:9000\n", encoding="utf-8")
    monkeypatch.setenv("ONE_PROPERTIES_PATH", str(properties_file))

    config = load_dev_config({"DEV_DEEPAGENT_URL": "http://127.0.0.1:9999"})

    assert config.deepagent_url == "http://127.0.0.1:9999"


def test_load_dev_config_onePropertiesPathEnv_isHonoured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    properties_file = tmp_path / "custom.properties"
    properties_file.write_text("DEV_SSO_URL=https://sso.example\n", encoding="utf-8")
    monkeypatch.setenv("ONE_PROPERTIES_PATH", str(properties_file))

    config = load_dev_config({})

    assert config.sso_url == "https://sso.example"


def test_load_dev_config_devConnectorsInFile_parsedIntoList(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    properties_file = tmp_path / "one-local.properties"
    properties_file.write_text(
        'DEV_CONNECTORS=[{"id":"sales","url":"http://127.0.0.1:8765/mcp"}]\n', encoding="utf-8"
    )
    monkeypatch.setenv("ONE_PROPERTIES_PATH", str(properties_file))

    config = load_dev_config({})

    assert config.connectors == [
        {
            "id": "sales",
            "name": "Sales",
            "url": "http://127.0.0.1:8765/mcp",
            "bearerTokenKey": None,
        }
    ]


def test_parse_dev_connectors_validList_defaultsNameAndKeepsBearerTokenKey() -> None:
    raw_value = (
        '[{"id":"sales","url":"http://x/mcp"},'
        '{"id":"mes","name":"MES","url":"https://mes.example/mcp","bearerTokenKey":"mes-key"}]'
    )

    connectors = parse_dev_connectors(raw_value)

    assert connectors[0] == {
        "id": "sales",
        "name": "Sales",
        "url": "http://x/mcp",
        "bearerTokenKey": None,
    }
    assert connectors[1] == {
        "id": "mes",
        "name": "MES",
        "url": "https://mes.example/mcp",
        "bearerTokenKey": "mes-key",
    }


def test_parse_dev_connectors_invalidJson_raisesValueErrorNamingKey() -> None:
    with pytest.raises(ValueError, match=DEV_CONNECTORS) as excinfo:
        parse_dev_connectors("not-json-SECRETVALUE123")
    assert "SECRETVALUE123" not in str(excinfo.value)


def test_parse_dev_connectors_nonList_raisesValueErrorNamingKey() -> None:
    with pytest.raises(ValueError, match=DEV_CONNECTORS):
        parse_dev_connectors('{"id": "sales"}')


def test_parse_dev_connectors_missingRequiredField_raisesValueErrorWithoutRawValue() -> None:
    raw_value = '[{"id": "sales"}]'  # url 缺, url 裡常藏 token, 錯誤訊息 NEVER 帶原始值
    with pytest.raises(ValueError, match=DEV_CONNECTORS) as excinfo:
        parse_dev_connectors(raw_value)
    assert raw_value not in str(excinfo.value)


def test_parse_dev_connectors_missingUrl_secretInBearerTokenKeyNeverLeaksIntoTraceback() -> None:
    # 重現 review 抓到的洩漏: bearerTokenKey/url 裡的 token 曾經隨 pydantic 的
    # ValidationError(`input_value={...}`)被 `raise ... from validation_error` 串進因果鏈,
    # 印進整條 traceback。斷言整條 rendered traceback(不只 str(exc)), 因為 traceback 會連
    # `__cause__`/`__context__` 一起印出來, str(exc) 測不到那段。
    secret_value = "sk-LEAK1234"
    raw_value = f'[{{"id": "s", "bearerTokenKey": "{secret_value}"}}]'  # url 缺 -> pydantic 拒絕
    with pytest.raises(ValueError, match=DEV_CONNECTORS) as excinfo:
        parse_dev_connectors(raw_value)
    rendered_traceback = "".join(traceback.format_exception(excinfo.value))
    assert secret_value not in rendered_traceback
    assert raw_value not in rendered_traceback


def test_parse_dev_connectors_entryNotObject_raisesValueErrorNamingIndex() -> None:
    with pytest.raises(ValueError, match=r"DEV_CONNECTORS\[0\]"):
        parse_dev_connectors('["not-an-object"]')
