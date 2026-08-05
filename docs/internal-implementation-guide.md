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
| B. 前端 library 接入 | `frontend/index.html`（加 script 標籤）＋ `frontend/src/bootstrap/internal.impl.ts` | 三支 script 標籤存在即生效 |
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
  `backend/src/main/java/**`。
  如果你覺得非改不可，**停下來回報給 upstream 維護者**，由 upstream 開新的接縫——
  在 internal 側改共用檔，下次同步就會消失，而且不會有任何警告。
  `backend/src/main/resources/application.properties` 是例外：它是雙邊擁有檔，
  internal 側可直接編輯（例如設定 `tsso.enabled=true`、`erd.upload.decryption.enabled=true`），
  同步時會還原 internal 版本；上游若也動過它，同步 commit 的 body 會提示需要人工調和。
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

    def build_langfuse(self, settings: "Settings") -> Any | None:
        """建構並回傳 Langfuse client（建構子本身會註冊全域 client，CallbackHandler 依賴它），
        回 None＝tracing 關閉。internal 覆寫以完整接管建構（自家 host/auth/mask/wrapper）。
        取用端一律 getattr fallback——結構實作可不提供此方法，OSS 預設建構路徑接手。"""
        ...
```

三個建構方法的回傳型別都是 langchain／langgraph 的 base type。internal lib 是 langgraph
wrapper，所以它產出的物件天然滿足這些型別——**不需要自己包一層轉換**。

`build_langfuse` 是選用方法（見本節最後「Langfuse seam」小節）：`AgentRuntime` 只是
Protocol，結構實作不提供這個方法也符合型別；取用端一律用 `getattr(runtime,
"build_langfuse", None)` 取用，缺方法時等同回傳 `None`，落到 OSS 預設建構路徑。

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

    def build_langfuse(self, settings):
        from internal_lib.tracing import build_internal_langfuse  # internal lib

        return build_internal_langfuse()  # 內含 internal host/auth/mask
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

### Langfuse seam：`build_langfuse`

`deepagent-service` 啟動時（FastAPI lifespan）會呼叫 `getattr(runtime, "build_langfuse",
None)`：若 runtime 有提供這個方法，就完整交給它接管建構，`app/agent/tracing.py` 不再自己
組 `Langfuse(...)` 的任何參數——host、auth、mask、甚至用不用官方 SDK 的 wrapper，都由這個
方法自己決定。

**contract**：這個方法 MUST 建構並回傳一個真正的 `langfuse.Langfuse` 實例（它的建構子本身
會註冊全域 client，`CallbackHandler()` 依賴這個全域 client 才能運作），或回傳 `None` 代表
tracing 關閉。internal 環境有自己的 host／認證／遮罩邏輯時，在 `InternalRuntime` 加這個
方法：

```python
def build_langfuse(self, settings):
    from internal_lib.tracing import build_internal_langfuse  # internal lib

    return build_internal_langfuse()  # 內含 internal host/auth/mask
```

**未實作此方法時**（`AgentRuntime` 是 Protocol，`build_langfuse` 屬選用方法，internal 側結構
實作不提供它也符合型別，取用端一律 `getattr` fallback）：落到 OSS 預設建構路徑——讀
one.properties／env 的 `LANGFUSE_PUBLIC_KEY`／`LANGFUSE_SECRET_KEY`／`LANGFUSE_HOST`，兩者
皆空即 no-op，只設其中一個則啟動即失敗，皆有值則顯式 `Langfuse(..., mask=None)`。

### one.properties：設定來源層疊優先序

`deepagent-service` 的設定（`app/config.py`）在 internal 環境可額外掛載檔案，與 env var
疊加而非互斥：

- 預設路徑為**啟動 CWD 下的 `one.properties`**（本機開發在 `deepagent-service/` 目錄啟動即自動生效）；internal 環境掛載到其他位置（例如 `/config/one.properties`）時 **MUST 顯式設 `ONE_PROPERTIES_PATH` 指向掛載路徑**
  （此 key 永遠只從 env 讀，不能放進檔案本身）。
- **優先序：env > one.properties > 欄位預設**。檔案存在時作為基底層：`Settings` 模型裡
  的每一個欄位，若 env 有設值就覆寫檔案值，env 沒設就落到檔案值，兩者都沒設則落到程式
  內建預設值——與欄位是否帶 `AGENT_*`／`OPENAI_*`／`LANGFUSE_*` 這類前綴無關。因此檔案
  **不必**完整列出所有欄位，只需列出要偏離預設值、且不打算逐一用 env 覆寫的 key；本機
  key 清單/型別/預設以 `deepagent-service/app/config.py` 的 `Settings` 欄位為準。
- 檔案不存在時照舊只讀 env（OSS 預設路徑）。
- 檔案格式是 Java 式 `KEY=value`：空行與 `#` 開頭的行會跳過；非空行若找不到 `=`，
  服務**啟動即失敗**並在錯誤訊息標出行號，NEVER 靜默跳過壞行。
