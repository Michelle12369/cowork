# 儲存改用 PVC RWX 與分級保留設計

日期：2026-08-01

## 1. 決策摘要

| 項目 | 決定 |
|---|---|
| 儲存後端 | **PVC RWX 單一路線**，S3/MinIO 全線移除 |
| PVC 規格 | `/data/files` 2 TB、`/data/workspace` 200 GB，皆 RWX |
| 保留策略 | artifact 2 年；workspace 與上傳原始檔依 session **最後活動時間** 半年窗 |
| 備份 | **待訂**（§5）——已確立只有 artifact 不可重建、約 120 GB，機制選型待平台能力確認 |
| 前置修正 | `ChatSession.updatedAt` 目前不隨對話更新，是現存 bug，必須先修 |

前提變更：原設計選 S3 的唯一理由是「公司 k8s 無 RWX PV」（`workspace_s3.py` 檔頭、`architecture.md` workspace 生命週期表）。該前提已確認不成立——平台可提供 RWX PVC。

不在此範圍：`docker-compose.yml` 的 `lf-minio` 是 Langfuse self-host 官方 topology 的一部分（`observability` profile），與本決策無關，**不得移除**。

## 2. 為什麼 PVC 而不是 MinIO

### 2.1 判準

物件儲存與共享檔案系統的取捨，取決於三個可量測的維度，而非架構偏好：

| 判準 | 傾向物件儲存 | 傾向檔案系統 |
|---|---|---|
| **讀取扇出** | 大量無狀態 reader 同時併發拉取 | 單一 request 讀少量檔案 |
| **物件數量級** | 10⁶ 以上小物件 | 10⁵ 以下 |
| **容量上界** | 無上界、不可預測 | 有界、可估算 |

### 2.2 對照組：Loki 為什麼需要物件儲存

- 一次 LogQL 查詢 fan-out 到數百個 querier，每個併發拉數千個 chunk。單一 NFS server 是 throughput 與 IOPS 的單點瓶頸；物件儲存背後是分散式叢集，對數百併發 GET 無感
- chunk 約 1.5 MB，忙碌叢集產出 10⁶–10⁹ 個。檔案系統 namespace 到這個量級，inode 與目錄 metadata 會崩潰
- 日誌 append-forever，無法預先 size 一顆 PVC
- Loki 自身不保證耐久性，完全委外給 object store

但 **Loki 官方同時提供 `filesystem` backend**，建議用於 single-binary 與小規模部署。連 Loki 的答案都是「看規模」，不是「物件儲存在原理上較優」。

### 2.3 本專案的量測

| 判準 | 本專案 | 判定 |
|---|---|---|
| 讀取扇出 | 一個 request 讀 1–5 個檔，DuckDB 順序掃描 | 檔案系統 |
| 物件數量級 | 每 session 個位數至數十檔；8,000 sessions 約 2.4 × 10⁵ 檔 | 檔案系統 |
| 容量上界 | 5 GB/session 硬上限 × 可估算的 session 產生率 | 檔案系統 |

且完全用不到物件儲存的專長：無 presigned URL 直連瀏覽器、無 CDN、無跨 region、不靠 S3 versioning 管版本（artifact 版本鏈在 DB 自管）、不靠 lifecycle policy 過期（已有 cron retention）。所有流量都經過後端行程。

### 2.4 叢集內 MinIO 不會變出磁碟

MinIO 自己也跑在 PVC 上，與 app 共用同一池 block storage，並額外付 erasure coding 的 1.5–2× 冗餘 overhead。「改用 MinIO」在容量上不僅沒有優勢，放大係數還更差。

只有當儲存是**外部託管的 object storage**（獨立容量池、冷熱分層計價）時，容量彈性才是真優勢。本專案不採此路線。

### 2.5 S3 路線在此 workload 上更慢

`S3FileStorage.store()` 因為 `putObject` 需要已知 content-length，把 `InputStream` spool 到 temp file 後才上傳。2 GB CSV 等於「寫本地 temp 2 GB → 再傳 2 GB」。`LocalDiskStorage` 是串流一次落地。**PVC 路徑比 S3 路徑少一份完整的磁碟 IO。**

