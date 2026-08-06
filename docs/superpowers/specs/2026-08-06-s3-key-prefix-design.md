# S3 key prefix 設計（共用 bucket 的子路徑接縫）

**日期**：2026-08-06
**狀態**：已與使用者確認設計方向
**背景**：internal 物件儲存的 bucket 是與其他團隊共用的 `rdp`，治理上要求 erd-cowork 的所有物件放在 `rdp/erd-cowork/` 子路徑下。現行 code 把 S3 key 寫在 bucket 根（`uploads/`、`artifacts/`、`workspace/`），無「共同前綴」設定。bucket 名不能含斜線（`rdp/erd-cowork` 非法），故需要一個 key prefix 設定。

## 目標與約束

- 新增 S3 key prefix 設定，非空時所有 S3 物件 key 前補 `{prefix}/`。
- **家裡（GitHub）開接縫、預設空字串**——家裡/compose 行為零變化（key 仍落 bucket 根）；internal 同步後設 `erd-cowork`。符合「接縫在家裡開、internal 只填值」的單向搬運紀律。
- **prefix 只活在 S3 client 邊界**：DB 的 storageKey、backend↔deepagent 交棒傳的 key，一律維持乾淨（不含 prefix）。local 模式與既有資料零影響、免遷移。
- backend 與 deepagent 兩側 prefix **必須同值**（跨 service 一致性要求，比照 workspace prefix）。

## 核心設計：邊界套用，key 本體乾淨

prefix 只在「打 S3 那一刻」補上；邏輯 key 全程不含 prefix。

- **DB 存的 storageKey 不含 prefix**（`uploads/...`）→ local 模式不憑空多資料夾；已存在資料不用遷移。
- **backend 傳給 deepagent 的仍是 `uploads/...`** → deepagent source_cache 下載時自己補 prefix；兩邊各在自己的 S3 邊界補一次，不重複。
- prefix 是「這顆 bucket 上的定址細節」，非資料模型的一部分。

## 一致性拓撲

| 資料 | 寫入方 | 讀取/清理方 | 兩側都要補 prefix |
|---|---|---|---|
| uploads | backend `S3FileStorage.store` | deepagent `source_cache` 下載 | ✓ |
| artifacts | backend `S3FileStorage`（write+read+delete） | backend 自己 | ✓（自洽） |
| workspace | deepagent `S3WorkspaceStore` | backend `S3WorkspacePurger` | ✓ |

uploads 與 workspace 是跨 service 配對——prefix 設不同值會導致讀取撲空／清理撲空。故兩側 MUST 同值，寫入 docs 與設定範本。

## 設定面

### backend `application.properties`（新增一行）

```properties
# S3 物件 key 共同前綴(internal 共用 bucket 的子路徑)。空=落 bucket 根。設值時 deepagent S3_KEY_PREFIX MUST 同值
erd.storage.s3.key-prefix=${ERD_STORAGE_S3_KEY_PREFIX:}
```

`StorageProperties.S3` 加 `keyPrefix` 欄位 → `S3(endpoint, bucket, accessKey, secretKey, keyPrefix)`。

### deepagent `Settings`（新增欄位；one.properties 同名 key）

```
S3_KEY_PREFIX=            # S3 物件 key 共同前綴;空=落 bucket 根;MUST 與 backend erd.storage.s3.key-prefix 同值
```

## 觸及點與 prefix 組法

統一規則：`joinKey(prefix, key)` = prefix 空 → `key`；否則 → `{prefix 去頭尾斜線}/{key}`。

- **backend `S3FileStorage`**：`store`（putObject 用 prefixed key，回傳仍是邏輯 key）、`read`、`delete` 三處在打 S3 前 `joinKey`。
- **backend `S3WorkspacePurger`**：list/delete 的前綴由 `workspace/{userId}/sessions/{sessionId}/` 改為 `joinKey(keyPrefix, "workspace/...")`。
- **deepagent `S3WorkspaceStore`**：建構時 `build_workspace_store` 把 `S3_KEY_PREFIX` 與現有 `WORKSPACE_PREFIX` 組成完整前綴（`{keyPrefix}/workspace`）傳入；store 內既有的 prefix 正規化不變。
- **deepagent `source_cache`**：`resolve_source_path` 下載時 S3 key = `joinKey(keyPrefix, raw_path)`；本地 cache 路徑維持 `raw_path`（本地細節，不受影響）。

## 測試策略

- **backend**：`S3FileStorageTest` 補 prefix 非空時 putObject/getObject/delete 用 prefixed key、回傳仍為邏輯 key；prefix 空時行為不變。`S3WorkspacePurgerTest` 補 prefix 反映在 list/delete 前綴。
- **deepagent**：`test_workspace_s3` 補 prefix 非空時 generation key 帶前綴；`test_source_cache` 補下載 key 帶前綴、cache 本地路徑不變。
- 所有既有測試（prefix 預設空）維持綠——證明零行為變化。

## 落地路徑

家裡 master 開接縫（預設空），PR 合併；同步到 internal 後，internal 在 `application.properties`（雙邊擁有檔，調和時併入）設 `erd.storage.s3.key-prefix=erd-cowork`、掛載的 `one.properties` 設 `S3_KEY_PREFIX=erd-cowork`、bucket 設 `rdp`。

## 明確不做（YAGNI）

- 不做「backend 與 deepagent prefix 不同值」的容錯或自動偵測——文件要求同值，設錯是設定錯誤。
- 不遷移既有 bucket 根資料（家裡/compose 本就在根，internal 是全新 bucket）。
- prefix 不進 DB storageKey、不進交棒 payload。
