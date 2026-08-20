# API Connector（Phase 1）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** deepagent 新增 `fetch_api_data` 工具——模型依對話意圖選 connector、蒐集參數（缺則反問）、抓 API 資料落 snapshot 掛進 DuckDB，接上既有 get_schema/run_sql/dashboard 流程；同時 dashboard skill 升級「敘事綁定」（數字/主詞從 `__ERD_RESULTS__` 綁定、判斷照分級表、自由洞察必標記）。

**Architecture:** connector 定義集中在 YAML config（單一事實來源），engine 層 executor 確定性執行（auth/caps/落檔），工具層只做驗證＋掛載＋sentinel 回報；prompt 的 connector 段由 config 生成。DuckDB 鎖門連線用 `allowed_directories` 白名單放行 `api_snapshots/` 目錄（duckdb≥1.2），mid-turn 掛載恢復可行、安全語意僅放寬此一目錄。敘事綁定的 resolver JS 由 finalize 後處理確定性注入（與 theme/inject_results 同層），模型只寫 `data-bind` 屬性與支撐查詢。每次 fetch 的 `{connector, params, alias}` 落 `fetches.json`——Phase 2 分享重繪的 recipe 材料，今天不存未來救不回。

**Tech Stack:** Python 3.11／FastAPI／httpx（既有）／Pydantic v2／PyYAML（需確認依賴）／duckdb≥1.2／pytest asyncio_mode=auto

## Global Constraints

- `app/engine/` 禁 import langchain/deepagents/langgraph/langfuse（ruff TID251）；duckdb/httpx/pydantic 可用
- **功能預設關閉不變式**：`AGENT_CONNECTORS_FILE` 未設或檔不存在 → 工具不註冊、prompt 無 connector 段、連線行為與現況 byte-identical——e2e 必須釘住此不變式
- 變數命名禁 1–2 字元；註解 1–2 行寫目的＋做法；測試命名 snake_case `methodName_condition_expectedBehavior`
- formatter hook 每次 Edit 後自動跑並刪未用 import——先改用法、最後補 import
- 每 task 結尾：`cd deepagent-service && uv run pytest tests/ -q && uv run ruff check .` 全綠才 commit；NEVER `| tail`
- 模型面錯誤訊息一律「帶下一步指引」（弱模型退貨可自癒模式）；NEVER 在錯誤訊息洩 endpoint URL/憑證
- sentinel 包裹一律用既有 `frame_data_content`（`app/agent/tools/framing.py`）
- master 現況：dashboard 由主 agent 直寫（無 renderer subagent）；本 plan 不依賴 PR #47

---

### Task 1: Connector config 模型與 loader

**Files:**
- Create: `deepagent-service/app/engine/connectors.py`
- Modify: `deepagent-service/app/config.py`（加 `AGENT_CONNECTORS_FILE: str | None = None`，照既有欄位樣式）
- Modify: `deepagent-service/pyproject.toml`（若 PyYAML 不在依賴樹才加 `pyyaml>=6.0`；先 `uv run python -c "import yaml"` 確認）
- Test: `deepagent-service/tests/test_connectors.py`（新檔）

**Interfaces:**
- Produces: `ConnectorParam`／`ConnectorDefinition`（Pydantic）、`ConnectorRegistry`（`.get(name)`、`.data_connectors()`、`.lookup_connectors()`、`.is_empty()`）、`load_connector_registry(config_path: Path | None) -> ConnectorRegistry`（None 或檔不存在 → 空 registry）
- 供 Task 3（executor 吃 definition）、Task 4（工具驗證）、Task 6（prompt 生成）

- [x] **Step 1: 寫 failing tests**

```python
"""tests/test_connectors.py"""
from pathlib import Path

import pytest

from app.engine.connectors import ConnectorConfigError, load_connector_registry

VALID_YAML = """\
connectors:
  - name: line_list
    kind: lookup
    description: 產線清單
    endpoint: ${TEST_API_BASE}/lines
    method: GET
    auth: bearer:TEST_API_TOKEN
    params: {}
  - name: mes_yield
    kind: data
    description: 產線良率
    endpoint: ${TEST_API_BASE}/yield
    method: POST
    auth: bearer:TEST_API_TOKEN
    params:
      line_id:
        type: str
        required: true
        validate_against: {connector: line_list, column: line_id}
      start_date: {type: date, required: true}
    limits: {timeout_s: 10, max_bytes: 1000000, max_rows: 50000}
"""


def _write_config(tmp_path: Path, text: str) -> Path:
    config_path = tmp_path / "connectors.yaml"
    config_path.write_text(text, encoding="utf-8")
    return config_path


def test_load_registry_validConfig_parsesDefinitions(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_API_BASE", "http://api.internal")
    registry = load_connector_registry(_write_config(tmp_path, VALID_YAML))
    assert not registry.is_empty()
    yield_definition = registry.get("mes_yield")
    assert yield_definition.kind == "data"
    assert yield_definition.endpoint == "http://api.internal/yield"
    assert yield_definition.params["line_id"].validate_against.connector == "line_list"
    assert [d.name for d in registry.lookup_connectors()] == ["line_list"]


def test_load_registry_missingFile_returnsEmptyRegistry(tmp_path):
    assert load_connector_registry(tmp_path / "absent.yaml").is_empty()
    assert load_connector_registry(None).is_empty()


def test_load_registry_unknownValidateAgainstConnector_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_API_BASE", "http://api.internal")
    broken = VALID_YAML.replace("connector: line_list", "connector: no_such")
    with pytest.raises(ConnectorConfigError, match="no_such"):
        load_connector_registry(_write_config(tmp_path, broken))


def test_load_registry_missingEnvVar_raisesWithVarName(tmp_path):
    with pytest.raises(ConnectorConfigError, match="TEST_API_BASE"):
        load_connector_registry(_write_config(tmp_path, VALID_YAML))


def test_load_registry_duplicateName_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_API_BASE", "http://api.internal")
    duplicated = VALID_YAML + VALID_YAML.split("connectors:\n")[1]
    with pytest.raises(ConnectorConfigError, match="duplicate"):
        load_connector_registry(_write_config(tmp_path, duplicated))
```

