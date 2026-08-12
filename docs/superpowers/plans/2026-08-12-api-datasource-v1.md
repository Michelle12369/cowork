# API Datasource v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Session 可掛上預先註冊的 mock internal API 作為資料來源——模型以 QUESTION 反問蒐集參數後呼叫 `fetch_api_data`，回應正規化成 CSV 快照落 workspace `api/`、掛進 DuckDB，進入既有分析管線。

**Architecture:** 全部改動在 `deepagent-service/`（Java/前端零改動）。engine 層新增 registry（`api_registry.py`）與快照讀寫（`api_snapshot.py`，stdlib only）；agent 層新增 `fetch_api_data` tool（httpx，never-raise）；`chat_turn` 輪初掃 `api/` 補 mount＋manifest 餵 `kind="api"`；system prompt 注入 API sources context；`sources.md` 同步退役。設計文件：`docs/superpowers/specs/2026-08-08-api-datasource-design.md`（§12 實作藍圖）。

**Tech Stack:** Python 3.12 / FastAPI / DuckDB / httpx / pytest（`uv run pytest`）/ ruff。

**設計討論後補的三個前瞻調整**（不在 spec 原文，已與使用者定案）：
1. `ApiDefinition.response_format` 定為可判別 union 保留字（`"json-array"` v1 唯一實作；`"sql"`/`"custom"` 保留給未來巢狀/文件型回應的抽取程式路線）
2. 「回應→欄與列」隔成獨立函式 `normalize_json_array`（未來換抽取執行器的接縫）
3. fetch 時順手存原始回應 `api/{alias}.raw.json`（debug＋未來離線重抽）

## Global Constraints

- 分支：`feat/api-datasource`（已建）；每 task 完成即 commit
- `app/engine/**` 只准 stdlib＋boto3＋duckdb（ruff TID251 會擋）；httpx 一律放 `app/agent/tools/`
- 工具 never-raise：錯誤回結構化字串（`PARAM_ERROR:` / `API_ERROR:`），永不拋例外給 agent
- 進 LLM 視野的 upstream 內容（欄名、統計摘要）MUST 經 `frame_data_content` 包裝
- DuckDB connection 先掛後鎖：輪初重建走 Source 清單（鎖門前）、輪中追加只能純記憶體 `CREATE TABLE`＋`INSERT`，NEVER 解鎖或重開 connection
- 變數/參數 NEVER 用 1–2 字元名稱（`id` 等 domain 語彙除外）
- Secrets/URL NEVER hardcode——一律走 Settings（`one.properties`/env）
- 每個 task 跑 `uv run pytest -q tests/<該檔>` 綠了才 commit；最終 task 跑全套＋`uv run ruff check .`
- 所有指令在 `deepagent-service/` 目錄下執行

---

### Task 1: API registry（`app/engine/api_registry.py`）＋ Settings

**Files:**
- Create: `deepagent-service/app/engine/api_registry.py`
- Modify: `deepagent-service/app/config.py`（Settings 加兩個欄位）
- Modify: `deepagent-service/one.properties`（範本補 key，值留空）
- Test: `deepagent-service/tests/test_api_registry.py`

**Interfaces:**
- Produces: `ApiParameter`（frozen dataclass：`name/type/required/multi/prompt/options/options_source`）、`ApiDefinition`（frozen dataclass：`id/alias/name/endpoint_path/method/parameters/response_format/max_rows`）、`API_REGISTRY: dict[str, ApiDefinition]`（兩支 mock）、`validate_params(definition: ApiDefinition, params: dict) -> list[str]`（空 list＝合法）
- Settings 新欄位：`API_MOCK_BASE_URL: str = ""`、`API_FETCH_TIMEOUT_SECONDS: float = 30.0`

- [x] **Step 1: 寫失敗測試**

```python
"""tests/test_api_registry.py"""
from app.engine.api_registry import API_REGISTRY, validate_params


def test_registry_contains_two_mock_apis_with_api_prefixed_aliases():
    assert set(API_REGISTRY) == {"mock_orders", "mock_machines"}
    for definition in API_REGISTRY.values():
        assert definition.alias.startswith("api_")
        assert definition.response_format == "json-array"


def test_validate_params_valid_orders_params_returns_empty():
    definition = API_REGISTRY["mock_orders"]
    errors = validate_params(definition, {"date_range": "30d", "machines": ["M1", "M3"]})
    assert errors == []


def test_validate_params_missing_required_reports_name():
    definition = API_REGISTRY["mock_orders"]
    errors = validate_params(definition, {"date_range": "30d"})
    assert any("machines" in message for message in errors)


def test_validate_params_enum_out_of_range_rejected():
    definition = API_REGISTRY["mock_orders"]
    errors = validate_params(definition, {"date_range": "365d", "machines": ["M1"]})
    assert any("date_range" in message for message in errors)


def test_validate_params_multi_requires_list_and_single_rejects_list():
    definition = API_REGISTRY["mock_orders"]
    assert any(
        "machines" in message
        for message in validate_params(definition, {"date_range": "7d", "machines": "M1"})
    )
    assert any(
        "date_range" in message
        for message in validate_params(definition, {"date_range": ["7d"], "machines": ["M1"]})
    )


def test_validate_params_unknown_parameter_rejected():
    definition = API_REGISTRY["mock_machines"]
    errors = validate_params(definition, {"site": "TP", "bogus": 1})
    assert any("bogus" in message for message in errors)
```

另補：`number` 型別收 int/float 但拒 bool、`date` 型別驗 `date.fromisoformat`、`string` 拒非字串——各一條測試（用一個臨時 `ApiDefinition` 建構含該型別參數的 definition 來測，registry 兩支 mock 沒有 number/date 參數）。

- [x] **Step 2: 跑測試確認失敗**

Run: `uv run pytest -q tests/test_api_registry.py`
Expected: FAIL（ModuleNotFoundError: app.engine.api_registry）

- [x] **Step 3: 實作**

