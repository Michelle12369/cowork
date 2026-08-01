# Data Insight Agent M1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 最薄端到端——使用者在既有 chat 上傳 CSV 後,由 LangGraph agent 對 DuckDB 跑 SQL 分析並串流回答;前端零改動。

**Architecture:** 新增 Python FastAPI 服務(`agent-service/`)承載 LangGraph `create_agent` ReAct loop 與 DuckDB(先 materialize 後鎖門);Java 端新增 `LangGraphAnalysisProvider` 實作既有 `DashboardAgentProvider` SPI,把 Python 的 SSE 事件翻譯成 `AgentEvent`。spec 見 `docs/superpowers/specs/2026-07-24-data-insight-agent-design.md`。

**Tech Stack:** Python 3.11+ / uv / Ruff / FastAPI(fastapi[standard], fastapi.sse)/ langchain≥1.0 / langchain-openai / langgraph / duckdb / pytest / httpx;Java 17 / Spring Boot(既有)/ Reactor WebClient。

## Global Constraints

- Java 17(NEVER 用 18+ API);constructor injection;`@RequiredArgsConstructor`;DTO 一律 record
- 變數/參數 NEVER 1–2 字元名稱;描述性單詞
- Secrets NEVER 進 yml/properties;一律 env vars
- 測試命名 `methodName_condition_expectedBehavior`;PR 前 `./mvnw test` 全綠
- google-java-format 由 hook 自動執行,勿手動改格式
- Config binding 用 `@ConfigurationProperties`;NEVER hardcode URL
- Python 服務內:所有函式有 type hints;DuckDB 連線遵守「先掛資料、後鎖門」(spec §6)
- FastAPI 程式 MUST 遵循 `.claude/skills/fastapi/SKILL.md`(官方 skill):SSE 用 `fastapi.sse.EventSourceResponse` + `ServerSentEvent`(不手組 `data:` 字串);參數/依賴一律 `Annotated`;實作前先讀該 skill 與 `references/streaming.md`
- Python 工具鏈用 **uv**(依賴管理,`pyproject.toml` + `uv.lock` 進版控)與 **Ruff**(lint,`uv run ruff check .` 通過才 commit);所有 Python 指令一律 `uv run <cmd>`
- LLM endpoint 一律 OpenAI-compatible,base url/key 從 env(`OPENAI_BASE_URL`/`OPENAI_API_KEY`)

---

### Task 1: agent-service 骨架 + /health

**Files:**
- Create: `agent-service/pyproject.toml`
- Create: `agent-service/app/__init__.py`(空檔)
- Create: `agent-service/app/main.py`
- Create: `agent-service/tests/__init__.py`(空檔)
- Test: `agent-service/tests/test_health.py`

**Interfaces:**
- Produces: FastAPI app 物件 `app.main:app`;`GET /health` → `{"status":"ok"}`(Task 7 的 compose healthcheck 用);`uv.lock`(commit 進版控)

- [ ] **Step 1: 建立 pyproject 與環境(uv)**

```toml
# agent-service/pyproject.toml
[project]
name = "agent-service"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "fastapi[standard]>=0.140",   # [standard] 含 fastapi CLI 與 uvicorn;>=0.140 才有 fastapi.sse
    "langchain>=1.0",
    "langchain-openai>=1.0",
    "langgraph>=1.0",
    "duckdb>=1.2",
    "langfuse>=3.0",              # tracing;未設 LANGFUSE_* env 時為 no-op(spec §14)
]

[dependency-groups]
dev = [
    "pytest>=8.0",
    "httpx>=0.27",
    "ruff>=0.8",
]

[tool.fastapi]
entrypoint = "app.main:app"

[tool.ruff]
line-length = 100

[tool.pytest.ini_options]
testpaths = ["tests"]
```

Run: `cd agent-service && uv sync`
Expected: 建立 `.venv/` 並產生 `uv.lock`(lock 檔 commit,`.venv/` 加入 `.gitignore`)

- [ ] **Step 2: Write the failing test**

```python
# agent-service/tests/test_health.py
from fastapi.testclient import TestClient

from app.main import app


def test_health_returns_ok() -> None:
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd agent-service && uv run pytest tests/test_health.py -v`
Expected: FAIL(`ModuleNotFoundError: app.main`)

- [ ] **Step 4: Write minimal implementation**

```python
# agent-service/app/main.py
from fastapi import FastAPI

app = FastAPI(title="erd-cowork agent service")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd agent-service && uv run pytest tests/test_health.py -v`
Expected: PASS

- [ ] **Step 6: Lint**

Run: `cd agent-service && uv run ruff check .`
Expected: All checks passed

- [ ] **Step 7: Commit(含 uv.lock 與 .gitignore)**

```bash
echo ".venv/" > agent-service/.gitignore
git add agent-service
git commit -m "feat(agent-service): FastAPI scaffold with /health (uv + ruff toolchain)"
```

---

### Task 2: DuckDB 鎖門連線工廠

**Files:**
- Create: `agent-service/app/duck.py`
- Test: `agent-service/tests/test_duck.py`

