# Artifact 認證交付（axios→srcdoc）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓 artifact HTML 的載入走認證通道（axios 帶 auth header → srcdoc 呈現），internal 環境 SSO lib 可統一驗證；同時補上 ownership 檢查（他人 artifact 一律 404），並以前端注入的 `<meta>` CSP 取代對 response header CSP 的依賴。

**Architecture:** `GET /api/artifacts/{id}` 端點本身不變（仍回 text/html 串流）；改變的是**消費方式**——前端用 apiClient（axios interceptor 自動帶 `X-User-Id`／internal Keycloak header）抓 HTML 字串，注入 CSP `<meta>` 後塞進 `sandbox="allow-scripts"` iframe 的 `srcdoc`。全螢幕改開 app 自己的殼頁（`/?artifactView={id}` query-param 模式，無 router），殼頁內同樣 srcdoc 呈現——絕不 `window.open` blob/原始 HTML（同源紅線）。後端補 ownership 檢查：artifact→session.userId 與 `CoworkContextHolder.userId()` 不符一律 404。

**Tech Stack:** React 18 + TanStack Query（useSuspenseQuery）+ Vitest/RTL；Spring Boot + Mongo（flapdoodle 測試）。

## Global Constraints

- 前端：`React.FC<Props>`、元件結構順序 Props→Hooks→Handlers(useCallback)→Render→export；主要資料抓取 MUST `useSuspenseQuery`；NEVER `any`；function 明確 return type；type-only import 用 `import type`；測試斷言元素級行為
- 後端：constructor injection＋`@RequiredArgsConstructor`；例外放 `com.erd.cowork.exception`；method 簽名 NEVER 傳 userId（用 `CoworkContextHolder`）；測試命名 `methodName_condition_expectedBehavior`
- iframe sandbox 一律 `"allow-scripts"`，NEVER 加 `allow-same-origin`；NEVER `window.open` blob URL 或原始 HTML 內容
- CSP `<meta>` 內容不含 `'self'`（srcdoc 是 opaque origin，`'self'` 匹配不到任何東西）——host 一律用注入時的 `window.location.origin`
- 完成前三側驗證：`cd frontend && npx vitest run`、`cd backend && ./mvnw test` 全綠
- Branch：`feat/artifact-csp`（即 PR #66 的 branch——本計畫擴充該 PR 為「CSP＋認證交付」；既有 CSP header commit 保留，header 對 srcdoc 無效但無害）。**已知與 open PR #63（ArtifactPanel ±89 行）檔案重疊**——後 merge 者 rebase，見 Task 7 PR 描述要求

## 檔案結構

- Create: `frontend/src/utils/artifactCsp.ts`（CSP meta 注入純函式）＋測試
- Create: `frontend/src/components/artifact/ArtifactFrame.tsx`（fetch＋srcdoc iframe 元件）＋測試
- Create: `frontend/src/components/artifact/ArtifactFullscreenPage.tsx`（全螢幕殼頁）＋測試
- Modify: `frontend/src/api/artifactApi.ts`（新增 `fetchArtifactHtml`）＋測試
- Modify: `frontend/src/components/artifact/ArtifactPanel.tsx`（iframe 換 ArtifactFrame、全螢幕改殼頁 URL）＋測試
- Modify: `frontend/src/App.tsx`（artifactView query-param 分流）
- Modify: `backend/.../service/ArtifactService.java`（ownership 檢查）＋測試
- Modify: `backend/.../web/ArtifactController.java`（僅 Javadoc：移除 auth hardening deferred 註記）

---

### Task 1: CSP meta 注入純函式

**Files:**
- Create: `frontend/src/utils/artifactCsp.ts`
- Test: `frontend/src/utils/artifactCsp.test.ts`

**Interfaces:**
- Produces: `injectCspMeta(html: string, origin: string): string`（Task 2/3 消費）

- [ ] **Step 1: 寫失敗測試**

