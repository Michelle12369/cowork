"""LangChain tool 包裝層——把 connector 供應層的抽象(`ConnectorTool`)包成每個
(connector, tool) 一個 LangChain `BaseTool`,加入呼叫點 `land_as` 落表決策、命名空間前綴、
每 turn 呼叫上限與退貨整形。
"""

import json
import logging
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import duckdb
from langchain_core.tools import BaseTool, StructuredTool

from app.agent.connectors.model import Connector, ConnectorTool, ConnectorToolError
from app.agent.tools.framing import frame_data_content
from app.engine.api_snapshot import EmptyLandingError, land_snapshot
from app.engine.replay_manifest import record_call, record_landing, schema_hash
from app.engine.workspace import SessionWorkspace

logger = logging.getLogger(__name__)

# lookup 回應(未落表)回給模型的字元數上限
LLM_VIEW_MAX_CHARS = 8000

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


def _build_args_schema(connector_id: str, connector_tool: ConnectorTool) -> dict[str, Any]:
    """`input_schema` 原樣透傳給 LangChain(args_schema 支援 JSON Schema dict)。
    只做兩件事:驗 `land_as`保留字(connector tool 自帶同名參數在掛載時 fail loud),與注入選用的 `land_as` 欄位。
    dict schema 模式下 LangChain 不做參數驗證——必填檢查移至 `_run`(見該處)。"""
    properties: dict[str, dict] = connector_tool.input_schema.get("properties", {})
    if "land_as" in properties:
        raise ValueError(
            f"connector tool parameter name land_as is reserved (connector={connector_id!r}, "
            f"tool={connector_tool.name!r}) -- use a different parameter name"
        )
    schema = dict(connector_tool.input_schema)
    schema["properties"] = {
        **properties,
        "land_as": {"type": "string", "description": _LAND_AS_DESCRIPTION},
    }
    return schema


def _render_lookup_view(response: object) -> str:
    """不帶 land_as 的回應——JSON 序列化後截到 `LLM_VIEW_MAX_CHARS`,超過時附註記回應"""
    serialized = json.dumps(response, ensure_ascii=False)
    if len(serialized) > LLM_VIEW_MAX_CHARS:
        serialized = (
            f"{serialized[:LLM_VIEW_MAX_CHARS]}...(truncated to {LLM_VIEW_MAX_CHARS} characters)"
        )
    return frame_data_content(serialized)


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
    args_schema = _build_args_schema(connector.connector_id, connector_tool)
    required_names = tuple(connector_tool.input_schema.get("required", []))
    input_schema_hash = schema_hash(connector_tool.input_schema)

    def _execute(land_as: str | None, args: dict[str, Any]) -> str:
        try:
            response = connector_tool.call(args)
        except ConnectorToolError as error:
            return str(error)
        except Exception as error:  # noqa: BLE001 -- never-raise contract, forward as actionable text
            return f"connector 呼叫失敗：{type(error).__name__}"

        try:
            record_call(
                workspace,
                connector_id=connector.connector_id,
                tool_name=connector_tool.name,
                args=args,
            )
        except Exception as error:  # noqa: BLE001 -- best-effort recording must not mask a successful call
            logger.warning(
                "record_call failed (non-fatal): connector=%s tool=%s error=%s",
                connector.connector_id,
                connector_tool.name,
                type(error).__name__,
            )

        if land_as is None:
            return _render_lookup_view(response)

        try:
            landing_result = land_snapshot(
                connection, connection_lock, workspace, land_as, response
            )
        except (EmptyLandingError, ValueError) as error:
            # EmptyLanding=0 列不落表;ValueError=land_as 未過 duck 的 alias 驗證——皆為
            # 預期錯誤,訊息已可行動,原樣回傳不包成泛用訊息蓋掉細節。
            return str(error)
        except Exception as error:  # noqa: BLE001 -- never-raise contract, forward as actionable text
            return f"connector 呼叫失敗：{type(error).__name__}"

        try:
            record_landing(
                workspace,
                connector_id=connector.connector_id,
                tool_name=connector_tool.name,
                args=args,
                land_as=land_as,
                observed_columns=landing_result.columns,
                input_schema_hash=input_schema_hash,
                snapshot_sha256=landing_result.sha256,
            )
        except Exception as error:  # noqa: BLE001 -- best-effort recording must not mask a successful landing
            logger.warning(
                "record_landing failed (non-fatal): connector=%s tool=%s land_as=%s error=%s",
                connector.connector_id,
                connector_tool.name,
                land_as,
                type(error).__name__,
            )
        columns_text = ", ".join(landing_result.columns)
        return f"已落表 {land_as}：{landing_result.row_count} 列，欄位 {columns_text}"

    def _run(**kwargs: Any) -> str:
        land_as = kwargs.pop("land_as", None)
        args = {key: value for key, value in kwargs.items() if value is not None}

        # dict args_schema 模式下 LangChain 不驗參數——必填檢查在此補上,缺欄不發網路請求,
        missing_names = [name for name in required_names if name not in args]
        if missing_names:
            missing_text = "; ".join(f"{name}: Field required" for name in missing_names)
            return f"參數驗證失敗——{missing_text}。請修正後重試"

        if not budget.try_consume():
            return f"本輪 connector 呼叫已達上限（{budget.call_budget}）"

        try:
            return _execute(land_as, args)
        except Exception as error:  # noqa: BLE001 -- absolute safety net, agent loop MUST continue
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
        args_schema=args_schema,
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
