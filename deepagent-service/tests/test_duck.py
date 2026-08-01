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
    """全 local 之後不該再對 DuckDB 下任何 INSTALL/LOAD httpfs 之類的網路 extension 指令。

    改用「側錄實際執行過的 SQL」而非事後查 duckdb_extensions()——鎖門
    (enable_external_access=false)後這張 metadata table function 本身就需要碰檔案系統
    列出 extensions 目錄,在鎖門連線上一律拋 Permission Error,查不出結果,驗證不到東西。
    """
    csv_path = tmp_path / "sales.csv"
    csv_path.write_text("region,amount\nnorth,10\n", encoding="utf-8")

    executed_statements: list[str] = []
    original_execute = duckdb.DuckDBPyConnection.execute

    def _recording_execute(self, query, *args, **kwargs):
        executed_statements.append(query)
        return original_execute(self, query, *args, **kwargs)

    monkeypatch.setattr(duckdb.DuckDBPyConnection, "execute", _recording_execute)

    open_locked_connection([Source(alias="sales", path=str(csv_path), file_type="csv")])

    assert not any("httpfs" in statement.lower() for statement in executed_statements)
