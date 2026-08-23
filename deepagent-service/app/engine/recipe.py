"""Replay recipe 組裝——「recipe 固定問題、token 決定答案」的靜態半成品，不含任何資料本身，
只記「該重放哪些 fetch/SQL」，供 Java 端 publish 時封存、之後 /replay 讀出重新取數。

engine 層——stdlib only(＋workspace/results 內部 import；ruff TID251 會擋 LLM 框架 import)。

`sources` 為 fetches.json **全量**、last-wins per alias：v1 不做 SQL→表名解析(哪個 qN
真的用到哪個 alias 目前無法從純文字 SQL 可靠反推)，直接把整輪抓過的來源都收進 recipe。
多帶一個沒被任何 qN 引用的來源只是 replay 時多一次無害的 fetch，超集永遠安全；漏掉一個
被引用的來源則會讓 replay 直接炸——因此選擇「寧多勿少」。"""

from app.engine.api_fetch import load_fetch_records
from app.engine.results import load_all_results, referenced_query_ids
from app.engine.workspace import SessionWorkspace

SCHEMA_VERSION = 1


def _load_sources(workspace: SessionWorkspace) -> list[dict]:
    """fetches.json 全量、last-wins per alias。舊格式記錄(補 columns 前寫入)沒有 `columns`
    鍵，`record.get("columns")` 落回 None——視為缺失，`expectedColumns` 整個鍵省略而非塞 None。
    """
    sources_by_alias: dict[str, dict] = {}
    for record in load_fetch_records(workspace):
        alias = record["alias"]
        source = {
            "connector": record["connector"],
            "params": record["params"],
            "alias": alias,
        }
        columns = record.get("columns")
        if columns is not None:
            source["expectedColumns"] = columns
        sources_by_alias[alias] = source
    return list(sources_by_alias.values())


def _load_queries(workspace: SessionWorkspace, html: str) -> dict[str, dict]:
    """html 實際引用到的 qN 子集，sql 讀 `queries/{id}.sql`、intent 讀 `results/{id}.json`
    (`record_query` 的落檔格式，見 `app.engine.results`)。任一半落檔缺失(理論上不該發生，
    但落檔曾經歷 crash/損毀隔離)一律跳過該 qN，不讓整份 recipe 因單一半成品壞掉而組裝失敗。
    """
    all_results = load_all_results(workspace)
    queries: dict[str, dict] = {}
    for query_id in sorted(referenced_query_ids(html)):
        result = all_results.get(query_id)
        sql_path = workspace.queries_dir / f"{query_id}.sql"
        if result is None or not sql_path.exists():
            continue
        queries[query_id] = {
            "sql": sql_path.read_text(encoding="utf-8"),
            "intent": result.get("intent", ""),
        }
    return queries


def build_recipe(workspace: SessionWorkspace, html: str) -> dict | None:
    """`None`＝純上傳 dashboard(fetches.json 空，無 API 源可重放)；否則回傳 schemaVersion 1
    的 recipe dict，供 Task 2(finalize 落檔) 與 Task 3(/replay 讀取重放) 使用。"""
    sources = _load_sources(workspace)
    if not sources:
        return None
    return {
        "schemaVersion": SCHEMA_VERSION,
        "sources": sources,
        "queries": _load_queries(workspace, html),
    }
