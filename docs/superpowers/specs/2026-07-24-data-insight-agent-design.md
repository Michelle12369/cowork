# Data Insight Agent — 設計規格

日期:2026-07-24 · 分支:`feat/data-insight-agent` · 狀態:M1 規劃中

## 1. 產品目標

在既有 Cowork · Data Studio 之上,新增「**data analyst agent**」能力:使用者選擇資料來源,
在對話中由 agent 探索、分析(merge/彙總、3σ、outlier、dig-in)並產出 insight;
對話收斂出的分析手法可「蒸餾」成可複用資產(skill 三件套),支撐每日 dashboard 與例行 insight。

**明確不做**:通用 cowork agent(任意檔案/任意任務)、無限互動的 BI 工具。

## 2. 需求 → 功能對照

| # | 需求 | 功能 | 機制 |
|---|---|---|---|
| 1 | 接收各系統資料、選來源 | 來源目錄 + 兩種 connector | CSV=具名槽位+上傳觸發;API=連線設定+執行時拉取;統一落成版本化 snapshot 檔(FileStorage) |
| 2 | 對話分析產 insight | Analysis Agent | DuckDB 直查 snapshot;`run_sql` 通用運算+具名方法;自動 profiling 開場;反問收斂;釘選 |
| 3 | 手法輸出可複用 | 蒸餾 → skill 三件套 | `SKILL.md` + `pipeline.yaml` + `queries/*.sql`(SQL 逐字凍結);scope 先只做 personal(team/org 暫緩) |
| 4 | 每日 dashboard | Pipeline Runner(無 LLM) | 觸發:on-demand(預設)/on-upload/cron(僅推送訂閱,如日報);注入沿用 `__ERD_DATA__` 契約;schema `expect` 大聲失敗 |
| 5 | 隔天重跑得 insight | Insight 窄呼叫 | pipeline 結果 + SKILL.md 指引 → 一次受控 LLM 呼叫;`when` 條件為規則引擎(告警訂閱暫緩,見 §8/§9) |
| 6 | 看了 dashboard 再問 | Dig-in 回流 | 從 dashboard 開 session,context 預載 pipeline 定義+當期結果 |

## 3. 整體架構

```
┌───────────────────── Frontend(沿用 React)─────────────────────────┐
│   對話介面 · 來源選擇 · 釘選板 · Dashboard/日報 · Skill/Pipeline 庫   │
└────────────┬──────────────────────────────▲───────────────────────┘
             │ SSE(沿用 AgentEvent:STEP/TOKEN/ANSWER/ARTIFACT/QUESTION…)
             ▼                              │ dig-in 回流
┌──────────────────── Java Spring Backend(沿用+擴充)────────────────┐
│  Session/User(X-User-Id)      FileStorage → Snapshot Store(版本化)│
│  Skill/Pipeline Registry(3 層 scope + 用量/休眠)                  │
│  Dashboard Store · ArtifactAssembler(注入,沿用)                   │
│  Scheduler(on-demand / on-upload / cron + run history)            │
│  Connectors:CsvConnector(槽位)· ApiConnector(設定+拉取落地)       │
│  AgentOrchestrator 對話層(持久化/歸屬/SSE/cancel,對模式無知)      │
│  AgentProvider SPI ─┬─ DashboardAgentProvider(+harden 修復迴圈)   │
│                     └─ LangGraphAnalysisProvider(analysis,§16)    │
└────────────┬───────────────────────────────────┬──────────────────┘
             │ 內部 HTTP+SSE                      │ 內部 HTTP
             ▼                                   ▼
┌──────【新建】Python Agent Service(FastAPI)────────────────────────┐
│  /chat          互動迴路:LangGraph agent(§4)                      │
│  /run-pipeline  排程迴路:純執行 pipeline.yaml(無 agent;           │
│                 可選 withInsight=一次窄 LLM 呼叫)                   │
│  DuckDB(嵌入式:掛 view/materialize → 鎖門;per-run 用完即丟)      │
│  LangGraph checkpointer(對話中間狀態,唯一屬於 Python 的狀態)       │
│  LLM:OpenAI-compatible endpoint(gpt-oss 120b / internal LLM)         │
│  Langfuse tracing(LangChain callback;env-driven,未設定即 no-op)  │
└────────────────────────────────────────────────────────────────────┘
```

**狀態歸屬**:Java 擁有所有產品資料(session、snapshot、skill、pipeline、dashboard、run 紀錄);
Python 只擁有對話中間狀態,可隨時重啟。LangChain 型別止步於 Python 服務內,對外只講 AgentEvent 協定。

## 4. Agent Orchestrator(LangGraph)節點設計

基底 `create_agent`(ReAct loop),外圍 middleware + interrupt:

```
 START ─► load_context ─► agent(LLM 推理)──①最終回答──► END
          (before_agent:     │    ▲   │
           skills frontmatter │    │   └③反問─► clarify(interrupt,
           + profile 摘要     ②    │              QUESTION 事件)──┐
           + dig-in 脈絡)     ▼    │                              │
                            tools ─┘◄────────────────────────────┘
                              │
                              └ distill_and_save ─► confirm_save(interrupt,
                                                    蒸餾三件套草稿確認)─► 回呼 Java
 另掛 summarize middleware(長對話壓縮)
```

**tools 節點工具清單**(agent 的全部能力面):

