# MCP 宿主：前端 bridge（hop ②）與 Java `/mcp-call`（hop ③）

> 狀態: **設計定案 2026-09-15**, 待 plan. branch `feat/mcp-dashboard-host`（自 `feat/mcp-dashboard` `2b5fbc7` 分出）. 本文落實主 spec `2026-09-08-mcp-dashboard-on-autoland-design.md` §7 D9 傳輸面的 hop ② 與 hop ③; 契約（訊息名、端點形狀、五個 code、五條不變量）已在該節凍結, 本文只定留給實作的數值與落點, 不改契約. 錯誤碼全表見 `2026-09-10-mcp-error-codes-design.md`, 本文只用它 §2 給「前端 bridge」與「Java proxy」那兩列.
>
> 範圍: 只做 D9 hop ②③. D10 第二半（connector 版 repair prompt）維持延後, `/repair` 仍是 file 模式 prompt. 全螢幕頁一併接 bridge. PR #88 開進 `feat/mcp-dashboard`.

## 1. 現況與缺口

- hop ①（`mcp()` prelude）由 deepagent `results.py` 於產出時注入, 只在 connector 模式; hop ④（deepagent `POST /tool-call`）已落地, 568 測試綠. 兩者都在 `feat/mcp-dashboard`.
- hop ②: `ArtifactPanel` 已有 `iframeRef` 與 `erd-artifact-error` listener（驗 `event.source`）, 但沒有人回應 `erd-mcp-call`. `ArtifactFullscreenPage` 連 `iframeRef` 都沒有.
- hop ③: Java 沒有 `/mcp-call`. 材料齊全: `ArtifactService` 的 ownership 兩步（`findById` → `SessionGuard.loadOwned`）, `ConnectorCatalogService.resolveSpecs`, `ConnectorSpec` record（與 deepagent pydantic 同形）, `AnalysisBrowserRepairClient` 作為第二支非串流 deepagent client 的模板, `CoworkContextHolder.ssoToken()/ssoUrl()`.
- 前端不需要 `dataMode`: prelude 只在 connector 模式注入, file 模式的頁面永遠不會發 `erd-mcp-call`, bridge 無條件掛著即可. 主 spec U3 的「`dataMode` 落在哪個 DTO」以「不需要」結案. Java 側 connector 模式由 `ChatSession.selectedConnectors` 判定, 也不需要新欄位.

## 2. 決策