### 2.6 刪乾淨的理由：讓一個未解缺陷真正消失

`S3WorkspaceStore` 的 lazy pull / turn-end push 帶著一個已知缺陷：`persist()` 失敗只 `log.warn`，此時最新 workspace 只在當前 pod 的本地 cache，下一輪若落到另一個 pod 會 lazy pull 到舊版，模型基於過期狀態開工（症狀：上一輪的 dashboard 修改消失、`qN` 編號空間回退、dashboard 引用的結果檔缺漏）。

原架構文件記載的緩解方案是 session affinity ＋ workspace 版本戳記，並註明「上 prod 多副本前 MUST 至少落地」。

RWX 下 workspace 就是單一 source of truth，沒有 pull/push、沒有 cache、沒有版本落後——**該缺陷與其兩項前置工程一併消失**。

這是保留 S3 實作為「退路」的真正成本：只要那條路線還活著，就不能宣告該缺陷不存在，那兩項工程就還在排程上。刪除的價值不在省下 271 行的維護，在於能誠實地關閉這個問題。

### 2.7 可逆性

`FileStorage` 是 14 行、3 個方法的介面；`WorkspaceStore` 是一個 Protocol。**介面保留、實作刪除、git history 仍在**。若未來環境改變，重新加回是貼回 271 行的工作，不是重新設計架構。正因代價低，應依現在已知的事實決定，不為假設性未來付當下的維護稅。

## 3. 容量估算方法

### 3.1 模型

保留期依資料類不同，因此**不能用「兩年累加」估算**，而是分別以各自的窗計算穩態值：

```
總容量 = artifact(2 年窗，全量累積)
       + workspace(半年活躍窗)
       + 上傳原始檔(半年活躍窗)
```

半年活躍窗內的 session 數 ≈ 半年新建量 ＋ 舊 session 回訪量。

### 3.2 每 session 佔用（程式碼實證）

| 成分 | 大小 | 依據 |
|---|---|---|
| 上傳原始檔 | ≤5 GB | 5 檔/session 共 5 GB 上限；xlsx 單檔 ≤200 MB，僅 CSV 可達 2 GB |
| `results/{qN}.json` | 每檔 ≤5000 列，約 ≤2–5 MB | `results.py` `STORE_MAX_ROWS = 5000`（硬上限） |
| `dashboard.html`（workspace） | ~100–500 KB | 模型產出 |
| `.skills` staging | 56 KB × 每 session 一份 | `deepagent-service/skills` 共 4 檔 |
| artifact HTML（每版） | ~1–5 MB | HTML 本體 30–150 KB（Tailwind/ECharts 走 vendored 外部載入，不內嵌）＋ 注入的 `__ERD_RESULTS__`（僅 answer 引用到的 `qN`） |

除上傳檔外，每項都有硬上限。workspace 合計約 15 MB/session。

**前提：僅 deepagent 線上 prod。** openai/dashboard 線的 `ArtifactAssembler.buildEntry()` 呼叫 `fileParsingService.readAll()` 取全量列注入 HTML，**無列數上限**。若該線上 prod 且 session 達 5 GB，單一 artifact 版本會膨脹到 7.5–15 GB（CSV→JSON 約 1.5–3× 膨脹），且 serve 一份該尺寸的 HTML 給瀏覽器本就不可行。此為獨立於儲存選型的設計問題（換 S3 同樣成立），列為條件式風險。

### 3.3 參數與敏感度

基準參數：200 人 × 20 session/年 ＝ **4,000 sessions/年**。

| 成分 | 計算 | 值 |
|---|---|---|
| artifact | 8,000 sessions × 5 版 × 3 MB | **120 GB**（悲觀 10 版 × 5 MB → 400 GB） |
| workspace | 2,400 sessions × 15 MB | **36 GB** |
| 上傳原始檔 | 2,400 sessions × 平均上傳量 | 見下表 |