- [x] **Step 2: 跑測試確認 fail**

Run: `cd deepagent-service && uv run pytest tests/test_connectors.py -q`
Expected: FAIL（ModuleNotFoundError）

- [x] **Step 3: 實作 `app/engine/connectors.py`**

```python
"""Connector 定義的單一事實來源——YAML 載入+Pydantic 驗證+env 展開。config 缺席=空 registry
(功能整體關閉);validate_against 引用、重複名、缺 env var 一律啟動即失敗,NEVER 靜默。"""

import os
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

_ENV_PATTERN = re.compile(r"\$\{(\w+)\}")

# alias/識別字安全樣式,與 duck.py 的掛載驗證同一標準。
SAFE_IDENTIFIER_PATTERN = re.compile(r"^\w+$", re.UNICODE)


class ConnectorConfigError(RuntimeError):
    pass


class ValidateAgainst(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connector: str
    column: str


class ConnectorParam(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["str", "int", "float", "date"]
    required: bool = False
    description: str = ""
    validate_against: ValidateAgainst | None = None


class ConnectorLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timeout_s: int = 30
    max_bytes: int = 50_000_000
    max_rows: int = 500_000


class ConnectorDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    kind: Literal["lookup", "data"]
    description: str
    endpoint: str
    method: Literal["GET", "POST"] = "GET"
    auth: str = ""  # "" 無認證;"bearer:ENV_NAME" 一期唯一模式,字串格式留擴充(user-token 等)
    params: dict[str, ConnectorParam] = Field(default_factory=dict)
    limits: ConnectorLimits = Field(default_factory=ConnectorLimits)


class ConnectorRegistry:
    def __init__(self, definitions: list[ConnectorDefinition]) -> None:
        self._by_name = {definition.name: definition for definition in definitions}

    def is_empty(self) -> bool:
        return not self._by_name

    def get(self, name: str) -> ConnectorDefinition | None:
        return self._by_name.get(name)

    def all(self) -> list[ConnectorDefinition]:
        return list(self._by_name.values())

    def data_connectors(self) -> list[ConnectorDefinition]:
        return [d for d in self._by_name.values() if d.kind == "data"]

    def lookup_connectors(self) -> list[ConnectorDefinition]:
        return [d for d in self._by_name.values() if d.kind == "lookup"]


def _expand_env(raw_value: str, context: str) -> str:
    def _replace(match: re.Match[str]) -> str:
        env_name = match.group(1)
        env_value = os.environ.get(env_name)
        if env_value is None:
            raise ConnectorConfigError(f"connector config {context}: env var {env_name} 未設定")
        return env_value

    return _ENV_PATTERN.sub(_replace, raw_value)


def load_connector_registry(config_path: Path | None) -> ConnectorRegistry:
    if config_path is None or not Path(config_path).is_file():
        return ConnectorRegistry([])
    raw_document = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
    raw_connectors = raw_document.get("connectors", [])
    definitions: list[ConnectorDefinition] = []
    for raw_connector in raw_connectors:
        try:
            definition = ConnectorDefinition.model_validate(raw_connector)
        except ValidationError as validation_error:
            raise ConnectorConfigError(f"connector config 驗證失敗: {validation_error}") from (
                validation_error
            )
        if not SAFE_IDENTIFIER_PATTERN.fullmatch(definition.name):
            raise ConnectorConfigError(f"connector 名稱非法識別字: {definition.name!r}")
        definition = definition.model_copy(
            update={"endpoint": _expand_env(definition.endpoint, definition.name)}
        )
        definitions.append(definition)
    names = [definition.name for definition in definitions]
    if len(names) != len(set(names)):
        raise ConnectorConfigError(f"connector config duplicate names: {names}")
    by_name = {definition.name: definition for definition in definitions}
    for definition in definitions:
        for param_name, param in definition.params.items():
            if param.validate_against and param.validate_against.connector not in by_name:
                raise ConnectorConfigError(
                    f"{definition.name}.{param_name} validate_against 引用不存在的 "
                    f"connector: {param.validate_against.connector}"
                )
    return ConnectorRegistry(definitions)
```

`app/config.py` 加欄位 `AGENT_CONNECTORS_FILE: str | None = None`（放 AGENT_* 群組內）。

- [x] **Step 4: 跑測試 pass → 全套＋ruff → commit**

```bash
git add deepagent-service/app/engine/connectors.py deepagent-service/tests/test_connectors.py deepagent-service/app/config.py deepagent-service/pyproject.toml deepagent-service/uv.lock
git commit -m "feat(deepagent): connector config 模型與 loader——YAML 單一事實來源,缺席即功能關閉"
```

---

### Task 2: DuckDB 鎖門連線的 snapshot 白名單（含 spike）

**Files:**
- Modify: `deepagent-service/app/engine/duck.py`
- Test: `deepagent-service/tests/test_duck.py`（追加）

**Interfaces:**
- Produces: `open_locked_connection(sources, memory_limit="2GB", api_snapshots_dir: Path | None = None)`——`api_snapshots_dir` 非 None 時鎖門前 `SET allowed_directories`，鎖後該目錄內 `read_json_auto` 可用；`_READERS` 加 `"json": "read_json_auto"`（下輪開門時 snapshot 當一般 source 掛載用）
- 供 Task 4（mid-turn 掛載）、Task 5（開門掛載）

- [x] **Step 1: SPIKE——先寫 pinning tests 驗證 duckdb 能力（本 plan 最大技術風險，先驗再往下）**