| # | 事項 | 決定 |
|---|---|---|
| H1 | deepagent 非 2xx 由誰折疊 | **Java 全部折成 200 + `error`**: 422 → `INVALID_CALL`; 5xx、逾時、連不上 → `RETRYABLE`; 401（Java 的 bearer 設錯）→ `CONNECTOR_UNAVAILABLE`; status 寫進 `message`. 前端的 status 折疊只剩 Java 自己的 404／400／5xx 這條保險. 理由: Java 能寫出帶 status 的具體 `message`, 頁面只看到一種形狀, 測試集中一處 |
| H2 | 前端 in-flight 上限 | **不設**. 收到 `erd-mcp-call` 立刻發送. 主 spec 建議的 6 來自 HTTP/1.1 每 host 六條連線, HTTP/2 下不成立; 後端保護若需要應看 MCP server 承受度另議. **接受的代價（2026-09-15 終審補記）**: 每個 in-flight 呼叫在 Java 佔一條 servlet worker 直到回應或逾時（service 內 `.block()`）; HTTP/2 下單一頁面可同時開約 100 條 stream, 一份惡意或寫壞的 dashboard 可耗盡執行緒池, 影響同機其他使用者. v1 接受; 待辦: per-artifact 速率限制（U6）或 bridge 端 in-flight 上限, 觀察每 artifact 每分鐘呼叫數後決定. |
| H3 | 前端逾時 | 75 秒, 從 bridge 發送那刻起算; 逾時回 `{error:{code:"RETRYABLE", message:"host timeout after 75 s"}}`, 遲到結果丟棄. **逾時包含瀏覽器連線排隊時間, 接受**: 頁面無法排除那段; 75 秒對幾秒量級的呼叫, 要同時超過六張卡且每個都拖十幾秒才會誤報, 誤報時 viewer 按 Retry 語意仍對. 日後觀察到誤報再加上限（2026-09-15 終審改案: 原 60 秒與 Java、deepagent 同值, 三層疊在同一數字無 headroom） |
| H4 | Java 對 deepagent 的逾時 | 獨立 property `erd.agent.analysis.tool-call-timeout-seconds`, 預設 65. 不沿用對話用的 `request-timeout-seconds`（180, local 600）: 檢視期呼叫必須短於前端逾時, 否則前端先報 `RETRYABLE` 而 Java 還在等. 三層逾時由內而外遞增: deepagent 最壞 60 s（30 s × 2 次）< Java 65 s < 前端 75 s, 每層留 headroom 讓內層自己的錯誤訊息能到達頁面（2026-09-15 終審改案） |
| H5 | 全螢幕頁 | 一併接. bridge 抽成 hook, 兩個宿主頁共用; 全螢幕頁只接 `mcp()` 呼叫, 不接修復卡 |
| H6 | `data` 直通的實作方式 | Java 以**原始字串**回傳 deepagent 2xx body, 不經 Jackson 重組; 只為了 log 另外唯讀解析一次 `error.code`. 守不變量 1 |
| H7 | iframe 重掛 | 每筆 pending 記住發送當時的 `contentWindow`, 回貼前比對, 不同即丟; `artifactId` 或 iframe 換掉時整批清空. 理由: 新 iframe 從 id `1` 重新編號, 舊呼叫的遲到結果會撞到新 id |
| H8 | PR | 原定暫不開; 2026-09-15 整條做完、三側測試綠與 opus 終審後開 PR #88（base `feat/mcp-dashboard`） |
| H9 | iframe 自我導覽 | 頁面可 `location.href=` 導到外部 origin（sandbox 不禁止自我導覽, CSP 無導覽指令）; 導覽後 `contentWindow` 仍是同一物件、`event.origin` 仍為 null, 只靠 source 比對擋不住, 會繞過 artifact CSP 的 `connect-src 'none'`. bridge 以 capture 階段監聽 iframe `load`: 偵測到同一元素第二次 `load` 完成即標記已導覽（只偵測既成的導覽, 不阻止導覽本身）, 之後的 `erd-mcp-call` 一律忽略、pending 結果不再貼回; 重掛換新元素自動解除（2026-09-15 終審補記）. **殘餘風險**: 換進來的文件在自己的 `load` 之前就能發 `erd-mcp-call`, 也能用永不完成的子資源讓 `load` 永遠不發, 因此這道防護只是 defense in depth, 擋的是意外或天真的導覽, 不是圍籬; 真正的圍籬仍是 sandbox、產出端不生成惡意 JS、以及下游 API 憑 viewer 的 SSO 決定資料權限. 宿主端無法從 opaque origin 觀察到導覽開始（`contentWindow` 同物件、`contentDocument` 為 null、`origin` 恆為 null）, 要收緊只能改 prelude 契約（另開 spec）. |

## 3. Java hop ③

### 3.1 端點

```
POST /api/artifacts/{id}/mcp-call
Body:  { connector: string, tool: string, args: object }
200:   deepagent 回應原樣（{data} 或 {error:{code,message}}）, 或 Java 自己產生的 {error:{code,message}}
404:   artifact 不存在／非本人（與 GET /api/artifacts/{id} 同一條 ownership 規則）, ErrorResponseDto NOT_FOUND
400:   body 驗證失敗（connector／tool 空白, args 不是物件）, Spring 預設 400
```

### 3.2 新增類別

