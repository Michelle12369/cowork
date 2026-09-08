# MCP dashboard 接上 connector 自動落表 — 實作計畫

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `feat/mcp-dashboard-merge-datasource`（已含 `origin/feat/mcp-datasource` `bcb61f3` 的 merge commit）上, 把 dashboard 側的 `check_dashboard`, skill gate root, SKILL.md 與 spike 依 spec 定案重新接上 datasource 的自動落表管線, 讓 `uv run ruff check .` 與 `uv run pytest` 回到全綠.

**Architecture:** merge commit 已把三個衝突檔全取 datasource 側, 因此現況是 `check.py` import 已刪除的 `replay_manifest`, `chat_turn` 不再註冊 `check_dashboard` 也不傳 `dashboard_skill_root`. 本計畫用一個 stdlib-only 的 `ConnectorCallLog`（workspace 頂層 `connector_calls.jsonl`, append-only, 跨輪）取代 replay manifest 作為 `check_dashboard` 的事實來源; `unwrap_envelope` 把走過的拆封路徑記下來, wrapper 用它在回饋文字裡明講「raw 回傳值長什麼樣, 表是從哪一層落的」, `check_dashboard` 用同一份路徑驗 handler 讀對層. prompt 與 SKILL.md 改講法, 讓「qN 只供對話, dashboard 走 `mcp()`」與「本 session = 紀錄所及任一輪」兩件事在 prompt, skill, 工具三處講的一致.

**Tech Stack:** Python 3.11, LangChain/LangGraph（agent 層）, DuckDB, stdlib `json`/`pathlib`（engine 層）, pytest, ruff.

**Spec:** `docs/superpowers/specs/2026-09-08-mcp-dashboard-on-autoland-design.md`（決策 D0, D5–D8, D1–D4 已定案; D9 傳輸面, D10, D11 不在本計畫）. 相關: `docs/superpowers/specs/2026-08-30-mcp-datasource-design.md`, `docs/superpowers/plans/2026-09-06-connector-autoland-ephemeral.md`（datasource 側的行為定義, 本計畫不推翻）.

## Global Constraints

- 分支: `feat/mcp-dashboard-merge-datasource`. 基底 merge commit 已落地, NEVER rebase, NEVER force-push. 每個 Task 結尾各自 commit; push 由使用者決定.
- 只動 `deepagent-service/`（含 `skills/`, `spike/`）與 `docs/superpowers/`. Java 與前端零改動.
- engine 層（`app/engine/`）只用 stdlib + duckdb, 禁止 import LLM 框架（ruff TID251 會擋）.
- `duck.py`, `mcp_adapter.py`, skill staging, call budget, bearer token, `connection_lock` 共用管線, `inject_results` 對 connector 模式的呼叫: 不動.
- `mcp()` 既有頁面契約（簽名, handler 一次, `{data}`／`{error:{message}}`, 禁止 API, CDN 白名單, `'erd'` theme）: 不動. `r.error.code` 等 D9 傳輸面提案不進本計畫.
- 變數／參數／lambda 參數 NEVER 用 1–2 字元名稱（`id` 等 domain 語彙除外）; 迴圈計數器用 `index`/`rowIndex` 等描述性名稱.
- 註解: 1–2 行寫目的＋做法; NEVER 寫 spec 編號, commit hash, 事故敘事. 訊息語言: raise/log 英文; 模型面文字（tool 回饋, prompt, SKILL.md）英文; 使用者面文案中文.
- `check_dashboard` NEVER 產生模型無法用任何行動消除的 finding: 事實來源不可用時讓路（跳過該檢查並說明原因）, 不退件.
- 所有 tool 維持 never-raise: 呼叫紀錄的 append/load 失敗只 `logger.warning`, 不影響 tool 回傳.
- 完成條件: 在 `deepagent-service/` 下 `uv run ruff check .` 乾淨 ＋ `uv run pytest -q` 全綠（spike 的 `DTZ011` 是 merge 前既有, 在 Task 8 一併修掉）.
- 測試命名: `test_<subject>_<condition>_<expected>`; 斷言元素級行為, 不做整段字串快照.

## 檔案結構（本計畫新增／修改的檔案與各自責任）

| 檔案 | 動作 | 責任 |
|---|---|---|
| `app/engine/connector_call_log.py` | 新增 | 呼叫紀錄的讀寫: 一行一筆 JSON, 損毀行跳過, 本輪記憶體鏡像, 降級旗標 |
| `app/engine/api_snapshot.py` | 修改 | `unwrap_envelope` 多回傳 `unwrap_path`; `LandingResult` 多 `unwrap_path`; `EmptyLandingError` 帶 `unwrap_path`/`envelope_fields` |
| `app/agent/connectors/wrapper.py` | 修改 | 接 `call_log`; 回饋多 `Raw response shape` 段; 成功與 0 列各 append 一筆 |
| `app/agent/tools/check.py` | 修改 | 事實來源改 `ConnectorCallLog`; keys 與 unwrap-path 兩條 lint; `call_log=None`／降級時跳過並說明 |
| `app/agent/chat_turn.py` | 修改 | 建 `ConnectorCallLog`, 傳給 wrapper 與 check tool; connector 模式註冊 `check_dashboard`; 傳 `dashboard_skill_root` |
| `app/agent/prompts.py` | 修改 | 兩段 connector prompt 改講法 |
| `skills/mcp-data-dashboard/SKILL.md` | 修改 | Workflow 第 1 步, 鐵律第 2 條, `r.data` 形狀, Reading the response 三種範例 |
| `spike/mcp-shell/bridge.py`, `README.md`, `mock_server.py` | 修改 | 拿掉 `UNWRAP_RESULT`; README 契約段指向 spec D9; 修 `DTZ011` |
| `docs/superpowers/specs/2026-09-04-mcp-dashboard-verification-options.md` | 修改 | level 2 表格的事實來源改 `connector_calls.jsonl` |
| `docs/superpowers/specs/2026-09-08-mcp-dashboard-on-autoland-design.md` | 修改 | 狀態列改「merge 已執行」 |
| `tests/test_connector_call_log.py` | 新增 | Task 1 |
| `tests/test_api_snapshot.py`, `tests/test_connector_wrapper.py`, `tests/test_check_dashboard.py`, `tests/test_chat_turn_connectors.py`, `tests/test_graph.py`, `tests/test_prompts.py` | 修改 | 各 Task |

---

### Task 1: `ConnectorCallLog` — 呼叫紀錄的讀寫物件

**Files:**
- Create: `deepagent-service/app/engine/connector_call_log.py`
- Test: `deepagent-service/tests/test_connector_call_log.py`

**Interfaces:**
- Consumes: 無（stdlib only）.
- Produces:
  - `class ConnectorCallLog`: `__init__(self, path: Path)`; `append(self, record: dict[str, Any]) -> None`; `load(self) -> list[dict[str, Any]]`; property `degraded: bool`.
  - 常數 `CONNECTOR_CALL_LOG_FILENAME = "connector_calls.jsonl"`.
  - 紀錄形狀（dict, 由 Task 3 的 wrapper 產生, Task 4 的 check 讀取）:
    `{"connector_id": str, "tool_name": str, "args": dict, "unwrap_path": list[str] | None, "envelope_keys": list[str], "columns": list[str], "row_count": int, "landed": bool}`

- [ ] **Step 1: 寫失敗的測試**

```python
# deepagent-service/tests/test_connector_call_log.py
"""`ConnectorCallLog`(app.engine.connector_call_log)——一行一筆 JSON 的 append/load 往返、
損毀行跳過、檔案不存在回空、本輪記憶體鏡像(磁碟寫失敗仍讀得到)、降級旗標。"""

import json
import logging
from pathlib import Path

import pytest

from app.engine.connector_call_log import CONNECTOR_CALL_LOG_FILENAME, ConnectorCallLog


def _record(**overrides) -> dict:
    record = {
        "connector_id": "sales",
        "tool_name": "list_orders",
        "args": {"days": 30},
        "unwrap_path": ["result"],
        "envelope_keys": [],
        "columns": ["order_id", "amount"],
        "row_count": 412,
        "landed": True,
    }
    record.update(overrides)
    return record


def test_filename_constant_is_connector_calls_jsonl() -> None:
    assert CONNECTOR_CALL_LOG_FILENAME == "connector_calls.jsonl"


def test_load_missing_file_returns_empty_list_and_not_degraded(tmp_path) -> None:
    call_log = ConnectorCallLog(tmp_path / CONNECTOR_CALL_LOG_FILENAME)

    assert call_log.load() == []
    assert call_log.degraded is False


def test_append_then_load_round_trips_one_record_per_line(tmp_path) -> None:
    path = tmp_path / CONNECTOR_CALL_LOG_FILENAME
    call_log = ConnectorCallLog(path)

    call_log.append(_record())
    call_log.append(_record(args={"days": 7}, row_count=9))

    assert call_log.load() == [_record(), _record(args={"days": 7}, row_count=9)]
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["args"] == {"days": 30}


def test_load_reads_records_written_by_a_previous_instance(tmp_path) -> None:
    """跨輪: 上一輪的物件寫的檔, 本輪新物件讀得到."""
    path = tmp_path / CONNECTOR_CALL_LOG_FILENAME
    ConnectorCallLog(path).append(_record())

    assert ConnectorCallLog(path).load() == [_record()]


def test_load_skips_corrupt_lines_and_keeps_the_rest(tmp_path, caplog) -> None:
    path = tmp_path / CONNECTOR_CALL_LOG_FILENAME
    path.write_text(
        json.dumps(_record()) + "\n" + "{not json\n" + "\n" + json.dumps(_record(row_count=1)) + "\n",
        encoding="utf-8",
    )

    with caplog.at_level(logging.WARNING):
        records = ConnectorCallLog(path).load()

    assert records == [_record(), _record(row_count=1)]
    assert "corrupt" in caplog.text


def test_append_write_failure_keeps_record_in_memory_and_sets_degraded(
    tmp_path, monkeypatch, caplog
) -> None:
    """磁碟寫失敗: 不拋, 本輪記憶體鏡像仍讓 load() 看得到這筆, degraded 轉 True."""
    path = tmp_path / CONNECTOR_CALL_LOG_FILENAME
    call_log = ConnectorCallLog(path)

    def _fail_open(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "open", _fail_open)
    with caplog.at_level(logging.WARNING):
        call_log.append(_record())

    assert call_log.degraded is True
    assert "append failed" in caplog.text
    monkeypatch.undo()
    assert call_log.load() == [_record()]
    assert not path.exists()


def test_load_read_failure_returns_memory_records_and_sets_degraded(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / CONNECTOR_CALL_LOG_FILENAME
    call_log = ConnectorCallLog(path)
    call_log.append(_record())
    original_read_text = Path.read_text

    def _fail_read(self, *args, **kwargs):
        if self == path:
            raise OSError("permission denied")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _fail_read)

    assert call_log.load() == []
    assert call_log.degraded is True


def test_append_creates_parent_directory(tmp_path) -> None:
    path = tmp_path / "nested" / CONNECTOR_CALL_LOG_FILENAME
    ConnectorCallLog(path).append(_record())

    assert path.exists()


@pytest.mark.parametrize("bad_record", [None, [], "text"])
def test_append_non_dict_record_raises_type_error(tmp_path, bad_record) -> None:
    with pytest.raises(TypeError):
        ConnectorCallLog(tmp_path / CONNECTOR_CALL_LOG_FILENAME).append(bad_record)
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd deepagent-service && uv run pytest tests/test_connector_call_log.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'app.engine.connector_call_log'`