```python
"""app/engine/api_registry.py"""
"""API datasource 目錄——v1 兩支 mock API 的靜態定義與參數驗證。base-url 走 Settings
(API_MOCK_BASE_URL),registry 只存路徑段。

engine 層——stdlib only,禁止 import 任何 LLM 框架(ruff TID251 會擋)。
"""

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class ApiParameter:
    name: str
    type: str  # string | number | date | enum
    required: bool
    multi: bool
    prompt: str  # 反問使用者時的問題文案素材
    options: tuple[str, ...] | None = None  # 有值→QUESTION 直接列選項
    options_source: str | None = None  # v2 動態候選值預留,v1 恆 None


@dataclass(frozen=True)
class ApiDefinition:
    id: str
    alias: str  # 掛進 session 後的表名,慣例帶 api_ 前綴(與上傳檔 alias 同空間)
    name: str
    endpoint_path: str
    method: str
    parameters: tuple[ApiParameter, ...]
    # 可判別 union 保留字:json-array(v1 唯一實作)| sql | custom(未實作,巢狀回應路線預留)
    response_format: str = "json-array"
    max_rows: int = 5000


API_REGISTRY: dict[str, ApiDefinition] = {
    "mock_orders": ApiDefinition(
        id="mock_orders",
        alias="api_orders",
        name="訂單查詢 API",
        endpoint_path="/orders",
        method="GET",
        parameters=(
            ApiParameter(
                name="date_range", type="enum", required=True, multi=False,
                prompt="要查詢的日期區間", options=("7d", "30d", "90d"),
            ),
            ApiParameter(
                name="machines", type="enum", required=True, multi=True,
                prompt="要查詢的機台", options=("M1", "M2", "M3", "M4"),
            ),
        ),
    ),
    "mock_machines": ApiDefinition(
        id="mock_machines",
        alias="api_machines",
        name="機台清單 API",
        endpoint_path="/machines",
        method="GET",
        parameters=(
            ApiParameter(
                name="site", type="string", required=True, multi=False,
                prompt="要查詢的廠區代碼",
            ),
        ),
    ),
}


def _validate_scalar(parameter: ApiParameter, value: object) -> list[str]:
    if parameter.type == "enum":
        if not isinstance(value, str) or (
            parameter.options is not None and value not in parameter.options
        ):
            allowed = ", ".join(parameter.options or ())
            return [f"parameter {parameter.name!r}: {value!r} not in allowed options ({allowed})"]
        return []
    if parameter.type == "string":
        if not isinstance(value, str):
            return [f"parameter {parameter.name!r} must be a string, got {value!r}"]
        return []
    if parameter.type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return [f"parameter {parameter.name!r} must be a number, got {value!r}"]
        return []
    if parameter.type == "date":
        try:
            date.fromisoformat(str(value))
        except ValueError:
            return [f"parameter {parameter.name!r} must be an ISO date (YYYY-MM-DD), got {value!r}"]
        return []
    return [f"parameter {parameter.name!r} has unknown type {parameter.type!r}"]


def validate_params(definition: ApiDefinition, params: dict) -> list[str]:
    """回傳錯誤訊息清單,空 list＝合法。驗:未知參數、必填缺漏、multi 形狀、型別、enum 值域。"""
    errors: list[str] = []
    known_names = {parameter.name for parameter in definition.parameters}
    errors.extend(
        f"unknown parameter {name!r}" for name in params if name not in known_names
    )
    for parameter in definition.parameters:
        if parameter.name not in params:
            if parameter.required:
                errors.append(f"missing required parameter {parameter.name!r}")
            continue
        value = params[parameter.name]
        if parameter.multi:
            if not isinstance(value, list):
                errors.append(f"parameter {parameter.name!r} must be a list (multi-select)")
                continue
            scalar_values = value
        else:
            if isinstance(value, list):
                errors.append(f"parameter {parameter.name!r} must be a single value, not a list")
                continue
            scalar_values = [value]
        for scalar_value in scalar_values:
            errors.extend(_validate_scalar(parameter, scalar_value))
    return errors
```

`app/config.py` Settings 加（放 `AGENT_BUILTIN_SKILLS_DIR` 之後）：

```python
    API_MOCK_BASE_URL: str = ""
    API_FETCH_TIMEOUT_SECONDS: float = 30.0
```

`one.properties` 範本補一行（與其他 key 同格式、值留空）：`API_MOCK_BASE_URL=`。

- [x] **Step 4: 跑測試確認通過**

Run: `uv run pytest -q tests/test_api_registry.py tests/test_config.py`
Expected: PASS

- [x] **Step 5: Commit**

```bash
git add app/engine/api_registry.py app/config.py one.properties tests/test_api_registry.py
git commit -m "feat(deepagent): API datasource registry——兩支 mock 定義＋參數驗證"
```

---

### Task 2: 快照落地與正規化（`app/engine/api_snapshot.py`）

**Files:**
- Create: `deepagent-service/app/engine/api_snapshot.py`
- Modify: `deepagent-service/app/engine/workspace.py`（`SessionWorkspace` 加 `api_dir` property）
- Test: `deepagent-service/tests/test_api_snapshot.py`

**Interfaces:**
- Consumes: `SessionWorkspace`（Task 前已存在）
- Produces:
  - `API_SNAPSHOT_DIRNAME = "api"`、`API_SNAPSHOT_MAX_ROWS = 5000`
  - `SnapshotMeta`（frozen dataclass：`api_id: str, alias: str, params: dict, fetched_at: str, schema: tuple[tuple[str, str], ...], row_count: int, truncated: bool`）
  - `normalize_json_array(payload: object) -> tuple[list[str], list[list]]`（欄名聯集＋缺鍵補 None；非物件陣列拋 `ValueError`——tool 層轉 `API_ERROR`。這是未來抽取執行器的替換接縫）
  - `sanitize_column_names(names: list[str]) -> list[str]`（非 `\w` 字元→`_`、空名→`column_N`、撞名加 `_2`/`_3` 後綴）
  - `infer_column_types(columns: list[str], rows: list[list]) -> tuple[tuple[str, str], ...]`（全 bool→BOOLEAN、全 int→BIGINT、int/float 混→DOUBLE、其餘/全 None→VARCHAR；忽略 None）
  - `write_snapshot(workspace: SessionWorkspace, meta: SnapshotMeta, columns: list[str], rows: list[list], raw_text: str | None) -> None`（寫 `api/{alias}.csv`＋`api/{alias}.meta.json`＋`api/{alias}.raw.json`，皆先落 `.part` 再 `Path.replace`）
  - `scan_snapshots(workspace: SessionWorkspace) -> list[SnapshotMeta]`（列 `api/*.meta.json`；csv 缺失＝壞快照→略過＋log warning；`api/` 不存在→空 list）
  - `SessionWorkspace.api_dir` property（`self.root / "api"`）

