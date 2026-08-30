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
| 分析引擎 | **維持 DuckDB**（1NF 為撰寫指引，Phase 1 寬鬆落表先實驗——見 §4-2）；「引擎換 Python（沙箱執行器）」獨立成案，不扣住本案（模型現況 deepseek-v4-flash 使其值得評估，但沙箱是獨立的平台級工作） |
| Auth | **user SSO token 上 wire**（Java→deepagent→MCP header），對話期用發話者的、重放期用 viewer 的——資料權限由下游 API 依 token 裁決 |
| HTML 直打 | 永遠不成立（MCP 是 agent 協定）；viewer 重抓一律 server 端零 LLM replay |

## 3. 架構總覽

```
對話期（Phase 1）
  選 connector（多選、與檔案互斥）→ 首訊鎖定
  → deepagent 只掛選定 server 的 MCP tools
  → agent: lookup tool → ask_user 反問 → data tool
  → 寬鬆落表（read_json_auto，底線見 §4-2）→ DuckDB 表（alias）→ snapshot 持久（跨 turn remount）
  → 分析 → dashboard；每次 data tool 呼叫記入 recipe

分享重放期（Phase 2）
  owner publish（凍結 recipe＋HTML）→ 分享
  → viewer 開啟 → Java 取 viewer SSO token → deepagent 零 LLM replay：
    分級驗證 ①②③④（見 §6）→ 凍結參數重打落表呼叫（viewer token）
  → 重落表 → 重跑 recipe 的 qN SQL → 注入 HTML → viewer 看到自己權限內的資料
```

## 4. Internal MCP server 契約規範（隨本 spec 交付給 internal 的文件）

1. **操作劇本 skill（取代靜態 tool 分類）**：每個 connector MUST 附一份劇本文件——tools 清單與語意、**呼叫關係與順序**（含多步相依、lookup 餵 lookup、結果當他 tool 參數等非典型流程）、參數來源、範例。交付通道：MCP server 以 **MCP resource** 自述；in-code 模擬版隨 connector 物件附帶。tools 不做 data/lookup 靜態分類——同一 tool 可依流程扮演不同角色；「落不落表」由呼叫點決定（見 §5 `land_as`）。不落表的回應巢狀不限。劇本 MUST 依**四段式模板**撰寫（tools 清單與語意／呼叫順序與相依／參數來源／範例）——多 connector 多作者時的品質地板；每 connector tools 數**建議 ≤10**（tool 膨脹源頭治理）。
2. **落表形狀——Phase 1 採寬鬆模式，實驗後定案**。給 internal 的**撰寫指引**（軟性，管線不強制）：目標形＝1NF long format——每元素一列、每格純量（日期 ISO-8601）、一對多展開成多列、欄集一致、欄名 snake_case，「像一張乾淨 CSV，用 JSON 送、帶型別」。**Phase 1 落表管線寬鬆**：回應直接交 DuckDB `read_json_auto`（信封成怪表、淺巢狀成 STRUCT 欄照吞），僅保兩條底線——`land_as` safe-identifier 驗證（安全）與 0 列不落表。**候補機制**（實驗證據觸發才建）：record_path 信封拆封＋錯誤慣例宣告、淺巢狀攤平層、1NF 硬驗證。實驗裁決訊號：(i) agent 對 STRUCT/信封表的 SQL 成功率；(ii) 同 tool 兩次拉取的推斷 schema 穩定性；(iii)「圖有出來但內容錯」的垃圾落表頻率；(iv) 複選 connector 時的 tool 選擇準確率。recipe 觀測 schema 記 DuckDB 推斷後欄名——寬鬆模式下 §6 關卡 ③ 可能偏噪，屬實驗已知代價，Phase 2 開工前隨裁決一併定案。
3. **攤不平的判斷階梯**：① 拆多表（一 tool 一表＋join key；管線原生多 alias）→ ② server 端預切片/預聚合（樹狀給切面，切面旋鈕＝tool 參數）→ ③ 承認非 data（改列 lookup/context 或不納入）。「JSON 字串塞一欄」等半吊子逃生艙不開。
4. **Tool＝版本化契約**：breaking change（改名/刪欄/改參數）MUST 開新 tool 名；演進盡量 additive。
5. **錯誤訊息 MUST 可行動**：缺參指名、**值不合法/過期給候選**——這條同時是對話期確定性退貨與 **replay 漂移偵測的承重牆**（重放時過期參數靠它以語意化錯誤浮現，見 §6 ②）。
6. **量級 caps**：rows/bytes 上限由 server 端強制並於超限時明確報錯。
7. **傳輸模式 MUST 為 stateless streamable HTTP**（FastMCP `stateless_http=True` 級別的一個旗標）：每個 tool call 自包含、無跨請求 session 狀態——per-user auth 因此是「每請求各帶各的 Authorization」，且天然適配多 pod/load balancer。放棄的 server 推播功能（notifications/sampling）本案 tools 用不到。契約同時相容 2025-03-26 stateless 模式與 2026-07-28 原生 stateless spec（後者已把協定級 session 整個移除——本契約即協定演進方向）。