| 類別 | 工具 | 說明 |
|---|---|---|
| 探查 | `get_schema` / `preview_data` / `profile_source` | 欄位、樣本、確定性 profiling(共同欄位、分佈) |
| 運算 | `run_sql` | 通用運算,對已鎖門 DuckDB 執行;dry-run+錯誤回饋重試 ×3 |
| 運算 | `trend_3sigma` / `flag_outliers` | 具名方法:標準化統計,不容 agent 發揮 |
| 知識 | `search_skills` / `load_skill` | progressive disclosure 載入 skill 全文 |
| 產出 | `render_dashboard` | 產 **dashboard spec(JSON)** → 確定性 renderer 組 HTML(§7) |
| 產出 | `distill_and_save` | 軌跡+釘選 → 三件套草稿 → confirm 後回呼 Java |
| 互動 | `ask_user` | 觸發 clarify interrupt(QUESTION 事件) |

**DeepAgents 元件採用策略**:SkillsMiddleware 第一天用(skill 格式即 Anthropic SKILL.md);
summarization 對話變長時掛;todo 規劃/sub-agents 等「放手調查」需求出現才加。

## 5. Skill 三件套(分析手法的存檔格式)

```
skills/<scope>/<name>/
├── SKILL.md          # 給 LLM:何時用、怎麼解讀、dig-in playbook
├── pipeline.yaml     # 給機器:見下
└── queries/*.sql     # 對話中驗證過的 SQL「原文」,逐字凍結
```

```yaml
version: 3
params: { window_days: 30 }
sources:
  defects:  { slot: csv/defect-log,    expect: [machine_id: text, ts: timestamp, defect_rate: double] }
  machines: { slot: api/mes-machines,  expect: [machine_id: text, line: text] }
steps:
  - { id: joined,   sql: queries/01_join.sql }
  - { id: summary,  sql: queries/02_summary.sql }
  - { id: outliers, method: flag_outliers, input: summary, args: { column: defect_rate, sigma: 2.5 } }
outputs:
  dashboard: { template: dash_xxx, inject: { summary: summary, outliers: outliers } }
  insight:   { when: outliers.count > 0, guide: SKILL.md }
  # alert 出口(when 條件觸發通知)為既定擴充點,暫緩實作——使用者決定先不做告警訂閱
```

設計原則:SQL 原文不轉譯;YAML 宣告式(無迴圈/分支);`expect` schema 契約大聲失敗;
綁槽位不綁檔案;文字格式可 diff;對話軌跡只是蒸餾原料,不是儲存格式。

### 5.1 釘選語意:釘的是產出物,手法是從來歷推導的

使用者**從不直接釘「手法」**。可釘選物只有兩類,各自拖著來歷鏈(provenance):

| 可釘選物 | 釘選位置 | 系統記住 |
|---|---|---|
| 結果表格 | 對話中結果表右上角 📌(hover 出現) | 表格 + 來歷鏈(來源綁定、經過的 SQL/具名方法步驟) |
| 圖表 | artifact 面板中**每個圖表區塊**各自的 📌 | 圖表 spec 區塊 + 同樣的來歷鏈 |

(圖表可逐塊釘選是 §7 dashboard spec 設計的紅利:宣告式區塊天然可定址,自由 HTML 做不到。)

蒸餾時(程式為主,LLM 只挑選與寫散文,§7):
1. **steps** = 所有釘選項來歷鏈的聯集去重——沒通往任何釘選項的岔路步驟自動排除;
2. **dashboard 模板** = 被釘選的圖表區塊組版(確認 Modal 可排序/剔除);釘選的表格可選擇是否
   也進 dashboard 當表格區塊;
3. **SKILL.md 草稿** = 從 agent 解讀與使用者反應整理。
確認 Modal 呈現:步驟清單(可勾掉)、dashboard 區塊預覽、輸出形態勾選。
Scope:先只做 personal(預設、私有、零治理)+ 用量追蹤與自動休眠退場。team/org 分層與「升級/review」策展動線為既定擴充點,**暫緩**——是否有跨人共用需求待驗證;registry 保留 scope 欄位,加回時不動 schema。

## 6. DuckDB 安全與資料量紀律

DuckDB = 無狀態查詢引擎;唯一儲存是 snapshot 檔(FileStorage)。

### 6.1 鎖門(lockdown)是什麼

DuckDB 預設能力很強:`read_csv_auto('/etc/passwd')` 讀主機任意檔案、讀其他使用者的
session 檔案、`COPY TO` 寫任意路徑、經 httpfs 對外發網路請求。而 M1 之後**寫 SQL 的是
LLM 不是人**——被資料裡藏的指令帶歪、或單純寫錯都可能產生上述 SQL。原則:不指望模型
不亂寫,要讓**亂寫也做不到**。

連線初始化紀律「**先掛資料、後鎖門**」,順序不可顛倒:

```python
# ① 先掛資料:後端依 userId 解析路徑後 materialize(LLM 從頭到尾沒機會指定任何路徑)
CREATE TABLE defects AS SELECT * FROM read_csv_auto('<後端解析的路徑或 s3://…>')

# ② 然後鎖門——四行就是「鎖門」的全部
SET enable_external_access = false   # 關掉讀寫檔案/網路的能力(含 httpfs)
SET memory_limit = '2GB'             # 記憶體上限
SET threads = 2                      # CPU 上限
SET lock_configuration = true        # 設定本身不可再改——門從裡面打不開

# ③ 這之後 agent 的 SQL 才進來
```

順序是關鍵:掛表(read_csv_auto / s3 讀取)本身需要外部存取能力,必須在關門**前**
完成;s3:// 讀取因此只存在於掛表那個窗口。鎖門後 agent 的世界只剩:記憶體裡已掛好的
表 + 純運算 SQL(SELECT/JOIN/GROUP BY)。傷害上限 = 查詢很笨,或撞資源上限被砍。

