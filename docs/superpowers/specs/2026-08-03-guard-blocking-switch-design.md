# html_guard 非阻擋開關 — design

**日期**：2026-08-03
**基準 commit**：`58df89a`
**規模**：一個環境變數、兩個判斷條件。刻意寫短。

---

## 目的

開發／實驗時想跑快一點。

## 量測先於設計

先量了成本才決定開關該裝在哪：

```
完整 check_dashboard_html（含 Level 2 quickjs sandbox）   35.6 ms
樣本：68 KB dashboard、18 筆查詢結果
```

**guard 本身是免費的。** 真正貴的是 `GUARD_REPAIR_MAX_RUNS = 5` 的修復迴圈——每輪一次完整模型重寫（真實 dashboard 約 18K tokens），分鐘級。

所以開關管的是「擋不擋」，不是「跑不跑」。命名 `ERD_GUARD_BLOCKING` 而非 `ERD_GUARD_ENABLED`：guard 一直啟用，變的是它會不會擋下 dashboard。

## 設計

`ERD_GUARD_BLOCKING`，預設 `true`。只影響 `app/agent/chat_turn.py`。

```python
# 1. 修復迴圈不進場
while GUARD_BLOCKING and not report.ok and repair_runs < GUARD_REPAIR_MAX_RUNS:

# 2. 終敗分支：非阻擋時照樣出貨
if not report.ok and GUARD_BLOCKING:
    dashboard_guard_failed = True
```

`check_dashboard_html` **一行都不改**。

### 三個刻意的決定

**一、預設 `true`。** 忘了設定的後果是「開發時跑得慢」，不是「出貨壞東西」。production 拿不到設定檔也安全。

**二、非阻擋失敗時不新增任何使用者可見字串。** 現有的 `DASHBOARD_REJECTED_PREFIX` 是「已退回不顯示」——非阻擋模式下 dashboard 有顯示，套用它就是說謊；`dashboard 製作失敗` 那個 STEP 同理。所以非阻擋失敗只走既有的 `logger.warning`（已含錯誤摘要），開發者看 server log。這也讓改動不碰凍結字串清單。

**三、`check_dashboard_html` 不動，是為了避開一個坑。** 它不是純驗證器——`_apply_erd_theme` 會把 `echarts.init(X)` 改寫成 `echarts.init(X, 'erd')`，兩個呼叫端下游都用 `report.html` 去注入。若把開關做成「整段跳過 guard」，會**靜默拿掉 erd 主題套用**，圖表變回 ECharts 預設色盤而不是 8 色 CVD 安全盤。讓 guard 照跑、只改「擋不擋」，這個坑自動不存在。

### 範圍：只管 `/chat`

`/repair` 也有 guard 重試迴圈，但**不納入**：

- 成本量級差很多——`/chat` 最多 5 輪整份重寫，`/repair` 只多 1 次模型呼叫
- `/repair` 是使用者主動按「修復」，回一份未驗證的 HTML 特別誤導

代價是兩條路徑行為不一致，需在程式碼註解裡說明理由，否則之後有人會問。

## 測試

- 既有的 guard 失敗路徑測試（`tests/test_chat.py` 的 `DASHBOARD_REJECTED_PREFIX` 與 `dashboard_guard` STEP 斷言）在預設值下必須**原封不動全綠**——它們就是「預設仍為阻擋」的證明
- 新增一條：`ERD_GUARD_BLOCKING=false` 時，一份必定過不了 guard 的 dashboard **仍會發出 `DASHBOARD_HTML`**，且**沒有** `dashboard_guard` STEP、ANSWER 沒有被加前綴

## 非目標

- 不做 per-rule 開關
- 不做 per-request／per-session 覆寫（env var 就夠）
- 不動 `/repair`
- 不修 findings 裡的 C1／C3——那是另一支 PR