- [x] **Step 1: 寫失敗測試**

```python
"""tests/test_api_snapshot.py"""
import json

import pytest

from app.engine.api_snapshot import (
    SnapshotMeta,
    infer_column_types,
    normalize_json_array,
    sanitize_column_names,
    scan_snapshots,
    write_snapshot,
)
from app.engine.workspace import SessionWorkspace


def _snapshot_meta(alias: str = "api_orders") -> SnapshotMeta:
    return SnapshotMeta(
        api_id="mock_orders", alias=alias, params={"date_range": "30d"},
        fetched_at="2026-08-12T09:00:00Z",
        schema=(("order_id", "BIGINT"), ("amount", "DOUBLE")),
        row_count=2, truncated=False,
    )


def test_normalize_json_array_unions_keys_and_fills_missing_with_none():
    columns, rows = normalize_json_array(
        [{"order_id": 1, "amount": 5.0}, {"order_id": 2, "site": "TP"}]
    )
    assert columns == ["order_id", "amount", "site"]
    assert rows == [[1, 5.0, None], [2, None, "TP"]]


def test_normalize_json_array_rejects_non_array_and_non_object_rows():
    with pytest.raises(ValueError):
        normalize_json_array({"data": []})
    with pytest.raises(ValueError):
        normalize_json_array([1, 2])


def test_sanitize_column_names_replaces_unsafe_dedupes_and_names_empty():
    assert sanitize_column_names(["a b", "a.b", "a_b", ""]) == ["a_b", "a_b_2", "a_b_3", "column_4"]


def test_infer_column_types_per_column():
    columns = ["flag", "count", "ratio", "label", "empty"]
    rows = [[True, 1, 1.5, "x", None], [False, 2, 2, None, None]]
    assert infer_column_types(columns, rows) == (
        ("flag", "BOOLEAN"), ("count", "BIGINT"), ("ratio", "DOUBLE"),
        ("label", "VARCHAR"), ("empty", "VARCHAR"),
    )


def test_write_then_scan_roundtrip(tmp_path):
    workspace = SessionWorkspace(root=tmp_path)
    meta = _snapshot_meta()
    write_snapshot(workspace, meta, ["order_id", "amount"], [[1, 5.0], [2, 7.5]], '[{"raw": 1}]')
    assert (workspace.api_dir / "api_orders.csv").is_file()
    assert (workspace.api_dir / "api_orders.raw.json").read_text(encoding="utf-8") == '[{"raw": 1}]'
    assert not list(workspace.api_dir.glob("*.part"))
    scanned = scan_snapshots(workspace)
    assert scanned == [meta]


def test_scan_skips_broken_snapshot_missing_csv(tmp_path, caplog):
    workspace = SessionWorkspace(root=tmp_path)
    write_snapshot(workspace, _snapshot_meta(), ["order_id"], [[1]], None)
    (workspace.api_dir / "api_orders.csv").unlink()
    assert scan_snapshots(workspace) == []


def test_scan_empty_when_api_dir_absent(tmp_path):
    assert scan_snapshots(SessionWorkspace(root=tmp_path)) == []
```

- [x] **Step 2: 跑測試確認失敗**

Run: `uv run pytest -q tests/test_api_snapshot.py`
Expected: FAIL（ModuleNotFoundError）

- [x] **Step 3: 實作**

`workspace.py` 的 `SessionWorkspace` 加：

```python
    @property
    def api_dir(self) -> Path:
        return self.root / "api"
```

`app/engine/api_snapshot.py`（檔頭註明 engine 層 stdlib only；temp-then-rename 同 `source_cache` 慣例）：

```python
import csv
import json
import logging
import re
from dataclasses import asdict, dataclass

from pathlib import Path

from app.engine.workspace import SessionWorkspace

logger = logging.getLogger(__name__)

API_SNAPSHOT_DIRNAME = "api"
API_SNAPSHOT_MAX_ROWS = 5000  # 對齊 STORE_MAX_ROWS 哲學(app.engine.results)

_UNSAFE_COLUMN_CHARS = re.compile(r"\W", re.UNICODE)


@dataclass(frozen=True)
class SnapshotMeta:
    api_id: str
    alias: str
    params: dict
    fetched_at: str
    schema: tuple[tuple[str, str], ...]
    row_count: int
    truncated: bool


def normalize_json_array(payload: object) -> tuple[list[str], list[list]]:
    """json-array 回應→(欄名, 列)。欄名取各物件鍵的首見順序聯集,缺鍵補 None。
    非物件陣列拋 ValueError(呼叫端轉 API_ERROR)。空陣列合法→([], [])。
    這是回應格式的替換接縫:未來巢狀/文件型回應的抽取程式在此換入,簽名不變。"""
    if not isinstance(payload, list):
        raise ValueError("expected a JSON array of objects")
    columns: list[str] = []
    seen_columns: set[str] = set()
    for element in payload:
        if not isinstance(element, dict):
            raise ValueError("expected every array element to be a JSON object")
        for key in element:
            if key not in seen_columns:
                seen_columns.add(key)
                columns.append(key)
    rows = [[element.get(column) for column in columns] for element in payload]
    return columns, rows


def sanitize_column_names(names: list[str]) -> list[str]:
    """欄名源自 upstream 回應(不可信):非 \\w 字元一律換底線、空名補 column_N、
    撞名(含消毒後撞名)加 _2/_3 後綴——絕不靜默丟欄。"""
    sanitized: list[str] = []
    used: set[str] = set()
    for position, raw_name in enumerate(names, start=1):
        candidate = _UNSAFE_COLUMN_CHARS.sub("_", raw_name) or f"column_{position}"
        deduped = candidate
        suffix = 2
        while deduped in used:
            deduped = f"{candidate}_{suffix}"
            suffix += 1
        used.add(deduped)
        sanitized.append(deduped)
    return sanitized


def infer_column_types(columns: list[str], rows: list[list]) -> tuple[tuple[str, str], ...]:
    inferred: list[tuple[str, str]] = []
    for column_index, column_name in enumerate(columns):
        values = [row[column_index] for row in rows if row[column_index] is not None]
        if values and all(isinstance(value, bool) for value in values):
            duck_type = "BOOLEAN"
        elif values and all(
            isinstance(value, int) and not isinstance(value, bool) for value in values
        ):
            duck_type = "BIGINT"
        elif values and all(
            isinstance(value, (int, float)) and not isinstance(value, bool) for value in values
        ):
            duck_type = "DOUBLE"
        else:
            duck_type = "VARCHAR"
        inferred.append((column_name, duck_type))
    return tuple(inferred)


def _write_atomic(target_path: Path, content: str) -> None:
    part_path = target_path.with_suffix(target_path.suffix + ".part")
    part_path.write_text(content, encoding="utf-8")
    part_path.replace(target_path)


def write_snapshot(
    workspace: SessionWorkspace,
    meta: SnapshotMeta,
    columns: list[str],
    rows: list[list],
    raw_text: str | None,
) -> None:
    ...  # mkdir api_dir;csv 用 io.StringIO + csv.writer 組字串後 _write_atomic;
    # meta 用 json.dumps(asdict(meta), ensure_ascii=False, indent=2);raw_text 非 None 才寫 raw.json


def scan_snapshots(workspace: SessionWorkspace) -> list[SnapshotMeta]:
    ...  # glob "*.meta.json" 排序;json 載入還原 SnapshotMeta(schema 轉回 tuple of tuple);
    # 對應 csv 不存在→logger.warning + 略過
```

