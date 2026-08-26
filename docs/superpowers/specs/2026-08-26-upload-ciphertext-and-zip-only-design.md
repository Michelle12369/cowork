# 上傳 xlsx 密文直存（解密搬 Python）＋ workspace 快照 zip-only — 設計

> 狀態：使用者已核可方向（2026-08-26 討論定案）。本 spec 與 PR #67 同支（`feat/workspace-zip-snapshot`）。

## 動機

1. **MinIO 靜態必須是密文**（internal 合規）：加密到達的上傳檔（現況僅 xlsx）不准解密後落地。現行管線在 Java 上傳時解密→轉 CSV→存明文，違反此要求。
2. **PR #67 簡化**：workspace 快照 zip 化已落地，舊 per-file 代的向後相容（混合掃描/舊代單檔下載/混合清理）不再需要——internal 尚未部署快照功能，無線上舊代要顧。

## 範圍定案（討論結論的紀錄）

- 密文要求**只限加密到達的檔**（internal 的 xlsx）；csv 明文到達、明文儲存，**現狀完全不動**。
- internal 只跑 deepagent（LangGraph analysis）線——wire 只傳 alias＋path，不吃 Java 上傳期 profile，故 **Java 可完全退出解密與 xlsx 解析**。
- `erd.upload.decryption.enabled` **整個刪除**：xlsx 處理環境無關——Java 一律原樣直存。dev 與 internal 走同一條 Python 管線，唯一差異是解密接縫實作（dev＝identity、internal＝真解密）。
- **Python 以副檔名（`.xlsx`）判定**需解密＋轉檔，不加 wire flag／DB 欄位。成立前提：改動後儲存裡 `.xlsx` 只有一種來源（原樣直存的上傳）；dev 明文 xlsx 也走同管線（identity 解密）。**文件化不變式（鏡像註解）**：Java `ENCRYPTED_UPLOAD_TYPES`（更名後見下）若增加任何型別（尤其 csv），副檔名推斷即失效，MUST 改 per-file metadata——Java 常數與 Python 判定處互相指名對方。

## 設計

### A. Java 上傳管線（backend）

- `FileService.upload`：型別 ∈ `RAW_STORED_TYPES`（原 `ENCRYPTED_UPLOAD_TYPES` 更名，仍＝`{"xlsx"}`，語意改為「原樣直存、由 deepagent 下載時處理」）→ 上傳 bytes **原樣**寫入 storage（storage key 保留原始檔名，`.xlsx` 結尾）、跳過 normalize 與 parsing、`metadataJson` 留 null、`storedType="xlsx"`。csv 路徑（normalize 編碼整理＋parsing＋profile）不變。
- 移除：`UploadDecryptor` 介面、`PassthroughUploadDecryptor`、`erd.upload.decryption.*` 所有綁定與引用。internal 側的解密實作（`backend/src/internal`）隨下次同步由 internal 自行清除——PR 描述註記。
- Null 安全：`metadataJson` 為 null 的檔案，`AgentOrchestrator`（`readValue(metadataJson)` 處）與 `PromptAssembler`（llm-api 線）MUST 跳過 profile 段而非 NPE；`FileDto.rowCount` 已 nullable。
- **Accepted trade-off——llm-api 線退化為 csv-only**：xlsx 在該線兩段皆斷——prompt 無 schema/樣本（上傳期不解析），且 `ArtifactAssembler` 注入端讀到密文解析失敗（internal；沿既有 fail-soft 逐檔跳過＋warning，dev 明文 xlsx 雖可解析但模型無 schema 名存實亡）。xlsx 僅 deepagent 線可用（Python 端自行解密＋get_schema）。csv 在 llm-api 線不受影響。
- xlsx 200MB 上限驗證保留（對密文 bytes 計）。

### B. Python 下載管線（deepagent `source_cache.py`）

- `resolve_source_path`：raw_path 以 `.xlsx` 結尾（s3 與 local 兩分支皆同）→
  1. 下載/複製密文到暫存
  2. **解密接縫** `decrypt_upload(ciphertext_path, plaintext_path)`（見 C）
  3. **xlsx→CSV 轉檔**：openpyxl read-only streaming、僅第一張 sheet、輸出格式化文字盡量比照 Java POI `DataFormatter` 語意（已知差異列入測試允收清單）
  4. cache 落 `.csv`（cache key＝原 key 副檔名換 `.csv`）；temp+rename 原子落檔沿用既有 `_fill_cache`
