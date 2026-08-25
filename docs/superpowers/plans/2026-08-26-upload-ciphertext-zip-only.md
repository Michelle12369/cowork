# 上傳 xlsx 密文直存＋快照 zip-only Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** xlsx 上傳改為原樣直存（internal＝密文落地），解密與 xlsx→CSV 轉檔搬到 deepagent 下載時；同支順帶把 workspace 快照的舊 per-file 代向後相容移除（zip-only）。

**Architecture:** Java 對 `RAW_STORED_TYPES`（xlsx）跳過解密/轉檔/解析、原 bytes 直存（`UploadDecryptor` 體系整組刪除，環境無關）；deepagent `source_cache` 以 `.xlsx` 副檔名觸發「下載→解密接縫（import-if-exists，dev＝identity）→openpyxl 轉 CSV→cache」。spec：`docs/superpowers/specs/2026-08-26-upload-ciphertext-and-zip-only-design.md`。

**Tech Stack:** Java 17 / Spring Boot、Python 3.12 / FastAPI、openpyxl（新增）、stdlib zipfile。

## Global Constraints

- Branch：`feat/workspace-zip-snapshot`（worktree `worktrees/workspace-zip`；PR #67 擴充）
- Java：constructor injection；測試命名 `methodName_condition_expectedBehavior`；google-java-format hook 自動跑
- Python：engine 純度規則更新為「stdlib＋boto3＋openpyxl（僅 xlsx 轉檔）」；`ruff check .` 必綠；FastAPI/pytest 慣例照 `.claude/skills/fastapi/SKILL.md`
- **鏡像註解不變式**：Java `RAW_STORED_TYPES` 與 Python 副檔名判定處 MUST 互相指名對方——「此清單增加任何型別（尤其 csv）時，Python source_cache 的副檔名推斷即失效，MUST 改 per-file metadata」
- 失敗模式 MUST fail loud：internal 未備妥解密實作 → identity 解密 → openpyxl 開檔失敗 raise（絕不 silent garbage）
- 驗證指令：deepagent `cd deepagent-service && uv run pytest -q && uv run ruff check .`；backend `cd backend && ./mvnw test`（注意帶 `SPRING_DATA_MONGODB_URI=mongodb://localhost:27017/cowork-test`，避免洗 dev DB）
- Commit trailer 一律：
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`
  `Claude-Session: https://claude.ai/code/session_01YANgdohkmgzDPhxeLJLgmK`

## 檔案地圖

- Modify: `deepagent-service/app/engine/workspace_store.py`（zip-only）
- Create: `deepagent-service/app/engine/upload_decrypt.py`（解密接縫）
- Create: `deepagent-service/app/engine/xlsx_to_csv.py`（轉檔）
- Modify: `deepagent-service/app/engine/source_cache.py`（.xlsx 管線）
- Modify: `deepagent-service/pyproject.toml`＋`requirements.txt`（openpyxl）
- Modify: `backend/.../service/FileService.java`（raw 直存分支；刪 decryptor）
- Delete: `backend/.../storage/UploadDecryptor.java`、`PassthroughUploadDecryptor.java`
- Modify: `backend/.../agent/AgentOrchestrator.java`（null profile 保留進 context）
- Modify: `backend/.../agent/provider/openai/PromptAssembler.java`（null profile 降級）
- Modify: `scripts/internal-owned-paths.txt`（+ `deepagent-service/app/engine/upload_decrypt_impl.py`）

---

### Task 1: workspace_store zip-only（移除舊 per-file 代相容）

**Files:**
- Modify: `deepagent-service/app/engine/workspace_store.py`
- Test: `deepagent-service/tests/test_workspace_store.py`

**Interfaces:**
- Produces: `prepare`/`persist`/`download_file`/`cleanup_scratch` 對外簽名不變；內部僅認 `gen-*.zip`