| 類別 | 位置 | 職責 |
|---|---|---|
| `McpCallRequestDto` | `web.dto` | record; `@NotBlank connector`, `@NotBlank tool`, `@NotNull Map<String, Object> args`. `@Schema` 三欄 |
| `AnalysisToolCallClient` | `agent.provider.analysis` | `@Component` + `@ConditionalOnProperty(erd.agent.provider=langgraph-analysis)`, 照 `AnalysisBrowserRepairClient` 建 WebClient（同 baseUrl、bearer、maxInMemorySize）. `Mono<String> call(ConnectorSpec, tool, args, ssoToken, ssoUrl)`: `POST /tool-call`, body `{connector, tool, args}`, SSO 兩 header 用 `AnalysisAgentProperties.ssoTokenHeader()/ssoUrlHeader()`. 2xx → body 字串原樣; 非 2xx 與例外依 H1 折成 `{error}` 字串（用 Jackson 序列化一個小 record, 不手拼字串）. `.timeout(toolCallTimeoutSeconds)` |
| `ArtifactMcpCallService` | `service` | `@Service`; 依序: `artifacts.findById` → `NotFoundException` → `sessionGuard.loadOwned(sessionId)` → `selectedConnectors` 為 null／空或不含 `connector` → 200 `INVALID_CALL`（message: `connector '<id>' is not in this session; allowed: [a, b]`）→ `connectorCatalogService.resolveSpecs(List.of(connector))`, `NotFoundException` → 200 `CONNECTOR_UNAVAILABLE`（message 用例外文字）→ client 缺席（`ObjectProvider` 為空）→ 200 `CONNECTOR_UNAVAILABLE`（`connector mode is not enabled on this server`）→ `client.call(...).block()`. SSO 值在呼叫前於 request thread 取出 |
| `AnalysisAgentProperties` | `config` | 加 `int toolCallTimeoutSeconds`, 預設 65; 既有 back-compat 建構子補預設值 |

Controller 加一個 method, 回 `ResponseEntity<String>` 帶 `application/json`; `@Operation`／`@ApiResponse`（200／400／404）照規範. 進入點 log 與 service 完成 log 各一行:

```
POST mcp-call artifact=<id> connector=<id> tool=<tool> argKeys=[k1,k2]
mcp-call artifact=<id> connector=<id> tool=<tool> ms=<n> ok=true|false code=<code>
```

不記 `args` 值, 不記 header 值, 不記 `data`.

### 3.3 不變量對照

1. `data` 原樣直通 → H6, 原始字串回傳.
2. `args` 原樣直通 → `Map<String, Object>` 經 Jackson 轉回 JSON, 數值語意不變（`1.10` 可能變 `1.1`, 值相等）; 不補預設值, 不轉型.
3. SSO 只在 header → client 簽名把 SSO 當獨立參數, 不進 body record; 例外訊息不含 header 值.
4. 一種形狀 → Java 自產的 `error` 與 deepagent 的同形; 只有 404／400 走 HTTP.
5. 一次 handler → 前端責任, 見 §4.

## 4. 前端 hop ②

### 4.1 檔案

| 檔案 | 內容 |
|---|---|
| `src/hooks/useMcpBridge.ts` | `useMcpBridge(iframeRef, artifactId)`. 掛 `message` listener, 只接 `data.type === 'erd-mcp-call'` 且 `event.source === iframeRef.current?.contentWindow`; 其他忽略. 每筆呼叫: 記 `{ sourceWindow, timer }` 到 `pendingById`, 呼叫 `callArtifactMcp`, 成功或折疊後 `postResult(id, result)`. `postResult` 先確認 `pendingById` 仍有此 id 且 `sourceWindow === iframeRef.current?.contentWindow`, 否則丟棄; 貼回用 `contentWindow.postMessage({type:'erd-mcp-result', id, result}, '*')`. 逾時依 H3. cleanup: 移除 listener, 清所有 timer, 清 `pendingById`. deps `[iframeRef, artifactId]` |
| `src/config/mcpBridge.ts` | `MCP_BRIDGE_TIMEOUT_MS = 75_000` |
| `src/utils/mcpResult.ts` | 純函式 `foldMcpFailure(error: unknown): McpResult`: axios 錯誤 401／403／404 → `AUTH`, 400／422 → `INVALID_CALL`, 其他 status 與無 response → `RETRYABLE`; `message` 含 `HTTP <status>` 或 `network error`. 非 axios 錯誤 → `RETRYABLE` |
| `src/api/artifactApi.ts` | `callArtifactMcp(id, {connector, tool, args}): Promise<McpResult>`, `apiClient.post`, 回 `response.data` 原樣 |
| `src/types.ts` | `McpErrorCode`（五個字面值聯集）, `McpResult`（`{data: unknown} \| {error:{code, message}}`）, `McpCallMessage` |
| `ArtifactPanel.tsx` | 呼叫 `useMcpBridge(iframeRef, artifact?.artifactId)`; 既有 error listener 不動 |
| `ArtifactFullscreenPage.tsx` | 建 `iframeRef` 傳給 `ArtifactFrame`, 呼叫 `useMcpBridge` |