```python
def test_locked_connection_allowedDirectory_readJsonWorksInsideOnly(tmp_path):
    inside_path = tmp_path / "api_snapshots"
    inside_path.mkdir()
    (inside_path / "sample.json").write_text('[{"tool":"A","value":1}]', encoding="utf-8")
    outside_path = tmp_path / "outside.json"
    outside_path.write_text('[{"tool":"B"}]', encoding="utf-8")

    connection = open_locked_connection([], api_snapshots_dir=inside_path)
    rows = connection.execute(
        "SELECT * FROM read_json_auto(?)", [str(inside_path / "sample.json")]
    ).fetchall()
    assert rows == [("A", 1)]
    with pytest.raises(duckdb.Error):
        connection.execute("SELECT * FROM read_json_auto(?)", [str(outside_path)]).fetchall()


def test_locked_connection_allowedDirectory_configStaysLocked(tmp_path):
    inside_path = tmp_path / "api_snapshots"
    inside_path.mkdir()
    connection = open_locked_connection([], api_snapshots_dir=inside_path)
    with pytest.raises(duckdb.Error):
        connection.execute("SET allowed_directories = []")
    with pytest.raises(duckdb.Error):
        connection.execute("SET enable_external_access = true")


def test_locked_connection_noSnapshotDir_behaviorUnchanged(tmp_path):
    outside_path = tmp_path / "any.json"
    outside_path.write_text("[]", encoding="utf-8")
    connection = open_locked_connection([])
    with pytest.raises(duckdb.Error):
        connection.execute("SELECT * FROM read_json_auto(?)", [str(outside_path)]).fetchall()
```

Run: `uv run pytest tests/test_duck.py -q` → 前兩條 FAIL（參數不存在）。

- [x] **Step 2: 實作**

`_READERS` 加 `"json": "read_json_auto"`。`open_locked_connection` 加 keyword 參數 `api_snapshots_dir: Path | None = None`；掛載完 sources 後、鎖門前：

```python
    if api_snapshots_dir is not None:
        # 白名單放行 snapshot 目錄:enable_external_access=false 之下僅此目錄可讀,
        # 供 fetch_api_data 於鎖門後 mid-turn 掛載;網路與其他路徑照舊全鎖。
        connection.execute(
            "SET allowed_directories = [?]", [str(Path(api_snapshots_dir).resolve())]
        )
    connection.execute("SET enable_external_access = false")
    connection.execute("SET lock_configuration = true")
```

（`allowed_directories` 與 `enable_external_access`/鎖定的正確順序以 spike 測試實跑為準——若 duckdb 要求以 connect config 傳入而非 SET，調整實作、斷言不變。）

- [x] **Step 3: 若 spike 無法通過（read_json_auto 鎖後不可用／allowed_directories 不存在）→ 回報 BLOCKED**

這是 plan 的既定升級點：fallback＝連線重建方案（ConnectionHolder），需要回 controller 重新規劃 Task 2/4，不得自行繞路實作。

- [x] **Step 4: 全套＋ruff → commit**

```bash
git add deepagent-service/app/engine/duck.py deepagent-service/tests/test_duck.py
git commit -m "feat(deepagent): 鎖門連線放行 api_snapshots 白名單目錄——mid-turn JSON 掛載的安全縫"
```

---

### Task 3: Engine executor——HTTP 執行、落檔、fetch 記錄

**Files:**
- Create: `deepagent-service/app/engine/api_fetch.py`
- Modify: `deepagent-service/app/engine/workspace.py`（`SessionWorkspace` 加 `api_snapshots_dir`、`fetches_path` property，照 `queries_dir` 既有樣式；`prepare_local_layout` mkdir）
- Test: `deepagent-service/tests/test_api_fetch.py`（新檔）

**Interfaces:**
- Consumes: `ConnectorDefinition`（Task 1）
- Produces: `execute_fetch(definition, params, transport=None) -> bytes`（成功回 payload；失敗拋 `ConnectorFetchError`，訊息帶指引不帶 URL）、`land_snapshot(workspace, alias, payload) -> Path`、`record_fetch(workspace, alias, connector_name, params)`（append `fetches.json`——Phase 2 recipe 材料）、`FETCH_ERROR_PREFIX = "FETCH_ERROR"`
- 供 Task 4（工具組裝）

- [x] **Step 1: 寫 failing tests（httpx.MockTransport，零真網路）**

```python
"""tests/test_api_fetch.py"""
import json

import httpx
import pytest

from app.engine.api_fetch import (
    ConnectorFetchError,
    execute_fetch,
    land_snapshot,
    load_fetch_records,
    record_fetch,
)
from app.engine.connectors import ConnectorDefinition


def _definition(**overrides) -> ConnectorDefinition:
    base = {
        "name": "mes_yield",
        "kind": "data",
        "description": "良率",
        "endpoint": "http://api.internal/yield",
        "method": "POST",
        "auth": "bearer:TEST_TOKEN_ENV",
        "params": {},
        "limits": {"timeout_s": 5, "max_bytes": 1000, "max_rows": 100},
    }
    base.update(overrides)
    return ConnectorDefinition.model_validate(base)


def test_execute_fetch_success_sendsAuthHeaderAndParams(monkeypatch):
    monkeypatch.setenv("TEST_TOKEN_ENV", "secret-token")
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=[{"tool": "A"}])

    payload = execute_fetch(
        _definition(), {"line_id": "A"}, transport=httpx.MockTransport(handler)
    )
    assert json.loads(payload) == [{"tool": "A"}]
    assert captured["auth"] == "Bearer secret-token"
    assert captured["body"] == {"line_id": "A"}


def test_execute_fetch_getMethod_paramsAsQuery(monkeypatch):
    monkeypatch.setenv("TEST_TOKEN_ENV", "t")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["line_id"] == "A"
        return httpx.Response(200, json=[])

    execute_fetch(
        _definition(method="GET"), {"line_id": "A"}, transport=httpx.MockTransport(handler)
    )


def test_execute_fetch_missingAuthEnv_raisesWithoutUrl(monkeypatch):
    monkeypatch.delenv("TEST_TOKEN_ENV", raising=False)
    with pytest.raises(ConnectorFetchError) as error_info:
        execute_fetch(_definition(), {}, transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    assert "TEST_TOKEN_ENV" in str(error_info.value)
    assert "api.internal" not in str(error_info.value)


def test_execute_fetch_httpError_raisesActionable(monkeypatch):
    monkeypatch.setenv("TEST_TOKEN_ENV", "t")
    transport = httpx.MockTransport(lambda request: httpx.Response(503))
    with pytest.raises(ConnectorFetchError, match="mes_yield"):
        execute_fetch(_definition(), {}, transport=transport)


def test_execute_fetch_oversizedBody_raisesCapMessage(monkeypatch):
    monkeypatch.setenv("TEST_TOKEN_ENV", "t")
    big_payload = json.dumps([{"x": "y" * 50}] * 100).encode()
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=big_payload))
    with pytest.raises(ConnectorFetchError, match="max_bytes"):
        execute_fetch(_definition(), {}, transport=transport)


def test_land_snapshot_and_record_fetch_roundTrip(tmp_path):
    workspace = _make_workspace(tmp_path)  # 照 tests/test_middleware.py 既有 workspace 構造慣例
    snapshot_path = land_snapshot(workspace, "yield_data", b'[{"a":1}]')
    assert snapshot_path == workspace.api_snapshots_dir / "yield_data.json"
    assert snapshot_path.read_bytes() == b'[{"a":1}]'
    record_fetch(workspace, "yield_data", "mes_yield", {"line_id": "A"})
    records = load_fetch_records(workspace)
    assert records == [{"alias": "yield_data", "connector": "mes_yield", "params": {"line_id": "A"}}]
    # 同 alias 重抓:snapshot 覆蓋、記錄以最後一筆為準
    land_snapshot(workspace, "yield_data", b'[{"a":2}]')
    record_fetch(workspace, "yield_data", "mes_yield", {"line_id": "B"})
    assert load_fetch_records(workspace)[-1]["params"] == {"line_id": "B"}
```