- [ ] **Step 1: 先讀後改**：通讀 `workspace_store.py`（355 行）與 `test_workspace_store.py`（602 行），列出所有 legacy 專屬物：`_COMPLETE_MARKER`、目錄形 generation 常數/註解（~44-55 行）、`_scan_generations` 的 per-file 掃描半邊、`_download_legacy_generation_file`、`_pull`（per-file 拉取；注意 skills 前綴 per-file 拉取**不是** legacy，保留）、cleanup 的目錄形 stale-incomplete 規則、模組 docstring 的向後相容段。
- [ ] **Step 2: 改寫測試為 zip-only 預期**（先改測試＝失敗基準）：刪除「舊代讀取」「混合排序」「舊代單檔下載」「混合清理」情境；新增/保留斷言：`prepare` 只認 `gen-*.zip`（bucket 裡放一個舊目錄形 `gen-*/file` ＋ `_complete` marker 時 MUST 被**忽略**——這是 zip-only 的定義性測試，命名 `prepare_legacyPerFileGeneration_ignored`）；`download_file` 僅 zip 路徑三情境（entry 在／entry 缺回 None／zip 物件缺回 None）保留；zip-slip、原子寫入測試不動。
- [ ] **Step 3: 跑測試確認失敗**：`uv run pytest tests/test_workspace_store.py -q` → 新斷言 FAIL（現行仍認舊代）。
- [ ] **Step 4: 實作移除**：刪 `_download_legacy_generation_file`、`_pull`（per-file 代版本）、`_scan_generations` 的目錄形半邊（函式收斂為「列 `gen-*.zip` 物件→取字串排序最大」）、`_COMPLETE_MARKER` 與目錄形常數、cleanup 目錄形規則；docstring 向後相容段改為「僅支援 zip 代；舊 per-file 代不再讀取（internal 未部署過快照，無線上舊代）」。
- [ ] **Step 5: 全綠確認**：`uv run pytest -q && uv run ruff check .` → PASS。
- [ ] **Step 6: Commit**：`refactor(deepagent): workspace 快照 zip-only——舊 per-file 代相容整組移除`

---

### Task 2: 解密接縫 upload_decrypt

**Files:**
- Create: `deepagent-service/app/engine/upload_decrypt.py`
- Test: `deepagent-service/tests/test_upload_decrypt.py`

**Interfaces:**
- Produces: `decrypt_upload(ciphertext_path: Path, plaintext_path: Path) -> None`（Task 4 消費）

- [ ] **Step 1: 寫失敗測試**

```python
"""解密接縫：repo 內預設 identity；internal 實作存在時整個函式被覆蓋。"""

from pathlib import Path

from app.engine import upload_decrypt


def test_decrypt_upload_default_copies_bytes_verbatim(tmp_path: Path) -> None:
    source = tmp_path / "cipher.bin"
    source.write_bytes(b"\x00\x01payload")
    destination = tmp_path / "plain.bin"
    upload_decrypt.decrypt_upload(source, destination)
    assert destination.read_bytes() == b"\x00\x01payload"
    assert source.exists()  # 接縫不得動來源檔


def test_decrypt_upload_is_overridable_seam() -> None:
    # internal 以同名模組覆蓋;repo 端只驗證預設實作可被替換的形狀(callable 模組屬性)
    assert callable(upload_decrypt.decrypt_upload)
```

- [ ] **Step 2: 確認失敗**：`uv run pytest tests/test_upload_decrypt.py -q` → FAIL（module 不存在）。
- [ ] **Step 3: 實作**

```python
"""上傳檔解密接縫。repo 內預設＝identity copy(dev/測試;上傳檔本來就是明文)。

internal 環境放置 `app/engine/upload_decrypt_impl.py`(獨佔路徑,見
scripts/internal-owned-paths.txt)提供真解密——模組存在即整個取代預設實作。
內部未備妥實作時,密文經 identity 直通,後續 xlsx 解析會直接 raise(fail loud,
絕不 silent garbage)。憑證與協定由 internal 實作自理,接縫只交換檔案路徑。
"""

import shutil
from pathlib import Path


def _passthrough_decrypt(ciphertext_path: Path, plaintext_path: Path) -> None:
    shutil.copyfile(ciphertext_path, plaintext_path)


try:
    from app.engine.upload_decrypt_impl import decrypt_upload  # type: ignore[no-redef]
except ImportError:
    decrypt_upload = _passthrough_decrypt
```

