# S3 key prefix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 S3 key prefix 設定（backend `erd.storage.s3.key-prefix` / deepagent `S3_KEY_PREFIX`，預設空），非空時所有 S3 物件 key 前補 `{prefix}/`，讓 internal 共用 bucket 能把物件放在 `erd-cowork/` 子路徑下。

**Architecture:** prefix 只套在 S3 client 邊界——DB storageKey 與 backend↔deepagent 交棒 payload 全程不含 prefix；local 模式與既有資料零影響。backend（S3FileStorage、S3WorkspacePurger）與 deepagent（S3WorkspaceStore、source_cache）兩側各在自己的 S3 邊界補 prefix，兩側 MUST 同值。

**Tech Stack:** Java 17 / Spring Boot / AWS SDK v2；Python 3.12 / boto3。

**Spec:** `docs/superpowers/specs/2026-08-06-s3-key-prefix-design.md`（權威需求）

## Global Constraints

- 預設空字串 → 家裡/compose 行為零變化（key 落 bucket 根）；所有既有測試維持綠。
- prefix 只在 S3 client 邊界套用；DB storageKey、交棒 payload 一律不含 prefix。
- prefix 組法：空 → key 原樣；非空 → `{prefix 去頭尾斜線}/{key}`。
- backend `erd.storage.s3.key-prefix` 與 deepagent `S3_KEY_PREFIX` MUST 同值（文件要求，不做容錯）。
- 命名分類法（CLAUDE.md）、engine 純度（ruff TID251）、測試命名 `方法_情境_預期`。

---

### Task 1: Backend S3 key prefix

**Files:**
- Modify: `backend/src/main/java/com/erd/cowork/config/StorageProperties.java`
- Modify: `backend/src/main/java/com/erd/cowork/storage/S3FileStorage.java`
- Modify: `backend/src/main/java/com/erd/cowork/storage/S3WorkspacePurger.java`
- Modify: `backend/src/main/resources/application.properties`
- Test: `backend/src/test/java/com/erd/cowork/storage/S3FileStorageTest.java`、`S3WorkspacePurgerTest.java`、`StorageConditionalRegistrationTest.java`（`new StorageProperties.S3(...)` 建構處補參數）

**Interfaces:**
- Produces: `StorageProperties.S3(String endpoint, String bucket, String accessKey, String secretKey, String keyPrefix)`。

- [ ] **Step 1: application.properties 加 key**

```properties
# S3 物件 key 共同前綴(internal 共用 bucket 的子路徑)。空=落 bucket 根;設值時 deepagent S3_KEY_PREFIX MUST 同值
erd.storage.s3.key-prefix=${ERD_STORAGE_S3_KEY_PREFIX:}
```

- [ ] **Step 2: StorageProperties.S3 加 keyPrefix 欄位**（record 尾端加 `String keyPrefix`）

- [ ] **Step 3: 寫失敗測試——S3FileStorage prefix 行為**

在 `S3FileStorageTest` 加（沿用既有 mock S3Client 手法，捕捉 `PutObjectRequest`/`GetObjectRequest`/`DeleteObjectRequest` 的 key 斷言）：

```java
@Test
void store_keyPrefixSet_putsUnderPrefixButReturnsLogicalKey() {
  // S3 properties: keyPrefix="erd-cowork"
  // store(UPLOAD, "sess-1", "a.csv", stream)
  // 斷言 putObject 的 key 以 "erd-cowork/uploads/sess-1/" 開頭
  // 斷言回傳值(邏輯 key)以 "uploads/sess-1/" 開頭、不含 "erd-cowork/"
}

@Test
void read_keyPrefixSet_getsFromPrefixedKey() {
  // read("uploads/sess-1/uuid_a.csv")；斷言 getObject 的 key = "erd-cowork/uploads/sess-1/uuid_a.csv"
}

@Test
void delete_keyPrefixSet_deletesPrefixedKey() {
  // delete("uploads/..")；斷言 deleteObject 的 key 帶前綴
}

@Test
void store_keyPrefixEmpty_usesKeyVerbatim() {
  // keyPrefix=""；斷言 putObject 的 key 不含額外前綴(與現行行為一致)
}
```

- [ ] **Step 4: 跑測試確認新測試失敗**（既有綠）

- [ ] **Step 5: 實作 S3FileStorage**

加私有 helper（class 內）：
```java
private String applyPrefix(String key) {
  String prefix = storageProperties.s3().keyPrefix();
  if (!StringUtils.hasText(prefix)) {
    return key;
  }
  return prefix.replaceAll("^/+|/+$", "") + "/" + key;
}
```
`store` 的 putObject、`read` 的 getObject、`delete` 的 deleteObject 各自把 key 換成 `applyPrefix(key)`。**store 回傳值維持邏輯 key（未經 applyPrefix）**——它會被存進 DB。

- [ ] **Step 6: S3WorkspacePurger prefix**

`S3WorkspacePurger` 注入的 `StorageProperties` 取 `s3().keyPrefix()`；`sessionExists`/`purgeSession` 組出的 `workspace/{userId}/sessions/{sessionId}/` 前綴改為 `applyPrefix("workspace/" + userId + "/sessions/" + sessionId + "/")`（同一 helper 邏輯，prefix 空則不變）。`S3WorkspacePurgerTest` 補 prefix 非空時 list/delete 前綴帶 `erd-cowork/` 的斷言。

- [ ] **Step 7: 補所有 `new StorageProperties.S3(...)` 呼叫處**（grep `new StorageProperties.S3(` 與 `.S3(`；測試 stub 給假值或空字串）

