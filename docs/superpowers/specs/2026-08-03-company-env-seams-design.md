# 公司環境接縫與單向搬運設計

**日期**：2026-08-03
**狀態**：設計完成，待實作
**範圍**：三側皆有改動——deepagent-service（主體）、backend（Maven profile + profile 設定檔）、frontend（Vite plugin + bootstrap 接縫）；外加搬運流程與紀律

---

## 問題

同一份程式碼要同時活在兩個環境：

- **外部（家裡）**：GitHub `Michelle12369/cowork`，dev 用 OpenRouter，deepagents + `ChatOpenAI`
- **公司內**：連不到 GitHub，走內部 registry 與內部 lib

兩邊**完全隔離，只能手動搬檔**——沒有共同 remote、沒有 3-way merge，搬進去就是**整檔覆蓋**。

因此凡是「兩邊都會編輯的檔案」，每搬一次就會覆蓋掉公司那側的修改。已知會被公司編輯的有
`pom.xml`、`application.yml`、`.env.example`、`uv.lock`、`index.html`、`main.tsx`，
以及 deepagent-service 的 agent 建構層。

---

## 硬約束：沒有 merge，只有覆蓋

這是本設計最主要的約束，也是它跟一般「多環境設定」問題的差別。多環境設定通常靠
branch/merge 收斂；這裡沒有那個工具，所以**唯一可行的策略是讓重疊面積為零**：

> 每個檔案 MUST 只有一側能寫。任何「兩邊都想改」的地方，都要先轉成接縫。

---

## 核心原則：家裡開接縫，公司填實作

**家裡是唯一的權威寫入者。** 公司拿到搬進來的檔案後一行都不改；任何「非改不可」的地方
回報給家裡，由家裡開一個接縫，一次性付出，之後該檔案永遠不再衝突。

### 三類檔案

| 類別 | 誰能寫 | 搬不搬 | 例子 |
|---|---|---|---|
| **共用權威檔** | 只有家裡 | 搬（整檔覆蓋） | `pom.xml`、`application.yml`、`pyproject.toml`、`index.html`、`main.tsx`、`app/agent/**`（`runtime/company_runtime.py` 除外）、`.env.example` |
| **公司獨佔檔** | 只有公司 | **永不搬** | `application-company.yml`、`src/company/java/**`、`company.impl.ts`、`company_runtime.py`、`.env` |
| **各自產生檔** | 兩邊各自產 | **永不搬** | `uv.lock`、`~/.m2/settings.xml` |

---

## 接縫一（主體）：deepagent-service 的 `AgentRuntime`

### 為什麼這是主體

`app/engine/**`（1918 行）因既有的 ruff banned-api 規則完全不碰 LLM 框架，天然共用。
牆的另一側有 1289 行 import langchain/langgraph/deepagents，而公司要換掉的
`create_deep_agent`、model、checkpointer 正是這 1289 行的支柱。

**在沒有 git 同步的環境下維護 1289 行雙份程式碼，必然發散到無法互相套用。**
這是整個問題裡唯一昂貴的部分。

### 為什麼可以做得很薄

公司 lib 是**包在 langgraph/langchain 之上的 wrapper**，底層仍是 langgraph。因此兩個實作
共用一組現成的型別詞彙——`BaseChatModel`、`BaseCheckpointSaver`、`CompiledStateGraph`。
介面不需要發明，langchain 已經定義好了。這消除了「對著未知的第二實作設計抽象」的風險。

### 介面

```python
# app/agent/runtime/base.py — 共用權威檔
# 三個建構點的 factory。型別一律用 langchain/langgraph base type：公司 wrapper 底層同為
# langgraph，兩個實作天然滿足同一組簽名，不另造中介型別。
class AgentRuntime(Protocol):
    def build_model(self) -> BaseChatModel: ...

    def build_checkpointer(self) -> BaseCheckpointSaver: ...

    def build_agent(
        self,
        *,
        model: BaseChatModel,
        tools: list,
        system_prompt: str,
        backend: FilesystemBackend,
        skills: list[str],
        checkpointer: BaseCheckpointSaver,
        middleware: list,
    ) -> CompiledStateGraph: ...
```

### 實作與選擇

| 實作 | 檔案 | 歸屬 |
|---|---|---|
| `DeepAgentsRuntime` | `app/agent/runtime/deepagents_runtime.py` | 共用權威檔（現行 `graph.py` 的建構內容搬入） |
| `CompanyRuntime` | `app/agent/runtime/company_runtime.py` | **公司獨佔、gitignored、永不搬** |

以 `AGENT_RUNTIME=deepagents|company` 選擇（預設 `deepagents`）。