```typescript
import { describe, expect, test } from 'vitest';
import { injectCspMeta } from './artifactCsp';

const ORIGIN = 'http://localhost:3000';

describe('injectCspMeta', () => {
  test('inserts meta right after <head>', () => {
    const html = '<!doctype html><html><head><title>t</title></head><body></body></html>';
    const result = injectCspMeta(html, ORIGIN);
    const headIndex = result.indexOf('<head>');
    const metaIndex = result.indexOf('<meta http-equiv="Content-Security-Policy"');
    expect(metaIndex).toBeGreaterThan(headIndex);
    expect(metaIndex).toBeLessThan(result.indexOf('<title>'));
  });

  test('policy uses origin host-source, never self keyword', () => {
    const result = injectCspMeta('<head></head>', ORIGIN);
    expect(result).toContain(`script-src ${ORIGIN} 'unsafe-inline'`);
    expect(result).toContain("connect-src 'none'");
    expect(result).toContain("default-src 'none'");
    expect(result).not.toContain("'self'");
  });

  test('handles <head> with attributes', () => {
    const result = injectCspMeta('<head lang="en"><script></script></head>', ORIGIN);
    expect(result.indexOf('Content-Security-Policy')).toBeLessThan(result.indexOf('<script>'));
  });

  test('prepends meta when no head tag exists', () => {
    const result = injectCspMeta('<div>bare</div>', ORIGIN);
    expect(result.startsWith('<meta http-equiv="Content-Security-Policy"')).toBe(true);
    expect(result).toContain('<div>bare</div>');
  });
});
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd frontend && npx vitest run src/utils/artifactCsp.test.ts`
Expected: FAIL（module 不存在）

- [ ] **Step 3: 最小實作**

```typescript
/** Artifact srcdoc 的 CSP 以 <meta> 注入：srcdoc 文件不吃 response header CSP，
 *  且 opaque origin 下 'self' 匹配不到任何來源，host 必須用父頁 origin 明寫。 */

const HEAD_OPEN_TAG = /<head[^>]*>/i;

function buildPolicy(origin: string): string {
  return [
    "default-src 'none'",
    `script-src ${origin} 'unsafe-inline'`,
    "style-src 'unsafe-inline'",
    `img-src ${origin} data:`,
    "connect-src 'none'",
  ].join('; ');
}

export function injectCspMeta(html: string, origin: string): string {
  const metaTag = `<meta http-equiv="Content-Security-Policy" content="${buildPolicy(origin)}">`;
  const headMatch = HEAD_OPEN_TAG.exec(html);
  if (!headMatch) {
    return metaTag + html;
  }
  const insertAt = headMatch.index + headMatch[0].length;
  return html.slice(0, insertAt) + metaTag + html.slice(insertAt);
}
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd frontend && npx vitest run src/utils/artifactCsp.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/utils/artifactCsp.ts frontend/src/utils/artifactCsp.test.ts
git commit -m "feat(frontend): artifact srcdoc 用 CSP meta 注入——opaque origin 下以父頁 origin 取代 'self'"
```

---

### Task 2: fetchArtifactHtml API 函式

**Files:**
- Modify: `frontend/src/api/artifactApi.ts`
- Test: `frontend/src/api/artifactApi.test.ts`（追加）

**Interfaces:**
- Consumes: `apiClient`（axios instance，interceptor 自動帶 auth header）
- Produces: `fetchArtifactHtml(artifactId: string, reloadNonce: number): Promise<string>`（Task 3 消費）

- [ ] **Step 1: 寫失敗測試**（追加到現有 `artifactApi.test.ts`；先讀該檔既有 mock 風格，`repairArtifact` 若以 mock adapter/spy 測 apiClient 就沿用同法）

```typescript
import { apiClient } from './apiClient';
import { fetchArtifactHtml } from './artifactApi';

test('fetchArtifactHtml requests artifact html as text via apiClient', async () => {
  const getSpy = vi
    .spyOn(apiClient, 'get')
    .mockResolvedValue({ data: '<html>dashboard</html>' });
  const html = await fetchArtifactHtml('artifact-1', 0);
  expect(html).toBe('<html>dashboard</html>');
  expect(getSpy).toHaveBeenCalledWith('/artifacts/artifact-1', {
    responseType: 'text',
    params: undefined,
  });
  getSpy.mockRestore();
});

test('fetchArtifactHtml appends cache-buster when nonce positive', async () => {
  const getSpy = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: 'x' });
  await fetchArtifactHtml('artifact-1', 3);
  expect(getSpy).toHaveBeenCalledWith('/artifacts/artifact-1', {
    responseType: 'text',
    params: { r: 3 },
  });
  getSpy.mockRestore();
});
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd frontend && npx vitest run src/api/artifactApi.test.ts`
Expected: FAIL（`fetchArtifactHtml` 未匯出）