M1 用 materialize(CREATE TABLE AS);大檔改 `allowed_directories` 白名單 + view(M2 評估)。

### 6.2 為什麼不需要第三層 sandbox——兩道牆模型

```
┌─ Docker container(外牆:進程/檔案系統/網路隔離)──────┐
│   FastAPI 進程(env 憑證在此層)                        │
│   ┌─ DuckDB 鎖門連線(內牆:SQL 能力限制)────┐         │
│   │   agent 的 SQL 只在這裡面跑               │         │
│   └───────────────────────────────────────────┘         │
└─────────────────────────────────────────────────────────┘
```

- **內牆(鎖門)擋「功能濫用」**:LLM 亂寫、prompt injection 產生的惡意 SQL、越權讀取
  ——實際威脅的絕大多數。誠實邊界:鎖門是 DuckDB 自己執行的規則,**擋不住 DuckDB 引擎
  本身被 exploit**(C++ 記憶體安全漏洞級別);那種攻擊拿到的是整個進程(含 env 憑證)。
- **外牆(container)擋進程級失守**:agent-service 本來就跑在 Docker 裡——獨立檔案系統
  (只見掛入的 volume)、獨立進程空間、可控網路,即引擎級 exploit 的 blast radius 也被
  container 圈住。
- **不需要的是第三層**:per-run microVM/gVisor 那種重隔離是「LLM 寫任意 Python/shell」
  產品的必需品(任意 code 攻擊面無限)。本設計把 LLM 輸出面收斂到 SQL,攻擊面只剩
  DuckDB parser/executor,兩道現有的牆足夠。威脅模型前提:輸入來自登入的內部使用者
  對自己 session 的操作。
- **重新評估訊號**(任一出現即把 per-run 隔離提上日程):①開放非受信任來源的檔案
  ②加入 Python code-execution 工具 ③變成對外多租戶 SaaS。
- **廉價加固(M2 排入)**:container 非 root 執行、uploads volume 掛 `:ro`、DuckDB 版本
  pin + 安全更新節奏、量大後 runner 拆獨立 worker 進程縮小 blast radius。

### 6.3 失控查詢與資料量

- 失控查詢:memory_limit + query timeout(逾時 cancel)+ 結果列數上限(200 列截斷)。
- Dashboard 注入的是 **pipeline 輸出(彙總結果)**,不是來源資料;硬預算(單 alias 5 萬列/
  payload 數 MB)超標 fail 並明講;需要多點的圖在 SQL 降維(time-bucket/hexbin/Top-N/LTTB);
  預備外的探索導回 dig-in 對話,不做 dashboard 動態查詢(v2 再議)。

## 7. gpt-oss 120b 品質策略(LLM 產出物 × 驗證器)

| LLM 產出物 | 驗證器(確定性) | 失敗處理 |
|---|---|---|
| SQL | DuckDB dry-run 執行 | 錯誤訊息回饋重試 ×3;要求 agent 一句話覆述查詢意圖供人抓語意錯 |
| dashboard spec(JSON) | JSON schema + renderer 已知元件 | schema 錯誤回饋重試;**不讓 LLM 寫 HTML**,renderer 是打磨過的 ECharts 元件庫 |
| pipeline.yaml | 程式從軌跡組裝為主(SQL/參數/順序都在軌跡裡),LLM 只挑選+命名;schema+dry-run | 人工確認關卡 |
| 工具呼叫 | 工具 schema(參數少而簡單) | 標準重試 |
| insight 文字 / SKILL.md 散文 | 免驗證(純文字不可執行) | 人審 |

核心經濟學:LLM 智慧花在「編寫時刻」(有人看著),產出的可執行物驗證後**凍結**;
執行時刻(排程重跑)零 LLM,唯一例外是 insight 窄呼叫——只被允許「看著算好的數字寫評語」。
**LLM 絕不產出任何數字**;`when` 條件是規則引擎。

## 8. UI 改動清單(按 milestone)

| Milestone | 改動 | 說明 |
|---|---|---|
| M1 | **無** | 沿用既有 chat/SSE/AgentEvent;新 provider 對前端透明 |
| M2 | 訊息內「結果表格」渲染 | run_sql 的 preview 結果以表格呈現(新 AgentEvent payload 或 markdown 表格皆可,M2 定案) |
| M2 | 「查詢意圖」小卡 | agent 覆述查詢意圖的顯示樣式 |
| M2 | 刪除 `isDisplayableStep` 過濾 | 還 §16.4-1 的債:`MessageList.tsx` 以 `stepKey` 字首 `d`/`r` 判斷是否渲染,查證後確認該過濾恆真(已無 `s*` 步驟)→ 整個過濾刪除,不改為語意欄位 |
| M3 | **釘選板** | 對話中圖表/結果可釘選;側欄釘選清單;蒸餾入口按鈕 |
| M3 | 蒸餾確認 Modal | 三件套草稿(SKILL.md 摘要 + 步驟清單 + 輸出設定)的確認/編輯 |
| M3 | Skill/Pipeline 庫頁 | 我的手法清單(personal)、版本、用量、休眠狀態 |
| 暫緩 | 團隊手法(team/org scope + 升級動線) | 使用者決定先不做——跨人共用需求未確認;registry 留 scope 欄位為擴充點 |
| M4 | 來源選擇器擴充 | 既有檔案上傳之外:API 來源設定表單、CSV 槽位(重複上傳)管理 |
| M4 | Dashboard/日報頁 | 每日 dashboard 檢視、資料時間戳(staleness)標示、「進一步問」dig-in 入口;**入口三處**:sidebar 新增「日報」區塊(主入口,列有 dashboard 的手法+新鮮度,點開即 on-demand 觸發)、手法庫卡片、直接 route `/dashboard/:pipelineId`(可書籤;無權限 404) |
| M4 | Run history 頁 | pipeline 執行紀錄(成功/失敗/schema 漂移/耗時) |
| 暫緩 | 告警訂閱設定 | `when` 條件與通知通道——使用者決定先不做,alert 出口保留為擴充點 |