**Interfaces:**
- Produces: `Source`(dataclass:`alias: str`,`path: str`,`file_type: str` — `"csv"` 或 `"parquet"`;`path` 為本地路徑**或** `s3://bucket/key` URL);`open_locked_connection(sources: list[Source], memory_limit: str = "2GB") -> duckdb.DuckDBPyConnection` — 回傳已 materialize 資料表且鎖門完成的 in-memory 連線(Task 3/4 使用)。S3/MinIO 憑證從 env 讀:`AGENT_S3_ENDPOINT`、`AGENT_S3_ACCESS_KEY_ID`、`AGENT_S3_SECRET_ACCESS_KEY`、`AGENT_S3_REGION`(預設 `us-east-1`)、`AGENT_S3_USE_SSL`(預設 `false`,MinIO 本機)

- [ ] **Step 1: Write the failing test**

```python
# agent-service/tests/test_duck.py
import csv
from pathlib import Path

import duckdb
import pytest

from app.duck import Source, open_locked_connection


@pytest.fixture()
def sample_csv(tmp_path: Path) -> Path:
    file_path = tmp_path / "defects.csv"
    with file_path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["machine_id", "defect_rate"])
        writer.writerow(["M1", "0.02"])
        writer.writerow(["M2", "0.05"])
    return file_path


def test_open_locked_connection_mounted_table_queryable(sample_csv: Path) -> None:
    connection = open_locked_connection([Source(alias="defects", path=str(sample_csv), file_type="csv")])
    rows = connection.execute("SELECT count(*) FROM defects").fetchone()
    assert rows == (2,)


def test_open_locked_connection_external_read_blocked(sample_csv: Path) -> None:
    connection = open_locked_connection([Source(alias="defects", path=str(sample_csv), file_type="csv")])
    with pytest.raises(duckdb.Error):
        connection.execute(f"SELECT * FROM read_csv_auto('{sample_csv}')").fetchall()


def test_open_locked_connection_configuration_locked(sample_csv: Path) -> None:
    connection = open_locked_connection([Source(alias="defects", path=str(sample_csv), file_type="csv")])
    with pytest.raises(duckdb.Error):
        connection.execute("SET enable_external_access = true")


def test_configure_s3_applies_endpoint_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.duck import _configure_s3

    monkeypatch.setenv("AGENT_S3_ENDPOINT", "minio:9000")
    monkeypatch.setenv("AGENT_S3_ACCESS_KEY_ID", "minioadmin")
    monkeypatch.setenv("AGENT_S3_SECRET_ACCESS_KEY", "minioadmin")
    connection = duckdb.connect(":memory:")
    _configure_s3(connection)
    endpoint = connection.execute("SELECT current_setting('s3_endpoint')").fetchone()
    assert endpoint == ("minio:9000",)
    url_style = connection.execute("SELECT current_setting('s3_url_style')").fetchone()
    assert url_style == ("path",)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd agent-service && uv run pytest tests/test_duck.py -v`
Expected: FAIL(`ModuleNotFoundError: app.duck`)

- [ ] **Step 3: Write implementation**

```python
# agent-service/app/duck.py
import os
from dataclasses import dataclass

import duckdb

_READERS = {"csv": "read_csv_auto", "parquet": "read_parquet"}


@dataclass(frozen=True)
class Source:
    alias: str
    path: str  # 本地路徑或 s3://bucket/key(MinIO/S3;由 Java 端依 erd.storage.type 決定)
    file_type: str


def _configure_s3(connection: duckdb.DuckDBPyConnection) -> None:
    """MinIO/S3 存取設定(httpfs extension)。只在有 s3:// 來源時呼叫;必須在鎖門前完成。"""
    connection.execute("INSTALL httpfs; LOAD httpfs;")
    connection.execute(f"SET s3_endpoint = '{os.environ['AGENT_S3_ENDPOINT']}'")
    connection.execute(f"SET s3_access_key_id = '{os.environ['AGENT_S3_ACCESS_KEY_ID']}'")
    connection.execute(f"SET s3_secret_access_key = '{os.environ['AGENT_S3_SECRET_ACCESS_KEY']}'")
    connection.execute(f"SET s3_region = '{os.environ.get('AGENT_S3_REGION', 'us-east-1')}'")
    connection.execute("SET s3_url_style = 'path'")  # MinIO 需要 path-style
    use_ssl = os.environ.get("AGENT_S3_USE_SSL", "false").lower() == "true"
    connection.execute(f"SET s3_use_ssl = {'true' if use_ssl else 'false'}")


def open_locked_connection(
    sources: list[Source], memory_limit: str = "2GB"
) -> duckdb.DuckDBPyConnection:
    """spec §6:先掛資料(materialize)、後鎖門。回傳的連線上任何 SQL 都無法再碰檔案系統/網路。

    materialize(CREATE TABLE AS)發生在鎖門前,所以 s3:// 讀取只存在於這個窗口;
    鎖門後連 httpfs 也一併被 enable_external_access=false 封住。
    """
    connection = duckdb.connect(":memory:")
    if any(source.path.startswith("s3://") for source in sources):
        _configure_s3(connection)
    for source in sources:
        reader = _READERS.get(source.file_type)
        if reader is None:
            raise ValueError(f"unsupported file type: {source.file_type}")
        connection.execute(
            f'CREATE TABLE "{source.alias}" AS SELECT * FROM {reader}(?)', [source.path]
        )
    connection.execute("SET enable_external_access = false")
    connection.execute(f"SET memory_limit = '{memory_limit}'")
    connection.execute("SET threads = 2")
    connection.execute("SET lock_configuration = true")
    return connection
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd agent-service && uv run pytest tests/test_duck.py -v`
Expected: PASS ×3