（`_make_workspace` 非佔位符：抄 `tests/test_middleware.py` 既有的 workspace fixture 構造。）

- [x] **Step 2: 跑測試 fail → 實作 `app/engine/api_fetch.py`**

```python
"""Connector 的確定性執行層——HTTP 呼叫(auth/timeout/caps)+snapshot 落檔+fetch 記錄。
錯誤一律 ConnectorFetchError 且訊息帶下一步指引、不含 endpoint URL(防洩內網位址)。"""

import json
import os
from pathlib import Path

import httpx

from app.engine.connectors import ConnectorDefinition
from app.engine.workspace import SessionWorkspace

FETCH_ERROR_PREFIX = "FETCH_ERROR"


class ConnectorFetchError(RuntimeError):
    pass


def _auth_headers(definition: ConnectorDefinition) -> dict[str, str]:
    if not definition.auth:
        return {}
    mode, _, env_name = definition.auth.partition(":")
    if mode != "bearer" or not env_name:
        raise ConnectorFetchError(
            f"connector {definition.name} 的 auth 模式不支援: {definition.auth!r}(一期僅 bearer:ENV)"
        )
    token = os.environ.get(env_name)
    if not token:
        raise ConnectorFetchError(
            f"connector {definition.name} 需要 env var {env_name}(未設定)——請聯繫維運補齊後重試"
        )
    return {"Authorization": f"Bearer {token}"}


def execute_fetch(
    definition: ConnectorDefinition,
    params: dict,
    transport: httpx.BaseTransport | None = None,
) -> bytes:
    headers = _auth_headers(definition)
    try:
        with httpx.Client(transport=transport, timeout=definition.limits.timeout_s) as client:
            if definition.method == "GET":
                response = client.get(definition.endpoint, params=params, headers=headers)
            else:
                response = client.post(definition.endpoint, json=params, headers=headers)
    except httpx.HTTPError as transport_error:
        raise ConnectorFetchError(
            f"connector {definition.name} 呼叫失敗({type(transport_error).__name__})——"
            "可稍後重試;持續失敗請如實告知使用者資料源暫不可用"
        ) from transport_error
    if response.status_code != 200:
        raise ConnectorFetchError(
            f"connector {definition.name} 回應 HTTP {response.status_code}——"
            "請檢查參數是否正確;若為權限問題請如實告知使用者"
        )
    body = response.content
    if len(body) > definition.limits.max_bytes:
        raise ConnectorFetchError(
            f"connector {definition.name} 回應超過 max_bytes 上限"
            f"({len(body)} > {definition.limits.max_bytes})——請縮小查詢範圍(如日期區間)再試"
        )
    return body


def land_snapshot(workspace: SessionWorkspace, alias: str, payload: bytes) -> Path:
    workspace.api_snapshots_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = workspace.api_snapshots_dir / f"{alias}.json"
    snapshot_path.write_bytes(payload)
    return snapshot_path


def record_fetch(
    workspace: SessionWorkspace, alias: str, connector_name: str, params: dict
) -> None:
    records = load_fetch_records(workspace)
    records.append({"alias": alias, "connector": connector_name, "params": params})
    workspace.fetches_path.write_text(
        json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8"
    )


def load_fetch_records(workspace: SessionWorkspace) -> list[dict]:
    if not workspace.fetches_path.exists():
        return []
    return json.loads(workspace.fetches_path.read_text(encoding="utf-8"))
```

`workspace.py`：`SessionWorkspace` 加兩個 property（照 `queries_dir` 樣式）：`api_snapshots_dir = root / "api_snapshots"`、`fetches_path = root / "api_snapshots" / "fetches.json"`；`prepare_local_layout` 一併 mkdir。注意：`fetches.json` 放 `api_snapshots/` 內但**不掛進 DuckDB**（Task 4 掛載只掛工具指定的單一檔案，不 glob 目錄）。

- [x] **Step 3: 全套＋ruff → commit**

```bash
git add deepagent-service/app/engine/api_fetch.py deepagent-service/app/engine/workspace.py deepagent-service/tests/test_api_fetch.py
git commit -m "feat(deepagent): connector executor——確定性 HTTP 執行+snapshot 落檔+fetch 記錄(recipe 前置)"
```

