# 公司環境接縫與單向搬運設計

**日期**：2026-08-03
**狀態**：設計完成，待實作
**範圍**：deepagent-service（`AgentRuntime` 接縫，主體）、frontend（`index.html` 雙邊擁有 ＋ bootstrap 接縫 ＋ `setUserId` 擴充點）、backend（`CurrentUser.deptId` ＋ `tsso.enabled` 條件註冊）、兩側 trace log、同步腳本與紀律。`backend/pom.xml`、`frontend/index.html` 皆為雙邊擁有檔，家裡不動

---

## 問題

同一份程式碼要同時活在兩個環境：

- **外部（家裡）**：GitHub `Michelle12369/cowork`，dev 用 OpenRouter，deepagents + `ChatOpenAI`
- **公司內**：走內部 registry 與內部 lib

兩邊的連通是**單向**的：GitHub 經公司內 GitLab 鏡像流入，公司的 commit **推不回 GitHub**。

已知會被公司編輯的有 `pom.xml`、`application.yml`、`.env.example`、`index.html`、
`main.tsx`，以及 deepagent-service 的 agent 建構層。

---

## 硬約束：單向連通

單向這件事比「有沒有 merge」更根本。`git fetch` 可用，`git merge` 技術上也可用——
但**公司的 commit 永遠回不到家裡**，於是：

- 共用檔上任何公司改動都是**孤兒**：家裡看不到，下一版照樣改同一行
- 那個衝突不是解一次就結束，是**每次同步都要重解一次**，且永遠解不完

所以策略不是「把衝突解好」，而是**讓重疊面積為零**：

> 每個檔案 MUST 只有一側能寫。任何「兩邊都想改」的地方，都要先轉成接縫。

單向也讓這條規則有了機械保障：公司**在物理上無法**把改動送回共用檔的權威來源。

---

## 核心原則：家裡開接縫，公司填實作

**家裡是唯一的權威寫入者。** 公司對 fetch 下來的共用檔一行都不改；任何「非改不可」的
地方回報給家裡，由家裡開一個接縫，一次性付出，之後該檔案永遠不再衝突。

### 三類檔案

| 類別 | 誰能寫 | 同步時 | 例子 |
|---|---|---|---|
| **共用權威檔** | 只有家裡 | 整檔取代 | `application.yml`（含 `tsso.enabled`）、`pyproject.toml`、`uv.lock`、`requirements.txt`、`main.tsx`、`app/agent/**`（`runtime/internal_runtime.py` 除外）、`.env.example` |
| **公司獨佔檔** | 只有公司 | **取代後還原** | `src/internal/java/**`、`application-internal.yml`、`internal.impl.ts`、`internal_runtime.py`、`internal/requirements-internal.txt` |
| **雙邊擁有檔** | 兩邊都寫 | **還原＋偵測上游變更後人工調和** | `backend/pom.xml`、`frontend/index.html` |
| **不在 repo 內** | 各自 | 不受影響 | `.env`（gitignored）、`~/.m2/settings.xml` |

**雙邊擁有是最後手段**，只在接縫成本超過分歧成本時採用（理由見〈接縫二〉）。
每多一個這類檔案，就多一份需要人工維護的分歧——清單長度即為技術債的度量。

「取代後還原」的機制見下方〈搬運流程〉；清單集中在 `scripts/internal-owned-paths.txt`。

---

## 接縫一（主體）：deepagent-service 的 `AgentRuntime`

### 為什麼這是主體

`app/engine/**`（1918 行）因既有的 ruff banned-api 規則完全不碰 LLM 框架，天然共用。
牆的另一側有 1289 行 import langchain/langgraph/deepagents，而公司要換掉的
`create_deep_agent`、model、checkpointer 正是這 1289 行的支柱。

**在公司改動永遠回不了家的前提下維護 1289 行雙份程式碼，必然發散到無法互相套用**——
每次上游同步，那 1289 行都要重新人工調和一次。這是整個問題裡唯一昂貴的部分。

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
| `InternalRuntime` | `app/agent/runtime/internal_runtime.py` | **公司獨佔**（家裡不存在；公司側 commit 於 `develop`，同步時還原） |