- [ ] **Step 5: Commit**

```bash
git add agent-service/app/duck.py agent-service/tests/test_duck.py
git commit -m "feat(agent-service): DuckDB locked connection factory (mount then lockdown)"
```

---

### Task 3: 分析工具 get_schema / run_sql

**Files:**
- Create: `agent-service/app/tools.py`
- Test: `agent-service/tests/test_tools.py`

**Interfaces:**
- Consumes: Task 2 的 `open_locked_connection` / `Source`
- Produces: `get_schema(connection) -> str`(所有表的欄位/型別描述);`run_sql(connection, sql: str) -> str` — 成功回傳 markdown 表格(截斷至 `MAX_RESULT_ROWS = 200` 列並標注),失敗回傳以 `SQL_ERROR:` 開頭的錯誤訊息字串(供 LLM 自我修復;絕不拋例外)。Task 4 將兩者包成 LangChain tools。

- [ ] **Step 1: Write the failing test**

```python
# agent-service/tests/test_tools.py
import csv
from pathlib import Path

import pytest

from app.duck import Source, open_locked_connection
from app.tools import get_schema, run_sql


@pytest.fixture()
def connection(tmp_path: Path):
    file_path = tmp_path / "defects.csv"
    with file_path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["machine_id", "defect_rate"])
        for index in range(300):
            writer.writerow([f"M{index}", "0.01"])
    return open_locked_connection([Source(alias="defects", path=str(file_path), file_type="csv")])


def test_get_schema_lists_table_and_columns(connection) -> None:
    schema_text = get_schema(connection)
    assert "defects" in schema_text
    assert "machine_id" in schema_text
    assert "defect_rate" in schema_text


def test_run_sql_valid_query_returns_markdown(connection) -> None:
    result = run_sql(connection, "SELECT count(*) AS total FROM defects")
    assert "total" in result
    assert "300" in result


def test_run_sql_truncates_large_result(connection) -> None:
    result = run_sql(connection, "SELECT * FROM defects")
    assert "truncated to 200 rows" in result


def test_run_sql_invalid_sql_returns_error_string(connection) -> None:
    result = run_sql(connection, "SELECT nope FROM defects")
    assert result.startswith("SQL_ERROR:")
    assert "nope" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd agent-service && uv run pytest tests/test_tools.py -v`
Expected: FAIL(`ModuleNotFoundError: app.tools`)

- [ ] **Step 3: Write implementation**

```python
# agent-service/app/tools.py
import duckdb

MAX_RESULT_ROWS = 200


def get_schema(connection: duckdb.DuckDBPyConnection) -> str:
    tables = connection.execute("SELECT table_name FROM information_schema.tables").fetchall()
    lines: list[str] = []
    for (table_name,) in tables:
        columns = connection.execute(
            "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = ?",
            [table_name],
        ).fetchall()
        column_text = ", ".join(f"{name} {dtype}" for name, dtype in columns)
        lines.append(f"table {table_name}: {column_text}")
    return "\n".join(lines)


def run_sql(connection: duckdb.DuckDBPyConnection, sql: str) -> str:
    try:
        cursor = connection.execute(sql)
        column_names = [description[0] for description in cursor.description]
        rows = cursor.fetchmany(MAX_RESULT_ROWS + 1)
    except duckdb.Error as error:
        return f"SQL_ERROR: {error}"
    truncated = len(rows) > MAX_RESULT_ROWS
    rows = rows[:MAX_RESULT_ROWS]
    header = "| " + " | ".join(column_names) + " |"
    divider = "| " + " | ".join("---" for _ in column_names) + " |"
    body = ["| " + " | ".join(str(value) for value in row) + " |" for row in rows]
    table = "\n".join([header, divider, *body])
    if truncated:
        table += f"\n(truncated to {MAX_RESULT_ROWS} rows)"
    return table
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd agent-service && uv run pytest tests/test_tools.py -v`
Expected: PASS ×4

- [ ] **Step 5: Commit**

```bash
git add agent-service/app/tools.py agent-service/tests/test_tools.py
git commit -m "feat(agent-service): get_schema and run_sql tools with error feedback and truncation"
```

---

### Task 4: /chat SSE 端點(LangGraph agent)

**Files:**
- Create: `agent-service/app/agent.py`
- Modify: `agent-service/app/main.py`
- Test: `agent-service/tests/test_chat.py`