（`write_snapshot`/`scan_snapshots` 的 `...` 展開由 implementer 完成——行為已由 Step 1 測試完整釘死：三檔落地、無 `.part` 殘留、roundtrip 相等、壞快照略過、目錄不存在回空。）

- [x] **Step 4: 跑測試確認通過**

Run: `uv run pytest -q tests/test_api_snapshot.py tests/test_workspace.py`
Expected: PASS

- [x] **Step 5: Commit**

```bash
git add app/engine/api_snapshot.py app/engine/workspace.py tests/test_api_snapshot.py
git commit -m "feat(deepagent): API 快照落地——正規化/欄名消毒/型別推斷/寫掃 roundtrip"
```

---

### Task 3: `fetch_api_data` tool（`app/agent/tools/api_data.py`）＋ STEP 標題

**Files:**
- Create: `deepagent-service/app/agent/tools/api_data.py`
- Modify: `deepagent-service/app/agent/events.py`（`step_title_for` 加一行）
- Test: `deepagent-service/tests/test_api_tools.py`、`deepagent-service/tests/test_events.py`（補一行斷言）

**Interfaces:**
- Consumes: Task 1 的 `API_REGISTRY`/`validate_params`/`ApiDefinition`；Task 2 的 `normalize_json_array`/`sanitize_column_names`/`infer_column_types`/`write_snapshot`/`SnapshotMeta`/`API_SNAPSHOT_MAX_ROWS`；`frame_data_content`（`app.agent.tools.framing`）；Settings 的 `API_MOCK_BASE_URL`/`API_FETCH_TIMEOUT_SECONDS`
- Produces: `build_api_tools(connection: duckdb.DuckDBPyConnection, workspace: SessionWorkspace, registry: dict[str, ApiDefinition], transport: httpx.BaseTransport | None = None) -> list[BaseTool]`——單一工具 `@tool("fetch_api_data")`，簽名 `fetch_api_data(source_id: str, params: dict) -> str`。`transport` 參數是測試接縫（`httpx.MockTransport`）。不接 `ToolResultRecorder`（快照不是查詢結果，不發 TABLE 事件）

**執行順序**（全程 never-raise；此檔在 agent 層，httpx 合法）：
1. `source_id` 不在 registry → `PARAM_ERROR: unknown source_id {source_id!r}; available: mock_orders, mock_machines`
2. `validate_params` 非空 → `PARAM_ERROR: ` ＋ `"; ".join(errors)`
3. alias 撞名檢查：alias 已存在於 `information_schema.tables` 且 `workspace.api_dir / f"{alias}.meta.json"` 不存在（＝撞到上傳檔而非自家快照重取）→ `PARAM_ERROR: alias {alias!r} already taken by an uploaded file`
4. httpx 呼叫：`httpx.Client(base_url=settings.API_MOCK_BASE_URL, timeout=settings.API_FETCH_TIMEOUT_SECONDS, transport=transport)`；GET query params——multi 值 `",".join(...)` 逗號串；非 2xx → `API_ERROR: HTTP {status_code}`；`httpx.HTTPError`（連線/逾時）→ `API_ERROR: {error}`
5. `response.json()` 失敗或 `normalize_json_array` 拋 `ValueError` → `API_ERROR: unexpected response shape: {error}`
6. 截斷至 `definition.max_rows`（`truncated = len(rows) > definition.max_rows`）→ `sanitize_column_names` → `infer_column_types`
7. `fetched_at` 用 `datetime.now(timezone.utc).isoformat(timespec="seconds")`；組 `SnapshotMeta`、`write_snapshot(..., raw_text=response.text)`（覆寫舊份）
8. 鎖定 connection 上掛表（純記憶體，不觸發 external access；identifiers 皆已消毒——alias 來自 registry、欄名過 `sanitize_column_names`，仍以 `"` quote）：
   ```python
   column_ddl = ", ".join(f'"{name}" {duck_type}' for name, duck_type in schema)
   connection.execute(f'CREATE OR REPLACE TABLE "{alias}" ({column_ddl})')
   if rows:
       placeholders = ", ".join("?" for _ in schema)
       connection.executemany(f'INSERT INTO "{alias}" VALUES ({placeholders})', rows)
   ```
