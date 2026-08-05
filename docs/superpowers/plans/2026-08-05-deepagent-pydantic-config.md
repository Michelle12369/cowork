# deepagent-service pydantic 設定集中化＋Langfuse 公司實例 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** deepagent-service 的 ~20 個散落 env 設定集中到 pydantic-settings `Settings`（one.properties 掛載檔與 env 互斥切換），Langfuse 改為啟動時顯式建構並經 runtime seam 掛公司 mask function。

**Architecture:** 新增 `app/config.py`（`Settings` + `PropertiesFileSource` + `get_settings()` lru_cache 單例）；`ONE_PROPERTIES_PATH`（env-only bootstrap，預設 `/config/one.properties`）指向的檔案存在→只讀檔、不存在→只讀 env。新增 `app/agent/tracing.py` 的 `init_langfuse(settings, runtime)` 於 FastAPI lifespan 呼叫一次；mask 以 `getattr(runtime, "build_langfuse_mask", ...)` 取用（`AgentRuntime` 是 **Protocol**，公司側為結構實作、不繼承預設方法，NEVER 直接呼叫該屬性）。六個檔案的 `os.environ.get` call site 全部改走 `get_settings()`。

**Tech Stack:** Python 3.11 / FastAPI / pydantic-settings>=2.0（新增依賴）/ langfuse>=3.0 / pytest + monkeypatch。

**Spec:** `docs/superpowers/specs/2026-08-05-deepagent-pydantic-config-design.md`（本 plan 的唯一需求來源）。

## Global Constraints

- 寫 Python 前先讀 `.claude/skills/fastapi/SKILL.md`；參數/依賴一律 `Annotated`
- 變數 NEVER 用 1–2 字元名稱；描述性命名
- 錯誤處理原則（spec §4）：檔案存在但解析失敗、Langfuse 半套 key——一律啟動即失敗（`RuntimeError`），NEVER 靜默降級或 fallback
- key 命名＝大寫底線、與現行 env var 同名；`Settings` 欄位名即 key 名（`case_sensitive=True`，不做 alias）
- 現行 call site 的字串處理語意（`.strip()`、`or None`、`!= "false"`）**原樣保留在 call site**，`Settings` 欄位只做型別與預設值——行為零改變
- 每個 task 結束：`cd deepagent-service && uv run pytest -q` 全綠＋`uv run ruff check app tests` 乾淨（若 repo 用 pip/venv 則以既有方式跑，先看 README/Makefile；`test_requirements_sync.py` 若因新依賴失敗，照該測試的說明同步 requirements 檔）
- 執行 branch：`feat/deepagent-config`
- 註解精簡：1–2 行寫目的＋做法

## 現況地圖（實作者必讀）

env 讀取現況（migrate 對照表；「保留語意」欄位照抄到 call site）：

| 檔案:行 | Key | Settings 欄位型別＝預設 | call site 保留語意 |
|---|---|---|---|
| `agent/auth.py:126` | AGENT_AUTH_MODE | `str = "bearer"` | `.strip() or "bearer"` |
| `agent/auth.py:131` | AGENT_TOKEN_EXCHANGE_URL | `str = ""` | `.strip()` |
| `agent/auth.py:132` | AGENT_TOKEN_HEADER | `str = ""` | — |
| `agent/auth.py:133` | AGENT_TOKEN_TTL | `int = 300` | — |
| `agent/auth.py:134-135` | AGENT_SERVICE_ACCOUNT_KEY / _FILE | `str \| None = None` | `or None` 已由型別涵蓋 |
| `agent/repair_flow.py:42` | REPAIR_MODEL_CALL_TIMEOUT_SECONDS | `float = 60.0` | module 常數，來源改 `get_settings()` |
| `agent/runtime/__init__.py:21` | AGENT_RUNTIME | `str = "deepagents"` | — |
| `agent/chat_turn.py:61` | AGENT_RECURSION_LIMIT | `int = 80` | module 常數 |
| `agent/chat_turn.py:74` | ERD_GUARD_BLOCKING | `str = "true"` | `.strip().lower() != "false"`（維持「非 false 即 true」語意，NEVER 改成 pydantic bool） |
| `agent/chat_turn.py:117` | LANGFUSE_PUBLIC_KEY | `str \| None = None` | truthiness gate |
| `agent/runtime/deepagents_runtime.py:24,27` | AGENT_MAX_TOKENS / AGENT_REASONING_MAX_TOKENS | `int = 32768` / `int = 8192` | — |
| `agent/runtime/deepagents_runtime.py:34,39` | AGENT_PROVIDER_SORT / AGENT_PROVIDER_IGNORE | `str = ""` | `.strip()`／`.split(",")` |
| `agent/runtime/deepagents_runtime.py:52-59` | AGENT_MODEL / OPENAI_BASE_URL / OPENAI_API_KEY | `str = "qwen3.6-35b"` / `str \| None = None` / `str = "unused"` | `bool(...)`／`or None` |
| `engine/workspace.py:64,74` | AGENT_WORKSPACE_ROOT / AGENT_BUILTIN_SKILLS_DIR | `str = "/data/workspace"` / `str \| None = None` | `Path(...)`／override truthiness |