**Interfaces:**
- Consumes: Task 2 `open_locked_connection`/`Source`、Task 3 `get_schema`/`run_sql`
- Produces: `POST /chat` body `{"sessionId": str, "userId": str, "message": str, "history": [{"role": "user"|"assistant", "text": str}], "sources": [{"alias": str, "path": str, "fileType": str}]}` → SSE(`text/event-stream`),每個事件 `data: <json>`,json 為 `{"type":"STEP","stepKey":str,"title":str}` / `{"type":"TOKEN","delta":str}` / `{"type":"ANSWER","text":str}` / `{"type":"ERROR","code":str,"message":str}`(欄位名對齊 Java `AgentEvent` records,Task 5 直接反序列化);`build_model()` 工廠函式(測試以 monkeypatch 換成 fake);`build_callbacks(session_id, user_id)` — 有 `LANGFUSE_PUBLIC_KEY` 時回傳 Langfuse `CallbackHandler`,否則空 list(spec §14)

- [ ] **Step 1: Write the failing test**

```python
# agent-service/tests/test_chat.py
import csv
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

import app.main as main_module
from app.main import app


class FakeToolCallingModel(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):  # fake 忽略工具綁定
        return self


def scripted_messages() -> Iterator[AIMessage]:
    yield AIMessage(
        content="",
        tool_calls=[{"name": "run_sql", "args": {"sql": "SELECT count(*) AS total FROM defects"}, "id": "call_1"}],
    )
    yield AIMessage(content="共有 2 筆資料。")


@pytest.fixture()
def sample_csv(tmp_path: Path) -> Path:
    file_path = tmp_path / "defects.csv"
    with file_path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["machine_id", "defect_rate"])
        writer.writerow(["M1", "0.02"])
        writer.writerow(["M2", "0.05"])
    return file_path


def test_chat_streams_answer_event(monkeypatch: pytest.MonkeyPatch, sample_csv: Path) -> None:
    monkeypatch.setattr(
        main_module, "build_model", lambda: FakeToolCallingModel(messages=scripted_messages())
    )
    client = TestClient(app)
    payload = {
        "sessionId": "s1",
        "userId": "u1",
        "message": "有幾筆資料?",
        "history": [],
        "sources": [{"alias": "defects", "path": str(sample_csv), "fileType": "csv"}],
    }
    with client.stream("POST", "/chat", json=payload) as response:
        assert response.status_code == 200
        events = [
            json.loads(line[len("data: "):])
            for line in response.iter_lines()
            if line.startswith("data: ")
        ]
    types = [event["type"] for event in events]
    assert "STEP" in types
    assert types[-1] == "ANSWER"
    answer = events[-1]
    assert "2" in answer["text"] or "筆" in answer["text"]


def test_build_callbacks_without_langfuse_env_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    assert main_module.build_callbacks("s1", "u1") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent-service && uv run pytest tests/test_chat.py -v`
Expected: FAIL(`ImportError: build_model`)

- [ ] **Step 3: Write agent module**

```python
# agent-service/app/agent.py
from typing import Any

import duckdb
from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import tool

from app.tools import get_schema, run_sql

SYSTEM_PROMPT = (
    "You are a data analyst agent. Data sources are mounted as DuckDB tables. "
    "Inspect schemas with get_schema before querying. Use run_sql (DuckDB SQL dialect) "
    "for all computation — never compute numbers yourself. If run_sql returns SQL_ERROR, "
    "fix the SQL and retry (at most 3 attempts). Answer in the user's language and, when "
    "you present query results, restate in one sentence what the query did."
)


def build_agent(model: BaseChatModel, connection: duckdb.DuckDBPyConnection) -> Any:
    @tool
    def get_schema_tool() -> str:
        """List every mounted table with its columns and types."""
        return get_schema(connection)

    @tool
    def run_sql_tool(sql: str) -> str:
        """Run a DuckDB SQL query against the mounted tables and return the result."""
        return run_sql(connection, sql)

    return create_agent(model, [get_schema_tool, run_sql_tool], system_prompt=SYSTEM_PROMPT)
```

- [ ] **Step 4: Wire /chat endpoint**

遵循 `.claude/skills/fastapi/SKILL.md`:SSE 用 `fastapi.sse.EventSourceResponse` + `ServerSentEvent`(自動 JSON 序列化 `data:` 欄位,不手組字串)。

