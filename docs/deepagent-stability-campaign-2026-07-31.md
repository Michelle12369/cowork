# Deepagent 3-Tab Dashboard 穩定性戰役（2026-07-31）

使用者指定的測試協定：同一 dashboard 依序建三個 tab（usage_log → feedback → SPC），驗證
HTML/JS 全部正確渲染；runtime 錯誤要能用修復按鈕修好；穩定後做多輪修改測試；解剖 trace
找出耗時原因並優化。本文件記錄過程中所有發現與變更。

## 測試結果總覽

| 階段 | 內容 | 結果 |
|---|---|---|
| Phase 1 | 3 cycles × 3-tab 順序建置（9 turns） | 9/9 成功；渲染 2/3 cycle 完美，1 cycle 3 張圖空白（引出發現 #1） |
| Phase 2 | 同 session 連續 5 輪修改 | 5/5 turn 成功；但 m1 引入 DOM id 懸空、五輪 guard 全放行（發現 #3） |
| 閉環驗證 | 修正部署後全新 session 8 turns（建置+修改） | 8/8 成功；**14 張圖 3 tabs 零壞圖零 console 錯誤** |
| 修復按鈕 e2e | backend 全鏈（示範資料→chat→artifact→/repair） | `{"repaired":true}`，Java→Python 修復鏈路實證 |

## 發現與修法（依發現順序）

### 1. try/catch 隔離悶死兩條錯誤偵測通道
每圖 try/catch（防一張壞全頁死）把 ReferenceError 吞掉 → guard 的 sandbox 看不到未捕捉
例外而放行壞圖；head-inject 的 window.onerror 也收不到 → 修復按鈕永不出現。
- **修**（`353a967`）：sandbox 的 console.error 改收集器，`[ERD] chart` 前綴訊息轉 guard 錯誤
- **修**（`08b2f85`）：skill 的 catch 範本加 `setTimeout(() => { throw error; }, 0)` 非同步重拋
  ——隔離保留、window.onerror 恢復、修復按鈕回歸

### 2. sandbox 假資料讓圖表本體根本沒被執行
guard 在注入前檢查，sandbox 餵假欄名（`__c0`），真實程式的 `getCol('department')` 全回 -1、
`if (idx >= 0)` 閘門全關——閘門內的錯誤物理上照不到。
- **修**（`a63442a`）：`check_dashboard_html` 增 `results` 參數，sandbox 餵真實欄名＋前 3 列
  樣本；main.py/repair 端全部接線。c1-t3 實檔為 regression fixture（stripped＋真實 results
  下 3 張壞圖全數攔截）

### 3. sandbox 的 getElementById 永不回 null
修改輪刪掉 KPI 卡但留著 `getElementById('kpi-failures').textContent=...` → 真瀏覽器
null.textContent 未捕捉例外殺掉整個 DOMContentLoaded、全頁零圖表，且連續五輪 guard 放行。
- **修**（`e94fab8`）：Python 端抽出 HTML 全部實際 id 餵進 sandbox；`getElementById` 對不存在
  的 id 回 **null**（與真瀏覽器同語意）；`querySelector('#id')` 簡單形式比照。mod-m1 實檔為
  第二個 regression fixture（TypeError＋行號精準攔截）
- **修**（`4e99cad`）：skill 自查規則擴到 element id（刪 markup 後 grep id 殘留引用）

### 4. 供應商吞吐瓶頸（效能）
解剖 250s 的 t3 輪：單次 107.5s 生成 4,720 tokens ＝ **44 tok/s**——OpenRouter 預設路由到
最便宜的 DeepInfra（同時也是尖峰 93% uptime、常斷線的那家）。
- **修**（`fe50215`）：OpenRouter provider 路由旋鈕 `AGENT_PROVIDER_SORT`／`AGENT_PROVIDER_IGNORE`
  （dev 設 `throughput`）
- **實測**：鯨魚生成 107.5s → **18.1s（5.9×）**，多數生成 100–232 tok/s
- 剩餘瓶頸已轉移：每呼叫 TTFT 底盤（~7-9s）× 呼叫數、prompt cache 命中僅 7%——下一步
  候選＝供應商 pin 定提升 cache 命中

### 5. 修復按鈕接上 deepagent 線（本戰役前置功能）
原機制只有 openai 線可用（`ArtifactRepairer` 的 `Optional<DashboardAgentProvider>` 在
analysis provider 下缺席）。
- **修**（`a662f87`＋`d9826cd`）：deepagent `/repair` 端點（strip→單呼叫修復→guard 驗證×2→
  重注入）＋ Java `AnalysisBrowserRepairClient` 路由；前端零改動

## 殘留的已知限制（非阻斷，記錄在案）

- m4 修改輪：`<h1>` 標題有改、`<title>` 沒改但模型回答聲稱兩者都改（小型誠實性落差）；
  回答文字裡出現「X 筆」佔位符（HTML 內是正常插值）
- guard 的 querySelector 複雜選擇器仍回 absorb 元素（界線已註記於程式）
- 純視覺問題（間距、配色觀感）仍需 headless 截圖層才能自動驗證

## 測試涵蓋

戰役期間 deepagent-service 測試 103 → **133**（含兩個真實案例 regression fixtures）；
backend 500 → 509；全部綠。