新增欄位：`LANGFUSE_SECRET_KEY: str | None = None`、`LANGFUSE_HOST: str | None = None`。

其他既定事實：`AgentRuntime` 是 `typing.Protocol`（`agent/runtime/base.py`）；`main.py` 目前**沒有 lifespan**；測試在 `deepagent-service/tests/`（pytest，conftest.py 有共用 fixture，先讀）；ruff TID251 禁 engine/ import LLM 框架——pydantic 不在禁單，`engine/workspace.py` import `app.config` 合法。

---

### Task 1: `app/config.py` — Settings、PropertiesFileSource、互斥切換

**Files:**
- Create: `deepagent-service/app/config.py`
- Test: `deepagent-service/tests/test_config.py`
- Modify: `deepagent-service/pyproject.toml`（dependencies 加 `"pydantic-settings>=2.0", # 集中設定;one.properties 與 env 互斥`）

**Interfaces:**
- Produces: `get_settings() -> Settings`（`functools.lru_cache` 單例；測試用 `get_settings.cache_clear()` 重置）；`Settings` 欄位如現況地圖表；`ONE_PROPERTIES_PATH` 為 env-only bootstrap（**不是** Settings 欄位）

- [ ] **Step 1: 加依賴並確認安裝**

pyproject.toml dependencies 加 `pydantic-settings>=2.0`，跑依賴安裝（`uv sync` 或既有方式）；若 `tests/test_requirements_sync.py` 檢查 requirements 對齊，照其規則同步。

- [ ] **Step 2: 寫失敗測試**

```python
"""app/config.py 的 Settings 載入與 one.properties 互斥語意。"""

import pytest

from app.config import Settings, get_settings


@pytest.fixture(autouse=True)
def reset_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _write_properties(tmp_path, content: str):
    properties_file = tmp_path / "one.properties"
    properties_file.write_text(content, encoding="utf-8")
    return properties_file


def test_no_properties_file_reads_env(monkeypatch, tmp_path):
    monkeypatch.setenv("ONE_PROPERTIES_PATH", str(tmp_path / "absent.properties"))
    monkeypatch.setenv("AGENT_MODEL", "env-model")
    assert get_settings().AGENT_MODEL == "env-model"


def test_properties_file_present_env_ignored(monkeypatch, tmp_path):
    properties_file = _write_properties(tmp_path, "AGENT_MODEL=file-model\n")
    monkeypatch.setenv("ONE_PROPERTIES_PATH", str(properties_file))
    monkeypatch.setenv("AGENT_MODEL", "env-model")
    settings = get_settings()
    assert settings.AGENT_MODEL == "file-model"
    # 檔案沒列的 key 用預設值，NEVER 從 env 漏進來
    monkeypatch.setenv("AGENT_TOKEN_TTL", "999")
    assert settings.AGENT_TOKEN_TTL == 300


def test_properties_parsing_comments_blanks_and_equals_in_value(monkeypatch, tmp_path):
    properties_file = _write_properties(
        tmp_path,
        "# comment\n\nOPENAI_BASE_URL=https://host/v1?a=b=c\n  AGENT_TOKEN_TTL = 120 \n",
    )
    monkeypatch.setenv("ONE_PROPERTIES_PATH", str(properties_file))
    settings = get_settings()
    assert settings.OPENAI_BASE_URL == "https://host/v1?a=b=c"
    assert settings.AGENT_TOKEN_TTL == 120


def test_properties_bad_line_fails_loud(monkeypatch, tmp_path):
    properties_file = _write_properties(tmp_path, "AGENT_MODEL=ok\nthis-line-has-no-separator\n")
    monkeypatch.setenv("ONE_PROPERTIES_PATH", str(properties_file))
    with pytest.raises(RuntimeError, match="line 2"):
        get_settings()


def test_defaults_without_any_source(monkeypatch, tmp_path):
    monkeypatch.setenv("ONE_PROPERTIES_PATH", str(tmp_path / "absent.properties"))
    for key in ("AGENT_MODEL", "AGENT_TOKEN_TTL", "ERD_GUARD_BLOCKING", "LANGFUSE_PUBLIC_KEY"):
        monkeypatch.delenv(key, raising=False)
    settings = get_settings()
    assert settings.AGENT_MODEL == "qwen3.6-35b"
    assert settings.AGENT_TOKEN_TTL == 300
    assert settings.ERD_GUARD_BLOCKING == "true"
    assert settings.LANGFUSE_PUBLIC_KEY is None
```