- [ ] **Step 8: 跑測試** `cd backend && ./mvnw test` 全綠

- [ ] **Step 9: Commit** `feat(backend): S3 key prefix——共用 bucket 子路徑`

---

### Task 2: Deepagent S3 key prefix

**Files:**
- Modify: `deepagent-service/app/config.py`（Settings 加 `S3_KEY_PREFIX`）
- Modify: `deepagent-service/app/engine/workspace.py`（`build_workspace_store` 組前綴）
- Modify: `deepagent-service/app/engine/source_cache.py`（下載 key 補前綴）
- Modify: `deepagent-service/one.properties`（committed 範本加 key，留空）
- Test: `deepagent-service/tests/test_workspace_s3.py`、`tests/test_source_cache.py`

**Interfaces:**
- Consumes: Task 1 無耦合（各自讀自己設定；一致性靠同值）。
- Produces: `Settings.S3_KEY_PREFIX: str = ""`。

- [ ] **Step 1: Settings 加欄位**

`app/config.py` 的 Settings 加 `S3_KEY_PREFIX: str = ""`（放在 `S3_*` 欄位群）。

- [ ] **Step 2: 寫失敗測試——source_cache prefix**

`tests/test_source_cache.py` 加（沿用既有 stub client 手法）：
```python
def test_resolve_source_path_key_prefix_downloads_from_prefixed_key():
    # S3_KEY_PREFIX="erd-cowork"、STORAGE_BACKEND="s3"
    # resolve_source_path("uploads/sess-1/uuid_a.csv")
    # 斷言 stub 的 download_file 收到的 Key = "erd-cowork/uploads/sess-1/uuid_a.csv"
    # 斷言回傳的本地路徑仍在 .sources-cache/uploads/...(不含 erd-cowork)

def test_resolve_source_path_key_prefix_empty_uses_key_verbatim():
    # S3_KEY_PREFIX=""；download_file 的 Key 不含額外前綴
```

- [ ] **Step 3: 跑測試確認失敗**

- [ ] **Step 4: 實作 source_cache**

`resolve_source_path` 在呼叫 `download_file` 前，S3 key 由 `raw_path` 改為 `_join_prefix(settings.S3_KEY_PREFIX, raw_path)`；本地 `destination` 路徑維持用 `raw_path`。加模組級 helper：
```python
def _join_prefix(prefix: str, key: str) -> str:
    prefix = prefix.strip("/")
    return f"{prefix}/{key}" if prefix else key
```

- [ ] **Step 5: 寫失敗測試——workspace_s3 prefix**

`tests/test_workspace_s3.py` 加：
```python
def test_persist_key_prefix_writes_under_prefixed_generation():
    # store 以 prefix="erd-cowork/workspace" 建構(見 Step 6 的組法)
    # persist 後斷言 stub 收到的 upload/put key 以 "erd-cowork/workspace/{user}/sessions/{session}/gen-" 開頭
```
（若既有測試已直接用 `prefix=` 參數建構 store，此測試只是換個 prefix 值驗證前綴透傳，不需改 store 內部。）

- [ ] **Step 6: build_workspace_store 組前綴**

`app/engine/workspace.py` 的 `build_workspace_store` s3 分支：
```python
from app.engine.workspace_s3 import WORKSPACE_PREFIX, S3WorkspaceStore
key_prefix = settings.S3_KEY_PREFIX.strip("/")
combined_prefix = f"{key_prefix}/{WORKSPACE_PREFIX}" if key_prefix else WORKSPACE_PREFIX
return S3WorkspaceStore(
    local_root=resolve_workspace_root(),
    bucket=settings.S3_BUCKET,
    prefix=combined_prefix,
    s3_client=build_s3_client(),
)
```
`S3WorkspaceStore` 內部不動（既有 prefix 正規化已處理任意前綴）。`S3WorkspacePurger`（backend）用同樣的 `{keyPrefix}/workspace/...` 組法，兩側對齊。

- [ ] **Step 7: one.properties 範本加 key**

在 S3 區塊加 `S3_KEY_PREFIX=`（留空，附註解：與 backend erd.storage.s3.key-prefix 同值）。

- [ ] **Step 8: 跑測試** `cd deepagent-service && uv run pytest -q && uv run ruff check .` 全綠

- [ ] **Step 9: Commit** `feat(deepagent): S3 key prefix——共用 bucket 子路徑`

---

### Task 3: 文件同步與驗證

**Files:**
- Modify: `docs/architecture.md`（S3 bucket 佈局：補 key prefix 說明、internal `rdp/erd-cowork/` 範例）
- Modify: `docs/internal-implementation-guide.md`（internal 物件儲存章節：bucket=rdp、key-prefix=erd-cowork、兩側同值要求、env 清單加 `ERD_STORAGE_S3_KEY_PREFIX`/`S3_KEY_PREFIX`）
- Modify: `.env.example`（若列 S3 keys，加 key-prefix 說明；否則指向權威清單即可）

- [ ] **Step 1: 三份文件更新**（敘述對齊 spec；強調預設空＝家裡行為不變、兩側 MUST 同值、internal 設定範例）

- [ ] **Step 2: 全套驗證**

Run: `cd backend && ./mvnw test` → 全綠
Run: `cd deepagent-service && uv run pytest -q && uv run ruff check .` → 全綠
Run: `cd frontend && npm test -- --run` → 全綠（前端零改動，確認）

- [ ] **Step 3: Commit** `docs: S3 key prefix 文件同步`