**設為 `company` 但實作檔不存在時 MUST 啟動即失敗並指出缺少的檔案，NEVER fallback 回
deepagents。** 靜默降級會讓公司環境跑在錯誤的 runtime 上而無人察覺，比啟動失敗糟得多。

### 連帶效果

- `graph.py` 保留 `DashboardOverwriteBackend`、`register_harness_profile` 與對 runtime 的
  呼叫，`build_model()`／`create_deep_agent()` 的具體建構移入 `DeepAgentsRuntime`。
- `session_state.py` 的 `InMemorySaver` 改由 `runtime.build_checkpointer()` 提供；
  `reset()` 的既有語意（重建 checkpointer）維持。
- `auth.py`（156 行 token-exchange http client）只被家裡的 `build_model()` 呼叫。公司
  runtime 自行處理 auth，該檔在公司環境不會被執行——**不需要分叉，也不需要刪**。
- `chat_turn.py`、`events.py`、`middleware.py`、`repair_flow.py`、`tools/`、`prompts.py`、
  `api/**`、`engine/**` 全部不動：`astream_events`、`@tool`、middleware 都是 langchain 層
  契約，wrapper 會保留。

### 前提：公司環境仍安裝 deepagents

`graph.py` 保留在共用權威檔，而它 module 層 import 了 `deepagents`
（`FilesystemBackend`、`register_harness_profile`）。這在公司環境成立的前提是
**公司 registry 有 deepagents**——`pyproject.toml` 的既有註解「公司 registry 僅有 0.5.x；
開發與生產一律鎖同一版」已確認此點。

若此前提日後不成立（公司完全移除 deepagents），`DashboardOverwriteBackend` 與
`register_harness_profile` 必須一併移入 runtime 實作，接縫會從三個方法擴大到五個。
**這是本設計對外部條件最脆弱的一處，變更前 MUST 重新評估。**

### 待驗證（拿到公司 lib 文件後定案）

- **backend 參數的相容性**。`build_agent` 傳入的 `backend=DashboardOverwriteBackend(...)`
  是 deepagents 的 `FilesystemBackend` 子類。若公司 wrapper 不接受該型別，single-write
  不變量（`dashboard.html` 只能整份重寫）在公司環境會失效——這不是設定問題，是行為差異，
  MUST 在公司側實測確認。
- **`astream_events` 事件名稱是否一致**。若不一致，`events.py` 的正規化要加一層 mapping。
- **tools 的定義形式**。若公司 wrapper 不吃標準 `@tool` 物件，需要第四個 factory
  `build_tools()`。**本次刻意不預先加**——尚無資訊，加了就是憑空造分支。

---

## 接縫二：backend

### `pom.xml` — 預埋 `company` profile

```xml
<profiles>
  <profile>
    <id>company</id>
    <dependencies><!-- 公司 SDK --></dependencies>
    <build><!-- build-helper-maven-plugin 掛上 src/company/java --></build>
  </profile>
</profiles>
```

家裡預設不啟用。Maven 不解析未啟用 profile 的依賴，故家裡 build 與既有測試零影響。
公司在 `~/.m2/settings.xml` 寫一次 `<activeProfiles>`，之後照常 `mvn`，**零編輯 `pom.xml`**。

公司獨佔的 Java 實作（如 `UploadDecryptor` 的公司版，見
`2026-08-02-upload-decryption-hook-design.md`）放 `backend/src/company/java/`，
由該 profile 掛進 source root。家裡沒有那個目錄也沒有那個 jar，編不到即為正確行為。

### `application.yml` — 公司獨佔 profile 檔

共用 `application.yml` 一行不動。公司專屬設定放 `application-company.yml`，
以 `SPRING_PROFILES_ACTIVE=company` 啟用。與現有 `application-local.yml` 對稱，不引入新概念。

家裡放一份 `application-company.yml.example` 當文件。

---

## 接縫三：frontend

### `index.html` — 不動一個字

公司 lib 是 index.html 掛的 global script（非 npm 套件），因此 `package.json` 與 lock 檔
完全不受影響。改在 `vite.config.ts` 加 plugin，依 env 注入：

```ts
// 公司環境由 VITE_COMPANY_SCRIPT_URL 注入內部 library；未設時不注入任何標籤。
{
  name: 'company-script',
  transformIndexHtml: () =>
    url ? [{ tag: 'script', attrs: { src: url }, injectTo: 'head' as const }] : [],
}
```

### `main.tsx` — 不動一個字

新增共用的 `src/bootstrap/company.ts`：

