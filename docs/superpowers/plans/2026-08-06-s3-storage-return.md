# S3 儲存路線回歸 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 上傳檔、artifact HTML、agent workspace 三者支援 S3 路線（`local`/`s3` 雙路線切換），workspace 採 write-once generation 快照模型。

**Architecture:** backend 回收 `S3FileStorage`/`S3StorageConfig`（git `1d3aae9^` 可取回原檔）＋抽 `WorkspacePurger` 接縫；deepagent 回收 `WorkspaceStore` Protocol（git `49021cc^`），`S3WorkspaceStore` 重寫為 generation 快照模型（每 turn pull 最新完整代到 per-turn scratch、persist 推全新代、`_complete` 標記、保留 2 代）；上傳檔交棒改傳 storageKey、deepagent 下載進 immutable sources cache。

**Tech Stack:** Java 17 / Spring Boot / AWS SDK v2 `s3`；Python 3.12 / FastAPI / boto3；MinIO（docker compose）。

**Spec:** `docs/superpowers/specs/2026-08-06-s3-storage-return-design.md`（權威需求來源）

## Global Constraints

- internal 儲存規範：**同一個 object key 不可重複上傳**——所有 S3 寫入路徑必須 write-once，每個 key 一生只寫一次。
- Committed 預設一律 `local`（backend `erd.storage.type`、deepagent `STORAGE_BACKEND`）；自動化測試不依賴 MinIO。
- storageKey 格式不變：`{category}/{sessionId}/{UUID}_{safeName}`；local/s3 的 key 可互換。
- Bucket 單一顆（預設 `erd-cowork`）；workspace 前綴 `workspace/{userId}/sessions/{sessionId}/gen-{epochMillis 13 碼}-{8 碼隨機 hex}/`，完成標記 `_complete` 最後寫入。
- persist 失敗 retry 兩次（每次全新 generation key），三次全失敗發 ERROR event。
- 保留最新 2 個完整 generation（常數，不做設定項）；未完成 generation 只有 timestamp 舊於 1 小時才可刪（防誤刪進行中的併發 persist）。
- Secrets NEVER 進 properties/程式碼：backend credentials 走 SDK default chain env（`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`）；deepagent 走 Settings（one.properties/env）。
- deepagent engine 純度：`app/engine/` 允許 stdlib + boto3，禁止 LLM 框架 import（ruff TID251）。
- Java 規則見 CLAUDE.md（constructor injection、@ConditionalOnProperty、測試命名 `methodName_condition_expectedBehavior`、google-java-format hook 自動跑）。
- 變數命名禁 1–2 字元縮寫（`id` 等 domain 語彙除外）。

---

### Task 1: Backend 儲存設定骨架與條件註冊

**Files:**
- Modify: `backend/pom.xml`（加回 AWS SDK v2 s3 依賴）
- Modify: `backend/src/main/java/com/erd/cowork/config/StorageProperties.java`
- Create: `backend/src/main/java/com/erd/cowork/config/S3StorageConfig.java`
- Modify: `backend/src/main/java/com/erd/cowork/storage/LocalDiskStorage.java`（加條件註解）
- Modify: `backend/src/main/resources/application.properties`
- Test: `backend/src/test/java/com/erd/cowork/storage/StorageConditionalRegistrationTest.java`

**Interfaces:**
- Produces: `StorageProperties(String type, String localDir, String workspaceDir, Cleanup cleanup, Retention retention, S3 s3)`；巢狀 `S3(String endpoint, String region, String bucket, boolean pathStyleAccess, String workspacePrefix)`。`S3Client` bean（僅 `type=s3` 時存在）。後續 Task 2/3/4 依賴。

- [ ] **Step 1: 取回舊檔參考**

```bash
git show 1d3aae9^:backend/src/main/java/com/erd/cowork/config/S3StorageConfig.java
git show 1d3aae9^:backend/src/test/java/com/erd/cowork/storage/StorageConditionalRegistrationTest.java
git show 1d3aae9^:backend/src/main/java/com/erd/cowork/config/StorageProperties.java
git show 1d3aae9^:backend/pom.xml | grep -B2 -A8 awssdk
```

- [ ] **Step 2: pom.xml 加回 AWS SDK**

比照舊 pom 段落（`software.amazon.awssdk:s3`，用 BOM 或直接版本——以舊 pom 取回的寫法為準；版本沿用舊 pom 的值，除非 `./mvnw dependency:resolve` 拉不到才調整）。

- [ ] **Step 3: StorageProperties 擴充**

```java
@ConfigurationProperties(prefix = "erd.storage")
public record StorageProperties(
    String type, String localDir, String workspaceDir, Cleanup cleanup, Retention retention, S3 s3) {

  public record Cleanup(String cron, boolean dryRun) {}

  public record Retention(Duration uploads, Duration workspace, Duration artifact) {}

  /** S3 connection settings; only read when {@code type=s3}. */
  public record S3(
      String endpoint, String region, String bucket, boolean pathStyleAccess, String workspacePrefix) {}
}
```

- [ ] **Step 4: application.properties 加 keys**