- key 名稱與對應 env var **完全同名**（皆為大寫底線，例如 `AGENT_MODEL`、
  `LANGFUSE_SECRET_KEY`），不需要另外對照表。

---

## 任務 B：前端 internal library 接入

### 要做什麼

internal 環境的前端需要載入一支 internal library（例如 SSO），並在 React 掛載前完成初始化。
分兩步：**步驟一**在 `index.html` 掛 script 標籤，**步驟二**在 `internal.impl.ts` 做
guard + init + 註冊 provider。

### 步驟一：編輯 `frontend/index.html`

內部 library 是**三支彼此有載入順序相依的 classic script**，直接在 `<head>` 依相依順序
加三支：

```html
<head>
  ...
  <script src="https://internal.example/lib1.js"></script>
  <script src="https://internal.example/lib2.js"></script>
  <script src="https://internal.example/lib3.js"></script>
</head>
```

三個 MUST：

- **標籤順序即載入順序**——三支互有相依，順序排錯會讓後面的 script 找不到前面該掛好的
  全域物件而噴錯。
- **一律 classic script**：NEVER 加 `type="module"`，也 NEVER 加 `async`/`defer`。
  classic、非 async/defer 的 script 會依文件順序**同步**執行完畢才繼續解析後面的節點，
  這保證三支 library 在 `<body>` 底部的 `main.tsx` 開始跑之前就已經全部掛到 `window` 上；
  一旦變成 module 或加了 async/defer，執行時機就不再保證早於 `main.tsx`，`internal.impl.ts`
  讀 `window.xxx` 時可能還是 `undefined`。
- `index.html` 是**雙邊擁有檔**：已在 `scripts/internal-owned-paths.txt`（同步時會把它從
  upstream 整棵樹取代後還原回 internal 版本），也在 `scripts/manual-merge-paths.txt`
  （upstream 若改動了 `index.html`，同步 commit 的 body 會多一行「需人工調和」提示，
  需要人工比對兩邊版本決定怎麼合併，不會被同步腳本自動處理）。

### 步驟二：建立 `frontend/src/bootstrap/internal.impl.ts`

`index.html` 已經把 script 載完，這一步**不再自己載入 script**，只做 guard + init +
註冊 provider。**檔名與 `initialize` 這個 export 名稱都是固定的**，改了就不會被
`internal.ts` 的 `import.meta.glob` 偵測到（而且不會有錯誤訊息，只會安靜地什麼都不做）。

介面（共用檔，NEVER 修改）—— `frontend/src/bootstrap/internal.ts`：

```typescript
export interface InternalBootstrap {
  initialize: () => Promise<void>;
}
```

`internal.impl.ts` 範例：

```typescript
// frontend/src/bootstrap/internal.impl.ts
// internal 環境專屬；upstream 不含此檔，由 bootstrap/internal.ts 的 import.meta.glob 偵測後載入。
import { setAuthHeaderProvider } from '@/api/apiClient';

// classic script 的 top-level function 會掛到 window；若 lib 用 let/const 宣告或包在 IIFE 裡
// 則不會，屆時改用 `declare global { function initKeycloak(...): ... }` 並以 typeof 檢查。
declare global {
  interface Window {
    // TODO(internal): 換成 lib 實際的函式名稱與簽名
    initKeycloak?: (options?: { onLoad?: string }) => Promise<void>;
    getKeycloakToken?: () => { token: string; idToken: string };
  }
}

export async function initialize(): Promise<void> {
  const { initKeycloak, getKeycloakToken } = window;
  if (!initKeycloak || !getKeycloakToken) {
    // MUST 中止，NEVER 靜默降級成匿名身分——降級後會以隨機 UUID 開 session，畫面上看不出異常。
    throw new Error('internal library 未載入：檢查 index.html 的三支 script 標籤與順序');
  }

  await initKeycloak({ onLoad: 'login-required' });

  // 讀值寫在 closure 內：provider 每次請求都會被呼叫，lib 背景刷新後自然拿到新 token。
  setAuthHeaderProvider(() => {
    const { token, idToken } = getKeycloakToken();
    return {
      '<header-name-1>': token,
      '<header-name-2>': idToken,
    };
  });
}
```

