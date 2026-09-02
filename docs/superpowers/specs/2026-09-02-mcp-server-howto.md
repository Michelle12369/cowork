# 怎麼寫一台能接上 Cowork 的 MCP server(白話版)

> 給要寫 connector server 的人。規格原文在 `2026-08-30-mcp-datasource-design.md` §4;
> 這份是「照著做就能動」的操作說明,以現行 client 實作(fastmcp v3)為準。

## 一、最小可動的骨架

```python
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.providers.skills import SkillsDirectoryProvider
import uvicorn

server = FastMCP("my-connector")

@server.tool()
def list_stations(fab: str) -> list[dict]:
    """列出指定 fab 的站點清單(id/name),供 get_metrics 的 station 參數選項。"""
    return [{"id": "ETCH-01", "name": "蝕刻一站"}, ...]

@server.tool()
def get_metrics(fab: str, station: str, week: str) -> dict:
    """取得指定站點/週別的量測資料。"""
    if week not in VALID_WEEKS:
        raise ToolError(f"週別 '{week}' 無資料——可用週別:{', '.join(VALID_WEEKS)}")
    return {"data": [...], "errorCode": ""}

server.add_provider(
    SkillsDirectoryProvider(roots="./skills", supporting_files="resources")
)
uvicorn.run(server.http_app(stateless_http=True), host="0.0.0.0", port=8200)
```

裝 `fastmcp>=3`,寫完上面這些就是一台合格的 server。

## 一之一、client 實際會打哪些 MCP 協定方法

你的 server 只會收到下面五種請求,全部由 fastmcp 自動處理,你不需要寫任何協定層程式碼
——列出來是讓你知道流量長什麼樣、除錯時看 log 對得上號:

| 協定方法 | 什麼時候被打 | 對應你寫的東西 |
|---|---|---|
| `initialize` | 每個連線開頭的握手(stateless 下**每次呼叫都會重打一次**,正常現象) | 無——fastmcp 自動回版本與能力 |
| `tools/list` | 每輪對話載入 connector 時(取得工具清單與 schema) | `@server.tool()` 的簽名與 docstring |
| `tools/call` | 模型每次呼叫工具時 | tool 函式本體 |
| `resources/list` | 每輪載入時列舉 skills | `SkillsDirectoryProvider` 自動 |
| `resources/read` | 逐檔下載 skill 內容(SKILL.md、支援檔、`_manifest`) | 同上 |

除了方法本身,協定層還有兩個 MUST:

1. **傳輸=stateless streamable HTTP**——`http_app(stateless_http=True)` 一個旗標搞定。
   每個請求自包含、server 不記任何跨請求 session 狀態;認證因此是「每請求各帶各的
   header」,天然適配多 pod/load balancer。掛載路徑預設 `/mcp`(catalog 登記的 URL 要含它)。
2. **`tools/call` 的回應 MUST 帶 structuredContent**——tool 回傳 dict/list 時 fastmcp
   自動生成;回純文字(str)不會生成,client 會以可行動錯誤拒收。這是 client 解析資料的
   唯一通道(文字 content 只在 isError 時被讀取,當錯誤訊息用)。

**不會用到的**(server 不必支援,fastmcp 有沒有實作都無所謂):`prompts/*`、
`resources/subscribe` 與變更通知、sampling、elicitation、roots、logging、
progress notifications。未來的分享重放(Phase 2)依賴面更窄:只有 `initialize`＋`tools/call`。

流量特徵供容量規劃:一輪對話的固定開銷=1 次 `tools/list`＋1 次 `resources/list`＋
skill 檔數次 `resources/read`(每 skill 上限 20 檔);之後每次工具呼叫=1 次 `initialize`
＋1 次 `tools/call`(每輪工具呼叫上限預設 12 次)。

## 二、Tools 的規矩

1. **參數用型別簽名宣告**(`fab: str`、`week: str = "latest"`)——fastmcp 自動生成
   schema,模型看得到完整宣告(含型別/預設/enum)。**必填與否由簽名決定**;client 端
   只擋「缺必填」,**型別對不對是你的 server 在驗**(fastmcp 自動),所以參數描述寫清楚。
2. **回傳一律 dict 或 list**——fastmcp 自動轉 structured output。回純文字會被 client
   拒收(「缺 structuredContent」)。