```python
# agent-service/app/main.py(整檔改寫)
import os
from collections.abc import AsyncIterable

from fastapi import FastAPI
from fastapi.sse import EventSourceResponse, ServerSentEvent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from app.agent import build_agent
from app.duck import Source, open_locked_connection

app = FastAPI(title="erd-cowork agent service")


class HistoryItem(BaseModel):
    role: str
    text: str


class SourceItem(BaseModel):
    alias: str
    path: str
    fileType: str


class ChatRequest(BaseModel):
    sessionId: str
    userId: str
    message: str
    history: list[HistoryItem] = []
    sources: list[SourceItem] = []


def build_model() -> BaseChatModel:
    return ChatOpenAI(
        model=os.environ.get("AGENT_MODEL", "gpt-oss-120b"),
        base_url=os.environ["OPENAI_BASE_URL"],
        api_key=os.environ.get("OPENAI_API_KEY", "unused"),
        streaming=True,
        temperature=0,
    )


def build_callbacks(session_id: str, user_id: str) -> list:
    """Langfuse tracing(spec §14):未設 LANGFUSE_PUBLIC_KEY 即 no-op,不建 handler。"""
    if not os.environ.get("LANGFUSE_PUBLIC_KEY"):
        return []
    from langfuse.langchain import CallbackHandler

    return [CallbackHandler()]


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/chat", response_class=EventSourceResponse)
async def chat(request: ChatRequest) -> AsyncIterable[ServerSentEvent]:
    connection = None
    try:
        connection = open_locked_connection(
            [Source(alias=item.alias, path=item.path, file_type=item.fileType) for item in request.sources]
        )
        agent = build_agent(build_model(), connection)
        yield ServerSentEvent(data={"type": "STEP", "stepKey": "analysis", "title": "分析資料中"})
        messages: list = [
            HumanMessage(item.text) if item.role == "user" else AIMessage(item.text)
            for item in request.history
        ]
        messages.append(HumanMessage(request.message))
        final_text = ""
        run_config = {
            "callbacks": build_callbacks(request.sessionId, request.userId),
            "metadata": {
                "langfuse_session_id": request.sessionId,
                "langfuse_user_id": request.userId,
            },
        }
        async for chunk, metadata in agent.astream(
            {"messages": messages}, stream_mode="messages", config=run_config
        ):
            if isinstance(chunk, AIMessage) and isinstance(chunk.content, str) and chunk.content:
                final_text += chunk.content
                yield ServerSentEvent(data={"type": "TOKEN", "delta": chunk.content})
        yield ServerSentEvent(data={"type": "ANSWER", "text": final_text.strip()})
    except Exception as error:  # noqa: BLE001 — 任何失敗都必須以 ERROR 事件收尾
        yield ServerSentEvent(data={"type": "ERROR", "code": "AGENT_FAILURE", "message": str(error)})
    finally:
        if connection is not None:
            connection.close()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd agent-service && uv run pytest -v`
Expected: 全部 PASS(health/duck/tools/chat)

- [ ] **Step 6: Commit**

```bash
git add agent-service/app agent-service/tests
git commit -m "feat(agent-service): /chat SSE endpoint with LangGraph create_agent loop"
```

---

### Task 5: AgentFileContext 增加 storageKey

**Files:**
- Modify: `backend/src/main/java/com/erd/cowork/agent/model/AgentFileContext.java`
- Modify: `backend/src/main/java/com/erd/cowork/agent/AgentOrchestrator.java:196-198`
- Modify: `backend/src/main/java/com/erd/cowork/service/ArtifactRepairService.java:171` 附近(同型建構)
- Test: 既有測試套件(編譯期即驗證所有建構點都補上)

**Interfaces:**
- Produces: `AgentFileContext(String alias, String name, String type, String storageKey, FileProfile profile)` — Task 6 的 provider 用 `storageKey` 組出 Python 端可讀的檔案路徑

- [ ] **Step 1: 修改 record**

```java
// backend/src/main/java/com/erd/cowork/agent/model/AgentFileContext.java
package com.erd.cowork.agent.model;

import com.erd.cowork.parsing.model.FileProfile;

public record AgentFileContext(
    String alias, String name, String type, String storageKey, FileProfile profile) {}
```

- [ ] **Step 2: 編譯找出所有建構點並補上 storageKey**

Run: `cd backend && ./mvnw -q compile 2>&1 | head -30`
Expected: `AgentOrchestrator`、`ArtifactRepairService` 與相關測試出現建構子不符的編譯錯誤

`AgentOrchestrator.java:196-198` 改為(來源物件 `uploadedFile` 已有 `getStorageKey()`):

```java
        fileContexts.add(
            new AgentFileContext(
                uploadedFile.getAlias(),
                uploadedFile.getName(),
                uploadedFile.getType(),
                uploadedFile.getStorageKey(),
                profile));
```

`ArtifactRepairService.java:171` 附近同型修改:在 `type` 參數後補上該處來源物件的 `getStorageKey()`;測試 fixture 中的建構呼叫在 `type` 後補 `"storage/key/test.csv"` 字面值。

- [ ] **Step 3: 跑全部後端測試**

Run: `cd backend && ./mvnw -q test`
Expected: BUILD SUCCESS,全綠

- [ ] **Step 4: Commit**

```bash
git add backend
git commit -m "feat(backend): carry storageKey in AgentFileContext for analysis provider"
```

---

### Task 6: LangGraphAnalysisProvider(Java)

**Files:**
- Create: `backend/src/main/java/com/erd/cowork/config/AnalysisAgentProperties.java`
- Create: `backend/src/main/java/com/erd/cowork/agent/provider/analysis/LangGraphAnalysisProvider.java`
- Test: `backend/src/test/java/com/erd/cowork/agent/provider/analysis/LangGraphAnalysisProviderTest.java`

**Interfaces:**
- Consumes: `DashboardAgentProvider`(既有 SPI)、`ProviderResult(Flux<AgentEvent> events, Supplier<ExtractionResult> extraction)`、Task 5 的 `AgentFileContext.storageKey()`、Task 4 的 SSE JSON 契約
- Produces: provider id `"langgraph-analysis"`(`erd.agent.provider` 值);`AnalysisAgentProperties(String baseUrl, String sourceRoot)`

