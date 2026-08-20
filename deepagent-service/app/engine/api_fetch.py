"""Connector 的確定性執行層——HTTP 呼叫(auth/timeout/caps)+snapshot 落檔+fetch 記錄。
錯誤一律 ConnectorFetchError 且訊息帶下一步指引、不含 endpoint URL(防洩內網位址)。"""

import json
import logging
import os
from pathlib import Path

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


def land_snapshot(workspace: SessionWorkspace, alias: str, payload: bytes) -> Path:
    workspace.api_snapshots_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = workspace.api_snapshots_dir / f"{alias}.json"
    snapshot_path.write_bytes(payload)
    return snapshot_path


def record_fetch(
    workspace: SessionWorkspace, alias: str, connector_name: str, params: dict
) -> None:
    records = load_fetch_records(workspace)
    records.append({"alias": alias, "connector": connector_name, "params": params})
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