9. 回傳摘要（經 `frame_data_content` 包裝——欄名源自 upstream 不可信）：
   ```
   mounted table api_orders (N rows{, truncated to 5000}): order_id BIGINT, amount DOUBLE, ...
   ```

`events.py` `step_title_for` 在 `preview_data` 分支後加：

```python
    if tool_name == "fetch_api_data":
        return "取得 API 資料"
```

- [x] **Step 1: 寫失敗測試**

```python
"""tests/test_api_tools.py——httpx.MockTransport,不起真 server。"""
import json

import duckdb
import httpx
import pytest

from app.agent.tools.api_data import build_api_tools
from app.agent.tools.framing import DATA_FRAME_OPEN
from app.engine.api_registry import API_REGISTRY
from app.engine.workspace import SessionWorkspace


ORDER_ROWS = [
    {"order_id": 1, "machine": "M1", "amount": 120.5},
    {"order_id": 2, "machine": "M3", "amount": 88.0},
]


def _make_tool(tmp_path, handler, connection=None):
    if connection is None:
        connection = duckdb.connect(":memory:")
        connection.execute("SET enable_external_access = false")
        connection.execute("SET lock_configuration = true")
    workspace = SessionWorkspace(root=tmp_path)
    transport = httpx.MockTransport(handler)
    (fetch_tool,) = build_api_tools(connection, workspace, API_REGISTRY, transport=transport)
    return fetch_tool, connection, workspace


def _ok_handler(request: httpx.Request) -> httpx.Response:
    assert request.url.params["machines"] == "M1,M3"  # multi→逗號串
    return httpx.Response(200, json=ORDER_ROWS)


def test_fetch_success_mounts_table_queryable_on_locked_connection(tmp_path, monkeypatch):
    monkeypatch.setenv("API_MOCK_BASE_URL", "http://mock-api")
    fetch_tool, connection, workspace = _make_tool(tmp_path, _ok_handler)
    result = fetch_tool.invoke(
        {"source_id": "mock_orders", "params": {"date_range": "30d", "machines": ["M1", "M3"]}}
    )
    assert "api_orders" in result and DATA_FRAME_OPEN in result
    assert connection.execute('SELECT count(*) FROM "api_orders"').fetchone()[0] == 2
    assert (workspace.api_dir / "api_orders.meta.json").is_file()
    assert (workspace.api_dir / "api_orders.raw.json").is_file()


def test_unknown_source_id_returns_param_error(tmp_path):
    fetch_tool, _, _ = _make_tool(tmp_path, _ok_handler)
    result = fetch_tool.invoke({"source_id": "nope", "params": {}})
    assert result.startswith("PARAM_ERROR:") and "mock_orders" in result


def test_invalid_params_returns_param_error(tmp_path):
    fetch_tool, _, _ = _make_tool(tmp_path, _ok_handler)
    result = fetch_tool.invoke({"source_id": "mock_orders", "params": {"date_range": "365d"}})
    assert result.startswith("PARAM_ERROR:")


def test_alias_collision_with_uploaded_file_returns_param_error(tmp_path):
    connection = duckdb.connect(":memory:")
    connection.execute('CREATE TABLE "api_orders" (existing INTEGER)')
    connection.execute("SET enable_external_access = false")
    connection.execute("SET lock_configuration = true")
    fetch_tool, _, _ = _make_tool(tmp_path, _ok_handler, connection=connection)
    result = fetch_tool.invoke(
        {"source_id": "mock_orders", "params": {"date_range": "7d", "machines": ["M1"]}}
    )
    assert result.startswith("PARAM_ERROR:") and "api_orders" in result


def test_http_500_and_bad_shape_return_api_error(tmp_path):
    fetch_tool, _, _ = _make_tool(tmp_path, lambda request: httpx.Response(500))
    result = fetch_tool.invoke(
        {"source_id": "mock_orders", "params": {"date_range": "7d", "machines": ["M1"]}}
    )
    assert result.startswith("API_ERROR:")

    fetch_tool, _, _ = _make_tool(
        tmp_path, lambda request: httpx.Response(200, json={"not": "an array"})
    )
    result = fetch_tool.invoke(
        {"source_id": "mock_orders", "params": {"date_range": "7d", "machines": ["M1"]}}
    )
    assert result.startswith("API_ERROR:") and "shape" in result


def test_refetch_overwrites_snapshot_and_table(tmp_path):
    responses = iter([ORDER_ROWS, ORDER_ROWS[:1]])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=next(responses))

    fetch_tool, connection, workspace = _make_tool(tmp_path, handler)
    request_params = {"source_id": "mock_orders", "params": {"date_range": "7d", "machines": ["M1"]}}
    fetch_tool.invoke(request_params)
    fetch_tool.invoke(request_params)
    assert connection.execute('SELECT count(*) FROM "api_orders"').fetchone()[0] == 1
    meta = json.loads((workspace.api_dir / "api_orders.meta.json").read_text(encoding="utf-8"))
    assert meta["row_count"] == 1
```

另補：截斷測試（handler 回 `max_rows + 1` 列——用 `dataclasses.replace(API_REGISTRY["mock_orders"], max_rows=3)` 組成小 registry 傳入，斷言表內 3 列、meta `truncated=True`）；timeout 測試（handler raise `httpx.ConnectTimeout`→`API_ERROR:`）。`tests/test_events.py` 補：`assert step_title_for("fetch_api_data", {}) == "取得 API 資料"`。

- [x] **Step 2: 跑測試確認失敗**

Run: `uv run pytest -q tests/test_api_tools.py tests/test_events.py`
Expected: FAIL（ModuleNotFoundError: app.agent.tools.api_data；events 斷言 FAIL 回 "處理中"）

- [x] **Step 3: 實作 `api_data.py`＋`events.py` 一行**

依上方「執行順序」1–9 實作；結構仿 `app/agent/tools/data.py`（closure over connection/workspace、`@tool("fetch_api_data")`、`*_tool` 命名慣例）。settings 於呼叫時 `get_settings()` 取（測試 monkeypatch env 後需 `get_settings.cache_clear()`——conftest 若已有慣例照用）。`API_MOCK_BASE_URL` 為空字串時直接回 `API_ERROR: API_MOCK_BASE_URL not configured`。httpx client 每次呼叫內 `with httpx.Client(...) as client:` 用完即關（try-with-resources 慣例）。