## 5. Repo 端機制

- **Connector 目錄**：internal-owned 設定（可選的 MCP server 清單：id、名稱、連線位址）——repo 給 seam 與空預設，形式比照既有 internal 接縫慣例。
- **Session 選擇與鎖定（Java）**：`ChatSession` 記 `selectedConnectors`；**首訊定案**後不可改（概念沿 #65）；**互斥**：session 已有 active 檔案→選 connector 拒（409），已鎖 connector→上傳拒（409）。換源＝開新對話。
- **Wire**：files/sources 之外新增 connector 資訊；**SSO token 新欄位**（Java `CoworkContext.ssoToken` → request body → deepagent；log 全程遮罩，比照 `CoworkContext.toString()` 前例）。
- **deepagent 接入——Connector 供應層雙實作**：repo 定義統一的 connector-tools 抽象（一個 connector 供應一組 tools：`name`／`inputSchema`／可呼叫體，**外加一份劇本 skill**）。repo 包裝每個 tool 加選用參數 **`land_as`（alias）**——帶了＝「寬鬆落表（§4-2 底線）→DuckDB→記 recipe」，沒帶＝回應進 agent context（lookup 式使用）；何時帶由劇本引導，落表決策在呼叫點而非 tool 靜態型別。劇本沿用 deepagent 既有 skills staging 機制、**只 stage 選定 connector 的劇本**（零注入原則延伸），且**漸進揭露**：context 僅含每個已選 connector 的一行索引，agent 需要時才讀劇本全文——目錄規模不影響單 session 成本。**複選情境**：跨 connector 關係不入劇本（配對知識 N² 不可維護）——沿 #65 概念以「跨 connector join 需使用者明確指定 key」護欄 prompt 承接；複選 tool 選擇準確率列實驗第四訊號。兩個實作：
  1. **MCP 版（internal 之後用）**：連上 internal 的 MCP server（**stateless streamable HTTP**，見 §4-7），把其 tools 映射進抽象；每次呼叫帶當下 contextvar 的 SSO token 進 `Authorization` header——stateless 下無連線綁身分問題；client 端 header 注入細節由小型 spike 驗證。
  2. **In-code 模擬版（dev/CI 先行）**：直接在 code 裡把 API 註冊成 tools（同一抽象、附同格式劇本、落表同樣走 §4-2 寬鬆管線）——dev/測試用它跑完整條「選 connector→lookup→ask_user→data→落表→recipe」管線，不需要真 MCP server。repo 內附示範 connector（合成資料）；internal 也可先用此形式在 code 層掛真 API 過渡，之後平移到自家 MCP server。**過渡期對齊不變式**（守住則換 MCP＝改設定非改架構）：(i) in-code tool 每次呼叫從 request_context 取 user SSO token 打 data API（NEVER service 帳號）；(ii) §4 契約整份適用於 in-code 實作（1NF 指引、可行動錯誤、caps 義務相同）；(iii) 劇本同格式，平移直搬；(iv) 平移時 tool 名與 inputSchema 保持穩定——recipe 為實作無關，in-code 時代發布的 dashboard 遷移後仍可 replay；若 schema 序列化形式改變致 hash 不符，屆時以 hash 換代寬限或重發布處理（遷移註記）。
  兩實作以 connector 目錄設定選擇；掛載範圍一律**只掛選定 connector 的 tools**（未選組零注入——概念沿 #65）。