```ts
// 公司初始化接縫：company.impl.ts 只存在於公司環境。import.meta.glob 對不存在的檔案
// 回傳空物件，故家裡 build 不會失敗——這是本接縫能成立的原因。
const impls = import.meta.glob<{ initialize: () => Promise<void> }>('./company.impl.ts');

export async function initCompanyRuntime(): Promise<void> {
  const load = impls['./company.impl.ts'];
  if (!load) return;
  await (await load()).initialize();
}
```

`main.tsx` 在 `createRoot` 之前 `await initCompanyRuntime()`。公司只需放進
`company.impl.ts`（gitignored、永不搬）並設 `VITE_COMPANY_SCRIPT_URL`；
`initialize` 怎麼呼叫、傳什麼參數，家裡不需要知道。

---

## 各自產生檔：`uv.lock`

`uv.lock` 記錄整張解析圖，**含每個套件的 index URL 與 hash**。公司走內部 PyPI mirror 時，
即使版本完全相同，URL 與 hash 也全不同 → 整檔差異。profile、overlay、3-way merge 都救不了
（lock 檔衝突實務上等於整檔重寫）。

**結論：`uv.lock` 列入永不搬清單，公司收到 `pyproject.toml` 後自行 `uv lock`。**
兩邊靠 `pyproject.toml` 的版本 pin 收斂，不靠 lock 檔。

`pyproject.toml` 現有的 `deepagents==0.5.5`（註解已載明「公司 registry 僅有 0.5.x」）
即為此做法。要做的是制度化：**關鍵套件 MUST pin 上下界**，讓兩邊各自解析出的結果等價。

---

## `.env.example`

純文件檔，由家裡統一維護——現況已經在做（檔內已有 `ERD_UPLOAD_DECRYPTION_ENABLED`
這類公司專用變數）。本設計新增的變數一併寫入：`AGENT_RUNTIME`、`VITE_COMPANY_SCRIPT_URL`、
`SPRING_PROFILES_ACTIVE=company`。公司零編輯。

---

## 搬運流程

### 永不搬清單

`.gitignore` 與同步腳本的 `--exclude` **雙重保險**：

```
.env
application-company.yml
uv.lock
backend/src/company/java/**
frontend/src/bootstrap/company.impl.ts
deepagent-service/app/agent/runtime/company_runtime.py
```

家裡以 `git archive HEAD` 產出壓縮檔——它天然排除所有 gitignored 檔案，是清單的第一道保險。

### 安全網：公司內 vendor branch

公司內 `git init`，兩條 branch：

- `vendor`：只放搬進來的家裡版本，公司**永不**在上面修改
- `company`：實際執行的分支

每次搬檔：覆蓋到 `vendor` → commit → `git merge vendor` 進 `company`。

價值有二：① 萬一仍有漏網的重疊，得到的是 3-way merge 而非整檔覆蓋；
② `git diff vendor company` 隨時能回答「公司到底改了什麼」——在完全隔離的環境裡，
這是唯一可稽核的差異清單。

---

## 測試

| 測試 | 內容 |
|---|---|
| `AgentRuntime` 契約測試 | 對 runtime 實作跑同一份測試（家裡跑 `DeepAgentsRuntime`，公司跑 `CompanyRuntime`）：三個 build 方法回傳的物件滿足對應 base type、`build_agent` 產出的 graph 可被 `astream_events` 驅動 |
| runtime 選擇測試 | `AGENT_RUNTIME=company` 而實作檔不存在時，啟動 MUST 失敗且錯誤訊息含缺少的模組名 |
| `initCompanyRuntime` 測試 | 無 `company.impl.ts` 時為 no-op 且不拋錯；以 mock glob 驗證有實作時會呼叫 `initialize()` |
| Vite plugin 測試 | 未設 `VITE_COMPANY_SCRIPT_URL` 時產出的 HTML 與現況逐字元相同 |
| backend profile 回歸 | 不啟用 `company` profile 時 `./mvnw test` 全綠（既有測試即為此保護） |

---

## 已知代價（不能消除，只能管理）

家裡**永遠測不到公司路徑**——所有接縫在家裡都是 no-op 或 deepagents 實作。對策有二：

1. **接縫保持極薄**，薄到肉眼可驗（前端接縫 6 行、Python 接縫僅三個 factory 方法）
2. **契約測試對兩個實作跑同一份**，使介面漂移不會靜默發生

---

## 不在範圍

- 公司內部 lib 的實際實作（由公司環境提供）
- 公司 tool 定義形式的適配（資訊不足，待公司 lib 文件到手後另議）
- 公司側 `settings.xml`、內部 registry、proxy、CA 憑證的設定內容（屬環境建置，非本專案程式碼）
- 自動化搬運工具（本設計只定義清單與紀律；搬運動作維持人工）