```properties
# 儲存路線:local(磁碟)| s3(物件儲存)。s3 credentials 走 AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY env
erd.storage.type=${ERD_STORAGE_TYPE:local}
erd.storage.s3.endpoint=${ERD_STORAGE_S3_ENDPOINT:}
erd.storage.s3.region=${ERD_STORAGE_S3_REGION:us-east-1}
erd.storage.s3.bucket=${ERD_STORAGE_S3_BUCKET:erd-cowork}
erd.storage.s3.path-style-access=${ERD_STORAGE_S3_PATH_STYLE:true}
erd.storage.s3.workspace-prefix=${ERD_STORAGE_S3_WORKSPACE_PREFIX:workspace}
```

- [ ] **Step 5: S3StorageConfig 原樣回收**（Step 1 取回內容照貼；`@ConditionalOnProperty(prefix = "erd.storage", name = "type", havingValue = "s3")`）

- [ ] **Step 6: LocalDiskStorage 加回條件註解**

```java
@ConditionalOnProperty(
    prefix = "erd.storage",
    name = "type",
    havingValue = "local",
    matchIfMissing = true)
```

- [ ] **Step 7: 回收 StorageConditionalRegistrationTest**（取回後依現行 StorageProperties 形狀微調；斷言 `type=local`（與未設）只有 `LocalDiskStorage` bean、`type=s3` 只有 `S3FileStorage` bean——後者此時會編譯失敗，先只斷言 local 側，s3 側斷言在 Task 2 補上並解開）

- [ ] **Step 8: 跑測試**

Run: `cd backend && ./mvnw test -Dtest=StorageConditionalRegistrationTest`
Expected: PASS

- [ ] **Step 9: Commit** `feat(backend): 儲存設定骨架——type 切換、S3Client 條件 bean`

---

### Task 2: S3FileStorage 回收

**Files:**
- Create: `backend/src/main/java/com/erd/cowork/storage/S3FileStorage.java`
- Test: `backend/src/test/java/com/erd/cowork/storage/S3FileStorageTest.java`
- Modify: `backend/src/test/java/com/erd/cowork/storage/StorageConditionalRegistrationTest.java`（補 s3 側斷言）

**Interfaces:**
- Consumes: Task 1 的 `StorageProperties`、`S3Client` bean。
- Produces: `S3FileStorage implements FileStorage`（`store`/`read`/`delete`，`SdkException`→`IOException`）。

- [ ] **Step 1: 取回舊檔**

```bash
git show 1d3aae9^:backend/src/main/java/com/erd/cowork/storage/S3FileStorage.java
git show 1d3aae9^:backend/src/test/java/com/erd/cowork/storage/S3FileStorageTest.java
```

- [ ] **Step 2: 原樣放回**，僅依現行 `StorageProperties` 形狀調整 `bucket()` 取值（`storageProperties.s3().bucket()`）。注意 `FileStorage.store()` 的契約：「MUST fully consume `in` to EOF」——舊實作以 `Files.copy` spool 到 temp file 已滿足，保留原註解。

- [ ] **Step 3: 測試放回並跑**（mock `S3Client`；含 store spool 行為、read 回 stream、delete、`SdkException` 包裝為 `IOException`、`NoSuchKeyException` 情境）

Run: `cd backend && ./mvnw test -Dtest='S3FileStorageTest,StorageConditionalRegistrationTest'`
Expected: PASS

- [ ] **Step 4: 全套 backend 測試**

Run: `cd backend && ./mvnw test`
Expected: 全綠（既有 LocalDiskStorage 路線不受影響）

- [ ] **Step 5: Commit** `feat(backend): S3FileStorage 回收——artifact/上傳檔 S3 路線`

---

### Task 3: 上傳檔交棒——resolveSourcePath 的 s3 分支

**Files:**
- Modify: `backend/src/main/java/com/erd/cowork/agent/provider/analysis/LangGraphAnalysisProvider.java`（`resolveSourcePath`，約 line 194–201，及 constructor）
- Test: `backend/src/test/java/com/erd/cowork/agent/provider/analysis/LangGraphAnalysisProviderTest.java`

**Interfaces:**
- Consumes: Task 1 的 `StorageProperties`。
- Produces: s3 模式下 deepagent 收到的 `SourceItem.path` 即 storageKey（`uploads/...`）；Task 8 依賴此語意。

- [ ] **Step 1: 寫失敗測試**（先看既有測試檔的建構方式，跟著現有 pattern 給 constructor 塞 `StorageProperties`）

```java
@Test
void resolveSourcePath_s3StorageType_returnsStorageKeyVerbatim() {
  // storageProperties stub: type="s3"
  assertThat(provider.resolveSourcePath("uploads/sess-1/abc_data.csv"))
      .isEqualTo("uploads/sess-1/abc_data.csv");
}

@Test
void resolveSourcePath_localStorageType_prependsSourceRoot() {
  // storageProperties stub: type="local"; sourceRoot="../backend/data/files"
  assertThat(provider.resolveSourcePath("uploads/sess-1/abc_data.csv"))
      .isEqualTo("../backend/data/files/uploads/sess-1/abc_data.csv");
}
```

- [ ] **Step 2: 跑測試確認失敗**（新測試紅、既有綠）

- [ ] **Step 3: 實作**

```java
String resolveSourcePath(String storageKey) {
  if ("s3".equals(storageProperties.type())) {
    return storageKey;
  }
  return analysisProperties.sourceRoot() + "/" + storageKey;
}
```

`StorageProperties` 以 constructor injection 加入（`@RequiredArgsConstructor` 既有欄位旁加 `private final StorageProperties storageProperties;`）；更新 Javadoc：s3 模式回傳 storageKey，deepagent 端自行下載。