## 9. Milestones

- **M1(最薄端到端,本次 plan)**:Python agent service(`/chat` + get_schema/run_sql + DuckDB
  鎖門)+ Java `LangGraphAnalysisProvider`(SSE→AgentEvent)+ compose 接線。
  前端零改動,用既有上傳+對話流程即可分析 CSV。
- **M2**:profiling 開場、preview/具名方法(3σ/outlier)、結果表格 UI、dashboard spec renderer;
  **Java SPI 收窄與兩筆技術債(§16)**——`AgentProvider` / `DashboardAgentProvider` 分層、
  `stepKey` 語意欄位化、STEP 改由 LangGraph 真實編排事件驅動(順帶補 heartbeat,解 §16.5);
  agent-service `engine/` / `agent/` 分層固化(§15)。前端結構不動(§17.2)。
- **M2.5(打磨,2026-07-26 使用者決策:先把分析與 dashboard 做好再往釘選/蒸餾)**:
  greeting/閒聊分流(非分析訊息不強制 profiling)、圖表元件庫打磨(tooltip/dataZoom/legend/
  多序列——§7「打磨過的 ECharts 元件庫」的兌現;fancy 且確定性)、重試可視化(SQL_ERROR/
  SPEC_ERROR 的步驟顯示警示狀態,不再偽裝 SUCCESS)、LLM 視圖截斷與 renderer 資料分離
  (store 全量上限 5000)、dashboard 迭代能力(LangGraph checkpointer + session 級 result store,
  tableId 跨 turn,前版 spec 微調不重查)、Langfuse 本地自架(compose profile,觀測 ReAct 行為)。
- **M3**:釘選、蒸餾三件套、skill registry(3 層 scope)、SkillsMiddleware 載入。
- **M4**:connector(API/槽位)、snapshot 版本化、pipeline runner + 三種觸發、insight 窄呼叫、
  日報、dig-in 回流(告警訂閱暫緩)。

每個 milestone 一份獨立 plan(`docs/superpowers/plans/`),按多人協作規則以 plan 為單位認領。

## 10. 已敲定決策(備忘)

1. 產品形態 = data analyst agent,非通用 cowork;自由度收斂在 SQL,確定性保在引擎與 pipeline
2. 框架 = LangChain `create_agent`(ReAct loop 必要)+ DeepAgents middleware 按片取用
3. 事件協定當防火牆:前端只認 AgentEvent;Python 服務對 Java 是一個 provider
4. 互動/排程共用同一個 SQL 執行器(`/run-pipeline` 與 `/chat` 同 code path)保可重現
5. 排程預設 on-demand+快取;cron 僅推送訂閱(日報);自動休眠退場;告警訂閱暫緩
6. gpt-oss 前提:LLM 不寫 HTML(spec+renderer)、不算數字;所有可執行產出配確定性驗證器

## 11. Pipeline Runner 執行設計(M4)

### 11.1 執行時序

```
觸發(on-demand 開頁 / CSV 槽位新檔 / cron 推送訂閱)
  → Java:取 pipeline(version N)→ 解析槽位→snapshot 路徑 → POST /run-pipeline
  → Python Runner(無 agent、無 loop,順序執行):
      YAML schema 驗證 → open_locked_connection(同 §6,與 /chat 共用 duck.py)
      → expect 契約檢查(information_schema 對照;不符→整場 fail「schema drift」)
      → 逐步執行 steps → 組 outputs
  → Java:寫 run 紀錄(version、snapshot 時間戳、耗時)→ 成功才注入 dashboard/發通知
```

### 11.2 Steps 執行模型

- 每步產出一張以 step id 命名的表(`CREATE TABLE "<id>" AS …`),後續步驟 SQL 直接
  `FROM <id>` 引用;**執行順序 = 宣告順序**(不做 DAG,數十步內拓撲排序是多餘複雜度)。
- `method` 步 = 具名 Python 函式,與互動迴路的工具**同一實作**。
- 參數代換白名單制:只允許 `{{ params.xxx }}` 且必須在 YAML `params` 宣告、有型別;
  嚴格綁定/轉義,非字串拼接。改邏輯走 `queries/*.sql` 升版本,不走參數。

### 11.3 Outputs 三出口

- **dashboard**:`inject` 的結果表匯出 `{alias: rows[]}` 回 Java → ArtifactAssembler 注入;
  注入預算在此把關(§6.3)。
- **when 條件 = 微型規則引擎,非 LLM**:文法貧乏——`<step>.<聚合> <比較符> <常數>`
  (如 `outliers.count > 3`),Runner 翻成一句 SQL 自己算。
- **insight**:`when` 成立才做——inject 小結果集 + SKILL.md 解讀指引 → **一次** LLM 呼叫
  (無工具無迴圈),整個排程迴路唯一的 LLM。

### 11.4 失敗設計