- [ ] **Step 3: 跑測試確認失敗**

Run: `cd deepagent-service && uv run pytest tests/test_config.py -q`
Expected: FAIL（`app.config` 不存在）

- [ ] **Step 4: 實作 `app/config.py`**

```python
"""集中設定。one.properties（ONE_PROPERTIES_PATH，預設 /config/one.properties）存在時
只讀該檔（env 完全忽略）；不存在時只讀 env——互斥切換，NEVER 混合來源。"""

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

_DEFAULT_PROPERTIES_PATH = "/config/one.properties"


def _properties_path() -> Path:
    return Path(os.environ.get("ONE_PROPERTIES_PATH", _DEFAULT_PROPERTIES_PATH))


def _parse_properties(properties_file: Path) -> dict[str, str]:
    """Java 式 KEY=value：空行與 # 註解跳過、首個 = 切分並 strip。
    無 = 的非空行是配置錯誤——啟動即失敗，NEVER 靜默跳過。"""
    parsed: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        properties_file.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise RuntimeError(
                f"one.properties line {line_number} 無 '=' 分隔: {raw_line!r}（{properties_file}）"
            )
        key, _, value = line.partition("=")
        parsed[key.strip()] = value.strip()
    return parsed


class PropertiesFileSource(PydanticBaseSettingsSource):
    def __init__(self, settings_cls: type[BaseSettings], properties_file: Path):
        super().__init__(settings_cls)
        self._values = _parse_properties(properties_file)

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
        return self._values.get(field_name), field_name, False

    def __call__(self) -> dict[str, Any]:
        return {
            field_name: self._values[field_name]
            for field_name in self.settings_cls.model_fields
            if field_name in self._values
        }


class Settings(BaseSettings):
    model_config = SettingsConfigDict(case_sensitive=True)

    AGENT_AUTH_MODE: str = "bearer"
    AGENT_TOKEN_EXCHANGE_URL: str = ""
    AGENT_TOKEN_HEADER: str = ""
    AGENT_TOKEN_TTL: int = 300
    AGENT_SERVICE_ACCOUNT_KEY: str | None = None
    AGENT_SERVICE_ACCOUNT_KEY_FILE: str | None = None
    REPAIR_MODEL_CALL_TIMEOUT_SECONDS: float = 60.0
    AGENT_RUNTIME: str = "deepagents"
    AGENT_RECURSION_LIMIT: int = 80
    ERD_GUARD_BLOCKING: str = "true"
    AGENT_MAX_TOKENS: int = 32768
    AGENT_REASONING_MAX_TOKENS: int = 8192
    AGENT_PROVIDER_SORT: str = ""
    AGENT_PROVIDER_IGNORE: str = ""
    AGENT_MODEL: str = "qwen3.6-35b"
    OPENAI_BASE_URL: str | None = None
    OPENAI_API_KEY: str = "unused"
    AGENT_WORKSPACE_ROOT: str = "/data/workspace"
    AGENT_BUILTIN_SKILLS_DIR: str | None = None
    LANGFUSE_PUBLIC_KEY: str | None = None
    LANGFUSE_SECRET_KEY: str | None = None
    LANGFUSE_HOST: str | None = None

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        properties_file = _properties_path()
        if properties_file.exists():
            return (init_settings, PropertiesFileSource(settings_cls, properties_file))
        return (init_settings, env_settings)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 5: 跑測試確認通過＋ruff**

Run: `cd deepagent-service && uv run pytest tests/test_config.py -q && uv run ruff check app tests`
Expected: PASS／乾淨

- [ ] **Step 6: Commit**

```bash
git add deepagent-service/app/config.py deepagent-service/tests/test_config.py deepagent-service/pyproject.toml
git commit -m "feat: pydantic-settings 集中設定，one.properties 與 env 互斥切換"
```

（若依賴同步動到 lockfile/requirements 檔一併 add。）

---

### Task 2: 六個 call site 遷移到 `get_settings()`

**Files:**
- Modify: `deepagent-service/app/agent/auth.py:126-135`、`app/agent/chat_turn.py:61,74,117`、`app/agent/repair_flow.py:42`、`app/agent/runtime/__init__.py:21`、`app/agent/runtime/deepagents_runtime.py:24-59`、`app/engine/workspace.py:64,74`
- Test: 既有測試適配（`tests/test_auth.py`、`tests/test_chat.py` 等——monkeypatch env 的測試需在 patch 後 `get_settings.cache_clear()`）

**Interfaces:**
- Consumes: Task 1 的 `get_settings()`
- Produces: 全 repo `app/` 內不再有 `os.environ.get`／`os.getenv` 讀取上表 20 個 key（`ONE_PROPERTIES_PATH` 的讀取只存在於 `app/config.py`）

- [ ] **Step 1: 逐檔改寫**

每個 call site 以 `get_settings().<FIELD>` 取代 `os.environ.get(...)`，**保留語意欄位的字串處理照抄**。範例（auth.py）：

```python
from app.config import get_settings

