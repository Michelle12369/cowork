"""API 資料源快照落地——正規化 upstream JSON 回應、消毒欄名、推斷 DuckDB 型別、
寫入/掃描 session workspace 的 `api/` 目錄。

engine 層——stdlib only,禁止 import 任何 LLM 框架(ruff TID251 會擋);
temp-then-rename 慣例同 `source_cache.py`。
"""

import csv
import io
import json
import logging
import re
import secrets
from dataclasses import asdict, dataclass
from pathlib import Path

from app.engine.workspace import SessionWorkspace

logger = logging.getLogger(__name__)

API_SNAPSHOT_DIRNAME = "api"
API_SNAPSHOT_MAX_ROWS = 5000  # 對齊 STORE_MAX_ROWS 哲學(app.engine.results)

_UNSAFE_COLUMN_CHARS = re.compile(r"\W", re.UNICODE)


@dataclass(frozen=True)
class SnapshotMeta:
    api_id: str
    alias: str
    params: dict
    fetched_at: str
    schema: tuple[tuple[str, str], ...]
    row_count: int
    truncated: bool


def normalize_json_array(payload: object) -> tuple[list[str], list[list]]:
    """json-array 回應→(欄名, 列)。欄名取各物件鍵的首見順序聯集,缺鍵補 None。
    非物件陣列拋 ValueError(呼叫端轉 API_ERROR)。空陣列合法→([], [])。
    這是回應格式的替換接縫:未來巢狀/文件型回應的抽取程式在此換入,簽名不變。"""
    if not isinstance(payload, list):
        # ValueError(非 TypeError)是契約的一部分:呼叫端(tool 層)接住轉 API_ERROR。
        raise ValueError("expected a JSON array of objects")  # noqa: TRY004
    columns: list[str] = []
    seen_columns: set[str] = set()
    for element in payload:
        if not isinstance(element, dict):
            raise ValueError("expected every array element to be a JSON object")  # noqa: TRY004
        for key in element:
            if key not in seen_columns:
                seen_columns.add(key)
                columns.append(key)
    rows = [[element.get(column) for column in columns] for element in payload]
    return columns, rows


def sanitize_column_names(names: list[str]) -> list[str]:
    """欄名源自 upstream 回應(不可信):非 \\w 字元一律換底線、空名補 column_N、
    撞名(含消毒後撞名)加 _2/_3 後綴——絕不靜默丟欄。"""
    sanitized: list[str] = []
    used: set[str] = set()
    for position, raw_name in enumerate(names, start=1):
        candidate = _UNSAFE_COLUMN_CHARS.sub("_", raw_name) or f"column_{position}"
        deduped = candidate
        suffix = 2
        while deduped in used:
            deduped = f"{candidate}_{suffix}"
            suffix += 1
        used.add(deduped)
        sanitized.append(deduped)
    return sanitized


def infer_column_types(columns: list[str], rows: list[list]) -> tuple[tuple[str, str], ...]:
    inferred: list[tuple[str, str]] = []
    for column_index, column_name in enumerate(columns):
        values = [row[column_index] for row in rows if row[column_index] is not None]
        if values and all(isinstance(value, bool) for value in values):
            duck_type = "BOOLEAN"
        elif values and all(
            isinstance(value, int) and not isinstance(value, bool) for value in values
        ):
            duck_type = "BIGINT"
        elif values and all(
            isinstance(value, (int, float)) and not isinstance(value, bool) for value in values
        ):
            duck_type = "DOUBLE"
        else:
            duck_type = "VARCHAR"
        inferred.append((column_name, duck_type))
    return tuple(inferred)


def _write_atomic(target_path: Path, content: str) -> None:
    # 同 source_cache._fill_cache 慣例:token 尾綴避免併發寫入互相搶佔/覆蓋對方的 .part。
    part_path = target_path.with_name(f"{target_path.name}.part-{secrets.token_hex(4)}")
    part_path.write_text(content, encoding="utf-8")
    part_path.replace(target_path)


def write_snapshot(
    workspace: SessionWorkspace,
    meta: SnapshotMeta,
    columns: list[str],
    rows: list[list],
    raw_text: str | None,
) -> None:
    workspace.api_dir.mkdir(parents=True, exist_ok=True)

    csv_buffer = io.StringIO()
    csv_writer = csv.writer(csv_buffer)
    csv_writer.writerow(columns)
    csv_writer.writerows(rows)
    _write_atomic(workspace.api_dir / f"{meta.alias}.csv", csv_buffer.getvalue())

    meta_json = json.dumps(asdict(meta), ensure_ascii=False, indent=2)
    _write_atomic(workspace.api_dir / f"{meta.alias}.meta.json", meta_json)

    if raw_text is not None:
        _write_atomic(workspace.api_dir / f"{meta.alias}.raw.json", raw_text)


def scan_snapshots(workspace: SessionWorkspace) -> list[SnapshotMeta]:
    if not workspace.api_dir.is_dir():
        return []

    snapshots: list[SnapshotMeta] = []
    for meta_path in sorted(workspace.api_dir.glob("*.meta.json")):
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
        alias = payload["alias"]
        csv_path = workspace.api_dir / f"{alias}.csv"
        if not csv_path.is_file():
            logger.warning("skipping broken api snapshot missing csv alias=%s", alias)
            continue
        snapshots.append(
            SnapshotMeta(
                api_id=payload["api_id"],
                alias=alias,
                params=payload["params"],
                fetched_at=payload["fetched_at"],
                schema=tuple(tuple(column) for column in payload["schema"]),
                row_count=payload["row_count"],
                truncated=payload["truncated"],
            )
        )
    return snapshots