---

### Task 4: `fetch_api_data` 工具

**Files:**
- Modify: `deepagent-service/app/agent/tools/data.py`（`build_data_tools` 加 `connectors: ConnectorRegistry | None = None` 參數；registry 非空才多回傳第 4 個工具；工具與其他三個共用同一把 `connection_lock`）
- Test: `deepagent-service/tests/test_data_tools.py`（追加；若該檔不存在則沿用現有 data-tools 測試檔）

**Interfaces:**
- Consumes: Task 1 registry、Task 2 掛載能力、Task 3 executor
- Produces: 模型面工具 `fetch_api_data(connector: str, params: dict, alias: str)`；`MAX_FETCHES_PER_TURN = 6`
- 供 Task 6 接線、Task 8 e2e

- [x] **Step 1: 寫 failing tests**

測試構造：registry 用 Task 1 的 `VALID_YAML` 樣式載入（monkeypatch env）；`execute_fetch` 用 monkeypatch 換成 fake（回固定 JSON bytes），不碰網路；connection 用 `open_locked_connection([], api_snapshots_dir=workspace.api_snapshots_dir)`。

```python
def test_fetch_api_data_success_mountsTableAndReturnsFramedSchema(...):
    # 呼叫工具(connector="mes_yield", params 合法, alias="yield_data")
    # 斷言:snapshot 檔存在;connection 可 SELECT * FROM yield_data;
    #      回傳含 frame sentinel、欄位型別行、"3 rows sample"、列數;
    #      load_fetch_records 有一筆 {alias, connector, params}

def test_fetch_api_data_unknownConnector_listsAvailable(...):
    # 回傳以 FETCH_ERROR 開頭,列出 registry 內全部 connector 名

def test_fetch_api_data_missingRequiredParam_namesParamAndType(...):
    # 缺 start_date → 訊息含 "start_date"、"date"

def test_fetch_api_data_invalidAlias_rejected(...):
    # alias "bad-name;drop" → 錯誤訊息教改用底線識別字;不執行 fetch

def test_fetch_api_data_aliasCollidesWithMountedTable_rejected(...):
    # 先掛一張 uploaded 表同名 → 退貨指引換 alias

def test_fetch_api_data_validateAgainst_valueMissing_returnsNearestCandidates(...):
    # 先 fetch line_list(lookup, 含 AX-03/AX-30/BX-11) → fetch mes_yield line_id="AX-3"
    # → FETCH_ERROR 含 "AX-03"(最近似候選)且指名 line_list

def test_fetch_api_data_validateAgainst_lookupNotFetched_redirectsToLookup(...):
    # 未先抓 line_list 就帶 line_id → 訊息指示先 fetch_api_data(line_list)

def test_fetch_api_data_perTurnCap_exceeded_returnsGuidance(...):
    # 連呼 7 次 → 第 7 次回上限訊息不執行

def test_fetch_api_data_zeroRows_statesEmptyExplicitly(...):
    # payload "[]" → 回傳含 "0 rows",不誤導

def test_build_data_tools_noRegistry_returnsThreeToolsOnly(...):
    # connectors=None / 空 registry → 工具數量與名稱與現況相同(功能關閉不變式)
```

- [x] **Step 2: 跑測試 fail → 實作（加在 `build_data_tools` 內，與既有三工具同一 closure）**

```python
    MAX_FETCHES_PER_TURN = 6
    fetch_count = {"used": 0}

    def _nearest_candidates(lookup_alias: str, column: str, value: str) -> list[str]:
        # column/alias 來自 config(已過識別字驗證),仍以白名單樣式雙保險;值走參數綁定。
        rows = (
            connection.cursor()
            .execute(
                f'SELECT DISTINCT "{column}" FROM "{lookup_alias}" '
                f'ORDER BY levenshtein(lower(CAST("{column}" AS VARCHAR)), lower(?)) LIMIT 5',
                [value],
            )
            .fetchall()
        )
        return [str(row[0]) for row in rows]

    @tool("fetch_api_data")
    def fetch_api_data_tool(connector: str, params: dict, alias: str) -> str:
        """Fetch data from a configured API connector into a queryable table.

        connector: 一個已設定的 connector 名稱(見 system prompt 的資料源清單)。
        params: 該 connector 宣告的參數(名稱→值)。alias: 掛載後的表名(底線識別字)。
        """
        with connection_lock:
            if fetch_count["used"] >= MAX_FETCHES_PER_TURN:
                return (
                    f"{FETCH_ERROR_PREFIX}: 本輪 fetch 次數已達上限({MAX_FETCHES_PER_TURN})。"
                    "請先用 run_sql 分析既有資料,或請使用者下一輪再繼續。"
                )
            definition = connectors.get(connector)
            if definition is None:
                available = ", ".join(d.name for d in connectors.all())
                return f"{FETCH_ERROR_PREFIX}: connector {connector!r} 不存在。可用: {available}"
            if not SAFE_IDENTIFIER_PATTERN.fullmatch(alias):
                return (
                    f"{FETCH_ERROR_PREFIX}: alias {alias!r} 非法——只能用字母/數字/底線,"
                    "請換一個(例: yield_data)再呼叫一次。"
                )
            mounted = {
                row[0]
                for row in connection.cursor()
                .execute("SELECT table_name FROM information_schema.tables")
                .fetchall()
            }
            if alias in mounted:
                return f"{FETCH_ERROR_PREFIX}: alias {alias!r} 已存在,請換一個名稱。"
            # 參數驗證:必填/型別/validate_against(值不存在→最近似候選;lookup 未抓→指路)
            validation_error = _validate_fetch_params(definition, params, mounted)
            if validation_error is not None:
                return validation_error
            try:
                payload = execute_fetch(definition, params)
            except ConnectorFetchError as fetch_error:
                return f"{FETCH_ERROR_PREFIX}: {fetch_error}"
            snapshot_path = land_snapshot(workspace, alias, payload)
            try:
                connection.execute(
                    f'CREATE TABLE "{alias}" AS SELECT * FROM read_json_auto(?)',
                    [str(snapshot_path)],
                )
            except duckdb.Error as mount_error:
                return (
                    f"{FETCH_ERROR_PREFIX}: 回應不是可解析的 JSON 表格({mount_error})。"
                    "請確認參數正確;若持續失敗請如實告知使用者。"
                )
            row_count = connection.execute(f'SELECT COUNT(*) FROM "{alias}"').fetchone()[0]
            if row_count > definition.limits.max_rows:
                connection.execute(f'DROP TABLE "{alias}"')
                snapshot_path.unlink()
                return (
                    f"{FETCH_ERROR_PREFIX}: 回應 {row_count} 列超過上限"
                    f"({definition.limits.max_rows})——請縮小查詢範圍(如日期區間)。"
                )
            record_fetch(workspace, alias, connector, params)
            fetch_count["used"] += 1
            schema_rows = (
                connection.cursor()
                .execute(
                    "SELECT column_name, data_type FROM information_schema.columns "
                    "WHERE table_name = ? ORDER BY ordinal_position",
                    [alias],
                )
                .fetchall()
            )
            sample_rows = connection.execute(f'SELECT * FROM "{alias}" LIMIT 3').fetchall()
        schema_line = ", ".join(f"{name} {dtype}" for name, dtype in schema_rows)
        empty_note = "\n(0 rows——資料為空,請如實告知使用者,勿臆測內容)" if row_count == 0 else ""
        sample_markdown = _render_markdown(
            [name for name, _ in schema_rows], [list(row) for row in sample_rows], truncated=False
        )
        return (
            f"table {alias} mounted ({row_count} rows)\n"
            + frame_data_content(f"schema: {schema_line}\n{sample_markdown}")
            + empty_note
        )
```

