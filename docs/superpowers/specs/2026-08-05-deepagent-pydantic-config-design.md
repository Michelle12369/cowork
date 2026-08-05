# deepagent-service：pydantic 設定集中化（one.properties）＋ Langfuse internal 實例

## 背景與目標

deepagent-service 目前有約 20 個設定值以 `os.environ.get` 散落在 6 個檔案；Langfuse tracing 只在設了 `LANGFUSE_PUBLIC_KEY` 時建 `CallbackHandler()`，host/secret 依賴 SDK 隱式讀 env 的全域 client。

internal 環境的設定交付方式是把 `one.properties`（Java 式 `KEY=value`，key 為**大寫底線**、與現行 env var 同名）掛載到容器內固定路徑；Langfuse 需指向 internal 自架實例並在建構時掛上 internal lib 的 **mask function**（trace 送出前的敏感資料遮罩）。

目標：

1. 設定集中到單一 pydantic-settings `Settings` 類別，來源**層疊優先序**：env > one.properties > 欄位預設——掛載檔存在時作為基底層、env 逐欄位覆寫；不存在 → 只讀 env（dev/docker 現況零改動）。（*此點取代本文件原先的互斥切換決策：見下方設計 §1 附註。*）
2. Langfuse 改為**顯式建構** `Langfuse(public_key, secret_key, host, mask=…)`；mask 經 `AgentRuntime` seam 提供（OSS 側為 None，internal 側由 `internal_runtime.py` 回傳 internal lib function）。

## 非目標

- 不改 backend（Java）與 frontend 的設定機制。
- 不引入熱重載——設定為 process 啟動時讀定（現行 env var 語意亦然）。
- 不在本 repo 實作 internal mask function 本體（屬 internal_runtime 範疇）。

## 設計

### 1. `app/config.py` — Settings 與來源層疊優先序

- 依賴：`pyproject.toml` 新增 `pydantic-settings>=2.0`（pydantic 本體已隨 FastAPI 存在）。
- `Settings(pydantic_settings.BaseSettings)`：欄位＝現有全部 env key（`AGENT_MODEL`、`AGENT_AUTH_MODE`、`AGENT_MAX_TOKENS`、`AGENT_REASONING_MAX_TOKENS`、`AGENT_RECURSION_LIMIT`、`AGENT_RUNTIME`、`AGENT_WORKSPACE_ROOT`、`AGENT_BUILTIN_SKILLS_DIR`、`AGENT_PROVIDER_SORT`、`AGENT_PROVIDER_IGNORE`、`AGENT_TOKEN_EXCHANGE_URL`、`AGENT_TOKEN_HEADER`、`AGENT_TOKEN_TTL`、`AGENT_SERVICE_ACCOUNT_KEY`、`AGENT_SERVICE_ACCOUNT_KEY_FILE`、`OPENAI_API_KEY`、`OPENAI_BASE_URL`、`ERD_GUARD_BLOCKING`、`REPAIR_MODEL_CALL_TIMEOUT_SECONDS`、`LANGFUSE_PUBLIC_KEY`）＋新增 `LANGFUSE_SECRET_KEY`、`LANGFUSE_HOST`。型別與預設值一律照現行 call site 的行為搬移，欄位名即大寫底線 key（不做 alias 映射）。
- **Bootstrap key**：`ONE_PROPERTIES_PATH`（**永遠只從 env 讀**，預設 `/config/one.properties`）——指向檔案位置的 key 不能放進檔案本身。internal 側確認實際掛載路徑後可直接沿用預設或以 env 覆寫。*（後續修訂：預設改為啟動 CWD 下的 `one.properties`——本機開發零設定即生效；internal 掛載至 `/config/one.properties` 等路徑時改為 MUST 顯式設 `ONE_PROPERTIES_PATH`。）*
- **層疊優先序**（`settings_customise_sources`；取代本文件原先的互斥切換決策——pydantic-settings 來源 tuple 中排愈前優先序愈高）：
  - 檔案存在 → `(init_settings, env_settings, PropertiesFileSource(...))`：env 覆寫檔案值，檔案覆寫欄位預設。
  - 檔案不存在 → `(init_settings, env_settings)`，與現況相同（`init` kwargs 保留供測試直接建構）。