def _auth_mode() -> str:
    return get_settings().AGENT_AUTH_MODE.strip() or "bearer"
```

module-level 常數（`chat_turn.AGENT_RECURSION_LIMIT`、`chat_turn.ERD_GUARD_BLOCKING`、`repair_flow.REPAIR_MODEL_CALL_TIMEOUT_SECONDS`）維持 module-level 名稱不變（既有測試可能 monkeypatch 這些屬性），來源改為：

```python
AGENT_RECURSION_LIMIT = get_settings().AGENT_RECURSION_LIMIT
ERD_GUARD_BLOCKING = get_settings().ERD_GUARD_BLOCKING.strip().lower() != "false"
```

`chat_turn.py:117` 的 gate 改為 `if not get_settings().LANGFUSE_PUBLIC_KEY:`。
`workspace.py` 維持回傳 `Path`：`Path(get_settings().AGENT_WORKSPACE_ROOT)`。
移除各檔不再需要的 `import os`（若該檔還有其他 os 用途則保留）。

- [ ] **Step 2: 驗證無殘留**

Run: `cd deepagent-service && grep -rn "os.environ.get\|os.getenv" app/ | grep -v "app/config.py"`
Expected: 零筆（或僅剩與上表無關的讀取——逐筆確認後在報告中列出）

- [ ] **Step 3: 跑全套測試，適配失敗案例**

Run: `cd deepagent-service && uv run pytest -q`
monkeypatch env 後斷言行為的測試：在 setenv/delenv 之後加 `get_settings.cache_clear()`（或在該測試檔加 autouse fixture）。module import 時序造成的常數固定問題：測試改 monkeypatch module 屬性（如 `monkeypatch.setattr(chat_turn, "ERD_GUARD_BLOCKING", False)`）——既有測試若已這樣寫則不動。
Expected: 全綠

- [ ] **Step 4: ruff + commit**

```bash
cd deepagent-service && uv run ruff check app tests
git add -A deepagent-service
git commit -m "refactor: 全部 env 讀取改走 get_settings()"
```

---

### Task 3: `tracing.py` init_langfuse＋mask seam＋lifespan 接線

**Files:**
- Create: `deepagent-service/app/agent/tracing.py`
- Modify: `deepagent-service/app/agent/runtime/base.py`（Protocol 加方法宣告＋預設實作註記）、`deepagent-service/app/agent/runtime/deepagents_runtime.py`（實作 `build_langfuse_mask` 回 `None`）、`deepagent-service/app/main.py`（lifespan）
- Test: `deepagent-service/tests/test_tracing.py`

**Interfaces:**
- Consumes: Task 1 `Settings`／`get_settings`；`load_runtime()`（既有）
- Produces: `init_langfuse(settings: Settings, runtime: object) -> None`；`AgentRuntime.build_langfuse_mask(self) -> Callable[..., Any] | None`（Protocol 宣告；**取用一律 `getattr(runtime, "build_langfuse_mask", None)`**——公司側是結構實作，不保證有此方法）

- [ ] **Step 1: 寫失敗測試**

```python
"""init_langfuse：顯式建構、半套 key fail-loud、mask 經 runtime seam 傳入。"""