- [ ] **Step 4: 跑全檔測試** `./mvnw test -Dtest=LangGraphAnalysisProviderTest` → PASS

- [ ] **Step 5: Commit** `feat(backend): s3 模式交棒改傳 storageKey`

---

### Task 4: WorkspacePurger 接縫——保留清理雙路線

**Files:**
- Create: `backend/src/main/java/com/erd/cowork/storage/WorkspacePurger.java`
- Create: `backend/src/main/java/com/erd/cowork/storage/LocalWorkspacePurger.java`
- Create: `backend/src/main/java/com/erd/cowork/storage/S3WorkspacePurger.java`
- Modify: `backend/src/main/java/com/erd/cowork/service/WorkspaceRetentionService.java`
- Test: `backend/src/test/java/com/erd/cowork/storage/S3WorkspacePurgerTest.java`
- 既有 `WorkspaceRetentionServiceTest` 必須全綠不改斷言（local 行為不變的迴歸網；`@TestPropertySource` 不需加 type——預設 local）。

**Interfaces:**
- Consumes: Task 1 的 `StorageProperties`、`S3Client` bean。
- Produces:

```java
public interface WorkspacePurger {
  /** True if the session leaves anything purgeable (directory / objects). Never deletes. */
  boolean sessionExists(String userId, String sessionId);

  /** Deletes the session's workspace. Returns true if anything was actually removed. */
  boolean purgeSession(String userId, String sessionId) throws IOException;
}
```

- [ ] **Step 1: LocalWorkspacePurger——現行邏輯原樣搬移**

`@Component` + `@ConditionalOnProperty(prefix = "erd.storage", name = "type", havingValue = "local", matchIfMissing = true)`。把 `WorkspaceRetentionService` 的路徑組裝、`startsWith` 檢查、`toRealPath` symlink 防護、`deleteRecursively` 全數搬進來：

- `sessionExists`：workspace root 無法 `toRealPath` → false（原「nothing to purge」語意）；組路徑＋`startsWith` 檢查失敗 → false（log warn 照舊）；回傳 `Files.isDirectory(sessionDir)`。
- `purgeSession`：重做同樣的路徑檢查（belt-and-suspenders 不因拆分而省略），`toRealPath` 落在 root 外 → log warn 回 false；通過才 `deleteRecursively` 回 true。`deleteRecursively`／`isSinglePathSegment` 之外的 Javadoc 註解一併搬移。

- [ ] **Step 2: WorkspaceRetentionService 瘦身**

```java
public int purgeStaleSessions(Instant cutoff) {
  List<ChatSession> staleSessions = sessionRepo.findByUpdatedAtBefore(cutoff);
  int count = 0;
  for (ChatSession session : staleSessions) {
    if (!isSinglePathSegment(session.getUserId()) || !isSinglePathSegment(session.getId())) {
      log.warn("Skipping workspace purge for malformed session userId={} sessionId={}",
          session.getUserId(), session.getId());
      continue;
    }
    try {
      if (!workspacePurger.sessionExists(session.getUserId(), session.getId())) {
        continue;
      }
      if (properties.cleanup().dryRun()) {
        log.info("[dry-run] would purge workspace session userId={} sessionId={}",
            session.getUserId(), session.getId());
        count++;
        continue;
      }
      if (workspacePurger.purgeSession(session.getUserId(), session.getId())) {
        count++;
      }
    } catch (IOException | RuntimeException exception) {
      log.warn("Failed to purge workspace session userId={} sessionId={}: {}",
          session.getUserId(), session.getId(), exception.getMessage(), exception);
    }
  }
  return count;
}
```

`isSinglePathSegment` 留在 service（兩種 purger 前的共同守門；Javadoc 原樣保留）。`WorkspacePurger` 以 constructor injection 注入。

- [ ] **Step 3: 跑既有測試** `./mvnw test -Dtest=WorkspaceRetentionServiceTest` → 全綠（五個既有測試含 symlink/unlistable/traversal 行為全部不變）

- [ ] **Step 4: S3WorkspacePurger + 失敗測試先行**

`@Component` + `@ConditionalOnProperty(... havingValue = "s3")`。前綴：`{workspacePrefix}/{userId}/sessions/{sessionId}/`（`workspacePrefix` 取自 `storageProperties.s3().workspacePrefix()`，尾端補 `/`）。

- `sessionExists`：`listObjectsV2`（`maxKeys(1)`）→ `keyCount() > 0`。
- `purgeSession`：`listObjectsV2Paginator` 收集 keys，每 1000 個一批 `deleteObjects`；回傳 `deletedCount > 0`。`SdkException` 包成 `IOException`。

測試（mock `S3Client`）：
```java
sessionExists_objectsUnderPrefix_returnsTrue
sessionExists_emptyPrefix_returnsFalse
purgeSession_manyObjects_deletesInBatchesOf1000
purgeSession_sdkException_wrapsAsIOException
purgeSession_prefixUsesConfiguredWorkspacePrefix   // 斷言完整前綴字串
```

- [ ] **Step 5: 跑新測試** → PASS；跑全套 `./mvnw test` → 全綠

- [ ] **Step 6: Commit** `feat(backend): WorkspacePurger 接縫——workspace 保留清理雙路線`

---

### Task 5: deepagent Settings 與 S3 client builder

