# MCP Datasource——API 作為 csv/xlsx 以外的資料源 — 設計

> 狀態：2026-08-30 討論定案。從零重設計（PR #62/#63/#65 僅作概念參考，不復活）；本 spec 涵蓋需求一（對話驅動取數）與需求二（publish/viewer 重放），實作分兩 Phase。

## 1. 需求

**需求一（對話驅動）**：使用者在對話開始前選擇 API connector（可多選）；每個 connector 對應一組 API——含「真正拉資料的 data API」與「提供 data API 參數選項的 lookup API」。首訊後 connector 鎖定不可改；**csv/xlsx 上傳與 connector 同 session 互斥**。對話中 agent 收集 data API 的 request payload：必要時打 lookup API 取選項→反問使用者（ask_user），或於 prompt 直接列選項——同時服務「熟」與「完全不會操作」兩類使用者。

**需求二（publish/分享/viewer 重放）**：dashboard 有 publish 按鈕可分享。其他人開啟時**拿自己的 SSO token 重打同樣的 data API**，看到自己有權看的資料。schema 漂移與 lookup 資料漂移 MUST 語意化失敗，不可白屏。

## 2. 決策紀錄（討論定案）

| 決策 | 結論 |
|---|---|
| 取數協定 | **MCP**：connector＝MCP server、data/lookup API＝tools、參數 schema＝inputSchema |
| MCP server 所有權 | **internal 自寫自養**（含攤平語意、憑證、tool 版本化）；repo 只做接入/落表/鎖定/replay 機制——同 upload_decrypt 整檔複寫哲學 |
| 分析引擎 | **維持 DuckDB＋1NF 表格契約**；「引擎換 Python（沙箱執行器）」獨立成案，不扣住本案（模型現況 deepseek-v4-flash 使其值得評估，但沙箱是獨立的平台級工作） |
| Auth | **user SSO token 上 wire**（Java→deepagent→MCP header），對話期用發話者的、重放期用 viewer 的——資料權限由下游 API 依 token 裁決 |
| HTML 直打 | 永遠不成立（MCP 是 agent 協定）；viewer 重抓一律 server 端零 LLM replay |

## 3. 架構總覽

```
對話期（Phase 1）
  選 connector（多選、與檔案互斥）→ 首訊鎖定
  → deepagent 只掛選定 server 的 MCP tools
  → agent: lookup tool → ask_user 反問 → data tool
  → 回應驗 1NF 契約 → 落 DuckDB 表（alias）→ snapshot 持久（跨 turn remount）
  → 分析 → dashboard；每次 data tool 呼叫記入 recipe

分享重放期（Phase 2）
  owner publish（凍結 recipe＋HTML）→ 分享
  → viewer 開啟 → Java 取 viewer SSO token → deepagent 零 LLM replay：
    分級驗證 ①②③④（見 §6）→ 按 recipe 重呼 MCP tools（viewer token）
  → 重算 __ERD_RESULTS__ → 注入 HTML → viewer 看到自己權限內的資料
```

## 4. Internal MCP server 契約規範（隨本 spec 交付給 internal 的文件）

1. **Tool 分類**：`data` tool（回資料落表）與 `lookup` tool（回選項供 agent/反問使用，不落表、巢狀不限）。分類以 tool annotation/命名慣例宣告。
2. **Data tool 回應＝1NF 表格契約**：頂層陣列、每元素一列、每格純量（string/number/boolean/null，日期 ISO-8601）、一對多展開成多列（long format）、列間欄集一致（缺值 null）、欄名 snake_case。直覺講法：「像一張願意直接交給分析師的乾淨 CSV，用 JSON 送、帶型別」。
3. **攤不平的判斷階梯**：① 拆多表（一 tool 一表＋join key；管線原生多 alias）→ ② server 端預切片/預聚合（樹狀給切面，切面旋鈕＝tool 參數）→ ③ 承認非 data（改列 lookup/context 或不納入）。「JSON 字串塞一欄」等半吊子逃生艙不開。
4. **Tool＝版本化契約**：breaking change（改名/刪欄/改參數）MUST 開新 tool 名；演進盡量 additive。
5. **錯誤訊息 MUST 可行動**：缺參指名、值不合法給候選——弱模型/確定性退貨的源頭要求。
6. **量級 caps**：rows/bytes 上限由 server 端強制並於超限時明確報錯。

## 5. Repo 端機制

