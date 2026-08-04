# Internal 環境實作指引

本文件給 **internal 環境的實作者（含 LLM coding agent）** 使用。

upstream（GitHub）的程式碼已經預留三個**接縫**：三個位置刻意留空，等 internal 環境填入實作。
本文件逐一說明要建立哪些檔案、要實作什麼介面、以及如何驗收。

## 前置知識：接縫的運作方式

upstream 是唯一的權威寫入者。internal 環境**不修改共用檔**，只新增下列「internal 獨佔檔」。
同步腳本（`scripts/sync-upstream.sh`）會在每次同步時把整棵樹換成 upstream 版本，
再把 `scripts/internal-owned-paths.txt` 列出的路徑還原回來。

**因此：只要你的檔案在那份清單裡，同步不會覆蓋它；不在清單裡的修改會被無聲抹掉。**

三個接縫互相獨立，可以分開實作、分開上線。

| 任務 | 要建立的檔案 | 啟用方式 |
|---|---|---|
| A. Agent runtime | `deepagent-service/app/agent/runtime/internal_runtime.py` | `AGENT_RUNTIME=internal` |
| B. 前端 library 接入 | `frontend/src/bootstrap/internal.impl.ts` | `VITE_INTERNAL_SCRIPT_URL=<url>` |
| C. 身分 filter | `backend/src/internal/java/.../InternalCurrentUserFilter.java` | `TSSO_ENABLED=true` |

---

## 通用規則（三個任務都適用）

**MUST**

- 只新增上表列出的檔案，以及 `backend/src/internal/java/` 下的其他 internal Java 類別。
- 新增任何 internal 獨佔檔後，MUST 把路徑加進 `scripts/internal-owned-paths.txt`，
  否則下次同步會被刪掉。
- 每個任務完成後跑該節的「驗收」指令，全綠才算完成。

**NEVER**

- NEVER 修改共用檔來讓自己的實作能動。共用檔包括但不限於：
  `deepagent-service/app/agent/runtime/base.py`、`deepagent-service/app/agent/graph.py`、
  `frontend/src/bootstrap/internal.ts`、`frontend/src/main.tsx`、
  `backend/src/main/java/**`、`backend/src/main/resources/application.yml`。
  如果你覺得非改不可，**停下來回報給 upstream 維護者**，由 upstream 開新的接縫——
  在 internal 側改共用檔，下次同步就會消失，而且不會有任何警告。
- NEVER 為了讓程式跑起來而把失敗改成靜默 fallback。接縫的設計刻意選擇「壞掉就大聲壞掉」：
  設定說要用 internal 實作卻找不到它時，MUST 啟動失敗，NEVER 退回預設實作。
- NEVER 在 log 中輸出 api key、token、完整 prompt／HTML、使用者資料內容。

---

## 任務 A：Agent runtime（Python）

### 要做什麼

`deepagent-service` 用 `AgentRuntime` 這個 Protocol 抽出 agent 建構層的三個點。
預設實作用 deepagents + `ChatOpenAI`；internal 環境改用 internal 的 langgraph wrapper。

### 介面（共用檔，NEVER 修改）

`deepagent-service/app/agent/runtime/base.py`：

```python
class AgentRuntime(Protocol):
    def build_model(self) -> BaseChatModel: ...

    def build_checkpointer(self) -> BaseCheckpointSaver: ...

    def build_agent(
        self,
        *,
        model: BaseChatModel,
        tools: list[Any],
        system_prompt: str,
        backend: FilesystemBackend,
        skills: list[str],
        checkpointer: BaseCheckpointSaver,
        middleware: list[Any],
    ) -> CompiledStateGraph: ...
```

三個回傳型別都是 langchain／langgraph 的 base type。internal lib 是 langgraph wrapper，
所以它產出的物件天然滿足這些型別——**不需要自己包一層轉換**。

### 建立檔案

`deepagent-service/app/agent/runtime/internal_runtime.py`，class 名稱 **MUST 是 `InternalRuntime`**
（loader 以這個名字取用，寫錯會啟動失敗）：

