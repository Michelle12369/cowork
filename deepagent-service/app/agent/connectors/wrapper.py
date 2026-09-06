"""LangChain tool 包裝層——把 connector 供應層的抽象(`ConnectorTool`)包成每個
(connector, tool) 一個 LangChain `BaseTool`,加入命名空間前綴、每次呼叫自動落表、每 turn
呼叫上限與退貨整形。
"""

import hashlib
import json
import logging
import re
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import duckdb
from langchain_core.tools import BaseTool, StructuredTool

from app.agent.connectors.model import Connector, ConnectorTool, ConnectorToolError
from app.agent.tools.data import render_markdown_table
from app.agent.tools.framing import frame_data_content
from app.engine.api_snapshot import (
    LANDING_PREVIEW_MAX_ROWS,
    EmptyLandingError,
    LandingResult,
    land_response,
)

logger = logging.getLogger(__name__)

# 卸表提示——每次落表都是本輪暫存表,下一輪需要時模型須重新呼叫該 tool。
_TABLE_LIFETIME_NOTE = (
    "This table lives only for the current turn; call the tool again next turn if needed."
)


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


def connector_table_name(connector_id: str, tool_name: str, args: dict[str, Any]) -> str:
    """落表表名——同參數必得同名(last-wins,重呼叫互相覆蓋),不同參數必得不同名,
    平行呼叫互不影響。無參數時就是 base,不接雜湊;有參數則接 8 碼 SHA-256 雜湊
    (canonical JSON,鍵排序後編碼),避免序號命名下模型在平行呼叫間對錯表。"""
    base = re.sub(r"\W", "_", f"{connector_id}_{tool_name}")
    if not args:
        return base
    canonical_json = json.dumps(args, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    args_hash = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()[:8]
    return f"{base}_{args_hash}"


def _build_args_schema(connector_tool: ConnectorTool) -> dict[str, Any]:
    """`input_schema` 原樣透傳給 LangChain(args_schema 支援 JSON Schema dict)。dict schema
    模式下 LangChain 不做參數驗證——必填檢查移至 `_run`(見該處)。"""
    return dict(connector_tool.input_schema)


def _format_landing_feedback(
    connector_id: str, tool_name: str, args: dict[str, Any], landing_result: LandingResult
) -> str:
    args_json = json.dumps(args, ensure_ascii=False)
    columns_text = ", ".join(landing_result.columns)
    landing_summary = (
        f"Landed table {landing_result.table_name} ({connector_id}.{tool_name}, "
        f"args {args_json}): {landing_result.row_count} rows, columns {columns_text}"
    )
    lines = [landing_summary]
    if landing_result.envelope_fields:
        envelope_text = ", ".join(
            f"{key}={json.dumps(value, ensure_ascii=False)}"
            for key, value in landing_result.envelope_fields.items()
        )
        lines.append(f"Other response fields: {envelope_text}")
    preview_markdown = render_markdown_table(
        landing_result.columns,
        landing_result.preview_rows,
        truncated=False,
        max_rows=LANDING_PREVIEW_MAX_ROWS,
    )
    lines.append(
        f"Preview of the first {min(landing_result.row_count, LANDING_PREVIEW_MAX_ROWS)} rows:"
    )
    lines.append(frame_data_content(preview_markdown))
    if landing_result.row_count > LANDING_PREVIEW_MAX_ROWS:
        lines.append(
            f"(showing the first {LANDING_PREVIEW_MAX_ROWS} of {landing_result.row_count} rows)"
        )
    lines.append(_TABLE_LIFETIME_NOTE)
    return "\n".join(lines)


def _build_tool(
    connector: Connector,
    connector_tool: ConnectorTool,
    connection: duckdb.DuckDBPyConnection,
    connection_lock: threading.Lock,
    landing_dir: Path,
    budget: _CallBudget,
) -> BaseTool:
    tool_name = f"{connector.connector_id}_{connector_tool.name}"
    tool_description = f"[{connector.display_name}] {connector_tool.description}"
    args_schema = _build_args_schema(connector_tool)
    required_names = tuple(connector_tool.input_schema.get("required", []))

    def _execute(args: dict[str, Any]) -> str:
        try:
            response = connector_tool.call(args)
        except ConnectorToolError as error:
            return str(error)
        except Exception as error:  # noqa: BLE001 -- never-raise contract, forward as actionable text
            return f"Connector call failed: {type(error).__name__}"

        table_name = connector_table_name(connector.connector_id, connector_tool.name, args)
        try:
            landing_result = land_response(
                connection, connection_lock, landing_dir, table_name, response
            )
        except (EmptyLandingError, ValueError) as error:
            # EmptyLanding=0 列不落表;ValueError=table_name 未過 duck 的 alias 驗證——皆為
            # 預期錯誤,訊息已可行動,原樣回傳不包成泛用訊息蓋掉細節。
            return str(error)
        except Exception as error:  # noqa: BLE001 -- never-raise contract, forward as actionable text
            return f"Connector call failed: {type(error).__name__}"

        return _format_landing_feedback(
            connector.connector_id, connector_tool.name, args, landing_result
        )

    def _run(**kwargs: Any) -> str:
        args = {key: value for key, value in kwargs.items() if value is not None}

        # dict args_schema 模式下 LangChain 不驗參數——必填檢查在此補上,缺欄不發網路請求,
        missing_names = [name for name in required_names if name not in args]
        if missing_names:
            missing_text = "; ".join(f"{name}: Field required" for name in missing_names)
            return f"Argument validation failed -- {missing_text}. Fix the arguments and retry."

        if not budget.try_consume():
            return f"Connector call budget for this turn is exhausted ({budget.call_budget})."

        try:
            return _execute(args)
        except Exception as error:  # noqa: BLE001 -- absolute safety net, agent loop MUST continue
            logger.warning(
                "connector tool wrapper raised unexpectedly: connector=%s tool=%s error=%s",
                connector.connector_id,
                connector_tool.name,
                type(error).__name__,
            )
            return f"Connector call failed: {type(error).__name__}"

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
    landing_dir: Path,
    *,
    call_budget: int = 12,
) -> list[BaseTool]:
    """把每個已選 connector 的每個 tool 包成一個 LangChain tool(名稱
    `{connector_id}_{tool.name}`,命名空間前綴防跨 connector 撞名)。所有回傳的 tool 共用
    同一個 `_CallBudget`——同一次呼叫代表同一個 turn,見檔頭說明。"""
    budget = _CallBudget(call_budget=call_budget)
    return [
        _build_tool(connector, connector_tool, connection, connection_lock, landing_dir, budget)
        for connector in connectors
        for connector_tool in connector.tools
    ]