| 類型 | 處理 |
|---|---|
| `schema_drift` | 整場 fail,標明來源/欄位 |
| `sql_error` | fail 附 DuckDB 原文——「手法該升版」的訊號 |
| `budget_exceeded` | fail 附修法建議(加彙總/抽稀步驟) |
| `insight_llm_error` | **降級不 fail**:dashboard 照更新,insight 標「今日解讀不可用」 |

原則:**原子性**(全部資料步驟成功才換 dashboard 資料,絕不半新半舊)、**可重放**
(run 紀錄釘 pipeline version + snapshot id,任何一天可精確重跑)。

Runner 放 Python 的唯一充分理由:`/chat` 與 `/run-pipeline` 必須共用同一套執行器
(duck.py、具名方法),否則兩套實作遲早出現方言差異,毀掉可重現性。

## 12. Chat History 三層管理

| 層 | 內容 | Owner | 生命週期 |
|---|---|---|---|
| ① 顯示用對話史 | 使用者看得到的訊息 | Java(既有 `ChatMessage`,唯一真相) | 永久(30 天政策) |
| ② Agent 工作記憶 | ①+ 工具軌跡(SQL、結果) | Python(LangGraph checkpointer,`thread_id=sessionId`) | **可丟棄快取** |
| ③ 蒸餾原料 | ②中通往釘選結果的軌跡 | 蒸餾時讀②,產物歸 Java | 用完即棄 |

- 每輪:Java 存訊息 → 呼叫 `/chat`(帶新訊息 + ①近況 fallback)→ Python 有 thread 就
  續跑;沒有(重啟/新 session)就用①重建——工具軌跡丟了**不補**,agent 需要時重查
  (查詢確定性+便宜,重查代價遠低於維護兩份軌跡同步)。②永遠可砍,①永遠是真相。
- Context 不爆三機制:工具結果 200 列截斷於源頭;summarization middleware 只壓②不動①;
  schema/profile/skill frontmatter 走 `load_context` system prompt 每輪重組,不累積在對話。
- Dig-in = 新 session 新 thread,pipeline 脈絡注入 system prompt,**不偽造對話史**。
- 演進:M1 stateless(每次由①重建,無 checkpointer)→ M2/M3 引入 checkpointer
  (M3 蒸餾需要完整工具軌跡是硬需求,跨輪軌跡記憶是順帶收益)。

## 13. Guardrails 規劃

### 13.1 已內建(執行面)

鎖門與兩道牆(§6.1–6.2)、資源與注入預算(§6.3)、recursion limit + SQL 重試上限 3 次
+ query timeout(M1)、`X-User-Id` 隔離/404(既有)、LLM 產出物×驗證器(§7)、
Runner 的 expect/原子性/規則引擎(§11)、日誌不落資料內容(CLAUDE.md 規則,延伸至
agent-service)。

### 13.2 缺口與規劃

1. **資料內容 prompt injection**(最重要的真實威脅):CSV/API 內容可藏指令,經查詢結果
   進 context。防線:(a) **架構性——agent 沒有任何外流工具**(不能上網/寄信;唯一輸出
   是給眼前使用者的文字),最壞情況=誤導性分析,威脅有上限;**未來新增任何 agent 可
   觸發的網路呼叫,MUST 過 guardrail review**。(b) 工具結果包結構化框架(標明「資料非
   指令」)+ system prompt 抗注入條款(對 gpt-oss 效果有限但便宜)。(c) 高風險動作
   (蒸餾存檔等)一律人工確認關卡。
2. **資料外流與 PII**:LLM endpoint 政策做成啟動檢查(`AGENT_ALLOW_EXTERNAL_LLM` 預設
   false,base_url 非 internal 域名即拒啟動;dev 才可接 OpenRouter);進 LLM 的只有 schema/
   彙總/樣本列(架構已保證,明文為契約);PII 欄位偵測(email/電話 pattern)→ preview/
   樣本遮罩。
3. **成本與濫用**:per-user 每日 token 預算、單 session 輪數上限、`/chat` 並發上限
   (semaphore)、單請求 wall-clock timeout(~120s);Java provider 層(識 userId)+
   Python 服務層雙邊落地。
4. **SQL 語句白名單**(低優先,鎖門已兜底):只允許 SELECT/CTE,擋 CREATE/DROP。
5. **Insight 數字後驗**(選配):檢查 insight 文字引用的數值存在於結果集,對不上標註
   或重生。

### 13.3 Milestone 對應

| Milestone | Guardrail 項目 |
|---|---|
| M1(已含) | 鎖門、截斷、重試/遞迴上限、timeout |
| M2 | 限流(token/輪數/並發)、SQL 白名單、防注入框架、LLM endpoint 啟動檢查、agent-service 日誌規範、container 加固(非 root、`:ro` volume) |
| M3 | confirm_save 人工關卡、蒸餾產物 schema 驗證(已規劃) |
| M4 | PII 偵測遮罩、connector 網路面 review、insight 數字後驗(選配) |

## 14. Observability(Langfuse)

**整合點**:agent-service 內兩處——`/chat` 的 agent loop(LangChain `CallbackHandler`
掛進 `config.callbacks`,完整記錄每輪的 LLM 呼叫、工具呼叫與參數、token/延遲)與
`/run-pipeline` 的 insight 窄呼叫(獨立 trace,name=`insight`)。Java 端不接——LLM
活動全部在 Python 側,Langfuse 的 LangChain 整合是現成的。

**Trace 對映**:`langfuse_session_id` = 我們的 sessionId(同一對話的多輪聚成一條
session)、`langfuse_user_id` = X-User-Id、tags = provider/milestone。這讓「某個使用者
的某場對話為什麼變慢/變貴/答錯」可以直接下鑽到單一 LLM 呼叫與工具軌跡。