以 `AGENT_RUNTIME=deepagents|internal` 選擇（預設 `deepagents`）。

**設為 `internal` 但實作檔不存在時 MUST 啟動即失敗並指出缺少的檔案，NEVER fallback 回
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

### 已確認相容

- **backend 參數**：公司 wrapper 接受 deepagents 的 `FilesystemBackend` 子類，
  `DashboardOverwriteBackend` 可原樣傳入 → single-write 不變量
  （`dashboard.html` 只能整份重寫）在公司環境維持不變。
- **`astream_events` 事件名稱**：與 langchain 一致 → `events.py` 不需要 mapping 層。

這兩點確認把接縫鎖在最小形狀：**`AgentRuntime` 維持三個方法**，
`chat_turn.py`（381 行）、`events.py`（180 行）、`middleware.py`（133 行）全數共用。

### 待驗證

- **tools 的定義形式**。若公司 wrapper 不吃標準 `@tool` 物件，需要第四個 factory
  `build_tools()`。**本次刻意不預先加**——尚無資訊，加了就是憑空造分支。

---

## 接縫二：backend

### 為什麼這裡不做接縫

前三個接縫（Python runtime、Vite 注入、bootstrap glob）都能做到公司零編輯且家裡零成本。
`pom.xml` 不行——要維持單一份 pom，代價是 `internal` profile ＋ GAV property 佔位符
＋ build-helper ＋ 公司端 `settings.xml` ＋ 家裡的替身驗證，五層間接只為了讓公司多一個
`<dependency>`。

**接縫成本超過分歧成本時，正確答案是接受分歧並偵測它，而不是硬造接縫。**
`pom.xml` 因此改列**雙邊擁有檔**：公司持有自己的版本，上游一有變更就停下來人工調和。

### 公司側的 `pom.xml`

＝上游版本 ＋ 兩塊：

1. 公司 SDK 的 `<dependency>`（座標直接寫，因為這份 pom 不會回到 GitHub）
2. build-helper 的 `add-source`，掛上 `src/internal/java`

家裡的 `pom.xml` 完全不動——沒有 profile、沒有佔位符、沒有 build-helper。
內部座標也自然不會出現在 GitHub。

### 公司獨佔檔的位置

```
backend/src/internal/java/com/erd/cowork/storage/InternalUploadDecryptor.java
backend/src/main/resources/application-internal.yml
```

**Java 走獨立 source root**（`src/internal/java`），讓還原清單以一行目錄涵蓋，
公司日後新增類別時清單不必跟著長。

**設定檔留在 `src/main/resources/`**，與 `application.yml`／`application-local.yml`
並排——Spring profile 檔放在慣例位置才找得到，且不需要為它多掛一個 resource 目錄。
代價是還原清單多一行具名檔案；只有一個檔，可接受。

Java 實作以 `@ConditionalOnProperty(name = "erd.upload.decryption.enabled",
havingValue = "true")` 掛上（介面見 `2026-08-02-upload-decryption-hook-design.md`）；
`application-internal.yml` 把該鍵設為 `true`，以 `SPRING_PROFILES_ACTIVE=internal` 啟用。
共用 `application.yml` 一行不動。

### 代價要說清楚

`backend/pom.xml` 的歷史顯示它**不是低變動檔**——repo 現有歷史雖僅兩天，其中就有兩次
依賴變更（新增 commons-io、移除 S3 路線）。每次上游動到它，公司都要人工套一次。

這個代價可接受的前提是**偵測必須可靠**：漏掉一次上游變更，公司就會缺依賴或帶著已移除
的依賴繼續跑，而症狀可能要很久才浮現。偵測機制見〈搬運流程〉的 `$LAST_UPSTREAM` 錨點；
實際調和動作在同步 branch 上完成，隨該次 PR 一起進 `develop`。

## 接縫三：frontend

### `index.html` — 雙邊擁有檔，公司直接編輯