- [ ] **Step 3: 實作**（加進 `artifactApi.ts`）

```typescript
/** Fetches the assembled artifact HTML through the authenticated apiClient channel
 *  (iframe src 導覽帶不了 auth header，srcdoc 呈現前先在這裡抓)。
 *  responseType 'text' 保證回傳一律是字串；nonce > 0 時附 cache-buster 對齊 repair reload 行為。 */
export async function fetchArtifactHtml(
  artifactId: string,
  reloadNonce: number,
): Promise<string> {
  const response = await apiClient.get<string>(`/artifacts/${artifactId}`, {
    responseType: 'text',
    params: reloadNonce > 0 ? { r: reloadNonce } : undefined,
  });
  return response.data;
}
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd frontend && npx vitest run src/api/artifactApi.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/artifactApi.ts frontend/src/api/artifactApi.test.ts
git commit -m "feat(frontend): fetchArtifactHtml 走 apiClient 認證通道抓 artifact HTML"
```

---

### Task 3: ArtifactFrame 元件（fetch → srcdoc iframe）

**Files:**
- Create: `frontend/src/components/artifact/ArtifactFrame.tsx`
- Test: `frontend/src/components/artifact/ArtifactFrame.test.tsx`

**Interfaces:**
- Consumes: `fetchArtifactHtml(artifactId, reloadNonce)`（Task 2）、`injectCspMeta(html, origin)`（Task 1）
- Produces: `ArtifactFrame: React.FC<ArtifactFrameProps>`，`ArtifactFrameProps = { artifactId: string; reloadNonce: number; title: string; iframeRef?: React.RefObject<HTMLIFrameElement | null> }`（Task 4/5 消費）。**呼叫端 MUST 自行以 `<SuspenseLoader>`＋`<ErrorBoundary>` 包覆**（useSuspenseQuery 會 suspend/throw）

- [ ] **Step 1: 寫失敗測試**

```typescript
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Suspense } from 'react';
import { expect, test, vi } from 'vitest';
import ArtifactFrame from './ArtifactFrame';

vi.mock('@/api/artifactApi', () => ({
  fetchArtifactHtml: vi.fn().mockResolvedValue('<head></head><body>DASH</body>'),
}));

function renderFrame(): void {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <Suspense fallback={<div>loading</div>}>
        <ArtifactFrame artifactId="artifact-1" reloadNonce={0} title="Dash" />
      </Suspense>
    </QueryClientProvider>,
  );
}

test('renders sandboxed iframe whose srcdoc contains fetched html plus CSP meta', async () => {
  renderFrame();
  const iframe = (await screen.findByTitle('Dash')) as HTMLIFrameElement;
  expect(iframe.getAttribute('sandbox')).toBe('allow-scripts');
  expect(iframe.getAttribute('srcdoc')).toContain('DASH');
  expect(iframe.getAttribute('srcdoc')).toContain('Content-Security-Policy');
  expect(iframe.getAttribute('srcdoc')).toContain("connect-src 'none'");
});
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd frontend && npx vitest run src/components/artifact/ArtifactFrame.test.tsx`
Expected: FAIL（module 不存在）

- [ ] **Step 3: 實作**

