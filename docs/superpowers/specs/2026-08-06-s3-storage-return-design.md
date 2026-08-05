# S3 儲存路線回歸設計（上傳檔 + agent workspace）

**日期**：2026-08-06
**狀態**：已與使用者逐節確認
**背景**：internal 環境最終確定不提供 PVC RWX，只提供 S3-compatible 物件儲存。先前為 PVC 單一路線移除的 S3 程式碼（backend `1d3aae9`、deepagent `49021cc`/`5b3430b`）需要回歸，且 workspace 同步模型須配合 internal 儲存規範重新設計。

## 目標與約束

- 檔案上傳、artifact HTML、agent workspace 三者都能走 S3。
- **雙路線保留**：`local`（現行磁碟實作，完整保留）與 `s3` 以設定切換；測試與備援走 local。
- 本機開發（IntelliJ + `uv run` 裸跑）也接 docker MinIO——與 internal 同一套設定面，只換值。
- internal 物件儲存認證形態：**endpoint + access key/secret key、path-style**（MinIO/Ceph 風格）。
- **internal 儲存規範：同一個 object key 不可重複上傳**（bucket 有 versioning，但治理規範禁止同檔多次 PUT）。所有寫入路徑必須 write-once：每個 key 一生只寫一次。
- deepagent workspace 同步模型採 **turn 邊界同步**：turn 開始全量 pull 到本地 scratch，turn 內全本地讀寫，turn 結束全量 push。不採每檔案操作直打 S3，也不用 DuckDB httpfs。

## 環境佈局

| 環境 | backend | deepagent workspace |
|---|---|---|
| 本機（IntelliJ + uv 裸跑） | `erd.storage.type=s3` → docker MinIO | `STORAGE_BACKEND=s3` → 同一顆 MinIO |
| docker compose | s3 → MinIO | s3 → MinIO |
| internal | s3 → 內部物件儲存 | s3 → 同 |
| 自動化測試 | `local`（committed 預設） | `local`（committed 預設） |

Committed 預設一律 `local`（零外部依賴）；本機切 s3 寫在各自 gitignored 設定檔（`application-local.properties`、`one.properties`）。

## Bucket 佈局

單一 bucket（預設 `erd-cowork`），前綴分層：

```
uploads/{sessionId}/{UUID}_{safeName}          ← StorageKeyUtils 現行格式，不變
artifacts/{sessionId}/{UUID}_{safeName}        ← 同上
workspace/{userId}/sessions/{sessionId}/
  gen-{epochMillis 13 碼}-{8 碼隨機 hex}/       ← generation 快照（見下）
    dashboard.html
    results/q1.json
    queries/q1.sql
    sources.md
    _complete                                  ← 完成標記，最後寫入
```

storageKey 格式與 local 模式完全相同——兩種 backend 的 key 可互換（舊設計已驗證的性質）。

## Backend：S3FileStorage 回收

從 `1d3aae9^` 回收，幾乎原樣：

- `S3FileStorage`（`@ConditionalOnProperty(erd.storage.type=s3)`）：`store()` 先 spool 到 temp file 再 `putObject`（S3 需要 content length；2GB CSV 走 ephemeral disk，舊實作已驗證）、`read()`/`delete()` 直打 S3，`SdkException` 一律包成 `IOException` 遵守介面契約。
- `S3StorageConfig`：建 `S3Client` bean——endpoint override、region、path-style；credentials 走 SDK default chain（`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` env vars），設定檔永不放 secrets。
- `StorageProperties` 加回 `S3(String endpoint, String region, String bucket, boolean pathStyleAccess)` 巢狀 record 與 `type` 欄位。
- pom.xml 加回 AWS SDK v2 `s3` 依賴（版本以 internal registry 可取得者為準）。
- 測試回收：`S3FileStorageTest`（mock `S3Client`）、`StorageConditionalRegistrationTest`（local/s3 條件註冊互斥）。
- **Artifact HTML 已走 `FileStorage`（PR #20），零改動即獲得 S3 支援。**
- write-once 合規：`StorageKeyUtils.buildKey` 每次呼叫產生含 UUID 的新 key，重試即新 key，天然合規。

## 上傳檔交棒：path 語意改為 storageKey

現行：backend 傳 `sourceRoot + "/" + storageKey` 本地路徑，deepagent（DuckDB）直接讀共享檔案系統。

s3 模式下共享檔案系統不存在，改為：