- [ ] **Step 1: Properties**

```java
// backend/src/main/java/com/erd/cowork/config/AnalysisAgentProperties.java
package com.erd.cowork.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * @param baseUrl agent-service 位址(如 http://agent-service:8000)
 * @param sourceRoot agent-service 視角的上傳檔根目錄;storageKey 接在其後組成完整路徑
 */
@ConfigurationProperties(prefix = "erd.agent.analysis")
public record AnalysisAgentProperties(String baseUrl, String sourceRoot) {}
```

並在既有 `@ConfigurationPropertiesScan`/`@EnableConfigurationProperties` 掛載點確認會掃到 `com.erd.cowork.config`(既有 Properties 類同包,無需改動即生效;若該包尚無其他 Properties,於主應用類加 `@ConfigurationPropertiesScan`)。

- [ ] **Step 2: Write the failing test(事件翻譯)**

```java
// backend/src/test/java/com/erd/cowork/agent/provider/analysis/LangGraphAnalysisProviderTest.java
package com.erd.cowork.agent.provider.analysis;

import static org.assertj.core.api.Assertions.assertThat;

import com.erd.cowork.agent.event.AnswerEvent;
import com.erd.cowork.agent.event.ErrorEvent;
import com.erd.cowork.agent.event.StepEvent;
import com.erd.cowork.agent.event.TokenEvent;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;

class LangGraphAnalysisProviderTest {

  private final ObjectMapper objectMapper = new ObjectMapper();

  @Test
  void toEvent_tokenPayload_returnsTokenEvent() {
    var event =
        LangGraphAnalysisProvider.toEvent("{\"type\":\"TOKEN\",\"delta\":\"hi\"}", objectMapper);
    assertThat(event).isEqualTo(new TokenEvent("hi"));
  }

  @Test
  void toEvent_stepPayload_returnsStepEvent() {
    var event =
        LangGraphAnalysisProvider.toEvent(
            "{\"type\":\"STEP\",\"stepKey\":\"analysis\",\"title\":\"分析資料中\"}", objectMapper);
    assertThat(event).isInstanceOf(StepEvent.class);
  }

  @Test
  void toEvent_answerPayload_returnsAnswerEvent() {
    var event =
        LangGraphAnalysisProvider.toEvent("{\"type\":\"ANSWER\",\"text\":\"done\"}", objectMapper);
    assertThat(event).isEqualTo(new AnswerEvent("done"));
  }

  @Test
  void toEvent_malformedPayload_returnsErrorEvent() {
    var event = LangGraphAnalysisProvider.toEvent("not-json", objectMapper);
    assertThat(event).isInstanceOf(ErrorEvent.class);
  }
}
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend && ./mvnw -q test -Dtest=LangGraphAnalysisProviderTest`
Expected: 編譯失敗(`LangGraphAnalysisProvider` 不存在)

- [ ] **Step 4: Provider 實作**

```java
// backend/src/main/java/com/erd/cowork/agent/provider/analysis/LangGraphAnalysisProvider.java
package com.erd.cowork.agent.provider.analysis;

import com.erd.cowork.agent.event.AgentEvent;
import com.erd.cowork.agent.event.AnswerEvent;
import com.erd.cowork.agent.event.ErrorEvent;
import com.erd.cowork.agent.extraction.ExtractionResult;
import com.erd.cowork.agent.model.AgentRequest;
import com.erd.cowork.agent.provider.DashboardAgentProvider;
import com.erd.cowork.agent.provider.ProviderResult;
import com.erd.cowork.config.AnalysisAgentProperties;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicReference;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Flux;

@Slf4j
@Component
@RequiredArgsConstructor
@ConditionalOnProperty(name = "erd.agent.provider", havingValue = "langgraph-analysis")
public class LangGraphAnalysisProvider implements DashboardAgentProvider {

  private final AnalysisAgentProperties properties;
  private final StorageProperties storageProperties;
  private final ObjectMapper objectMapper;

  @Override
  public ProviderResult generate(AgentRequest request) {
    log.info(
        "analysis request session={} files={} questionLength={}",
        request.sessionId(),
        request.files().size(),
        request.question().length());
    List<Map<String, String>> sources =
        request.files().stream()
            .map(
                file ->
                    Map.of(
                        "alias", file.alias(),
                        "path", resolveSourcePath(file.storageKey()),
                        "fileType", file.type()))
            .toList();
    List<Map<String, String>> history =
        request.history().stream()
            .map(message -> Map.of("role", message.role(), "text", message.text()))
            .toList();
    Map<String, Object> body =
        Map.of(
            "sessionId", request.sessionId(),
            "userId", request.userId(),
            "message", request.question(),
            "history", history,
            "sources", sources);

    AtomicReference<String> answerText = new AtomicReference<>("");
    Flux<AgentEvent> events =
        WebClient.create(properties.baseUrl())
            .post()
            .uri("/chat")
            .bodyValue(body)
            .retrieve()
            .bodyToFlux(String.class)
            .map(payload -> toEvent(payload, objectMapper))
            .doOnNext(
                event -> {
                  if (event instanceof AnswerEvent answer) answerText.set(answer.text());
                })
            .onErrorResume(
                error -> {
                  log.warn("analysis stream failed session={}", request.sessionId(), error);
                  return Flux.just(new ErrorEvent("ANALYSIS_STREAM", error.getMessage()));
                });
    return new ProviderResult(
        events, () -> new ExtractionResult(answerText.get(), null, null));
  }

  /**
   * 依 erd.storage.type 決定 Python 端拿到的來源路徑形態: local → sourceRoot/storageKey(共享
   * volume 路徑); s3 → s3://bucket/storageKey(agent-service 用 DuckDB httpfs + MinIO 憑證讀取)。
   */
  private String resolveSourcePath(String storageKey) {
    if ("s3".equals(storageProperties.type())) {
      return "s3://" + storageProperties.s3().bucket() + "/" + storageKey;
    }
    return properties.sourceRoot() + "/" + storageKey;
  }

  static AgentEvent toEvent(String payload, ObjectMapper objectMapper) {
    try {
      return objectMapper.readValue(payload, AgentEvent.class);
    } catch (Exception exception) {
      return new ErrorEvent("ANALYSIS_EVENT_PARSE", "unparseable event: " + payload);
    }
  }
}
```