```python
"""internal 環境的 AgentRuntime 實作：改用 internal langgraph wrapper 建構 model 與 agent。"""

import logging
import os
from typing import Any

from deepagents.backends.filesystem import FilesystemBackend
from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

# TODO(internal): 換成 internal lib 的實際 import
# from internal_agent_lib import create_internal_agent, InternalChatModel

logger = logging.getLogger(__name__)


class InternalRuntime:
    def build_model(self) -> BaseChatModel:
        # 只記「有無設定」不記 base-url 的值——它是內部位址。NEVER 記 api key 或 token。
        logger.info(
            "building internal chat model model=%s baseUrlSet=%s",
            os.environ.get("AGENT_MODEL", ""),
            bool(os.environ.get("OPENAI_BASE_URL")),
        )
        # TODO(internal): 回傳 internal lib 的 chat model。MUST 是 BaseChatModel 的子類。
        # MUST 設定 streaming=True——事件層靠 astream_events 逐 token 推送，
        # 非串流模型會讓前端等到整輪結束才看到任何東西。
        raise NotImplementedError

    def build_checkpointer(self) -> BaseCheckpointSaver:
        # TODO(internal): 回傳 checkpointer。
        # 每次呼叫 MUST 回傳「新的實例」——測試的 reset 機制靠這點清掉跨測試殘留的對話歷史。
        raise NotImplementedError

    def build_agent(
        self,
        *,
        model: BaseChatModel,
        tools: list[Any],
        system_prompt: str,
        backend: FilesystemBackend,
        skills: list[str],
        checkpointer: BaseCheckpointSaver,
        middleware: list[Any],
    ) -> CompiledStateGraph:
        # TODO(internal): 用 internal lib 建 agent，七個參數全部要傳進去。
        raise NotImplementedError
```

### ⚠️ 七個參數一個都不能漏

`build_agent` 的每個參數都對應一個已經驗證過的行為。少傳任何一個都會造成**功能靜默失效**——
不會拋錯，只會讓產出品質變差，而且很難追。

| 參數 | 漏掉的後果 |
|---|---|
| `backend` | dashboard.html 的「只能整份重寫」不變量失效，模型會開始做局部編輯並產生破碎的 HTML |
| `middleware` | 三個 middleware 全失效：tool call 併發互相覆蓋、資料欄位綁定錯亂、未讀 skill 就寫 dashboard |
| `skills` | 模型看不到 dashboard 撰寫指引，產出品質大幅下降 |
| `checkpointer` | 多輪對話失憶，每輪都像第一次 |
| `system_prompt` | 模型不知道自己在做什麼 |
| `tools` | 模型無法查資料 |

`backend` 參數傳進來的是 deepagents `FilesystemBackend` 的子類。已確認 internal wrapper 接受
這個型別，**原樣傳入即可，NEVER 自行替換成別的檔案層**。

### 啟用

環境變數 `AGENT_RUNTIME=internal`。

未設或設為 `deepagents` 時走預設實作。設為 `internal` 但檔案不存在時，服務**啟動即失敗**並
指出缺少的模組路徑——這是刻意設計，不要試圖繞過。

### 驗收

```bash
cd deepagent-service

# 1. 預設實作不受影響（回歸）
uv run pytest

# 2. internal runtime 能被載入
AGENT_RUNTIME=internal uv run python -c "
from app.agent.runtime import load_runtime
runtime = load_runtime()
print('loaded:', type(runtime).__name__)
"
# 預期輸出：loaded: InternalRuntime

# 3. 三個方法回傳正確型別
AGENT_RUNTIME=internal uv run python -c "
from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from app.agent.runtime import load_runtime
runtime = load_runtime()
assert isinstance(runtime.build_model(), BaseChatModel), 'build_model 型別錯誤'
first = runtime.build_checkpointer()
assert isinstance(first, BaseCheckpointSaver), 'build_checkpointer 型別錯誤'
assert first is not runtime.build_checkpointer(), 'build_checkpointer MUST 每次回傳新實例'
print('all type checks passed')
"

# 4. 端到端：實際跑一輪對話，確認有 token 逐步串流出來、且產出 dashboard.html
```

