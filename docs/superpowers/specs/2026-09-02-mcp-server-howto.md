# 怎麼寫一台能接上 Cowork 的 MCP server(白話版)

> 給要寫 connector server 的人。規格原文在 `2026-08-30-mcp-datasource-design.md` §4;
> 這份是「照著做就能動」的操作說明,以現行 client 實作(fastmcp v3)為準。

## 一、最小可動的骨架(用現成的 OpenAPI spec 包)

大多數 data API 已經有 OpenAPI(Swagger)文件——不用手寫任何 tool,直接讓 fastmcp
照著 spec 自動生成:

```python
import httpx
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_headers
from fastmcp.server.providers.openapi import MCPType, RouteMap
from fastmcp.server.providers.skills import SkillsDirectoryProvider
import uvicorn

API_BASE = "https://data-api.internal/v1"

# 關鍵:把「打進來的 SSO header」逐請求轉發給下游 API——
# 誰發問就用誰的憑證取數,權限由下游 API 決定。
async def forward_sso(request: httpx.Request) -> None:
    request.headers.update(get_http_headers(include={"x-sso-token", "x-sso-url"}))

client = httpx.AsyncClient(base_url=API_BASE, event_hooks={"request": [forward_sso]})
spec = httpx.get(f"{API_BASE}/openapi.json").json()

server = FastMCP.from_openapi(
    openapi_spec=spec,
    client=client,
    name="my-connector",
    route_maps=[
        # 只放要給模型用的查詢 endpoint,其餘全部排除——寧缺勿濫(建議 ≤10 支)
        RouteMap(methods=["GET"], pattern=r"^/stations$", mcp_type=MCPType.TOOL),
        RouteMap(methods=["GET"], pattern=r"^/metrics$", mcp_type=MCPType.TOOL),
        RouteMap(pattern=r".*", mcp_type=MCPType.EXCLUDE),
    ],
    mcp_names={"getMetricsV1": "get_metrics"},  # operationId 太醜時改個好名字(選用)
)
server.add_provider(
    SkillsDirectoryProvider(roots="./skills", supporting_files="resources")
)
uvicorn.run(server.http_app(stateless_http=True), host="0.0.0.0", port=8200)
```

裝 `fastmcp>=3`,寫完上面這些就是一台合格的 server。tool 的名稱來自 operationId
(或 `mcp_names` 改名)、參數與描述都來自 OpenAPI spec——**spec 寫得多清楚,模型就看得
多清楚**,所以參數的 description、enum、required 請在 spec 裡補好,這比任何 prompt 都有效。

> 沒有 OpenAPI spec 的資料來源,也可以用 `@server.tool()` 手寫函式(參數用型別簽名宣告、
> 回傳 dict/list)——本機的 demo server 就是手寫的例子,規矩同第二節。

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

除了上面的方法,還有兩件一定要做到的事:

1. **要用 stateless 模式**——就是範例裡 `http_app(stateless_http=True)` 那個參數。
   意思是每個請求都是獨立的,server 不用記得上一個請求發生過什麼;誰的憑證就跟著
   誰的請求走。好處是你要開幾台 server、要不要放 load balancer 後面都隨意。
   另外掛載路徑預設是 `/mcp`,登記到 catalog 的網址記得帶上。
2. **工具一定要回傳 dict 或 list**——fastmcp 會自動把它包成協定要求的結構化格式。
   如果回傳純文字,client 讀不到資料,模型只會收到一句「這個工具沒回傳結構化資料」
   的錯誤。(純文字唯一的用途是錯誤訊息:工具失敗時的 ToolError 訊息就是走文字通道。)

**不會用到的**(server 不必支援,fastmcp 有沒有實作都無所謂):`prompts/*`、
`resources/subscribe` 與變更通知、sampling、elicitation、roots、logging、
progress notifications。未來的分享重放(Phase 2)依賴面更窄:只有 `initialize`＋`tools/call`。

流量特徵供容量規劃:一輪對話的固定開銷=1 次 `tools/list`＋1 次 `resources/list`＋
skill 檔數次 `resources/read`(每 skill 上限 20 檔);之後每次工具呼叫=1 次 `initialize`
＋1 次 `tools/call`(每輪工具呼叫上限預設 12 次)。

## 二、Tools 的規矩

1. **模型看到的工具定義=你的 OpenAPI spec**(參數/型別/必填/enum/描述全部照搬)。
   client 端只擋「缺必填」,**型別對不對是 server 這端在驗**——所以 spec 的參數描述
   跟錯誤回應要寫清楚。手寫 tool 的話同理,型別簽名+docstring 就是規格。
2. **回傳一律 dict 或 list**——fastmcp 自動轉 structured output。回純文字 client 讀不到資料
   (模型會收到錯誤,見「一之一」第 2 點)。
3. **錯誤一律 `raise ToolError("...")`**——訊息會一字不改送到模型面前,所以要寫成
   「讓對方知道哪裡錯、下一步怎麼辦」:好的例子是「週別 'X' 無資料——可用週別:W29~W32」;壞的例子是
   「invalid input」。注意:raise 其他例外(ValueError 之類)訊息會被 fastmcp 遮罩,
   模型只會看到一句空泛的錯誤。走 OpenAPI 包裝時,下游 API 回 4xx/5xx 就是模型看到的
   錯誤——把錯誤 response body 寫清楚(缺什麼參數、可用值有哪些),效果等同 ToolError。
4. **資料形狀盡量攤平**(1NF:每列一筆、每格純量,像一張乾淨的 CSV 用 JSON 送)。
   信封(`{"data": [...], "errorCode": ""}`)與淺巢狀吞得下去,但實測代價很真實:
   模型要多燒好幾次錯誤 SQL 才學會展開信封,探查結果還會爆量。攤得越平,分析越穩。
5. **量的上限自己擋**:單次回應的 rows/bytes 超過你定的上限時,回一句清楚的錯誤請對方
   縮小範圍(例如「資料超過 1 萬列,請縮短時間區間」),不要硬吐大包。
6. **`land_as` 是保留字**——你的 tool 參數不能叫這個名字(client 掛載時會直接拒絕)。
7. 一台 server 的 tools **建議不超過 10 支**;要改參數/改名/刪欄位=開新 tool 名,
   舊的留著(breaking change 用版本化處理,不要原地改)。

## 三、Skills(使用說明書)的規矩

skills 資料夾長這樣,`SkillsDirectoryProvider` 指過去就好:

```
skills/
└── my-connector-usage/          ← 目錄名要跟 SKILL.md frontmatter 的 name 一模一樣
    ├── SKILL.md                 ← 必要,開頭要有 frontmatter(見下)
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
   `X-SSO-Url`)——這是「發問的那個人」的憑證,轉發方式見第一節骨架的 `forward_sso`
   (httpx event hook + `get_http_headers`),逐請求帶給下游 data API,
   誰能看到什麼資料,由下游 API 認這個 token 決定。之後做「分享儀表板」功能時,帶的會是「打開的人」的 token,同一套機制。
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

- [ ] 有開 `stateless_http=True`(server 不記跨請求狀態)
- [ ] 所有 tool 回 dict/list;錯誤用 ToolError,訊息讓人知道下一步怎麼辦
- [ ] 參數簽名含型別與描述;沒有叫 `land_as` 的參數
- [ ] rows/bytes 上限有擋
- [ ] skills 目錄:目錄名=frontmatter name、`supporting_files="resources"`、四段式含 SQL 範例
- [ ] SSO headers 有接、有往下游帶;需要 service token 的話 Bearer 有驗
- [ ] catalog 登記的 mcpUrl 帶 `/mcp` 後綴
