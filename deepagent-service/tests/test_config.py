"""app/config.py 的 Settings 載入與 one.properties 層疊優先序語意(env > 檔案 > 預設)。"""

import pytest

from app.config import get_settings


@pytest.fixture(autouse=True)
def reset_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _write_properties(tmp_path, content: str):
    properties_file = tmp_path / "one.properties"
    properties_file.write_text(content, encoding="utf-8")
    return properties_file


def test_no_properties_file_reads_env(monkeypatch, tmp_path):
    monkeypatch.setenv("ONE_PROPERTIES_PATH", str(tmp_path / "absent.properties"))
    monkeypatch.setenv("AGENT_MODEL", "env-model")
    assert get_settings().AGENT_MODEL == "env-model"


def test_env_overrides_properties_file(monkeypatch, tmp_path):
    properties_file = _write_properties(tmp_path, "AGENT_MODEL=file-model\n")
    monkeypatch.setenv("ONE_PROPERTIES_PATH", str(properties_file))
    monkeypatch.setenv("AGENT_MODEL", "env-model")
    settings = get_settings()
    assert settings.AGENT_MODEL == "env-model"


def test_properties_file_fills_when_env_unset(monkeypatch, tmp_path):
    properties_file = _write_properties(tmp_path, "AGENT_MODEL=file-model\n")
    monkeypatch.setenv("ONE_PROPERTIES_PATH", str(properties_file))
    monkeypatch.delenv("AGENT_MODEL", raising=False)
    monkeypatch.delenv("AGENT_TOKEN_TTL", raising=False)
    settings = get_settings()
    # env 未設該欄位 → 落到檔案值
    assert settings.AGENT_MODEL == "file-model"
    # 檔案與 env 都沒設的 key → 落到欄位預設值
    assert settings.AGENT_TOKEN_TTL == 300


def test_properties_parsing_comments_blanks_and_equals_in_value(monkeypatch, tmp_path):
    properties_file = _write_properties(
        tmp_path,
        "# comment\n\nOPENAI_BASE_URL=https://host/v1?a=b=c\n  AGENT_TOKEN_TTL = 120 \n",
    )
    monkeypatch.setenv("ONE_PROPERTIES_PATH", str(properties_file))
    # env 對這兩個 key 不設值,避免殘留環境蓋掉檔案值(層疊優先序下 env 若有值會蓋過)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("AGENT_TOKEN_TTL", raising=False)
    settings = get_settings()
    assert settings.OPENAI_BASE_URL == "https://host/v1?a=b=c"
    assert settings.AGENT_TOKEN_TTL == 120


def test_properties_bad_line_fails_loud(monkeypatch, tmp_path):
    properties_file = _write_properties(tmp_path, "AGENT_MODEL=ok\nthis-line-has-no-separator\n")
    monkeypatch.setenv("ONE_PROPERTIES_PATH", str(properties_file))
    with pytest.raises(RuntimeError, match="line 2"):
        get_settings()


def test_defaults_without_any_source(monkeypatch, tmp_path):
    monkeypatch.setenv("ONE_PROPERTIES_PATH", str(tmp_path / "absent.properties"))
    for key in ("AGENT_MODEL", "AGENT_TOKEN_TTL", "LANGFUSE_PUBLIC_KEY"):
        monkeypatch.delenv(key, raising=False)
    settings = get_settings()
    assert settings.AGENT_MODEL == "qwen3.6-35b"
    assert settings.AGENT_TOKEN_TTL == 300
    assert settings.LANGFUSE_PUBLIC_KEY is None


