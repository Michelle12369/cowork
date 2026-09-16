"""scripts/dev_config.py: DEV_* key 讀取(CLI flag > one-local.properties > 預設; NEVER 讀
env var)與 DEV_CONNECTORS 的 JSON 驗證。conftest.py 的 `_isolate_one_properties` autouse
fixture 已把 ONE_PROPERTIES_PATH 指到不存在的路徑, 這裡要測檔案行為的測試自行 setenv 覆寫指向
tmp_path 下的檔案(`ONE_PROPERTIES_PATH` 本身仍是官方 env var, 不受這條「DEV_* 不讀 env」規則
影響——它只決定去讀哪個檔案)。"""

import traceback
from pathlib import Path

import pytest

from scripts.dev_config import (
    DEV_CONNECTORS,
    DEV_KEYS,
    SOURCE_DEFAULT,
    SOURCE_ENV,
    SOURCE_PROPERTIES,
    DevConfig,
    _print_shell_exports,
    connectors_needing_real_sso,
    dev_key_sources,
    load_dev_config,
    official_key_source,
    parse_dev_connectors,
    properties_path_source,
    resolve_shell_exports,
)


def test_load_dev_config_missingFile_usesDefaults() -> None:
    config = load_dev_config()

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

    config = load_dev_config()

    assert config.deepagent_url == "http://127.0.0.1:9000"
    assert config.sso_token == "file-token"
    assert config.sso_url is None


