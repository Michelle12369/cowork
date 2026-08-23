"""Connector 的確定性執行層——HTTP 呼叫(auth/timeout/caps)+snapshot 落檔+fetch 記錄。
錯誤一律 ConnectorFetchError 且訊息帶下一步指引、不含 endpoint URL(防洩內網位址)。"""

import hashlib
import json
import logging
import os
from pathlib import Path

import duckdb
import httpx

from app.engine.connectors import ConnectorDefinition
from app.engine.workspace import SessionWorkspace

FETCH_ERROR_PREFIX = "FETCH_ERROR"

logger = logging.getLogger(__name__)


class ConnectorFetchError(RuntimeError):
    pass


def _auth_headers(definition: ConnectorDefinition) -> dict[str, str]:
    if not definition.auth:
        return {}
    mode, _, env_name = definition.auth.partition(":")
    if mode != "bearer" or not env_name:
        raise ConnectorFetchError(
            f"connector {definition.name} 的 auth 模式不支援: {definition.auth!r}(一期僅 bearer:ENV)"
        )
    token = os.environ.get(env_name)
    if not token:
        raise ConnectorFetchError(
            f"connector {definition.name} 需要 env var {env_name}(未設定)——請聯繫維運補齊後重試"
        )
    return {"Authorization": f"Bearer {token}"}


def execute_fetch(
    definition: ConnectorDefinition,
    params: dict,
    transport: httpx.BaseTransport | None = None,
) -> bytes:
    headers = _auth_headers(definition)
    try:
        with httpx.Client(transport=transport, timeout=definition.limits.timeout_s) as client:
            if definition.method == "GET":
                response = client.get(definition.endpoint, params=params, headers=headers)
            else:
                response = client.post(definition.endpoint, json=params, headers=headers)
    except httpx.HTTPError as transport_error:
        raise ConnectorFetchError(
            f"connector {definition.name} 呼叫失敗({type(transport_error).__name__})——"
            "可稍後重試;持續失敗請如實告知使用者資料源暫不可用"
        ) from transport_error
    if response.status_code != 200:
        raise ConnectorFetchError(
            f"connector {definition.name} 回應 HTTP {response.status_code}——"
            "請檢查參數是否正確;若為權限問題請如實告知使用者"
        )
    body = response.content
    if len(body) > definition.limits.max_bytes:
        raise ConnectorFetchError(
            f"connector {definition.name} 回應超過 max_bytes 上限"
            f"({len(body)} > {definition.limits.max_bytes})——請縮小查詢範圍(如日期區間)再試"
        )
    return body


def snapshot_fingerprint(connector: str, params: dict) -> str:
    """snapshot 身分＝(connector, 正規化 params) 的指紋——同源重抓同指紋(覆蓋刷新),換
    params 換指紋(新檔)。sort_keys 讓 dict 順序無關;ensure_ascii=False 中文參數穩定。"""
    canonical = f"{connector}\n{json.dumps(params, sort_keys=True, ensure_ascii=False)}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def land_snapshot(workspace: SessionWorkspace, fingerprint: str, payload: bytes) -> Path:
    """原子寫(tmp+replace,比照 `record_fetch`)——避免 mid-write crash(process kill/OOM)
    留下半寫壞檔,讓下一輪 `open_locked_connection` 對著它 `read_json_auto` 直接炸掉。
    檔名＝指紋(§12.4):同源重抓覆蓋同檔,換 params 落新檔,不再靠模型指定的 alias 命名。"""
    workspace.api_snapshots_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = workspace.api_snapshots_dir / f"{fingerprint}.json"
    tmp_path = snapshot_path.with_suffix(".json.tmp")
    tmp_path.write_bytes(payload)
    os.replace(tmp_path, snapshot_path)
    return snapshot_path


def stage_snapshot_for_validation(
    workspace: SessionWorkspace, fingerprint: str, payload: bytes
) -> Path:
    """驗証專用暫存檔(§12 review finding 3 stage-then-swap)——後綴 `.json.stage`,不會被
    `glob("*.json")` 撿到,不是正式指紋檔。呼叫端先對這份暫存檔做 parse/列數驗証,通過才呼叫
    `land_snapshot` 落成正式檔並把真表轉正;失敗只清這份暫存檔,舊表、舊指紋檔完全不動。"""
    workspace.api_snapshots_dir.mkdir(parents=True, exist_ok=True)
    stage_path = (workspace.api_snapshots_dir / f"{fingerprint}.json").with_suffix(".json.stage")
    stage_path.write_bytes(payload)
    return stage_path


def quarantine_unmountable_snapshots(snapshot_paths: list[Path]) -> list[Path]:
    """逐一用 throwaway in-memory connection 對每個 snapshot 檔 probe `read_json_auto`
    ——與正式掛載同一套語法接受面(NEVER 用 `json.loads`,語法接受面不同,probe 過但正式
    掛載仍可能炸的檔案就白 probe 了)。probe 失敗(mid-write crash 留下的壞檔、內容非
    JSON 等)的檔案就地改名成 `.corrupt`(glob `*.json` 不再匹配,之後每輪不會再對著它炸),
    保留原檔供鑑識;回傳仍可掛載的清單,順序不變。"""
    mountable_paths: list[Path] = []
    for snapshot_path in snapshot_paths:
        probe_connection = duckdb.connect(":memory:")
        try:
            probe_connection.execute(
                "SELECT * FROM read_json_auto(?) LIMIT 0", [str(snapshot_path)]
            )
        except duckdb.Error:
            corrupt_path = snapshot_path.with_suffix(".json.corrupt")
            os.replace(snapshot_path, corrupt_path)
            logger.warning(
                "api snapshot 無法解析,已隔離保留鑑識: %s -> %s", snapshot_path, corrupt_path
            )
            continue
        finally:
            probe_connection.close()
        mountable_paths.append(snapshot_path)
    return mountable_paths


def record_fetch(
    workspace: SessionWorkspace,
    fingerprint: str,
    alias: str,
    connector_name: str,
    params: dict,
) -> None:
    records = load_fetch_records(workspace)
    records.append(
        {"fingerprint": fingerprint, "alias": alias, "connector": connector_name, "params": params}
    )
    tmp_path = workspace.fetches_path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp_path, workspace.fetches_path)


def load_fetch_records(workspace: SessionWorkspace) -> list[dict]:
    if not workspace.fetches_path.exists():
        return []
    try:
        return json.loads(workspace.fetches_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        corrupt_path = workspace.fetches_path.with_suffix(".json.corrupt")
        os.replace(workspace.fetches_path, corrupt_path)
        logger.warning("fetches.json 損毀,已改名保留鑑識並重新開始: %s", corrupt_path)
        return []