第 4 項無法自動化，MUST 人工確認：**串流是逐 token 出現而非一次全出**（證明 `streaming=True`
有生效），且 dashboard 是一次寫完整份而非多次局部編輯（證明 `backend` 有傳進去）。

---

## 任務 B：前端 internal library 接入

### 要做什麼

internal 環境的前端需要載入一支 internal library（例如 SSO），並在 React 掛載前完成初始化。

### 運作方式

1. `vite.config.ts` 讀 `VITE_INTERNAL_SCRIPT_URL`，有值時把 `<script src="...">` 注入
   `index.html` 的 `<head>`。這是 classic script（非 module），會在 `main.tsx` 之前同步執行完畢，
   所以 `main.tsx` 執行時該 library 已掛在 `window` 上。
2. `main.tsx` 在掛載前 `await initInternalRuntime()`。
3. `initInternalRuntime()` 用 `import.meta.glob` 偵測 `internal.impl.ts` 是否存在——
   不存在就是 no-op（upstream 的情況），存在就呼叫它的 `initialize()`。

`index.html` 與 `main.tsx` 兩個檔案 internal 側**完全不需要修改**。

### 介面（共用檔，NEVER 修改）

`frontend/src/bootstrap/internal.ts`：

```typescript
export interface InternalBootstrap {
  initialize: () => Promise<void>;
}
```

### 建立檔案

`frontend/src/bootstrap/internal.impl.ts`。**檔名與 `initialize` 這個 export 名稱都是固定的**，
改了就不會被載入（而且不會有錯誤訊息，只會安靜地什麼都不做）。

```typescript
// internal 環境專屬；upstream 不含此檔，由 bootstrap/internal.ts 的 import.meta.glob 偵測後載入。
// library 來自 index.html 注入的 global script（VITE_INTERNAL_SCRIPT_URL）。
import { setUserId } from '@/api/apiClient';

// global script 掛在 window 上，沒有 npm 套件也沒有 .d.ts，型別需自行宣告。
declare global {
  interface Window {
    // TODO(internal): 換成 internal library 實際掛在 window 上的名稱與簽名
    InternalSso?: {
      init(options: { appId: string }): Promise<void>;
      getUserId(): string;
    };
  }
}

export async function initialize(): Promise<void> {
  const sso = window.InternalSso;
  if (!sso) {
    // MUST 中止，NEVER 靜默降級成匿名身分——降級後使用者會以隨機 UUID 開 session，
    // 從畫面上完全看不出異常，而且資料會存到錯的使用者底下。
    throw new Error('internal library 未載入：檢查 VITE_INTERNAL_SCRIPT_URL');
  }

  await sso.init({ appId: import.meta.env.VITE_INTERNAL_APP_ID });
  setUserId(sso.getUserId());
}
```

### ⚠️ 身分覆寫 MUST 走 `setUserId()`

`apiClient.ts` 匯出 `setUserId(userId: string): void`，這是**唯一**被支援的身分覆寫方式。

不要直接寫 `localStorage.setItem('erd_user_id', ...)`。那樣現在也能動，但那個 key 是模組內部
實作細節——upstream 哪天改了 key 名稱，你這邊**不會編譯錯誤、不會有警告**，只會安靜地退回
匿名 UUID。用 `setUserId()` 的話，key 改名時 TypeScript 會直接編譯失敗。

`setUserId()` 寫入後，axios interceptor 與 `agentApi.ts` 的 raw `fetch` 兩條路徑都會帶到新
的 `X-User-Id`（兩者共用同一個讀取函式）。

### 啟用

```bash
VITE_INTERNAL_SCRIPT_URL=https://<internal-host>/sso.js
VITE_INTERNAL_APP_ID=cowork
```

兩者都是 build time 變數，**改值後 MUST 重新 build**，重啟不夠。

### 驗收