- [ ] **Step 3: 實作**

```python
# deepagent-service/app/engine/connector_call_log.py
"""connector 呼叫紀錄: 一行一筆 JSON, append-only, 放在 workspace 頂層隨 session 跨輪保留.
只記 metadata (connector, tool, 參數, 拆封路徑, 欄位名, 列數), 不記資料列. engine 層只用 stdlib."""

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CONNECTOR_CALL_LOG_FILENAME = "connector_calls.jsonl"


class ConnectorCallLog:
    """一個 ChatTurn 一個實例. 磁碟寫失敗的紀錄留在記憶體讓同一輪的 load() 仍看得到,
    任何一次 append/load 例外都把 degraded 設成 True, 讓讀端決定要不要跳過依賴紀錄的檢查."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._unwritten_records: list[dict[str, Any]] = []
        self._degraded = False

    @property
    def path(self) -> Path:
        return self._path

    @property
    def degraded(self) -> bool:
        return self._degraded

    def append(self, record: dict[str, Any]) -> None:
        """寫一行. 失敗只 warning 並把這筆留在記憶體, 不往外拋."""
        if not isinstance(record, dict):
            raise TypeError(f"call log record must be a dict, got {type(record).__name__}")
        line = json.dumps(record, ensure_ascii=False, sort_keys=True)
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except Exception as error:  # noqa: BLE001 -- never-raise: recording must not break the tool
            self._degraded = True
            self._unwritten_records.append(record)
            logger.warning(
                "connector call log append failed: path=%s error=%s",
                self._path,
                type(error).__name__,
            )

    def load(self) -> list[dict[str, Any]]:
        """磁碟上的全部紀錄 (跨輪) 加上本輪寫失敗的紀錄. 損毀行跳過並 warning."""
        disk_records: list[dict[str, Any]] = []
        if self._path.exists():
            try:
                raw_text = self._path.read_text(encoding="utf-8")
            except Exception as error:  # noqa: BLE001 -- never-raise, degrade instead
                self._degraded = True
                logger.warning(
                    "connector call log read failed: path=%s error=%s",
                    self._path,
                    type(error).__name__,
                )
                raw_text = ""
            for line_number, line in enumerate(raw_text.splitlines(), start=1):
                if not line.strip():
                    continue
                try:
                    parsed = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning(
                        "connector call log corrupt line skipped: path=%s line=%d",
                        self._path,
                        line_number,
                    )
                    continue
                if isinstance(parsed, dict):
                    disk_records.append(parsed)
        return [*disk_records, *self._unwritten_records]
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd deepagent-service && uv run pytest tests/test_connector_call_log.py -q && uv run ruff check app/engine/connector_call_log.py tests/test_connector_call_log.py`
Expected: 10 passed; ruff 乾淨.

- [ ] **Step 5: Commit**

```bash
git add deepagent-service/app/engine/connector_call_log.py deepagent-service/tests/test_connector_call_log.py
git commit -m "feat(deepagent): ConnectorCallLog——connector 呼叫紀錄 append/load, 損毀行跳過, 本輪記憶體鏡像與降級旗標"
```

---

### Task 2: `unwrap_envelope` 記下拆封路徑

**Files:**
- Modify: `deepagent-service/app/engine/api_snapshot.py`
- Test: `deepagent-service/tests/test_api_snapshot.py`

**Interfaces:**
- Consumes: 無.
- Produces:
  - `unwrap_envelope(payload) -> tuple[Any, dict[str, Any], list[str] | None]`: 第三元 `unwrap_path` — list 原樣時 `[]`; 拆過的 key 依序; 非信封 dict（整包落成一列）時 `None`.
  - `LandingResult` 新增欄位 `unwrap_path: list[str] | None`（`envelope_fields` 保留; 讀端用 `list(envelope_fields)` 得 `envelope_keys`）.
  - `EmptyLandingError` 新增屬性 `unwrap_path: list[str] | None`, `envelope_fields: dict[str, Any]`; 建構子 `__init__(self, table_name, unwrap_path=None, envelope_fields=None)`.

- [ ] **Step 1: 改測試（既有 7 條 unwrap 測試改三元組, 並補 D5 表格五列）**

把 `tests/test_api_snapshot.py` 既有的 `unwrap_envelope` 測試（第 29–90 行, 7 條）的 `data, envelope_fields = unwrap_envelope(...)` 全改成 `data, envelope_fields, unwrap_path = unwrap_envelope(...)`, 各補一行 `unwrap_path` 斷言:

| 測試 | 期望 `unwrap_path` |
|---|---|
| `..._plain_list_passes_through...` | `[]` |
| `..._dict_with_data_list_splits_out...` | `["data"]` |
| `..._non_envelope_shape_passes_through...` | `None` |
| `..._fastmcp_result_wrapper_around_list...` | `["result"]` |
| `..._fastmcp_result_wrapper_around_data_envelope...` | `["result", "data"]` |
| `..._fastmcp_result_wrapper_around_scalar_stays_single_row` | `None` |
| `..._dict_with_result_and_other_keys_is_not_treated_as_wrapper` | `None` |

再新增:

```python
def test_unwrap_envelope_path_table_matches_documented_shapes() -> None:
    """五種 raw 形狀各自回正確的 unwrap_path 與 envelope keys."""
    cases = [
        ([{"a": 1}], [], []),
        ({"data": [{"a": 1}], "errorCode": ""}, ["data"], ["errorCode"]),
        ({"result": [{"a": 1}]}, ["result"], []),
        ({"result": {"data": [{"a": 1}], "total": 9}}, ["result", "data"], ["total"]),
        ({"fab": "A", "yield": 0.97}, None, []),
    ]
    for payload, expected_path, expected_envelope_keys in cases:
        _data, envelope_fields, unwrap_path = unwrap_envelope(payload)
        assert unwrap_path == expected_path, payload
        assert list(envelope_fields) == expected_envelope_keys, payload


def test_land_response_result_carries_unwrap_path(tmp_path, connection, connection_lock) -> None:
    landing_result = land_response(
        connection, connection_lock, tmp_path, "wrapped", {"result": [{"a": 1}, {"a": 2}]}
    )

    assert landing_result.unwrap_path == ["result"]
    assert landing_result.envelope_fields == {}


def test_land_response_empty_data_error_carries_unwrap_path_and_envelope(
    tmp_path, connection, connection_lock
) -> None:
    with pytest.raises(EmptyLandingError) as error_info:
        land_response(
            connection, connection_lock, tmp_path, "empty", {"data": [], "errorCode": "E1"}
        )

    assert error_info.value.unwrap_path == ["data"]
    assert error_info.value.envelope_fields == {"errorCode": "E1"}
```

（`land_response` 的既有測試中若有直接建構 `LandingResult` 或比對整個 dataclass 的, 補 `unwrap_path`; 第 92–260 行的 `land_response` 測試只斷言欄位, 預期不需改.）

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd deepagent-service && uv run pytest tests/test_api_snapshot.py -q`
Expected: FAIL — `ValueError: too many values to unpack` 或 `AttributeError: 'LandingResult' object has no attribute 'unwrap_path'`.

- [ ] **Step 3: 實作**

`app/engine/api_snapshot.py` 改動:

```python
class EmptyLandingError(Exception):
    """payload 拆封後是 0 列時拋出, 因為 DuckDB 的 read_json_auto 推不出 schema, 落表前先擋下.
    帶著拆封路徑與信封欄位, 讓呼叫端仍能把這次成功但無資料的呼叫記進紀錄."""

    def __init__(
        self,
        table_name: str,
        unwrap_path: list[str] | None = None,
        envelope_fields: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            f"cannot land empty response as table {table_name!r}: payload has no rows, so "
            "DuckDB read_json_auto has no schema to infer -- retry with different call "
            "arguments that return at least one row before landing"
        )
        self.unwrap_path = unwrap_path
        self.envelope_fields = envelope_fields or {}


@dataclass(frozen=True)
class LandingResult:
    table_name: str
    columns: list[str]
    row_count: int
    preview_rows: list[list]
    envelope_fields: dict[str, Any]
    unwrap_path: list[str] | None