def test_connector_timeout_and_retries_default(monkeypatch, tmp_path):
    monkeypatch.setenv("ONE_PROPERTIES_PATH", str(tmp_path / "absent.properties"))
    monkeypatch.delenv("CONNECTOR_REQUEST_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("CONNECTOR_CALL_RETRIES", raising=False)
    settings = get_settings()
    assert settings.CONNECTOR_REQUEST_TIMEOUT_SECONDS == 30.0
    assert settings.CONNECTOR_CALL_RETRIES == 1


def test_connector_timeout_and_retries_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("ONE_PROPERTIES_PATH", str(tmp_path / "absent.properties"))
    monkeypatch.setenv("CONNECTOR_REQUEST_TIMEOUT_SECONDS", "5.5")
    monkeypatch.setenv("CONNECTOR_CALL_RETRIES", "3")
    settings = get_settings()
    assert settings.CONNECTOR_REQUEST_TIMEOUT_SECONDS == 5.5
    assert settings.CONNECTOR_CALL_RETRIES == 3


def test_connector_bearer_tokens_from_properties_file_decodes_json_dict(monkeypatch, tmp_path):
    properties_file = _write_properties(tmp_path, 'CONNECTOR_BEARER_TOKENS={"gw": "tok"}\n')
    monkeypatch.setenv("ONE_PROPERTIES_PATH", str(properties_file))
    monkeypatch.delenv("CONNECTOR_BEARER_TOKENS", raising=False)
    assert get_settings().CONNECTOR_BEARER_TOKENS == {"gw": "tok"}


def test_connector_bearer_tokens_from_env_decodes_json_dict(monkeypatch, tmp_path):
    monkeypatch.setenv("ONE_PROPERTIES_PATH", str(tmp_path / "absent.properties"))
    monkeypatch.setenv("CONNECTOR_BEARER_TOKENS", '{"gw":"tok"}')
    assert get_settings().CONNECTOR_BEARER_TOKENS == {"gw": "tok"}


def test_connector_bearer_tokens_default_is_empty_dict(monkeypatch, tmp_path):
    monkeypatch.setenv("ONE_PROPERTIES_PATH", str(tmp_path / "absent.properties"))
    monkeypatch.delenv("CONNECTOR_BEARER_TOKENS", raising=False)
    assert get_settings().CONNECTOR_BEARER_TOKENS == {}


def test_connector_bearer_tokens_blank_via_properties_file_is_empty_dict(monkeypatch, tmp_path):
    properties_file = _write_properties(tmp_path, "CONNECTOR_BEARER_TOKENS=\n")
    monkeypatch.setenv("ONE_PROPERTIES_PATH", str(properties_file))
    monkeypatch.delenv("CONNECTOR_BEARER_TOKENS", raising=False)
    assert get_settings().CONNECTOR_BEARER_TOKENS == {}


def test_connector_bearer_tokens_blank_via_env_is_empty_dict(monkeypatch, tmp_path):
    monkeypatch.setenv("ONE_PROPERTIES_PATH", str(tmp_path / "absent.properties"))
    monkeypatch.setenv("CONNECTOR_BEARER_TOKENS", "")
    assert get_settings().CONNECTOR_BEARER_TOKENS == {}


def test_connector_bearer_tokens_bad_json_via_properties_file_fails_without_leaking_value(
    monkeypatch, tmp_path
):
    properties_file = _write_properties(
        tmp_path, "CONNECTOR_BEARER_TOKENS=not-json-SECRETVALUE123\n"
    )
    monkeypatch.setenv("ONE_PROPERTIES_PATH", str(properties_file))
    monkeypatch.delenv("CONNECTOR_BEARER_TOKENS", raising=False)
    with pytest.raises(ValueError) as excinfo:
        get_settings()
    assert "SECRETVALUE123" not in str(excinfo.value)


def test_connector_bearer_tokens_bad_json_via_env_fails_without_leaking_value(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("ONE_PROPERTIES_PATH", str(tmp_path / "absent.properties"))
    monkeypatch.setenv("CONNECTOR_BEARER_TOKENS", "not-json-SECRETVALUE123")
    with pytest.raises(ValueError) as excinfo:
        get_settings()
    assert "SECRETVALUE123" not in str(excinfo.value)


def test_connector_bearer_tokens_json_list_fails_without_leaking_value(monkeypatch, tmp_path):
    monkeypatch.setenv("ONE_PROPERTIES_PATH", str(tmp_path / "absent.properties"))
    monkeypatch.setenv("CONNECTOR_BEARER_TOKENS", '["mes", "secret-token-value"]')
    with pytest.raises(ValueError) as excinfo:
        get_settings()
    assert "secret-token-value" not in str(excinfo.value)
