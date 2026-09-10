# Connector dashboard 的「問數字」功能——腦力激盪筆記（未定案）

> 狀態: **筆記, 不是 spec.** 2026-09-10 對話中整理, 尚未經 brainstorming 流程拍板, 沒有 plan. 目的是把兩張比較表與最小設計留存, 待 D9 傳輸面（`docs/superpowers/specs/2026-09-08-mcp-dashboard-on-autoland-design.md` §7）落地後再回來決定. 任何條目與主 spec 衝突以主 spec 為準.
>
> 前提: connector 模式的 dashboard 在檢視時經 `mcp()` 現抓資料, 數字在 viewer 瀏覽器裡算出來; 模型（在 server）從未看過畫面上的數字. 單給模型 HTML+JS 只能讓它知道「數字怎麼算」, 不知道「數字是多少」.

## 1. 問題

使用者看著 dashboard 問「為什麼這裡是 43%」. 模型手上只有 HTML+JS 與對話歷史; 顯示中的數值活在瀏覽器變數、ECharts option 與 DOM 文字裡. 需要把「畫面上現在是什麼」帶回模型, 或讓模型自己重算.

## 2. 兩條路

**A. 從瀏覽器擷取快照, 隨問題送回.** 前端已隨每輪送 `previousDashboardHtml`; 比照加一個 `dashboardSnapshot`, 由注入 iframe 的 runtime 依宿主要求產生（`erd-snapshot-request` → `erd-snapshot`）.

**B. server 端重算.** 模型已有呼叫紀錄, 可用同參數重打 connector tool 落表, `run_sql` 算精確值（主 spec D6: qN 供對話回答）. 精確但可能與畫面不一致（viewer 身分、當下控制項狀態、時間點都不同）; 「畫面說 43%, 模型說 41%」比不回答更糟.

建議兩者並用, 信任順序: 先以快照回答「畫面上現在是什麼」並得知控制項狀態; 快照沒有的數字才重打 connector, 且說明是重算.

## 3. 快照擷取方式比較

| 方式 | 做法 | 拿得到 | 限制 |
|---|---|---|---|
| `console.log` 轉發 | runtime 覆寫 iframe 的 `console.log`, postMessage 給宿主 | 模型自己選擇印出的東西 | 無結構、雜訊多、靠模型記得印、沒有控制項脈絡. **不建議** |
| DOM 文字 | `document.body.innerText` 修剪 | 使用者讀得到的每個 KPI、表格格、標題、標籤 | 圖表序列看不到（canvas）; 數字是格式化字串（"1.2M"） |
| ECharts option 匯出 | 列舉 `[_echarts_instance_]` 元素, `getOption()`, 只留 `title`、`xAxis.data`、`series[].name`、`series[].data` | 每張圖實際畫出的值, 不需模型配合 | 可能很大; 用 `dataset` 的序列要另外處理 |
| 控制項狀態 | 序列化 `select`／`input` 值 | 使用者目前套用的區域、時間窗、篩選 | 需要穩定 `id`（skill 已要求） |
| 明確 view 物件 | skill 約定: dashboard 把衍生數字寫進 `window.__ERD_VIEW__ = {kpis:{...}, series:{...}}`; runtime 一併帶出 | 有名字、有結構, 與模型自己的思路一致 | 靠模型照做; `check_dashboard` 補一條「缺席即警告」 |

建議出貨組合: 控制項狀態 + DOM 文字 + ECharts option 匯出為基底（任何 dashboard 都可用, 零模型配合）, 再加 `__ERD_VIEW__` 約定讓有照做的 dashboard 回答更準. 快照進對話歷史, 需設上限（建議 200 KB, 序列截斷）.

## 4. 編輯模式 vs 檢視模式

需求（2026-09-10）: 兩種模式都要能問數字. 檢視模式只能篩選與看, 不能改 dashboard; 編輯模式兩者皆可.

**兩種模式相同的部分:** 注入的 runtime 與 `mcp()` 往返（檢視模式的篩選就是控制項觸發新的 `mcp()` 呼叫, 現有機制已涵蓋）; 快照擷取（runtime 不知道也不需要知道模式）; 模型輸入＝dashboard HTML + 快照 + 問題.

**不同的部分:**

| | 編輯模式（作者） | 檢視模式（viewer） |
|---|---|---|
| 誰 | session owner | 被分享者; 存取規則是「能看」, 不是「擁有」 |
| 入口 | 既有 `/chat` 一輪, 請求多帶 `dashboardSnapshot` | 新 endpoint `POST /api/artifacts/{id}/ask`, 轉給 deepagent 時帶 `mode: "ask"` |
| 模型可以 | 回答, 或修改並產出新版 dashboard | 只回答. 無 `write_file`／`edit_file`, 不發 `DASHBOARD_HTML` 事件, 不掛 dashboard skill gate |
| 模型可重打 connector | 可, 同現況 | 可, 但以 viewer 身分: viewer 的 SSO 上請求, connector 取自 artifact 所屬 session, 額度照算 |
| 對象 dashboard | session 最新版 | viewer 正在看的那個已發布版本（`artifactId` 釘住） |
| 對話狀態 | session checkpoint, 同現況 | v1 server 端不存: 前端隨每次 ask 帶最近幾輪問答（同 `previousDashboardHtml` 模式）; 不寫進作者的 session |
| 前端介面 | chat 頁的 `ArtifactPanel` | `ArtifactFullscreenPage`（或分享路由）加一個不影響篩選的問答框; 同樣需要 bridge hook 與快照擷取 |
| prompt 內的資料 | 快照是作者看到的 | 快照是 viewer 看到的, 因權限可能與作者不同; 答案不得回流作者 session |

## 5. 最小設計

1. deepagent chat request 加 `mode: "edit" | "ask"` 與 `dashboardSnapshot`. `ask` 時 agent 只掛 connector tools 與 `run_sql`, 無寫檔工具, 無 skill gate, 系統段落多一句: 先依快照回答; 畫面上沒有的數字才重打 connector, 並說明.
2. Java: `POST /api/artifacts/{id}/ask`, body `{question, snapshot, priorTurns[]}`. 存取規則同 `GET /api/artifacts/{id}`. 從 artifact 所屬 session 取 connectors, 轉發 viewer SSO, 串流回答. 無狀態.
3. 前端: 一個快照 hook 供兩個介面共用. 編輯模式把快照附在既有 send; 檢視模式打 ask endpoint, `priorTurns` 存在元件狀態.
4. skill: 加 `__ERD_VIEW__` 約定; `check_dashboard` 缺席時警告（不退件）.

## 6. 這件事逼出的決定（未定）

- 檢視模式問答是第一個「替非 owner 跑模型」的功能: 每次 ask 可能觸發 connector 呼叫, 需要每 viewer 的成本與濫用上限（產品決定）.
- 快照把顯示中的值放進模型 prompt. 檢視模式下那是 viewer 自己權限內的資料, 且無狀態不落地, 是較安全的預設; 日後要不要持久化 viewer 的對話是另一個決定.
- 依賴 D9 傳輸面先落地（runtime、`ArtifactPanel` bridge、Java `/mcp-call`、deepagent `/tool-call`）; 本筆記的快照訊息（`erd-snapshot-request`／`erd-snapshot`）應與 D9 的訊息名稱一起定案.
- 分享頁 viewer 能否開 connector dashboard 本身仍是開放的產品決定（主 spec §11）; 沒有它, 檢視模式問答無對象.
