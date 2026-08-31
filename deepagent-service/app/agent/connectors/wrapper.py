"""LangChain tool 包裝層——把 connector 供應層的抽象(`ConnectorTool`)包成每個
(connector, tool) 一個 LangChain `BaseTool`,加入呼叫點 `land_as` 落表決策、命名空間前綴、
每 turn 呼叫上限與退貨整形。

**落表決策在呼叫點,不在 tool 靜態型別**——同一 tool 帶 `land_as` 就落表、不帶就是
lookup,由 skill(`Connector.skills`)引導模型何時該帶。

**每 turn 上限為共享狀態**:同一次 `build_connector_tools` 呼叫產出的所有 tool 共用同一個
計數器與鎖——LangGraph 平行 tool_calls 在 executor 執行緒同時觸發,鎖外先判斷「還有沒有
額度」、鎖內才遞增,避免多執行緒重複讀到未遞增前的計數而超額放行(check-and-increment 需
同一臨界區)。

agent 層——可以 import langchain,與 engine 層(app.engine.*,stdlib only)的界線見
`app/engine/api_snapshot.py`/`app/engine/replay_manifest.py` 檔頭說明。
"""

import json
import logging
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import duckdb
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import Field, ValidationError, create_model

from app.agent.connectors.model import Connector, ConnectorTool, ConnectorToolError
from app.agent.tools.framing import frame_data_content
from app.engine.api_snapshot import EmptyLandingError, land_snapshot
from app.engine.replay_manifest import record_landing, record_tool_audit, schema_hash
from app.engine.workspace import SessionWorkspace

logger = logging.getLogger(__name__)

# lookup 回應(未落表)回給模型的字元數上限——真正要分析的資料走 land_as 落表後用 run_sql 查。
LLM_VIEW_MAX_CHARS = 8000

# JSON Schema 頂層 type → Python type；未知/非 scalar type 退回 str，避免 create_model 炸掉。
_JSON_SCHEMA_TYPE_MAP: dict[str, type] = {
    "string": str,
    "number": float,
    "integer": int,
    "boolean": bool,
}

_LAND_AS_DESCRIPTION = "落表 alias——帶了就把回應落成 DuckDB 表"


@dataclass
class _CallBudget:
    """單一 turn 內所有包裝工具共用的呼叫額度——見檔頭「每 turn 上限為共享狀態」。"""

    call_budget: int
    lock: threading.Lock = field(default_factory=threading.Lock)
    calls_made: int = 0

    def try_consume(self) -> bool:
        """回傳是否還有額度可用,若有則原子遞增。check-and-increment 在同一把鎖內,
        避免平行 tool_calls 競態讀到超額前的計數。"""
        with self.lock:
            if self.calls_made >= self.call_budget:
                return False
            self.calls_made += 1
            return True


def _log_metric(event: str, connector_id: str, tool_name: str) -> None:
    """實驗埋點——NEVER 記 args 值或 token,只記 event/connector/tool 三個低基數維度。"""
    logger.info("connector_metric event=%s connector=%s tool=%s", event, connector_id, tool_name)


def _safe_record_tool_audit(
    workspace: SessionWorkspace,
    *,
    connector_id: str,
    tool_name: str,
    args: dict[str, Any],
    landed: bool,
) -> None:
    """replay manifest 稽核寫入失敗為 non-fatal——connector 呼叫本身已成功,不該因稽核檔寫入失敗
    (如磁碟滿)就讓 tool 回報成功呼叫「失敗」,使 agent 誤信資料不存在而白白重試。失敗只
    記警告(僅型別名,不帶 args 值),不影響 `_execute` 的回傳訊息。"""
    try:
        record_tool_audit(
            workspace, connector_id=connector_id, tool_name=tool_name, args=args, landed=landed
        )
    except Exception as error:  # noqa: BLE001 -- best-effort recording must not mask a successful call
        logger.warning(
            "record_tool_audit failed (non-fatal): connector=%s tool=%s error=%s",
            connector_id,
            tool_name,
            type(error).__name__,
        )


