"""Connector 呼叫的 recipe 記錄——Phase 2 重放材料＋前置呼叫稽核(見 spec §5)。

兩個獨立 append-only JSONL 檔:`recipe/landings.jsonl` 記落表呼叫(server id、tool
name、args、inputSchema hash、觀測 schema、snapshot sha256——Phase 2 重放時凍結參數
重打這些呼叫、按 sha256 驗證後重掛,見 `api_snapshot.remount_snapshots`);
`recipe/audit.jsonl` 記所有工具呼叫(含未落表的前置呼叫,如 lookup)供稽核,**不重放**。
qN SQL 已由既有 `results.record_query` 持久化於 `queries/`,本模組不重複記錄。

**args 原樣記錄**——token 不在 args 裡(SSO token 走 request_context/wire header,
NEVER 進 tool 呼叫參數),故此處落地天然不含 token,對齊 spec §8「token NEVER 進
recipe」。

engine 層——stdlib only,禁止 import 任何 LLM 框架(ruff TID251 會擋)。
"""

import hashlib
import json
import logging
import threading
from pathlib import Path
from typing import Any

from app.engine.workspace import SessionWorkspace

logger = logging.getLogger(__name__)

# LangGraph 平行 tool_calls 在 executor 執行緒同時觸發 record_landing/record_tool_audit
# ——同一 process 內的併發 append 靠這把全域鎖序列化(open+write 一起鎖住),跨 process
# 併發不在本模組保護範圍內(目前架構下 recipe 檔只被單一 deepagent worker process 寫入)。
_append_lock = threading.Lock()


def schema_hash(input_schema: dict) -> str:
    """inputSchema 的穩定指紋——`json.dumps(sort_keys=True)` 讓 key 順序不影響雜湊
    (含巢狀 dict——`sort_keys` 對所有巢狀層級遞迴生效),截斷成 16 字元供 recipe 內比對用
    (非安全用途,只需碰撞率低到能辨識 schema 是否漂移)。
    """
    serialized = json.dumps(input_schema, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


def _append_json_line(path: Path, record: dict[str, Any]) -> None:
    """單一 `.write()` 呼叫寫入整行(`json.dumps(...) + "\\n"` 先組成一個字串)——分兩次
    `.write()` 在多執行緒併發 append 下會產生斷行(reviewer 實測 8 threads × ~30KB
    payload 51% 機率斷行,見 fix round 1);再用模組級 `_append_lock` 包住 open+write,
    確保同一 process 內併發呼叫序列化、不交錯。單一 `.write()` call 本身在多數平台已是
    atomic append(`O_APPEND` 語意),鎖是雙重保險,兩者缺一在高併發/大 payload 下都可能
    斷行——誠實記載這個保證來源,不只靠平台 atomic write 的僥倖。"""
    line = json.dumps(record, ensure_ascii=False) + "\n"
    with _append_lock, open(path, "a", encoding="utf-8") as recipe_file:
        recipe_file.write(line)


def record_landing(
    workspace: SessionWorkspace,
    *,
    connector_id: str,
    tool_name: str,
    args: dict[str, Any],
    land_as: str,
    observed_columns: list[str],
    input_schema_hash: str,
    snapshot_sha256: str,
) -> None:
    """append 一筆落表呼叫記錄到 `recipe/landings.jsonl`。`snapshot_sha256` 是
    `api_snapshot.land_snapshot` 回傳的 `LandingResult.sha256`——Phase 2
    `remount_snapshots` 靠它做雜湊門禁,呼叫端(agent 層)MUST 在每次落表後立刻記錄。
    同一 alias 可重複記錄(同 turn 重試/迭代皆落表)——重放時只認最後一筆,見
    `landing_hashes`。args 原樣記錄,不做任何遮罩:token 不在 args 裡,設計上就不會
    落進 recipe(見模組 docstring)。
    """
    _append_json_line(
        workspace.recipe_dir / "landings.jsonl",
        {
            "connector_id": connector_id,
            "tool_name": tool_name,
            "args": args,
            "land_as": land_as,
            "observed_columns": observed_columns,
            "input_schema_hash": input_schema_hash,
            "snapshot_sha256": snapshot_sha256,
        },
    )


def record_tool_audit(
    workspace: SessionWorkspace,
    *,
    connector_id: str,
    tool_name: str,
    args: dict[str, Any],
    landed: bool,
) -> None:
    """append 一筆工具呼叫記錄到 `recipe/audit.jsonl`——涵蓋所有 connector 工具呼叫
    (含落表與前置/lookup 呼叫);此檔僅供稽核,Phase 2 重放不讀它(只讀
    `landings.jsonl`)。`landed` 標記這次呼叫是否也落表(便於稽核時交叉核對兩份檔案)。
    """
    _append_json_line(
        workspace.recipe_dir / "audit.jsonl",
        {
            "connector_id": connector_id,
            "tool_name": tool_name,
            "args": args,
            "landed": landed,
        },
    )


def load_landings(workspace: SessionWorkspace) -> list[dict]:
    """讀 `recipe/landings.jsonl` 全部記錄,依寫入順序回傳。檔案不存在回空列表。
    單行損毀(process 被砍到一半、外部工具直接動過檔案——同一 process 內的併發 append
    已由 `_append_json_line` 的 `_append_lock` 序列化,不會是斷行成因)只跳過那一行並
    記警告,不讓一行壞資料卡死整份 recipe——容錯手法沿 `results.load_all_results` 的
    既有作法。
    """
    landings_path = workspace.recipe_dir / "landings.jsonl"
    if not landings_path.is_file():
        return []

    landings: list[dict] = []
    for line_number, raw_line in enumerate(
        landings_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        stripped_line = raw_line.strip()
        if not stripped_line:
            continue
        try:
            landings.append(json.loads(stripped_line))
        except json.JSONDecodeError as parse_error:
            logger.warning(
                "skipping unreadable landing record at %s line %d: %s",
                landings_path,
                line_number,
                parse_error,
            )
    return landings


def landing_hashes(workspace: SessionWorkspace) -> dict[str, str]:
    """`{land_as: snapshot_sha256}`,last-wins per alias——同一 alias 多次落表時只有
    最後一筆代表當下掛載的資料。Task 6 直接把回傳值餵給
    `api_snapshot.remount_snapshots` 的 `expected_hashes` 參數。
    """
    hashes: dict[str, str] = {}
    for landing in load_landings(workspace):
        hashes[landing["land_as"]] = landing["snapshot_sha256"]
    return hashes
