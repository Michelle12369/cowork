"""scripts/dev_config.py: 每個 key 同一條規則(CLI flag > env > one-local.properties > 預設)、
DEV_CONNECTORS 的 JSON 驗證、shell exports。conftest.py 的 `_isolate_one_properties` autouse
fixture 已把 ONE_PROPERTIES_PATH 指到不存在的路徑並清掉 DEV_* env, 這裡要測檔案或 env 行為的
測試自行 setenv(`ONE_PROPERTIES_PATH` 本身只有 env > 預設兩層——它決定去讀哪個檔案)。
`resolve()` 會走 `get_settings()`(process 級 lru_cache), 同一個測試裡改了 env 再 resolve 一次
前要先清快取。"""

import traceback
from pathlib import Path

import pytest

from app.config import get_settings
from scripts.dev_config import (
    AGENT_API_BEARER_TOKEN,
    DEV_CONNECTORS,
    DEV_DEEPAGENT_URL,
    DEV_SSO_TOKEN,
    DEV_SSO_URL,
    ONE_PROPERTIES_PATH,
    SOURCE_CLI,
    SOURCE_DEFAULT,
    SOURCE_ENV,
    SOURCE_PROPERTIES,
    SSO_TOKEN_HEADER,
    SSO_URL_HEADER,
    DevSetting,
    _print_shell_exports,
    connectors_needing_real_sso,
    env_shadow_warning_lines,
    env_shadowed_keys,
    key_source,
    parse_dev_connectors,
    resolve,
    resolve_shell_exports,
)


def _write_properties(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: str) -> Path:
    properties_file = tmp_path / "one-local.properties"
    properties_file.write_text(content, encoding="utf-8")
    monkeypatch.setenv("ONE_PROPERTIES_PATH", str(properties_file))
    return properties_file


def test_resolve_missingFile_usesDefaults() -> None:
    config = resolve()

    assert config.deepagent_url == "http://127.0.0.1:8000"
    assert config.sso_token is None
    assert config.sso_url is None
    assert config.connectors == []
    assert config.sso_token_header == "X-SSO-Token"
    assert config.sso_url_header == "X-SSO-Url"
    # conftest 的 autouse fixture 給了 bearer token(env), 其餘全 default.
    assert config.bearer_token == "test-bearer-token"
    assert [setting.key for setting in config.settings] == [
        ONE_PROPERTIES_PATH,
        DEV_DEEPAGENT_URL,
        AGENT_API_BEARER_TOKEN,
        DEV_SSO_TOKEN,
        DEV_SSO_URL,
        DEV_CONNECTORS,
        SSO_TOKEN_HEADER,
        SSO_URL_HEADER,
    ]
    # conftest 的兩個 autouse fixture 各設了一個 env(ONE_PROPERTIES_PATH、bearer token), 其餘全 default.
    env_sourced = {ONE_PROPERTIES_PATH, AGENT_API_BEARER_TOKEN}
    assert config.setting(AGENT_API_BEARER_TOKEN).source == SOURCE_ENV
    assert {setting.source for setting in config.settings if setting.key not in env_sourced} == {
        SOURCE_DEFAULT
    }


def test_resolve_fileValues_areUsed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_properties(
        tmp_path,
        monkeypatch,
        "DEV_DEEPAGENT_URL=http://127.0.0.1:9000\nDEV_SSO_TOKEN=file-token\n"
        "SSO_TOKEN_HEADER=X-File-Token\n",
    )

    config = resolve()

    assert config.deepagent_url == "http://127.0.0.1:9000"
    assert config.sso_token == "file-token"
    assert config.sso_url is None
    assert config.sso_token_header == "X-File-Token"
    assert config.setting(DEV_DEEPAGENT_URL).source == SOURCE_PROPERTIES
    assert config.setting(SSO_TOKEN_HEADER).source == SOURCE_PROPERTIES
    assert config.setting(DEV_SSO_URL).source == SOURCE_DEFAULT


