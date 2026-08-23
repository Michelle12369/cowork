# Snapshot 身分指紋化（§12）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** snapshot 身分從「模型指定的 alias」改為「`(connector, 正規化 params)` 的內容指紋」——同源重抓自動覆蓋（不增生，化解 M9）、換 params 落新檔（舊資料不被沖）、alias 降為 DuckDB 表名（顯示名）。

**Architecture:** 引入 `snapshot_fingerprint(connector, params)`＝`sha256(connector + sorted-params-json)[:16]`。落檔 `api_snapshots/{fingerprint}.json`（不再 `{alias}.json`）；`fetches.json` 每筆記 `{fingerprint, connector, params, alias}`。fetch 工具算指紋、同指紋覆蓋刷新、alias 只做「DuckDB 表名」的碰撞語意。跨 turn 掛回 glob 指紋檔、從 fetches.json 映射 alias 掛表。**指紋（身分）與 content-hash（manifest 版本）正交、都保留**——指紋定「哪次查詢」、content-hash 定「資料變沒變」。

**Tech Stack:** Python 3.11／deepagent engine（stdlib＋duckdb）／pytest asyncio_mode=auto

**設計權威**：`docs/superpowers/specs/2026-08-20-api-connector-design.md` §12。

**分支**：`feat/connector-groups`（stacked on feat/api-connector；§11 隨後在同分支）。

## Global Constraints

- engine 禁 langchain 家族 import；指紋函式純 stdlib（hashlib/json）
- `AGENT_CONNECTORS_FILE` 未設＝功能整體關閉不變式**不得破壞**（無 connector 時行為 byte-identical，e2e 釘）
- 身分＝指紋、版本＝content-hash，兩者**都保留、不混用**（manifest 的「重抓內容變」偵測仍靠 `snapshot_version_token`）
- fetch 工具 never-raise、退貨帶指引不變；錯誤訊息 NEVER 含 URL/token
- 命名 ≥3 字元；註解 1–2 行；測試 snake_case；formatter hook（用法先、import 後）
- 每 task：`cd deepagent-service && uv run pytest tests/ -q && uv run ruff check .` 全綠才 commit；NEVER `| tail`
- **既有 connector 測試面**：Phase 1 的 fetch/跨 turn/manifest 測試會因身分模型改變而需適配——適配是本 plan 的一部分，NEVER 為了過測試弱化身分語意

## Task 1: 指紋函式＋落檔／記錄改身分

**Files:**
- Modify: `deepagent-service/app/engine/api_fetch.py`
- Test: `deepagent-service/tests/test_api_fetch.py`

**Interfaces:**
- Produces: `snapshot_fingerprint(connector: str, params: dict) -> str`（16 字元 hex）；`land_snapshot(workspace, fingerprint, payload) -> Path`（檔名改 `{fingerprint}.json`）；`record_fetch(workspace, fingerprint, alias, connector_name, params)`（記 fingerprint）；`load_fetch_records` 回傳每筆含 `fingerprint`
- 供 Task 2（fetch 工具）、Task 3（跨 turn 掛回）

- [ ] **Step 1: failing tests**
  - `snapshot_fingerprint`：同 connector+同 params（**dict 順序無關**，靠 sort_keys）→ 同指紋；換 params → 不同指紋；換 connector → 不同指紋
  - `land_snapshot`：落檔名＝`{fingerprint}.json`；同指紋重落＝覆蓋同檔（原子寫保留）
  - `record_fetch`＋`load_fetch_records` round-trip：每筆含 `{fingerprint, alias, connector, params}`

- [ ] **Step 2: 實作**

```python
def snapshot_fingerprint(connector: str, params: dict) -> str:
    """snapshot 身分＝(connector, 正規化 params) 的指紋——同源重抓同指紋(覆蓋刷新),換
    params 換指紋(新檔)。sort_keys 讓 dict 順序無關;ensure_ascii=False 中文參數穩定。"""
    canonical = f"{connector}\n{json.dumps(params, sort_keys=True, ensure_ascii=False)}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
```

`land_snapshot(workspace, fingerprint, payload)`：`snapshot_path = api_snapshots_dir / f"{fingerprint}.json"`，其餘原子寫不變。
`record_fetch`：簽名加 `fingerprint`，記進 dict（`{"fingerprint": fingerprint, "alias": alias, "connector": connector_name, "params": params}`）。舊 `.corrupt` 自癒不變。

（本分支 `record_fetch` 是 Phase 1 版無 `columns`——artifact-replay 的 `columns` 由該分支之後 rebase 時適配，本 plan 不碰。）

- [ ] **Step 3: 全套＋ruff → commit** `feat(deepagent): snapshot 指紋身分——(connector,params) 內容指紋落檔,fetches 記 fingerprint`

## Task 2: fetch 工具的指紋去重與 alias 表名語意

**Files:**
- Modify: `deepagent-service/app/agent/tools/data.py`（`fetch_api_data_tool`＋helper）
- Test: `deepagent-service/tests/test_data_tools.py`

**Interfaces:** Consumes Task 1。工具行為改：算指紋→落指紋檔→掛 alias 表；身分去重取代舊「alias 已存在就退貨」。

