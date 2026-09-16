# 範例對照

全部取自本 repo 的 PR 描述。讀得懂的：#84、#81、#87。讀不懂的：#40、#38、#21、#19。

## 讀得懂的長什麼樣

### #84：每句一件事，有主詞有動詞

> 之前只有第一輪會掃，之後每輪 system prompt 裡的 skill 清單都是第一輪的快照。connector 的 skill 在第二輪起新增、改名、移除，模型都看不到。

兩句。第一句說現況，第二句說後果。沒有括號，沒有比喻。

> deepagents 0.5.5 的 `SkillsMiddleware.before_agent` 開頭是「state 裡已經有 `skills_metadata` 就直接 return」。這個值存在 checkpoint 裡，所以同一個 thread 只掃一次。

程式名稱後面緊接它做什麼。讀者不用打開檔案就知道根因。

### #81：告訴 reviewer 從哪裡看、為什麼

> **不要看整個 PR diff**，分兩步看。

粗體用在給讀者的指示。

| 順序 | 檔案 | 看什麼 | 為什麼重要 |
|---|---|---|---|
| 1 | `app/agent/connectors/wrapper.py` | `describe_raw_response_shape` 多接 `response` | 這段英文是模型唯一能得知 raw 形狀的來源。句子講錯，模型就讀錯層。 |

表格欄位名是讀者會問的問題。

### #87：背景先行，名詞單獨定義，階段命名

> connector 的資料是即時的，埋進 HTML 一發布就過期。讓頁面自己在檢視時抓，dashboard 才會跟資料來源同步。

問題、後果、決定。三句。

> 名詞：**宿主（host）** 指把 dashboard 放進 sandboxed iframe 裡開起來的那一頁，以及它背後代打 MCP 的那一段。（中略）下文的「宿主」都指這個角色。

撐起整份 PR 的詞單獨一段定義，並說明產品裡對應什麼、這個 PR 裡由什麼代替。

> 資料流分兩個時期。左邊是對話期（使用者跟 agent 對話，產出 `dashboard.html`），右邊是檢視期（有人打開那份 dashboard）。

先命名階段，之後每一節都說自己屬於哪一邊。圖裡直接標「未實作」。

> **高**：信任邊界，或每份 dashboard 都帶著、每次呼叫都跑到的碼。出錯是使用者看得到的行為錯誤或安全暴露。

等級先定義「出錯會怎樣」，表格裡才用高中低。

## 讀不懂的長什麼樣

### #40：句尾把一串名詞與動詞黏在一起

> 查詢結果改物件列：`record_query` 落檔即 `[{column: value}]`(columns 保留供排序)；注入端 Proxy 包每列——**錯欄名或任何 index 存取直接 throw**(訊息帶 qN+可用欄名)，魔術索引綁錯欄的安靜 NaN 全類轉為 onerror 修復鏈路可接的爆炸。

前半段沒問題。「：」「；」後面每一項都是一個具體事實，讀者能逐項對上程式。

問題在最後一個子句：

> 魔術索引綁錯欄的安靜 NaN 全類轉為 onerror 修復鏈路可接的爆炸

- 主詞是「魔術索引綁錯欄的安靜 NaN 全類」。一個名詞前面掛了三層修飾，裡面還藏了一個動作（綁錯欄）。
- 受詞是「onerror 修復鏈路可接的爆炸」。又是一層修飾，「鏈路」「爆炸」都是比喻。
- 整句只有一個動詞「轉為」，卻要讀者同時解開兩邊的名詞串。

改寫。把藏在修飾裡的動作拆成自己的句子，比喻換成實際行為：

> 以前用數字序號讀值、序號對錯欄時，只會得到 NaN，圖會靜靜畫錯。現在這種錯誤會拋出例外，觸發 `onerror`，進入修復流程。

### #38：bullet 沒有動詞

> versionId 一律 `opaque_version_id()` 不可逆摘要——manifest 在檔案工具 jail 內模型可讀，不存路徑/檔名/uuid

問題：
- 第一個子句沒有動詞，讀不出這是規則、改動、還是描述。
- 「——」讀者要猜是「因為」還是「所以」。
- 「jail」沒有解釋。

改寫：

> versionId 一律用 `opaque_version_id()` 產生，這是不可逆的摘要。因為 manifest 放在模型能讀到的目錄裡，所以裡面不能有路徑、檔名或 uuid。

### #21：句子太長，括號裡塞清單

> 移除 `chat_message.referenced_tables_json` 與其整條持久化鏈（entity 欄位、orchestrator 的 `[[table:id]]` marker 抽取與 `tableAccum` 持久化、`TableMarkerUtils`、`MessageDto` 欄位、前端 history 側解析與 `MessageBubble` history prop、architecture.md 相關敘述）。

問題：
- 一句。括號裡七個項目。
- 「持久化鏈」是比喻。

改寫：

> 移除 `chat_message.referenced_tables_json` 欄位。連同它相關的程式一起刪：
> - entity 欄位
> - orchestrator 抽取 `[[table:id]]` marker 與 `tableAccum` 寫入
> - `TableMarkerUtils`
> - `MessageDto` 欄位
> - 前端 history 解析與 `MessageBubble` 的 history prop
> - architecture.md 的相關段落

### #19：說服，不描述

> **接縫成本超過分歧成本時，正確答案是接受分歧並偵測它**——與 `pom.xml` 同一個判準。

問題：
- 這是格言，不是描述。讀者不知道實際做了什麼。
- 「接縫」「分歧」是自創詞，整份 PR 反覆使用，從未解釋。
- 粗體標的是原則。

改寫：

> `index.html` 改成兩邊各自維護一份，不再用 plugin 注入。原因：plugin 讀不到 `.env` 檔，要修得多加一層設定，而 `index.html` 很少改。兩邊各自維護一份比較便宜。`pom.xml` 也是同樣的處理。

## 差異總結

| 讀得懂 | 讀不懂 |
|---|---|
| 主詞與受詞是短名詞 | 主詞或受詞是三層「的」串起來的名詞堆 |
| 一句一件事，句號結尾 | 「——」接子句，讀者要猜關係 |
| 每句有主詞有動詞 | 名詞堆疊 |
| 程式名稱後接一句它做什麼 | 程式名稱自己撐句子 |
| 用實際動作 | 用比喻 |
| 自創詞第一次出現就解釋 | 自創詞從不解釋 |
| 表格放多欄位項目 | 括號裡塞清單 |
| 粗體標讀者要做的事 | 粗體標結論與口號 |
| 順序照讀者需要 | 順序照 commit 或論證 |
| 描述發生了什麼 | 說服讀者相信什麼 |