半年窗 session 數 2,400 ＝ 半年新建 2,000 ＋ 回訪估 400。

| 平均上傳/session | 原始檔 | 總計 | PVC（＋40% headroom） |
|---|---|---|---|
| 100 MB | 240 GB | 0.4 TB | 0.6 TB |
| 300 MB | 720 GB | 0.9 TB | 1.3 TB |
| **500 MB** | 1.2 TB | **1.4 TB** | **2 TB** |
| 1 GB | 2.4 TB | 2.6 TB | 3.6 TB |
| 2 GB | 4.8 TB | 5.0 TB | 7 TB |

**平均上傳量是唯一沒有實測依據的參數**，也是唯一的主導變數。

### 3.4 PVC 規格

| PVC | 大小 | 存取模式 | 掛載 | 存放內容 |
|---|---|---|---|---|
| `/data/files` | **2 TB** | RWX | backend `rw`、deepagent-service `ro` | 上傳原始檔 ＋ artifact HTML 版本鏈 |
| `/data/workspace` | **200 GB** | RWX | deepagent-service `rw`、backend `rw`（**新增**，供清理用） | agent 每輪的工作目錄 ＋ user skills |

**`/data/files`** —— 寫入端**只有 Java**（`FileService`、`AgentConversationWriter`、`ArtifactRepairService`）；deepagent-service 唯讀，且路徑不由它自己組，是 Java 經 request body 的 `sources[].path` 傳入。

```
/data/files/
├── uploads/{sessionId}/{uuid}_{filename}        ← 上傳原始檔（CSV/Excel）
│                                                   FileService 串流落地；deepagent 唯讀當 DuckDB 資料源
│                                                   保留：session 最後活動半年窗
└── artifacts/{sessionId}/{uuid}_{artifactId}.html  ← artifact HTML 版本鏈（append-only）
                                                    AgentConversationWriter 寫入（與 AI 訊息同交易）
                                                    ArtifactRepairService 於瀏覽器錯誤修復時覆寫
                                                    保留：2 年　備份：必要（機制待訂，§5）
```

`uploads/`／`artifacts/` 前綴為**目標結構**；現況是扁平的 `{sessionId}/{UUID}_{name}`、兩類混在同一目錄，改造見 §7.3。

**`/data/workspace`** —— 寫入端只有 deepagent-service；backend 新增 `rw` 掛載僅為執行清理（需要 session 的 `updatedAt`，而該資料在 backend DB）。

```
/data/workspace/{userId}/
├── sessions/{sessionId}/
│   ├── queries/            ← 模型寫的 SQL
│   ├── results/{qN}.json   ← 查詢結果，每檔 ≤5000 列（STORE_MAX_ROWS）
│   ├── dashboard.html      ← 工作副本，下一輪模型 edit_file 的對象
│   ├── sources.md          ← 資料源 alias 對照
│   └── .skills/            ← 每輪 stage_skills() 清空重建的暫存（builtin ＋ user，56 KB）
└── skills/                 ← 該 user 的自訂 skills
```

保留：session 最後活動半年窗。備份：選配（§5 待訂）。

⚠️ **`dashboard.html` 在兩顆 PVC 上各有一份，角色不同**：workspace 那份是模型下一輪繼續編輯的可變工作副本，`/data/files` 那份是不可變的版本鏈成員。**這正是分級保留能成立的原因**——半年後清掉 workspace，已獨立存在的 artifact 不受影響。§3.3 的容量計算已分別計入（workspace 100–500 KB、artifact 每版 1–5 MB），無重複計算。

2 TB 的依據：涵蓋平均 700 MB/session。而 xlsx 硬上限 200 MB、僅 CSV 可達 2 GB，2 GB CSV 是離群值非常態，實務平均預期落在 100–300 MB，故 2 TB 有 3–7 倍餘裕。

