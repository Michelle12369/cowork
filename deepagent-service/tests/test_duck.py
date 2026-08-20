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


def test_open_locked_connection_never_loads_httpfs(tmp_path, monkeypatch):
    """全 local 之後不該再讓 DuckDB 曝露到任何網路 extension 相關管道。

    改用「側錄」而非事後查 duckdb_extensions()——鎖門(enable_external_access=false)後那張
    metadata table function 本身要碰檔案系統列出 extensions 目錄,在鎖門連線上一律拋
    Permission Error,查不出結果,驗證不到東西。側錄涵蓋兩條舊 `_s3_config()` 曾經用過的路徑,
    缺一條都會漏掉回歸:
    - `DuckDBPyConnection.execute()` 實際跑過的 SQL——擋 `INSTALL httpfs; LOAD httpfs;` 這種寫法
    - `duckdb.connect(config=...)` 傳入的 config dict——擋直接把 `s3_*` 系列 key 併進 config
      這種不下 SQL、只側錄 execute 抓不到的寫法(這正是舊 `_s3_config()` 的實際機制)
    """
    csv_path = tmp_path / "sales.csv"
    csv_path.write_text("region,amount\nnorth,10\n", encoding="utf-8")

    executed_statements: list[str] = []
    original_execute = duckdb.DuckDBPyConnection.execute

    def _recording_execute(self, query, *args, **kwargs):
        executed_statements.append(query)
        return original_execute(self, query, *args, **kwargs)

    monkeypatch.setattr(duckdb.DuckDBPyConnection, "execute", _recording_execute)

    connect_configs: list[dict[str, object]] = []
    original_connect = duckdb.connect

    def _recording_connect(*args, **kwargs):
        connect_configs.append(kwargs.get("config") or {})
        return original_connect(*args, **kwargs)

    monkeypatch.setattr(duckdb, "connect", _recording_connect)

    open_locked_connection([Source(alias="sales", path=str(csv_path), file_type="csv")])

    assert not any("httpfs" in statement.lower() for statement in executed_statements)
    assert not any(key.lower().startswith("s3_") for config in connect_configs for key in config)


def test_locked_connection_allowedDirectory_readJsonWorksInsideOnly(tmp_path):
    inside_path = tmp_path / "api_snapshots"
    inside_path.mkdir()
    (inside_path / "sample.json").write_text('[{"tool":"A","value":1}]', encoding="utf-8")
    outside_path = tmp_path / "outside.json"
    outside_path.write_text('[{"tool":"B"}]', encoding="utf-8")

    connection = open_locked_connection([], api_snapshots_dir=inside_path)
    rows = connection.execute(
        "SELECT * FROM read_json_auto(?)", [str(inside_path / "sample.json")]
    ).fetchall()
    assert rows == [("A", 1)]
    with pytest.raises(duckdb.Error):
        connection.execute("SELECT * FROM read_json_auto(?)", [str(outside_path)]).fetchall()


def test_locked_connection_allowedDirectory_configStaysLocked(tmp_path):
    inside_path = tmp_path / "api_snapshots"
    inside_path.mkdir()
    connection = open_locked_connection([], api_snapshots_dir=inside_path)
    with pytest.raises(duckdb.Error):
        connection.execute("SET allowed_directories = []")
    with pytest.raises(duckdb.Error):
        connection.execute("SET enable_external_access = true")


def test_locked_connection_noSnapshotDir_behaviorUnchanged(tmp_path):
    outside_path = tmp_path / "any.json"
    outside_path.write_text("[]", encoding="utf-8")
    connection = open_locked_connection([])
    with pytest.raises(duckdb.Error):
        connection.execute("SELECT * FROM read_json_auto(?)", [str(outside_path)]).fetchall()


def test_open_locked_connection_mounts_json_as_table(tmp_path):
    json_path = tmp_path / "orders.json"
    json_path.write_text('[{"system":"CRM","tickets":42}]', encoding="utf-8")
    connection = open_locked_connection([Source("orders", str(json_path), "json")])
    rows = connection.execute('SELECT system, tickets FROM "orders"').fetchall()
    assert rows == [("CRM", 42)]