```bash
cd frontend

# 1. 既有測試不受影響
npm test

# 2. 未設變數時產出乾淨（確認沒有汙染預設環境）
npm run build && grep -i internal dist/index.html
# 預期：沒有任何輸出

# 3. 設了變數時 script 有被注入
VITE_INTERNAL_SCRIPT_URL=https://example.internal/sso.js npm run build \
  && grep -c "example.internal/sso.js" dist/index.html
# 預期：1
```

第 4 項人工確認：實際開啟頁面，在 DevTools Network 確認 SSO script 有載入，
並確認送出的 API 請求 `X-User-Id` 是真實帳號而非隨機 UUID。

---

## 任務 C：身分 filter（Java）

### 要做什麼

預設環境從 `X-User-Id` header 取身分，缺值時 fallback 成 `local-dev`。
internal 環境改由 internal 的身分機制注入。

### 運作方式

`CurrentUser` 是 `@RequestScope` bean，持有 `userId` 與 `deptId`。

`tsso.enabled=false`（預設）時，共用的 `CurrentUserFilter` 註冊並填入 `userId`。
`tsso.enabled=true` 時，該 filter **不註冊**，改由 internal 側自己的 filter 填。

啟動時會記一行 log 說明走了哪一條路。若 `tsso.enabled=true` 而 internal filter 尚未提供，
會看到這行 WARN：

```
CurrentUserFilter not registered (tsso.enabled=true); identity MUST come from the internal identity filter
```

看到它代表身分**全空**，所有查詢都會失敗。

### 建立檔案

`backend/src/internal/java/com/erd/cowork/context/InternalCurrentUserFilter.java`：

```java
package com.erd.cowork.context;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

/** Populates {@link CurrentUser} from the internal identity mechanism. Registered only when
 *  {@code tsso.enabled} is true; the shared header-based filter is absent in that case. */
@Component
@ConditionalOnProperty(name = "tsso.enabled", havingValue = "true")
@RequiredArgsConstructor
@Slf4j
@Order(-100)
public class InternalCurrentUserFilter extends OncePerRequestFilter {

  private static final String ACTUATOR_PATH_PREFIX = "/actuator/";

  private final CurrentUser currentUser;

  @Override
  protected void doFilterInternal(
      HttpServletRequest request, HttpServletResponse response, FilterChain filterChain)
      throws ServletException, IOException {
    // TODO(internal): 從 internal 身分機制取得 userId 與 deptId 並填入 currentUser。
    // currentUser.setUserId(...);
    // currentUser.setDeptId(...);
    log.debug("resolved identity userId={} deptId={}",
        currentUser.getUserId(), currentUser.getDeptId());
    filterChain.doFilter(request, response);
  }

  @Override
  protected boolean shouldNotFilter(HttpServletRequest request) {
    return request.getRequestURI().startsWith(ACTUATOR_PATH_PREFIX);
  }
}
```

### ⚠️ 三個一定要照抄的細節

**1. `@Order` MUST 大於 `-105`。**
`CurrentUser` 是 `@RequestScope` bean，靠 `RequestContextHolder` 解析，而那個 holder 要等
Spring Boot 的 `OrderedRequestContextFilter`（order `-105`）跑完才會綁到當前執行緒。
你的 filter 若排在它之前，寫入 `CurrentUser` 會拋
`IllegalStateException: No thread-bound request found`。

**這個錯不會在啟動時出現，只會在第一個請求進來時爆。** 照抄 `@Order(-100)` 最安全。

**2. MUST 排除 `/actuator/**`。**
不排除的話每次健康檢查都會觸發一次身分解析，在 k8s 環境下是每幾秒一次的無謂負載，
而且可能因為健康檢查請求沒有身分資訊而拋錯，導致 pod 被判定為不健康而重啟。

**3. `deptId` MUST 一併填入。**
`deptId` 是 internal 專用欄位，共用的 filter 不會填它（永遠是 null）。
只有你的 filter 會填，downstream 若有用到而你沒填，會拿到 null。

### ⚠️ async／SSE 邊界

`CurrentUser` 是 request scope，**不跨執行緒**。agent 串流是在別的執行緒上跑的。