### 4.2 行為

- bridge 不看 `data`, 不改形狀, `args` 原樣交給 axios 序列化.
- bridge 落地後, prelude 對 `TOOL_ERROR`／`INVALID_CALL` 會自動發 `erd-artifact-error`, `ArtifactPanel` 既有 listener 會送進修復卡. 這是 D10 第一半已在 deepagent 完成的行為, 前端不加碼; `/repair` 仍是 file 模式 prompt, 修不好屬預期（D10 第二半延後）. 全螢幕頁沒有修復卡, 該訊息被忽略.
- 同一份 dashboard 多次呼叫同一 tool 不去重不快取（契約 hop ② 不做的事）.

## 5. 測試

Java（`@WebMvcTest` + `MockWebServer`, 照 `ArtifactRepairControllerTest` 與 `AnalysisBrowserRepairClientTest` 樣式）:

| 測試 | 釘住 |
|---|---|
| `ArtifactMcpCallControllerTest`: 200 透傳 service 回傳字串、404 `NOT_FOUND`、400 缺 tool、400 `args` 是陣列 | §3.1 |
| `ArtifactMcpCallServiceTest`: connector 不在 session（message 列允許 id）、session 是 file 模式、catalog 下架、client 缺席、log 只含 arg keys | §3.2 service 列 |
| `AnalysisToolCallClientTest`: wire body 含 `connector`／`tool`／`args` 且 SSO 不在 body 只在 header; 2xx body 字串逐字相等（含 `{result:[...]}` 信封與浮點字面值）; 422／503／401／逾時／連線拒絕各一條; SSO token 值不出現在任何 `message` | H1, H6, 不變量 1／3 |
| 契約 fixture: 讀 `deepagent-service/tests/fixtures/mcp_result_examples.json`, 逐例經 client 透傳後字串相等 | 跨服務契約 |

前端（Vitest + RTL, 照 `ArtifactPanel.test.tsx` 的 `MessageEvent` 模式）:

| 測試 | 釘住 |
|---|---|
| `mcpResult.test.ts`: 401／403／404／400／422／500／無 response 各一條; fixture 每個 code 原樣通過 | §4.1 折疊 |
| `useMcpBridge.test.tsx`（透過 `ArtifactPanel` 掛載, spy `iframe.contentWindow.postMessage`）: 正常往返恰好一次; `type` 或 `source` 不對忽略; 逾時回 `RETRYABLE` 且遲到結果不貼回（fake timers）; iframe 換 `key` 後舊結果丟棄; 卸載時 listener 與 timer 清掉 | H3, H7, 不變量 5 |
| `ArtifactFullscreenPage.test.tsx`: 全螢幕頁回應 `erd-mcp-call` | H5 |

## 6. 完成條件

- backend `./mvnw test` 全綠; frontend 測試全綠; deepagent 不動, 不重跑.
- opus 全 branch 終審 Ready to merge, 結論留在 ledger; PR 依 H8 暫不開.
- 手動端到端（使用者執行）: 本機起前端、Java、deepagent 加 spike `mock_server.py`, 開一份 connector dashboard 看圖有畫、換篩選恰好一次呼叫; 停掉 mock server 看 `RETRYABLE` 卡與 Retry.

## 7. 非目標

- D10 第二半（connector 版 repair prompt、`RepairRequest.connectors`、Java repair client 帶 connectors 與 SSO）.
- per-artifact tool 允許清單（U5）、Java 側額度（U6）、分享頁 viewer 存取規則（U4）.
- in-flight 上限（H2 明確不做, 觀察後再議）.
- spike `shell.html`／`bridge.py` 對齊: 產品宿主落地後 spike 只剩 mock server 有用, 不再維護宿主半邊.
- per-artifact 速率限制與 in-flight 上限（H2 接受的代價, 待 U6）.
