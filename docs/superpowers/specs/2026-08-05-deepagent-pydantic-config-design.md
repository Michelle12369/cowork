# deepagent-service：pydantic 設定集中化（one.properties）＋ Langfuse 公司實例

## 背景與目標

deepagent-service 目前有約 20 個設定值以 `os.environ.get` 散落在 6 個檔案；Langfuse tracing 只在設了 `LANGFUSE_PUBLIC_KEY` 時建 `CallbackHandler()`，host/secret 依賴 SDK 隱式讀 env 的全域 client。

公司環境的設定交付方式是把 `one.properties`（Java 式 `KEY=value`，key 為**大寫底線**、與現行 env var 同名）掛載到容器內固定路徑；Langfuse 需指向公司自架實例並在建構時掛上公司內部 lib 的 **mask function**（trace 送出前的敏感資料遮罩）。

目標：

1. 設定集中到單一 pydantic-settings `Settings` 類別，來源**層疊優先序**：env > one.properties > 欄位預設——掛載檔存在時作為基底層、env 逐欄位覆寫；不存在 → 只讀 env（dev/docker 現況零改動）。（*此點取代本文件原先的互斥切換決策：見下方設計 §1 附註。*）
2. Langfuse 改為**顯式建構** `Langfuse(public_key, secret_key, host, mask=…)`；mask 經 `AgentRuntime` seam 提供（OSS 側為 None，公司側由 `internal_runtime.py` 回傳內部 lib function）。

## 非目標

- 不改 backend（Java）與 frontend 的設定機制。
- 不引入熱重載——設定為 process 啟動時讀定（現行 env var 語意亦然）。
- 不在本 repo 實作公司 mask function 本體（屬 internal_runtime 範疇）。

## 設計

### 1. `app/config.py` — Settings 與來源層疊優先序

- 依賴：`pyproject.toml` 新增 `pydantic-settings>=2.0`（pydantic 本體已隨 FastAPI 存在）。
- `Settings(pydantic_settings.BaseSettings)`：欄位＝現有全部 env key（`AGENT_MODEL`、`AGENT_AUTH_MODE`、`AGENT_MAX_TOKENS`、`AGENT_REASONING_MAX_TOKENS`、`AGENT_RECURSION_LIMIT`、`AGENT_RUNTIME`、`AGENT_WORKSPACE_ROOT`、`AGENT_BUILTIN_SKILLS_DIR`、`AGENT_PROVIDER_SORT`、`AGENT_PROVIDER_IGNORE`、`AGENT_TOKEN_EXCHANGE_URL`、`AGENT_TOKEN_HEADER`、`AGENT_TOKEN_TTL`、`AGENT_SERVICE_ACCOUNT_KEY`、`AGENT_SERVICE_ACCOUNT_KEY_FILE`、`OPENAI_API_KEY`、`OPENAI_BASE_URL`、`ERD_GUARD_BLOCKING`、`REPAIR_MODEL_CALL_TIMEOUT_SECONDS`、`LANGFUSE_PUBLIC_KEY`）＋新增 `LANGFUSE_SECRET_KEY`、`LANGFUSE_HOST`。型別與預設值一律照現行 call site 的行為搬移，欄位名即大寫底線 key（不做 alias 映射）。
- **Bootstrap key**：`ONE_PROPERTIES_PATH`（**永遠只從 env 讀**，預設 `/config/one.properties`）——指向檔案位置的 key 不能放進檔案本身。公司側確認實際掛載路徑後可直接沿用預設或以 env 覆寫。
- **層疊優先序**（`settings_customise_sources`；取代本文件原先的互斥切換決策——pydantic-settings 來源 tuple 中排愈前優先序愈高）：
  - 檔案存在 → `(init_settings, env_settings, PropertiesFileSource(...))`：env 覆寫檔案值，檔案覆寫欄位預設。
  - 檔案不存在 → `(init_settings, env_settings)`，與現況相同（`init` kwargs 保留供測試直接建構）。
