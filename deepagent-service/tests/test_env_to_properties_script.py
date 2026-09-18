"""scripts/env_to_properties.py 純函式與 main() 的行為測試: env 篩選、merge、newline guard、
--dry-run 不寫檔且不印值。"""

import importlib.util
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "env_to_properties.py"
spec = importlib.util.spec_from_file_location("env_to_properties", SCRIPT_PATH)
assert spec is not None and spec.loader is not None
env_to_properties = importlib.util.module_from_spec(spec)
spec.loader.exec_module(env_to_properties)

FAKE_SECRET_VALUE = "sk-fake-distinctive-secret-should-never-print-9182"


def test_collect_env_values_onlyKnownKeysWithNonEmptyValue_areCollected() -> None:
    environment = {"AGENT_MODEL": "qwen3.6-35b", "S3_BUCKET": "", "UNRELATED_KEY": "x"}
    collected = env_to_properties.collect_env_values(
        environment, ["AGENT_MODEL", "S3_BUCKET", "AGENT_RUNTIME"]
    )
    assert collected == {"AGENT_MODEL": "qwen3.6-35b"}


def test_collect_env_values_missingKey_isSkipped() -> None:
    collected = env_to_properties.collect_env_values({}, ["AGENT_MODEL"])
    assert collected == {}


def test_merge_properties_envOverridesExisting_andKeepsUntouchedKeys() -> None:
    existing = {"AGENT_MODEL": "old-model", "AGENT_RUNTIME": "deepagents"}
    from_env = {"AGENT_MODEL": "new-model"}
    ordered_keys = ["AGENT_MODEL", "AGENT_RUNTIME", "AGENT_MAX_TOKENS"]

    merged, unknown_keys = env_to_properties.merge_properties(existing, from_env, ordered_keys)

    assert merged == {"AGENT_MODEL": "new-model", "AGENT_RUNTIME": "deepagents"}
    assert unknown_keys == []


def test_merge_properties_unknownExistingKey_surfacedSeparately() -> None:
    existing = {"AGENT_MODEL": "old-model", "SOME_LEGACY_KEY": "legacy-value"}
    ordered_keys = ["AGENT_MODEL"]

    merged, unknown_keys = env_to_properties.merge_properties(existing, {}, ordered_keys)

    assert merged == {"AGENT_MODEL": "old-model"}
    assert unknown_keys == ["SOME_LEGACY_KEY"]


def test_render_properties_roundTripsThroughParseProperties(tmp_path: Path) -> None:
    from app.config import _parse_properties

    merged = {"AGENT_MODEL": "qwen3.6-35b", "S3_BUCKET": "erd-cowork"}
    rendered = env_to_properties.render_properties(merged, ["LEGACY_KEY"], {"LEGACY_KEY": "kept"})

    properties_file = tmp_path / "one-local.properties"
    properties_file.write_text(rendered, encoding="utf-8")
    parsed = _parse_properties(properties_file)

    assert parsed["AGENT_MODEL"] == "qwen3.6-35b"
    assert parsed["S3_BUCKET"] == "erd-cowork"
    assert parsed["LEGACY_KEY"] == "kept"


def test_render_properties_valueWithEquals_roundTrips(tmp_path: Path) -> None:
    from app.config import _parse_properties

    rendered = env_to_properties.render_properties({"OPENAI_BASE_URL": "https://x/v1?a=b"}, [], {})

    properties_file = tmp_path / "one-local.properties"
    properties_file.write_text(rendered, encoding="utf-8")
    parsed = _parse_properties(properties_file)

    assert parsed["OPENAI_BASE_URL"] == "https://x/v1?a=b"


def test_render_properties_valueWithNewline_raisesValueError() -> None:
    with pytest.raises(ValueError):
        env_to_properties.render_properties({"AGENT_MODEL": "bad\nvalue"}, [], {})


def test_render_properties_unknownValueWithNewline_raisesValueError() -> None:
    with pytest.raises(ValueError):
        env_to_properties.render_properties({}, ["LEGACY_KEY"], {"LEGACY_KEY": "bad\nvalue"})


def test_main_dryRun_writesNothingAndNeverPrintsValues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENT_MODEL", FAKE_SECRET_VALUE)
    out_path = tmp_path / "one-local.properties"

    env_to_properties.main(["--out", str(out_path), "--dry-run"])

    assert not out_path.exists()
    captured = capsys.readouterr()
    assert "AGENT_MODEL" in captured.out
    assert FAKE_SECRET_VALUE not in captured.out


def test_main_noMatchingEnv_printsNoticeAndExitsCleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for field_name in env_to_properties.Settings.model_fields:
        monkeypatch.delenv(field_name, raising=False)
    out_path = tmp_path / "one-local.properties"

    env_to_properties.main(["--out", str(out_path), "--dry-run"])

    captured = capsys.readouterr()
    assert "no matching env vars" in captured.out


def test_main_writesFile_thenSecondRunMergesWithNewEnv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.config import _parse_properties

    out_path = tmp_path / "one-local.properties"

    monkeypatch.setenv("AGENT_MODEL", "first-model")
    env_to_properties.main(["--out", str(out_path)])
    first_pass = _parse_properties(out_path)
    assert first_pass["AGENT_MODEL"] == "first-model"

    monkeypatch.delenv("AGENT_MODEL", raising=False)
    monkeypatch.setenv("AGENT_RUNTIME", "internal")
    env_to_properties.main(["--out", str(out_path)])
    second_pass = _parse_properties(out_path)

    assert second_pass["AGENT_MODEL"] == "first-model"
    assert second_pass["AGENT_RUNTIME"] == "internal"


def test_main_defaultOut_resolvesRelativeToScriptDirectoryNotCwd() -> None:
    default_path = env_to_properties._default_out_path()
    assert default_path == SCRIPT_PATH.resolve().parent.parent / "one-local.properties"