### ⚠️ 若 `window.xxx` 是 `undefined`

classic script 中 top-level 的 `function`/`var` 宣告會掛到 `window`，但 `let`/`const`、
IIFE 包裝、`type="module"` 都**不會**。若 lib 是這幾種形式之一，`internal.impl.ts` 開頭的
`declare global { interface Window { ... } }` 寫法讀不到值，改用：

```typescript
declare global {
  function initKeycloak(options?: { onLoad?: string }): Promise<void>;
}
```

並以 `typeof initKeycloak !== 'function'` 做 guard。

排查時在 DevTools Console 執行：

```js
typeof initKeycloak
Object.keys(window).filter((key) => /keycloak/i.test(key))
```

前者確認函式本身有沒有掛上去，後者列出 lib 實際掛在 `window` 上的所有名稱（常見情況是
lib 用了跟預期不同的命名空間，例如把整個 API 包在 `window.Keycloak` 底下）。

### ⚠️ 身分覆寫 MUST 走 `setAuthHeaderProvider()`

`apiClient.ts` 匯出 `setAuthHeaderProvider(next: AuthHeaderProvider): void`，這是**唯一**被
支援的身分覆寫方式；`AuthHeaderProvider` 是 `() => Record<string, string>`。

provider 回傳的 header **完全取代**預設的 `X-User-Id`——upstream 不知道也不需要知道 internal
實際的 header 名稱叫什麼，provider 回傳什麼就送什麼。

**provider MUST 每次請求都被呼叫**，NEVER 在 `initialize()` 裡先呼叫一次、把結果存成閉包變數
再回傳固定值。internal 的 library 會在背景刷新 token，快取住的話會在 token 過期後開始 401，
而且只在 internal 環境發生，本機用預設 provider 測不出來。直接在 provider 裡呼叫 lib 的
token getter 才能保證每次請求都拿到當下有效的 token。

`setAuthHeaderProvider()` 生效後，axios interceptor 與 `agentApi.ts` 的 raw `fetch` 兩條路徑
都會帶到新的 header（兩者共用同一個 `getAuthHeaders()` 讀取函式）。

### 啟用

不需要任何環境變數——三支 script 標籤存在即生效。`initKeycloak` 若需要 appId 之類參數，
直接在 `internal.impl.ts` 內寫定（它是 internal 獨佔檔，不走 env、不受同步影響）。

### 驗收

```bash
cd frontend

# 1. 既有測試不受影響
npm test

# 2. build 正常產出
npm run build
```

第 3 項人工確認：開啟 `frontend/index.html`（或 build 後的 `dist/index.html`），確認三支
script 標籤依相依順序出現在 `<head>`；實際開啟頁面，在 DevTools Network 確認三支 internal
library 依序載入，並確認送出的 API 請求帶著 internal 的認證 header（而非預設的
`X-User-Id`）。

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

## Internal 物件儲存接線（S3）

儲存走雙路線：`local`（磁碟，測試與本機裸跑預設）與 `s3`（internal 現行路線，因為 internal 環境不提供 RWX PVC，只提供 S3-compatible 物件儲存）。這**不是**一個接縫——`application.properties` 已是雙邊擁有檔（見前述通用規則），`erd.storage.s3.*` 設定與 `S3FileStorage`／`S3WorkspaceStore` 兩份實作都在共用檔中，internal 側只需要**填值**，不需要新增 internal 獨佔檔。

### backend env

在 internal 部署環境設定：