def test_load_dev_config_envVarsIgnored_onlyFileAndDefaultApply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DEV_* NEVER 讀 env(只有 dev 腳本讀這幾個 key, 刻意少一層; 不是因為 env 在 production
    用不到——compose 整包設定都走 env): 檔案有值就用檔案值(不是 env 的值), 檔案沒設的 key
    照樣落回內建預設(不會撿到 env 的值)。"""
    properties_file = tmp_path / "one-local.properties"
    properties_file.write_text("DEV_DEEPAGENT_URL=http://127.0.0.1:9000\n", encoding="utf-8")
    monkeypatch.setenv("ONE_PROPERTIES_PATH", str(properties_file))
    monkeypatch.setenv("DEV_DEEPAGENT_URL", "http://127.0.0.1:9999")
    monkeypatch.setenv("DEV_SSO_TOKEN", "env-token")

    config = load_dev_config()

    assert config.deepagent_url == "http://127.0.0.1:9000"
    assert config.sso_token is None


def test_load_dev_config_onePropertiesPathEnv_isHonoured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    properties_file = tmp_path / "custom.properties"
    properties_file.write_text("DEV_SSO_URL=https://sso.example\n", encoding="utf-8")
    monkeypatch.setenv("ONE_PROPERTIES_PATH", str(properties_file))

    config = load_dev_config()

    assert config.sso_url == "https://sso.example"


def test_load_dev_config_devConnectorsInFile_parsedIntoList(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    properties_file = tmp_path / "one-local.properties"
    properties_file.write_text(
        'DEV_CONNECTORS=[{"id":"sales","url":"http://127.0.0.1:8765/mcp"}]\n', encoding="utf-8"
    )
    monkeypatch.setenv("ONE_PROPERTIES_PATH", str(properties_file))

    config = load_dev_config()

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


def test_resolve_shell_exports_missingFile_usesDefaults() -> None:
    exports = resolve_shell_exports()

    assert exports == {"DEEPAGENT_PORT": "8000", "AGENT_WORKSPACE_ROOT": "/tmp/erd-spike-workspace"}


def test_resolve_shell_exports_fileValues_areUsed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    properties_file = tmp_path / "one-local.properties"
    properties_file.write_text(
        "DEV_DEEPAGENT_URL=http://127.0.0.1:8010\nAGENT_WORKSPACE_ROOT=/data/workspace\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ONE_PROPERTIES_PATH", str(properties_file))

    exports = resolve_shell_exports()

    assert exports == {"DEEPAGENT_PORT": "8010", "AGENT_WORKSPACE_ROOT": "/data/workspace"}


def test_resolve_shell_exports_urlWithoutExplicitPort_fallsBackToDefaultPort(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    properties_file = tmp_path / "one-local.properties"
    properties_file.write_text("DEV_DEEPAGENT_URL=http://127.0.0.1\n", encoding="utf-8")
    monkeypatch.setenv("ONE_PROPERTIES_PATH", str(properties_file))

    exports = resolve_shell_exports()

    assert exports["DEEPAGENT_PORT"] == "8000"


def test_resolve_shell_exports_extraKeysInFile_neverLeakIntoResult(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """檔案裡其他 key(含 secrets)一律不進回傳值——只有 DEEPAGENT_PORT/AGENT_WORKSPACE_ROOT
    這兩個 key 允許出現。"""
    secret_value = "sk-SHOULD-NEVER-APPEAR"
    properties_file = tmp_path / "one-local.properties"
    properties_file.write_text(
        f"AGENT_API_BEARER_TOKEN={secret_value}\nDEV_SSO_TOKEN=another-secret\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ONE_PROPERTIES_PATH", str(properties_file))

    exports = resolve_shell_exports()

    assert set(exports.keys()) == {"DEEPAGENT_PORT", "AGENT_WORKSPACE_ROOT"}
    assert secret_value not in exports.values()
    assert "another-secret" not in exports.values()


def test_print_shell_exports_printsExactlyTwoLines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    properties_file = tmp_path / "one-local.properties"
    properties_file.write_text("DEV_DEEPAGENT_URL=http://127.0.0.1:8010\n", encoding="utf-8")
    monkeypatch.setenv("ONE_PROPERTIES_PATH", str(properties_file))

    _print_shell_exports()

    printed_lines = capsys.readouterr().out.splitlines()
    assert printed_lines == [
        "DEEPAGENT_PORT=8010",
        "AGENT_WORKSPACE_ROOT=/tmp/erd-spike-workspace",
    ]


def test_dev_key_sources_missingFile_allDefault() -> None:
    assert dev_key_sources() == {key: SOURCE_DEFAULT for key in DEV_KEYS}


def test_dev_key_sources_fileValueIsProperties_envNeverCounts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    properties_file = tmp_path / "one-local.properties"
    properties_file.write_text("DEV_DEEPAGENT_URL=http://127.0.0.1:9000\n", encoding="utf-8")
    monkeypatch.setenv("ONE_PROPERTIES_PATH", str(properties_file))
    monkeypatch.setenv("DEV_SSO_TOKEN", "env-token")

    sources = dev_key_sources()

    assert sources["DEV_DEEPAGENT_URL"] == SOURCE_PROPERTIES
    assert sources["DEV_SSO_TOKEN"] == SOURCE_DEFAULT
    assert sources["DEV_CONNECTORS"] == SOURCE_DEFAULT


def test_official_key_source_envBeatsFileBeatsDefault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    properties_file = tmp_path / "one-local.properties"
    properties_file.write_text(
        "AGENT_API_BEARER_TOKEN=file-token\nSSO_TOKEN_HEADER=X-File-Token\n", encoding="utf-8"
    )
    monkeypatch.setenv("ONE_PROPERTIES_PATH", str(properties_file))
    monkeypatch.delenv("AGENT_API_BEARER_TOKEN", raising=False)
    monkeypatch.setenv("SSO_TOKEN_HEADER", "X-Env-Token")
    monkeypatch.delenv("SSO_URL_HEADER", raising=False)

    assert official_key_source("AGENT_API_BEARER_TOKEN") == SOURCE_PROPERTIES
    assert official_key_source("SSO_TOKEN_HEADER") == SOURCE_ENV
    assert official_key_source("SSO_URL_HEADER") == SOURCE_DEFAULT


def test_official_key_source_emptyEnvValue_isIgnored(monkeypatch: pytest.MonkeyPatch) -> None:
    """空 env 值視為未設(同 Settings 的 env_ignore_empty), 來源落回 default."""
    monkeypatch.setenv("AGENT_API_BEARER_TOKEN", "")

    assert official_key_source("AGENT_API_BEARER_TOKEN") == SOURCE_DEFAULT


def test_properties_path_source_envSet_isEnv_elseDefault(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ONE_PROPERTIES_PATH", "/tmp/some.properties")
    assert properties_path_source() == SOURCE_ENV

    monkeypatch.delenv("ONE_PROPERTIES_PATH")
    assert properties_path_source() == SOURCE_DEFAULT


@pytest.mark.parametrize(
    "loopback_url",
    [
        "http://127.0.0.1:8765/mcp",
        "http://127.0.0.2:8765/mcp",  # 整個 127.0.0.0/8 都是 loopback
        "http://localhost:8765/mcp",
        "http://LOCALHOST:8765/mcp",  # hostname 會轉小寫
        "http://mock.localhost/mcp",  # RFC 6761: .localhost 子網域
        "http://[::1]:8766/mcp",  # IPv6 loopback, 中括號由 hostname 去掉
    ],
)
def test_connectors_needing_real_sso_loopbackHosts_needNone(loopback_url: str) -> None:
    assert connectors_needing_real_sso([{"id": "local", "url": loopback_url}]) == []


@pytest.mark.parametrize(
    "remote_url",
    [
        "https://mes.example/mcp",
        "http://10.0.0.5:8765/mcp",
        "http://[2001:db8::1]/mcp",
        "not-a-url",  # 解析不出 host 一律當遠端(方向安全的那邊)
    ],
)
def test_connectors_needing_real_sso_remoteHosts_areListed(remote_url: str) -> None:
    assert connectors_needing_real_sso([{"id": "remote", "url": remote_url}]) == ["remote"]


def test_connectors_needing_real_sso_mixed_keepsInputOrderAndOnlyRemotes() -> None:
    connectors = [
        {"id": "local", "url": "http://127.0.0.1:8765/mcp"},
        {"id": "mes", "url": "https://mes.example/mcp"},
        {"id": "crm", "url": "https://crm.example/mcp"},
    ]

    assert connectors_needing_real_sso(connectors) == ["mes", "crm"]


def test_connectors_needing_real_sso_hostnameNeverResolvedViaDns() -> None:
    """NEVER 為了判斷做 DNS 查詢: 即使某個名字實際解析到 127.0.0.1, 也一律當遠端——寧可多要求
    一次真值, 也不要因為解析結果而默默把假憑證送出去。"""
    assert connectors_needing_real_sso(
        [{"id": "aliased", "url": "http://localhost.example.com/mcp"}]
    ) == ["aliased"]