最初設計是用 `vite.config.ts` 讀 env 變數、透過 Vite plugin 把 `<script>` 動態注入
`index.html`，讓 `index.html` 本身維持「不動一個字」。實作後發現這條路走不通：plugin
讀的是 `process.env`，而 `vite.config.ts` 在 Vite 載入 `.env` 檔**之前**就已經執行，
導致 `frontend/.env.local` 設了值也不生效——症狀是 script 靜默不注入、沒有任何錯誤
訊息。要修就得多引入 `loadEnv()` 這層設定，而 `index.html` 本身極少變動，直接讓它
成為雙邊擁有檔的成本更低。

改採的做法：公司 lib 仍是 index.html 掛的 global script（非 npm 套件），因此
`package.json` 與 lock 檔完全不受影響；但改由**公司側直接編輯 `frontend/index.html`**，
在 `<head>` 依相依順序加三支 classic script（NEVER `type="module"`、NEVER
`async`/`defer`，才能保證在 `main.tsx` 之前同步執行完畢）。`frontend/index.html`
因此同時列進兩份清單：

- `scripts/internal-owned-paths.txt`——同步時先被上游整棵樹蓋掉，再從公司的
  `develop` 撈回來，公司的 script 標籤不會被同步抹掉。
- `scripts/manual-merge-paths.txt`——上游若也動過 `index.html`，同步 commit 的
  body 會多一行「需人工調和」提示，人工比對兩邊版本後決定怎麼合併。

兩份清單都要加：只加前者，上游改了 `index.html` 沒人會知道；只加後者，公司的
script 標籤每次同步都會被抹掉。

### `main.tsx` — 不動一個字

新增共用的 `src/bootstrap/internal.ts`：

```ts
// 公司初始化接縫：internal.impl.ts 只存在於公司環境。import.meta.glob 對不存在的檔案
// 回傳空物件，故家裡 build 不會失敗——這是本接縫能成立的原因。
const impls = import.meta.glob<{ initialize: () => Promise<void> }>('./internal.impl.ts');

export async function initInternalRuntime(): Promise<void> {
  const load = impls['./internal.impl.ts'];
  if (!load) return;
  await (await load()).initialize();
}
```

`main.tsx` 在 `createRoot` 之前 `await initInternalRuntime()`。公司只需放進
`internal.impl.ts`（家裡不存在；公司側 commit 於 `develop`，同步時還原），並確保
`index.html` 已掛好三支 script（見上節）；`initialize` 怎麼呼叫、傳什麼參數，家裡不需要知道。

### `internal.impl.ts` 範例（公司側撰寫）

最可能的用途就是 CLAUDE.md 已載明的那件事——**公司環境的 `X-User-Id` 改由 SSO 提供**，
取代 v1 的 localStorage 匿名 UUID：

```ts
// frontend/src/bootstrap/internal.impl.ts
// 公司環境專屬；家裡不存在此檔，由 internal.ts 的 import.meta.glob 偵測後載入。
// 公司 library 是 index.html 掛的 global script，這裡不再自己載入，只做 guard + init。
import { setUserId } from '@/api/apiClient';

// global script 掛在 window 上，沒有 npm 套件也沒有 .d.ts，型別需自行宣告。
declare global {
  interface Window {
    ErdSso?: {
      init(options: { appId: string }): Promise<void>;
      getUserId(): string;
    };
  }
}

export async function initialize(): Promise<void> {
  const sso = window.ErdSso;
  if (!sso) {
    // index.html 掛的是 classic script（非 module），在 main.tsx 之前就同步執行完畢。
    // 走到這裡代表 index.html 的 script 標籤沒掛好或載入失敗——MUST 中止，
    // NEVER 靜默降級成匿名身分，否則使用者會以隨機 UUID 開 session，且從畫面上完全看不出異常。
    throw new Error('公司 SSO library 未載入：檢查 index.html 的 script 標籤');
  }

  await sso.init({ appId: import.meta.env.VITE_INTERNAL_APP_ID });
  setUserId(sso.getUserId());
}
```