- [ ] **Step 4: 確認通過**：`uv run pytest tests/test_upload_decrypt.py -q` → PASS。
- [ ] **Step 5: Commit**：`feat(deepagent): 上傳檔解密接縫——import-if-exists,預設 identity`

---

### Task 3: xlsx→CSV 轉檔器

**Files:**
- Create: `deepagent-service/app/engine/xlsx_to_csv.py`
- Modify: `deepagent-service/pyproject.toml`（dependencies 加 `"openpyxl>=3.1,<4"`）；隨後 `uv lock && uv export --no-emit-project -o requirements.txt`（比照 repo 既有流程；`tests/test_requirements_sync.py` 會驗同步）
- Test: `deepagent-service/tests/test_xlsx_to_csv.py`

**Interfaces:**
- Produces: `convert_xlsx_to_csv(xlsx_path: Path, csv_path: Path) -> None`（Task 4 消費）；語意＝僅第一張 sheet、cell 轉格式化文字、UTF-8 CSV

- [ ] **Step 1: 寫失敗測試**（fixture 用 openpyxl 現場產生，不放二進位檔）

```python
from datetime import datetime
from pathlib import Path

import openpyxl

from app.engine.xlsx_to_csv import convert_xlsx_to_csv


def _write_xlsx(path: Path, rows: list[list[object]], extra_sheet: bool = False) -> None:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(row)
    if extra_sheet:
        second = workbook.create_sheet("second")
        second.append(["should", "not", "appear"])
    workbook.save(path)


def test_convert_first_sheet_values_to_csv(tmp_path: Path) -> None:
    source = tmp_path / "input.xlsx"
    _write_xlsx(source, [["name", "count"], ["widget", 3], ["gadget", 1.5]])
    output = tmp_path / "out.csv"
    convert_xlsx_to_csv(source, output)
    assert output.read_text(encoding="utf-8").splitlines() == [
        "name,count",
        "widget,3",
        "gadget,1.5",
    ]


def test_convert_only_first_sheet(tmp_path: Path) -> None:
    source = tmp_path / "multi.xlsx"
    _write_xlsx(source, [["a"]], extra_sheet=True)
    output = tmp_path / "out.csv"
    convert_xlsx_to_csv(source, output)
    assert "appear" not in output.read_text(encoding="utf-8")


def test_convert_none_and_datetime_cells(tmp_path: Path) -> None:
    source = tmp_path / "mixed.xlsx"
    _write_xlsx(source, [["when", "note"], [datetime(2026, 8, 26, 9, 30), None]])
    output = tmp_path / "out.csv"
    convert_xlsx_to_csv(source, output)
    lines = output.read_text(encoding="utf-8").splitlines()
    assert lines[1] == "2026-08-26 09:30:00,"  # datetime→isoformat(sep=' ')、None→空字串


def test_convert_invalid_xlsx_raises(tmp_path: Path) -> None:
    source = tmp_path / "garbage.xlsx"
    source.write_bytes(b"not a zip at all")
    import pytest

    with pytest.raises(Exception):  # fail loud:internal 未解密的密文走到這裡必炸
        convert_xlsx_to_csv(source, tmp_path / "out.csv")
```

- [ ] **Step 2: 確認失敗** → module 不存在。
- [ ] **Step 3: 實作**

```python
"""xlsx→CSV 轉檔(openpyxl read-only streaming)。

語意對齊 Java 舊管線(POI DataFormatter):僅第一張 sheet、cell 輸出為文字。
已知差異(允收):數字格式代碼(千分位/百分比樣式)不重現——輸出原始值文字;
datetime 輸出 `YYYY-MM-DD HH:MM:SS`。壞檔(未解密密文/非 xlsx)由 openpyxl
直接 raise——fail loud 是設計要求,勿包成靜默略過。
"""

import csv
from datetime import datetime
from pathlib import Path

import openpyxl


def _format_cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def convert_xlsx_to_csv(xlsx_path: Path, csv_path: Path) -> None:
    workbook = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
    try:
        first_sheet = workbook.worksheets[0]
        with csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            for row in first_sheet.iter_rows(values_only=True):
                writer.writerow([_format_cell(cell) for cell in row])
    finally:
        workbook.close()
```