- backend `LangGraphAnalysisProvider` 在 s3 模式直接傳 **storageKey**（`uploads/…` 開頭）；local 模式維持現行 `sourceRoot` 組路徑，零改動。
- deepagent 端 `STORAGE_BACKEND=s3` 時把收到的 path 視為 S3 key，下載到 **sources cache**：`{AGENT_WORKSPACE_ROOT}/.sources-cache/{storageKey}`。
- 上傳檔 immutable（上傳後永不改寫）→ cache 檔案存在即跳過下載；2GB CSV 每 pod 只拉一次。DuckDB 照常讀本地路徑。
- sources cache 不在 session workspace 內，永不 push 回 S3，也不受 generation 機制影響；pod 重啟即消失，重新下載即可。
- deepagent 用自己的 boto3 client 與 backend **共用同一組 credentials**（同 bucket 讀取）；不走 presigned URL（internal 支援度未知，且無必要）。

## deepagent workspace：S3WorkspaceStore（generation 快照模型）

回收 `WorkspaceStore` Protocol（`5b3430b` 移除的抽象：`prepare()`/`persist()`，`LocalWorkspaceStore` 為現行行為），`S3WorkspaceStore` 依 write-once 規範重寫為 generation 模型。boto3 依賴加回 `pyproject.toml`（版本以 internal registry 可取得者為準；engine 純度規則允許 stdlib + boto3，LLM 框架仍禁止）：

### prepare()（turn 開始）

1. `ListObjectsV2` 列出 `workspace/{user}/sessions/{session}/` 下的 generation prefixes。
2. 過濾出**含 `_complete` 標記**者，取 timestamp 最大的 generation，全量拉到本地 scratch。
3. 沒有任何完整 generation → 空 workspace 開工（全新 session）。
4. 拉取失敗 fail-loud：例外往上冒、request 500（照舊——資料不完整不能開工）。

### per-turn scratch 隔離

本地 scratch 路徑為 `{root}/.turns/{隨機 hex}/{user}/sessions/{session}/`（固定 `.turns` 目錄、隨機 hex 在 root 之後、session 路徑之前）——**每 turn 一個隔離目錄**，persist 完成後刪除。理由：兩個併發 turn（雙 tab）落在同一 pod 時不互踩；成本近零（本來每 turn 就全量重拉）。以 `.turns/{hex}` 為 scratch base 讓 session 目錄相對佈局（含 `workspace.root.parents[1]/skills` 的 user skills 路徑算法）與 local 模式完全一致。local 模式不變（仍是共享 session 目錄，維持現行行為）。

### persist()（turn 結束，SSE 收尾前）

1. 產生新 generation prefix：`gen-{當下 epoch millis}-{secrets.token_hex(4)}`。epoch millis 13 位數固定寬度（至 2286 年），字典序＝時間序；隨機尾碼保證 key 全域唯一——即使極端併發也零 key 碰撞、零規範違反。
2. 全量 push scratch 內容（**排除** `.skills/` staging；sources cache 本就在 workspace 外），**最後**寫 `_complete` 標記。
3. **失敗處理（修正舊版靜默吞錯缺陷）**：整段 retry 兩次（每次都是新 timestamp＋新尾碼，全新 key）；三次都失敗 → 發 **ERROR event** 讓使用者知道本輪結果未保存，而非下一輪默默拿到舊資料。
4. 成功後清理：保留**最新 2 個完整 generation**（`KEPT_GENERATIONS = 2` 常數，不做設定項），刪除其餘所有 generation prefix。無 `_complete` 的殘骸只有 timestamp 舊於 1 小時才可刪——防止誤刪另一個併發 turn 正在推送中的半成品（其 `_complete` 尚未落地）。刪除失敗不擋主流程（殘留由 session 保留清理兜底）。

### 併發語意（雙 tab 同 session）

**Last-writer-wins，整份快照為單位**：兩個併發 turn 各自從同一基準 generation 出發、各自 persist 出新 generation，下一次 prepare 取 timestamp 較大者——輸的一方整份靜默被蓋。這是明確接受的語意：

- 比現況（共享磁碟併發交錯寫入→混血損壞狀態）**更好**：讀方永遠拿到某一輪的完整一致快照。
- 不做 session 級鎖（跨 pod 分散式鎖複雜度不成比例；產品情境為單人操作自己的 session）。若日後要硬防，backend 對同 session 併發 `/chat` 回 409 是獨立於儲存路線的正交改動。

已知理論弱點：跨 pod 時鐘偏移可能讓較晚寫入者 timestamp 較小。同 session turn 間隔以秒計、k8s NTP 偏移遠小於 1 秒，實務可忽略。

## 保留清理