- [x] **Step 4: 跑測試確認通過**

Run: `uv run pytest -q tests/test_api_tools.py tests/test_events.py`
Expected: PASS

- [x] **Step 5: Commit**

```bash
git add app/agent/tools/api_data.py app/agent/events.py tests/test_api_tools.py tests/test_events.py
git commit -m "feat(deepagent): fetch_api_data tool——驗參/取數/快照/鎖定連線掛表,never-raise"
```

---

### Task 4: API sources context 注入素材（`app/agent/prompts.py`）

**Files:**
- Modify: `deepagent-service/app/agent/prompts.py`
- Test: `deepagent-service/tests/test_prompts.py`（補測試）

**Interfaces:**
- Consumes: Task 1 `ApiDefinition`/`API_REGISTRY`、Task 2 `SnapshotMeta`
- Produces:
  - `build_api_sources_context(registry: dict[str, ApiDefinition], snapshots: list[SnapshotMeta]) -> str`——零目錄回 `""`；否則回附加在 system prompt 之後的區塊（前置 `"\n\n"`），只列非空段落
  - `SYSTEM_PROMPT` 追加兩段指引（questions block＋fetch_api_data，見 Step 3）

**輸出格式**（不含路徑/uuid；params 摘要 `name (required)`/`name (required, multi)`；已取數 params `k=v`、multi 值逗號串）：

```
Available API datasources (call fetch_api_data after collecting required params):
- `api_machines` — 機台清單 API; params: site (required)

Fetched API datasources (already mounted as tables; params shown for partial re-fetch):
- `api_orders` — fetched 2026-08-12T09:00:00+00:00; params: date_range=30d, machines=M1,M3
```

- [x] **Step 1: 寫失敗測試**（`tests/test_prompts.py` 補）

```python
from app.agent.prompts import build_api_sources_context
from app.engine.api_registry import API_REGISTRY
from app.engine.api_snapshot import SnapshotMeta


def _orders_snapshot() -> SnapshotMeta:
    return SnapshotMeta(
        api_id="mock_orders", alias="api_orders",
        params={"date_range": "30d", "machines": ["M1", "M3"]},
        fetched_at="2026-08-12T09:00:00+00:00",
        schema=(("order_id", "BIGINT"),), row_count=2, truncated=False,
    )


def test_api_sources_context_empty_registry_returns_empty_string():
    assert build_api_sources_context({}, []) == ""


def test_api_sources_context_all_unfetched_lists_available_only():
    context = build_api_sources_context(API_REGISTRY, [])
    assert "Available API datasources" in context
    assert "`api_orders`" in context and "`api_machines`" in context
    assert "machines (required, multi)" in context
    assert "Fetched API datasources" not in context


def test_api_sources_context_mixed_lists_both_sections():
    context = build_api_sources_context(API_REGISTRY, [_orders_snapshot()])
    assert "Available API datasources" in context and "`api_machines`" in context
    assert "Fetched API datasources" in context
    assert "date_range=30d" in context and "machines=M1,M3" in context
    assert "- `api_orders` — fetched 2026-08-12T09:00:00+00:00" in context


def test_api_sources_context_all_fetched_omits_available_section():
    snapshots = [
        _orders_snapshot(),
        SnapshotMeta(
            api_id="mock_machines", alias="api_machines", params={"site": "TP"},
            fetched_at="2026-08-12T10:00:00+00:00", schema=(), row_count=0, truncated=False,
        ),
    ]
    context = build_api_sources_context(API_REGISTRY, snapshots)
    assert "Available API datasources" not in context
```

- [x] **Step 2: 跑測試確認失敗**

Run: `uv run pytest -q tests/test_prompts.py`
Expected: FAIL（ImportError: build_api_sources_context）

- [x] **Step 3: 實作**

`build_api_sources_context` 依格式組字串（unfetched＝registry 中 alias 不在 snapshot aliases 者，依 registry 插入順序；fetched 依 alias 排序）。`SYSTEM_PROMPT` 追加兩段（保持 thin，放 "Interim findings" 條目之後）：

```
- API datasources: the system message may end with an "API datasources" context listing \
available (not yet fetched) and fetched APIs. To use an available API you MUST first collect \
every required parameter, then call fetch_api_data(source_id, params). If required parameters \
are missing and the conversation does not imply their values, ask the user by emitting a \
```questions fenced block in your reply text: a JSON array where each element is \
{"text": "...", "options": ["..."], "multiSelect": false} -- one question per missing \
parameter, all missing parameters in one block, list the parameter's options when the context \
shows them. Do not invent an "other" option; the UI adds free-form input automatically.
- Re-fetching: to change parameters (e.g. 30d -> 90d) do a partial update -- reuse the current \
params shown in the fetched list for anything the user did not mention, then call \
fetch_api_data again. "Refresh the data" means calling fetch_api_data again with the same \
params. Calling fetch_api_data always fetches; it never deduplicates.
```

- [x] **Step 4: 跑測試確認通過**

Run: `uv run pytest -q tests/test_prompts.py`
Expected: PASS

- [x] **Step 5: Commit**

```bash
git add app/agent/prompts.py tests/test_prompts.py
git commit -m "feat(deepagent): API sources context 注入素材＋system prompt 反問/取數指引"
```

---

### Task 5: manifest 擴充——API 快照進 diff 機制（`app/engine/source_manifest.py`）

**Files:**
- Modify: `deepagent-service/app/engine/source_manifest.py`（`build_manifest` 加 `api_sources` 參數）
- Test: `deepagent-service/tests/test_source_manifest.py`（補測試）

**Interfaces:**
- Consumes: 既有 `SourceRecord`（`kind` 欄位已存在）、`opaque_version_id`
- Produces: `build_manifest(connection, sources: list[tuple[str, str]], api_sources: list[tuple[str, str]] | None = None) -> dict[str, SourceRecord]`——`api_sources` 為 `(alias, fetched_at)` pairs，產出 `kind="api"`、`version_id=opaque_version_id(fetched_at)`；欄位一樣從 `information_schema.columns` 撈（API 表已掛載）。既有呼叫端不帶新參數即不變

- [x] **Step 1: 寫失敗測試**（`tests/test_source_manifest.py` 補）

```python
def test_build_manifest_includes_api_sources_with_api_kind(tmp_path):
    connection = duckdb.connect(":memory:")
    connection.execute('CREATE TABLE "api_orders" (order_id BIGINT, amount DOUBLE)')
    manifest = build_manifest(
        connection, [], api_sources=[("api_orders", "2026-08-12T09:00:00+00:00")]
    )
    record = manifest["api_orders"]
    assert record.kind == "api"
    assert record.version_id == opaque_version_id("2026-08-12T09:00:00+00:00")
    assert record.columns == (("order_id", "BIGINT"), ("amount", "DOUBLE"))