- [ ] **Step 4: 依賴落地**：pyproject 加 `"openpyxl>=3.1,<4",`（附註解「僅 source_cache xlsx 轉檔用——engine 純度：stdlib＋boto3＋openpyxl」）→ `uv lock && uv export --no-emit-project -o requirements.txt` → `uv run pytest tests/test_xlsx_to_csv.py tests/test_requirements_sync.py -q` → PASS。
- [ ] **Step 5: 全綠**：`uv run pytest -q && uv run ruff check .`。
- [ ] **Step 6: Commit**：`feat(deepagent): xlsx→CSV 轉檔器(openpyxl,第一 sheet,fail loud)`

---

### Task 4: source_cache 接上 .xlsx 管線

**Files:**
- Modify: `deepagent-service/app/engine/source_cache.py`
- Test: `deepagent-service/tests/test_source_cache.py`（追加）

**Interfaces:**
- Consumes: `decrypt_upload(Path, Path)`（Task 2）、`convert_xlsx_to_csv(Path, Path)`（Task 3）
- Produces: `resolve_source_path(raw_path)` 對 `.xlsx` 回傳 cache 內 `.csv` 路徑；`.csv` 行為零變化

- [ ] **Step 1: 寫失敗測試**（沿用該檔既有 fixture 手法——先讀現有測試的 settings/s3 mock 模式再落筆；核心斷言如下，s3 與 local 兩分支都要蓋）

```python
def test_resolve_xlsx_local_decrypts_converts_and_caches_csv(tmp_path, monkeypatch):
    # local 模式:uploads 路徑放一個「明文 xlsx」(identity 解密路徑=dev 即 internal 同管線)
    source_dir = tmp_path / "uploads" / "sess-1"
    source_dir.mkdir(parents=True)
    xlsx_path = source_dir / "u1_data.xlsx"
    _write_minimal_xlsx(xlsx_path, [["col"], ["value"]])  # 用 openpyxl helper,同 Task 3 測試
    resolved = resolve_source_path(str(xlsx_path))
    assert resolved.endswith("uploads/sess-1/u1_data.csv")
    assert Path(resolved).read_text(encoding="utf-8").startswith("col")


def test_resolve_xlsx_cache_hit_skips_pipeline(tmp_path, monkeypatch):
    # 第二次呼叫不得重新解密/轉檔:先 resolve 一次,改寫 cache 檔內容當標記,再 resolve
    ...  # 斷言回傳內容仍是標記(pipeline 未重跑)


def test_resolve_csv_path_behaviour_unchanged(tmp_path, monkeypatch):
    ...  # 既有 .csv 情境照舊(檔案複製、cache key)——釘零變化
```

- [ ] **Step 2: 確認失敗**。
- [ ] **Step 3: 實作**：`resolve_source_path` 內以 `raw_path.endswith(".xlsx")`（s3 分支對 storageKey、local 分支對磁碟路徑）分流：cache 目的地＝原 key 去 `.xlsx` 換 `.csv`；fill 函式改為三步——下載/複製密文到 `partial` 旁的暫存（`partial.with_suffix(".cipher")` 之類，用完 finally 刪）→ `decrypt_upload(cipher_tmp, plain_tmp)` → `convert_xlsx_to_csv(plain_tmp, partial)`。既有 `_fill_cache` 的 temp+rename 原子性照用。**鏡像註解**：在副檔名判斷處寫上「與 backend FileService.RAW_STORED_TYPES 互為鏡像——該清單增型別時此推斷失效，MUST 改 per-file metadata（見 spec）」。
- [ ] **Step 4: 全綠**：`uv run pytest -q && uv run ruff check .`。
- [ ] **Step 5: Commit**：`feat(deepagent): source_cache 對 .xlsx 走下載→解密→轉檔→cache CSV`