`_validate_fetch_params(definition, params, mounted)`（同檔 helper，回 None 或錯誤字串）：
1. 未知參數名 → 列出合法參數；2. 必填缺 → 指名參數與型別；3. 型別轉換失敗（date 用 `datetime.date.fromisoformat`）→ 給格式範例；4. `validate_against`：lookup alias 未在 `mounted` → 「請先 `fetch_api_data({lookup})` 取得合法值」；已掛但值不存在（`SELECT COUNT(*) WHERE column = ?`）→ 「{value!r} 不存在，最接近：{`_nearest_candidates(...)`}」。**注意**：validate_against 的 lookup 表名以「connector 名＝掛載 alias」約定比對——lookup 的掛載 alias 由 prompt 規範「lookup 一律用 connector 名當 alias」（Task 6 prompt 寫入此約定），驗證時若找不到同名表再掃 `load_fetch_records` 反查實際 alias。

新 import（最後補）：`from app.engine.api_fetch import ConnectorFetchError, FETCH_ERROR_PREFIX, execute_fetch, land_snapshot, load_fetch_records, record_fetch`、`from app.engine.connectors import SAFE_IDENTIFIER_PATTERN, ConnectorRegistry`。

- [x] **Step 3: 全套＋ruff → commit**

```bash
git add deepagent-service/app/agent/tools/data.py deepagent-service/tests/
git commit -m "feat(deepagent): fetch_api_data 工具——驗證/執行/掛載/sentinel 回報,退貨訊息一律帶指引"
```

---

### Task 5: 跨 turn snapshot 掛載與 manifest 整合

**Files:**
- Modify: `deepagent-service/app/agent/chat_turn.py`（`__aenter__` 內組 sources 後、開連線前）
- Modify: `deepagent-service/app/engine/source_manifest.py`（`build_manifest` 納入 api snapshot 條目）
- Test: `deepagent-service/tests/test_chat.py` 或 `tests/test_graph.py` 追加（照既有 e2e 構造）

**Interfaces:**
- Consumes: Task 2/3 產物
- Produces: 下一輪連線開啟時，`api_snapshots/*.json`（排除 `fetches.json`）以檔名為 alias、file_type="json" 加入 sources 掛載；manifest 對 snapshot 檔記 version（檔案 sha256 經 `opaque_version_id`），重抓後 diff 產生既有的「來源已變」提示

- [x] **Step 1: 寫 failing test**

```python
def test_chat_turn_remountsApiSnapshots_acrossTurns(...):
    # turn1 workspace 預放 api_snapshots/yield_data.json(手工寫,模擬前輪 fetch)
    # 開新 ChatTurn(同 session) → 斷言 connection 可 SELECT * FROM yield_data
    # 且 get_schema 輸出含 yield_data

def test_build_manifest_includesApiSnapshots_versionChangesOnRewrite(...):
    # manifest 含 alias yield_data;覆寫 snapshot 內容後重建 manifest → version 不同
    # → diff_manifests 產生 version_changed 條目
```

- [x] **Step 2: 實作**

`chat_turn.py` 組 sources 處（`open_locked_connection` 呼叫前）：

```python
        # 前輪 fetch 落的 API snapshot 以一般 source 掛回——alias=檔名,json reader;
        # fetches.json 是記錄檔不掛載。
        api_snapshot_sources = [
            Source(snapshot_path.stem, str(snapshot_path), "json")
            for snapshot_path in sorted(self._workspace.api_snapshots_dir.glob("*.json"))
            if snapshot_path.name != "fetches.json"
        ]
        self._connection = open_locked_connection(
            [...既有 uploaded sources...] + api_snapshot_sources,
            api_snapshots_dir=self._workspace.api_snapshots_dir,
        )
```

（`api_snapshots_dir` 參數無論有無 connector 設定都傳——目錄不存在或空時行為與現況一致，Task 2 的 `noSnapshotDir` 測試已釘。若 `prepare_local_layout` 已 mkdir 則恆存在，白名單多放行一個空目錄無安全差異。）