**啟用方式**:env-driven(`LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY`/`LANGFUSE_HOST`),
**未設定即 no-op**——不設 key 時完全不建 handler,零開銷、零依賴啟動順序;測試環境
預設關閉。

**部署**:M1 不把 Langfuse server 塞進本專案 compose(v3 自架需 Postgres+ClickHouse+
Redis,是獨立的基建決策)——SDK 指向 internal 自架 instance 或 Langfuse Cloud 皆可,由 env
決定。要在本機起一套時,依官方 compose 另行啟動。

**隱私(與 §13.2-2 同一政策)**:trace 內容包含 prompt、查詢結果樣本與 insight 文字
——**prod 的 `LANGFUSE_HOST` MUST 是 internal 位址**,與 LLM endpoint 政策一併納入啟動
檢查;之後需要時用 SDK 的 mask hook 對 trace 做欄位遮罩(接 §13.2-2 的 PII 偵測)。

**用途路線**:M1 起 = debug 與成本觀測(gpt-oss 的 SQL 重試率、工具選擇行為、每 session
token);M2 起 = 品質迭代(以 Langfuse dataset/score 對 prompt/工具描述做 A/B 與回歸);
M4 = 排程 insight 的品質抽查。per-user token 預算(§13.2-3)的計量數據源也從這裡取。

## 15. agent-service 內部分層:engine 與 agent 的縫

### 15.1 目標結構(M2 起固化;M1 的 duck.py/tools.py 已符合精神,先維持扁平)

```
agent-service/app/
├── engine/                  ← 分析引擎:一行 langchain 都不准 import
│   ├── duck.py              # 連線工廠、鎖門(§6)
│   ├── query.py             # get_schema / run_sql(純函式:connection in, str out)
│   ├── methods/             # 具名方法:trend_3sigma.py、flag_outliers.py…
│   ├── profiling.py         # 自動 profiling(M2)
│   └── runner.py            # pipeline runner(M4,§11)
├── agent/                   ← LangGraph 面:唯一知道 LLM 存在的地方
│   ├── graph.py             # build_agent、middleware 組裝
│   ├── toolbelt.py          # 把 engine 函式包成 @tool——工具描述文字全集中在此
│   └── prompts.py           # system prompt、防注入框架(§13.2-1)
└── main.py                  # FastAPI routes(/chat 用 agent、/run-pipeline 直呼 engine)
```

### 15.2 單向依賴規則(lint 強制,不靠自覺)

**`agent/` 可 import `engine/`;`engine/` NEVER import `agent/`、langchain、langgraph、
langfuse。** 以 Ruff banned-import(或 import-linter contract)強制,M2 重構時一併加入 CI。

理由(三個都已是既定設計的需求,非品味):
1. `/run-pipeline` 本來就必須繞過 agent 直呼引擎(§11)——引擎 agent-agnostic 是排程
   迴路「無 LLM 重跑」的功能前提,不是選配。
2. 框架防火牆第二道:對外 AgentEvent 協定把 LangChain 擋在 Python 服務內(§10-3),
   對內這條縫把它擋在 `agent/` 內——換框架只重寫 `agent/` 三檔,引擎零改動。
3. 測試成本:engine 測試是純函式測試(不需 fake LLM/SSE),維持 M1 test_duck/test_tools
   的形態。

### 15.3 服務級拆分:不預先做,觸發條件明列

同進程 package 邊界為預設;出現以下任一訊號才把 engine(通常先是 runner)拆獨立
服務/進程,且沿本節的縫拆:
1. Runner 需要獨立伸縮,或要與互動對話隔離資源/縮小 crash blast radius(§6.2 加固清單
   「runner 拆獨立 worker」即此事)。
2. 出現 agent 之外的第二個引擎消費者(如 Java 直呼 profiling)。

在此之前不加 HTTP 邊界——多一層網路只多一層除錯面,縫留好即可,拆時就是搬資料夾。

## 16. Java 側分層:對話層與生成編排的縫

M1 的 `LangGraphAnalysisProvider` 證實既有 SPI 的形狀是繞著「產 HTML dashboard」長出來的。
本節定義 M2 起的縫。§3 架構圖的 Java 區塊已同步改繪:原本單行「LangGraphAnalysisProvider ──
實作既有 DashboardAgentProvider SPI」改為「對話層 + 收窄的 AgentProvider SPI 分岔兩種模式」。

### 16.1 證據:M1 擠過 SPI 時的四處硬塞

| 契約成員 | analysis 模式的實際情況 |
|---|---|
| `ExtractionResult(answerText, html, questions)` | 永遠是 `(text, null, null)`(**僅 M1**;M2 起 html 由 renderer 填入,見 16.2.1) |
| `AgentRequest.previousArtifactHtml` | 永遠未使用 |
| `DashboardAgentProvider.harden()` | 走 default passthrough(該鉤子是 HTML 生成期修復概念) |
| `stepKey` 的 `d`/`r` 字首慣例 | 需在 provider 內改寫成 `d_analysis` 才通過持久化與渲染 |

**判準**:出問題的是 request 型別與 `harden()` 鉤子——不是 `AgentEvent` 聯集,**也不是結果型別**。
某個模式不發某幾種事件是正常的,事件聯集可持續擴充;結果型別的三個欄位兩種模式最終都會用到
(16.2.1)。真正只屬於 dashboard 的,是「LLM 直接寫 HTML」所衍生的**生成期修復**與**前版 HTML 回餵**。

### 16.2 切法:一層共用對話層 + 各模式自帶生成編排