---

### Task 5: Java xlsx 原樣直存＋UploadDecryptor 移除

**Files:**
- Modify: `backend/src/main/java/com/erd/cowork/service/FileService.java`
- Delete: `backend/src/main/java/com/erd/cowork/storage/UploadDecryptor.java`、`backend/src/main/java/com/erd/cowork/storage/PassthroughUploadDecryptor.java`
- Test: `backend/src/test/java/com/erd/cowork/service/FileServiceTest.java`（找到實際檔名；若為別名以 grep `FileService` 定位）

**Interfaces:**
- Produces: xlsx 上傳後 `UploadedFile{type="xlsx", storageKey 以 .xlsx 結尾, rowCount=null, metadataJson=null}`；storage 內容與上傳 bytes 完全一致（Task 6 依賴 null metadataJson 語意）

- [x] **Step 1: 寫失敗測試**（依既有 FileService 測試的建構手法；核心三條）

```java
@Test
void upload_xlsxFile_storedVerbatimWithoutProfile() {
  // 上傳一段任意 bytes(偽 xlsx,內容不需要是合法 xlsx——Java 不再解析它)
  // 斷言:storage 讀回 byte-identical;entity.type=="xlsx";rowCount==null;metadataJson==null;
  // storageKey 以 .xlsx 結尾
}

@Test
void upload_csvFile_normalizeAndProfileUnchanged() {
  // 既有 csv 行為回歸:profile 有值、metadataJson 非 null(多半已有同型測試,補斷言即可)
}

@Test
void upload_xlsxFile_neverInvokesNormalizerOrParsing() {
  // 以 mock/spy 驗證 normalizer.normalize 與 parsing.profile 未被呼叫(xlsx 路徑)
}
```

- [x] **Step 2: 確認失敗**：`./mvnw test -Dtest=FileServiceTest`（帶 cowork-test URI）。
- [x] **Step 3: 實作**：
  - `ENCRYPTED_UPLOAD_TYPES` 更名 `RAW_STORED_TYPES`（值仍 `Set.of("xlsx")`），註解改寫：「原樣直存、由 deepagent 下載時解密＋轉檔。**與 deepagent source_cache 的 `.xlsx` 副檔名推斷互為鏡像**——此清單增加任何型別（尤其 csv）時該推斷失效，MUST 改 per-file metadata（見 spec 2026-08-26）。internal 環境此類檔案是密文；本服務對其 bytes 不可有任何解讀。」
  - 上傳迴圈分流：`RAW_STORED_TYPES.contains(uploadedExtension)` → 直接 `storage.store(StorageCategory.UPLOAD, sessionId, filename, counting)`（原 bytes；`CountingInputStream` 包 `upload.getInputStream()`），`storedType = uploadedExtension`、`profile = null`，不經 normalizer 與 `parsing.profile`；else → 既有路徑原樣（解密呼叫拿掉，csv 本來就不經 decryptor）。
  - entity 寫入改 null 安全：`entity.setRowCount(profile == null ? null : profile.rowCount()); entity.setMetadataJson(profile == null ? null : parsing.toJson(profile));`
  - 刪 `UploadDecryptor`/`PassthroughUploadDecryptor` 檔案、`decryptor` 欄位與 import；grep 全 repo `erd.upload.decryption` 與 `UploadDecryptor` 清乾淨（`application*.properties`、config、文件）。
- [x] **Step 4: 全綠**：`./mvnw test`（帶 cowork-test URI）。
- [x] **Step 5: Commit**：`feat(backend): xlsx 原樣直存——UploadDecryptor 體系移除,解密轉檔移交 deepagent`

---

### Task 6: null profile 消費端（orchestrator 保留進 context＋PromptAssembler 降級）

