# PVC 儲存改造 — 合併後待辦

來源：`docs/superpowers/plans/2026-08-01-pvc-storage-and-retention.md` 執行期間，八個 task review ＋ opus 全分支終審 ＋ 修正波複審累積的延後項。

**已在分支內修掉的不在此列**（path guard leaf 檢查、`UncheckedIOException` 中止整批、中介 symlink 逃逸、`scheduledCleanup()` 接線測試、artifact 刪除失敗仍清 key、CLOB 全表掃描、`workspace < uploads` 警告、文件／spec 矛盾、`CLAUDE.md` 過時指引）。

分級：**P1** 進 prod 前應處理／**P2** 有實質價值、可排程／**P3** 整潔性。

---

## P1

### 1. 每晚三次無界全表掃描 ＋ 每 session 一次 NFS stat

`RetentionCleanupService.scheduledCleanup()` 觸發三次 `findByUpdatedAtBefore`（uploads、workspace 各一，加上 `cleanup()` 自身一次），每次回傳未分頁的 `List<ChatSession>`，且**只會愈來愈長**——session 從不刪除，已清理過的 session 每晚重複命中。uploads 接著對每個 session 做一次 `findBySessionIdAndExpiredFalse`（N+1），`purgeStaleSessions` 對每個 session 做一次 `Files.isDirectory`，在 NFS/CephFS 上每次都是網路往返。

以 spec 的 4,000 sessions/年估算，第五年約每晚 2 萬次 stat 打在共享儲存上。同時違反專案規則「大量結果查詢 MUST 用 `Pageable`/`Page<T>`」。

修法擇一：查詢加窗（`updatedAt between cutoff.minus(window) and cutoff`），或加 `cleaned_at` 標記讓每個 session 只被處理一次。

### 2. 上傳檔刪除失敗仍標記 `expired` 並計數

`RetentionCleanupService.java:83-94`：`storage.delete()` 拋錯只 `log.warn`，接著照樣 `file.setExpired(true)` 並 `count++`。

artifact 那條路徑在修正波已改為「刪除成功才清 key」，所以失敗的列下一晚會重試。uploads 沒有——檔案永久留在 volume 上、且不會再被任何清理處理（`storageKey` 仍在列上所以可被人工找到，但無自動回收）。兩條路徑現在語意不一致。

### 3. `WorkspaceRetentionService` 無法解析 workspace root 時整趟靜默跳過

`WorkspaceRetentionService.java:38-46`：`workspaceRoot.toRealPath()` 的任何 `IOException` 都記 **INFO** 然後 `return 0`。

ENOENT（全新部署尚未建目錄）記 INFO 合理；但 `EACCES`／`EIO`（volume 掛上了卻壞掉）正是「清理停擺而沒人發現」的形狀，應為 WARN。

同一段的 `Paths.get(properties.workspaceDir())` 在設定未給時會 NPE（`application.yml` 目前有預設值，故非活躍風險）。

---

## P2

### 4. `RetentionCleanupService` 三類清理職責不對稱

兩類（uploads、artifacts）內聯在服務裡，第三類（workspace）委派給 `WorkspaceRetentionService`。`cleanup(Instant)` 應更名為 `cleanupUploads(Instant)` 以對齊 `cleanupArtifacts(Instant)`。

自然的終局是三個 sibling `*RetentionService`，`RetentionCleanupService` 收斂為排程與彙總 log。**建議等第四類資料出現時再拆**，現在拆是為重構而重構。

### 5. `clearHtmlStorageKey` 回傳 `void`

`ArtifactRepository.java:33`：0-row update（列被並行刪除）與成功無法區分，仍會累加 purge 計數。改回傳 `int` 可讓 log 誠實。

### 6. 啟動 log 的 `inDays()` 會截斷

`RetentionCleanupService.java:62-63` 用 `Duration.toDays()`。`Retention` 綁定任意 `Duration`，所以 `ERD_STORAGE_RETENTION_UPLOADS=12h` 會印成 `uploads=0d`。

這行 log 的目的正是給 operator 一個可讀的事實來源，sub-day 設定下印出的值是錯的。（WARN 比較用的是 `Duration` 本身，不受影響。）

### 7. 「舊的扁平 key 仍可 resolve」無測試涵蓋

這是這次改造唯一明確宣稱的向後相容性質（spec §7.3、`architecture.md`），由 `LocalDiskStorage.resolve()` 的結構保證，但沒有測試釘住。兩行即可。

### 8. workspace 刪除可能與進行中的 turn 競態