```typescript
import React, { useMemo } from 'react';
import { useSuspenseQuery } from '@tanstack/react-query';
import { fetchArtifactHtml } from '@/api/artifactApi';
import { injectCspMeta } from '@/utils/artifactCsp';

interface ArtifactFrameProps {
  artifactId: string;
  /** 遞增即重新抓取並重掛 iframe（repair reload 與手動 refresh 共用）。 */
  reloadNonce: number;
  title: string;
  /** 供父層做 postMessage 來源比對（runtime error 回報）。 */
  iframeRef?: React.RefObject<HTMLIFrameElement | null>;
}

/** 經認證通道抓 artifact HTML 再以 srcdoc 呈現——iframe src 導覽帶不了 auth header。
 *  sandbox 維持 allow-scripts（opaque origin）；CSP 以 meta 注入（srcdoc 不吃 response header）。
 *  呼叫端 MUST 以 SuspenseLoader + ErrorBoundary 包覆。 */
const ArtifactFrame: React.FC<ArtifactFrameProps> = ({
  artifactId,
  reloadNonce,
  title,
  iframeRef,
}) => {
  const { data: rawHtml } = useSuspenseQuery({
    queryKey: ['artifact-html', artifactId, reloadNonce],
    queryFn: () => fetchArtifactHtml(artifactId, reloadNonce),
    staleTime: Infinity,
  });

  const secureHtml = useMemo(
    () => injectCspMeta(rawHtml, window.location.origin),
    [rawHtml],
  );

  return (
    <iframe
      ref={iframeRef}
      key={`${artifactId}-${reloadNonce}`}
      srcDoc={secureHtml}
      sandbox="allow-scripts"
      className="absolute inset-0 h-full w-full border-0"
      title={title}
    />
  );
};

export default ArtifactFrame;
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd frontend && npx vitest run src/components/artifact/ArtifactFrame.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/artifact/ArtifactFrame.tsx frontend/src/components/artifact/ArtifactFrame.test.tsx
git commit -m "feat(frontend): ArtifactFrame——認證 fetch + CSP meta + srcdoc sandbox 呈現"
```

---

### Task 4: ArtifactPanel 接上 ArtifactFrame、全螢幕改殼頁 URL

**Files:**
- Modify: `frontend/src/components/artifact/ArtifactPanel.tsx`
- Test: `frontend/src/components/artifact/ArtifactPanel.test.tsx`（更新既有斷言）

**Interfaces:**
- Consumes: `ArtifactFrame`（Task 3）；殼頁 URL 契約 `/?artifactView={artifactId}`（Task 5 實作該頁）
- Produces: ArtifactPanel 對外 Props **不變**

- [ ] **Step 1: 更新測試**（先讀既有 `ArtifactPanel.test.tsx` 的 render helper 與 mock；沿用其建 props 的方式）

  - 既有 `iframe has sandbox="allow-scripts"` 測試：改為 mock `@/api/artifactApi` 的 `fetchArtifactHtml`（同 Task 3 手法），render 後 `await screen.findByTitle(...)`，斷言 `sandbox` 屬性與 `srcdoc` 內含 fetch 回傳內容——**不再斷言 `src` 屬性**
  - 新增測試 `fullscreen button opens shell page url`：mock `window.open`（`vi.spyOn(window, 'open').mockReturnValue(null)`），點擊全螢幕按鈕，斷言呼叫參數為 `('/?artifactView=artifact-1', '_blank', 'noopener,noreferrer')`
  - render helper 需包 `QueryClientProvider` ＋ `Suspense`（同 Task 3 測試）

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd frontend && npx vitest run src/components/artifact/ArtifactPanel.test.tsx`
Expected: FAIL（Panel 仍用 src iframe）

- [ ] **Step 3: 改寫 ArtifactPanel**

  - 刪除 `iframeSrc` 計算（`ArtifactPanel.tsx:179-181`）
  - `handleOpenFullscreen`（`ArtifactPanel.tsx:81-86`）改為：

```typescript
const handleOpenFullscreen = useCallback((): void => {
  if (artifact) {
    // 殼頁（app 自身路由模式）內以 srcdoc 呈現；NEVER 直開 /api HTML 或 blob（同源紅線）。
    window.open(`/?artifactView=${artifact.artifactId}`, '_blank', 'noopener,noreferrer');
  }
}, [artifact]);
```

  - Content 區塊的 `<iframe ...>`（`ArtifactPanel.tsx:238-245`）換成：

```tsx
<ErrorBoundary>
  <SuspenseLoader>
    <ArtifactFrame
      artifactId={artifact.artifactId}
      reloadNonce={combinedNonce}
      title={artifact.title}
      iframeRef={iframeRef}
    />
  </SuspenseLoader>
