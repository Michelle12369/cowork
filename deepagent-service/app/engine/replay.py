"""`/replay`——recipe 的零 LLM 確定性重放:逐 source fetch→落暫存目錄→掛載→expectedColumns
子集檢查→逐 qN 跑 SQL→注入結果,不經過模型、不留痕(用完即棄的 tempdir)。

engine 層——stdlib only(＋engine 內部 import；ruff TID251 會擋 LLM 框架 import)。
"""

import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path

import duckdb

from app.engine.api_fetch import ConnectorFetchError, execute_fetch
from app.engine.connectors import ConnectorRegistry
from app.engine.duck import Source, open_locked_connection
from app.engine.narrative_bind import inject_bind_resolver
from app.engine.results import (
    STORE_MAX_ROWS,
    inject_results,
    normalize_rows,
    strip_injected_blocks,
)

logger = logging.getLogger(__name__)

# 分享重放不重算自由洞察(無 LLM、無新資料佐證)——CSS 隱藏而非 DOM 手術,附一行註解供 debug。
_NARRATIVE_HIDE_MARKER = "data-erd-replay-hide"
_NARRATIVE_HIDE_BLOCK = (
    "<!-- erd-replay: 分享重放不重算自由洞察,隱藏 data-erd-narrative 區塊 -->\n"
    "<style data-erd-replay-hide>[data-erd-narrative]{display:none}</style>"
)


@dataclass(frozen=True)
class ReplayOutcome:
    """成功時只有 `html`;失敗時 `html=None`、`error_code`/`error_message` 成對出現。"""

    html: str | None
    error_code: str | None = None
    error_message: str | None = None


def _fail(error_code: str, error_message: str) -> ReplayOutcome:
    return ReplayOutcome(html=None, error_code=error_code, error_message=error_message)


def _validate_recipe_shape(recipe: dict) -> str | None:
    """最小形狀檢查——不逐欄驗證型別細節,只擋掉會讓下游 KeyError/TypeError 的缺席與錯型別。"""
    if not isinstance(recipe, dict):
        return "recipe 必須是物件"
    if "schemaVersion" not in recipe:
        return "recipe 缺少 schemaVersion"
    sources = recipe.get("sources")
    if not isinstance(sources, list):
        return "recipe.sources 必須是陣列"
    queries = recipe.get("queries")
    if not isinstance(queries, dict):
        return "recipe.queries 必須是物件"
    for index, source in enumerate(sources):
        if not isinstance(source, dict) or "connector" not in source or "alias" not in source:
            return f"recipe.sources[{index}] 缺少 connector 或 alias"
    for query_id, query in queries.items():
        if not isinstance(query, dict) or "sql" not in query:
            return f"recipe.queries[{query_id}] 缺少 sql"
    return None


def _inject_narrative_hide(html: str) -> str:
    """冪等——與 `inject_bind_resolver` 同一套「已存在就原樣返回」模式。"""
    if _NARRATIVE_HIDE_MARKER in html:
        return html
    if "</body>" in html:
        return html.replace("</body>", f"{_NARRATIVE_HIDE_BLOCK}</body>", 1)
    return html + _NARRATIVE_HIDE_BLOCK


def _check_expected_columns(
    connection: duckdb.DuckDBPyConnection, expected_columns_by_alias: dict[str, list[str]]
) -> str | None:
    """子集檢查(§4):expectedColumns ⊆ 現時欄名即通過,additive 升版不斷舊 dashboard。"""
    for alias, expected_columns in expected_columns_by_alias.items():
        current_columns = {
            row[0]
            for row in connection.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
                [alias],
            ).fetchall()
        }
        missing_columns = [column for column in expected_columns if column not in current_columns]
        if missing_columns:
            return f"{alias} 資料源結構已變更,缺少欄位: {missing_columns}"
    return None


def _fetch_sources_to_tempdir(
    sources: list[dict], registry: ConnectorRegistry, tmpdir: Path
) -> tuple[list[Source], dict[str, list[str]], ReplayOutcome | None]:
    """逐 source 解析 connector、fetch、落暫存檔;任一失敗立刻回傳該筆 outcome(呼叫端據此短路)。"""
    duck_sources: list[Source] = []
    expected_columns_by_alias: dict[str, list[str]] = {}
    for source in sources:
        connector_name = source["connector"]
        alias = source["alias"]
        definition = registry.get(connector_name)
        if definition is None:
            return (
                [],
                {},
                _fail("SOURCE_GONE", f"資料源已停用: connector {connector_name!r} 不存在"),
            )
        try:
            payload = execute_fetch(definition, source.get("params") or {})
        except ConnectorFetchError as fetch_error:
            return [], {}, _fail("FETCH_FAILED", str(fetch_error))
        snapshot_path = tmpdir / f"{alias}.json"
        snapshot_path.write_bytes(payload)
        duck_sources.append(Source(alias=alias, path=str(snapshot_path), file_type="json"))
        expected_columns = source.get("expectedColumns")
        if expected_columns is not None:
            expected_columns_by_alias[alias] = expected_columns
    return duck_sources, expected_columns_by_alias, None


def run_replay(recipe: dict, html: str, registry: ConnectorRegistry) -> ReplayOutcome:
    """全確定性管線,never-raise:任何內部例外一律 fail-closed 成 `REPLAY_INTERNAL`。"""
    try:
        shape_error = _validate_recipe_shape(recipe)
        if shape_error is not None:
            return _fail("INVALID_RECIPE", shape_error)

        sources = recipe["sources"]
        queries = recipe["queries"]

        with tempfile.TemporaryDirectory() as tmpdir_name:
            tmpdir = Path(tmpdir_name)
            duck_sources, expected_columns_by_alias, fetch_failure = _fetch_sources_to_tempdir(
                sources, registry, tmpdir
            )
            if fetch_failure is not None:
                return fetch_failure

            connection = open_locked_connection(duck_sources, api_snapshots_dir=tmpdir)
            try:
                schema_error = _check_expected_columns(connection, expected_columns_by_alias)
                if schema_error is not None:
                    return _fail("SOURCE_SCHEMA_CHANGED", schema_error)

                records: dict[str, dict] = {}
                for query_id, query in queries.items():
                    try:
                        cursor = connection.execute(query["sql"])
                        columns = [description[0] for description in cursor.description]
                        fetched_rows = cursor.fetchmany(STORE_MAX_ROWS + 1)
                    except duckdb.Error as sql_error:
                        return _fail(
                            "SOURCE_SCHEMA_CHANGED",
                            f"{query_id} SQL 執行失敗(資料源結構可能已變更): {sql_error}",
                        )
                    truncated = len(fetched_rows) > STORE_MAX_ROWS
                    stored_rows = normalize_rows(
                        [list(row) for row in fetched_rows[:STORE_MAX_ROWS]]
                    )
                    records[query_id] = {
                        "intent": query.get("intent", ""),
                        "columns": columns,
                        "rows": [dict(zip(columns, row, strict=False)) for row in stored_rows],
                        "truncated": truncated,
                    }
            finally:
                connection.close()

        clean_html = strip_injected_blocks(html)
        injected_html = inject_results(clean_html, records)
        html_with_resolver = inject_bind_resolver(injected_html)
        final_html = _inject_narrative_hide(html_with_resolver)
        return ReplayOutcome(html=final_html)
    except Exception:
        logger.exception("replay 內部錯誤")
        return _fail("REPLAY_INTERNAL", "重放時發生未預期錯誤")