import pytest

import app.agent.tracing as tracing_module
from app.agent.tracing import init_langfuse
from app.config import Settings


class _FakeLangfuse:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class _RuntimeWithMask:
    def build_langfuse_mask(self):
        return _mask_function


class _RuntimeWithoutMask:
    pass


def _mask_function(**kwargs):
    return kwargs


def _settings(**overrides) -> Settings:
    return Settings(**overrides)  # init kwargs 來源（見 config 的 init_settings）


def test_both_keys_absent_is_noop(monkeypatch):
    created = []
    monkeypatch.setattr(tracing_module, "Langfuse", lambda **kw: created.append(kw))
    init_langfuse(_settings(), _RuntimeWithMask())
    assert created == []


@pytest.mark.parametrize(
    "overrides", [{"LANGFUSE_PUBLIC_KEY": "pk"}, {"LANGFUSE_SECRET_KEY": "sk"}]
)
def test_half_configured_fails_loud(overrides):
    with pytest.raises(RuntimeError, match="LANGFUSE"):
        init_langfuse(_settings(**overrides), _RuntimeWithMask())


def test_full_config_builds_client_with_mask(monkeypatch):
    monkeypatch.setattr(tracing_module, "Langfuse", _FakeLangfuse)
    client_holder = {}
    monkeypatch.setattr(
        tracing_module, "Langfuse", lambda **kw: client_holder.setdefault("kwargs", kw)
    )
    init_langfuse(
        _settings(
            LANGFUSE_PUBLIC_KEY="pk", LANGFUSE_SECRET_KEY="sk", LANGFUSE_HOST="https://lf.corp"
        ),
        _RuntimeWithMask(),
    )
    assert client_holder["kwargs"] == {
        "public_key": "pk",
        "secret_key": "sk",
        "host": "https://lf.corp",
        "mask": _mask_function,
    }


def test_runtime_without_mask_method_passes_none(monkeypatch):
    client_holder = {}
    monkeypatch.setattr(
        tracing_module, "Langfuse", lambda **kw: client_holder.setdefault("kwargs", kw)
    )
    init_langfuse(
        _settings(LANGFUSE_PUBLIC_KEY="pk", LANGFUSE_SECRET_KEY="sk"), _RuntimeWithoutMask()
    )
    assert client_holder["kwargs"]["mask"] is None
    assert client_holder["kwargs"]["host"] is None
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd deepagent-service && uv run pytest tests/test_tracing.py -q`
Expected: FAIL（`app.agent.tracing` 不存在）

- [ ] **Step 3: 實作**

`app/agent/tracing.py`：

```python
"""Langfuse 啟動時顯式初始化。半套 key 是配置錯誤——啟動即失敗，NEVER 靜默半開。"""

import logging
from typing import Any

from langfuse import Langfuse

from app.config import Settings

logger = logging.getLogger(__name__)


def init_langfuse(settings: Settings, runtime: Any) -> None:
    """public+secret 皆空→no-op；皆有→顯式建構（註冊全域 client），mask 經 runtime seam
    取得（getattr——AgentRuntime 是 Protocol，公司側結構實作不保證有此方法）。"""
    public_key = settings.LANGFUSE_PUBLIC_KEY
    secret_key = settings.LANGFUSE_SECRET_KEY
    if not public_key and not secret_key:
        return
    if not (public_key and secret_key):
        raise RuntimeError(
            "LANGFUSE_PUBLIC_KEY 與 LANGFUSE_SECRET_KEY 必須成對設定（半套是配置錯誤）"
        )
    mask_builder = getattr(runtime, "build_langfuse_mask", None)
    mask_function = mask_builder() if mask_builder is not None else None
    Langfuse(
        public_key=public_key,
        secret_key=secret_key,
        host=settings.LANGFUSE_HOST,
        mask=mask_function,
    )
    logger.info(
        "langfuse initialized host=%s maskProvided=%s",
        settings.LANGFUSE_HOST or "(sdk default)",
        mask_function is not None,
    )