### 這個範例需要主線開一個小擴充點

`apiClient.ts` 目前把 `erd_user_id` 這個 localStorage key 藏在模組內，
且 `getUserId()` 同時被 axios interceptor 與 `agentApi.ts` 的 raw `fetch` 使用。

公司實作若直接 `localStorage.setItem('erd_user_id', ...)` 硬寫同一個 key，
可以完全不改主線——但那是**隱性耦合**：哪天主線改了 key 名稱，公司端不會編譯錯誤、
不會有任何警告，只會安靜地退回匿名 UUID。

因此主線 `apiClient.ts` MUST 補一個具名擴充點，把耦合顯性化：

```ts
export function setUserId(userId: string): void {
  localStorage.setItem(USER_KEY, userId);
}
```

三行，且對家裡完全無副作用（沒有人呼叫它）。**這是本設計唯一要求主線為公司開的洞**——
換來的是耦合有型別、有名字、改名時會編譯失敗。

---

## 接縫四：backend 身分來源（`tsso.enabled`）

家裡 v1 無 SSO，身分靠 `X-User-Id` header ＋ `local-dev` fallback。公司環境改由 TSSO 提供，
且多一個 `deptId` 維度。

### `CurrentUser` 增加 `deptId`

`deptId` 與 `userId` 同屬請求身分，一起由 interceptor 填入。**request scope 不跨執行緒的既有
約束不變**——async/SSE 邊界前 MUST 先值物件化，`deptId` 一併適用。

### interceptor 依 `tsso.enabled` 條件註冊

| `tsso.enabled` | 行為 |
|---|---|
| `false` 或未設（家裡） | 註冊 `CurrentUserInterceptor`，讀 `X-User-Id`／`X-Dept-Id`，缺值 fallback `local-dev` |
| `true`（公司） | **不註冊**主線 interceptor；身分由公司 TSSO 提供，公司在 `src/internal/java` 放自己的 `WebMvcConfigurer` |

作法沿用 repo 既有的 `UploadDecryptor` pattern：`CurrentUserInterceptor` 掛
`@ConditionalOnProperty(name = "tsso.enabled", havingValue = "false", matchIfMissing = true)`，
`WebConfig` 改注入 `ObjectProvider<CurrentUserInterceptor>` 並以 `ifAvailable(...)` 註冊。
Spring 允許多個 `WebMvcConfigurer` 並存，故公司側新增自己的 config 不需要主線配合。

**`tsso.enabled=true` 但公司尚未提供 config 時**，沒有任何 interceptor 會填 `CurrentUser`。
此時 MUST 在啟動時記 WARN 明講這件事——否則症狀會延後到第一次查詢才以 null userId 爆開，
離真正的原因很遠。

---

## 可追蹤性：兩側的 trace log

公司環境的問題家裡重現不了，log 是唯一的線索。**接縫的分支點 MUST 留下 log**——
「走了哪一條路」比「發生什麼錯」更難事後推斷。

| 位置 | 等級 | 內容 |
|---|---|---|
| `WebConfig.addInterceptors` | INFO／WARN | 有無註冊主線 interceptor、`tsso.enabled` 值 |
| `CurrentUserInterceptor.preHandle` | DEBUG | 解析出的 `userId`、`deptId`、是否走 fallback |
| `load_runtime()` | INFO | 選中的 runtime 名稱與模組路徑 |
| `DeepAgentsRuntime.build_model()` | INFO | model 名稱、base-url 有無設定、auth 模式 |

沿用兩側既有風格：Java `log.info("... key={}", value)`、Python
`logger.info("... key=%s", value)`。

**NEVER log**：api key、token、完整 prompt／HTML、使用者資料內容。`userId`／`deptId`
是識別碼不是資料內容，可記；`base-url` 只記「有無設定」不記值，避免內部位址外流到 log 蒐集系統。

---

## Python 依賴：`uv.lock` 與 `requirements.txt` 的分工

兩邊用**不同的安裝路徑**，而這個分工現況已經存在：