**Files:**
- Modify: `deepagent-service/app/config.py`（Settings 加欄位）
- Modify: `deepagent-service/pyproject.toml`（boto3 依賴；`uv add boto3` 後檢查 lock）
- Create: `deepagent-service/app/engine/s3.py`
- Test: `deepagent-service/tests/test_s3_client.py`

**Interfaces:**
- Produces: Settings 欄位 `STORAGE_BACKEND: str = "local"`、`S3_ENDPOINT: str = ""`、`S3_REGION: str = "us-east-1"`、`S3_BUCKET: str = "erd-cowork"`、`S3_ACCESS_KEY: str = ""`、`S3_SECRET_KEY: str = ""`、`S3_WORKSPACE_PREFIX: str = "workspace"`；`app.engine.s3.build_s3_client() -> Any`。Task 6/7/8 依賴。

- [ ] **Step 1: Settings 加欄位**（照上方型別與預設值，加在 `AGENT_WORKSPACE_ROOT` 附近；one.properties 同名 key 自動生效——現有 PropertiesFileSource 機制）

- [ ] **Step 2: `uv add boto3`**（版本以 internal registry 可取得為準；若 constraint-dependencies 需要 pin transitive 參考 pyproject 既有 `[tool.uv]` 段）

- [ ] **Step 3: app/engine/s3.py**

```python
"""boto3 S3 client 建構。engine 層——stdlib + boto3,禁止 LLM 框架。"""

from typing import Any

from app.config import get_settings


def build_s3_client() -> Any:
    """以 Settings 顯式建構(不依賴 boto3 的 env 探測——one.properties 為 internal 單一設定來源)。

    每次呼叫現讀 settings,不做 module 單例——理由同 workspace.resolve_workspace_root()。
    """
    import boto3
    from botocore.config import Config

    settings = get_settings()
    return boto3.client(
        "s3",
        endpoint_url=settings.S3_ENDPOINT or None,
        aws_access_key_id=settings.S3_ACCESS_KEY,
        aws_secret_access_key=settings.S3_SECRET_KEY,
        region_name=settings.S3_REGION,
        # MinIO/內部物件儲存需要 path-style(virtual-hosted 對非 AWS endpoint 解析失敗)
        config=Config(s3={"addressing_style": "path"}),
    )
```

- [ ] **Step 4: 測試**（monkeypatch env 設定值，mock `boto3.client` 斷言參數；含 `S3_ENDPOINT` 空字串 → `endpoint_url=None`）

Run: `cd deepagent-service && uv run pytest tests/test_s3_client.py -v`
Expected: PASS

- [ ] **Step 5: Commit** `feat(deepagent): Settings S3 欄位與 boto3 client builder`

---

### Task 6: WorkspaceStore 抽象回歸與 persist 接線

**Files:**
- Modify: `deepagent-service/app/engine/workspace.py`
- Modify: `deepagent-service/app/agent/chat_turn.py`（`__aenter__` 約 line 218、`finalize()` 尾端約 line 396）
- Modify: `deepagent-service/app/agent/repair_flow.py`（約 line 64）
- Test: `deepagent-service/tests/test_workspace.py`（既有檔案擴充）、`deepagent-service/tests/test_chat_turn.py`（persist 失敗 → ERROR event）

**Interfaces:**
- Consumes: Task 5 的 Settings。
- Produces:

```python
class WorkspaceStore(Protocol):
    def prepare(self, user_id: str, session_id: str) -> SessionWorkspace: ...
    def persist(self, workspace: SessionWorkspace) -> None: ...

class WorkspacePersistError(RuntimeError):
    """persist 重試耗盡;呼叫端(ChatTurn.finalize)以 ERROR event 呈現。"""

class LocalWorkspaceStore:  # persist = no-op
def build_workspace_store() -> WorkspaceStore:  # STORAGE_BACKEND 分派
```

Task 7 在 `build_workspace_store` 補 s3 分支。

- [ ] **Step 1: workspace.py 加回抽象**（參考 `git show 49021cc^:deepagent-service/app/engine/workspace.py`）

```python
class WorkspaceStore(Protocol):
    def prepare(self, user_id: str, session_id: str) -> SessionWorkspace: ...

    def persist(self, workspace: SessionWorkspace) -> None: ...


class WorkspacePersistError(RuntimeError):
    """persist 重試耗盡——本輪產出未寫入持久層。"""


class LocalWorkspaceStore:
    def __init__(self, workspace_root: Path) -> None:
        self._workspace_root = workspace_root

    def prepare(self, user_id: str, session_id: str) -> SessionWorkspace:
        return prepare_local_layout(self._workspace_root, user_id, session_id)

    def persist(self, workspace: SessionWorkspace) -> None:
        """本地目錄即持久層,no-op。"""


def build_workspace_store() -> WorkspaceStore:
    """依 STORAGE_BACKEND 分派。每 request 呼叫一次現讀 settings(不做 module 單例)。"""
    backend = get_settings().STORAGE_BACKEND
    if backend == "local":
        return LocalWorkspaceStore(resolve_workspace_root())
    if backend == "s3":
        from app.engine.s3 import build_s3_client
        from app.engine.workspace_s3 import S3WorkspaceStore

        settings = get_settings()
        return S3WorkspaceStore(
            local_root=resolve_workspace_root(),
            bucket=settings.S3_BUCKET,
            prefix=settings.S3_WORKSPACE_PREFIX,
            s3_client=build_s3_client(),
        )
    raise ValueError(f"unknown STORAGE_BACKEND: {backend!r}")
```

