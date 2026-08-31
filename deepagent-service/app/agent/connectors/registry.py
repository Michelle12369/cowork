"""純測試 fixture——產線一律 MCP,本模組僅供 pytest 直組 Connector 物件。

`demo_connector()` 是合成資料版 connector（無網路呼叫），供「選 connector→lookup→
ask_user→data→落表→replay manifest」整條管線在測試裡不需要真 MCP server 也能組出
`Connector` 物件驗證。production wire 路徑一律走 `mcp_adapter.load_mcp_connector`。
"""

from app.agent.connectors.model import Connector, ConnectorTool, ConnectorToolError

# 合成 fab 清單，供 list_fabs 使用(lookup 用途，不落表)。
_DEMO_FABS: tuple[dict, ...] = (
    {"id": "FAB_A", "name": "Fab A - Hsinchu", "region": "TW"},
    {"id": "FAB_B", "name": "Fab B - Tainan", "region": "TW"},
    {"id": "FAB_C", "name": "Fab C - Kaohsiung", "region": "TW"},
)

# 合成品質量測資料——9 列，含一個淺巢狀欄 device({"id","name"})，演練寬鬆落表。內容固定，
# 不含隨機性/時間依賴，fab/week 由呼叫時併入每一列，同一組 (fab, week) 永遠回傳相同結果。
_DEMO_QUALITY_ROWS: tuple[dict, ...] = (
    {
        "lot_id": "LOT-1001",
        "device": {"id": "DEV-01", "name": "Device Alpha"},
        "station": "ETCH-01",
        "yield_pct": 98.2,
        "defect_count": 3,
        "measured_at": "2026-08-01",
    },
    {
        "lot_id": "LOT-1002",
        "device": {"id": "DEV-01", "name": "Device Alpha"},
        "station": "ETCH-02",
        "yield_pct": 97.5,
        "defect_count": 5,
        "measured_at": "2026-08-01",
    },
    {
        "lot_id": "LOT-1003",
        "device": {"id": "DEV-02", "name": "Device Beta"},
        "station": "ETCH-01",
        "yield_pct": 95.8,
        "defect_count": 9,
        "measured_at": "2026-08-02",
    },
    {
        "lot_id": "LOT-1004",
        "device": {"id": "DEV-02", "name": "Device Beta"},
        "station": "CVD-01",
        "yield_pct": 96.4,
        "defect_count": 7,
        "measured_at": "2026-08-02",
    },
    {
        "lot_id": "LOT-1005",
        "device": {"id": "DEV-03", "name": "Device Gamma"},
        "station": "CVD-01",
        "yield_pct": 99.1,
        "defect_count": 1,
        "measured_at": "2026-08-03",
    },
    {
        "lot_id": "LOT-1006",
        "device": {"id": "DEV-03", "name": "Device Gamma"},
        "station": "CVD-02",
        "yield_pct": 94.7,
        "defect_count": 12,
        "measured_at": "2026-08-03",
    },
    {
        "lot_id": "LOT-1007",
        "device": {"id": "DEV-04", "name": "Device Delta"},
        "station": "LITHO-01",
        "yield_pct": 93.2,
        "defect_count": 15,
        "measured_at": "2026-08-04",
    },
    {
        "lot_id": "LOT-1008",
        "device": {"id": "DEV-04", "name": "Device Delta"},
        "station": "LITHO-02",
        "yield_pct": 97.9,
        "defect_count": 4,
        "measured_at": "2026-08-04",
    },
    {
        "lot_id": "LOT-1009",
        "device": {"id": "DEV-05", "name": "Device Epsilon"},
        "station": "LITHO-01",
        "yield_pct": 98.8,
        "defect_count": 2,
        "measured_at": "2026-08-05",
    },
)