**共用(留在 Java,對 agent 模式無知)**:session/訊息持久化、`X-User-Id` 歸屬與 404 語意、
對瀏覽器的 SSE、cancel 補 interrupted 列、CAS 單一持久化路徑、檔案上傳/解析/儲存/清理。
這層只認 `Flux<AgentEvent>`。

**各模式自帶**:怎麼從問題走到答案。dashboard 模式帶 `previousArtifactHtml`、HTML 抽取與
`harden()` 修復迴圈;analysis 模式帶 LangGraph ReAct 與 spec renderer。

**縫劃在「要不要生成期修復」,不是「產不產 artifact」**(理由見 16.2.1):

```java
public interface AgentProvider {
  ProviderResult generate(AgentRequest request);   // 事件流 + 結果(結果含 artifact html)
}

/** 只有「LLM 直接寫 HTML」的模式需要:生成期修復 + 前版 HTML 回餵。 */
public interface DashboardAgentProvider extends AgentProvider {
  RepairResult harden(String sessionId, AgentRequest request, AgentOutcome outcome);
}
```

- **結果型別共用,不再分兩層**:`answerText` / `html` / `questions` 三個欄位兩種模式最終都會
  用到(16.2.1),因此**不需要**「窄結果介面 + dashboard 專用結果」的兩層設計。既有
  `ExtractionResult` 更名為 `AgentOutcome`——analysis 模式的 html 來自 renderer,不是從 LLM
  文字「抽取」出來的,舊名會說謊(與 `HtmlExtractionHelper` → `ResponseExtractionHelper`
  同一個理由);`ProviderResult.extraction` 同步更名為 `outcome`。純機械更名,編譯器全程可驗證。
- **dashboard 專屬輸入**(`previousArtifactHtml`)收進具名巢狀 record,不平鋪在 `AgentRequest`
- **對話層如何分岔**:`AgentOrchestrator` 以 Java 17 pattern matching 在單一位置判斷
  `if (provider instanceof DashboardAgentProvider dashboardProvider)` 決定是否跑修復階段。
  兩三種模式的規模下,一處顯式 `instanceof` 比注入 `Optional<修復階段 bean>` 更好讀——
  **這是刻意選擇,code review 不必當 smell**;成長到需要第三個分支時再改策略表。
- **NEVER 為此把 SPI 泛型參數化**——三個 provider 的規模不值得
- 加第四種 agent 只需實作 `AgentProvider`,不必實作一個永遠 passthrough 的 `harden()`

#### 16.2.1 為什麼縫不能劃在「產不產 artifact」

M1 的 analysis 模式不產 artifact,容易誤以為 artifact 是 dashboard 專屬。**M2 起此前提不成立**:
`render_dashboard`(§4)會讓 analysis 也產出 artifact。但兩者的**產生方式**不同,而差異正好
落在需不需要修復:

| | dashboard 模式 | analysis 模式(M2 起) |
|---|---|---|
| HTML 從哪來 | LLM 直接寫 | LLM 只寫 dashboard spec(JSON),確定性 renderer 組 HTML(§7) |
| 會不會壞 | 會——JS 語法錯、區塊遺漏 | 不會——spec 過 JSON schema 才進 renderer,renderer 是打磨過的元件庫 |
| 需要 `harden()` | 需要 | **不需要** |
| 迭代修改回餵什麼 | 前版 raw HTML | 前版 spec |

`questions` 同理:analysis 模式有 `ask_user` 工具(§4)會觸發 QUESTION 事件,並非 dashboard 專屬。

若照修正前的字面(把結果型別整個歸給 dashboard)實作,M2 一加 `render_dashboard` 就得再改一次
SPI——正是本節存在的目的所要避免的事。

### 16.3 已否決方案:Java orchestrator 凍結在舊模式、LangGraph 側自己另蓋一套

理由:orchestrator 這層真正值錢的不是 dashboard 邏輯,而是 M1–M6 打磨出來的 session upsert、
歸屬 404、cancel 補 interrupted 列、CAS 單一持久化、SSE 斷線處理——每一條都與 agent 模式無關,
且都是踩過坑才修出來的。

把這層留給準備淘汰的 demo 模式獨佔、讓有未來的模式在旁邊重蓋一套,結果是新路徑拿不到已驗證
的保證,而且持久化/取消邏輯各寫一次。**共用層要跟著有未來的模式走,不是跟著 legacy 走。**

### 16.4 一併償還的兩筆技術債

**1. `stepKey` 字首編碼語意 → 整個概念刪除**(2026-07-25 修正:原訂改為具名欄位,查證後
確認不需要)。現況:`AgentOrchestrator` 以 `startsWith("d")` 決定是否持久化、前端
`MessageList.isDisplayableStep` 以 `d`/`r` 決定是否渲染;M1 因此被迫在 provider 內把
`analysis` 改寫成 `d_analysis` 才看得見。

查證結果(三項,決定了做法):
1. 目前所有發出點只用 `d1`/`d2`/`d3`(codegen 罐頭)、`d{n}`(模型標記,`ResponseExtractionHelper`)、
   `d_analysis`、`r1`(修復)——**已無任何地方發 `s*`**(固定步驟早已移除)。
2. `d` 與 `r` 除「要不要顯示」外**無任何語意差異**:`StepChain` 只依 `status` 決定圖示,
   `stepKey` 僅作為識別碼(`key` 與 last-state-per-key 覆寫)。
3. 使用者確認**不需相容既有資料**。