`source_manifest.py`：`build_manifest` 的呼叫端（chat_turn）把 api snapshot 的 `(alias, path)` 一併傳入既有參數；version token 部分——讀 `build_manifest` 現行 version 來源，若以路徑/mtime 為 token，snapshot 條目改用檔案 sha256 前 16 碼經 `opaque_version_id`（重抓同名檔路徑不變、內容才變，路徑 token 偵測不到覆寫）。以現檔實作為準做最小整合，diff/提示機制零改動。

- [x] **Step 3: 全套＋ruff → commit**

```bash
git add deepagent-service/app/agent/chat_turn.py deepagent-service/app/engine/source_manifest.py deepagent-service/tests/
git commit -m "feat(deepagent): api snapshot 跨 turn 掛回+manifest 版本納管——重抓觸發既有換源提示"
```

---

### Task 6: Prompt 生成與 graph 接線

**Files:**
- Modify: `deepagent-service/app/agent/prompts.py`（加 `build_connector_prompt_section(registry) -> str`）
- Modify: `deepagent-service/app/agent/graph.py`（load registry、傳給 `build_data_tools`、prompt 拼接）
- Modify: `deepagent-service/app/agent/events.py`（`step_title_for` 加 `fetch_api_data` → `"取得 API 資料"`）
- Test: `deepagent-service/tests/test_prompts.py`、`tests/test_events.py` 追加

**Interfaces:**
- Consumes: Task 1 registry、Task 4 工具
- Produces: registry 非空時 SYSTEM_PROMPT 附加 connector 段；空時 prompt byte-identical（不變式）

- [x] **Step 1: 寫 failing tests**

```python
def test_build_connector_prompt_section_emptyRegistry_returnsEmptyString(...):
def test_build_connector_prompt_section_listsDataConnectorsWithLookupPointers(...):
    # 段落含 "mes_yield"、"line_id(值來自 line_list)"、lookup 分組、
    # 意圖解析三步遞減規則、選項三帶(≤10/11–200/>200)、
    # 「lookup 一律用 connector 名作 alias」約定、每 turn fetch 上限
def test_step_title_for_fetchApiData_returnsTitle(...):
    # == "取得 API 資料"
```

- [x] **Step 2: 實作**

`build_connector_prompt_section(registry)`：空 registry 回 `""`；否則生成（英文，與 SYSTEM_PROMPT 語言一致；規則文字如下要點，行文照 prompts.py 既有密度）：

```
Available API data sources（由 config 生成）:
- data 類逐條: name(description): 參數列表,validate_against 的參數標注 "values from {lookup}"
- lookup 類逐條: name(description) — option/mapping tables
規則:
- Parameter resolution, in order: (1) infer from conversation(相對日期換算成絕對);
  (2) partial hints → fetch the lookup, narrow with run_sql, ask ONLY the residue;
  (3) no hints → ask open-ended(validation will catch invalid values)。
  NEVER ask for a parameter you can infer.
- Options: ≤10 enumerate as choices; 11–200 → run_sql the list(shown to the user as a
  table) and ask open-ended; >200 → ask for a keyword first. NEVER enumerate >10.
- Always mount lookups with alias = connector name. Data fetches: pick a short
  snake_case alias.
- At most 6 fetches per turn.
```

`graph.py`：`build_agent` 內 `registry = load_connector_registry(Path(settings.AGENT_CONNECTORS_FILE) if settings.AGENT_CONNECTORS_FILE else None)`；`build_data_tools(connection, workspace, recorder, connectors=registry if not registry.is_empty() else None)`；`system_prompt=SYSTEM_PROMPT + build_connector_prompt_section(registry)`。（settings import 照 graph.py 既有取得方式；registry 每 request load 一次，config 檔小、可接受，不做快取。）

- [x] **Step 3: 全套＋ruff → commit**

```bash
git add deepagent-service/app/agent/prompts.py deepagent-service/app/agent/graph.py deepagent-service/app/agent/events.py deepagent-service/tests/
git commit -m "feat(deepagent): connector prompt 段由 config 生成+graph 接線——空 config 即 prompt/工具零變化"
```

---

### Task 7: 敘事綁定——skill 規範＋resolver 注入

**Files:**
- Modify: `deepagent-service/skills/dashboard/SKILL.md`（新增敘事三層規範段）
- Create: `deepagent-service/app/engine/narrative_bind.py`
- Modify: `deepagent-service/app/agent/chat_turn.py`（finalize：`apply_erd_theme` 之後、`inject_results` 之前插 `inject_bind_resolver`）
- Test: `deepagent-service/tests/test_narrative_bind.py`（新檔）＋ `tests/test_dashboard_skill.py`（若有規範性斷言則追加）

**Interfaces:**
- Produces: `inject_bind_resolver(html: str) -> str`（冪等；resolver script 包在 `inject_results` 既有的注入標記區塊內，`strip_injected_blocks` 一併剝除——確認 `app/engine/results.py` 的標記機制後對齊，若標記僅屬 results 區塊則 resolver 用同款自有標記對）
- skill 產出契約：`data-bind="qN.column"`（取該查詢第一列該欄）、`data-bind-row="qN:k"` 可選列索引；自由洞察標 `data-erd-narrative`

- [x] **Step 1: 寫 failing tests**

```python
def test_inject_bind_resolver_fillsSpanFromResults(...):
    # html 含 <span data-bind="q1.worst_tool"></span> + __ERD_RESULTS__ 注入 q1
    # → 注入 resolver 後以無頭方式驗證?(不引入瀏覽器依賴——改為斷言 resolver script
    #    的關鍵行為片段存在:querySelectorAll('[data-bind]')、path 解析、"—" fallback)
def test_inject_bind_resolver_idempotent_secondCallNoDuplicate(...):
def test_inject_bind_resolver_strippedBy_strip_injected_blocks(...):
    # 與 results.py 的 strip 機制往返:注入→strip→html 回到未注入狀態
```

（不引入瀏覽器測試依賴：resolver 的 JS 行為以「腳本內容斷言＋strip 往返」釘住；真實渲染由既有瀏覽器修復迴路與人工驗收覆蓋。）