- **落表管線**：`land_as` 回應→寬鬆落表（`read_json_auto` 直接吃；底線＝**0 列不落表**——空陣列推不出 schema，回可行動訊息由 agent 轉告）→DuckDB alias（沿 `open_locked_connection` 鎖門）→snapshot 原子落檔＋跨 turn remount（概念沿 #62）。**`land_as` 為模型控制字串：MUST 過 safe-identifier 驗證；同 alias 重落表＝取代（last-wins）**。多 connector 掛載時 **tool 名以 connector id 前綴命名空間化**（防跨 server 撞名）。
- **退貨整形與上限**：MCP 錯誤包一層可行動整形；每 turn tool 呼叫上限。
- **Recipe 記錄**：① **落表呼叫**（server id＋tool name＋args＋inputSchema hash＋觀測 schema）；② **qN SQL**（agent 對落表資料計算 __ERD_RESULTS__ 的查詢——重放鏈的後半，沿 #63 概念；引用欄集自 SQL 解析）；③ 前置呼叫僅記錄供稽核、**不重放**。**重放＝凍結參數重打落表呼叫（viewer token）→ 重落表 → 重跑 qN SQL → 注入**——不依新 lookup 重推參數（否則 dashboard 靜默變成另一個切片）；過期參數由契約 §4-5 可行動錯誤浮現。

## 6. 漂移防護——replay 分級驗證（需求二核心）

| 關卡 | 偵測 | viewer 所見 |
|---|---|---|
| ① tool 存在＋inputSchema hash | server 改版、tool 改名/改參數 | 「資料源接口已變更，請聯絡擁有者」 |
| ② 參數過期 | 重放序列時，過期/不合法參數由 server 依契約 §4-5 回可行動錯誤（含候選） | 「選項 X 已不存在」＋現行候選（改選互動＝Phase 2+ 接縫） |
| ③ 欄位子集檢查 | 引用欄集（自 recipe qN SQL 解析）⊆ 實際欄集（新增欄無害） | 「資料欄位結構已變更」 |
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

**Phase 1（對話驅動）**：connector 目錄 seam、UI 選擇器＋鎖定＋互斥、token wire＋contextvar、**connector 供應層抽象＋in-code 模擬版（先行，整條管線靠它開發與 CI）**、寬鬆落表＋snapshot＋實驗觀測點（§4-2 三訊號可量測化）、退貨整形＋上限、recipe 記錄（為 Phase 2 存料）、prompt 段＋connector 劇本 staging（載入與引導 land_as 的通用說明；per-connector 劇本由 internal 供）、**MCP 版 adapter（含 per-user auth spike，與主線並行、不阻塞）**。
**Phase 2（publish/重放）**：publish 凍結、分享 link、viewer 開啟流程、零 LLM replay＋分級驗證 ①②③④（② 由 server 可行動錯誤承重）、viewer 改選互動與更細分享控制＝Phase 2+。

**風險與前置 spike**：
1. **P1｜MCP client 的 per-request header 注入**（已由 stateless 契約大幅降級）：server 端 stateless 化讓 per-user auth 成為每請求自帶 header；殘餘 spike＝驗證 client SDK（官方 python SDK／langchain-mcp-adapters）對 stateless server 的逐請求 header 注入 ergonomics，與模擬版並行、不阻塞。
2. internal 寫 MCP server 的意願/能量與網段可達性——契約規範（§4）隨 spec 先交付對齊。
3. internal 端模型版本未確認（dev＝deepseek-v4-flash）；本設計不倚賴模型升級（確定性結構照舊），故不阻塞。

## 10. 與舊資產的關係

概念挖礦對照：#62→驗證階梯理念（退貨可行動）、snapshot/remount、caps；#63→recipe/零 LLM replay/語意化錯誤分級；#65→只注入選定組、session-lock 首訊定案。**三支舊 PR 於本案 Phase 1 開工時關閉**（內容不再 rebase）。csv/connector 互斥為新增規則（舊設計允許混用）。