_SKILL_MARKDOWN = """# demo_quality 操作劇本

## tools 清單與語意

- `list_fabs`：列出可查詢的 fab 清單(id／name／region)，無參數。純 lookup 用途，回傳結果
  不落表(不帶 `land_as`)，供反問使用者或直接列選項。
- `get_quality(fab, week)`：取得指定 fab、week 的品質量測資料，回傳信封
  `{"data": [...9 列...], "errorCode": ""}`。每列含淺巢狀欄 `device: {"id", "name"}`。
  `data` 建議落表(帶 `land_as`)，`errorCode` 非空時視為業務錯誤，不落表。

## 呼叫順序與相依

1. 若使用者未直接指名 fab，先呼叫 `list_fabs`（不落表）取得候選，交由 agent 反問使用者
   (ask_user)或直接在對話中列出選項。
2. 取得 fab 與 week 後才可呼叫 `get_quality`；`get_quality` 不依賴 `list_fabs` 的落表結果，
   僅需要其中一個 `id` 值作為 `fab` 參數。
3. `get_quality` 回傳的 `errorCode` 非空字串時代表業務層錯誤(如 fab 已下線)，agent 需將
   `errorCode` 內容轉述給使用者，不落表、不當作資料使用。

## 參數來源

- `fab`：來自 `list_fabs` 回傳清單裡任一 fab 物件的 `id` 欄位；使用者也可能直接在對話中指名
  fab 代號，此時可略過 `list_fabs`。
- `week`：由使用者於對話中提供的 ISO 週別字串(例如 `2026-W32`)；本 connector 不提供 week
  的 lookup tool，需 ask_user 取得或由使用者主動給出。

## 範例

使用者：「幫我看 Fab A 上週的品質數據」

1. 呼叫 `list_fabs()`（不落表）→ 取得 `[{"id": "FAB_A", ...}, ...]`，確認使用者指的是
   `FAB_A`。
2. 反問使用者要看哪一週（或使用者已在訊息中提供），例如使用者回覆 `2026-W32`。
3. 呼叫 `get_quality(fab="FAB_A", week="2026-W32")`，並帶 `land_as="quality_fab_a"`
   —— 表示這次呼叫的 `data` 要落表成 DuckDB alias `quality_fab_a`，之後即可對
   `quality_fab_a` 下 SQL 分析良率與缺陷分布。
"""


def _list_fabs(args: dict) -> object:
    return [dict(fab) for fab in _DEMO_FABS]


def _get_quality(args: dict) -> object:
    fab = args.get("fab")
    week = args.get("week")
    valid_fab_ids = [fab_entry["id"] for fab_entry in _DEMO_FABS]
    if fab not in valid_fab_ids:
        raise ConnectorToolError(
            f"未知的 fab '{fab}'——可用 fab id：{', '.join(valid_fab_ids)}（呼叫 list_fabs "
            "取得完整清單）"
        )
    rows = [{**row, "fab": fab, "week": week} for row in _DEMO_QUALITY_ROWS]
    return {"data": rows, "errorCode": ""}


def demo_connector() -> Connector:
    return Connector(
        connector_id="demo_quality",
        display_name="示範品質資料（合成）",
        tools=(
            ConnectorTool(
                name="list_fabs",
                description="列出可查詢的 fab 清單(id/name/region)，無參數；用於取得 "
                "get_quality 的 fab 候選。",
                input_schema={"type": "object", "properties": {}, "required": []},
                call=_list_fabs,
            ),
            ConnectorTool(
                name="get_quality",
                description="取得指定 fab/week 的品質量測資料(9 列合成資料)，回傳信封 "
                "{data, errorCode}。",
                input_schema={
                    "type": "object",
                    "properties": {
                        "fab": {
                            "type": "string",
                            "description": "fab id，取自 list_fabs 回傳清單的 id 欄位",
                        },
                        "week": {
                            "type": "string",
                            "description": "ISO 週別，例如 2026-W32",
                        },
                    },
                    "required": ["fab", "week"],
                },
                call=_get_quality,
            ),
        ),
        skill_markdown=_SKILL_MARKDOWN,
    )