任何要在 async／SSE 邊界之後使用的身分資訊，MUST 在**還在請求執行緒上時**先取出成區域變數
或值物件。直接在 worker thread 讀這個 bean 會拋
`IllegalStateException: No thread-bound request found`。

這條規則對 `deptId` 與 `userId` 同樣適用。

### 上傳檔解密（若 internal 環境的上傳檔是加密的）

介面 `com.erd.cowork.storage.UploadDecryptor` 已在共用檔中定義，預設實作是原樣回傳的
passthrough。internal 環境若需要解密，建立
`backend/src/internal/java/com/erd/cowork/storage/InternalUploadDecryptor.java`：

```java
@Component
@ConditionalOnProperty(name = "erd.upload.decryption.enabled", havingValue = "true")
@RequiredArgsConstructor
public class InternalUploadDecryptor implements UploadDecryptor {

  @Override
  public InputStream decrypt(InputStream ciphertext, String originalFilename) throws IOException {
    // TODO(internal): 呼叫內部解密 API，回傳明文串流。
    throw new UnsupportedOperationException();
  }
}
```

介面契約是 stream-in／stream-out。**若內部 API 無法串流，就在實作內部自行 buffer**——
不要因此改介面。上傳檔可達 2GB，把「要不要整份讀進記憶體」這個決定留在實作內部，
才不會逼所有呼叫端都承擔記憶體成本。

`close()` MUST 是冪等的（呼叫端可能關閉回傳串流與原始串流各一次）。
若實作把明文緩衝到暫存檔，該實作**自己負責刪除它**。

啟用：`ERD_UPLOAD_DECRYPTION_ENABLED=true`。
⚠️ 設為 true 前 MUST 先提供實作 bean，否則啟動時找不到 bean 會失敗。

### 啟用與驗收

```bash
cd backend

# 1. 預設路徑不受影響（回歸）
./mvnw test

# 2. tsso.enabled=true 時啟動，確認 log
TSSO_ENABLED=true ./mvnw spring-boot:run
```

啟動 log MUST **看不到**這行：

```
CurrentUserFilter not registered (tsso.enabled=true); identity MUST come from the internal identity filter
```

看得到就代表你的 filter 沒有被註冊——檢查 `@Component` 與 `@ConditionalOnProperty` 是否正確、
以及 `src/internal/java` 有沒有被 `pom.xml` 的 build-helper 掛成 source root。

第 3 項人工確認：打一個實際 API 請求，確認 `CurrentUser` 拿到正確的 `userId` 與 `deptId`
（可暫時把 `log.debug` 提升為 `log.info` 觀察，確認後改回）。

---

## 完成後 MUST 做的兩件事

### 1. 把新檔案加進還原清單

編輯 `scripts/internal-owned-paths.txt`，確認你建立的每個檔案都被涵蓋：

```
internal/
backend/pom.xml
backend/src/internal
backend/src/main/resources/application-internal.yml
frontend/src/bootstrap/internal.impl.ts
deepagent-service/app/agent/runtime/internal_runtime.py
```

`backend/src/internal` 是整個目錄，所以在它底下新增 Java 檔不需要再改清單。
**其他位置的新檔案都要自己加進去。**

漏加的後果：下次同步時該檔案被**無聲刪除**，沒有任何警告。

### 2. 驗證同步不會弄丟你的檔案

在跑真正的同步之前，先確認清單正確：

```bash
bash scripts/test-sync-upstream.sh
```

七個情境全部 `ok:` 才算通過。

---

## 遇到「非改共用檔不可」時

**停下來，不要改。**

在 internal 側修改共用檔，下次同步會被 `read-tree --reset` 抹掉，而且沒有任何警告——
你會在幾週後發現功能莫名其妙壞了，且很難連回原因。

正確做法是回報給 upstream 維護者，說明：

1. 你需要改哪個共用檔的哪一段
2. 為什麼現有接縫不夠用
3. 你需要的行為是什麼

由 upstream 開一個新接縫、放進共用檔，再經同步流回 internal。
這是一次性成本，之後那個位置就永遠不會再衝突。

同步流程本身見 [`docs/internal-sync.md`](internal-sync.md)。