`prepare_workspace()` 既有函式移除，呼叫端全部改走 store（grep `prepare_workspace` 清乾淨，含測試）。

- [ ] **Step 2: chat_turn.py 接線**

`__aenter__`：
```python
self._store = build_workspace_store()
self._workspace = self._store.prepare(request.userId, request.sessionId)
```

`finalize()` 尾端（`yield AnswerEvent(...)` 之後）：
```python
try:
    self._store.persist(self._workspace)
except WorkspacePersistError:
    logger.exception("workspace persist failed session=%s", request.sessionId)
    yield ErrorEvent(
        code="WORKSPACE_PERSIST_FAILED",
        message="本輪結果未能寫入儲存空間,下一輪可能拿不到這次的變更。",
    )
```

註：stream() 以 ErrorEvent 終止的失敗輪不會走到 finalize 尾端——刻意不 persist，前一個完整 generation 就是一致的回復點（寫成註解放在 persist 呼叫處）。

- [ ] **Step 3: repair_flow.py 接線**（read-only，不 persist）

```python
workspace = build_workspace_store().prepare(request.userId, request.sessionId)
```

- [ ] **Step 4: 測試**

- `test_workspace.py`：`build_workspace_store_local_returns_local_store`、`build_workspace_store_unknown_backend_raises`、`LocalWorkspaceStore.prepare` 等價於原 `prepare_workspace` 行為（骨架目錄存在）。
- `test_chat_turn.py`：monkeypatch `build_workspace_store` 回傳 stub store（`persist` raise `WorkspacePersistError`）→ 斷言 finalize 產出 `ErrorEvent(code="WORKSPACE_PERSIST_FAILED")` 且在 AnswerEvent 之後；stub `persist` 正常 → 無 ERROR event 且被呼叫一次。

Run: `cd deepagent-service && uv run pytest -x -q`
Expected: 全綠

- [ ] **Step 5: Commit** `feat(deepagent): WorkspaceStore 抽象回歸,persist 接上 turn 收尾`

---

### Task 7: S3WorkspaceStore——generation 快照模型

**Files:**
- Create: `deepagent-service/app/engine/workspace_s3.py`
- Test: `deepagent-service/tests/test_workspace_s3.py`

**Interfaces:**
- Consumes: Task 6 的 `WorkspaceStore`/`WorkspacePersistError`/`prepare_local_layout`/`_validate_segment`；Task 5 的 client。
- Produces: `S3WorkspaceStore(local_root: Path, bucket: str, prefix: str, s3_client: Any)`，`prepare`/`persist` 符合 Protocol。**per-use 有狀態 helper**（per request 建構，prepare→persist 同一實例）。

- [ ] **Step 1: 完整實作**（舊版 `git show 49021cc^:deepagent-service/app/engine/workspace_s3.py` 可參考 `_pull` 的防禦寫法與 `_S3Client` Protocol 手法，但同步模型全面重寫如下）