⇒ 每個步驟都應顯示,兩處過濾器皆為恆真的 no-op。因此**刪除前綴慣例本身**(兩處過濾器、
`DYNAMIC_STEP_KEY_PREFIX` 常數、provider 內的 `d_` 改寫),而**不是**引入 `source` 欄位——
少一個列舉型別與一組相容邏輯,`stepKey` 回歸單純識別碼。日後真的需要區分步驟來源(例如修復
步驟要有不同樣式)再加欄位即可。

**2. STEP 改由真實編排事件驅動** — 目前 dashboard 模式靠模型自報 `[[step:]]` 標記,analysis
模式發單一罐頭步驟。M2 起 analysis 改用 LangGraph 的 `on_tool_start`/`on_tool_end` 發 STEP:
步驟反映真實執行(不必信任模型的自我描述),且天然提供 heartbeat,即 16.5 的解法。

### 16.5 已知風險:逾時是閒置語意,且缺 heartbeat

`ERD_AGENT_ANALYSIS_REQUEST_TIMEOUT_SECONDS`(預設 180)是 `Flux#timeout` 的**事件間閒置**
逾時,非總時長:持續吐 TOKEN 的 turn 不受限,長時間靜默則被切斷。agent-service 在 SQL 執行與
LLM 思考期間不發任何事件,而 `fastapi.sse` 的 keepalive comment 會被 Spring 的 SSE reader 丟棄、
到不了 AgentEvent 層——因此健康但慢的分析會被誤砍。16.4-2 的 STEP 心跳即為解法。

### 16.6 不做的事(YAGNI 界線)

1. NEVER 設計以 optional 欄位涵蓋所有未來模式的萬用 SPI
2. NEVER 為求對稱把 Python 端也改成外掛架構(Python 的縫見 §15)
3. 不新增第二個 orchestrator bean——切的是責任歸屬,不是複製一份編排流程

## 17. 前端分層:何時該改結構,以及怎麼不硬改

前端目前 2467 LOC、單一畫面(`CoworkPage`)、無 router,結構是 `api/` `hooks/` `utils/` 平鋪 +
`components/` 依內聚分子資料夾(`chat/` `artifact/` `files/` `common/`)。這個形狀對「一個畫面」
是對的,但 §8 在 M3/M4 要加三個**新畫面**(skill 庫頁、dashboard/日報頁、run history 頁)與
可書籤路由 `/dashboard/:pipelineId`。硬把新畫面塞進單頁結構,就是技術債的來源。

本節沿用 §15.3 的紀律:**不預先做,觸發條件明列**。

### 17.1 CLAUDE.md 既有規則的修訂點(不是漂移,是明示變更)

CLAUDE.md 現行規則:「components 可依內聚分子資料夾(**不做 features 分層**)」。
該規則在單畫面前提下正確,且 M1/M2 continue 有效。**出現第二個畫面時(M3)此規則須修訂**,
修訂由該 milestone 的 plan 明確提出並更新 CLAUDE.md,NEVER 靠實作時自由發揮。

修訂方向(最小增量,非全面 features 分層):
- 引入 router,`CoworkPage` 降為其中一個 route,不再是 App 的唯一內容
- **每個 route 一個資料夾**放該畫面專屬元件;跨畫面共用的留在 `components/`
- `api/` `hooks/` `utils/` 維持頂層平鋪(CLAUDE.md 現有規則,不變)

### 17.2 觸發條件

| 觸發 | 動作 | 何時 |
|---|---|---|
| 仍是單一畫面 | **結構不動**。新元件放既有 `components/chat/` 等分子資料夾 | M2(結果表格、查詢意圖小卡都是訊息內元件) |
| 出現第二個畫面 | 引入 router + per-route 資料夾;修訂 CLAUDE.md(§17.1) | M3(skill 庫頁) |
| 單檔超出可讀範圍 | 才拆檔。現況最大 `ChatPanel.tsx` 318 行,尚在範圍內 | 觸發時 |
| 跨畫面共用伺服器狀態變複雜 | 才評估狀態管理方案 | 見 §17.4 |

### 17.3 釘選 UI 的可維護性前提:dashboard spec,不是自由 HTML

§5.1 的「圖表可逐塊釘選」之所以可行,是因為 §7 的產出物是**宣告式 dashboard spec(JSON)**,
區塊天然可定址。若回頭讓 LLM 產自由 HTML,釘選就只能靠 DOM 選擇器/字串比對硬塞——那正是
「改得很難維護」的典型形態。**釘選板(M3)MUST 建立在 spec 區塊 id 之上,NEVER 依賴 HTML 結構。**

### 17.4 型別同步紀律

`frontend/src/types.ts` 的 `AgentEvent` union 是 Java `AgentEvent` sealed interface 的**手抄鏡像**。
M2 起事件會長(結果表格 payload、STEP 語意欄位),兩邊漂移會產生沉默的渲染錯誤。
新增/修改事件型別時 MUST 同一個 PR 內同步兩側,並在 `utils/sseParser.test.ts` 釘住新形態
(該檔已有逐事件斷言的形態,沿用即可)。

### 17.5 不做的事(YAGNI 界線)

1. NEVER 預先建立 `features/` 骨架「等以後長進去」——空資料夾比沒有更難維護
2. NEVER 為目前規模引入狀態管理庫;伺服器狀態用 react-query(既有規則),UI 狀態用
   `useState`/`useReducer`。真的不夠用時才在 plan 提案
3. 不為單一畫面做 code splitting(CLAUDE.md 現行規則:獨立路由或重型第三方元件才 lazy);
   M3 引入 router 後,新畫面才依該規則各自 `React.lazy`