```
ERD_STORAGE_TYPE=s3
ERD_STORAGE_S3_ENDPOINT=<internal 物件儲存 endpoint>
ERD_STORAGE_S3_REGION=<region，MinIO/Ceph 風格可留 us-east-1>
ERD_STORAGE_S3_BUCKET=<bucket 名稱>
ERD_STORAGE_S3_PATH_STYLE=true
AWS_ACCESS_KEY_ID=<access key>
AWS_SECRET_ACCESS_KEY=<secret key>
```

`AWS_ACCESS_KEY_ID`／`AWS_SECRET_ACCESS_KEY` 走 AWS SDK v2 的 default credentials chain，**NEVER** 放進 `application.properties` 或任何 properties 檔案——一律 env。完整 key 清單以 `backend/src/main/resources/application.properties` 的 `erd.storage.*` 區塊為準。

### deepagent one.properties

`deepagent-service` 走同一套「env > one.properties > 欄位預設」層疊（見前面「one.properties：設定來源層疊優先序」節）。internal 部署掛載 `one.properties`（或設對應 env）時加入：

```
STORAGE_BACKEND=s3
S3_ENDPOINT=<與 backend 同一個物件儲存 endpoint>
S3_REGION=us-east-1
S3_BUCKET=<與 backend 同一個 bucket>
S3_ACCESS_KEY=<access key>
S3_SECRET_KEY=<secret key>
S3_WORKSPACE_PREFIX=workspace
```

deepagent 與 backend **必須共用同一組 credentials、同一個 bucket**——deepagent 用自己的 boto3 client 讀 backend 寫入的 `uploads/` 物件（storageKey 交棒，見 `docs/architecture.md`「上傳檔交棒」節），不走 presigned URL。key 名稱、型別、預設值以 `deepagent-service/app/config.py` 的 `Settings` 欄位為準，此處僅列必填項。

### write-once 規範如何被 generation 模型滿足

internal 治理規範禁止同一個 object key 重複 PUT。上傳檔／artifact 兩類本來就靠 `StorageKeyUtils.buildKey()` 每次產生含 UUID 的新 key 天然合規；workspace 較特別——同一個 session 每輪對話都要「換版」，`S3WorkspaceStore` 用 **generation 前綴**（`gen-{epochMillis13}-{隨機8碼hex}/`）而非覆寫既有物件解決：每次 persist 都是一整組全新 key，從不覆寫，`_complete` 標記最後寫入保證讀端只會拿到「完整一代」或「視為不存在」，沒有中間狀態。舊 generation 保留最新 2 個、其餘由 backend 清理（`WorkspacePurger`）。

### bucket 需求

單一 bucket 即可（backend／deepagent 用同一個），**不需要開 versioning**——write-once 是靠 key 設計（UUID／generation 前綴）滿足的，不倚賴 bucket 層的版本機制。需要 path-style access（`ERD_STORAGE_S3_PATH_STYLE=true`），對齊 MinIO/Ceph 風格的 internal 物件儲存。

---

## 完成後 MUST 做的兩件事

### 1. 把新檔案加進還原清單

編輯 `scripts/internal-owned-paths.txt`，確認你建立的每個檔案都被涵蓋：

```
internal/
.env.internal.example
backend/pom.xml
backend/src/internal
backend/src/main/resources/application.properties
frontend/index.html
frontend/src/bootstrap/internal.impl.ts
deepagent-service/app/agent/runtime/internal_runtime.py
```

`backend/src/internal` 是整個目錄，所以在它底下新增 Java 檔不需要再改清單。
**其他位置的新檔案都要自己加進去。**

⚠️ `.env.internal.example` 是 internal 端的環境變數範本（只有變數名與說明、不含值）。
`.gitignore` 已特別放行它，因此可以正常 `git add`。但它**在 upstream 不存在**，
所以 MUST 在第一次同步之前先 commit 到 `develop`——清單上的路徑在 `develop` 找不到時，
`git checkout develop -- <path>` 會失敗並中止整個同步。

`backend/pom.xml`、`frontend/index.html`、`backend/src/main/resources/application.properties`
同時也在 `scripts/manual-merge-paths.txt`——它們是雙邊擁有檔，upstream 改動時同步腳本會在
commit body 提示人工調和，不會被自動覆蓋或自動合併。

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