| 檔案 | 誰用 | 產生方式 |
|---|---|---|
| `uv.lock` | 家裡（Dockerfile `uv sync --frozen`） | `uv lock` |
| `requirements.txt` | **公司環境** | `uv export --no-dev --no-hashes --format requirements-txt` |

因此 `uv.lock` **不進公司獨佔路徑清單**——公司不讀它，同步時被上游整檔取代是正確行為。
現行 export 命令的 `--no-hashes` 也已經對內部 mirror 友善：mirror 的 artifact 與公有
PyPI 的 hash 不同，帶 hash 會直接安裝失敗。

公司專屬套件（內部 wrapper lib）NEVER 進共用 `pyproject.toml`——它在家裡解析不到，
會讓家裡的 `uv lock` 直接失敗。改放公司獨佔的 `internal/requirements-internal.txt`，
於共用 `requirements.txt` 之後安裝。

`pyproject.toml` 現有的 `deepagents==0.5.5`（註解已載明「公司 registry 僅有 0.5.x」）
維持不變：關鍵套件 MUST pin 到公司 registry 供得起的版本，這是共用檔的既有紀律。

### ⚠️ `requirements.txt` 是生成物——漂移只會在公司炸

`requirements.txt` 由 `uv.lock` 匯出。若改了依賴卻忘記重新 export，家裡完全無感
（家裡走 `uv sync --frozen`，讀的是 lock），**公司卻會靜默裝到舊依賴**。

這正是〈已知代價〉所說「家裡永遠測不到公司路徑」的具體案例，而且是最容易發生的一種——
它不需要任何人犯錯，只需要有人忘記一個步驟。

對策：一致性檢查納入家裡的 CI／pre-commit：

```bash
uv export --no-dev --no-hashes --format requirements-txt -o - \
  | diff -q - requirements.txt
```

不一致即失敗。**這是少數能在家裡就攔下公司環境問題的機制**，價值遠高於它的實作成本。

---

## `.env.example`

純文件檔，由家裡統一維護——現況已經在做（檔內已有 `ERD_UPLOAD_DECRYPTION_ENABLED`
這類公司專用變數）。本設計新增的變數一併寫入：`AGENT_RUNTIME`、
`VITE_INTERNAL_APP_ID`、`SPRING_PROFILES_ACTIVE=internal`。公司零編輯。

---

## 搬運流程：replace-then-restore

**無人工搬檔**；四步裡只有第三步需要人：

```
① 家裡 push ─────────▶ GitHub master              （家裡，權威）
                              │ 自動鏡像
② 　　　　　　　　　　  ▼
                       公司內 GitLab  gl/master     （唯讀上游）
                              │
③ 有人跑 sync-upstream.sh ────┤  ← 唯一的人工動作
                              ▼
④                      Azure 工作 repo  develop     （公司主線，可推）
```

腳本在 **Azure 工作 repo 的 clone** 裡執行，remote 設定：

```bash
origin  https://dev.azure.com/.../cowork      # 公司工作 repo，可推
gl      https://gitlab.<公司>/.../cowork      # GitHub 鏡像，只讀

git remote set-url --push gl no_push          # 從物理上擋掉誤推鏡像
```

### 在哪條 branch 上跑

**MUST 在 `develop` 上、worktree 乾淨時執行**，並由腳本第一道守門強制檢查。

技術上從任何 branch 都能跑（`checkout -b upstream-sync develop` 不依賴當前分支），
但腳本結束時會把使用者留在 `develop`——從 feature branch 跑完會被莫名切走，
更糟的是造成「我在同步自己的 branch」的錯覺，實際上同步的永遠是 `develop`。

最乾淨的做法是**準備一份專用 clone 或 worktree 只跑同步**，不與任何人的工作區共用。

### 守門的觀察範圍僅限 `develop`

若公司的越界改動還躺在未合併的 feature branch 上，本次同步看不到；
等它們 merge 進 `develop`，會在**下一次**同步才被攔下。