def test_api_snapshot_refetch_changes_version_id_and_diff_reports_it(tmp_path):
    connection = duckdb.connect(":memory:")
    connection.execute('CREATE TABLE "api_orders" (order_id BIGINT)')
    manifest_before = build_manifest(connection, [], api_sources=[("api_orders", "T1")])
    manifest_after = build_manifest(connection, [], api_sources=[("api_orders", "T2")])
    sources_diff = diff_manifests(manifest_before, manifest_after)
    assert sources_diff.version_changed == ("api_orders",)
```

- [x] **Step 2: 跑測試確認失敗**

Run: `uv run pytest -q tests/test_source_manifest.py`
Expected: FAIL（TypeError: unexpected keyword argument 'api_sources'）

- [x] **Step 3: 實作**

`build_manifest` 尾端在既有 dict comprehension 之後補：

```python
    for alias, fetched_at in api_sources or []:
        manifest[alias] = SourceRecord(
            alias=alias,
            kind="api",
            version_id=opaque_version_id(fetched_at),
            columns=tuple(columns_by_table.get(alias, [])),
        )
```

（既有 comprehension 需先改成具名變數 `manifest = {...}` 再 return。docstring 補一句：API 快照的版本 token 是 `meta.fetched_at`，同樣經 `opaque_version_id` 摘要。）

- [x] **Step 4: 跑測試確認通過**

Run: `uv run pytest -q tests/test_source_manifest.py`
Expected: PASS

- [x] **Step 5: Commit**

```bash
git add app/engine/source_manifest.py tests/test_source_manifest.py
git commit -m "feat(deepagent): manifest 納入 API 快照——kind=api,版本 token 用 fetchedAt"
```

---

### Task 6: chat_turn/graph 接線＋sources.md 退役

**Files:**
- Modify: `deepagent-service/app/agent/chat_turn.py`（掃 api/ 補 mount、manifest api_sources、context 傳遞、移除 `write_sources_doc` 呼叫與 import）
- Modify: `deepagent-service/app/agent/graph.py`（`build_agent` 加 `api_sources_context` 參數、tools 併入 `build_api_tools`）
- Modify: `deepagent-service/app/engine/workspace.py`（刪 `write_sources_doc` 與 `sources_doc_path`）
- Test: `deepagent-service/tests/test_workspace.py`（刪 sources.md 對應測試）、`deepagent-service/tests/test_graph.py`／`tests/test_chat.py`（補接線測試，仿既有慣例）

**Interfaces:**
- Consumes: Task 2 `scan_snapshots`/`SnapshotMeta`、Task 3 `build_api_tools`、Task 4 `build_api_sources_context`、Task 5 `build_manifest(..., api_sources=...)`、Task 1 `API_REGISTRY`
- Produces: `build_agent(model, connection, workspace, staged_skill_paths, recorder, api_sources_context: str = "") -> CompiledStateGraph`

**chat_turn `__aenter__` 改動點**（依現行行號順序）：
1. 刪 `write_sources_doc(...)` 呼叫（`self._store.prepare` 之後那行）與 import
2. `open_locked_connection` 前：`api_snapshots = scan_snapshots(self._workspace)`；mount 清單改為
   ```python
   file_sources = [
       Source(item.alias, resolve_source_path(item.path), item.fileType)
       for item in request.sources
   ]
   snapshot_sources = [
       Source(meta.alias, str(self._workspace.api_dir / f"{meta.alias}.csv"), "csv")
       for meta in api_snapshots
   ]
   self._connection = open_locked_connection(file_sources + snapshot_sources)
   ```
3. `build_manifest` 呼叫補 `api_sources=[(meta.alias, meta.fetched_at) for meta in api_snapshots]`
4. `build_agent(...)` 尾參補 `api_sources_context=build_api_sources_context(API_REGISTRY, api_snapshots)`

**graph.py 改動點**：`build_agent` 簽名加 `api_sources_context: str = ""`；`system_prompt=SYSTEM_PROMPT + api_sources_context`；`tools=build_data_tools(...) + build_api_tools(connection, workspace, API_REGISTRY)`（transport 不傳＝真 httpx，測試層不會真的呼叫——工具不被 invoke 就不打網路）。

**workspace.py**：刪 `write_sources_doc` 函式與 `SessionWorkspace.sources_doc_path` property。舊 session workspace 殘留的 sources.md 無害，不遷移。

- [x] **Step 1: 寫失敗測試**

`tests/test_graph.py` 補（仿既有 build_agent 測試的 fixture 慣例）：

```python
def test_build_agent_includes_fetch_api_data_tool_and_context_suffix(...):
    # 以既有 test_graph 的 fake model/workspace fixture 組 build_agent,
    # api_sources_context="\n\nAvailable API datasources: ..." 傳入
    # 斷言 1: agent graph 的 tools 含名為 "fetch_api_data" 的工具
    # 斷言 2: system prompt 以 SYSTEM_PROMPT + api_sources_context 組成
    #        (依 test_graph 既有的斷言方式取得 prompt;若既有測試未斷言 prompt,
    #         改斷言 load_runtime().build_agent 收到的 system_prompt kwarg——monkeypatch runtime)