workspace 拆成獨立小 PVC 是刻意的：**容量耗盡的後果不對稱**——應該讓失敗發生在「新上傳被拒」，而非「artifact 寫不進去導致整輪分析白做」。小池隔離成本低。

### 3.5 配套（比初始數字更重要）

1. **CSI MUST 支援線上擴容**——這比一開始開多大重要
2. **70% 用量告警**，並按 `uploads/` 與 `artifacts/` 前綴分別監控
3. **上線 1–2 個月後以實測平均值重算**，再決定是否擴容

### 3.6 重新估算的觸發條件

任一條成立即重跑 3.3 的算式：使用者數或 session 產生率變動 >50%、實測平均上傳量偏離 500 MB 假設 >2×、openai/dashboard 線決定上 prod、artifact 版本鏈平均長度 >10。

## 4. 分級保留策略

### 4.1 政策

| 資料類 | 保留條件 | 現況 |
|---|---|---|
| artifact HTML | 建立後 **2 年** | **目前無任何清理，等於永久保留**——需新增 |
| workspace | session 最後活動 **半年**內 | **目前無任何清理，只長不消**——需新增 |
| 上傳原始檔 | session 最後活動 **半年**內 | 已有機制，`retentionDays` 30 → 180 |

`RetentionCleanupService.cleanup()` 使用 `sessionRepo.findByUpdatedAtBefore(cutoff)`——**依 session 最後活動時間判定，非檔案上傳時間**。語意已正確，惟該欄位目前未被正確維護（見 §6）。

### 4.2 自洽性

此政策成立的關鍵：**deepagent 線的 artifact 是 self-contained**。`__ERD_RESULTS__` 在生成時就已注入 HTML，`ArtifactAssembler` 對其 `includeData=false`、完全不讀原始檔。

因此**半年後清掉原始檔，兩年內打開 artifact 仍可正常檢視**。既有的 `expired = true` 語意（刪實體檔、DB 列保留、UI 仍可見檔案存在過）正是為此設計。

### 4.3 產品語意

- **半年未活動的 session 變成唯讀存檔**：可檢視 artifact，但不能續問（DuckDB 無資料源可載）。既有的 `FilesExpiredException` guard 已覆蓋此路徑
- **「瀏覽」不算使用**：GET 不得改變狀態（專案規則），故活動定義為「對話 or 上傳」。一個半年只被瀏覽、未被追問的 session，原始檔會被清除

### 4.4 設定介面（環境變數）

現況：`retention-days: 30` 在 `application.yml` 中**寫死、無 env placeholder**；`cleanup-cron` 甚至不在 yml 裡，只存在於 `@Scheduled(cron = "${erd.storage.cleanup-cron:0 0 3 * * *}")` 的註解預設值。兩者皆無法以環境變數調整。

改為（沿用本專案慣例：顯式 `${ENV_VAR:default}`，不依賴 relaxed binding）：

```yaml
erd:
  storage:
    cleanup:
      cron: ${ERD_STORAGE_CLEANUP_CRON:0 0 3 * * *}    # "-" 停用整個排程
      dry-run: ${ERD_STORAGE_CLEANUP_DRY_RUN:false}    # true：只記錄將刪除什麼，不實際刪除
    retention:
      uploads:   ${ERD_STORAGE_RETENTION_UPLOADS:180d}
      workspace: ${ERD_STORAGE_RETENTION_WORKSPACE:180d}
      artifact:  ${ERD_STORAGE_RETENTION_ARTIFACT:730d}
```

| 環境變數 | 預設 | 說明 |
|---|---|---|
| `ERD_STORAGE_CLEANUP_CRON` | `0 0 3 * * *` | 每日 03:00。設為 `-` 即停用排程（Spring 的 `Scheduled.CRON_DISABLED`），不需另設 enabled 旗標 |
| `ERD_STORAGE_CLEANUP_DRY_RUN` | `false` | 首次上線建議先開 `true` 跑一輪，確認刪除清單符合預期再關閉 |
| `ERD_STORAGE_RETENTION_UPLOADS` | `180d` | 上傳原始檔；依 session 最後活動時間 |
| `ERD_STORAGE_RETENTION_WORKSPACE` | `180d` | deepagent workspace；同上 |
| `ERD_STORAGE_RETENTION_ARTIFACT` | `730d` | artifact HTML；依 artifact 建立時間，非 session 活動 |

