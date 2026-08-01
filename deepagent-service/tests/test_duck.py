import duckdb
import pytest

from app.engine.duck import Source, open_locked_connection


@pytest.fixture()
def sample_csv(tmp_path):
    csv_path = tmp_path / "orders.csv"
    csv_path.write_text("system,tickets\nCRM,42\nERP,7\n", encoding="utf-8")
    return csv_path


def test_open_locked_connection_mounts_csv_as_table(sample_csv) -> None:
    connection = open_locked_connection([Source("orders", str(sample_csv), "csv")])
    rows = connection.execute('SELECT system, tickets FROM "orders" ORDER BY tickets').fetchall()
    assert rows == [("ERP", 7), ("CRM", 42)]


def test_open_locked_connection_blocks_config_change(sample_csv) -> None:
    connection = open_locked_connection([Source("orders", str(sample_csv), "csv")])
    with pytest.raises(duckdb.Error):
        connection.execute("SET enable_external_access = true")


def test_open_locked_connection_rejects_unknown_file_type(sample_csv) -> None:
    with pytest.raises(ValueError, match="unsupported file type"):
        open_locked_connection([Source("orders", str(sample_csv), "xlsx")])


def test_open_locked_connection_rejects_bad_alias(sample_csv) -> None:
    with pytest.raises(ValueError):
        open_locked_connection([Source("bad-alias!", str(sample_csv), "csv")])


def test_open_locked_connection_applies_config_settings(sample_csv) -> None:
    connection = open_locked_connection([Source("orders", str(sample_csv), "csv")], "1GB")
    memory_limit = connection.execute("SELECT current_setting('memory_limit')").fetchone()[0]
    threads = connection.execute("SELECT current_setting('threads')").fetchone()[0]
    assert memory_limit.endswith("MiB")  # 1GB → DuckDB 內部以 953.6 MiB 表示
    assert int(threads) == 2


def test_s3_config_built_from_env(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_S3_ENDPOINT", "minio:9000")
    monkeypatch.setenv("AGENT_S3_ACCESS_KEY_ID", "test-key")
    monkeypatch.setenv("AGENT_S3_SECRET_ACCESS_KEY", "test-secret")
    monkeypatch.delenv("AGENT_S3_REGION", raising=False)
    monkeypatch.setenv("AGENT_S3_USE_SSL", "false")
    from app.engine.duck import _s3_config

    config = _s3_config()
    assert config["s3_endpoint"] == "minio:9000"
    assert config["s3_access_key_id"] == "test-key"
    assert config["s3_secret_access_key"] == "test-secret"
    assert config["s3_region"] == "us-east-1"
    assert config["s3_url_style"] == "path"
    assert config["s3_use_ssl"] is False