- cache 命中（`.csv` 已在）→ 跳過下載/解密/轉檔全程。`.csv` 源路徑行為零變化（釘測試）。
- 本地 cache 為明文：合規邊界＝「持久共享儲存（MinIO）密文；分析容器暫存磁碟明文」——**此假設需 internal 點頭**，spec 記載待確認。
- engine 純度規則更新：stdlib＋boto3＋**openpyxl**（僅 source_cache 轉檔用）。

### C. 解密接縫（整檔複寫，`upload_decrypt.py` 本身列入 internal 獨佔路徑）

- repo 內 `app/engine/upload_decrypt.py`：定義 `decrypt_upload(src: Path, dst: Path) -> None` 的預設實作＝identity（copy），並呼叫一次 `request_context.require_user_id()` 作為「userId 在此點確實可取」的活測試（值本身 identity 路徑不需要，但形狀先驗證起來）。
- `scripts/internal-owned-paths.txt` 直接列 `deepagent-service/app/engine/upload_decrypt.py` 本檔——不是 import-if-exists 那種另開 `_impl` 模組的接縫，internal 同步時整檔覆蓋這支檔案，真解密實作取代 identity 版。部署順序註記：internal 下次同步前先備妥真解密版本，否則同步當下會先落地 repo 版（identity），密文 xlsx 會被當明文轉檔失敗（fail loud，非 silent garbage）。
- 新增 `app/engine/request_context.py`（repo 共用、非 internal 獨佔）：以 `contextvars.ContextVar` 傳遞當前請求的 userId/sessionId，`require_user_id()`/`require_session_id()` 未設定時丟 `LookupError`。`ChatTurn.__aenter__`（`/chat`）與 `run_repair`（`/repair`）在請求最初就呼叫 `set_request_identity(...)`，並保證所有退出路徑（正常完成、提前 return、`__aenter__`/`prepare()` 失敗）都會 `reset_request_identity(...)`——contextvar 不跨 thread，若未來 source 解析被 offload 到 `run_in_executor`/`to_thread`，`require_user_id()` 會 fail loud 而非悄悄回空字串。internal 版 `decrypt_upload` 呼叫 `require_user_id()` 取得 userId 當解密 API payload 之一。
- 憑證/協定由 internal 實作自理；接縫只交換檔案路徑（＋ contextvar 帶的 userId）。

### D. PR #67 zip-only（deepagent `workspace_store.py`）

- 移除混合掃描：prepare 只認 `gen-*.zip`（物件在＝complete）；舊 per-file 代與 `_complete` marker 邏輯、舊代單檔下載分支、混合清理規則全部刪除。
- `download_file`：僅 zip 代路徑（zip 內 entry 解出／缺失回 None）；skills 前綴 per-file 拉取不動。
- zip-slip 防護、write-once 新 key、原子寫入（PR #67 已落地）不變。
- 對應測試同步刪減/改寫。

### E. 測試策略

- **Java**：xlsx 直存 byte-identical＋metadataJson null＋不呼叫 normalizer/parsing（mock 驗證）；csv 路徑回歸不變；null profile 的 orchestrator/PromptAssembler 不炸。
- **Python**：明文 xlsx fixture 走完「下載→identity 解密→轉檔→cache」全程（dev 即 internal 同路徑的證明）；轉檔語意比對（同 fixture 與 Java 舊轉檔輸出對照，差異允收清單）；cache 命中不重轉；`.csv` 路徑零變化；zip-only 後的 workspace_store 測試全面改寫（混合情境測試刪除）。
- 三側全綠＋既有 e2e。

## 待確認（不阻塞實作，PR 描述標註）

1. internal 對「本地 sources-cache 明文」的合規認可。
2. internal 解密 API 從 deepagent 網段可達與憑證形狀（實作放 internal 側）。
3. internal 同步時：把 `deepagent-service/app/engine/upload_decrypt.py` 整檔換成真解密實作（呼叫 `request_context.require_user_id()` 取 userId 當 payload）、清除 `backend/src/internal` 舊解密實作。