這不是漏洞（最終仍會被抓到），但**延遲是真的**——攔下的時間點可能離犯錯的人很遠。
公司側的 code review MUST 一併把關「共用檔不得修改」，不要只依賴同步時的守門。

### 用語

「上游（upstream）」＝ GitHub 那份，經 GitLab 鏡像被公司消費——**唯讀、不可修改、
只能整批接收**。腳本、tag、commit 訊息一律用 upstream 一詞，NEVER 用 vendor
（vendor branch 是同一套做法的業界術語，但這裡的上游是自家程式碼，用 vendor 易誤導）。

### ⚠️ `gl/master` 未必是最新

取決於 GitLab 鏡像排程有沒有跑過。這不影響正確性——只是同步到較舊的 commit，
且同步 commit 訊息記了是哪一版，事後查得到。但**家裡剛 push 就要公司同步時，
MUST 先確認鏡像已執行**，否則會以為同步過了其實沒有。

### ⚠️ GitLab MUST 是真鏡像，不是重新匯入

同步 commit 訊息記的是 `gl/master` 的 short hash，而**整個流程的稽核能力全靠它**——
它是「這份 code 對應到家裡哪一版」的唯一線索。

若 GitLab 是 `--mirror` 真鏡像，SHA 與 GitHub 完全相同，該 hash 可直接拿去 GitHub 對照。
若改成重新匯入／squash／重打包，SHA 會全部變成 GitLab 自己的，**對照能力當場歸零，
而且不會有任何錯誤訊息**。鏡像設定變更 MUST 視為破壞性變更。

`merge` 技術上可用，但**刻意不用**：merge 會在兩側同時動到同一檔時談判衝突，而依
〈硬約束〉，公司那側的改動永遠回不了家，同一個衝突會無限重複。改用
**取代後還原**——`git read-tree -u --reset` 把整個 worktree 換成上游（含上游的刪除，
這是 `git checkout` 做不到的），再把公司獨佔路徑撈回來。**因此永遠不會有 conflict**，
而且它不是「避開」紀律，是**強制執行**紀律：違反單邊擁有的檔案會被直接抹掉。

`read-tree` 只動 index 與 worktree、不動 HEAD，故同步 commit 直接長在從 `develop`
切出的 branch 上，是一顆普通 commit。

### 落地方式：feature branch → 人工確認與適配 → PR

同步 **NEVER 直接推 `develop`**。流程是：

1. 從 `develop` 切一條 `sync/upstream-<shorthash>`
2. 腳本在該 branch 上完成 replace-then-restore 並 commit
3. **人在這條 branch 上**確認 diff、做 `pom.xml` 人工調和、以及**接縫適配**
4. 發 PR 進 `develop`，公司 CI 跑過後合併

第 3 步是這個流程存在的理由。上游若改動了接縫（例如 `AgentRuntime` 增加方法），
`internal_runtime.py` MUST 在**同一個 PR**裡跟著改——否則 `develop` 會從同步落地那刻
壞到有人補救為止，而公司側 CI 是第一個、也是唯一一個能發現它的地方（家裡永遠測不到
公司路徑）。

**PR 合併時 develop 已前進也安全**：同步 branch 相對其切出點並未修改任何獨佔路徑
（`read-tree` 抹掉、`checkout` 原樣還原，淨變更為零），故三方合併會保留 `develop`
上較新的獨佔檔內容，不會倒退。

### 基準點用 commit 而非 tag

守門與上游變更偵測都需要「上次同步到哪」。**NEVER 用推分支時移動的 tag**——PR 可能
被放棄或擱置一週，基準就會指向從未落地的狀態，下一次同步的判斷全錯。

改為從 `origin/develop` 的歷史找最後一顆同步 commit，並自其 trailer 取上游 SHA。
基準因此**只反映真正合併進 develop 的同步**，被放棄的 PR 不會污染它。

代價：同步 PR **MUST NOT squash 合併**（squash 會丟掉 trailer）。這條規則 MUST 寫進
公司側的 PR 流程說明。

### 腳本（`scripts/sync-upstream.sh`，公司側維護）

