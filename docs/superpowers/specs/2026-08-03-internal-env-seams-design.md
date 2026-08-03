# 公司環境接縫與單向搬運設計

**日期**：2026-08-03
**狀態**：設計完成，待實作
**範圍**：三側皆有改動——deepagent-service（主體）、backend（Maven profile + profile 設定檔）、frontend（Vite plugin + bootstrap 接縫）；外加搬運流程與紀律

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
| **共用權威檔** | 只有家裡 | 整檔取代 | `application.yml`、`pyproject.toml`、`uv.lock`、`requirements.txt`、`index.html`、`main.tsx`、`app/agent/**`（`runtime/internal_runtime.py` 除外）、`.env.example` |
| **公司獨佔檔** | 只有公司 | **取代後還原** | `src/internal/**`（含 `application-internal.yml`）、`internal.impl.ts`、`internal_runtime.py`、`internal/requirements-internal.txt` |
| **雙邊擁有檔** | 兩邊都寫 | **還原＋偵測上游變更後人工調和** | `backend/pom.xml` |
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
2. build-helper 掛上 `src/internal/java` 與 `src/internal/resources`

家裡的 `pom.xml` 完全不動——沒有 profile、沒有佔位符、沒有 build-helper。
內部座標也自然不會出現在 GitHub。

### 公司獨佔檔仍集中在 `backend/src/internal/`

```
backend/src/internal/
├── java/com/erd/cowork/storage/InternalUploadDecryptor.java
└── resources/application-internal.yml
```

維持獨立目錄而非散進 `src/main/`，是為了讓還原清單保持**一行一個目錄**；
公司新增檔案時清單不必跟著長。

Java 實作以 `@ConditionalOnProperty(name = "erd.upload.decryption.enabled",
havingValue = "true")` 掛上（介面見 `2026-08-02-upload-decryption-hook-design.md`）；
`application-internal.yml` 把該鍵設為 `true`，以 `SPRING_PROFILES_ACTIVE=internal` 啟用。
共用 `application.yml` 一行不動。

### 代價要說清楚

`backend/pom.xml` 的歷史顯示它**不是低變動檔**——repo 現有歷史雖僅兩天，其中就有兩次
依賴變更（新增 commons-io、移除 S3 路線）。每次上游動到它，公司都要人工套一次。

這個代價可接受的前提是**偵測必須可靠**：漏掉一次上游變更，公司就會缺依賴或帶著已移除
的依賴繼續跑，而症狀可能要很久才浮現。偵測機制見〈搬運流程〉的 `vendor-upstream` 錨點。

## 接縫三：frontend

### `index.html` — 不動一個字

公司 lib 是 index.html 掛的 global script（非 npm 套件），因此 `package.json` 與 lock 檔
完全不受影響。改在 `vite.config.ts` 加 plugin，依 env 注入：

```ts
// 公司環境由 VITE_INTERNAL_SCRIPT_URL 注入內部 library；未設時不注入任何標籤。
{
  name: 'internal-script',
  transformIndexHtml: () =>
    url ? [{ tag: 'script', attrs: { src: url }, injectTo: 'head' as const }] : [],
}
```

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
`internal.impl.ts`（家裡不存在；公司側 commit 於 `develop`，同步時還原）並設
`VITE_INTERNAL_SCRIPT_URL`；
`initialize` 怎麼呼叫、傳什麼參數，家裡不需要知道。

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
這類公司專用變數）。本設計新增的變數一併寫入：`AGENT_RUNTIME`、`VITE_INTERNAL_SCRIPT_URL`、
`SPRING_PROFILES_ACTIVE=internal`。公司零編輯。

---

## 搬運流程：replace-then-restore

拓撲共三段，全部自動、**無人工搬檔**：

```
GitHub（家裡，權威）  ──鏡像──▶  公司內 GitLab（remote `gl`，唯讀上游）
                                          │
                                          ▼  sync-vendor.sh
                              Azure 工作 repo（remote `origin`，主線 `develop`）
```

公司實際開發在 Azure；`gl` 只作為上游來源被 `fetch`，NEVER 推。

### ⚠️ GitLab MUST 是真鏡像，不是重新匯入

vendor commit 訊息記的是 `gl/master` 的 short hash，而**整個流程的稽核能力全靠它**——
它是「這份 code 對應到家裡哪一版」的唯一線索。

若 GitLab 是 `--mirror` 真鏡像，SHA 與 GitHub 完全相同，該 hash 可直接拿去 GitHub 對照。
若改成重新匯入／squash／重打包，SHA 會全部變成 GitLab 自己的，**對照能力當場歸零，
而且不會有任何錯誤訊息**。鏡像設定變更 MUST 視為破壞性變更。

`merge` 技術上可用，但**刻意不用**：merge 會在兩側同時動到同一檔時談判衝突，而依
〈硬約束〉，公司那側的改動永遠回不了家，同一個衝突會無限重複。改用
**取代後還原**——`git read-tree -u --reset` 把整個 worktree 換成上游（含上游的刪除，
這是 `git checkout` 做不到的），再把公司獨佔路徑撈回來。**因此永遠不會有 conflict**，
而且它不是「避開」紀律，是**強制執行**紀律：違反單邊擁有的檔案會被直接抹掉。