```python
"""S3-backed WorkspaceStore——generation 快照模型。

internal 儲存規範:同一 object key 不可重複上傳。因此 workspace 不覆寫既有物件,每 turn
persist 推一個全新 generation prefix(gen-{epochMillis13碼}-{8碼隨機hex}/),全部推完後最後寫
`_complete` 標記;prepare 只讀「timestamp 最大且帶 _complete」的 generation。讀方永遠拿到
完整一致快照,半途失敗的 push 天然不可見。

本地 scratch 為 per-turn 隔離目錄({local_root}/.turns/{hex}/),persist 成功後刪除——兩個
併發 turn(雙 tab)落在同一 pod 也不互踩;跨 turn 併發語意為 last-writer-wins(spec 定案)。

engine 純度規則:stdlib + boto3,禁止 LLM 框架(ruff TID251)。
"""

import logging
import re
import secrets
import shutil
import time
from pathlib import Path
from typing import Any, Protocol

from app.engine.workspace import (
    SessionWorkspace,
    WorkspacePersistError,
    _validate_segment,
    prepare_local_layout,
)

logger = logging.getLogger(__name__)

_GENERATION_PATTERN = re.compile(r"^gen-(\d{13})-([0-9a-f]{8})$")
_COMPLETE_MARKER = "_complete"
_KEPT_GENERATIONS = 2
_PERSIST_ATTEMPTS = 3
# 未完成 generation 只有舊於此值才可刪——防止清掉「另一個併發 turn 正在推」的半成品
_STALE_INCOMPLETE_MS = 60 * 60 * 1000
_SKILLS_STAGING_DIRNAME = ".skills"
_TURN_SCRATCH_DIRNAME = ".turns"


class _S3Client(Protocol):
    """boto3 S3 client 中本模組用到的方法——Protocol 收斂型別,測試注入 stub。"""

    def get_paginator(self, operation_name: str) -> Any: ...

    def download_file(self, Bucket: str, Key: str, Filename: str) -> None: ...

    def upload_file(self, Filename: str, Bucket: str, Key: str) -> None: ...

    def put_object(self, *, Bucket: str, Key: str, Body: bytes) -> Any: ...

    def delete_objects(self, *, Bucket: str, Delete: dict[str, Any]) -> Any: ...


class S3WorkspaceStore:
    """non-bean: instantiate per request(prepare→persist 同一實例,跨呼叫持有 turn 狀態)。"""

    def __init__(self, local_root: Path, bucket: str, prefix: str, s3_client: _S3Client) -> None:
        self._local_root = local_root
        self._bucket = bucket
        self._prefix = f"{prefix.strip('/')}/" if prefix.strip("/") else ""
        self._s3_client = s3_client
        self._session_prefix: str | None = None
        self._scratch_base: Path | None = None

    def prepare(self, user_id: str, session_id: str) -> SessionWorkspace:
        _validate_segment(user_id, "user_id")
        _validate_segment(session_id, "session_id")
        self._session_prefix = f"{self._prefix}{user_id}/sessions/{session_id}/"
        self._scratch_base = self._local_root / _TURN_SCRATCH_DIRNAME / secrets.token_hex(8)
        workspace = prepare_local_layout(self._scratch_base, user_id, session_id)
        latest = self._latest_complete_generation()
        if latest is not None:
            self._pull(f"{self._session_prefix}{latest}/", workspace.root)
        # user skills 與 session 無關、read-only(本 store 永不推回)——拉到 scratch 內對應
        # 位置,讓 chat_turn 的 workspace.root.parents[1]/"skills" 路徑算法照常成立
        self._pull(f"{self._prefix}{user_id}/skills/", workspace.root.parents[1] / "skills")
        return workspace

    def persist(self, workspace: SessionWorkspace) -> None:
        last_error: Exception | None = None
        for _attempt in range(_PERSIST_ATTEMPTS):
            generation = _new_generation_name()
            try:
                self._push(workspace, generation)
                break
            except Exception as error:  # noqa: BLE001 -- 任何 push 失敗都值得換新 key 重試
                last_error = error
                logger.warning(
                    "workspace push failed generation=%s, retrying with fresh key",
                    generation,
                    exc_info=True,
                )
        else:
            raise WorkspacePersistError(
                f"workspace persist failed after {_PERSIST_ATTEMPTS} attempts"
            ) from last_error
        self._cleanup_generations()
        if self._scratch_base is not None:
            shutil.rmtree(self._scratch_base, ignore_errors=True)

    # -- internals ---------------------------------------------------------------------------

    def _scan_generations(self) -> dict[str, dict[str, Any]]:
        """單趟 list 整個 session 前綴 → {generation 名: {"keys": [...], "complete": bool}}。"""
        assert self._session_prefix is not None
        generations: dict[str, dict[str, Any]] = {}
        paginator = self._s3_client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=self._session_prefix):
            for entry in page.get("Contents", []):
                key = entry["Key"]
                relative_key = key[len(self._session_prefix) :]
                generation_name, _, remainder = relative_key.partition("/")
                if not _GENERATION_PATTERN.fullmatch(generation_name):
                    continue
                record = generations.setdefault(generation_name, {"keys": [], "complete": False})
                record["keys"].append(key)
                if remainder == _COMPLETE_MARKER:
                    record["complete"] = True
        return generations

    def _latest_complete_generation(self) -> str | None:
        generations = self._scan_generations()
        complete = sorted(name for name, record in generations.items() if record["complete"])
        return complete[-1] if complete else None

    def _pull(self, remote_prefix: str, local_dir: Path) -> None:
        local_dir.mkdir(parents=True, exist_ok=True)
        resolved_local_dir = local_dir.resolve()
        paginator = self._s3_client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=remote_prefix):
            for entry in page.get("Contents", []):
                key = entry["Key"]
                relative_key = key[len(remote_prefix) :]
                if not relative_key or key.endswith("/") or relative_key == _COMPLETE_MARKER:
                    continue
                destination = (local_dir / relative_key).resolve()
                if resolved_local_dir not in destination.parents:
                    raise ValueError(f"S3 object key escapes local workspace dir: {key!r}")
                destination.parent.mkdir(parents=True, exist_ok=True)
                self._s3_client.download_file(self._bucket, key, str(destination))

    def _push(self, workspace: SessionWorkspace, generation: str) -> None:
        assert self._session_prefix is not None
        generation_prefix = f"{self._session_prefix}{generation}/"
        for path in sorted(workspace.root.rglob("*")):
            if path.is_dir():
                continue
            relative_path = path.relative_to(workspace.root)
            if relative_path.parts[0] == _SKILLS_STAGING_DIRNAME:
                continue
            self._s3_client.upload_file(
                str(path), self._bucket, f"{generation_prefix}{relative_path.as_posix()}"
            )
        # 完成標記 MUST 最後寫——它落地前這個 generation 對所有讀方不可見
        self._s3_client.put_object(
            Bucket=self._bucket, Key=f"{generation_prefix}{_COMPLETE_MARKER}", Body=b""
        )

    def _cleanup_generations(self) -> None:
        try:
            generations = self._scan_generations()
            complete_names = sorted(
                name for name, record in generations.items() if record["complete"]
            )
            keep = set(complete_names[-_KEPT_GENERATIONS:])
            now_millis = time.time_ns() // 1_000_000
            doomed_keys: list[str] = []
            for name, record in generations.items():
                if name in keep:
                    continue
                if not record["complete"]:
                    timestamp_millis = int(_GENERATION_PATTERN.fullmatch(name).group(1))
                    if now_millis - timestamp_millis < _STALE_INCOMPLETE_MS:
                        continue  # 可能是另一個併發 turn 正在推的半成品,不碰
                doomed_keys.extend(record["keys"])
            for batch_start in range(0, len(doomed_keys), 1000):
                batch = doomed_keys[batch_start : batch_start + 1000]
                self._s3_client.delete_objects(
                    Bucket=self._bucket,
                    Delete={"Objects": [{"Key": key} for key in batch], "Quiet": True},
                )
        except Exception:  # noqa: BLE001 -- 清舊代失敗不擋主流程,session 保留清理兜底
            logger.warning("generation cleanup failed, leftover objects remain", exc_info=True)


def _new_generation_name() -> str:
    return f"gen-{time.time_ns() // 1_000_000:013d}-{secrets.token_hex(4)}"
```