def unwrap_envelope(payload: Any) -> tuple[Any, dict[str, Any], list[str] | None]:
    """list 直接回傳; dict 有 data (list, null 或空 dict) 就回 (data, 其餘頂層欄位); FastMCP 把非 dict
    回傳值包成 {"result": ...}, 只有這一個 key 且內容是 list, dict, null 或空字串時先拆開再套同樣規則;
    其他形狀原樣落表. 第三元是走過的 key 路徑: list 為 [], 非信封 dict 為 None."""
    if isinstance(payload, list):
        return payload, {}, []
    if isinstance(payload, dict) and "data" in payload:
        data = payload["data"]
        if data is None or data == {} or isinstance(data, list):
            envelope_fields = {key: value for key, value in payload.items() if key != "data"}
            return data, envelope_fields, ["data"]
    if isinstance(payload, dict) and set(payload) == {"result"}:
        inner = payload["result"]
        # 拆開的 result 是 null 或空字串: 仍算「拆過 result」, 讓呼叫端判成 0 列而不是落成一列.
        if inner is None or inner == "":
            return inner, {}, ["result"]
        if isinstance(inner, list | dict):
            inner_data, inner_envelope, inner_path = unwrap_envelope(inner)
            if inner_path is None:
                return payload, {}, None
            return inner_data, inner_envelope, ["result", *inner_path]
    return payload, {}, None
```

行為與 merge 前逐案相同（`{"result": None}` 與 `{"result": ""}` 仍走 EmptyLandingError; `{"result": {"fab": "A"}}` 非信封 dict 仍整包落成一列）; 既有測試 `test_land_response_empty_dict_shapes_raise_and_write_no_file` 與 `..._scalar_only_dict_still_lands_as_single_row` 守住這兩點.

`land_response`:

```python
    data, envelope_fields, unwrap_path = unwrap_envelope(payload)
    if _is_empty_payload(data):
        raise EmptyLandingError(table_name, unwrap_path, envelope_fields)
    ...
    return LandingResult(
        table_name=table_name,
        columns=columns,
        row_count=row_count,
        preview_rows=preview_rows,
        envelope_fields=envelope_fields,
        unwrap_path=unwrap_path,
    )
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd deepagent-service && uv run pytest tests/test_api_snapshot.py -q && uv run ruff check app/engine/api_snapshot.py tests/test_api_snapshot.py`
Expected: 全部 passed; ruff 乾淨. 另跑 `uv run pytest tests/test_connector_wrapper.py -q` 確認 wrapper 尚未改也仍全綠（wrapper 只呼叫 `land_response`, 不直接解包）.

- [ ] **Step 5: Commit**

```bash
git add deepagent-service/app/engine/api_snapshot.py deepagent-service/tests/test_api_snapshot.py
git commit -m "feat(deepagent): unwrap_envelope 回傳拆封路徑——LandingResult 與 EmptyLandingError 帶 unwrap_path"
```

---

### Task 3: wrapper 接呼叫紀錄, 回饋文字明講 raw 形狀

**Files:**
- Modify: `deepagent-service/app/agent/connectors/wrapper.py`
- Test: `deepagent-service/tests/test_connector_wrapper.py`

**Interfaces:**
- Consumes: Task 1 `ConnectorCallLog.append`; Task 2 `LandingResult.unwrap_path`, `EmptyLandingError.unwrap_path/envelope_fields`.
- Produces:
  - `build_connector_tools(connectors, connection, connection_lock, landing_dir, *, call_budget=50, call_log: ConnectorCallLog | None = None)`.
  - 回饋文字新增 `Raw response shape: ...` 段（緊接在 `Landed table ...` 那行之後, `Other response fields` 之前）.
  - 紀錄形狀見 Task 1 Interfaces.
  - 公開 helper `describe_raw_response_shape(response, unwrap_path, envelope_fields, row_count) -> str`（Task 4 的測試與 spike 不用, 但 SKILL.md 引用其輸出句型）.

- [ ] **Step 1: 寫失敗的測試**

在 `tests/test_connector_wrapper.py` 補 import 與測試:

```python
import json

from app.engine.connector_call_log import ConnectorCallLog


def _single_tool_connector(connector_id: str, tool_name: str, response) -> Connector:
    return Connector(
        connector_id=connector_id,
        display_name=connector_id.title(),
        tools=(
            ConnectorTool(
                name=tool_name,
                description="fixture tool",
                input_schema={
                    "type": "object",
                    "properties": {"days": {"type": "integer"}},
                    "required": [],
                },
                call=lambda args: response,
            ),
        ),
        skills={},
    )


def test_feedback_fastmcp_result_wrapper_tells_model_to_read_r_data_result(
    tmp_path, connection, connection_lock
) -> None:
    connector = _single_tool_connector("sales", "list_orders", {"result": [{"a": 1}, {"a": 2}]})
    tools = _tools_by_name((connector,), connection, connection_lock, tmp_path)

    result = tools["sales_list_orders"].invoke({"days": 30})

    assert "Raw response shape: object with keys [result]." in result
    assert "The table was built from response.result (an array of 2 objects)" in result
    assert "read the rows with `r.data.result` -- not `r.data`" in result


def test_feedback_plain_array_says_r_data_is_already_the_array(
    tmp_path, connection, connection_lock
) -> None:
    connector = _single_tool_connector("sales", "list_orders", [{"a": 1}])
    tools = _tools_by_name((connector,), connection, connection_lock, tmp_path)

    result = tools["sales_list_orders"].invoke({})

    assert "Raw response shape: array of 1 objects." in result
    assert "r.data is already the array" in result


def test_feedback_data_envelope_names_other_fields_location(
    tmp_path, connection, connection_lock
) -> None:
    connector = _single_tool_connector(
        "sales", "list_orders", {"data": [{"a": 1}], "errorCode": ""}
    )
    tools = _tools_by_name((connector,), connection, connection_lock, tmp_path)

    result = tools["sales_list_orders"].invoke({})

    assert "Raw response shape: object with keys [data, errorCode]." in result
    assert "read the rows with `r.data.data` -- not `r.data`" in result
    assert (
        "Other top-level fields (errorCode) were not landed; in the dashboard they are at "
        "r.data.errorCode" in result
    )


def test_feedback_non_envelope_dict_says_read_fields_directly(
    tmp_path, connection, connection_lock
) -> None:
    connector = _single_tool_connector("sales", "summary", {"fab": "A", "yield": 0.97})
    tools = _tools_by_name((connector,), connection, connection_lock, tmp_path)

    result = tools["sales_summary"].invoke({})

    assert "Raw response shape: object with keys [fab, yield]; landed as a single row." in result
    assert "read fields directly (r.data.fab)" in result


def test_successful_landing_appends_call_record_with_unwrap_path(
    tmp_path, connection, connection_lock
) -> None:
    call_log = ConnectorCallLog(tmp_path / "connector_calls.jsonl")
    connector = _single_tool_connector("sales", "list_orders", {"result": [{"a": 1}, {"a": 2}]})
    tools = _tools_by_name(
        (connector,), connection, connection_lock, tmp_path, call_log=call_log
    )

    tools["sales_list_orders"].invoke({"days": 30})

    assert call_log.load() == [
        {
            "connector_id": "sales",
            "tool_name": "list_orders",
            "args": {"days": 30},
            "unwrap_path": ["result"],
            "envelope_keys": [],
            "columns": ["a"],
            "row_count": 2,
            "landed": True,
        }
    ]


def test_empty_response_appends_call_record_with_landed_false(
    tmp_path, connection, connection_lock
) -> None:
    call_log = ConnectorCallLog(tmp_path / "connector_calls.jsonl")
    connector = _single_tool_connector("sales", "list_orders", {"data": [], "errorCode": "E1"})
    tools = _tools_by_name(
        (connector,), connection, connection_lock, tmp_path, call_log=call_log
    )

    result = tools["sales_list_orders"].invoke({"days": 30})

    assert "cannot land empty response" in result
    [record] = call_log.load()
    assert record["landed"] is False
    assert record["columns"] == []
    assert record["row_count"] == 0
    assert record["unwrap_path"] == ["data"]
    assert record["envelope_keys"] == ["errorCode"]


def test_connector_tool_error_does_not_append_call_record(
    tmp_path, connection, connection_lock
) -> None:
    from app.agent.connectors.model import ConnectorToolError

    def _raise(args):
        raise ConnectorToolError("tool rejected the arguments")

    connector = Connector(
        connector_id="sales",
        display_name="Sales",
        tools=(
            ConnectorTool(
                name="list_orders",
                description="fixture",
                input_schema={"type": "object", "properties": {}, "required": []},
                call=_raise,
            ),
        ),
        skills={},
    )
    call_log = ConnectorCallLog(tmp_path / "connector_calls.jsonl")
    tools = _tools_by_name(
        (connector,), connection, connection_lock, tmp_path, call_log=call_log
    )

    tools["sales_list_orders"].invoke({})

    assert call_log.load() == []


def test_call_log_none_writes_nothing_and_still_lands(tmp_path, connection, connection_lock) -> None:
    tools = _tools_by_name((demo_connector(),), connection, connection_lock, tmp_path)

    result = tools["demo_quality_list_fabs"].invoke({})

    assert result.startswith("Landed table")
    assert not (tmp_path / "connector_calls.jsonl").exists()


def test_call_log_append_failure_does_not_change_tool_result(
    tmp_path, connection, connection_lock, monkeypatch
) -> None:
    call_log = ConnectorCallLog(tmp_path / "connector_calls.jsonl")

    def _boom(record):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(call_log, "append", _boom)
    tools = _tools_by_name(
        (demo_connector(),), connection, connection_lock, tmp_path, call_log=call_log
    )

    result = tools["demo_quality_list_fabs"].invoke({})

    assert result.startswith("Landed table")
```

`_tools_by_name` 已接受 `**kwargs`, 直接透傳 `call_log`.

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd deepagent-service && uv run pytest tests/test_connector_wrapper.py -q`
Expected: 新測試 FAIL（`TypeError: build_connector_tools() got an unexpected keyword argument 'call_log'` 與 `Raw response shape` 缺席）; 既有測試仍 PASS.

- [ ] **Step 3: 實作**

`app/agent/connectors/wrapper.py`:

```python
from app.engine.api_snapshot import (
    LANDING_PREVIEW_MAX_ROWS,
    EmptyLandingError,
    LandingResult,
    land_response,
)
from app.engine.connector_call_log import ConnectorCallLog


def _dotted(path: list[str]) -> str:
    return ".".join(path)


def describe_raw_response_shape(
    response: Any,
    unwrap_path: list[str] | None,
    envelope_fields: dict[str, Any],
    row_count: int,
) -> str:
    """給模型看的一段英文: raw 回傳值長什麼樣, 表是從哪一層落的, 在 dashboard 的 handler 裡該讀哪個路徑."""
    if isinstance(response, list):
        return (
            f"Raw response shape: array of {row_count} objects. In the dashboard, mcp() hands "
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
        f"Raw response shape: object with keys [{top_level_keys}]. The table was built from "
        f"response.{rows_path} (an array of {row_count} objects)"
        + ("; nothing else was dropped." if not envelope_fields else "."),
        "In the dashboard, mcp() hands your handler the raw response as r.data, so read the "
        f"rows with `r.data.{rows_path}` -- not `r.data`.",
    ]
    if envelope_fields:
        envelope_prefix = _dotted(["r.data", *unwrap_path[:-1]])
        field_names = ", ".join(envelope_fields)
        located = ", ".join(f"{envelope_prefix}.{name}" for name in envelope_fields)
        lines.append(
            f"Other top-level fields ({field_names}) were not landed; in the dashboard they "
            f"are at {located}."
        )
    return "\n".join(lines)
```

`_format_landing_feedback` 多接 `response`, 在 `landing_summary` 之後插入:

```python
    lines = [landing_summary]
    lines.append(
        describe_raw_response_shape(
            response, landing_result.unwrap_path, landing_result.envelope_fields, landing_result.row_count
        )
    )
    if landing_result.envelope_fields:
        ...  # 既有 Other response fields 段保留
```

`_build_tool` 多接 `call_log: ConnectorCallLog | None`, `_execute` 兩處 append:

```python
    def _record_call(
        args: dict[str, Any],
        unwrap_path: list[str] | None,
        envelope_fields: dict[str, Any],
        columns: list[str],
        row_count: int,
        landed: bool,
    ) -> None:
        if call_log is None:
            return
        try:
            call_log.append(
                {
                    "connector_id": connector.connector_id,
                    "tool_name": connector_tool.name,
                    "args": args,
                    "unwrap_path": unwrap_path,
                    "envelope_keys": list(envelope_fields),
                    "columns": columns,
                    "row_count": row_count,
                    "landed": landed,
                }
            )
        except Exception as error:  # noqa: BLE001 -- recording is best-effort, never masks the call
            logger.warning(
                "connector call record failed: connector=%s tool=%s error=%s",
                connector.connector_id,
                connector_tool.name,
                type(error).__name__,
            )
```

在 `_execute` 裡:

```python
        try:
            landing_result = land_response(
                connection, connection_lock, landing_dir, table_name, response
            )
        except EmptyLandingError as error:
            _record_call(args, error.unwrap_path, error.envelope_fields, [], 0, landed=False)
            return str(error)
        except ValueError as error:
            # table_name 沒通過驗證, 訊息本身已可行動, 原樣回傳.
            return str(error)
        except Exception as error:
            ...  # 既有 landing failed 分支不動

        _record_call(
            args,
            landing_result.unwrap_path,
            landing_result.envelope_fields,
            landing_result.columns,
            landing_result.row_count,
            landed=True,
        )
        return _format_landing_feedback(
            connector.connector_id, connector_tool.name, args, landing_result, response
        )
```

`build_connector_tools` 簽名加 `call_log: ConnectorCallLog | None = None`, 傳進 `_build_tool`. `_build_tool` docstring 補一句「成功與 0 列各記一筆呼叫紀錄; tool 錯誤與傳輸失敗不記」.

既有測試 `test_call_auto_lands_table_and_feedback_has_expected_shape` 斷言 `Other response fields: errorCode=` 仍成立（段落順序: Landed table → Raw response shape → Other response fields → Preview）.

- [ ] **Step 4: 跑測試確認通過**

Run: `cd deepagent-service && uv run pytest tests/test_connector_wrapper.py tests/test_api_snapshot.py -q && uv run ruff check app/agent/connectors/wrapper.py tests/test_connector_wrapper.py`
Expected: 全部 passed; ruff 乾淨.

- [ ] **Step 5: Commit**

```bash
git add deepagent-service/app/agent/connectors/wrapper.py deepagent-service/tests/test_connector_wrapper.py
git commit -m "feat(deepagent): connector wrapper 記呼叫紀錄, 回饋明講 Raw response shape 與 r.data 讀列路徑"
```

---

### Task 4: `check_dashboard` 改讀 `ConnectorCallLog`, 加 unwrap-path lint

**Files:**
- Modify: `deepagent-service/app/agent/tools/check.py`
- Test: `deepagent-service/tests/test_check_dashboard.py`

**Interfaces:**
- Consumes: Task 1 `ConnectorCallLog.load()`, `.degraded`; 紀錄形狀.
- Produces: `build_check_tools(workspace, connectors, call_log: ConnectorCallLog | None = None) -> list[BaseTool]`.
- 報告文字:
  - `call_log is None`: 報告末尾一行 `call-record checks not enabled`（無 finding 時整份為 `OK: no findings\ncall-record checks not enabled`）.
  - `call_log.degraded`: 報告開頭一行 `call record unavailable; arg-key and response-layer checks skipped`.
  - 未呼叫 finding: `tool was never called in this session — call it first`（拿掉舊字串裡的 `(landed)`）.
  - keys 不合 finding: 既有 `args keys {...} do not match any landed call — observed key sets: ...` 改字為 `do not match any recorded call`.
  - 讀層 finding（`unwrap_path` 非空, handler 第一層不是路徑首 key）: `rows are at r.data.<path> (the analysis-time landing unwrapped that key); handler reads r.data.<observed> instead` — `<observed>` 是 handler 實際寫的第一層 key, 若是 `.map(`/`[`/裸用則寫 `r.data directly`.
  - 讀層 finding（`unwrap_path == []`, handler 讀 `r.data.result` 或 `r.data.data`）: `r.data is already the array — read it directly, not r.data.<key>`.

- [ ] **Step 1: 改測試**

`tests/test_check_dashboard.py` 頂部:

```python
from app.agent.connectors.model import Connector, ConnectorTool
from app.agent.tools.check import build_check_tools
from app.engine.connector_call_log import CONNECTOR_CALL_LOG_FILENAME, ConnectorCallLog
from app.engine.workspace import prepare_local_layout
```

刪掉 `from app.engine.replay_manifest import ...`. `_land_default_call` 與 `_check_report` 改成:

```python
def _call_log(workspace) -> ConnectorCallLog:
    return ConnectorCallLog(workspace.root / CONNECTOR_CALL_LOG_FILENAME)


def _record_default_call(workspace, unwrap_path=None) -> ConnectorCallLog:
    call_log = _call_log(workspace)
    call_log.append(
        {
            "connector_id": "sales",
            "tool_name": "list_orders",
            "args": {"status": "open"},
            "unwrap_path": [] if unwrap_path is None else unwrap_path,
            "envelope_keys": [],
            "columns": ["status"],
            "row_count": 3,
            "landed": True,
        }
    )
    return call_log


def _check_report(workspace, connectors=(), call_log=None) -> str:
    tools = {
        tool.name: tool for tool in build_check_tools(workspace, connectors, call_log=call_log)
    }
    return tools["check_dashboard"].invoke({})
```

既有測試逐條調整:
- 所有原本呼叫 `_land_default_call(workspace)` 的測試改成 `call_log = _record_default_call(workspace)` 並把 `call_log=call_log` 傳給 `_check_report`.
- `test_check_dashboard_tool_never_landed_reports_finding` 改名 `test_check_dashboard_tool_never_called_reports_finding`; 傳一個空的 `_call_log(workspace)`; 斷言 `"tool was never called in this session — call it first" in report`.
- `test_check_dashboard_lookup_call_without_land_as_satisfies_lint` 刪除（`land_as` 已不存在; 0 列紀錄的案例由下面新測試覆蓋）.
- `test_check_dashboard_arg_key_set_mismatch_reports_observed_key_sets` 的斷言字串改 `do not match any recorded call`.
- 原本不呼叫 `_land_default_call` 且期望 `OK` 或只驗某條 finding 的測試（forbidden token, script src, echarts theme, no error handler, no mcp call, syntax）: 若原本沒有 `mcp()` 或只驗其他 finding, 維持 `call_log=None`, 但期望文字要允許末尾多一行 `call-record checks not enabled`——用 `_finding_lines(report)` 或 `in report` 斷言, 不比對整份字串.

新增測試:

```python
def test_check_dashboard_call_log_none_skips_record_checks_with_note(tmp_path) -> None:
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")
    script_body = (
        "mcp('sales', 'list_orders', { status: 'open' }, r => { if (r.error) return; });\n"
    )
    workspace.dashboard_path.write_text(_build_dashboard_html(script_body), encoding="utf-8")

    report = _check_report(workspace, (_sales_connector(),), call_log=None)

    assert "never called" not in report
    assert report.splitlines()[-1] == "call-record checks not enabled"


def test_check_dashboard_zero_row_record_still_counts_as_called(tmp_path) -> None:
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")
    call_log = _call_log(workspace)
    call_log.append(
        {
            "connector_id": "sales",
            "tool_name": "list_orders",
            "args": {"status": "open"},
            "unwrap_path": ["data"],
            "envelope_keys": [],
            "columns": [],
            "row_count": 0,
            "landed": False,
        }
    )
    script_body = (
        "mcp('sales', 'list_orders', { status: 'open' }, r => { if (r.error) return; "
        "const rows = r.data.data; });\n"
    )
    workspace.dashboard_path.write_text(_build_dashboard_html(script_body), encoding="utf-8")

    report = _check_report(workspace, (_sales_connector(),), call_log=call_log)

    assert "never called" not in report


def test_check_dashboard_record_from_previous_turn_satisfies_lint(tmp_path) -> None:
    """跨輪: 上一輪物件寫的紀錄, 本輪新物件讀到即可通過."""
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")
    _record_default_call(workspace)
    this_turn_call_log = _call_log(workspace)
    script_body = (
        "mcp('sales', 'list_orders', { status: 'open' }, r => { if (r.error) return; });\n"
    )
    workspace.dashboard_path.write_text(_build_dashboard_html(script_body), encoding="utf-8")

    report = _check_report(workspace, (_sales_connector(),), call_log=this_turn_call_log)

    assert "never called" not in report


def test_check_dashboard_result_path_but_handler_reads_r_data_directly_reports_finding(
    tmp_path,
) -> None:
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")
    call_log = _record_default_call(workspace, unwrap_path=["result"])
    script_body = (
        "mcp('sales', 'list_orders', { status: 'open' }, r => { if (r.error) return; "
        "const rows = r.data.map(x => x); });\n"
    )
    workspace.dashboard_path.write_text(_build_dashboard_html(script_body), encoding="utf-8")

    report = _check_report(workspace, (_sales_connector(),), call_log=call_log)

    assert "rows are at r.data.result" in report


def test_check_dashboard_result_path_and_handler_reads_r_data_result_passes(tmp_path) -> None:
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")
    call_log = _record_default_call(workspace, unwrap_path=["result"])
    script_body = (
        "mcp('sales', 'list_orders', { status: 'open' }, r => { if (r.error) return; "
        "const rows = r.data.result; console.log(rows.length); });\n"
    )
    workspace.dashboard_path.write_text(_build_dashboard_html(script_body), encoding="utf-8")

    report = _check_report(workspace, (_sales_connector(),), call_log=call_log)

    assert "rows are at" not in report
    assert "already the array" not in report


def test_check_dashboard_empty_path_but_handler_reads_r_data_result_reports_finding(
    tmp_path,
) -> None:
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")
    call_log = _record_default_call(workspace, unwrap_path=[])
    script_body = (
        "mcp('sales', 'list_orders', { status: 'open' }, r => { if (r.error) return; "
        "const rows = r.data.result; });\n"
    )
    workspace.dashboard_path.write_text(_build_dashboard_html(script_body), encoding="utf-8")

    report = _check_report(workspace, (_sales_connector(),), call_log=call_log)

    assert "r.data is already the array" in report


def test_check_dashboard_handler_parameter_named_other_than_r_is_scanned(tmp_path) -> None:
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")
    call_log = _record_default_call(workspace, unwrap_path=["result"])
    script_body = (
        "mcp('sales', 'list_orders', { status: 'open' }, (response) => { "
        "if (response.error) return; const rows = response.data; });\n"
    )
    workspace.dashboard_path.write_text(_build_dashboard_html(script_body), encoding="utf-8")

    report = _check_report(workspace, (_sales_connector(),), call_log=call_log)

    assert "rows are at response.data.result" in report


def test_check_dashboard_envelope_key_access_at_first_level_is_allowed(tmp_path) -> None:
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")
    call_log = _call_log(workspace)
    call_log.append(
        {
            "connector_id": "sales",
            "tool_name": "list_orders",
            "args": {"status": "open"},
            "unwrap_path": ["data"],
            "envelope_keys": ["errorCode"],
            "columns": ["status"],
            "row_count": 3,
            "landed": True,
        }
    )
    script_body = (
        "mcp('sales', 'list_orders', { status: 'open' }, r => { if (r.error) return; "
        "if (r.data.errorCode) return; const rows = r.data.data; });\n"
    )
    workspace.dashboard_path.write_text(_build_dashboard_html(script_body), encoding="utf-8")

    report = _check_report(workspace, (_sales_connector(),), call_log=call_log)

    assert "rows are at" not in report


def test_check_dashboard_null_path_record_skips_layer_lint(tmp_path) -> None:
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")
    call_log = _call_log(workspace)
    call_log.append(
        {
            "connector_id": "sales",
            "tool_name": "list_orders",
            "args": {"status": "open"},
            "unwrap_path": None,
            "envelope_keys": [],
            "columns": ["fab", "yield"],
            "row_count": 1,
            "landed": True,
        }
    )
    script_body = (
        "mcp('sales', 'list_orders', { status: 'open' }, r => { if (r.error) return; "
        "console.log(r.data.fab); });\n"
    )
    workspace.dashboard_path.write_text(_build_dashboard_html(script_body), encoding="utf-8")

    report = _check_report(workspace, (_sales_connector(),), call_log=call_log)

    assert "rows are at" not in report
    assert "already the array" not in report


def test_check_dashboard_degraded_call_log_skips_record_checks_with_leading_note(
    tmp_path, monkeypatch
) -> None:
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")
    call_log = _call_log(workspace)
    monkeypatch.setattr(type(call_log), "degraded", property(lambda self: True))
    script_body = (
        "mcp('sales', 'list_orders', { status: 'open' }, r => { if (r.error) return; });\n"
    )
    workspace.dashboard_path.write_text(_build_dashboard_html(script_body), encoding="utf-8")

    report = _check_report(workspace, (_sales_connector(),), call_log=call_log)

    assert report.splitlines()[0] == (
        "call record unavailable; arg-key and response-layer checks skipped"
    )
    assert "never called" not in report
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd deepagent-service && uv run pytest tests/test_check_dashboard.py -q`
Expected: collection 通過（import 已改）但多條 FAIL: `TypeError: build_check_tools() got an unexpected keyword argument 'call_log'`.

- [ ] **Step 3: 實作**

`app/agent/tools/check.py`:

1. 刪 `from app.engine.replay_manifest import load_calls, load_landings`; 加 `from app.engine.connector_call_log import ConnectorCallLog`.
2. 常數:

```python
_CALL_RECORD_DISABLED_NOTE = "call-record checks not enabled"
_CALL_RECORD_UNAVAILABLE_NOTE = (
    "call record unavailable; arg-key and response-layer checks skipped"
)
_ARROW_PARAMETER_PATTERN = re.compile(
    r"^\(?\s*([A-Za-z_$][\w$]*)\s*\)?\s*=>|^(?:async\s+)?function\s*\w*\s*\(\s*([A-Za-z_$][\w$]*)"
)
```

3. 紀錄分組:

```python
@dataclass(frozen=True)
class _CallRecordGroup:
    arg_key_sets: list[frozenset[str]]
    unwrap_path: list[str] | None
    envelope_keys: list[str]
    layer_known: bool


def _group_call_records(records: list[dict]) -> dict[tuple[str, str], _CallRecordGroup]:
    """同一 (connector, tool) 的紀錄收成一組: keys 集合全部保留, 拆封路徑取最後一筆 (多筆不一致時 warning)."""
    grouped: dict[tuple[str, str], _CallRecordGroup] = {}
    for record in records:
        pair_key = (record.get("connector_id"), record.get("tool_name"))
        record_args = record.get("args") or {}
        previous = grouped.get(pair_key)
        arg_key_sets = [*(previous.arg_key_sets if previous else []), frozenset(record_args)]
        unwrap_path = record.get("unwrap_path")
        if previous is not None and previous.layer_known and previous.unwrap_path != unwrap_path:
            logger.warning(
                "connector call records disagree on unwrap_path: connector=%s tool=%s",
                pair_key[0],
                pair_key[1],
            )
        grouped[pair_key] = _CallRecordGroup(
            arg_key_sets=arg_key_sets,
            unwrap_path=unwrap_path,
            envelope_keys=list(record.get("envelope_keys") or []),
            layer_known="unwrap_path" in record,
        )
    return grouped
```

4. `build_check_tools(workspace, connectors, call_log: ConnectorCallLog | None = None)`, `_check_dashboard(workspace, connectors, call_log)`:

```python
def _check_dashboard(
    workspace: SessionWorkspace,
    connectors: Sequence[Connector],
    call_log: ConnectorCallLog | None,
) -> str:
    if not workspace.dashboard_path.exists():
        return _DASHBOARD_NOT_FOUND_MESSAGE

    html_text = workspace.dashboard_path.read_text(encoding="utf-8")
    script_blocks = _extract_script_blocks(html_text)

    record_groups: dict[tuple[str, str], _CallRecordGroup] | None = None
    leading_notes: list[str] = []
    trailing_notes: list[str] = []
    if call_log is None:
        trailing_notes.append(_CALL_RECORD_DISABLED_NOTE)
    else:
        records = call_log.load()
        if call_log.degraded:
            leading_notes.append(_CALL_RECORD_UNAVAILABLE_NOTE)
        else:
            record_groups = _group_call_records(records)

    findings: list[tuple[int, str, str]] = []
    findings.extend(_run_syntax_pass(script_blocks))
    findings.extend(_run_contract_pass(html_text, script_blocks, connectors, record_groups))
    return _render_report(findings, leading_notes, trailing_notes)


def _render_report(
    findings: list[tuple[int, str, str]],
    leading_notes: Sequence[str] = (),
    trailing_notes: Sequence[str] = (),
) -> str:
    if not findings:
        body_lines = ["OK: no findings"]
    else:
        ordered_findings = sorted(findings, key=lambda finding: finding[0])
        body_lines = [f"{len(ordered_findings)} finding(s):"]
        body_lines.extend(
            f"- [{kind}] line {line}: {message}" for line, kind, message in ordered_findings
        )
    return "\n".join([*leading_notes, *body_lines, *trailing_notes])
```

`record_groups is None` 代表「兩條紀錄檢查關閉」; `_run_contract_pass` 與 `_check_mcp_call` 的 `landings_by_pair` 參數改名 `record_groups: dict[...] | None`. `_check_mcp_call` 在取得 `observed_keys` 後:

```python
    if record_groups is None:
        return findings
    group = record_groups.get((connector_id, tool_name))
    if group is None:
        findings.append(
            (call_line, "contract", "tool was never called in this session — call it first")
        )
        return findings
    if frozenset(observed_keys) not in group.arg_key_sets:
        observed_sets_text = "; ".join(
            "{" + ", ".join(sorted(key_set)) + "}" for key_set in dict.fromkeys(group.arg_key_sets)
        )
        call_keys_text = ", ".join(sorted(observed_keys)) or "(none)"
        findings.append(
            (
                call_line,
                "contract",
                f"args keys {{{call_keys_text}}} do not match any recorded call — observed key "
                f"sets: {observed_sets_text}",
            )
        )
    if len(arguments) >= 4 and group.layer_known and group.unwrap_path is not None:
        findings.extend(_check_response_layer(call_line, arguments[3].strip(), group))
    return findings
```

5. 讀層 lint:

```python
def _handler_parameter_name(handler_text: str) -> str | None:
    match = _ARROW_PARAMETER_PATTERN.match(handler_text)
    if match is None:
        return None
    return match.group(1) or match.group(2)


def _first_level_accesses(handler_text: str, parameter_name: str) -> list[str | None]:
    """handler 本體裡每一次 `<param>.data` 存取的第一層 key; 沒有接 `.key` (直接 .map/[ /裸用) 記 None."""
    pattern = re.compile(rf"\b{re.escape(parameter_name)}\.data\b(?:\.([A-Za-z_$][\w$]*))?")
    return [match.group(1) for match in pattern.finditer(handler_text)]


_ARRAY_METHOD_NAMES = frozenset(
    {"map", "forEach", "filter", "length", "slice", "reduce", "find", "some", "every", "sort", "flatMap"}
)


def _check_response_layer(
    call_line: int, handler_text: str, group: _CallRecordGroup
) -> list[tuple[int, str, str]]:
    parameter_name = _handler_parameter_name(handler_text)
    if parameter_name is None:
        return []
    unwrap_path = group.unwrap_path or []
    findings: list[tuple[int, str, str]] = []
    for first_key in _first_level_accesses(handler_text, parameter_name):
        if unwrap_path:
            expected_key = unwrap_path[0]
            allowed_keys = {expected_key, *(group.envelope_keys if len(unwrap_path) == 1 else [])}
            if first_key in allowed_keys:
                continue
            observed_text = (
                f"{parameter_name}.data directly"
                if first_key is None or first_key in _ARRAY_METHOD_NAMES
                else f"{parameter_name}.data.{first_key}"
            )
            findings.append(
                (
                    call_line,
                    "contract",
                    f"rows are at {parameter_name}.data.{'.'.join(unwrap_path)} (the "
                    f"analysis-time landing unwrapped that key); handler reads {observed_text} "
                    "instead",
                )
            )
            break
        if first_key in ("result", "data"):
            findings.append(
                (
                    call_line,
                    "contract",
                    f"{parameter_name}.data is already the array — read it directly, not "
                    f"{parameter_name}.data.{first_key}",
                )
            )
            break
    return findings
```

6. `check_dashboard_tool` 的 docstring 把「arg keys matching a call actually made this session」補成「arg keys and response-layer access matching a connector call recorded in this session (any turn)」.
7. 刪掉 `_group_landings_by_pair` 與 `calls.jsonl 記所有成功呼叫...` 那段舊註解.

- [ ] **Step 4: 跑測試確認通過**

Run: `cd deepagent-service && uv run pytest tests/test_check_dashboard.py -q && uv run ruff check app/agent/tools/check.py tests/test_check_dashboard.py`
Expected: 全部 passed（node 未安裝的環境會 skip 標了 `skipif` 的幾條）; ruff 乾淨（含原本的 I001）.

- [ ] **Step 5: Commit**

```bash
git add deepagent-service/app/agent/tools/check.py deepagent-service/tests/test_check_dashboard.py
git commit -m "feat(deepagent): check_dashboard 改讀 ConnectorCallLog——keys 與 r.data 讀層兩條 lint, 紀錄不可用時跳過並說明"
```

---

### Task 5: `chat_turn` 接線 — 建紀錄物件, 註冊 `check_dashboard`, 傳 `dashboard_skill_root`

**Files:**
- Modify: `deepagent-service/app/agent/chat_turn.py`
- Modify: `deepagent-service/tests/test_graph.py:104`
- Test: `deepagent-service/tests/test_chat_turn_connectors.py`

**Interfaces:**
- Consumes: Task 1 `ConnectorCallLog`, `CONNECTOR_CALL_LOG_FILENAME`; Task 3 `build_connector_tools(..., call_log=)`; Task 4 `build_check_tools(workspace, connectors, call_log=)`; 既有 `build_agent(..., dashboard_skill_root=)`.
- Produces: `ChatTurn` 在 connector 模式下持有 `self._call_log: ConnectorCallLog | None`（檔案 `workspace.root / "connector_calls.jsonl"`）; tools 清單含 `check_dashboard`; `build_agent` 收到 `dashboard_skill_root=".skills/builtin/mcp-data-dashboard"`.

- [ ] **Step 1: 修 `tests/test_graph.py`, 補 `tests/test_chat_turn_connectors.py`**

`tests/test_graph.py` 第 98–106 行的 `build_agent(...)` 拿掉 `ToolResultRecorder(),` 那一行（其餘不動）:

```python
    agent = build_agent(
        model,
        connection,
        workspace,
        staged,
        dashboard_skill_root=".skills/builtin/mcp-data-dashboard",
    )
```

`tests/test_chat_turn_connectors.py` 新增（放在 `test_connectors_landing_dir_removed_after_aexit` 之後）:

```python
async def test_connectors_mode_registers_check_dashboard_tool(connector_turn_env) -> None:
    request = _connector_request()
    async with ChatTurn(request) as turn:
        await turn.prepare()
        tool_names = set(turn._agent.nodes["tools"].bound.tools_by_name)

    assert "check_dashboard" in tool_names


async def test_connectors_mode_gates_on_mcp_data_dashboard_skill(
    connector_turn_env, monkeypatch
) -> None:
    captured: dict[str, object] = {}
    original_build_agent = chat_turn.build_agent

    def _spy_build_agent(*args, **kwargs):
        captured.update(kwargs)
        return original_build_agent(*args, **kwargs)

    monkeypatch.setattr(chat_turn, "build_agent", _spy_build_agent)
    request = _connector_request()
    async with ChatTurn(request) as turn:
        await turn.prepare()

    assert captured["dashboard_skill_root"] == ".skills/builtin/mcp-data-dashboard"


async def test_file_mode_uses_default_dashboard_skill_root(connector_turn_env, monkeypatch) -> None:
    captured: dict[str, object] = {}
    original_build_agent = chat_turn.build_agent

    def _spy_build_agent(*args, **kwargs):
        captured.update(kwargs)
        return original_build_agent(*args, **kwargs)

    monkeypatch.setattr(chat_turn, "build_agent", _spy_build_agent)
    request = _connector_request(connectors=[])
    async with ChatTurn(request) as turn:
        await turn.prepare()

    assert "dashboard_skill_root" not in captured


async def test_connector_call_writes_record_at_workspace_root(connector_turn_env) -> None:
    """connector tool 打一次, workspace 頂層就有 connector_calls.jsonl, 內容不含資料列."""
    request = _connector_request()
    async with ChatTurn(request) as turn:
        await turn.prepare()
        tools_by_name = turn._agent.nodes["tools"].bound.tools_by_name
        tools_by_name["demo_quality_list_fabs"].invoke({})
        record_path = turn._workspace.root / "connector_calls.jsonl"

        assert record_path.exists()
        [record] = [json.loads(line) for line in record_path.read_text().splitlines()]
        assert record["connector_id"] == "demo_quality"
        assert record["tool_name"] == "list_fabs"
        assert record["landed"] is True
        assert "rows" not in record and "preview_rows" not in record


async def test_connector_call_record_is_visible_to_check_dashboard_in_next_turn(
    connector_turn_env,
) -> None:
    """第二輪 (同 session, 新 ChatTurn) 讀得到第一輪的紀錄: 純改版面不用重打 connector."""
    request = _connector_request()
    async with ChatTurn(request) as first_turn:
        await first_turn.prepare()
        first_turn._agent.nodes["tools"].bound.tools_by_name["demo_quality_list_fabs"].invoke({})
        # 正常流程由 stream() 結尾 persist; 這裡不驅動模型, 所以手動推一代 zip.
        first_turn._store.persist(first_turn._workspace)

    async with ChatTurn(request) as second_turn:
        await second_turn.prepare()
        assert (second_turn._workspace.root / "connector_calls.jsonl").exists()
        assert second_turn._call_log is not None
        records = second_turn._call_log.load()

    assert [(record["connector_id"], record["tool_name"]) for record in records] == [
        ("demo_quality", "list_fabs")
    ]
```

`_connector_request(**overrides)` 用 `payload.update(overrides)`, 所以 `_connector_request(connectors=[])` 直接可用（`test_empty_connectors_uses_file_mode_unaffected` 是同一種 spy 寫法, 照它）.

跨輪測試依賴的機制（`app/engine/workspace_store.py:66-77`）: 每一輪 `store.prepare()` 開一個新的 scratch 目錄（`secrets.token_hex(8)`）, 再把最新一代 zip 解壓進去; zip 由 `_build_zip` 打包整個 `workspace.root`, 只排除 `.skills`, 所以頂層的 `connector_calls.jsonl` 會隨 zip 跨輪. 兩輪的 `workspace.root` 不同, 斷言的是檔案與紀錄內容, 不是路徑.

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd deepagent-service && uv run pytest tests/test_graph.py tests/test_chat_turn_connectors.py -q`
Expected: `test_graph.py` 全 PASS; 新增的五條 FAIL（`check_dashboard` 不在 tools, `dashboard_skill_root` 不在 kwargs, `connector_calls.jsonl` 不存在, `_call_log` 屬性不存在）.

- [ ] **Step 3: 實作**

`app/agent/chat_turn.py`:

```python
from app.agent.tools.check import build_check_tools
from app.engine.connector_call_log import CONNECTOR_CALL_LOG_FILENAME, ConnectorCallLog