```bash
set -euo pipefail

# 獨佔路徑清單是唯一事實來源：還原用它，守門的排除範圍也用它——
# 兩者 MUST 同源，否則清單一改就會漏守或誤報。
OWNED=(); EXCLUDES=()
while read -r ownedPath; do
  [ -n "$ownedPath" ] || continue
  OWNED+=("$ownedPath"); EXCLUDES+=(":(exclude)$ownedPath")
done < scripts/internal-owned-paths.txt

# --multiple 才會把兩個參數都當 remote；`git fetch gl origin` 會把 origin 當成 gl 上的 refspec 而失敗。
git fetch -q --multiple gl origin                 # gl＝GitHub 鏡像；origin＝Azure

# 基準點：origin/develop 上最後一顆已落地的同步 commit，及其記錄的上游 SHA。
LAST_SYNC=$(git log origin/develop --grep='^upstream-sync: ' -1 --format=%H)
LAST_UPSTREAM=$(git log -1 --format=%B "$LAST_SYNC" | sed -n 's/^Upstream-Commit: //p')
test -n "$LAST_SYNC" && test -n "$LAST_UPSTREAM"   # 空值＝首次同步，MUST 人工 bootstrap

# 前置守門——全部 MUST 通過，否則停下來由人處理
test "$(git rev-parse --abbrev-ref HEAD)" = develop                          # 在 develop 上
test -z "$(git status --porcelain)"                                          # worktree 乾淨
test -z "$(git diff --name-only "$LAST_SYNC" develop -- . "${EXCLUDES[@]}")"  # 無越界改動
test -z "$(git ls-files --others --exclude-standard -- . "${EXCLUDES[@]}")"   # 無野生檔案

UPSTREAM=$(git rev-parse gl/master)               # MUST 先解析，失敗即中止

# 雙邊擁有檔：列出上游這次動過的，交給第 3 步人工調和。錨點 MUST 是 $LAST_UPSTREAM
# （上次同步的上游 commit），NEVER 用 $LAST_SYNC——後者的 pom.xml 已是公司版，
# 拿它跟上游比永遠有差，檢查會退化成每次都報。
MANUAL_NOTES=""
while read -r mergePath; do
  [ -n "$mergePath" ] || continue
  git diff --quiet "$LAST_UPSTREAM" gl/master -- "$mergePath" \
    || MANUAL_NOTES="${MANUAL_NOTES}需人工調和：${mergePath}"$'\n'
done < scripts/manual-merge-paths.txt

SYNC_BRANCH="sync/upstream-$(git rev-parse --short gl/master)"
git checkout -b "$SYNC_BRANCH"
git read-tree -u --reset gl/master                # 整個換成上游（含刪除）
git checkout develop -- "${OWNED[@]}"             # 還原公司獨佔路徑（淨變更為零）
git add -A

# 待辦寫進 commit body，PR 上直接看得到，NEVER 只 echo 到終端機。
# --allow-empty 是必要的：雙邊擁有檔還原後淨變更為零，若上游這次只動了 pom.xml，
# 沒有 --allow-empty 會 commit 失敗，連帶丟掉基準指標與待辦註記。
git commit --allow-empty -m "upstream-sync: 同步至 $(git rev-parse --short gl/master)" \
           -m "${MANUAL_NOTES}" -m "Upstream-Commit: ${UPSTREAM}"
git push -u origin "$SYNC_BRANCH"

echo "已推出 $SYNC_BRANCH。接著人工完成："
echo "  1. 檢視 diff，確認上游改動"
echo "  2. 調和上列雙邊擁有檔"
echo "  3. 接縫適配（上游若改了 AgentRuntime 等介面，internal 實作要跟著改）"
echo "  4. 發 PR 進 develop，CI 綠燈後合併（MUST NOT squash）"
```

### 公司獨佔路徑清單（`scripts/internal-owned-paths.txt`，單一事實來源）

```
internal/
backend/pom.xml
backend/src/internal
backend/src/main/resources/application-internal.yml
frontend/src/bootstrap/internal.impl.ts
deepagent-service/app/agent/runtime/internal_runtime.py
```