（`workspace.py` 的 `_validate_segment` 若為底線私有，改名為 `validate_segment` 公開並更新原呼叫點，或維持底線直接 import——擇一，跟 reviewer 說明選擇。）

- [ ] **Step 2: 測試**（stub `_S3Client`：dict 存物件的 fake，記錄 upload/delete 順序）

必測行為（每個一個測試函式，命名 `方法_情境_預期`）：
1. `prepare` 空 session → 空 workspace 骨架、無 pull
2. `prepare` 兩個完整 generation → 只 pull timestamp 較大者
3. `prepare` 最新 generation 無 `_complete` → pull 次新的完整代
4. `prepare` 的 scratch 路徑在 `.turns/` 下且兩次 prepare 互不相同
5. `_complete` 是 `_push` 最後一個寫入動作（以 stub 記錄順序斷言）
6. `persist` 首次 push 失敗 → 換新 generation 名重試（斷言兩次 push 的 prefix 不同）
7. `persist` 三次全失敗 → raise `WorkspacePersistError`
8. `persist` 成功 → 舊 generation 被刪、只留最新 2 個完整代
9. 未完成且 timestamp 新（< 1h）的 generation 不被清（併發保護）
10. 未完成且 timestamp 舊（> 1h）的殘骸被清
11. `persist` 排除 `.skills/`
12. `persist` 成功後 scratch 目錄被刪除
13. `_pull` 遇到 escape 路徑的 key → raise `ValueError`
14. user skills prefix 有物件 → 拉到 `workspace.root.parents[1]/skills`

Run: `cd deepagent-service && uv run pytest tests/test_workspace_s3.py -v`
Expected: 全 PASS

- [ ] **Step 3: 全套** `uv run pytest -x -q` → 全綠；`uv run ruff check .` → 乾淨

- [ ] **Step 4: Commit** `feat(deepagent): S3WorkspaceStore generation 快照模型`

---

### Task 8: Sources cache——s3 模式上傳檔下載

**Files:**
- Create: `deepagent-service/app/engine/source_cache.py`
- Modify: `deepagent-service/app/agent/chat_turn.py`（`__aenter__` 的 `open_locked_connection` 呼叫，約 line 225–227）
- Test: `deepagent-service/tests/test_source_cache.py`

**Interfaces:**
- Consumes: Task 5 的 Settings/`build_s3_client`。
- Produces: `resolve_source_path(raw_path: str) -> str`——local 模式原樣回傳；s3 模式視為 S3 key，下載到 cache 後回傳本地路徑。

- [ ] **Step 1: 實作**

```python
"""s3 模式的上傳檔本地 cache。上傳檔 immutable(上傳後永不改寫)→ cache 命中即跳過下載。

engine 純度規則:stdlib + boto3,禁止 LLM 框架(ruff TID251)。
"""

import logging
import secrets
from pathlib import Path

from app.config import get_settings

logger = logging.getLogger(__name__)

_SOURCES_CACHE_DIRNAME = ".sources-cache"


def resolve_source_path(raw_path: str) -> str:
    """local 模式:raw_path 即共享磁碟路徑,原樣回傳。s3 模式:raw_path 是 storageKey,
    下載到 {AGENT_WORKSPACE_ROOT}/.sources-cache/{storageKey} 後回傳本地路徑。"""
    settings = get_settings()
    if settings.STORAGE_BACKEND != "s3":
        return raw_path
    _validate_storage_key(raw_path)
    cache_root = Path(settings.AGENT_WORKSPACE_ROOT) / _SOURCES_CACHE_DIRNAME
    destination = cache_root / raw_path
    if destination.exists():
        return str(destination)
    from app.engine.s3 import build_s3_client

    destination.parent.mkdir(parents=True, exist_ok=True)
    # 先落 temp 再 rename:併發下載互不影響,cache 內永遠只有完整檔案
    partial = destination.with_name(f"{destination.name}.part-{secrets.token_hex(4)}")
    build_s3_client().download_file(settings.S3_BUCKET, raw_path, str(partial))
    partial.replace(destination)
    logger.info("source cached key=%s", raw_path)
    return str(destination)


def _validate_storage_key(storage_key: str) -> None:
    key_path = Path(storage_key)
    if key_path.is_absolute() or ".." in key_path.parts or not storage_key:
        raise ValueError(f"unsafe storage key: {storage_key!r}")
```

- [ ] **Step 2: chat_turn 接線**

```python
self._connection = open_locked_connection(
    [Source(item.alias, resolve_source_path(item.path), item.fileType) for item in request.sources]
)
```

