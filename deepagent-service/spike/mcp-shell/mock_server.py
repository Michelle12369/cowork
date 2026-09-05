"""THROWAWAY spike -- mock MCP server ``sales-mock``, stateless HTTP on 127.0.0.1:8765.

Run: ``uv run python spike/mcp-shell/mock_server.py`` (from ``deepagent-service/``).

Mirrors the fixture pattern in ``tests/test_chat_turn_connectors.py``/``tests/test_mcp_adapter.py``:
``FastMCP(...)`` + ``SkillsDirectoryProvider(roots=...)`` + ``mcp_server.http_app(stateless_http=True)``.
"""

import random
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import uvicorn
from fastmcp import FastMCP
from fastmcp.server.providers.skills import SkillsDirectoryProvider

_HOST = "127.0.0.1"
_PORT = 8765
_SEED = 20260904
_ANCHOR_DATE = date.today()
_TOTAL_DAYS = 365
_ORDERS_PER_90_DAYS = 200

_REGIONS = [
    {"region": "north", "display_name": "North"},
    {"region": "south", "display_name": "South"},
    {"region": "east", "display_name": "East"},
    {"region": "west", "display_name": "West"},
]

_PRODUCTS = [
    ("Aurora Desk Lamp", 24.0),
    ("Cascade Water Bottle", 18.0),
    ("Meridian Backpack", 62.0),
    ("Pebble Wireless Mouse", 29.0),
    ("Summit Trail Jacket", 89.0),
    ("Cobalt Notebook Set", 12.0),
]

_STATUSES = ["completed", "completed", "completed", "pending", "cancelled", "refunded"]

_DEFECT_TYPES = ["packaging", "late_delivery", "wrong_item", "damaged", "other"]
_DEFECT_WEIGHTS = [3, 5, 2, 4, 1]


def _generate_orders() -> list[dict[str, Any]]:
    """一年份、~811 筆(200/90 天等比例外推)的 1NF 訂單列,啟動時產生一次、之後純過濾。"""
    randomizer = random.Random(_SEED)
    row_count = round(_ORDERS_PER_90_DAYS * _TOTAL_DAYS / 90)
    orders: list[dict[str, Any]] = []
    for order_index in range(row_count):
        days_ago = randomizer.randint(0, _TOTAL_DAYS - 1)
        order_date = _ANCHOR_DATE - timedelta(days=days_ago)
        region = randomizer.choice(_REGIONS)["region"]
        product_name, base_price = randomizer.choice(_PRODUCTS)
        quantity = randomizer.randint(1, 20)
        unit_price = round(base_price * randomizer.uniform(0.9, 1.1), 2)
        orders.append(
            {
                "order_id": f"ORD-{order_index + 1:05d}",
                "order_date": order_date.isoformat(),
                "region": region,
                "product": product_name,
                "quantity": quantity,
                "unit_price": unit_price,
                "amount": round(quantity * unit_price, 2),
                "status": randomizer.choice(_STATUSES),
            }
        )
    orders.sort(key=lambda row: row["order_date"])
    return orders


_ORDERS = _generate_orders()

mcp_server = FastMCP("sales-mock")


@mcp_server.tool()
def list_regions() -> list[dict[str, str]]:
    """列出可選的銷售區域(id + 顯示名),供 viewer 的區域下拉選單使用。"""
    return list(_REGIONS)


@mcp_server.tool()
def list_orders(regions: list[str] | None = None, days: int = 30) -> list[dict[str, Any]]:
    """列出最近 ``days`` 天內的訂單明細(1NF 列),可選用 ``regions``(來自 ``list_regions``
    的 region id 清單)過濾區域。"""
    cutoff_date = _ANCHOR_DATE - timedelta(days=days)
    region_filter = set(regions) if regions else None
    return [
        order
        for order in _ORDERS
        if date.fromisoformat(order["order_date"]) >= cutoff_date
        and (region_filter is None or order["region"] in region_filter)
    ]


@mcp_server.tool()
def defect_summary(days: int = 30) -> list[dict[str, Any]]:
    """回傳最近 ``days`` 天的瑕疵類型統計(count + rate),5 種瑕疵類型。"""
    randomizer = random.Random(_SEED + days)
    scale = max(days, 1) / 30.0
    counts = [
        max(1, round(weight * scale * randomizer.uniform(0.85, 1.15))) for weight in _DEFECT_WEIGHTS
    ]
    total = sum(counts)
    return [
        {
            "defect_type": defect_type,
            "count": count,
            "rate": round(count / total * 100, 2),
        }
        for defect_type, count in zip(_DEFECT_TYPES, counts, strict=True)
    ]


_skills_root = Path(__file__).parent / "skills"
mcp_server.add_provider(SkillsDirectoryProvider(roots=_skills_root))


if __name__ == "__main__":
    uvicorn.run(mcp_server.http_app(stateless_http=True), host=_HOST, port=_PORT)