- **Connector 目錄**：internal-owned 設定（可選的 MCP server 清單：id、名稱、連線位址）——repo 給 seam 與空預設，形式比照既有 internal 接縫慣例。
- **Session 選擇與鎖定（Java）**：`ChatSession` 記 `selectedConnectors`；**首訊定案**後不可改（概念沿 #65）；**互斥**：session 已有 active 檔案→選 connector 拒（409），已鎖 connector→上傳拒（409）。換源＝開新對話。
- **Wire**：files/sources 之外新增 connector 資訊；**SSO token 新欄位**（Java `CoworkContext.ssoToken` → request body → deepagent；log 全程遮罩，比照 `CoworkContext.toString()` 前例）。
- **deepagent 接入**：`request_context` 擴充 token contextvar；MCP client 以**選定 server 為範圍**掛 tools（未選組零注入——概念沿 #65）；per-user auth 形狀（per-call header vs per-session 連線）由 **Phase 1 首個 spike 定案**（見 §9 風險）。
- **落表管線**：data tool 回應→1NF 契約驗證（違規→語意化退貨指出列/欄）→DuckDB alias（沿 `open_locked_connection` 鎖門）→snapshot 原子落檔＋跨 turn remount（概念沿 #62）。
- **退貨整形與上限**：MCP 錯誤包一層可行動整形；每 turn tool 呼叫上限。
- **Recipe 記錄**（每次 data tool 呼叫）：① server id＋tool name＋args；② 觀測 schema＋dashboard 實際引用欄集；③ 參數出處（arg 值←哪個 lookup tool）；④ tool inputSchema hash。

## 6. 漂移防護——replay 分級驗證（需求二核心）

| 關卡 | 偵測 | viewer 所見 |
|---|---|---|
| ① tool 存在＋inputSchema hash | server 改版、tool 改名/改參數 | 「資料源接口已變更，請聯絡擁有者」 |
| ② lookup 重驗 | 重打 recipe 記錄的 lookup，驗當年參數值仍在 | 「選項 X 已不存在」＋現行清單（改選互動＝Phase 2+ 接縫） |
| ③ 欄位子集檢查 | 引用欄集 ⊆ 實際欄集（新增欄無害） | 「資料欄位結構已變更」 |
| ④ 渲染韌性 | 漏網之魚 | 單卡 try/catch＋resolver「—」fallback（沿 T26/data-bind 機制），壞卡不壞頁 |

**Viewer 權限≠漂移**：token 不同導致的空/少資料是 feature，正常渲染空狀態；③ 只驗結構不驗列數。

## 7. Publish/分享模型（Phase 2）

- publish＝凍結 recipe＋HTML 為可分享版本；分享預設形＝**capability link＋SSO 登入必須**（知道連結且登入者可開；資料層權限交給下游 API 依 viewer token 裁決）——如 internal 要更細的分享對象控制，於 Phase 2 細化。
- viewer 呈現走既有 artifact 認證交付管線（axios→srcdoc，#66 機制）；replay 在 server 端完成後注入，不動 CSP `connect-src 'none'`。

## 8. 安全

- **Token 邊界**：SSO token 只活在 Java context、wire body（遮罩）、deepagent contextvar、MCP request header；NEVER 進 log/prompt/recipe/落盤。
- **Prompt injection 面**：tools 唯讀、資料權限在下游 API、每 turn 呼叫上限；MCP server 為 internal 自有（無第三方工具描述注入面）。
- **鎖門不變**：DuckDB `enable_external_access=false`＋`lock_configuration` 照舊；MCP 呼叫只發生在 engine 掛表之前。

## 9. Phase 切分與風險

**Phase 1（對話驅動）**：connector 目錄 seam、UI 選擇器＋鎖定＋互斥、token wire＋contextvar、MCP client 接入＋tools 掛載、1NF 驗證＋落表＋snapshot、退貨整形＋上限、recipe 記錄（為 Phase 2 存料）、prompt 段（lookup→ask_user 劇本）。
**Phase 2（publish/重放）**：publish 凍結、分享 link、viewer 開啟流程、零 LLM replay＋分級驗證 ①③④（② 驗證報錯版）、viewer 改選互動與更細分享控制＝Phase 2+。

**風險與前置 spike**：
1. **P1｜MCP SDK per-user auth**（最大工程風險）：Phase 1 第一個 task＝spike 驗證 per-call header 或 per-session 連線的可行形狀（langchain-mcp-adapters／官方 python SDK）。
2. internal 寫 MCP server 的意願/能量與網段可達性——契約規範（§4）隨 spec 先交付對齊。
3. internal 端模型版本未確認（dev＝deepseek-v4-flash）；本設計不倚賴模型升級（確定性結構照舊），故不阻塞。

## 10. 與舊資產的關係

概念挖礦對照：#62→驗證階梯理念（退貨可行動）、snapshot/remount、caps；#63→recipe/零 LLM replay/語意化錯誤分級；#65→只注入選定組、session-lock 首訊定案。**三支舊 PR 於本案 Phase 1 開工時關閉**（內容不再 rebase）。csv/connector 互斥為新增規則（舊設計允許混用）。