def test_resolve_envBeatsFile_forDevAndOfficialKeysAlike(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """單一規則: DEV_* 跟官方 key 一樣有 env 層, env 蓋過檔案; 檔案沒設的 key 也會撿到 env。"""
    _write_properties(
        tmp_path,
        monkeypatch,
        "DEV_DEEPAGENT_URL=http://127.0.0.1:9000\nSSO_URL_HEADER=X-File-Url\n",
    )
    monkeypatch.setenv("DEV_DEEPAGENT_URL", "http://127.0.0.1:9999")
    monkeypatch.setenv("DEV_SSO_TOKEN", "env-token")
    monkeypatch.setenv("SSO_URL_HEADER", "X-Env-Url")

    config = resolve()

    assert config.deepagent_url == "http://127.0.0.1:9999"
    assert config.sso_token == "env-token"
    assert config.sso_url_header == "X-Env-Url"
    assert config.setting(DEV_DEEPAGENT_URL).source == SOURCE_ENV
    assert config.setting(DEV_SSO_TOKEN).source == SOURCE_ENV
    assert config.setting(SSO_URL_HEADER).source == SOURCE_ENV


def test_resolve_cliBeatsEnv_evenWhenEmpty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEV_SSO_TOKEN", "env-token")
    monkeypatch.setenv("AGENT_API_BEARER_TOKEN", "env-bearer")

    config = resolve({DEV_SSO_TOKEN: "cli-token", AGENT_API_BEARER_TOKEN: ""})

    assert config.sso_token == "cli-token"
    assert config.setting(DEV_SSO_TOKEN).source == SOURCE_CLI
    # CLI 給了空字串也算 cli 來源(flag 有給), 值就是空——呼叫端自己判斷缺 token.
    assert config.bearer_token == ""
    assert config.setting(AGENT_API_BEARER_TOKEN).source == SOURCE_CLI


def test_resolve_cliOverrideNone_meansFlagNotGiven(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEV_SSO_URL", "https://sso.env")

    config = resolve({DEV_SSO_URL: None, DEV_DEEPAGENT_URL: None})

    assert config.sso_url == "https://sso.env"
    assert config.setting(DEV_SSO_URL).source == SOURCE_ENV
    assert config.deepagent_url == "http://127.0.0.1:8000"


def test_resolve_emptyEnvValue_isIgnored(monkeypatch: pytest.MonkeyPatch) -> None:
    """空 env 值視為未設(同 Settings 的 env_ignore_empty), 來源落回 default."""
    monkeypatch.setenv("DEV_DEEPAGENT_URL", "")
    monkeypatch.setenv("AGENT_API_BEARER_TOKEN", "")

    config = resolve()

    assert config.deepagent_url == "http://127.0.0.1:8000"
    assert config.setting(DEV_DEEPAGENT_URL).source == SOURCE_DEFAULT
    assert config.bearer_token is None
    assert config.setting(AGENT_API_BEARER_TOKEN).source == SOURCE_DEFAULT


def test_resolve_officialKeyValue_comesFromSettings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """官方 key 的值以 get_settings() 為準(服務會讀到什麼, dev 腳本就報什麼)。"""
    _write_properties(tmp_path, monkeypatch, "AGENT_API_BEARER_TOKEN= file-token \n")
    monkeypatch.delenv("AGENT_API_BEARER_TOKEN", raising=False)

    config = resolve()

    assert config.bearer_token == get_settings().AGENT_API_BEARER_TOKEN == "file-token"
    assert config.setting(AGENT_API_BEARER_TOKEN).source == SOURCE_PROPERTIES


def test_resolve_onePropertiesPath_isFirstSettingWithEnvOrDefaultSource(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    properties_file = _write_properties(tmp_path, monkeypatch, "DEV_SSO_URL=https://sso.example\n")

    config = resolve()

    assert config.sso_url == "https://sso.example"
    assert config.settings[0] == DevSetting(
        ONE_PROPERTIES_PATH, str(properties_file), SOURCE_ENV, secret=False
    )

    monkeypatch.delenv("ONE_PROPERTIES_PATH")
    get_settings.cache_clear()
    assert resolve().settings[0].source == SOURCE_DEFAULT


def test_resolve_secretFlags_markOnlySecrets() -> None:
    secret_keys = {setting.key for setting in resolve().settings if setting.secret}

    assert secret_keys == {AGENT_API_BEARER_TOKEN, DEV_SSO_TOKEN, DEV_SSO_URL}


def test_resolve_devConnectorsInFile_parsedIntoList(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_properties(
        tmp_path, monkeypatch, 'DEV_CONNECTORS=[{"id":"sales","url":"http://127.0.0.1:8765/mcp"}]\n'
    )

    config = resolve()

    assert config.connectors == [
        {
            "id": "sales",
            "name": "Sales",
            "url": "http://127.0.0.1:8765/mcp",
            "bearerTokenKey": None,
        }
    ]
    assert config.setting(DEV_CONNECTORS).source == SOURCE_PROPERTIES


def test_resolve_devConnectorsInEnv_parsedIntoList(monkeypatch: pytest.MonkeyPatch) -> None:
    """容器裡只有 env 的情境(§5.2): DEV_CONNECTORS 不用再手動寫進檔案。"""
    monkeypatch.setenv("DEV_CONNECTORS", '[{"id":"sales","url":"http://127.0.0.1:8765/mcp"}]')

    config = resolve()

    assert [connector["id"] for connector in config.connectors] == ["sales"]
    assert config.setting(DEV_CONNECTORS).source == SOURCE_ENV


def test_resolve_invalidDevConnectors_raisesValueErrorNamingKey(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEV_CONNECTORS", "not-json-SECRETVALUE123")

    with pytest.raises(ValueError, match=DEV_CONNECTORS) as excinfo:
        resolve()
    assert "SECRETVALUE123" not in str(excinfo.value)


def test_key_source_followsTheSingleRule(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_properties(
        tmp_path, monkeypatch, "AGENT_API_BEARER_TOKEN=file-token\nDEV_SSO_TOKEN=file-sso\n"
    )
    monkeypatch.delenv("AGENT_API_BEARER_TOKEN", raising=False)
    monkeypatch.setenv("SSO_TOKEN_HEADER", "X-Env-Token")

    assert key_source(AGENT_API_BEARER_TOKEN) == SOURCE_PROPERTIES
    assert key_source(AGENT_API_BEARER_TOKEN, cli_value="cli") == SOURCE_CLI
    assert key_source(DEV_SSO_TOKEN) == SOURCE_PROPERTIES
    assert key_source(SSO_TOKEN_HEADER) == SOURCE_ENV
    assert key_source(SSO_URL_HEADER) == SOURCE_DEFAULT
    assert key_source(ONE_PROPERTIES_PATH) == SOURCE_ENV


def test_env_shadowed_keys_onlyWhenFileAlsoSetsTheKey(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """env 蓋掉檔案的值才算 shadow; 檔案沒設時 env 是唯一來源(容器情境), 不警告。
    ONE_PROPERTIES_PATH 永遠不算——它不可能在檔裡。"""
    properties_file = _write_properties(
        tmp_path, monkeypatch, "DEV_SSO_TOKEN=file-sso\nAGENT_API_BEARER_TOKEN=file-token\n"
    )
    monkeypatch.setenv("DEV_SSO_TOKEN", "env-sso")
    monkeypatch.setenv("AGENT_API_BEARER_TOKEN", "env-token")
    monkeypatch.setenv("DEV_SSO_URL", "https://sso.env")  # 檔案沒設 -> 不算

    config = resolve()

    assert env_shadowed_keys(config) == [AGENT_API_BEARER_TOKEN, DEV_SSO_TOKEN]
    warning_lines = env_shadow_warning_lines(config)
    assert len(warning_lines) == 2
    assert AGENT_API_BEARER_TOKEN in warning_lines[0] and DEV_SSO_TOKEN in warning_lines[0]
    assert str(properties_file) in warning_lines[0]
    rendered = "\n".join(warning_lines)
    assert "env-sso" not in rendered and "env-token" not in rendered
    assert "file-sso" not in rendered and "file-token" not in rendered


def test_env_shadowed_keys_cliOverride_isNotShadowing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_properties(tmp_path, monkeypatch, "DEV_SSO_TOKEN=file-sso\n")
    monkeypatch.setenv("DEV_SSO_TOKEN", "env-sso")

    config = resolve({DEV_SSO_TOKEN: "cli-sso"})

    assert env_shadowed_keys(config) == []
    assert env_shadow_warning_lines(config) == []


def test_env_shadowed_keys_nothingSet_isEmpty() -> None:
    assert env_shadow_warning_lines(resolve()) == []


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


def test_resolve_shell_exports_missingFile_usesDefaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_WORKSPACE_ROOT", raising=False)

    exports = resolve_shell_exports()

    assert exports == {"DEEPAGENT_PORT": "8000", "AGENT_WORKSPACE_ROOT": "/tmp/erd-spike-workspace"}


def test_resolve_shell_exports_envBeatsFileBeatsSpikeDefault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """spike 跑出來的反例(spec §10): env 設了 AGENT_WORKSPACE_ROOT、檔案沒設, 以前會回 spike
    預設, 讓 run-deepagent.sh 用錯的目錄蓋掉服務自己會讀到的值。port 同樣吃 env 的
    DEV_DEEPAGENT_URL。"""
    _write_properties(tmp_path, monkeypatch, "AGENT_WORKSPACE_ROOT=/data/file-workspace\n")
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", "/workspace")
    monkeypatch.setenv("DEV_DEEPAGENT_URL", "http://127.0.0.1:8020")

    exports = resolve_shell_exports()

    assert exports == {"DEEPAGENT_PORT": "8020", "AGENT_WORKSPACE_ROOT": "/workspace"}


def test_resolve_shell_exports_fileValues_areUsed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    properties_file = tmp_path / "one-local.properties"
    properties_file.write_text(
        "DEV_DEEPAGENT_URL=http://127.0.0.1:8010\nAGENT_WORKSPACE_ROOT=/data/workspace\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ONE_PROPERTIES_PATH", str(properties_file))
    monkeypatch.delenv("AGENT_WORKSPACE_ROOT", raising=False)

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
    monkeypatch.delenv("AGENT_WORKSPACE_ROOT", raising=False)

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
    monkeypatch.delenv("AGENT_WORKSPACE_ROOT", raising=False)

    _print_shell_exports()

    printed_lines = capsys.readouterr().out.splitlines()
    assert printed_lines == [
        "DEEPAGENT_PORT=8010",
        "AGENT_WORKSPACE_ROOT=/tmp/erd-spike-workspace",
    ]


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