3. **錯誤一律 `raise ToolError("可行動訊息")`**——訊息會原文送到模型面前,所以要寫
   「缺什麼、給候選」:好的例子是「週別 'X' 無資料——可用週別:W29~W32」;壞的例子是
   「invalid input」。注意:raise 其他例外(ValueError 之類)訊息會被 fastmcp 遮罩,
   模型只會看到一句空泛的錯誤。
4. **資料形狀盡量攤平**(1NF:每列一筆、每格純量,像一張乾淨的 CSV 用 JSON 送)。
   信封(`{"data": [...], "errorCode": ""}`)與淺巢狀吞得下去,但實測代價很真實:
   模型要多燒好幾次錯誤 SQL 才學會展開信封,探查結果還會爆量。攤得越平,分析越穩。
5. **量的上限自己擋**:單次回應的 rows/bytes 超過你定的上限時,回可行動錯誤請對方
   縮小範圍(加 filter/縮週期),不要硬吐大包。
6. **`land_as` 是保留字**——你的 tool 參數不能叫這個名字(client 掛載時會直接拒絕)。
7. 一台 server 的 tools **建議不超過 10 支**;要改參數/改名/刪欄位=開新 tool 名,
   舊的留著(breaking change 用版本化處理,不要原地改)。

## 三、Skills(使用說明書)的規矩

skills 資料夾長這樣,`SkillsDirectoryProvider` 指過去就好:

```
skills/
└── my-connector-usage/          ← 目錄名 MUST 等於 SKILL.md frontmatter 的 name
    ├── SKILL.md                 ← 必要,開頭 MUST 有 frontmatter(見下)
    └── references/
        └── weeks.md             ← 選用的補充文件(只有 .md 會被掛載)
```

`SKILL.md` 開頭必須是:

```markdown
---
name: my-connector-usage
description: my-connector 的使用說明——查詢前必讀,涵蓋工具清單、呼叫順序、參數來源、範例。
---
```

- **`supporting_files="resources"` 這個參數不能省**——預設模式下補充文件不會被送出去。
- **name 全域唯一**(跨所有 connector),建議 `{connector-id}-{用途}`;不可含 `/` 或 `..`。
- 內容照四段式寫:**工具清單與語意/呼叫順序與相依/參數來源/範例**。範例段請放
  「怎麼查」的實際示範,包括資料形狀特殊時的 SQL 範式(例如信封表的 UNNEST 展開寫法)
  ——模型會照抄你的範例,範例寫得好錯誤率直接降。
- **改版盡量只改內容、不要改 name**——進行中的對話 session 對 skill 清單有快取,
  改名對它們等於 skill 消失(視同 breaking change)。
- 量上限:每個 skill 20 個檔/總計 200K 字元,超過的部分會被 client 丟棄。

## 四、認證與身分

1. **每個請求都會帶兩個 SSO header**(名稱依部署配置,dev 預設 `X-SSO-Token`/
   `X-SSO-Url`)——這是「發問的那個人」的憑證,你的 server 拿它對下游 data API 取數,
   資料權限由下游依這個 token 裁決。分享重放時帶的是「看的人」的 token,同一機制。
2. **需要 service token 的 server**:驗 `Authorization: Bearer <token>`。對應的部署
   設定是兩邊:Mongo catalog 該 connector 的 `bearerTokenKey` 欄位宣告 key 名、
   deepagent 的 `CONNECTOR_BEARER_TOKENS`(JSON dict)提供 key→token 值。不需要認證
   就兩邊都不設。

## 五、上線登記

寫好的 server 讓 Cowork 看得到,只要在 Mongo 加一筆:

```javascript
db.connector_catalog.insertOne({
  connectorId: "my_connector",        // 唯一 id,會成為工具名前綴(my_connector_get_metrics)
  displayName: "我的資料源",           // 使用者在 UI 看到的名字
  mcpUrl: "http://<host>:8200/mcp",   // /mcp 後綴必要
  bearerTokenKey: "my-gateway-token"  // 選用,不需認證就不加這欄
})
```

## 六、自我檢查清單

- [ ] `stateless_http=True`(每個請求自包含,無跨請求狀態)
- [ ] 所有 tool 回 dict/list,錯誤用 ToolError 且訊息可行動
- [ ] 參數簽名含型別與描述;沒有叫 `land_as` 的參數
- [ ] rows/bytes 上限有擋
- [ ] skills 目錄:目錄名=frontmatter name、`supporting_files="resources"`、四段式含 SQL 範例
- [ ] SSO headers 有接、有往下游帶;需要 service token 的話 Bearer 有驗
- [ ] catalog 登記的 mcpUrl 帶 `/mcp` 後綴