</ErrorBoundary>
```

  - import：`ArtifactFrame`、`ErrorBoundary`（`@/components/common/ErrorBoundary`）、`SuspenseLoader`（`@/components/common/SuspenseLoader`）
  - `combinedNonce`（`reloadNonce + localRefreshCounter`）與 runtime-error postMessage listener（`ArtifactPanel.tsx:59-79`）維持原樣——`iframeRef` 傳入 ArtifactFrame，`contentWindow` 比對在 srcdoc 下行為相同
  - Props interface 的 `reloadNonce` Javadoc 註解由「appending ?r={nonce} to the src」改為「refetches and remounts the frame」

- [ ] **Step 4: 跑測試確認通過**

Run: `cd frontend && npx vitest run src/components/artifact/ArtifactPanel.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/artifact/ArtifactPanel.tsx frontend/src/components/artifact/ArtifactPanel.test.tsx
git commit -m "feat(frontend): ArtifactPanel 改用 ArtifactFrame srcdoc 呈現，全螢幕走殼頁"
```

---

### Task 5: 全螢幕殼頁（?artifactView query-param 模式）

**Files:**
- Create: `frontend/src/components/artifact/ArtifactFullscreenPage.tsx`
- Modify: `frontend/src/App.tsx`
- Test: `frontend/src/components/artifact/ArtifactFullscreenPage.test.tsx`

**Interfaces:**
- Consumes: `ArtifactFrame`（Task 3）
- Produces: URL 契約 `/?artifactView={artifactId}` → 全螢幕殼頁（Task 4 的 window.open 目標）

- [ ] **Step 1: 寫失敗測試**

```typescript
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { expect, test, vi } from 'vitest';
import ArtifactFullscreenPage from './ArtifactFullscreenPage';

vi.mock('@/api/artifactApi', () => ({
  fetchArtifactHtml: vi.fn().mockResolvedValue('<head></head><body>FULL</body>'),
}));

test('renders full-viewport sandboxed frame for the artifact', async () => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <ArtifactFullscreenPage artifactId="artifact-9" />
    </QueryClientProvider>,
  );
  const iframe = (await screen.findByTitle('Dashboard')) as HTMLIFrameElement;
  expect(iframe.getAttribute('sandbox')).toBe('allow-scripts');
  expect(iframe.getAttribute('srcdoc')).toContain('FULL');
});
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd frontend && npx vitest run src/components/artifact/ArtifactFullscreenPage.test.tsx`
Expected: FAIL

- [ ] **Step 3: 實作殼頁＋App 分流**

`ArtifactFullscreenPage.tsx`：

```typescript
import React from 'react';
import ErrorBoundary from '@/components/common/ErrorBoundary';
import SuspenseLoader from '@/components/common/SuspenseLoader';
import ArtifactFrame from './ArtifactFrame';

interface ArtifactFullscreenPageProps {
  artifactId: string;
}

/** 全螢幕殼頁：app 自身的頁面（可帶 auth header），內部仍以 sandbox srcdoc 關住 artifact。
 *  取代直接 window.open /api HTML——導覽請求帶不了 auth header，blob 又會同源逃逸。 */
const ArtifactFullscreenPage: React.FC<ArtifactFullscreenPageProps> = ({ artifactId }) => (
  <div className="relative h-screen w-screen">
    <ErrorBoundary>
      <SuspenseLoader>
        <ArtifactFrame artifactId={artifactId} reloadNonce={0} title="Dashboard" />
      </SuspenseLoader>
    </ErrorBoundary>
  </div>
);

export default ArtifactFullscreenPage;
```

`App.tsx`：

```typescript
import React from 'react';
import { ConfigProvider } from 'antd';
import { FONT_FAMILY } from '@/theme/fonts';
import CoworkPage from './CoworkPage';
import ArtifactFullscreenPage from './components/artifact/ArtifactFullscreenPage';
import ErrorBoundary from './components/common/ErrorBoundary';
import SuspenseLoader from './components/common/SuspenseLoader';