- `PropertiesFileSource` 解析規則（刻意保守）：UTF-8 逐行；空行與 `#` 開頭行跳過；其餘行以**首個 `=`** 切為 key/value 並 strip；無 `=` 的非空行 → **啟動即失敗**（`RuntimeError` 指出行號），NEVER 靜默跳過或退回 env。不支援 Java properties 的跳脫、多行接續與 `:` 分隔——公司檔案格式單純，先不做（出現再加）。
- `get_settings()`：`functools.lru_cache` 單例；測試以 `get_settings.cache_clear()` 重置。
- 六個檔案的 `os.environ.get` call site 全部改為 `get_settings().<FIELD>`：`agent/auth.py`、`agent/chat_turn.py`、`agent/repair_flow.py`、`agent/runtime/__init__.py`、`agent/runtime/deepagents_runtime.py`、`engine/workspace.py`。engine 層允許 import `app.config`（pydantic 非 LLM 框架，不觸犯 ruff TID251）。

### 2. Langfuse 顯式初始化 ＋ mask seam

- `agent/runtime/base.py` 的 `AgentRuntime` 增加**非抽象**方法：

  ```python
  def build_langfuse_mask(self) -> Callable[..., Any] | None:
      """Langfuse mask function；OSS 環境無遮罩需求，預設 None。internal runtime 覆寫回傳公司 lib 的 mask。"""
      return None
  ```

  非抽象＝公司側既有 `internal_runtime.py` 不會因新抽象方法而啟動失敗；要接 mask 時再覆寫。
- 新模組 `agent/tracing.py`：

  ```python
  def init_langfuse(settings: Settings, runtime: AgentRuntime) -> None
  ```

  - `LANGFUSE_PUBLIC_KEY` 與 `LANGFUSE_SECRET_KEY` **皆空** → no-op（tracing 關閉）。
  - **只設其中一個** → `RuntimeError` 啟動即失敗（半套設定是配置錯誤，比現行隱式行為嚴格）。
  - 皆有值 → `Langfuse(public_key=…, secret_key=…, host=settings.LANGFUSE_HOST or None, mask=runtime.build_langfuse_mask())`——langfuse v3 建構子即註冊全域 client；`host=None` 時由 SDK 用官方預設。
- `main.py` lifespan 啟動時呼叫 `init_langfuse(get_settings(), load_runtime())` 一次。
- `chat_turn._build_callbacks`：gate 改為 `get_settings().LANGFUSE_PUBLIC_KEY`，其餘不變（`CallbackHandler()` 使用已註冊的全域 client）。

### 3. 文件與範例

- `.env.example`：補 `ONE_PROPERTIES_PATH`、`LANGFUSE_SECRET_KEY`、`LANGFUSE_HOST`，並以註解說明互斥語意（「掛載檔存在時 env 全數失效」）。
- `docs/internal-implementation-guide.md` 補兩節：one.properties 掛載與互斥規則（含 key 同名對照）、`build_langfuse_mask` 公司側覆寫範例。

### 4. 錯誤處理原則

沿用 internal seam 的 NEVER-fallback 原則：檔案存在但解析失敗、Langfuse 半套 key、mask 覆寫存在但拋錯——一律啟動即失敗，不靜默降級。

### 5. 測試

- `PropertiesFileSource`：正常解析、註解／空行、value 含 `=`、壞行 fail-loud（含行號）。
- 互斥：檔案存在時 env 值不生效；不存在時 env 生效；`ONE_PROPERTIES_PATH` 覆寫路徑生效。
- `Settings` 預設值與型別轉換（int／bool 欄位）。
- `init_langfuse`：雙空 no-op、半套 RuntimeError、雙有值時以 fake runtime 驗證 mask 正確傳入（monkeypatch `Langfuse` 建構子）。
- 既有測試：新增 autouse／顯式 fixture 於 monkeypatch env 後 `get_settings.cache_clear()`。