設計決定：

- **型別用 `Duration` 而非 int days**（`180d`／`730d`），自我說明且免去 `-days` 後綴。`RetentionCleanupService` 現行的 `Duration.ofDays(properties.retentionDays())` 可直接改為使用綁定值
- **單一 cron 掃三類**，而非三個排程。三者 cutoff 不同但都是廉價查詢，拆開只增加運維面
- **`dry-run` 是刻意保留的**：artifact 是本設計中唯一標記為「不可重建」的資料，而清理它是全新的刪除路徑。首次對兩年前資料執行前能先看清單，是廉價的保險
- 既有的 `erd.storage.retention-days` **移除**，不保留為別名（v1 前無相容包袱）。`FilesExpiredException` 目前顯示的保留天數改用 `retention.uploads`

`StorageProperties` 對應調整為巢狀 record（`Cleanup`／`Retention`），符合專案「config binding 一律 `@ConfigurationProperties`」規則。

### 4.5 清理實作要點

- 清理任務按 **storage key 前綴**（`uploads/` vs `artifacts/`）分別掃描，不同 cutoff——這是 §7.3 key 前綴改造的直接動機
- workspace 清理需要 session 的 `updatedAt`（在 backend DB），而檔案在 deepagent 的 PVC 上。**RWX 讓 backend 直接掛載 workspace 自行清理，單一 `RetentionCleanupService` 涵蓋兩邊**；S3 方案下需跨服務開 cleanup API
- 沿用既有的逐檔獨立小交易（單檔失敗不影響其他），storage 刪除失敗僅 `log.warn`

## 5. 備份策略（待訂）

**未定案**，取決於平台能提供什麼備份能力——這是本 spec 唯一懸而未決的部分，須在落地前補齊（§9 #3）。

已確立的輸入（不因平台能力而改變）：

| 資料類 | 量 | 可重建？ | 備份需求 |
|---|---|---|---|
| **artifact HTML** | ~120 GB | **不可能**——模型有不確定性，同樣的 prompt 產不出同一份 dashboard | 必要 |
| workspace | ~36 GB | 部分可從 artifact 反推 | 選配 |
| 上傳原始檔 | ~1–2 TB | 可以——原檔在使用者本機 | 不需要 |

**真正需要備份的只有約 6% 的資料量**，備份規模是每天 120 GB 而非 2 TB。

待訂的內容：機制選型（儲存陣列既有備份／CSI VolumeSnapshot／Velero／自建匯出）、RPO 與 RTO、上傳原始檔是否真的完全不備份、以及平台若完全無備份能力時是否改採混合方案（僅 `artifacts/` 放外部物件儲存）。

決定時需納入的兩項既有事實：

- **RWX（NFS/CephFS）的 VolumeSnapshot 支援度遠低於 RWO block storage**，許多 NFS provisioner 不提供 `VolumeSnapshotClass`。須向平台具體確認，不可假設
- **artifact 是 append-only，備份不一致的後果已被既有設計吸收**——「檔案比 DB 新」只產生可清理的孤兒檔，反向的 dangling reference 由 `ArtifactService.getHtml()` 回 404 處理。因此後果是「少數 artifact 無法檢視」而非資料損毀，還原順序訂為「先檔案、後 DB」即可，對備份精確度的要求不高

## 6. 前置修正：`updatedAt` 不隨活動更新

### 6.1 問題

`ChatSession` row 全專案只有兩個寫入點：

| 位置 | 時機 |
|---|---|
| `SessionGuard.java:112` | session 建立時 |
| `AgentOrchestrator.java:167-168` | **第一則** USER 訊息時設 title |