_MCP_DASHBOARD_SKILL_ROOT = ".skills/builtin/mcp-data-dashboard"
```

`__init__` 補 `self._call_log: ConnectorCallLog | None = None`. `prepare()` 的 connector 分支:

```python
        extra_tools: list[BaseTool] | None = None
        connector_tables_reset_note: str | None = None
        build_agent_options: dict[str, Any] = {}
        connection_lock = threading.Lock()
        if connector_specs:
            connectors = tuple(...)  # 既有
            ...  # stage_connector_skills 既有
            self._landing_dir = tempfile.TemporaryDirectory(prefix="connector-landings-")
            landing_path = Path(self._landing_dir.name)
            self._connection = open_locked_connection([], allowed_directories=[str(landing_path)])
            self._call_log = ConnectorCallLog(self._workspace.root / CONNECTOR_CALL_LOG_FILENAME)
            extra_tools = [
                *build_connector_tools(
                    connectors,
                    self._connection,
                    connection_lock,
                    landing_path,
                    call_budget=get_settings().CONNECTOR_CALL_BUDGET,
                    call_log=self._call_log,
                ),
                *build_check_tools(self._workspace, connectors, call_log=self._call_log),
            ]
            build_agent_options["dashboard_skill_root"] = _MCP_DASHBOARD_SKILL_ROOT
            if session_state.has_checkpoint(request.sessionId):
                connector_tables_reset_note = CONNECTOR_TABLES_RESET_NOTE
        else:
            ...  # 既有

        self._agent = build_agent(
            build_model(),
            self._connection,
            self._workspace,
            staged_skill_paths,
            extra_tools=extra_tools,
            connection_lock=connection_lock,
            extra_system_section=(
                build_connector_mode_system_section(connectors) if connector_specs else None
            ),
            **build_agent_options,
        )
```

`from typing import Any` 若尚未 import 則補. 模組 docstring 或 `prepare` docstring 補一句「connector 模式另建呼叫紀錄物件, 同一個實例給 wrapper 寫, 給 check_dashboard 讀」.

- [ ] **Step 4: 跑測試確認通過**

Run: `cd deepagent-service && uv run pytest tests/test_graph.py tests/test_chat_turn_connectors.py tests/test_check_dashboard.py -q && uv run ruff check app/agent/chat_turn.py tests/test_graph.py tests/test_chat_turn_connectors.py`
Expected: 全部 passed; ruff 乾淨（F821 消失）.

- [ ] **Step 5: Commit**

```bash
git add deepagent-service/app/agent/chat_turn.py deepagent-service/tests/test_graph.py deepagent-service/tests/test_chat_turn_connectors.py
git commit -m "feat(deepagent): connector 模式接回 check_dashboard 與 mcp-data-dashboard skill gate, 呼叫紀錄落 workspace 頂層跨輪保留"
```

---

### Task 6: prompt 措辭 — qN 只供對話, dashboard 走 `mcp()`

**Files:**
- Modify: `deepagent-service/app/agent/prompts.py:136-145, 176-183`
- Test: `deepagent-service/tests/test_prompts.py:84-126`

**Interfaces:**
- Consumes: 無.
- Produces: `CONNECTOR_MODE_SYSTEM_SECTION`, `CONNECTOR_TABLES_RESET_NOTE` 新文字（下列逐字）.

- [ ] **Step 1: 改測試**

`tests/test_prompts.py` 第 119–126 行兩條改成:

```python
def test_connector_tables_reset_note_mentions_reload_instruction() -> None:
    assert "unloaded" in CONNECTOR_TABLES_RESET_NOTE
    assert "Call the corresponding" in CONNECTOR_TABLES_RESET_NOTE


def test_connector_tables_reset_note_says_call_records_persist_and_dashboard_uses_mcp() -> None:
    assert "call records from previous turns are still available" in CONNECTOR_TABLES_RESET_NOTE
    assert "layout-only change needs no new connector call" in CONNECTOR_TABLES_RESET_NOTE
    assert "referenced in the dashboard directly" not in CONNECTOR_TABLES_RESET_NOTE
    assert "remain valid" not in CONNECTOR_TABLES_RESET_NOTE
```

新增:

```python
def test_connector_mode_system_section_says_dashboard_fetches_live_via_mcp() -> None:
    """connector 模式 qN 只供對話回答; dashboard 檢視時經 mcp() 現抓, 不嵌資料."""
    assert "The dashboard never embeds data" in CONNECTOR_MODE_SYSTEM_SECTION
    assert "fetches live through `mcp()` at view time" in CONNECTOR_MODE_SYSTEM_SECTION
    assert "mcp-data-dashboard skill" in CONNECTOR_MODE_SYSTEM_SECTION
    assert "`check_dashboard` validates against the calls already recorded" in (
        CONNECTOR_MODE_SYSTEM_SECTION
    )
    assert "reuse the existing qN" not in CONNECTOR_MODE_SYSTEM_SECTION
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd deepagent-service && uv run pytest tests/test_prompts.py -q`
Expected: 上述三條 FAIL.

- [ ] **Step 3: 實作**

`CONNECTOR_MODE_SYSTEM_SECTION` 第 139–142 行（`Landed tables live only for the current turn, but the qN results ... a new data slice is needed. `）整段換成:

```python
    "Landed tables live only for the current turn. The dashboard never embeds data: it fetches "
    "live through `mcp()` at view time (see the mcp-data-dashboard skill), so a layout-only "
    "change needs no new connector call -- `check_dashboard` validates against the calls "
    "already recorded in this session. Call a connector tool again only when you need to see "
    "a new tool or a new argument shape. The qN results produced by run_sql are for answering "
    "the user in the conversation; the dashboard does not read them. "
```

`CONNECTOR_TABLES_RESET_NOTE` 整段換成:

```python
CONNECTOR_TABLES_RESET_NOTE = (
    "\n\n(System note: the tables landed by connector tools in previous turns have been "
    "unloaded; DuckDB currently holds no connector tables. The connector call records from "
    "previous turns are still available to `check_dashboard`, so a layout-only change needs "
    "no new connector call. Call the corresponding connector tool again only if this turn "
    "needs to see a new tool or a new argument shape, or needs fresh rows to answer the user.)"
)
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd deepagent-service && uv run pytest tests/test_prompts.py tests/test_chat_turn_connectors.py -q && uv run ruff check app/agent/prompts.py tests/test_prompts.py`
Expected: 全部 passed（`test_second_turn_seed_message_has_connector_tables_reset_note` 只斷言常數本身在 seed 訊息裡, 不受措辭影響）.

- [ ] **Step 5: Commit**

```bash
git add deepagent-service/app/agent/prompts.py deepagent-service/tests/test_prompts.py deepagent-service/tests/test_chat_turn_connectors.py
git commit -m "docs(deepagent): connector prompt 改講法——qN 只供對話回答, dashboard 經 mcp() 現抓, 純改版面靠呼叫紀錄不重打"
```

---

### Task 7: SKILL.md 與 09-04 spec 表格對齊

**Files:**
- Modify: `deepagent-service/skills/mcp-data-dashboard/SKILL.md:19-25, 84-90, 101-104, 147-152`
- Modify: `docs/superpowers/specs/2026-09-04-mcp-dashboard-verification-options.md:34-35`
- Test: `deepagent-service/tests/test_middleware.py`（既有 gate 測試, 只確認仍綠）; 補一條純文字斷言測試（下）

**Interfaces:**
- Consumes: Task 3 回饋句型（`Raw response shape`）; Task 4 的「session」定義.
- Produces: SKILL.md 新文字.

- [ ] **Step 1: 寫失敗的測試**

新增 `tests/test_mcp_dashboard_skill_text.py`:

```python
"""mcp-data-dashboard SKILL.md 與工具回饋文字的一致性——skill 講的讀列路徑來源與 session 定義
必須和 wrapper 回饋、check_dashboard 的紀錄一致, 否則模型會二選一."""

from pathlib import Path

_SKILL_PATH = Path(__file__).resolve().parents[1] / "skills" / "mcp-data-dashboard" / "SKILL.md"


def _skill_text() -> str:
    return _SKILL_PATH.read_text(encoding="utf-8")


def test_skill_has_no_land_as() -> None:
    assert "land_as" not in _skill_text()


def test_skill_points_r_data_path_at_raw_response_shape_feedback() -> None:
    text = _skill_text()
    assert "Raw response shape" in text
    assert "byte-for-byte" not in text
    assert "`r.data` is the raw response" in text


def test_skill_defines_this_session_as_recorded_calls_in_any_turn() -> None:
    text = _skill_text()
    assert "any turn of this conversation" in text
    assert "`check_dashboard`'s call record" in text


def test_skill_reading_the_response_shows_three_paths() -> None:
    text = _skill_text()
    assert "const rows = r.data;" in text
    assert "const rows = r.data.result;" in text
    assert "const rows = r.data.data;" in text
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd deepagent-service && uv run pytest tests/test_mcp_dashboard_skill_text.py -q`
Expected: 4 FAIL（`land_as` 仍在, `byte-for-byte` 仍在, 其餘字串缺席）.

- [ ] **Step 3: 改 SKILL.md**

Workflow 第 1 步（第 21–25 行）換成:

```markdown
1. Finish the analysis first with the connector tools (`<connector id>_<tool>`). Every call
   automatically lands its response as a DuckDB table and the tool feedback gives you the
   table name, the columns, a preview, and a `Raw response shape` paragraph that says what the
   raw response looks like and which path the rows were taken from. Query the table with
   `run_sql` to understand the data. Everything the dashboard needs per dataset -- the
   **connector id**, the **tool name**, the **exact arg keys**, and the **path to the rows
   inside `r.data`** -- is copied from that feedback; never reconstruct it from memory and
   never from the DuckDB table (the table is the unwrapped rows, not the raw response).
```

`r.data` 段（第 87–89 行 `success → ...`）換成:

```markdown
  - **success** → `r.error` is `null`/`undefined` and `r.data` is the raw response, exactly
    as the connector returned it. `r.data` is **not** the DuckDB table you queried during
    analysis: the landing unwrapped one or more keys to reach the rows. The `Raw response
    shape` paragraph in each connector tool's feedback tells you the path (`r.data`,
    `r.data.result`, `r.data.data`, ...). If you never saw that paragraph for a tool, you
    have not called it -- call it first.
```

鐵律第 2 條（第 101–104 行）換成:

```markdown
2. **Every call MUST mirror an actual tool call you made in any turn of this conversation**
   (same connector, same tool, same arg keys, values of the same type), as recorded in
   `check_dashboard`'s call record. Never a tool you only saw in a skill file but didn't run.
   A layout-only change does not need new calls: the earlier calls are still on record. If the
   dashboard needs a dataset you haven't fetched, fetch it with the tool first, read its `Raw
   response shape`, then write the call.
```

Reading the response 第一點（第 149–152 行）換成:

```markdown
- Normalize to `rows` at the top of the handler using the path from the tool feedback's
  `Raw response shape` paragraph -- one of these three, copied exactly:
  `const rows = r.data;` (the response is already the array),
  `const rows = r.data.result;` (the server wrapped the array in `{result: [...]}`),
  `const rows = r.data.data;` (an envelope like `{data: [...], errorCode: ""}`; the other
  envelope fields are then at `r.data.errorCode`).
  NEVER guess a key and NEVER "search" for the array
  (`Object.values(r.data).find(Array.isArray)`) -- a guess reads `undefined` and every card on
  that dataset dies silently.
```

其餘（卡片狀態, 控制項, 佈局, ECharts 規則, `mcp()` 簽名, `{data}`／`{error:{message}}`, 禁止 API, CDN, theme）不動. 全文 grep `land_as`, `byte-for-byte`, `this session` 確認沒有漏改的地方.

`docs/superpowers/specs/2026-09-04-mcp-dashboard-verification-options.md` 第 34–35 行的 `replay/landings.jsonl` 改成 `connector_calls.jsonl`（workspace 頂層, 跨輪）; 第 48 行 level 3 段落是已 deferred 的敘述, 把 `landings.jsonl`／`land_as` 改成 `connector_calls.jsonl`／`args hash` 用語即可, 不重寫.

- [ ] **Step 4: 跑測試確認通過**

Run: `cd deepagent-service && uv run pytest tests/test_mcp_dashboard_skill_text.py tests/test_middleware.py -q`
Expected: 全部 passed.

- [ ] **Step 5: Commit**

```bash
git add deepagent-service/skills/mcp-data-dashboard/SKILL.md deepagent-service/tests/test_mcp_dashboard_skill_text.py docs/superpowers/specs/2026-09-04-mcp-dashboard-verification-options.md
git commit -m "docs(deepagent): mcp-data-dashboard skill 對齊自動落表——r.data 是 raw, 讀列路徑抄 Raw response shape, session 定義為紀錄所及任一輪"
```

---

### Task 8: spike 對齊 — 拿掉 `UNWRAP_RESULT`, 修 lint, README 指向 spec

**Files:**
- Modify: `deepagent-service/spike/mcp-shell/bridge.py:130-141`
- Modify: `deepagent-service/spike/mcp-shell/README.md:20-25, 44-49`
- Modify: `deepagent-service/spike/mcp-shell/mock_server.py:21`

**Interfaces:** 無（throwaway 探針, 無自動化測試）.

- [ ] **Step 1: 改 `bridge.py`**

刪掉第 134–140 行的 `UNWRAP_RESULT` 分支, 保留前面「NEVER unwrap」註解; `os` import 若只剩這裡用就一併刪. `row_count` 那行改成:

```python
    if isinstance(payload, list):
        row_count = len(payload)
    elif isinstance(payload, dict) and isinstance(payload.get("result"), list):
        row_count = len(payload["result"])
    elif isinstance(payload, dict) and isinstance(payload.get("data"), list):
        row_count = len(payload["data"])
    else:
        row_count = "n/a"
```

- [ ] **Step 2: 改 `mock_server.py`**

第 21 行 `_ANCHOR_DATE = date.today()` 改 `_ANCHOR_DATE = datetime.now(tz=UTC).date()`, import 補 `from datetime import UTC, datetime`（`date` 若他處仍用則保留）.

- [ ] **Step 3: 改 README**

- 第 20–25 行 `out/` 段: 三張舊快照的敘述改成「`out/` holds the snapshots from the latest acceptance run（見下方 Acceptance）; earlier runs' snapshots were removed」.
- 第 44–49 行「Other knobs」: 刪 `UNWRAP_RESULT=1 (...)` 那句.
- 第 16 行起的「Contract assumptions (confirm before productising)」段改成一句: `The page-facing contract this spike implements is the mcp-data-dashboard skill's; the transport-side contract (frontend prelude, Java proxy, deepagent tool-call endpoint, error codes) is drafted in docs/superpowers/specs/2026-09-08-mcp-dashboard-on-autoland-design.md §7 (D9) and is not implemented here.`
- 新增「Acceptance」段, 列人工驗收三點（來自 spec §10）:
  1. 模型第一版 `dashboard.html` 的 handler 就依回饋的 `Raw response shape` 讀 `r.data.result`（mock server 的 list 型 tool）, 不再在 `r.data` 與 `r.data.result` 之間來回改.
  2. 第二輪只說「把兩張圖換位置」: 模型不重打 connector, `check_dashboard` 回 OK.
  3. 故意打一個 mock server 會拒絕的參數值: 頁面該卡顯示 server 的錯誤訊息而非空白.

- [ ] **Step 4: 驗證**

Run: `cd deepagent-service && uv run ruff check spike/ && uv run pytest -q`
Expected: ruff 乾淨（`DTZ011` 消失）; pytest 全綠.

- [ ] **Step 5: Commit**

```bash
git add deepagent-service/spike/mcp-shell/bridge.py deepagent-service/spike/mcp-shell/README.md deepagent-service/spike/mcp-shell/mock_server.py
git commit -m "chore(deepagent): spike 對齊 raw 契約——拿掉 UNWRAP_RESULT, README 契約段指向 spec D9 並列驗收三點"
```

- [ ] **Step 6: 人工驗收（需要 OpenRouter 與真模型, 不在 CI）**

依 README「What was actually run」起 mock server, deepagent, bridge, 用 `generate.sh` 跑一輪; 依 Acceptance 三點檢查; 把新一組 `dashboard.html` 快照放進 `out/`, 刪舊三張; 另一個 commit `chore(deepagent): spike 驗收快照——merge 後重跑`. 若驗收第 1 點失敗（模型仍讀錯層）, 回報使用者, 不自行改 prompt.

---

### Task 9: 收尾 — 全綠確認, spec 狀態更新

**Files:**
- Modify: `docs/superpowers/specs/2026-09-08-mcp-dashboard-on-autoland-design.md:3`（狀態列）
- Modify: 本計畫（勾選完成的 checkbox）

- [ ] **Step 1: 全套驗證**

Run: `cd deepagent-service && uv run ruff check . && uv run pytest -q`
Expected: ruff 乾淨; 全綠. 參考基準: merge commit 當下 `--ignore=tests/test_check_dashboard.py` 跑出 436 passed + 1 failed; 本計畫恢復 `test_check_dashboard.py`（約 18 條）並新增約 40 條, 總數應落在 490 附近, 少於這個量級代表有測試檔沒被收集.

Run（不受影響但仍跑, 專案規則）: `cd backend && ./mvnw -q test`（若本機無 Java 環境, 由 CI 跑）.

- [ ] **Step 2: spec 狀態列**

第 3 行的 `** merge 設計規格, merge 尚未執行.**` 改為 `**merge 已於 2026-09-08 執行於 branch feat/mcp-dashboard-merge-datasource（merge commit 基準 datasource bcb61f3, 與本文一致）; D0, D5–D8, D1–D4 已依 plan 2026-09-08-mcp-dashboard-on-autoland.md 落地.**`; 第 13 節末段「merge 本身尚未執行」同步改.

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-09-08-mcp-dashboard-on-autoland-design.md docs/superpowers/plans/2026-09-08-mcp-dashboard-on-autoland.md
git commit -m "docs(spec): mcp-dashboard-on-autoland 狀態改為 merge 已執行, plan checkbox 勾選"
```

- [ ] **Step 4: 交付**

依 CLAUDE.md 多人協作規則: opus 全 branch 終審（範圍 `origin/feat/mcp-dashboard...HEAD`, 含 merge commit 之後每一個 commit）→ 終審結論寫進 PR 描述 → PR 描述附 spec 連結與第 12 節拍板結果 → 使用者觸發 merge. push 與開 PR 都等使用者指示.

---

## 自我檢查（寫完後對 spec 逐節核對）

| spec 項目 | 對應 Task |
|---|---|
| D0 merge 方式 A（merge commit 已落地, dashboard 功能獨立 commit 重落） | merge commit `577d1ee`; Task 3–5 |
| D5 `unwrap_path` 三元組 / `LandingResult` 欄位 | Task 2 |
| D5 `Raw response shape` 回饋段（四種句型） | Task 3 |
| D5 SKILL.md `r.data` 段 + Reading the response 三範例 | Task 7 |
| D5 spike 拿掉 `UNWRAP_RESULT` | Task 8 |
| D6 兩段 prompt 措辭; `inject_results` 不動 | Task 6（chat_turn 的 `inject_results` 呼叫本計畫不碰） |
| D7 `dashboard_skill_root` 保留並在 connector 模式傳入; SKILL.md Workflow 1 / 鐵律 2 | Task 5, Task 7 |
| D8 spike 保留, README, 人工重跑換快照 | Task 8 |
| D1 位置 workspace 頂層 `connector_calls.jsonl`, 跨輪, 不去重 | Task 1, Task 5 |
| D2 內容（含 0 列 `landed:false`; tool 錯誤不記）; 寫入失敗退路（記憶體鏡像 + 降級） | Task 1, Task 3, Task 4 |
| D3 `ConnectorCallLog` 注入 wrapper 與 check | Task 3, Task 4, Task 5 |
| D4 keys 比對跨輪; `call_log=None` 行為 | Task 4 |
| D4b unwrap-path lint（handler 參數名不假設 `r`; 多筆不一致 warning） | Task 4 |
| §9 `test_graph.py` 兩邊合併 | Task 5 |
| §9 09-04 spec level 2 表格 | Task 7 |
| §10 測試與完成條件 | Task 9 |
| D9 傳輸面, D10, D11 | 非本計畫（spec 已定案延後／不做） |

型別一致性: `unwrap_path: list[str] | None` 在 Task 2（回傳值, `LandingResult`, `EmptyLandingError`）, Task 3（紀錄 dict, `describe_raw_response_shape` 參數）, Task 4（`_CallRecordGroup.unwrap_path`）三處同名同型; `envelope_keys` 在紀錄裡是 `list[str]`, 由 Task 3 以 `list(envelope_fields)` 產生, Task 4 以 `record.get("envelope_keys") or []` 讀.