// 單頁 app 無 router；全螢幕殼頁以 query param 分流（載入時讀一次即可，殼頁無 in-app 導覽）。
const fullscreenArtifactId = new URLSearchParams(window.location.search).get('artifactView');

const App: React.FC = () => (
  <ConfigProvider theme={{ token: { fontFamily: FONT_FAMILY } }}>
    <ErrorBoundary>
      <SuspenseLoader>
        {fullscreenArtifactId ? (
          <ArtifactFullscreenPage artifactId={fullscreenArtifactId} />
        ) : (
          <CoworkPage />
        )}
      </SuspenseLoader>
    </ErrorBoundary>
  </ConfigProvider>
);

export default App;
```

- [ ] **Step 4: 跑測試確認通過（含全套前端）**

Run: `cd frontend && npx vitest run && npx tsc --noEmit`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/artifact/ArtifactFullscreenPage.tsx frontend/src/components/artifact/ArtifactFullscreenPage.test.tsx frontend/src/App.tsx
git commit -m "feat(frontend): 全螢幕殼頁 ?artifactView——認證載入取代直開 /api HTML"
```

---

### Task 6: Backend ownership 檢查（他人 artifact 一律 404）

**Files:**
- Modify: `backend/src/main/java/com/erd/cowork/service/ArtifactService.java`
- Modify: `backend/src/main/java/com/erd/cowork/web/ArtifactController.java`（僅 Javadoc）
- Test: `backend/src/test/java/com/erd/cowork/service/ArtifactServiceTest.java`（既有檔追加；若無則依鄰近 service 測試風格建立）

**Interfaces:**
- Consumes: `CoworkContextHolder.userId()`（既有）、`ChatSessionRepository`（既有）、`Artifact.getSessionId()`（既有）
- Produces: `getHtmlStream`／`getRawHtml` 對非擁有者拋 `NotFoundException`（對外仍是 404，與不存在無法區分）

- [ ] **Step 1: 寫失敗測試**（先讀既有 ArtifactService 測試的資料建置手法——嵌入式 Mongo 共用 DB，斷言 MUST 按唯一 id scope，NEVER 全域計數）

```java
@Test
void getHtmlStream_artifactOwnedByAnotherUser_throwsNotFound() {
  // 建 session（userId = "owner-user"）＋掛在其下的 artifact（htmlStorageKey 有值），
  // 以 CoworkContextHolder 設定 caller = "other-user"（比照既有測試的 context 設定手法，
  // 測試結束 MUST 清 context 避免污染共用 JVM）
  assertThatThrownBy(() -> artifactService.getHtmlStream(artifactId))
      .isInstanceOf(NotFoundException.class);
}

@Test
void getRawHtml_artifactOwnedByAnotherUser_throwsNotFound() { /* 同上手法 */ }

@Test
void getHtmlStream_ownArtifact_returnsStream() {
  // caller = "owner-user" 時正常回傳（既有 happy-path 若已涵蓋則只需補 context 設定）
}
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd backend && ./mvnw test -Dtest=ArtifactServiceTest`
Expected: 新測試 FAIL（現況無 ownership 檢查）

- [ ] **Step 3: 實作**

`ArtifactService`（`@RequiredArgsConstructor` 追加 `private final ChatSessionRepository chatSessions;`——確認欄位名不與既有衝突）：

```java
/**
 * Ownership guard: artifact 經 sessionId 歸屬到 session.userId，與呼叫者不符一律當作不存在
 * (404)——不回 403，避免洩漏 artifact id 存在性。session 缺失視同不符。
 */
private void assertOwnedByCaller(Artifact artifact) {
  String callerUserId = CoworkContextHolder.userId();
  boolean owned =
      chatSessions
          .findById(artifact.getSessionId())
          .map(session -> session.getUserId().equals(callerUserId))
          .orElse(false);
  if (!owned) {
    throw new NotFoundException("Artifact not found: " + artifact.getId());
  }
}
```