第二輪起 `hasUserMessage == true`，不進 if、不 save。`loadOrCreateOwnedAs()` 只做 load 或 create，不修改既有 row。無 rename endpoint。

加上 JPA 機制：`@LastModifiedDate` 靠 `AuditingEntityListener` 的 `@PreUpdate` 回呼，而 `@PreUpdate` 僅在 Hibernate 髒檢查判定該 entity 有欄位變更、真的發出 UPDATE 時才觸發。

**結論：`ChatSession.updatedAt` 實質等同 `createdAt`。**

### 6.2 這是現存 bug，非僅未來風險

現行 30 天 retention 的實際語意是「**建立後 30 天**」而非「閒置 30 天」。連續使用三個月的 session，第 31 天原始檔即被清除，下一輪對話撞上 `prepare()` 的 `FilesExpiredException` guard——使用者體感為「用得好好的 session 突然不能用」。

dev 階段未暴露，僅因尚無 session 存活超過 30 天。**改為 180 天只是把引信拉長六倍，並讓問題更難察覺。**

### 6.3 修法

在 `AgentOrchestrator.prepare()` 中無條件 touch session；檔案上傳路徑同樣 touch（已上傳但尚未提問的 session 亦應視為活躍）。

實作要點：**必須實際變更欄位才會 dirty**。對未變更的 entity 呼叫 `save()`，Hibernate 判定無變更 → 不發 UPDATE → `@PreUpdate` 不觸發 → auditing 不執行。因此不能只是把 `save()` 移出 if，需實際 set 一個值使其變髒（auditing 隨後會以自身的 `now()` 覆寫）。

成本為每輪一次 UPDATE，相對整輪數十秒的 LLM 呼叫可忽略。

**替代方案（未採用）**：retention 改查「該 session 最新一則 message 的 `createdAt`」，零寫入放大，但會留下一個名為 `updatedAt` 卻實為建立時間的欄位，後續維護者將重蹈覆轍。

## 7. 變更範圍

### 7.1 移除

| 標的 | 位置 |
|---|---|
| `S3FileStorage` | `backend/.../storage/S3FileStorage.java`（83 行） |
| `S3StorageConfig` | `backend/.../config/S3StorageConfig.java`（37 行） |
| `S3WorkspaceStore` ＋ `_build_s3_client` | `deepagent-service/app/engine/workspace_s3.py`（151 行） |
| DuckDB S3 路徑 | `duck.py` 的 `_s3_config()`、`has_s3_source` 分支、`INSTALL httpfs; LOAD httpfs;` |
| `resolveSourcePath` 的 s3 分支 | `LangGraphAnalysisProvider.java:205-210` |
| compose `minio` / `minio-init` 服務與 `minio-data` volume | `docker-compose.yml`（**保留 `lf-minio`**） |
| 環境變數 | `ERD_STORAGE_TYPE`、`ERD_STORAGE_S3_*`、`AWS_ACCESS_KEY_ID/SECRET`、`AGENT_WORKSPACE_BACKEND`、`AGENT_WORKSPACE_S3_*`、`AGENT_S3_*` |
| 對應測試 | `S3FileStorageTest`、`StorageConditionalRegistrationTest`、`workspace_s3` 相關測試 |

`FileStorage` 介面與 `WorkspaceStore` Protocol **保留**（測試接縫），但只剩單一實作；`@ConditionalOnProperty` 與 `build_workspace_store()` 的分支一併移除。

### 7.2 新增

- artifact 2 年清理（目前完全不存在）
- workspace 清理（目前完全不存在，屬實際的磁碟洩漏）
- 按 storage key 前綴分別統計用量的監控端點或指標

### 7.3 修改

- **storage key 加類型前綴**：`StorageKeyUtils.buildKey()` 目前產出 `{sessionId}/{UUID}_{name}`，上傳檔與 artifact HTML 共用同一扁平 key 空間、混在同一 session 目錄。改為 `uploads/{sessionId}/...` 與 `artifacts/{sessionId}/...`
  - 分級保留的**硬前置條件**（無前綴就無法對兩類施加不同 cutoff）；未來若採分級備份亦以此為前提
  - 對既有資料為 breaking change，需 migration 或雙讀。**現階段資料量最小，是成本最低的時機**