若 03:00 的查詢在 `prepare()` touch 之前的微秒內把 session 快照為 stale，目錄會在 turn 進行中被刪除。後果溫和（`prepare_local_layout` 會重建骨架，session 表現得像全新的），且需要 180 天閒置的 session 在同一秒被喚醒。

但與上傳檔不同，這裡**沒有 `FilesExpiredException` 的等價物**告訴使用者為什麼 dashboard 重置了。至少在 `architecture.md` 記一行。

### 9. `@Modifying` 未加 `clearAutomatically`/`flushAutomatically`

`ArtifactRepository.java:30`。目前無害（唯一呼叫端非交易性、`open-in-view: false`）。若未來有交易性的管理路徑呼叫 `cleanupArtifacts`，該交易的 persistence context 裡若已有該 `Artifact`，會把陳舊的 key flush 回去。

### 10. `FileService.upload()` 的 touch 發生在 `validate()` 之前

被拒絕的上傳（超大、不支援格式、超過檔數上限）仍會刷新 `updatedAt`。只會失敗的 client 可以無限延長自己的 session 壽命。

「嘗試也算活動」是可辯護的語意，但若不是刻意的，把兩行移到 `validate()` 之後即可。

---

## P3

### 11. 文件與註解

- `FileStorage.java:8-9` 介面 Javadoc 未提及新的 `category` 參數
- `architecture.md:544` Workspace 生命週期表殘留單欄標頭「Local（唯一實作…）」，原為 Local/S3 雙欄對照收合而成，現在沒有對照對象，改成條列較自然
- spec §3:144 與 §7.3:303 仍以現在式敘述扁平 key；在 §7.3 加 `已完成` 標記即可移除最後一種把 spec 讀成描述現況的方式
- `app/engine/workspace.py` 的 `WorkspaceStore` Protocol 現只剩一個實作、且無測試建立 fake/stub，測試接縫的意圖未被行使——在 docstring 註明
- `AgentOrchestrator` 的 touch 註解 3 行，house style 為 1-2 行

### 12. 測試衛生

- `WorkspaceRetentionServiceTest` 植入 id 為 `".."` 的 `ChatSession` 列到 JVM 共享的 H2（`DB_CLOSE_DELAY=-1`）且從未移除（`resetDb()` 僅 `@BeforeEach`），後續測試類別看得到
- `RetentionCleanupDryRunTest` 缺 `@BeforeEach` DB reset 與 `@AfterAll` 檔案清理，與 sibling 測試類別不一致
- `WorkspaceRetentionServiceTest:784` 的 `Files.deleteIfExists(userDirLink)` 若前次崩潰留下真目錄會拋 `DirectoryNotEmptyException`
- `WorkspaceRetentionServiceTest` 的 `WORKSPACE_ROOT` 常數與 `@TestPropertySource` 手動同步，改一處會靜默失聯——改為注入 `StorageProperties` 推導
- `StorageConditionalRegistrationTest` 刪除後，「`FileStorage` bean 有註冊」無直接斷言（無條件 `@Component` 是 Spring 自身保證，價值低）

### 13. 其他

- `loadOrCreateOwned` 剛建立的 session 會在 `FileService.upload()` 多一次冗餘 UPDATE
- `AppConfigController:197` 的 `(int)` 窄化 `Duration.toDays()` 無邊界檢查，可考慮 `Math.toIntExact`
- artifact projection query 仍為未分頁 `List`，`Pageable` 規則技術上未滿足（heap 缺陷已解——每列從 CLOB 降為 id+key，約 1000× 縮減）
- 孤兒父目錄永不清理（最後一個 session 被清後，`{root}/{userId}/sessions` 與 `{root}/{userId}` 仍留存）
- commit `11c6071` scope 標為 `fix(deepagent):` 但改動全在 backend
- **既有問題（非本分支引入）**：`StorageKeyUtils` 的 private 建構子未拋 `UnsupportedOperationException`，違反專案的 `*Utils` 規則

---

## 刻意不做

**`ERD_STORAGE_CLEANUP_DRY_RUN` 預設維持 `false`。** 首次執行會刪除 0 筆 artifact（本專案沒有 730 天前的資料），而 uploads 從 30d 放寬到 180d 是刪得**更少**——新規則在任何情況下都不會比現行行為刪更多。改預設為 `true` 反而換來一個活的風險：清理靜默不執行、磁碟長滿而沒人發現。首次上線的 dry-run 一輪已列為部署程序必做步驟（`.env.example` 與 `architecture.md`）。

**備份策略仍為待訂**（spec §5），取決於平台的 PVC 備份能力。已確立的輸入：三類資料中只有 artifact 不可重建（約 120 GB，佔總量約 6%），且 artifact 為 append-only 使得備份不一致的後果被既有的 404 處理吸收。