- [x] **Step 2: 實作 `narrative_bind.py`**

```python
"""敘事綁定 resolver 的確定性注入——填 [data-bind] 的值來自 __ERD_RESULTS__,
路徑無效顯示「—」;與 inject_results 同層後處理,注入區塊可被 strip 剝除。"""

_RESOLVER_MARKER_START = "<!--erd-bind-resolver-->"
_RESOLVER_MARKER_END = "<!--/erd-bind-resolver-->"

_RESOLVER_SCRIPT = """\
<script>
(function () {
  function resolveBind(path) {
    var parts = path.split(".");
    var results = window.__ERD_RESULTS__ || {};
    var query = results[parts[0]];
    if (!query || !query.rows || !query.rows.length) return null;
    var columnIndex = query.columns.indexOf(parts[1]);
    if (columnIndex < 0) return null;
    return query.rows[0][columnIndex];
  }
  document.querySelectorAll("[data-bind]").forEach(function (element) {
    var value = resolveBind(element.getAttribute("data-bind"));
    element.textContent = value === null || value === undefined ? "—" : String(value);
  });
})();
</script>"""


def inject_bind_resolver(html: str) -> str:
    if _RESOLVER_MARKER_START in html:
        return html
    block = f"{_RESOLVER_MARKER_START}\n{_RESOLVER_SCRIPT}\n{_RESOLVER_MARKER_END}"
    if "</body>" in html:
        return html.replace("</body>", f"{block}\n</body>", 1)
    return html + block
```

（`__ERD_RESULTS__` 的實際 JSON 形狀——`columns`/`rows` 鍵名——以 `app/engine/results.py` 的 `inject_results` 落地格式為準，實作前先讀該檔對齊；resolver 必須在 results script **之後**執行——注入順序由 finalize 的呼叫順序保證：resolver block 置於 body 末端、results 注入亦在末端時，確認 resolver 在後（必要時 finalize 改為先 inject_results 再 inject_bind_resolver）。`strip_injected_blocks` 若為通用標記機制則直接沿用其標記格式取代自有 marker。）

- [x] **Step 3: SKILL.md 新增段（放交付規則之後，行文照 SKILL.md 既有密度）**

要點（實作時據此撰文，逐字契約含範例）：

```
Narrative rules (three tiers):
1. Facts(numbers, rankings, subject names) MUST be bound, never typed literally:
   write a backing query first(e.g. q9: worst tool by cpk), then
   <span data-bind="q9.tool"></span>(Cpk=<span data-bind="q9.cpk"></span>)。
   The harness fills [data-bind] from __ERD_RESULTS__ at render time.
2. Judgements(嚴重不足/尚可/良好) MUST use data-driven thresholds in inline JS,
   following the grading tables below — never hardcode the judged text.
   [內建 SPC 分級表:Cpk<1.0 嚴重不足(red)/1.0–1.33 尚可(amber)/≥1.33 良好(green);
    其他領域由模型自訂門檻但 MUST 寫成條件式]
3. Free-form insights that cannot be computed(pattern speculation, causal guesses)
   are allowed but MUST carry data-erd-narrative on the element。
NEVER write a literal number in narrative text that exists in query results。
```

- [x] **Step 4: finalize 接線＋全套＋ruff → commit**

```bash
git add deepagent-service/app/engine/narrative_bind.py deepagent-service/skills/dashboard/SKILL.md deepagent-service/app/agent/chat_turn.py deepagent-service/tests/
git commit -m "feat(deepagent): 敘事綁定——skill 三層規範+bind resolver 確定性注入,敘事數字不再經模型抄寫"
```

---

### Task 8: e2e 驗收＋文件

**Files:**
- Create: `deepagent-service/tests/test_api_connector_e2e.py`
- Modify: `CLAUDE.md`（專案脈絡的 Artifact 契約/檔案段補 connector 一句；行文照既有密度）

**Interfaces:** Consumes 全部。

- [x] **Step 1: e2e（ScriptedChatModel＋monkeypatch `execute_fetch`，照 `tests/test_chat.py` 既有 e2e 構造）**

```python
def test_connector_flow_lookupNarrowAskThenFetchAndDashboard(...):
    # 腳本:fetch(line_list) → run_sql 窄化 → 問題區塊(QuestionEvent) [turn1]
    #       turn2: fetch(mes_yield) → run_sql → write_file dashboard.html(含 data-bind span)
    # 斷言:兩輪 STEP 含「取得 API 資料」;turn1 出 QUESTION;turn2 出 DASHBOARD_HTML;
    #       最終 HTML 含 bind resolver 注入區塊;fetches.json 兩筆;
    #       snapshot 檔存在且 turn2 連線可查 turn1 落的 lookup 表

def test_connector_feature_off_noConfigMeansNoChanges(...):
    # AGENT_CONNECTORS_FILE 未設 → build_agent 的工具集無 fetch_api_data、
    # system prompt 與現況相同(功能關閉不變式,合併安全的核心保證)
```

- [x] **Step 2: 全套驗收**

Run: `cd deepagent-service && uv run pytest tests/ -q && uv run ruff check .`
Expected: 全綠、零 lint

- [x] **Step 3: CLAUDE.md 更新＋commit**

```bash
git add deepagent-service/tests/test_api_connector_e2e.py CLAUDE.md
git commit -m "test(deepagent): connector e2e——lookup 窄化反問/跨 turn 掛回/功能關閉不變式三場景"
```

---

## 驗收與收尾（主迴圈執行）

- 全套測試＋ruff 終驗；opus 全分支終審 → PR（終審結論入描述）
- ledger 記帳：connector Phase 1 上線、`fetches.json`＝Phase 2 recipe 材料、allowed_directories spike 結論、敘事綁定 skill 版本
- Phase 2 預告（不在本 plan）：recipe 組裝＋`/replay`＋owner refresh；Phase 3：share 網域＋user-token
- 部署備忘：internal 掛 `AGENT_CONNECTORS_FILE`＋各 connector 的 token env；不設＝功能關閉