另有一份 `scripts/manual-merge-paths.txt`（雙邊擁有檔，目前只有 `backend/pom.xml`）。
它的內容 MUST 是上面清單的子集：先被還原保住公司版，再由上游變更偵測攔下需人工調和的情況。

`uv.lock` **不在清單內**——公司走 `requirements.txt`，不讀 lock（見上節）。
`.env` 也不在，它 gitignored、不在 index，`read-tree` 不會碰它。

清單首次建立時這些路徑在 `develop` 上還不存在，`git checkout develop -- <path>` 會失敗；
**首次同步 MUST 在公司先 commit 各獨佔檔之後才跑。**

### 守門檢查是這個流程唯一的安全裝置

`read-tree --reset` 會**無聲抹掉**獨佔清單以外的一切公司改動；`git add -A` 則會把公司
遺留的 untracked 檔案**永久收編**成 同步 commit 的一部分，事後看起來像是上游帶來的。
這兩者都沒有任何警告。

前置守門的三項檢查把「清單漏列」這個唯一失敗模式從**靜默資料遺失**轉成**同步中止**。
**NEVER 為了讓同步跑完而跳過它們。**

### 家裡側的配合：獨佔路徑 NEVER 加進 `.gitignore`

公司必須把獨佔檔 commit 到 `develop`，`git checkout develop -- <path>` 才有東西可還原。
若家裡把這些路徑寫進 `.gitignore`，該 `.gitignore` 會隨 ③ 傳進公司，使那些檔案變成
ignored，公司得靠 `git add -f` 才能追蹤——一個沒必要的陷阱。

家裡不需要 gitignore 它們：**這些檔案在家裡根本不存在**，`gl/master` 自然不含它們。
唯一維持 gitignored 的是 `.env`（兩側皆從不追蹤）。

---

## 測試

| 測試 | 內容 |
|---|---|
| `AgentRuntime` 契約測試 | 對 runtime 實作跑同一份測試（家裡跑 `DeepAgentsRuntime`，公司跑 `InternalRuntime`）：三個 build 方法回傳的物件滿足對應 base type、`build_agent` 產出的 graph 可被 `astream_events` 驅動 |
| runtime 選擇測試 | `AGENT_RUNTIME=internal` 而實作檔不存在時，啟動 MUST 失敗且錯誤訊息含缺少的模組名 |
| `initInternalRuntime` 測試 | 無 `internal.impl.ts` 時為 no-op 且不拋錯；以 mock glob 驗證有實作時會呼叫 `initialize()` |
| `setUserId` 測試 | 寫入後 `getUserId()` 回傳同一值，且 axios interceptor 與 `agentApi` 的 raw fetch 兩條路徑都帶到新 id（兩者共用 `getUserId()`，MUST 一起驗） |
| `index.html` 雙邊擁有檔測試 | 未編輯 `index.html` 時產出的 HTML 與現況逐字元相同（見 `scripts/test-sync-upstream.sh`） |
| 同步腳本守門測試 | 在拋棄式 repo 上驗證**皆中止**：① 獨佔清單外有公司改動 ② 有野生 untracked 檔 ③ 不在 `develop` 上 ④ 找不到基準同步 commit（首次同步未 bootstrap）。守門是整個流程唯一的安全裝置，MUST 有自動化驗證 |
| 雙邊擁有檔提示測試 | 上游動過 `backend/pom.xml` 時，同步 commit 的 body MUST 含該路徑的待辦行（PR 上看得到）；未動過時 body 不含待辦 |
| 錨點回歸測試 | 連跑兩次同步（上游未動 `pom.xml`）第二次 MUST 不再列出待辦——用以釘死錨點是 `$LAST_UPSTREAM` 而非 `$LAST_SYNC`，後者會讓提示每次都出現 |
| 基準點來源測試 | 同步 branch 已推出但 PR 未合併時，再跑一次腳本的基準 MUST 仍是舊的同步 commit（證明基準取自 `origin/develop` 而非分支或 tag） |

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
