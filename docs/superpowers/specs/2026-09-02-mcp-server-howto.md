# 怎麼寫一台能接上 Cowork 的 MCP server

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
    validate_output=False,  # 關閉輸出驗證,理由見下方說明
    route_maps=[
        # 只放要給模型用的查詢 endpoint,其餘全部排除(建議 ≤10 支)
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

**建議加上 `validate_output=False`**:不加的話,spec 的 response schema 會變成輸出契約,
client 每次回應都拿它驗——spec 只要有一點跟實況不符(最常見:欄位實際會回 null 但 spec
沒標 nullable),整支 tool 就直接不能用,錯誤長這樣:`Invalid structured content returned
by tool: None is not of type 'array'`。而 Cowork 這端分析用的是實際回應資料、不靠輸出
宣告,這層驗證對它沒有實質保護,誤傷卻很實在,所以建議直接關掉。spec 把 response 寫誠實
仍然是好習慣(文件品質),但不用當成執行期的門檻。

## 一之一、client 實際會打哪些 MCP 協定方法

server 只會收到下面五種請求,如果使用fastmcp就不需要寫任何下列的協定層程式碼:

| 協定方法 | 什麼時候被打 |
|---|---|
| `initialize` | 每個連線開頭的握手(stateless 下**每次呼叫都會重打一次**,正常現象) |
| `tools/list` | 每輪對話載入 connector 時(取得工具清單與 schema) |
| `tools/call` | 模型每次呼叫工具時 |
| `resources/list` | 每輪載入時列舉 skills |
| `resources/read` | 逐檔下載 skill 內容(SKILL.md、支援檔、`_manifest`) |

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
progress notifications。

一輪對話的固定開銷=1 次 `tools/list`＋1 次 `resources/list`＋
skill 檔數次 `resources/read`(每 skill 上限 20 檔);之後每次工具呼叫=1 次 `initialize`
＋1 次 `tools/call`(每輪工具呼叫上限預設 12 次)。

## 二、Tools 的規矩 (OpenAPI 包裝的情況)

用 `FastMCP.from_openapi` 包的時候, 一支 tool 就是一個 endpoint. 模型看到的工具定義是 spec 裡的 operationId, 參數, 描述; 模型拿到的資料是 endpoint 的 response body. 所以規矩全部落在「挑哪些 endpoint」「spec 怎麼寫」「response 長什麼樣」三件事上.

先講 Cowork 那邊拿到 tool 之後會怎麼用, 規矩都是從這裡推出來的:

- tool 名稱掛進 agent 時會變成 `{connector id}_{tool 名}`, 名稱裡不是英數底線的字元換成底線.
- 模型每呼叫一次, response 就自動存成一張 DuckDB 表, 表名是 `{connector id}_{tool 名}_{參數的雜湊}`. 同樣參數再呼叫是同一張表, 不同參數各一張.
- 模型看到的不是原始資料, 是「表名, 列數, 欄位清單, 前 20 列預覽」. 之後用 SQL 查那張表.
- 一輪對話最多呼叫 50 次 (Cowork 端可設). 每次請求逾時 30 秒 (可設).
- 任何呼叫失敗 (連不上, 逾時, 4xx, 5xx, 協定錯誤) Cowork 都會立刻再試一次.

### 1. 只放 GET 的查詢 endpoint

`route_maps` 只放查詢用的 GET, 其他一律 `EXCLUDE`. 原因: Cowork 對任何失敗都會重試, 模型一輪內也可能對同一支 tool 用同樣參數打好幾次. 會寫入, 扣額度, 觸發動作的 endpoint 絕對不能露出來.

### 2. response body 要是 JSON 的陣列, 或是包在 `data` 裡的陣列

fastmcp 會把 response body 轉成結構化回傳值: JSON object 原樣送, 其他 (陣列, 字串, 數字) 包成 `{"result": ...}`, Cowork 會自己拆開. Cowork 端的存法:

| response body | 會怎麼存 |
|---|---|
| JSON 陣列, 每個元素一個 object | 每個元素一列, 最理想 |
| JSON object, 裡面有 `data` 這個陣列 | 只有 `data` 存成表, 其他頂層欄位 (例如 `errorCode`, `total`) 以文字附給模型看 |
| 純字串或數字 | 存成一列一欄 `result`, 幾乎沒用 |
| 其他 object (沒有 `data`) | 整包存成一列, 巢狀變 STRUCT 欄, 模型很難用 |

只有 server 完全不給結構化回傳值時才會被拒收 (OpenAPI 包裝不會發生, 除非 response 不是 JSON).

回空陣列代表「這組參數沒資料」, Cowork 不會存表, 會請模型換參數.

### 3. 每列像一張乾淨的 CSV

- 每列的 key 一致, 每格是純量: 字串, 數字, 布林, null. 巢狀 object 會變成 STRUCT 欄, 巢狀陣列會變成 LIST 欄, 模型要多燒好幾次錯誤 SQL 才學會展開.
- 日期時間用 ISO 8601 字串 (`2026-08-05` 或 `2026-08-05T10:30:00+08:00`), 數字用數字不要用字串, 布林用布林.
- 欄位名用 `snake_case`, 只用英數底線, 不要空白與 SQL 保留字 (`order`, `group`, `select` 這類). 欄位名會原樣進 SQL.
- 同一個 endpoint 每次回的欄集要一樣, 沒值就給 null, 不要有時多一欄有時少一欄. 模型是照第一次看到的欄位寫 SQL 的.
- 一對多的關係展成多列, 或拆成另一個 endpoint 加 join key.

### 4. spec 裡的參數描述就是模型的說明書

Cowork 端只擋「缺必填」, 型別對不對是下游 API 在驗. 所以 spec 裡每個參數都要:

- 有 `description`, 說明值從哪裡來 (「來自 `GET /fabs` 回的 id」) 與格式 (「ISO 週別, 例如 2026-W32」).
- 有固定選項的用 `enum`.
- 必填的標 `required`.
- 盡量用純量. 陣列型的參數可以用, 但表名的雜湊會看不出內容, 模型只能靠回饋文字對應.

response schema 寫誠實但不用當門檻, `validate_output=False` 的理由見第一節.

### 5. 錯誤 response 要讓模型知道下一步

下游 API 回 4xx/5xx 時, response body 的文字就是模型看到的錯誤, 一字不改. 所以 body 要寫成「哪裡錯, 下一步怎麼辦」:

- 好的例子: `{"error": "週別 'X' 無資料, 可用週別: 2026-W29 到 2026-W32"}`.
- 壞的例子: `{"error": "invalid input"}` 或只有 HTTP 400 沒有 body.
- 參數不合法要回 4xx 並給候選, 不要回空陣列. 空陣列留給「參數合法但真的沒資料」.
- Cowork 會在錯誤前面加上「這是 MCP server 回報的錯誤」, body 本身不用再解釋來源.

### 6. 資料量在 API 端擋

單次 response 有列數或 bytes 上限, 超過就回 4xx 請對方縮小範圍 (「資料超過 1 萬列, 請縮短時間區間」), 不要硬吐大包. Cowork 端不切片, 整包進記憶體再存表; 30 秒逾時也是這裡撞到的.

大資料需求的做法: 多開一個聚合版 endpoint (API 先算好), 或提供時間區間, 分頁這類縮小範圍的參數.

### 7. endpoint 的數量與名稱

- 一台 server 露出的 tool 建議不超過 10 支. 模型每次都會讀到全部工具定義.
- tool 名來自 operationId, 太醜就用 `mcp_names` 改成短而具體的名字 (`list_fabs`, `get_quality`), 只用小寫英數底線. 兩支 tool 的名稱在把特殊字元換成底線之後不能一樣.
- 用 lookup 型的 endpoint 提供選項 (`GET /fabs`), 讓模型能先查再問使用者, 不要讓模型猜參數值.
- endpoint 改了參數或回傳欄位就開新的 operationId (例如 `getQualityV2`), 舊的留一陣子. 模型與 skill 是照名字對應的.

## 三、Skills(使用說明書)的規矩

skills 資料夾長這樣,`SkillsDirectoryProvider` 指過去就好:

```
skills/
└── usage/                       ← 目錄名要跟 SKILL.md frontmatter 的 name 一模一樣
    ├── SKILL.md                 ← 必要,開頭要有 frontmatter(見下)
    └── references/
        └── weeks.md             ← 選用的補充文件(只有 .md 會被掛載)
```

`SKILL.md` 開頭必須是:

```markdown
---
name: usage
description: my-connector 的使用說明——查詢前必讀,涵蓋工具清單、呼叫順序、參數來源、範例。
---
```

- **name 只要在同一台 server 內唯一**;Cowork 會在 staging 時自動加上 `{connector id}-`
  前綴,例如上面這個 `usage` 掛給模型看到的名稱會是 `my-connector-usage`——不同 server
  的 skill 因為前綴不同不會互相撞名,server 端不需要自己拼前綴。name 不可含 `/` 或 `..`。
- 內容照四段式寫:**工具清單與語意/呼叫順序與相依/參數來源/範例**。範例段請放
  「怎麼查」的實際示範,包括資料形狀特殊時的 SQL (例如信封表的 UNNEST 展開寫法)
  ——模型會照抄你的範例,範例寫得好錯誤率直接降。
- **改版盡量只改內容、不要改 name**——進行中的對話 session 對 skill 清單有快取,
  改名對它們等於 skill 消失(視同 breaking change)。
- 量上限:每個 skill 20 個檔/總計 200K 字元,超過的部分會被 client 丟棄, 被丟掉的檔名會附在 SKILL.md 末尾告訴模型不要去讀。

## 四、認證與身分

1. **每個請求都會帶兩個 SSO header**這是「發問的那個人」的憑證,轉發方式見第一節骨架的 `forward_sso`
   (httpx event hook + `get_http_headers`),逐請求帶給下游 data API,
   誰能看到什麼資料,由下游 API 認這個 token 決定。之後做「分享儀表板」功能時,帶的會是「打開的人」的 token,同一套機制。
2. **需要 service token 的 server**:驗 `Authorization: Bearer <token>`。對應的部署
   設定是兩邊:Mongo catalog 該 connector 的 `bearerTokenKey` 欄位宣告 key 名、
   deepagent 的 `CONNECTOR_BEARER_TOKENS`(JSON dict)提供 key→token 值。不需要認證
   就兩邊都不設。