```

`tests/test_chat.py` 補整合測試（仿既有 ChatTurn 測試慣例，fake model 不呼叫工具）：

```python
def test_chat_turn_mounts_api_snapshot_and_feeds_manifest(...):
    # 安排: 在 workspace api/ 先放一組 api_orders.csv + api_orders.meta.json
    #  (直接呼叫 write_snapshot 產生,fetched_at="T1")
    # 執行: ChatTurn __aenter__(用既有 fixture 的 request,sources 為空或一個上傳檔)
    # 斷言 1: self._connection 可查 "api_orders"(輪初重建走 csv reader 掛載成功)
    # 斷言 2: save_manifest 寫出的 .sources-manifest.json 含 api_orders 且 kind == "api"
    # 斷言 3: workspace root 沒有 sources.md 被寫出
```

`tests/test_workspace.py`：刪 `write_sources_doc`/`sources_doc_path` 相關測試。

- [x] **Step 2: 跑測試確認失敗**

Run: `uv run pytest -q tests/test_graph.py tests/test_chat.py`
Expected: 新增測試 FAIL（TypeError / 表不存在）

- [x] **Step 3: 實作三檔改動**

依上方改動點逐一落地。注意：`chat_turn.py` 的 import 區塊同步整理（刪 `write_sources_doc`，加 `scan_snapshots`、`build_api_sources_context`、`API_REGISTRY`）。

- [x] **Step 4: 跑該三檔測試＋全套**

Run: `uv run pytest -q`
Expected: PASS（全綠——sources.md 退役影響面以全套確認）

- [x] **Step 5: Commit**

```bash
git add app/agent/chat_turn.py app/agent/graph.py app/engine/workspace.py tests/
git commit -m "feat(deepagent): 輪初掃 api/ 補 mount＋context 注入接線;sources.md 退役"
```

---

### Task 7: dashboard skill 洞察分級規則

**Files:**
- Modify: `deepagent-service/skills/dashboard/SKILL.md`
- Test: 無新測試（`tests/test_dashboard_skill.py` 全套照跑確認未破壞）

**Interfaces:** 無程式介面——純 skill 文案。

- [x] **Step 1: 讀 `skills/dashboard/SKILL.md` 找到產出規則區**（洞察卡相關段落；若無明確區塊，加在洞察卡既有規則旁）

- [x] **Step 2: 加入兩條規則**（spec §7 原文照搬，措辭對齊該檔既有風格；若該檔為英文則譯為英文，語意不變）：

```
- 數值型洞察(最大/最小/成長率/超標計數等):MUST 以 JS 從 __ERD_RESULTS__ 現算並以模板字串
  嵌入(如 `最高為 ${maxName}(${maxValue} 件)`);NEVER 把你看到的數值直接寫死在 HTML 文字。
- 敘事型洞察:卡片 MUST 標注資料基準時間(來自注入資料的 fetchedAt 或上傳時間);讓日後資料
  刷新時能以時間戳比對判斷洞察是否過期。
```

- [x] **Step 3: 跑 skill 相關測試**

Run: `uv run pytest -q tests/test_dashboard_skill.py`
Expected: PASS

- [x] **Step 4: Commit**

```bash
git add skills/dashboard/SKILL.md
git commit -m "docs(deepagent): dashboard skill 洞察分級——數值 JS 現算/敘事標基準時間"
```

---

### Task 8: mock API server 腳本＋最終驗證

**Files:**
- Create: `deepagent-service/scripts/mock_api_server.py`
- Test: 全套 `uv run pytest -q`＋`uv run ruff check .`

**Interfaces:** 獨立小 FastAPI（不 import `app/` 任何模組）；`uv run python scripts/mock_api_server.py` 起在 :9100。

- [x] **Step 1: 寫腳本**

```python
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
    uvicorn.run(app, host="127.0.0.1", port=9100)
```

（注意 `order_date` 用參數推導的固定字串——腳本頂層不可用 `datetime.now()` 之類會讓同參數輸出漂移的來源，手動驗證要可重現。ruff 對 `scripts/` 的規則若擋 `random`，加 per-file ignore 而非改用 secrets。）

- [x] **Step 2: 手動 smoke**

Run: `uv run python scripts/mock_api_server.py &`，`curl "http://localhost:9100/orders?date_range=7d&machines=M1,M3" | head -c 300`，然後 kill。
Expected: JSON 陣列，含 order_id/machine/quantity 欄位。

- [x] **Step 3: 最終全套驗證**

Run: `uv run pytest -q && uv run ruff check .`
Expected: 全綠、ruff clean。

- [x] **Step 4: Commit**

```bash
git add scripts/mock_api_server.py
git commit -m "chore(deepagent): 本機手動驗證用 mock API server(:9100)"
```

---

## 驗收清單（對齊 spec §13；自動化部分由 Task 1–8 覆蓋，手動劇本於 opus 終審前執行）

- [x] `uv run pytest -q` 全綠＋`uv run ruff check .` clean（263 passed）
- [ ] 手動兩輪劇本（mock server＋backend local profile＋前端）：
  - [ ] 首輪問「訂單趨勢」→ 前端出現 QuestionCards（date_range 單選＋machines 多選）
  - [ ] 作答 → 模型 fetch → STEP 顯示「取得 API 資料」→ dashboard 產出，數字來自 mock 資料
  - [ ] 第三輪「換成 90 天」→ 部分更新參數重 fetch → 快照覆寫 → dashboard 更新
  - [ ] 重啟 deepagent 後同 session 再問 → 掃 `api/` 補 mount 成功，模型看得到 `api_orders`（不重問參數）
- [ ] 快照替換後下一輪 manifest note 出現於模型訊息（Task 5/6 測試層已驗）
- [ ] API sources context 內容正確（Task 4 測試已驗）；`sources.md`/`write_sources_doc` 已移除（Task 6）
- [ ] 洞察規則進 skill（Task 7）；抽查一輪產出的洞察卡數字是 JS 現算

## 明確不做（對齊 spec §11/§12.10）

replay 端點、動態候選值（optionsSource）、認證 header、parquet、巢狀/文件型回應抽取（`response_format` 保留字已留）、Java/前端任何改動。