- `RetentionCleanupService`（uploads 過期、artifacts 過期）走 `FileStorage.delete()`，**零改動**。
- `WorkspaceRetentionService`：抽 `WorkspacePurger` 接縫——`LocalWorkspacePurger`（現行 FS walk＋symlink/traversal 防護，原樣搬移）與 `S3WorkspacePurger`（list ＋ batch delete `workspace/{user}/sessions/{session}/` 整條前綴，天然涵蓋所有 generations；純 key 前綴比對，無 symlink/traversal 面）。依 `erd.storage.type` 條件註冊。
- `dry-run` 語意兩種實作照舊生效。

## 設定面

### backend `application.properties`（新增）

```properties
erd.storage.type=${ERD_STORAGE_TYPE:local}
erd.storage.s3.endpoint=${ERD_STORAGE_S3_ENDPOINT:}
erd.storage.s3.region=${ERD_STORAGE_S3_REGION:us-east-1}
erd.storage.s3.bucket=${ERD_STORAGE_S3_BUCKET:erd-cowork}
erd.storage.s3.path-style-access=${ERD_STORAGE_S3_PATH_STYLE:true}
```

Credentials 只走 env（`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`，SDK default chain）。`application-local.properties`（gitignored）鏡射並指向 `http://localhost:9000`。

### deepagent `Settings`（`app/config.py` 新增欄位；one.properties 同名 key）

```
STORAGE_BACKEND=local            # local | s3
S3_ENDPOINT=
S3_REGION=us-east-1
S3_BUCKET=erd-cowork
S3_ACCESS_KEY=
S3_SECRET_KEY=
S3_WORKSPACE_PREFIX=workspace
```

boto3 client 以 Settings 值顯式建構（不依賴 boto3 自己的 env 探測，維持「one.properties 為 internal 單一設定來源」的既有原則）。

### docker compose

`docker-compose.infra.yml` 新增獨立 `minio` service（**不共用** Langfuse 的 `lf-minio`——該顆綁 `observability` profile 且 credentials/生命週期不同）：

- ports：`9000:9000`（S3 API）、`9001:9001`（console）
- 一次性 `mc` bootstrap container：`mc mb` 建 `erd-cowork` bucket（plain bucket，不主動開 versioning——與「永不覆寫」設計相容，也對齊 internal 行為）
- dev credentials 走 `.env`

## 錯誤處理總表

| 失敗點 | 行為 |
|---|---|
| backend S3 store/read/delete 失敗 | `SdkException` → `IOException`，走既有例外路徑 |
| deepagent 源檔下載失敗 | fail-loud，turn 起不來（同 prepare） |
| prepare pull 失敗 | fail-loud，request 500（照舊） |
| persist push 失敗 | retry 兩次（每次全新 key）→ 仍失敗發 ERROR event |
| 舊 generation 清理失敗 | log 後放行，保留清理兜底 |
| workspace 保留清理單 session 失敗 | log 後繼續下一個 session（照舊語意） |

## 測試策略

- **backend**：回收 `S3FileStorageTest`（mock S3Client：store spool/read/delete/例外包裝）、`StorageConditionalRegistrationTest`（type=local 只有 LocalDiskStorage、type=s3 只有 S3FileStorage）；`S3WorkspacePurger` 單元測試（mock client：前綴刪除、dry-run、單 session 失敗不擋批次）。
- **deepagent**：stub S3 client（沿用舊 `_S3Client` Protocol 注入手法）測 generation 模型——`_complete` 過濾、取最新、空 session、persist 排除 `.skills/`、`_complete` 最後寫、retry 換新 key、三次全失敗發 ERROR event、保留 2 代清理、per-turn scratch 隔離與清除、sources cache 命中跳過下載。
- **端到端**：本機 MinIO 手動驗證（上傳→分析→dashboard→迭代→保留清理 dry-run）。自動化測試不依賴 MinIO。

## 文件與週邊更新

- `docs/architecture.md`：儲存章節改為雙路線敘述＋generation 快照圖。
- `CLAUDE.md` 檔案段落：「PVC RWX 單一路線」敘述改為雙路線。
- `.env.example`：compose 的 MinIO credentials 與 `ERD_STORAGE_*` 說明。
- `docs/internal-implementation-guide.md`：internal 物件儲存接線章節（endpoint/credentials/bucket 規範、write-once 規範的落地方式）。

## 明確不做（YAGNI）

- DuckDB httpfs 直讀 S3（turn 邊界同步已定案）。
- Presigned URL 交棒。
- Session 級分散式鎖／併發 409。
- Generation 數量可設定化（常數 2）。
- S3 multipart 串流上傳優化（temp spool 已驗證夠用）。
- Bucket versioning 的任何依賴。