在 `getHtmlStream`（`ArtifactService.java:53`）與 `getRawHtml`（`ArtifactService.java:78`）取得 `Artifact` 後、任何使用前呼叫 `assertOwnedByCaller(artifact)`。
`ArtifactController.java:50` 與 `:61` Javadoc 的「Unguessable UUID capability URL; auth hardening deferred」改為「Requires authenticated caller; non-owner access returns 404」（`@Operation` description 同步改）。

**注意**：`CoworkContextHolder.userId()` 若在無 context 時拋例外或回 null，依其實際行為處理——null caller 一律視為不符（404），NEVER NPE。

- [ ] **Step 4: 跑全套後端測試**

Run: `cd backend && ./mvnw test`
Expected: 全 PASS（既有 controller slice 測試 mock service，不受影響；若有直打 service 的整合測試未設 context 而轉紅，補 owner context 而非放寬檢查）

- [ ] **Step 5: Commit**

```bash
git add backend/src/main/java/com/erd/cowork/service/ArtifactService.java backend/src/main/java/com/erd/cowork/web/ArtifactController.java backend/src/test/java/com/erd/cowork/service/ArtifactServiceTest.java
git commit -m "feat(backend): artifact 讀取補 ownership 檢查——他人資源一律 404"
```

---

### Task 7: 三側驗證＋PR

- [ ] **Step 1: 全套驗證**

```bash
cd frontend && npx vitest run && npx tsc --noEmit
cd ../backend && ./mvnw test
```
Expected: 全綠（deepagent 未動，不需跑）

- [ ] **Step 2: 實機驗證**（依 superpowers:verification-before-completion）

啟動前後端，確認：(1) dashboard 正常顯示（vendor script 從前端 origin 載入成功）；(2) DevTools Network 看 `/api/artifacts/{id}` 請求帶 `X-User-Id` header；(3) 全螢幕開新分頁正常；(4) 瀏覽器直貼 `/api/artifacts/{id}` URL 得 404（ownership 生效）；(5) iframe 內 console 無 CSP 誤殺（`connect-src 'none'` 不影響現有 dashboard——它們不 fetch）

- [ ] **Step 3: 更新 PR #66**

push 後以 `gh pr edit 66` 更新標題與描述（本 branch 即 PR #66）。描述 MUST 含：
- 動機：iframe src／window.open 導覽帶不了 auth header，internal SSO lib 驗不到 artifact 端點；改 axios→srcdoc 使其成為普通認證 API
- 安全不變式：sandbox 維持 `allow-scripts`（NEVER `allow-same-origin`）；CSP 改由前端 meta 注入（含理由：srcdoc 不吃 header CSP、opaque origin 下 `'self'` 失效）；全螢幕走殼頁（NEVER blob/直開）
- Ownership：他人 artifact 404
- **與 open PR 的交互**：#63（replay）——ArtifactPanel 衝突已知，#63 rebase 時其 refresh srcdoc 路徑應改用本支的 `injectCspMeta`＋`ArtifactFrame` 呈現縫；既有 CSP header commit（`9f5a96b`）保留——header 對 srcdoc 無效但無害，仍保護任何直開 URL 的殘餘路徑
- 部署備忘：artifact HTML 現量級 ≤ 700KB（19 份實測），瀏覽器端整包緩衝無感；`/vendor/*` 由前端 origin 靜態供應，不經 SSO 保護的 backend 路徑，internal 無需額外豁免

---

## Self-Review 紀錄

- Spec 覆蓋：header 帶不到→axios（T2/T3）、CSP 失效→meta（T1）、全螢幕紅線→殼頁（T4/T5）、驗證缺口→ownership（T6）、PR 62/63/66 交互→T7 PR 描述。無缺口
- 型別一致：`fetchArtifactHtml(artifactId, reloadNonce)`、`injectCspMeta(html, origin)`、`ArtifactFrameProps` 三處簽名 T1–T5 一致
- 已知風險（記入 PR）：srcdoc 失去邊下邊渲染（現量級無感）；`useSuspenseQuery` 預設 gcTime 快取 HTML 字串（量級小，可接受）