**Files:**
- Modify: `backend/src/main/java/com/erd/cowork/agent/AgentOrchestrator.java:200-220`
- Modify: `backend/src/main/java/com/erd/cowork/agent/provider/openai/PromptAssembler.java`（`appendFileSection`）
- Test: 對應既有測試檔追加

**Interfaces:**
- Consumes: Task 5 的「xlsx → metadataJson=null」語意
- Produces: null-metadata 檔案**仍進** `AgentFileContext`（profile=null）→ deepagent 線拿得到 alias/storageKey；llm-api 線 prompt 對 null profile 印最小段

- [ ] **Step 1: 寫失敗測試**

```java
// AgentOrchestrator 側(找到現有 orchestrator 測試檔追加):
// buildFileContexts_nullMetadataJson_stillIncludedWithNullProfile
//   —— 存一筆 metadataJson=null 的 UploadedFile,斷言 fileContexts 包含該 alias 且 profile()==null
//   (現行為 continue 跳過——此測試現在必 FAIL)

// PromptAssembler 側:
// assemble_fileWithNullProfile_rendersHeaderWithoutSchemaSections
//   —— AgentFileContext.profile()==null 時輸出含 "## file: <alias>" 與 type,
//      不含 "| column |" 表頭與 sample 段,且不拋 NPE
```

- [ ] **Step 2: 確認失敗**。
- [ ] **Step 3: 實作**：orchestrator 把 `metadataJson == null → continue` 改為 `profile = null` 續建 context（readValue 失敗仍 warn＋null）；`PromptAssembler.appendFileSection` 開頭 `if (file.profile() == null)` → 印 `## file: alias (name)` ＋ `type: xlsx · rows: unknown（schema available at analysis time only）` 後 return。
- [ ] **Step 4: 全綠**：`./mvnw test`。
- [ ] **Step 5: Commit**：`fix(backend): null profile 檔案保留進 agent context——deepagent 線不因無 metadata 漏檔`

---

### Task 7: 獨佔路徑清單＋三側驗證＋PR #67 更新

- [ ] **Step 1**：`scripts/internal-owned-paths.txt` 加一行 `deepagent-service/app/engine/upload_decrypt_impl.py`（位置照現有排序慣例；這是 internal 真解密實作的落點）。
- [ ] **Step 2: 三側全套**：deepagent `uv run pytest -q && uv run ruff check .`；backend `SPRING_DATA_MONGODB_URI=mongodb://localhost:27017/cowork-test ./mvnw test`；frontend 未動（可跳，或快跑 `npx vitest run` 保險）。全綠才續。
- [ ] **Step 3: 實機煙測**（verification-before-completion）：起 compose/本地雙服務，上傳一個真 xlsx → 確認 storage 裡是 `.xlsx` 原 bytes、DB rowCount null → 對話觸發分析 → deepagent log 出現 source cached `.csv`、DuckDB 讀到資料。
- [ ] **Step 4: 更新 PR #67**：`gh pr edit 67` 標題改「feat(deepagent+backend): 快照 zip-only＋上傳 xlsx 密文直存（解密轉檔移交 Python）」；描述含：spec 連結、兩塊機制摘要、accepted trade-off（llm-api 線 csv-only）、**部署備忘**（internal 同步前 MUST 備妥 `upload_decrypt_impl.py`、清除 backend/src/internal 舊解密實作、三條待確認事項照 spec §待確認）、測試數字。
- [ ] **Step 5**：ledger 記帳、通知使用者觸發 opus 終審（或依需求由我派）。

---

## Self-Review 紀錄

- Spec 覆蓋：A→Task 5/6、B→Task 3/4、C→Task 2＋Task 7 Step 1、D→Task 1、E→各 task 測試步＋Task 7。無缺口
- 型別一致：`decrypt_upload(Path, Path)`、`convert_xlsx_to_csv(Path, Path)`、`RAW_STORED_TYPES` 三處跨 task 引用已對齊
- 已知風險記載：轉檔語意差異（Task 3 docstring 允收清單）、fail-loud 路徑（Task 3 壞檔測試釘住）