- [ ] **Step 1: failing tests**（覆蓋新身分語意）
  - **同 (connector,params) 重抓、同 alias** → 覆蓋刷新（指紋檔覆蓋＋DROP 舊表重建），**不退貨**（M9 化解）
  - **同 (connector,params)、不同 alias** → 覆蓋同指紋檔，新 alias 表指向它；不重複落檔
  - **不同 params、同 alias**（換查詢卻同表名）→ 退貨叫換 alias（表名衝突，避免靜默沖掉語意不同的表）
  - alias 仍過 `SAFE_IDENTIFIER_PATTERN`；validate_against／最近似候選／每 turn cap／回滾等既有階梯**行為不變**（適配到指紋落檔即可）
  - 0 列、非法 JSON 回滾：回滾刪的是**指紋檔**

- [ ] **Step 2: 實作要點**

`fetch_api_data_tool(connector, params, alias)` 內：
1. 算 `fingerprint = snapshot_fingerprint(connector, params)`
2. alias 合法性檢查不變
3. **碰撞語意改**：查 `mounted` 表——
   - `alias in mounted`：需判斷這個既有 alias 表是不是同指紋（查 fetches.json 該 alias 最後一筆的 fingerprint）。同指紋＝刷新→`DROP TABLE {alias}` 後重建（放行）；不同指紋＝換了查詢卻撞表名→退貨「alias {alias} 已用於不同查詢，請換名」
4. `execute_fetch` → `land_snapshot(workspace, fingerprint, payload)`（指紋檔）
5. `CREATE TABLE {alias} FROM read_json_auto({fingerprint}.json)`
6. max_rows 超限回滾：`DROP TABLE {alias}` ＋刪 `{fingerprint}.json`（但若該指紋檔被別的 alias 共用則不刪——v1 簡化：指紋檔僅此次 fetch 產生，回滾刪之安全；docstring 註明）
7. `record_fetch(workspace, fingerprint, alias, connector, params)`

`_resolve_lookup_alias`／validate_against：邏輯不變（仍靠 fetches.json 的 alias），只是 record 多帶 fingerprint 欄位不影響其查詢。

- [ ] **Step 3: 全套＋ruff → commit** `feat(deepagent): fetch 工具指紋去重——同源重抓覆蓋刷新(化解 M9),alias 降為表名`

## Task 3: 跨 turn 掛回按指紋＋manifest 整合

**Files:**
- Modify: `deepagent-service/app/agent/chat_turn.py`（掛回邏輯）
- Test: `deepagent-service/tests/test_chat.py`

**Interfaces:** Consumes Task 1/2。跨 turn glob 指紋檔、從 fetches.json 映射 alias 掛表。

- [ ] **Step 1: failing tests**
  - turn1 落指紋檔＋fetches 記錄 → turn2 新 ChatTurn → 指紋檔以 fetches 記的 alias 掛回、可查
  - **同 alias 跨 turn 重抓同源**（M9 場景）→ 同指紋覆蓋、不增生新表（斷言 workspace 只一個指紋檔、alias 表指向最新內容）
  - 撞上傳檔 alias→上傳優先（既有行為保留）；損壞指紋檔隔離（quarantine，檔名改指紋不影響 probe 邏輯）

- [ ] **Step 2: 實作**

`chat_turn.py` 掛回段（現況 glob `*.json` 以 `path.stem` 當 alias）改為：
- glob `api_snapshots/*.json`（現在 stem 是指紋，不是 alias）
- 讀 `load_fetch_records`，建 `fingerprint → alias`（last-wins per fingerprint）
- 對每個指紋檔：alias 從映射取（映射缺該指紋＝孤兒檔，跳過＋warning）；`Source(alias, path, "json")` 掛回
- `fetches.json` 本身排除（它在 api_snapshots_dir 內、非指紋檔——現況已排除，確認名稱比對仍對）
- manifest：version token 仍用 `snapshot_version_token`（content-hash）——指紋是身分、content-hash 是版本，同源重抓內容變時 content-hash 變、觸發既有「來源已變」提示。**兩者都餵**：manifest 條目 alias 用掛載 alias、version 用 content-hash（不變）

- [ ] **Step 3: 全套＋ruff → commit** `feat(deepagent): 跨 turn 指紋掛回——glob 指紋檔+fetches 映射 alias,M9 增生根治`

## Task 4: e2e＋既有測試適配＋功能關閉不變式

**Files:**
- Modify: `deepagent-service/tests/test_api_connector_e2e.py`（既有 e2e 適配指紋身分）
- Test: 全套跑一遍抓適配漏網

- [ ] **Step 1**：既有 connector 測試中，凡斷言 `{alias}.json` 檔名的改為 `{fingerprint}.json`（或改斷言「以 alias 掛載的表可查」的行為層，避免綁死檔名）；e2e 加一條「同 alias 跨 turn 重抓不增生」的端到端斷言
- [ ] **Step 2**：功能關閉不變式 e2e——`AGENT_CONNECTORS_FILE` 未設 → 無 fetch 工具、snapshot 相關全不觸發、byte-identical（指紋機制在 connector 關閉時完全不可達）
- [ ] **Step 3: 全套＋ruff → commit** `test(deepagent): 指紋身分 e2e+既有測試適配+功能關閉不變式`

## 驗收與收尾（主迴圈）

- 全套＋ruff 終驗；opus 全分支終審（重點：身分模型正確性、M9 是否真化解、功能關閉不變式、never-raise 保留）→ 修整波
- ledger 記帳：§12 指紋身分上線、M9 化解、artifact-replay rebase 時需適配 `columns`/recipe 到指紋身分（recipe 改存 fingerprint 更穩）
- **不單獨開 PR**：§12 與 §11 共 `feat/connector-groups`，§11 完成後整條一起（PR base＝feat/api-connector）
- 接續：§11（per-session 資料源選擇）另寫 plan，在本分支續作