(import 補 `com.erd.cowork.config.StorageProperties`——既有 record:`StorageProperties(String type, String localDir, int retentionDays, S3 s3)`,`S3(String bucket, String region, String endpoint, boolean pathStyleAccess)`。)

測試補一條 `resolveSourcePath` 行為(加入 Step 2 的測試類):

```java
  @Test
  void resolveSourcePath_s3StorageType_buildsS3Url() {
    var provider =
        new LangGraphAnalysisProvider(
            new com.erd.cowork.config.AnalysisAgentProperties("http://localhost:8000", "/data/uploads"),
            new com.erd.cowork.config.StorageProperties(
                "s3", "./data/files", 30,
                new com.erd.cowork.config.StorageProperties.S3("erd-cowork", "us-east-1", "http://minio:9000", true)),
            new ObjectMapper());
    // 以 reflection 或 package-private 存取皆可;最簡:把 resolveSourcePath 改為 package-private 直接呼叫
  }
```

(實作時把 `resolveSourcePath` 宣告為 package-private 以便直接測;斷言 `resolveSourcePath("s1/a.csv")` 回傳 `"s3://erd-cowork/s1/a.csv"`,以及 local type 時回傳 `"/data/uploads/s1/a.csv"`。)

注意:`AgentEvent` 已有 `@JsonTypeInfo(property = "type")` 多型設定,Python 端 JSON 欄位名(`delta`/`text`/`stepKey`…)即為 record 欄位名,`objectMapper.readValue` 直接反序列化;`HistoryMessage` 的存取器名稱以實際 record 為準(若為 `sender()`/`content()` 則對應調整,編譯器會指出)。

- [ ] **Step 5: Run tests**

Run: `cd backend && ./mvnw -q test -Dtest=LangGraphAnalysisProviderTest`
Expected: PASS ×4

- [ ] **Step 6: 跑全部後端測試 + Commit**

Run: `cd backend && ./mvnw -q test`
Expected: 全綠

```bash
git add backend
git commit -m "feat(backend): LangGraphAnalysisProvider bridging agent-service SSE to AgentEvent"
```

---

### Task 7: 設定與 docker compose 接線 + 端到端煙霧測試

**Files:**
- Modify: `backend/src/main/resources/application.yml`(`erd.agent` 區塊,47 行附近)
- Create: `agent-service/Dockerfile`
- Modify: `docker-compose.yml`(services 區塊)

**Interfaces:**
- Consumes: Task 6 的 `AnalysisAgentProperties`(`erd.agent.analysis.base-url` / `erd.agent.analysis.source-root`)、Task 1 的 `/health`

- [ ] **Step 1: application.yml 增加設定**

在既有 `erd.agent.provider` 下方加:

```yaml
    analysis:
      base-url: ${ANALYSIS_AGENT_URL:http://localhost:8000}
      source-root: ${ANALYSIS_SOURCE_ROOT:${ERD_UPLOAD_DIR:./uploads}}
```

(`source-root` 的預設對齊 LocalDiskStorage 實際根目錄;以 `backend/src/main/resources/application.yml` 內 `erd.storage` 區塊既有值為準,若名稱不同以該值代入。)

- [ ] **Step 2: Dockerfile**

```dockerfile
# agent-service/Dockerfile
FROM python:3.11-slim
WORKDIR /srv
RUN pip install --no-cache-dir uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY app ./app
EXPOSE 8000
# fastapi CLI(fastapi[standard])讀 pyproject 的 [tool.fastapi] entrypoint;預設 0.0.0.0:8000
CMD ["uv", "run", "fastapi", "run"]
```

- [ ] **Step 3: docker-compose 增加 service**

在 `services:` 下加(與 backend 共掛同一個上傳 volume;backend 服務既有的 uploads volume 對映以 `docker-compose.yml:85` 附近實際值為準,兩邊掛同一 host 目錄,container 內路徑 `/data/uploads`):

