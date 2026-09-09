"""這是 LangChain tool 的包裝層, 把 connector 供應層的抽象(ConnectorTool)包成每一組
(connector, tool) 各自的 LangChain BaseTool, 加上命名空間前綴, 每次呼叫自動落表, 並處理
每一輪的呼叫上限和回傳內容整形.
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

# 這是給模型看的提示: 每次落表都是這一輪的暫存表, 下一輪如果還需要就要重新呼叫這個 tool.
_TABLE_LIFETIME_NOTE = (
    "This table lives only for the current turn; call the tool again next turn if needed."
)


@dataclass
class _CallBudget:
    """這是同一輪裡所有包裝過的工具共用的呼叫額度, 細節看檔頭說明裡關於每輪上限是共享
    狀態的部分."""

    call_budget: int
    lock: threading.Lock = field(default_factory=threading.Lock)
    calls_made: int = 0

    def try_consume(self) -> bool:
        """回傳目前還有沒有額度可用, 有的話就原子遞增. check 跟 increment 都在同一把鎖裡
        完成, 避免平行的 tool_calls 發生競態, 讀到超額之前的計數."""
        with self.lock:
            if self.calls_made >= self.call_budget:
                return False
            self.calls_made += 1
            return True


def connector_table_name(connector_id: str, tool_name: str, args: dict[str, Any]) -> str:
    """算出落表用的表名. 相同參數要得到相同名字, 不同參數要得到不同名字, 避免平行呼叫互相覆蓋.
    沒有參數就用 base 本身, 有參數就接上參數內容的 8 碼雜湊."""
    base = re.sub(r"\W", "_", f"{connector_id}_{tool_name}")
    if not args:
        return base
    canonical_json = json.dumps(args, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    args_hash = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()[:8]
    return f"{base}_{args_hash}"


def _build_args_schema(connector_tool: ConnectorTool) -> dict[str, Any]:
    """把 input_schema 原樣傳給 LangChain, 因為 args_schema 支援 JSON Schema 格式的
    dict. 在 dict schema 模式下 LangChain 不會做參數驗證, 必填檢查移到 _run 裡做."""
    return dict(connector_tool.input_schema)


def _dotted(path: list[str]) -> str:
    return ".".join(path)


def describe_raw_response_shape(
    response: Any,
    unwrap_path: list[str] | None,
    envelope_fields: dict[str, Any],
    row_count: int,
) -> str:
    """給模型看的一段英文: raw 回傳值長什麼樣, 表是從哪一層落的, 在 dashboard 的 handler 裡該讀哪個路徑."""
    row_count_text = f"{row_count} object" + ("" if row_count == 1 else "s")
    if isinstance(response, list):
        return (
            f"Raw response shape: array of {row_count_text}. In the dashboard, mcp() hands "
            "your handler the raw response as r.data, so r.data is already the array; read the "
            "rows with `r.data`."
        )
    top_level_keys = ", ".join(response.keys()) if isinstance(response, dict) else "?"
    if unwrap_path is None:
        first_key = next(iter(response), "field") if isinstance(response, dict) else "field"
        return (
            f"Raw response shape: object with keys [{top_level_keys}]; landed as a single row. "
            f"In the dashboard r.data is that object; read fields directly (r.data.{first_key})."
        )
    rows_path = _dotted(unwrap_path)
    lines = [
        (
            f"Raw response shape: object with keys [{top_level_keys}]. The table was built from "
            f"response.{rows_path} (an array of {row_count_text})"
            + ("; nothing else was dropped." if not envelope_fields else ".")
        ),
        (
            "In the dashboard, mcp() hands your handler the raw response as r.data, so read the "
            f"rows with `r.data.{rows_path}` -- not `r.data`."
        ),
    ]
    if envelope_fields:
        envelope_prefix = _dotted(["r.data", *unwrap_path[:-1]])
        field_names = ", ".join(envelope_fields)
        located = ", ".join(f"{envelope_prefix}.{name}" for name in envelope_fields)
        lines.append(
            f"Other fields beside the rows ({field_names}) were not landed; in the dashboard "
            f"they are at {located}."
        )
    return "\n".join(lines)


def _format_landing_feedback(
    connector_id: str,
    tool_name: str,
    args: dict[str, Any],
    landing_result: LandingResult,
    response: Any,
) -> str:
    args_json = json.dumps(args, ensure_ascii=False)
    columns_text = ", ".join(landing_result.columns)
    landing_summary = (
        f"Landed table {landing_result.table_name} ({connector_id}.{tool_name}, "
        f"args {args_json}): {landing_result.row_count} rows, columns {columns_text}"
    )
    lines = [landing_summary]
    lines.append(
        describe_raw_response_shape(
            response,
            landing_result.unwrap_path,
            landing_result.envelope_fields,
            landing_result.row_count,
        )
    )
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
    """把一個 MCP tool 包成 LangChain tool, 多做: tool 名稱加 connector 前綴, required field
    validation, 扣本輪呼叫額度, 回應存成 DuckDB 表. tool message 不是原始資料而是落表描述:
    表名, tool 與參數, 列數, 欄位, 信封欄位, 前 20 列預覽, 表只活本輪的提醒. 失敗回文字不拋例外."""
    tool_name = f"{connector.connector_id}_{connector_tool.name}"
    tool_description = f"[{connector.display_name}] {connector_tool.description}"
    args_schema = _build_args_schema(connector_tool)
    required_names = tuple(connector_tool.input_schema.get("required", []))

    def _execute(args: dict[str, Any]) -> str:
        try:
            response = connector_tool.call(args)
        except ConnectorToolError as error:
            return str(error)
        except Exception as error:  # never-raise contract, forward as actionable text
            logger.warning(
                "connector call raised unexpectedly: connector=%s tool=%s",
                connector.connector_id,
                connector_tool.name,
                exc_info=error,
            )
            return f"Connector call failed: {type(error).__name__}"

        table_name = connector_table_name(connector.connector_id, connector_tool.name, args)
        try:
            landing_result = land_response(
                connection, connection_lock, landing_dir, table_name, response
            )
        except EmptyLandingError as error:
            # 0 列不落表, 但呼叫成功, 模型仍需要 raw 形狀才寫得出 dashboard 的讀列路徑.
            shape_text = describe_raw_response_shape(
                response, error.unwrap_path, error.envelope_fields, 0
            )
            return f"{error}\n{shape_text}"
        except ValueError as error:
            # table_name 沒通過驗證; 訊息本身已可行動, 原樣回傳.
            return str(error)
        except Exception as error:  # never-raise contract, forward as actionable text
            logger.warning(
                "connector landing failed: connector=%s tool=%s table=%s",
                connector.connector_id,
                connector_tool.name,
                table_name,
                exc_info=error,
            )
            return f"Connector landing failed: {type(error).__name__}"

        return _format_landing_feedback(
            connector.connector_id, connector_tool.name, args, landing_result, response
        )

    def _run(**kwargs: Any) -> str:
        args = {key: value for key, value in kwargs.items() if value is not None}

        # dict args_schema 模式下 LangChain 不會驗參數, 必填檢查在這裡補上, 缺欄位就不會
        # 發出網路請求.
        missing_names = [name for name in required_names if name not in args]
        if missing_names:
            missing_text = "; ".join(f"{name}: Field required" for name in missing_names)
            return f"Argument validation failed -- {missing_text}. Fix the arguments and retry."

        if not budget.try_consume():
            return f"Connector call budget for this turn is exhausted ({budget.call_budget})."

        try:
            return _execute(args)
        except Exception as error:  # noqa: BLE001 -- last safety net so the agent loop keeps running
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
    call_budget: int = 50,
) -> list[BaseTool]:
    """把每一個已選 connector 底下的每個 tool 都包成一個 LangChain tool, 名稱是
    {connector_id}_{tool.name}, 命名空間前綴用來防止跨 connector 撞名. 回傳的所有 tool
    共用同一個 _CallBudget, 因為同一次呼叫代表同一輪, 細節看檔頭說明."""
    budget = _CallBudget(call_budget=call_budget)
    return [
        _build_tool(connector, connector_tool, connection, connection_lock, landing_dir, budget)
        for connector in connectors
        for connector_tool in connector.tools
    ]