- `PropertiesFileSource` 解析規則（刻意保守）：UTF-8 逐行；空行與 `#` 開頭行跳過；其餘行以**首個 `=`** 切為 key/value 並 strip；無 `=` 的非空行 → **啟動即失敗**（`RuntimeError` 指出行號），NEVER 靜默跳過或退回 env。不支援 Java properties 的跳脫、多行接續與 `:` 分隔——internal 檔案格式單純，先不做（出現再加）。
- `get_settings()`：`functools.lru_cache` 單例；測試以 `get_settings.cache_clear()` 重置。
- 六個檔案的 `os.environ.get` call site 全部改為 `get_settings().<FIELD>`：`agent/auth.py`、`agent/chat_turn.py`、`agent/repair_flow.py`、`agent/runtime/__init__.py`、`agent/runtime/deepagents_runtime.py`、`engine/workspace.py`。engine 層允許 import `app.config`（pydantic 非 LLM 框架，不觸犯 ruff TID251）。

### 2. Langfuse 顯式初始化 ＋ 完整建構 seam

**（後續修訂：舊版「只給 mask function」的 seam 已改為 `build_langfuse`，擴大為「完整接管
client 建構」——見下方。）**

- `agent/runtime/base.py` 的 `AgentRuntime` 增加**選用**方法：

  ```python
  def build_langfuse(self, settings: Settings) -> Any | None:
      """建構並回傳 Langfuse client，回 None＝tracing 關閉。internal 覆寫以完整接管建構
      （自家 host/auth/mask/wrapper）。"""
      ...
  ```

  取用端一律 `getattr(runtime, "build_langfuse", None)`；internal 側結構實作不提供也不影響型別。
- 新模組 `agent/tracing.py`：

  ```python
  def init_langfuse(settings: Settings, runtime: AgentRuntime) -> None
  def is_tracing_enabled() -> bool
  ```

  - runtime 提供 `build_langfuse` → 呼叫一次取得 client，enabled＝`client is not None`，
    OSS 預設建構路徑完全不跑。
  - 否則走 OSS 預設路徑：`LANGFUSE_PUBLIC_KEY`／`LANGFUSE_SECRET_KEY` 皆空 → no-op；只設
    其中一個 → `RuntimeError` 啟動即失敗；皆有值 → `Langfuse(public_key=…, secret_key=…,
    host=settings.LANGFUSE_HOST or None, mask=None)`。
- `main.py` lifespan 啟動時呼叫 `init_langfuse(get_settings(), load_runtime())` 一次。
- `chat_turn._build_callbacks`：gate 改為 `tracing.is_tracing_enabled()`（不再直接看
  Settings 的 key——runtime 完整接管建構時 client 不一定源自那兩個 key）。

### 3. 文件與範例

- `.env.example`：補 `ONE_PROPERTIES_PATH`、`LANGFUSE_SECRET_KEY`、`LANGFUSE_HOST`，並以註解說明層疊優先序（「env > one.properties > 欄位預設，檔案存在時作為基底層」）。（*後續修訂：deepagent-service 全部參數含 LANGFUSE_* 已進一步遷至 `deepagent-service/one.properties.example`，`.env.example` 現只留 `ONE_PROPERTIES_PATH` 掛載指標——見該次 commit。*）
- `docs/internal-implementation-guide.md` 補兩節：one.properties 掛載與層疊優先序規則（含 key 同名對照）、`build_langfuse` internal 側覆寫範例（完整接管 client 建構）。

### 4. 錯誤處理原則

沿用 internal seam 的 NEVER-fallback 原則：檔案存在但解析失敗、Langfuse 半套 key、mask 覆寫存在但拋錯——一律啟動即失敗，不靜默降級。

### 5. 測試

- `PropertiesFileSource`：正常解析、註解／空行、value 含 `=`、壞行 fail-loud（含行號）。
- 層疊優先序：檔案存在時 env 覆寫檔案值、檔案覆寫欄位預設；不存在時只讀 env；`ONE_PROPERTIES_PATH` 覆寫掛載路徑生效。
- `Settings` 預設值與型別轉換（int／bool 欄位）。
- `init_langfuse`：雙空 no-op、半套 RuntimeError、雙有值時以 fake runtime 驗證 mask 正確傳入（monkeypatch `Langfuse` 建構子）。
- 既有測試：新增 autouse／顯式 fixture 於 monkeypatch env 後 `get_settings.cache_clear()`。