`read-tree` 只動 index 與 worktree、不動 HEAD，故 commit 直接長在 `develop` 線上，
`vendor-sync` 併回去是 fast-forward。

### 腳本（`scripts/sync-vendor.sh`，公司側維護）

```bash
set -euo pipefail

# 獨佔路徑清單是唯一事實來源：還原用它，守門的排除範圍也用它——
# 兩者 MUST 同源，否則清單一改就會漏守或誤報。
OWNED=(); EXCLUDES=()
while read -r ownedPath; do
  [ -n "$ownedPath" ] || continue
  OWNED+=("$ownedPath"); EXCLUDES+=(":(exclude)$ownedPath")
done < scripts/internal-owned-paths.txt

# 前置守門——三者皆 MUST 通過，否則停下來由人處理
test -z "$(git status --porcelain)"                                        # worktree 乾淨
test -z "$(git diff --name-only vendor-last develop -- . "${EXCLUDES[@]}")"  # 無越界改動
test -z "$(git ls-files --others --exclude-standard -- . "${EXCLUDES[@]}")"  # 無野生檔案

git fetch gl                                     # gl＝公司 GitLab 上的 GitHub 鏡像
UPSTREAM=$(git rev-parse --short gl/master)      # MUST 先解析，失敗即中止

# 雙邊擁有檔：上游動過就停下來人工調和。錨點 MUST 是 vendor-upstream（上次同步時的
# 上游 commit），NEVER 用 vendor-last——vendor-last 上的 pom.xml 已是公司版，
# 拿它跟上游比永遠有差，檢查會退化成每次都報錯。
while read -r mergePath; do
  [ -n "$mergePath" ] || continue
  git diff --quiet vendor-upstream gl/master -- "$mergePath" || {
    echo "上游變更需人工套用：$mergePath"
    echo "  git diff vendor-upstream gl/master -- $mergePath"
    exit 1
  }
done < scripts/manual-merge-paths.txt

git checkout -b vendor-sync develop
git read-tree -u --reset gl/master               # 整個換成上游（含刪除）
git checkout develop -- "${OWNED[@]}"
git add -A
git commit -m "vendor: 同步上游 @${UPSTREAM}"
git checkout develop
git merge --ff-only vendor-sync                  # 失敗＝同步期間 develop 被動過
git branch -d vendor-sync
git tag -f vendor-last     develop               # 公司側同步點
git tag -f vendor-upstream gl/master             # 上游側同步點——雙邊擁有檔的比較基準
git push origin develop --follow-tags            # origin＝Azure
```

人工調和完 `pom.xml` 之後，重跑腳本即可通過（`vendor-upstream` 尚未移動，
但差異已被套進 `develop`，人工確認後可加 `--skip-manual-check` 之類的旗標放行；
**該旗標 MUST 一次性、不得寫進預設流程**）。

### 公司獨佔路徑清單（`scripts/internal-owned-paths.txt`，單一事實來源）

```
internal/
backend/pom.xml
backend/src/internal
frontend/src/bootstrap/internal.impl.ts
deepagent-service/app/agent/runtime/internal_runtime.py
```

另有一份 `scripts/manual-merge-paths.txt`（雙邊擁有檔，目前只有 `backend/pom.xml`）。
它的內容 MUST 是上面清單的子集：先被還原保住公司版，再由上游變更偵測攔下需人工調和的情況。

`backend/src/internal` 一行同時涵蓋 Java 實作與 `application-internal.yml`
（兩者皆置於該目錄下，見〈接縫二〉）。

`uv.lock` **不在清單內**——公司走 `requirements.txt`，不讀 lock（見上節）。
`.env` 也不在，它 gitignored、不在 index，`read-tree` 不會碰它。

清單首次建立時這些路徑在 `develop` 上還不存在，`git checkout develop -- <path>` 會失敗；
**首次同步 MUST 在公司先 commit 各獨佔檔之後才跑。**

### 守門檢查是這個流程唯一的安全裝置

`read-tree --reset` 會**無聲抹掉**獨佔清單以外的一切公司改動；`git add -A` 則會把公司
遺留的 untracked 檔案**永久收編**成 vendor commit 的一部分，事後看起來像是上游帶來的。
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
| Vite plugin 測試 | 未設 `VITE_INTERNAL_SCRIPT_URL` 時產出的 HTML 與現況逐字元相同 |
| 同步腳本守門測試 | 在拋棄式 repo 上驗證四種情境**皆中止**：① 獨佔清單外有公司改動 ② 有野生 untracked 檔 ③ 上游動過 `backend/pom.xml` ④ `develop` 在同步期間被推進（`--ff-only` 失敗）。守門是整個流程唯一的安全裝置，MUST 有自動化驗證 |
| 上游變更偵測的錨點測試 | 連跑兩次同步（上游未動 `pom.xml`）第二次 MUST 通過——用以釘死錨點是 `vendor-upstream` 而非 `vendor-last`，後者會讓檢查每次都誤報 |

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