```yaml
  agent-service:
    build: ./agent-service
    environment:
      OPENAI_BASE_URL: ${OPENAI_BASE_URL}
      OPENAI_API_KEY: ${OPENAI_API_KEY:-unused}
      AGENT_MODEL: ${AGENT_MODEL:-gpt-oss-120b}
      # MinIO/S3 模式(erd.storage.type=s3)時由 DuckDB httpfs 直讀;local 模式忽略
      AGENT_S3_ENDPOINT: ${AGENT_S3_ENDPOINT:-minio:9000}
      AGENT_S3_ACCESS_KEY_ID: ${AGENT_S3_ACCESS_KEY_ID:-minioadmin}
      AGENT_S3_SECRET_ACCESS_KEY: ${AGENT_S3_SECRET_ACCESS_KEY:-minioadmin}
      AGENT_S3_USE_SSL: "false"
      # Langfuse tracing(spec §14):不設 key 即完全停用;prod 的 HOST MUST 為公司內部位址
      LANGFUSE_PUBLIC_KEY: ${LANGFUSE_PUBLIC_KEY:-}
      LANGFUSE_SECRET_KEY: ${LANGFUSE_SECRET_KEY:-}
      LANGFUSE_HOST: ${LANGFUSE_HOST:-}
    volumes:
      - ./uploads:/data/uploads   # local 儲存模式用;s3 模式不依賴此 volume
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request;urllib.request.urlopen('http://localhost:8000/health')"]
      interval: 10s
      timeout: 3s
      retries: 5
```

並在 backend service 的 environment 加:`ANALYSIS_AGENT_URL: http://agent-service:8000`、`ANALYSIS_SOURCE_ROOT: /data/uploads`(backend 也掛 `./uploads:/data/uploads`,若 backend 原本上傳目錄不同,以原目錄為準對齊兩邊)。

- [ ] **Step 4: 端到端煙霧測試**

```bash
# 終端 1:啟動 agent-service(本機,fastapi CLI dev 模式)
cd agent-service && OPENAI_BASE_URL=<公司 LLM 或 OpenRouter> OPENAI_API_KEY=<key> \
  uv run fastapi dev --port 8000
# 終端 2:啟動 backend,切換 provider
cd backend && ERD_AGENT_PROVIDER=langgraph-analysis ./mvnw spring-boot:run
# 終端 3:上傳 sample CSV 並提問(沿用既有 API;X-User-Id 任意 UUID)
curl -s -X POST -H "X-User-Id: 11111111-1111-1111-1111-111111111111" \
  -F "file=@backend/src/test/resources/sample.csv" \
  "http://localhost:8080/api/sessions/<sessionId>/files"
curl -N -X POST -H "X-User-Id: 11111111-1111-1111-1111-111111111111" -H "Content-Type: application/json" \
  -d '{"question":"這份資料有幾筆?各欄位是什麼?"}' \
  "http://localhost:8080/api/sessions/<sessionId>/messages"
```

Expected: SSE 依序出現 `STEP` → `TOKEN`×N → `ANSWER`,answer 正確描述列數與欄位。
(若 repo 無 `sample.csv` 測試資源,任取一個兩欄小 CSV。)

MinIO 模式煙霧測試(第二輪,驗證 s3:// 路徑):

```bash
docker compose --profile minio up -d minio minio-init
ERD_STORAGE_TYPE=s3 ERD_STORAGE_S3_BUCKET=erd-cowork \
  ERD_STORAGE_S3_ENDPOINT=http://localhost:9000 ERD_STORAGE_S3_PATH_STYLE_ACCESS=true \
  ERD_AGENT_PROVIDER=langgraph-analysis ./mvnw spring-boot:run
# agent-service 以 AGENT_S3_ENDPOINT=localhost:9000 啟動,重複上面的上傳+提問流程
```

Expected: 與 local 模式相同的事件序列;agent-service log 無檔案讀取錯誤(DuckDB 經 httpfs 直讀 MinIO)。

- [ ] **Step 5: 全套驗證 + Commit**

Run: `cd backend && ./mvnw -q test && cd ../agent-service && uv run pytest -q && uv run ruff check .`
Expected: 兩邊全綠

```bash
git add backend/src/main/resources/application.yml agent-service/Dockerfile docker-compose.yml
git commit -m "feat: wire agent-service into compose and backend config for langgraph-analysis provider"
```

---

## Self-Review 紀錄

- Spec 覆蓋:M1 範圍(spec §9 M1 行)= Python 服務 `/chat` + 兩工具 + 鎖門 + Java provider + compose ✔(§8 UI M1 = 無改動 ✔)
- Placeholder 掃描:Task 5 Step 2 與 Task 7 Step 1/3 含「以實際值為準」的對齊指示——保留,因該值需在既有檔案現場讀取,已給明確行號與判準
- 型別一致:`Source(alias, path, file_type)` / SSE JSON(`TOKEN.delta`/`ANSWER.text`/`STEP.stepKey,title`)/ `AgentFileContext(alias, name, type, storageKey, profile)` 在 Task 2/4/5/6 間一致 ✔