- [ ] **Step 3: 測試**（stub client）：local 模式原樣回傳且零 S3 呼叫、s3 模式下載並回傳 cache 路徑、cache 命中跳過下載、`..`/絕對路徑 key → `ValueError`、下載中殘留 `.part-*` 不被當成 cache 命中。

Run: `cd deepagent-service && uv run pytest tests/test_source_cache.py -v` → PASS；`uv run pytest -x -q` → 全綠

- [ ] **Step 4: Commit** `feat(deepagent): s3 模式 sources cache——storageKey 下載與 immutable 快取`

---

### Task 9: docker compose MinIO 與環境接線

**Files:**
- Modify: `docker-compose.infra.yml`（`minio` + `minio-init` service）
- Modify: `docker-compose.app.yml`（backend/deepagent 的 s3 env）
- Modify: `.env.example`

**Interfaces:**
- Consumes: Task 1/5 的設定 keys。

- [ ] **Step 1: infra 加 MinIO**（不共用 `lf-minio`——它綁 observability profile、credentials 不同。網路/volume 寫法比照檔內既有 service）

```yaml
  minio:
    image: minio/minio:RELEASE.2025-09-07T16-13-09Z
    restart: unless-stopped
    command: server --address ":9000" --console-address ":9001" /data
    environment:
      MINIO_ROOT_USER: ${ERD_MINIO_ROOT_USER:-erd_minio}
      MINIO_ROOT_PASSWORD: ${ERD_MINIO_ROOT_PASSWORD:-erd_minio_dev}
    ports:
      - "9000:9000"
      - "9001:9001"
    volumes:
      - minio-data:/data
    networks:
      - erd-cowork-net
    healthcheck:
      test: ["CMD", "mc", "ready", "local"]
      interval: 2s
      timeout: 5s
      retries: 10
      start_period: 2s

  minio-init:
    # minio/minio 镜像內建 mc,不另拉 minio/mc 映像
    image: minio/minio:RELEASE.2025-09-07T16-13-09Z
    depends_on:
      minio:
        condition: service_healthy
    entrypoint: sh
    # plain bucket:不開 versioning——workspace 走 write-once generation,不需要版本
    command: -c 'mc alias set local http://minio:9000 "$${MINIO_ROOT_USER}" "$${MINIO_ROOT_PASSWORD}" && mc mb --ignore-existing local/erd-cowork'
    environment:
      MINIO_ROOT_USER: ${ERD_MINIO_ROOT_USER:-erd_minio}
      MINIO_ROOT_PASSWORD: ${ERD_MINIO_ROOT_PASSWORD:-erd_minio_dev}
    networks:
      - erd-cowork-net
    restart: "no"
```

volume `minio-data` 加進檔尾 volumes 區。

- [ ] **Step 2: app compose 接 env**（先讀現行 `docker-compose.app.yml` 的 env 寫法，比照補上）

backend service：
```yaml
      ERD_STORAGE_TYPE: ${ERD_STORAGE_TYPE:-s3}
      ERD_STORAGE_S3_ENDPOINT: http://minio:9000
      AWS_ACCESS_KEY_ID: ${ERD_MINIO_ROOT_USER:-erd_minio}
      AWS_SECRET_ACCESS_KEY: ${ERD_MINIO_ROOT_PASSWORD:-erd_minio_dev}
      AWS_REGION: us-east-1
```

deepagent service：
```yaml
      STORAGE_BACKEND: ${STORAGE_BACKEND:-s3}
      S3_ENDPOINT: http://minio:9000
      S3_ACCESS_KEY: ${ERD_MINIO_ROOT_USER:-erd_minio}
      S3_SECRET_KEY: ${ERD_MINIO_ROOT_PASSWORD:-erd_minio_dev}
```

（compose 路線預設 s3；`local` 仍可用 env 覆寫回去。）

- [ ] **Step 3: .env.example 補段落**（MinIO credentials 兩個 key＋一句話說明 compose 預設走 s3→MinIO；backend/deepagent 的 key 清單權威來源仍是 application.properties / app/config.py Settings，不重複列舉）

- [ ] **Step 4: 驗證 compose 語法** `docker compose -f docker-compose.infra.yml config -q && docker compose -f docker-compose.app.yml config -q` → 無錯誤

- [ ] **Step 5: Commit** `feat(compose): MinIO service 與 s3 env 接線`

---

### Task 10: 文件同步與全套驗證

**Files:**
- Modify: `docs/architecture.md`（儲存章節：雙路線＋generation 快照模型；上傳檔交棒 s3 語意）
- Modify: `CLAUDE.md`（「檔案」bullet：PVC RWX 單一路線敘述改為雙路線 local/s3，一句話帶 generation 模型與 write-once 規範）
- Modify: `docs/internal-implementation-guide.md`（internal 物件儲存接線章節：endpoint/credentials env、bucket 規範、write-once 落地方式、deepagent Settings keys）

- [ ] **Step 1: 三份文件更新**（敘述對齊 spec；architecture.md 若有儲存相關圖表一併改）

- [ ] **Step 2: 全套測試**

Run: `cd backend && ./mvnw test`
Expected: 全綠
Run: `cd deepagent-service && uv run pytest -q && uv run ruff check .`
Expected: 全綠
Run: `cd frontend && npm test -- --run`
Expected: 全綠（前端零改動，跑一次確認）

- [ ] **Step 3: Commit** `docs: 儲存雙路線與 generation 模型文件同步`
