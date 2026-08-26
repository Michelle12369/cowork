from pathlib import Path

import duckdb
import openpyxl
import pytest

from app.config import get_settings
from app.engine.duck import Source, open_locked_connection
from app.engine.request_context import reset_request_identity, set_request_identity
from app.engine.source_cache import resolve_source_path, resolved_file_type


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


def test_resolved_file_type_feeds_open_locked_connection_for_csv(sample_csv) -> None:
    """`resolved_file_type` 推斷出的型別直接餵給 `open_locked_connection` 也要能查——
    釘住 chat_turn 現行接線方式(resolve → derive type → Source)。"""
    file_type = resolved_file_type(str(sample_csv))
    connection = open_locked_connection([Source("orders", str(sample_csv), file_type)])
    rows = connection.execute('SELECT system, tickets FROM "orders" ORDER BY tickets').fetchall()
    assert rows == [("ERP", 7), ("CRM", 42)]


def test_xlsx_upload_resolves_to_csv_and_opens_in_duckdb(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """端到端重現原本的回歸:xlsx 上傳經 `resolve_source_path` 轉檔成 .csv 後,
    file_type 若沿用 wire 上的 "xlsx" 會在 `open_locked_connection` 炸
    `unsupported file type: xlsx`——這正是本次修的 bug。改由 `resolved_file_type`
    推斷型別後,整段管線(轉檔 → 推斷型別 → 掛進 duckdb → 查詢)都要能跑通。"""
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path / "ws"))

    get_settings.cache_clear()
    source_dir = tmp_path / "backend-data" / "uploads" / "sess-1"
    source_dir.mkdir(parents=True)
    xlsx_path = source_dir / "u1_data.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["system", "tickets"])
    sheet.append(["CRM", 42])
    sheet.append(["ERP", 7])
    workbook.save(xlsx_path)

    tokens = set_request_identity("user-1", "sess-1")
    try:
        resolved_path = resolve_source_path(str(xlsx_path))
    finally:
        reset_request_identity(tokens)

    file_type = resolved_file_type(resolved_path)
    assert file_type == "csv"  # wire 上的 item.fileType 會是 "xlsx",此處 MUST 已改推斷值

    connection = open_locked_connection([Source("orders", resolved_path, file_type)])
    rows = connection.execute('SELECT system, tickets FROM "orders" ORDER BY tickets').fetchall()
    assert rows == [("ERP", 7), ("CRM", 42)]