- `StorageProperties` 拆出巢狀 `Cleanup`／`Retention` record，三類保留期與 cron／dry-run 全數改為環境變數（現況兩者皆寫死，見 §4.4）
- §6 的 `updatedAt` 修正

## 8. 對未來 API data source artifact 的預留

已知後續會有「artifact 接 API data source、瀏覽時注入即時資料」的需求（另立 spec）。已確認的兩個硬約束記錄於此，避免當下把路走死：

1. **iframe 沙箱決定資料只能從外部推入**。`ArtifactPanel.tsx:242` 為 `sandbox="allow-scripts"`、無 `allow-same-origin` → opaque origin，其 fetch 帶 `Origin: null`，無法呼叫自家 API。故只能由後端 serve 時代抓後注入，或由 parent 代抓後經 postMessage 推入
2. **LLM insight 與即時資料語意上不相容**。新資料配舊洞察會產生自我矛盾且靜默的誤導（文字稱成長、圖表顯示衰退）。傾向拆成 snapshot／live 兩種 artifact 型別，live 型別不含 LLM 文字

對本 spec 的預留：

- storage key 前綴需留得下未來的型別細分（`artifacts/snapshot/`、`artifacts/live/`）
- retention 實作寫成「按資料類」的可擴充形式，而非寫死兩條規則
- `ArtifactService.getHtml()` 的 serve 路徑（現為 `StreamingResponseBody` 逐行 CDN 改寫）即未來的注入點所在，此路徑須保持乾淨
- 「artifact 保留 2 年」對未來的 live 型別不成立——其內容依賴外部 API 兩年後仍存活且 schema 相容

## 9. 落地前必須驗證

| # | 事項 | 未通過的後果 |
|---|---|---|
| 1 | RWX provisioner 型別與 2 GB CSV 順序讀實測 latency | Azure Files (SMB) latency 明顯較差；NFS/CephFS 預期無虞 |
| 2 | CSI 是否支援線上擴容 | 比初始容量更重要 |
| 3 | 平台的 PVC 備份能力 | **§5 備份策略待此結果才能定案**——含機制選型、RPO/RTO，以及平台若無備份能力時是否改採混合方案 |
| 4 | 平台可提供的 RWX PVC 容量上限 | 若低於 2 TB 需重新規劃保留期 |
| 5 | openai/dashboard 線是否上 prod | 若是，`__ERD_DATA__` 全量注入須先解（§3.2） |

## 10. 非目標

- 不做冷熱分層或歸檔（YAGNI；容量估算顯示不需要）
- 不保留 S3 實作作為退路（理由見 §2.6、§2.7）
- 不動 Langfuse 的 `lf-minio`
- 不在此 spec 設計 API data source artifact（§8 僅記錄預留點）
- 不引入 session affinity 或 workspace 版本戳記（RWX 下這兩項的動機已消失）

## 11. 測試策略

- **儲存**：`LocalDiskStorage` 既有測試涵蓋 store/read/delete 與 path traversal；移除 S3 後補一則「key 前綴正確落在 `uploads/`／`artifacts/`」的測試
- **保留**：`RetentionCleanupService` 需覆蓋——artifact 滿 2 年被清、workspace 依 session 活動半年窗被清、上傳檔同上、單檔刪除失敗不影響其他檔
- **`updatedAt` 修正**：integration test 斷言「第二輪對話後 `updatedAt` 確實前進」——這正是現行實作會失敗的案例，需為此撰寫紅燈測試後再修
- **deepagent**：`build_workspace_store()` 移除分支後，確認 `AGENT_WORKSPACE_BACKEND` 不再被讀取；`duck.py` 確認不再有 `INSTALL httpfs` 路徑
- 依專案規則，合併前 `./mvnw test` 與前端測試須全綠
