"""跨輪 world-state manifest——取代舊版只比 alias 集合的 `read_sources_aliases`。除了
新增/移除 alias 外,還要能認出「同 alias 換底層檔案」(同名重上傳、或 session 外部被換過的
API snapshot)與 schema 變動(欄位新增/移除/型別改變),讓模型 checkpoint 記憶裡卡著的舊
`get_schema` 結果能被準確地要求刷新。

engine 層——stdlib + duckdb only,禁止 import 任何 LLM 框架(ruff TID251 會擋)。
"""

import hashlib
import json
from dataclasses import dataclass

import duckdb

from app.engine.workspace import SessionWorkspace

# version_id 摘要長度——只做等值比較,16 hex 已遠超同 session 幾個檔案的碰撞需求。
_VERSION_DIGEST_LENGTH = 16


@dataclass(frozen=True)
class SourceRecord:
    alias: str
    kind: str  # 目前一律 "file";欄位保留給日後的 API datasource 工作
    version_id: str  # raw path 的不透明摘要(見 opaque_version_id)——manifest 在模型可讀的
    # workspace root 內,NEVER 存原始路徑/檔名(infra 佈局+使用者可控檔名不進模型視野)
    columns: tuple[tuple[str, str], ...]  # (name, type),依 ordinal_position 排序


def opaque_version_id(raw_token: str) -> str:
    """把版本 token(如帶上傳 uuid 的 raw path)壓成不可逆摘要。diff 只需要等值比較——
    同檔同摘要、換檔換摘要,語意不變,但 manifest 裡不再出現路徑、檔名或 uuid。"""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()[:_VERSION_DIGEST_LENGTH]


@dataclass(frozen=True)
class SchemaChange:
    alias: str
    added_columns: tuple[str, ...]
    removed_columns: tuple[str, ...]
    type_changed_columns: tuple[str, ...]


@dataclass(frozen=True)
class SourcesDiff:
    added: tuple[str, ...]
    removed: tuple[str, ...]
    version_changed: tuple[str, ...]
    schema_changed: tuple[SchemaChange, ...]

    def is_empty(self) -> bool:
        return not (self.added or self.removed or self.version_changed or self.schema_changed)


def build_manifest(
    connection: duckdb.DuckDBPyConnection,
    sources: list[tuple[str, str]],
    api_sources: list[tuple[str, str]] | None = None,
) -> dict[str, SourceRecord]:
    """`sources` 為 (alias, raw_path) pairs,raw_path 是請求裡的原始路徑(帶上傳 uuid),NOT
    `resolve_source_path` 解出的 cache 路徑——經 opaque_version_id 壓成摘要後當版本 token,
    同名重上傳、內容換了就是條新路徑=新摘要。`api_sources` 為 (alias, fetched_at) pairs,產出
    kind="api" 的紀錄;版本 token 是 API 快照的 fetched_at 字串,同樣經 opaque_version_id 壓成
    摘要——重新 fetch 就是新 token=新摘要。欄位用單一常數 SQL 撈出全部表、依 table 分組
    (同 get_schema 的作法),不對 alias 逐一查詢。"""
    column_rows = (
        connection.cursor()
        .execute(
            "SELECT table_name, column_name, data_type "
            "FROM information_schema.columns "
            "ORDER BY table_name, ordinal_position"
        )
        .fetchall()
    )
    columns_by_table: dict[str, list[tuple[str, str]]] = {}
    for table_name, column_name, data_type in column_rows:
        columns_by_table.setdefault(table_name, []).append((column_name, data_type))

    manifest = {
        alias: SourceRecord(
            alias=alias,
            kind="file",
            version_id=opaque_version_id(raw_path),
            columns=tuple(columns_by_table.get(alias, [])),
        )
        for alias, raw_path in sources
    }

    for alias, fetched_at in api_sources or []:
        manifest[alias] = SourceRecord(
            alias=alias,
            kind="api",
            version_id=opaque_version_id(fetched_at),
            columns=tuple(columns_by_table.get(alias, [])),
        )

    return manifest


def load_manifest(workspace: SessionWorkspace) -> dict[str, SourceRecord] | None:
    """讀回上一輪(若有)`save_manifest` 寫下的 manifest。回傳 None 代表沒有基準可比——
    session 首輪,或是舊 session 在這個功能上線前就已存在(尚未有 manifest 檔案),兩種情況
    都沒有「上一輪」的意義,不該生出變更提示。未知欄位(日後可能加的 fetched_at/params)
    容忍——只讀取目前認得的欄位,其餘忽略。"""
    if not workspace.sources_manifest_path.is_file():
        return None
    raw_manifest = json.loads(workspace.sources_manifest_path.read_text(encoding="utf-8"))
    return {
        alias: SourceRecord(
            alias=alias,
            kind=raw_record.get("kind", "file"),
            version_id=raw_record["version_id"],
            columns=tuple(tuple(column) for column in raw_record.get("columns", [])),
        )
        for alias, raw_record in raw_manifest.items()
    }


def save_manifest(workspace: SessionWorkspace, manifest: dict[str, SourceRecord]) -> None:
    """寫出 manifest,key 依 alias 排序方便人工 diff。與 generation 快照同放 workspace root
    (不在 `.skills/` 底下),`_push` 會自動把它推進下一代快照,不需要額外接線。"""
    payload = {
        alias: {
            "kind": record.kind,
            "version_id": record.version_id,
            "columns": [list(column) for column in record.columns],
        }
        for alias, record in sorted(manifest.items())
    }
    workspace.sources_manifest_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def diff_manifests(
    previous: dict[str, SourceRecord], current: dict[str, SourceRecord]
) -> SourcesDiff:
    """同 alias、同 version_id 的來源永不列入回報——檔案是 immutable 的,version_id 不變時
    schema 不可能變。同 alias、不同 version_id 時比較 schema:有差異就歸進 `schema_changed`
    (訊息本身已涵蓋「換了底層檔案」這件事,不需要在 `version_changed` 重複講一次);schema
    相同才歸進 `version_changed`(單純同名重上傳、內容外觀不變)。"""
    previous_aliases = set(previous)
    current_aliases = set(current)
    added = tuple(sorted(current_aliases - previous_aliases))
    removed = tuple(sorted(previous_aliases - current_aliases))

    version_changed: list[str] = []
    schema_changed: list[SchemaChange] = []
    for alias in sorted(previous_aliases & current_aliases):
        previous_record = previous[alias]
        current_record = current[alias]
        if previous_record.version_id == current_record.version_id:
            continue

        previous_columns = dict(previous_record.columns)
        current_columns = dict(current_record.columns)
        added_columns = tuple(name for name in current_columns if name not in previous_columns)
        removed_columns = tuple(name for name in previous_columns if name not in current_columns)
        type_changed_columns = tuple(
            name
            for name, data_type in current_columns.items()
            if name in previous_columns and previous_columns[name] != data_type
        )

        if added_columns or removed_columns or type_changed_columns:
            schema_changed.append(
                SchemaChange(alias, added_columns, removed_columns, type_changed_columns)
            )
        else:
            version_changed.append(alias)

    return SourcesDiff(added, removed, tuple(version_changed), tuple(schema_changed))
