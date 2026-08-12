"""本機手動驗證用 mock API——兩個端點回固定 JSON 陣列。
uv run python scripts/mock_api_server.py  # :9100
one-local.properties 設 API_MOCK_BASE_URL=http://localhost:9100
"""

import random

import uvicorn
from fastapi import FastAPI

app = FastAPI(title="erd-cowork mock API")

_MACHINE_ROWS = [
    {"machine": "M1", "site": "TP", "model": "X-200", "installed": "2024-03-01"},
    {"machine": "M2", "site": "TP", "model": "X-200", "installed": "2024-06-15"},
    {"machine": "M3", "site": "KH", "model": "Z-90", "installed": "2025-01-20"},
    {"machine": "M4", "site": "KH", "model": "Z-90", "installed": "2025-08-05"},
]

_DAYS_BY_RANGE = {"7d": 7, "30d": 30, "90d": 90}


@app.get("/orders")
def orders(date_range: str = "30d", machines: str = "") -> list[dict]:
    """依參數生成確定性假訂單(seed 固定,同參數同輸出——手動驗證可重現)。"""
    selected_machines = [name for name in machines.split(",") if name] or ["M1"]
    day_count = _DAYS_BY_RANGE.get(date_range, 30)
    generator = random.Random(f"{date_range}:{machines}")
    rows = []
    order_id = 1
    for day_offset in range(day_count):
        for machine in selected_machines:
            rows.append(
                {
                    "order_id": order_id,
                    "order_date": f"2026-08-{(day_offset % 28) + 1:02d}",
                    "machine": machine,
                    "quantity": generator.randint(50, 500),
                    "defect_count": generator.randint(0, 12),
                }
            )
            order_id += 1
    return rows


@app.get("/machines")
def machines_listing(site: str = "") -> list[dict]:
    if not site:
        return _MACHINE_ROWS
    return [row for row in _MACHINE_ROWS if row["site"] == site]


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=9100)