```

`agent/runtime/base.py` 的 Protocol 內加（含註解）：

```python
    def build_langfuse_mask(self) -> Callable[..., Any] | None:
        """Langfuse mask function;OSS 環境無遮罩需求回 None。internal 覆寫回傳公司 lib 的
        mask。取用端一律 getattr fallback——結構實作可不提供此方法。"""
        ...
```

（`from collections.abc import Callable` 加入 imports。）
`deepagents_runtime.py` 的 `DeepAgentsRuntime` 加 `def build_langfuse_mask(self) -> Callable[..., Any] | None: return None`。
`main.py`：加 lifespan 並掛到 app——

```python
from contextlib import asynccontextmanager

from app.agent.runtime import load_runtime
from app.agent.tracing import init_langfuse
from app.config import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_langfuse(get_settings(), load_runtime())
    yield


app = FastAPI(title="deepagent-service", lifespan=lifespan)
```

- [ ] **Step 4: 跑全套＋ruff**

Run: `cd deepagent-service && uv run pytest -q && uv run ruff check app tests`
Expected: 全綠（`tests/test_health.py` 等用 TestClient 的測試會觸發 lifespan——無 LANGFUSE key 時 no-op，不應受影響；若有測試環境誤設 key 導致連線嘗試，在該測試 fixture 清掉）

- [ ] **Step 5: Commit**

```bash
git add -A deepagent-service
git commit -m "feat: Langfuse 啟動時顯式初始化，mask 走 runtime seam"
```

---

### Task 4: 文件——.env.example 與 internal-implementation-guide

**Files:**
- Modify: `.env.example`（deepagent 相關段落）
- Modify: `docs/internal-implementation-guide.md`

**Interfaces:**
- Consumes: Tasks 1–3 的最終行為

- [ ] **Step 1: `.env.example` 補三個 key（放在既有 LANGFUSE_PUBLIC_KEY 附近，沿用該檔註解風格）**

```bash
# deepagent 設定來源互斥：ONE_PROPERTIES_PATH 指向的檔案存在時「只讀該檔」（下方所有
# AGENT_*/OPENAI_*/LANGFUSE_* env 全數失效）；不存在時照舊讀 env。公司環境掛載用。
# ONE_PROPERTIES_PATH=/config/one.properties
# Langfuse tracing：public/secret 必須成對設定（半套會啟動失敗）；host 空值＝官方雲端。
# LANGFUSE_SECRET_KEY=
# LANGFUSE_HOST=
```

- [ ] **Step 2: `docs/internal-implementation-guide.md` 補兩節**（放進 deepagent-service 相關章節，沿用該檔的「公司側實作範例」寫法）：

1. **one.properties**：掛載路徑約定（預設 `/config/one.properties`、可用 `ONE_PROPERTIES_PATH` 覆寫）、key＝env var 同名大寫底線、互斥語意（檔案存在時 env 全失效，故檔案必須完整列出所有非預設值）、壞行啟動即失敗。
2. **build_langfuse_mask**：在 `internal_runtime.py` 加

```python
def build_langfuse_mask(self):
    from company_lib.tracing import mask_sensitive_data  # 公司內部 lib

    return mask_sensitive_data
```

說明：回傳的 callable 直接傳給 `Langfuse(mask=...)`；未實作此方法時 OSS 側傳 `mask=None`。

- [ ] **Step 3: Commit**

```bash
git add .env.example docs/internal-implementation-guide.md
git commit -m "docs: one.properties 互斥設定與 Langfuse mask seam 說明"
```

---

## Self-Review 紀錄

- Spec 覆蓋：§1 Settings/互斥/解析（T1）、call site 遷移（T2）、§2 Langfuse/lifespan/mask seam（T3）、§3 文件（T4）、§4 fail-loud 原則（T1 Step 4 解析、T3 半套 key）、§5 測試全數對應——齊。
- Protocol 修正：spec 寫「非抽象方法預設 None」，plan 依 base.py 實為 Protocol 的事實改為「Protocol 宣告＋getattr fallback＋DeepAgentsRuntime 顯式實作」，語意等價且不破壞公司側結構實作（此為 plan 對 spec 的實作層修正，已於 Architecture 段落載明）。
- 型別一致：`get_settings()`／`Settings` 欄位名／`init_langfuse(settings, runtime)`／`build_langfuse_mask()` 於各 task 一致。