def _safe_record_landing(
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
    """見 `_safe_record_tool_audit`——同一理由:replay manifest landing 記錄失敗不該蓋掉已成功的
    落表結果,回報失敗只會讓 agent 白白重試已完成的工作。"""
    try:
        record_landing(
            workspace,
            connector_id=connector_id,
            tool_name=tool_name,
            args=args,
            land_as=land_as,
            observed_columns=observed_columns,
            input_schema_hash=input_schema_hash,
            snapshot_sha256=snapshot_sha256,
        )
    except Exception as error:  # noqa: BLE001 -- best-effort recording must not mask a successful landing
        logger.warning(
            "record_landing failed (non-fatal): connector=%s tool=%s land_as=%s error=%s",
            connector_id,
            tool_name,
            land_as,
            type(error).__name__,
        )


def _build_args_model(connector_id: str, connector_tool: ConnectorTool) -> type:
    """從 `input_schema` 頂層 properties 建 pydantic model──全部 Optional(呼叫端只帶模型
    實際給的參數,見 `_run` 的 None 過濾),外加固定的 `land_as` 欄位。`land_as` 是本包裝層的
    保留字——connector tool 若自帶同名參數在掛載時就 fail loud,避免執行期被靜默蓋掉語意。"""
    properties: dict[str, dict] = connector_tool.input_schema.get("properties", {})
    if "land_as" in properties:
        raise ValueError(
            f"connector tool 參數名 land_as 為保留字（connector={connector_id!r}，"
            f"tool={connector_tool.name!r}）——請改用其他參數名"
        )
    fields: dict[str, Any] = {}
    for property_name, property_schema in properties.items():
        python_type = _JSON_SCHEMA_TYPE_MAP.get(property_schema.get("type", "string"), str)
        fields[property_name] = (
            python_type | None,
            Field(default=None, description=property_schema.get("description", "")),
        )
    fields["land_as"] = (str | None, Field(default=None, description=_LAND_AS_DESCRIPTION))
    model_name = f"{connector_id}_{connector_tool.name}_Args"
    return create_model(model_name, **fields)


def _render_lookup_view(response: object) -> str:
    """不帶 land_as 的回應退貨整形——JSON 序列化後截到 `LLM_VIEW_MAX_CHARS`,超過時附註記
    (比照 app.agent.tools.data._render_markdown 的截斷慣例)。回應內容視同外部資料,用
    `frame_data_content` 圈住,防止資料值被誤讀成指令。"""
    serialized = json.dumps(response, ensure_ascii=False)
    if len(serialized) > LLM_VIEW_MAX_CHARS:
        serialized = (
            serialized[:LLM_VIEW_MAX_CHARS] + f"...(truncated to {LLM_VIEW_MAX_CHARS} characters)"
        )
    return frame_data_content(serialized)


def _describe_validation_error(error: ValidationError) -> str:
    """`args_schema` 的參數驗證由 LangChain `BaseTool.run()` 在呼叫 `_run` 之前完成
    ——不在 `_run`/`_execute` 的 try/except 範圍內,沒有這個掛鉤,pydantic
    `ValidationError` 會直接炸穿 langgraph 的 ToolNode 中斷整個 turn。回傳訊息只給
    型別名,不含 pydantic 完整錯誤明細。"""
    return f"connector 呼叫失敗：{type(error).__name__}"


def _build_tool(
    connector: Connector,
    connector_tool: ConnectorTool,
    connection: duckdb.DuckDBPyConnection,
    connection_lock: threading.Lock,
    workspace: SessionWorkspace,
    budget: _CallBudget,
) -> BaseTool:
    tool_name = f"{connector.connector_id}_{connector_tool.name}"
    tool_description = f"[{connector.display_name}] {connector_tool.description}"
    args_model = _build_args_model(connector.connector_id, connector_tool)
    input_schema_hash = schema_hash(connector_tool.input_schema)

    def _execute(land_as: str | None, args: dict[str, Any]) -> str:
        try:
            response = connector_tool.call(args)
        except ConnectorToolError as error:
            _log_metric("tool_error", connector.connector_id, connector_tool.name)
            _safe_record_tool_audit(
                workspace,
                connector_id=connector.connector_id,
                tool_name=connector_tool.name,
                args=args,
                landed=False,
            )
            return str(error)
        except Exception as error:  # noqa: BLE001 -- never-raise contract, forward as actionable text
            _log_metric("tool_error", connector.connector_id, connector_tool.name)
            _safe_record_tool_audit(
                workspace,
                connector_id=connector.connector_id,
                tool_name=connector_tool.name,
                args=args,
                landed=False,
            )
            return f"connector 呼叫失敗：{type(error).__name__}"

        if land_as is None:
            _safe_record_tool_audit(
                workspace,
                connector_id=connector.connector_id,
                tool_name=connector_tool.name,
                args=args,
                landed=False,
            )
            _log_metric("lookup_ok", connector.connector_id, connector_tool.name)
            return _render_lookup_view(response)

        try:
            landing_result = land_snapshot(
                connection, connection_lock, workspace, land_as, response
            )
        except EmptyLandingError as error:
            _log_metric("landing_empty", connector.connector_id, connector_tool.name)
            _safe_record_tool_audit(
                workspace,
                connector_id=connector.connector_id,
                tool_name=connector_tool.name,
                args=args,
                landed=False,
            )
            return str(error)
        except ValueError as error:
            # `land_as` 未過 duck._validate_alias——預期錯誤,訊息已可行動,原樣回傳不包成
            # 泛用的「connector 呼叫失敗」蓋掉細節。
            _log_metric("tool_error", connector.connector_id, connector_tool.name)
            _safe_record_tool_audit(
                workspace,
                connector_id=connector.connector_id,
                tool_name=connector_tool.name,
                args=args,
                landed=False,
            )
            return str(error)
        except Exception as error:  # noqa: BLE001 -- never-raise contract, forward as actionable text
            _log_metric("tool_error", connector.connector_id, connector_tool.name)
            _safe_record_tool_audit(
                workspace,
                connector_id=connector.connector_id,
                tool_name=connector_tool.name,
                args=args,
                landed=False,
            )
            return f"connector 呼叫失敗：{type(error).__name__}"

        # 落表已成功——以下記錄呼叫失敗不該讓這次 tool 呼叫回報成「失敗」,故走
        # _safe_record_* 吞掉例外。
        _safe_record_landing(
            workspace,
            connector_id=connector.connector_id,
            tool_name=connector_tool.name,
            args=args,
            land_as=land_as,
            observed_columns=landing_result.columns,
            input_schema_hash=input_schema_hash,
            snapshot_sha256=landing_result.sha256,
        )
        _safe_record_tool_audit(
            workspace,
            connector_id=connector.connector_id,
            tool_name=connector_tool.name,
            args=args,
            landed=True,
        )
        _log_metric("landing_ok", connector.connector_id, connector_tool.name)
        columns_text = ", ".join(landing_result.columns)
        return f"已落表 {land_as}：{landing_result.row_count} 列，欄位 {columns_text}"

    def _run(**kwargs: Any) -> str:
        land_as = kwargs.pop("land_as", None)
        args = {key: value for key, value in kwargs.items() if value is not None}

        if not budget.try_consume():
            _log_metric("budget_exhausted", connector.connector_id, connector_tool.name)
            return f"本輪 connector 呼叫已達上限（{budget.call_budget}）"

        try:
            return _execute(land_as, args)
        except Exception as error:  # noqa: BLE001 -- absolute safety net, agent loop MUST continue
            # `_execute` 已攔下已知例外類型轉成可行動訊息;這一層只接住意外 bug,回泛用
            # 文字並記警告,絕不讓例外逸出 tool 中斷 agent loop。
            logger.warning(
                "connector tool wrapper raised unexpectedly: connector=%s tool=%s error=%s",
                connector.connector_id,
                connector_tool.name,
                type(error).__name__,
            )
            return f"connector 呼叫失敗：{type(error).__name__}"

    return StructuredTool.from_function(
        func=_run,
        name=tool_name,
        description=tool_description,
        args_schema=args_model,
        # args_schema 驗證在 LangChain BaseTool.run() 內完成,早於 `_run`——沒有這個掛鉤,
        # ValidationError 會直接炸穿 ToolNode 中斷整個 turn。
        handle_validation_error=_describe_validation_error,
    )


def build_connector_tools(
    connectors: Sequence[Connector],
    connection: duckdb.DuckDBPyConnection,
    connection_lock: threading.Lock,
    workspace: SessionWorkspace,
    *,
    call_budget: int = 12,
) -> list[BaseTool]:
    """把每個已選 connector 的每個 tool 包成一個 LangChain tool(名稱
    `{connector_id}_{tool.name}`,命名空間前綴防跨 connector 撞名)。所有回傳的 tool 共用
    同一個 `_CallBudget`——同一次呼叫代表同一個 turn,見檔頭說明。"""
    budget = _CallBudget(call_budget=call_budget)
    return [
        _build_tool(connector, connector_tool, connection, connection_lock, workspace, budget)
        for connector in connectors
        for connector_tool in connector.tools
    ]
