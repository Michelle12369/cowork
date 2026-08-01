import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, vi } from 'vitest';
import HtmlCodePanel from './HtmlCodePanel';

// Mock artifactApi so tests can control fetch outcomes without network calls.
vi.mock('@/api/artifactApi', () => ({
  fetchArtifactRawHtml: vi.fn(),
}));

import { fetchArtifactRawHtml } from '@/api/artifactApi';

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

test('HtmlCodePanel: renders label text', () => {
  render(<HtmlCodePanel label="</> 查看 HTML" artifactId="art-1" />);
  expect(screen.getByText('</> 查看 HTML')).toBeInTheDocument();
});

test('HtmlCodePanel: content is NOT in the document by default (collapsed)', () => {
  render(<HtmlCodePanel label="</> HTML" code="<html>hello</html>" />);
  expect(screen.queryByText('<html>hello</html>')).not.toBeInTheDocument();
});

test('HtmlCodePanel: click expands — with code prop shows code text in pre, no fetch called', async () => {
  const user = userEvent.setup();
  render(<HtmlCodePanel label="</> HTML" code="<html>live code</html>" />);

  await user.click(screen.getByText('</> HTML'));

  expect(screen.getByText('<html>live code</html>')).toBeInTheDocument();
  expect(fetchArtifactRawHtml).not.toHaveBeenCalled();
});

test('HtmlCodePanel: autoScroll sets scrollTop to scrollHeight when expanded and code grows', async () => {
  const user = userEvent.setup();
  const { rerender } = render(
    <HtmlCodePanel label="</> 產生中的 HTML" code="<div>start</div>" autoScroll={true} />,
  );

  // Expand the panel
  await user.click(screen.getByText('</> 產生中的 HTML'));

  // Find the pre element and mock scrollHeight
  const pre = document.querySelector('pre');
  expect(pre).not.toBeNull();

  Object.defineProperty(pre!, 'scrollHeight', { configurable: true, value: 500 });
  pre!.scrollTop = 0;

  // Rerender with longer code to trigger the scroll effect
  rerender(
    <HtmlCodePanel
      label="</> 產生中的 HTML"
      code="<div>start</div><div>more content appended</div>"
      autoScroll={true}
    />,
  );

  expect(pre!.scrollTop).toBe(500);
});

test('HtmlCodePanel: with artifactId (no code), expand triggers fetchArtifactRawHtml', async () => {
  vi.mocked(fetchArtifactRawHtml).mockResolvedValue('<html>fetched</html>');
  const user = userEvent.setup();

  render(<HtmlCodePanel label="</> 查看 HTML" artifactId="art-42" />);

  await user.click(screen.getByText('</> 查看 HTML'));

  expect(fetchArtifactRawHtml).toHaveBeenCalledWith('art-42', expect.any(AbortSignal));
});

test('HtmlCodePanel: shows fetched HTML content in pre/code after successful fetch', async () => {
  vi.mocked(fetchArtifactRawHtml).mockResolvedValue('<html>hello world</html>');
  const user = userEvent.setup();

  render(<HtmlCodePanel label="</> 查看 HTML" artifactId="art-1" />);

  await user.click(screen.getByText('</> 查看 HTML'));

  expect(await screen.findByText('<html>hello world</html>')).toBeInTheDocument();
});

test('HtmlCodePanel: shows error text including "無法載入" when fetch fails', async () => {
  vi.mocked(fetchArtifactRawHtml).mockRejectedValue(new Error('Failed to fetch raw HTML: 404'));
  const user = userEvent.setup();

  render(<HtmlCodePanel label="</> 查看 HTML" artifactId="art-missing" />);

  await user.click(screen.getByText('</> 查看 HTML'));

  // Text includes both the contextual label and the keyword checked by MessageBubble tests.
  expect(await screen.findByText(/無法載入/)).toBeInTheDocument();
  expect(await screen.findByText(/此版本無原始碼可檢視/)).toBeInTheDocument();
});

// Stubs native fetch (bypassing the artifactApi mock) to confirm the AbortSignal
// reaching the network layer is never aborted by an effect-deps change mid-request.
test('HtmlCodePanel: expand triggers fetch only once and signal is NOT aborted after resolve', async () => {
  let capturedSignal: AbortSignal | undefined;

  // Stub native fetch so we can inspect the AbortSignal passed through.
  const mockNativeFetch = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
    capturedSignal = init?.signal as AbortSignal | undefined;
    return Promise.resolve(new Response('<html>stub content</html>', { status: 200 }));
  });
  vi.stubGlobal('fetch', mockNativeFetch);

  // Override the api-layer mock to delegate to native fetch so the signal flows through.
  vi.mocked(fetchArtifactRawHtml).mockImplementation(async (id, signal) => {
    const resp = await fetch(`/api/artifacts/${id}/raw`, { signal });
    return resp.text();
  });

  const user = userEvent.setup();
  render(<HtmlCodePanel label="</> 查看 HTML" artifactId="art-signal" />);
  await user.click(screen.getByText('</> 查看 HTML'));

  expect(await screen.findByText('<html>stub content</html>')).toBeInTheDocument();
  // Fetch was called exactly once — no self-abort retry loop.
  expect(mockNativeFetch).toHaveBeenCalledTimes(1);
  // The signal must NOT be aborted after a successful response.
  expect(capturedSignal?.aborted).toBe(false);
});

// requestedRef persists across expand/collapse cycles once the fetch completed.
test('HtmlCodePanel: collapse then re-expand does not re-fetch if data already loaded', async () => {
  vi.mocked(fetchArtifactRawHtml).mockResolvedValue('<html>cached content</html>');
  const user = userEvent.setup();

  render(<HtmlCodePanel label="</> 查看 HTML" artifactId="art-cache" />);

  // First expand — fetches data.
  await user.click(screen.getByText('</> 查看 HTML'));
  expect(await screen.findByText('<html>cached content</html>')).toBeInTheDocument();

  // Collapse.
  await user.click(screen.getByText('</> 查看 HTML'));
  // Re-expand — must show cached data without a second fetch.
  await user.click(screen.getByText('</> 查看 HTML'));

  expect(screen.getByText('<html>cached content</html>')).toBeInTheDocument();
  expect(fetchArtifactRawHtml).toHaveBeenCalledTimes(1);
});

// requestedRef is reset on abort, so collapsing mid-fetch then re-expanding retries.
test('HtmlCodePanel: collapse mid-fetch then re-expand retries the fetch', async () => {
  let resolveFn: (v: string) => void;

  // First call: a promise we control (simulates slow network).
  vi.mocked(fetchArtifactRawHtml).mockImplementationOnce(
    () =>
      new Promise<string>((res) => {
        resolveFn = res;
      }),
  );
  // Second call (retry after re-expand): resolves immediately.
  vi.mocked(fetchArtifactRawHtml).mockResolvedValueOnce('<html>retry content</html>');

  const user = userEvent.setup();
  render(<HtmlCodePanel label="</> 查看 HTML" artifactId="art-retry" />);

  // Expand — fetch starts but hangs.
  await user.click(screen.getByText('</> 查看 HTML'));
  expect(screen.getByText('載入中…')).toBeInTheDocument();

  // Collapse mid-fetch — triggers abort and ref reset.
  await user.click(screen.getByText('</> 查看 HTML'));
  // Resolve the original (now-aborted) promise to ensure no state corruption.
  resolveFn!('<html>stale</html>');

  // Re-expand — should trigger a fresh fetch.
  await user.click(screen.getByText('</> 查看 HTML'));
  expect(await screen.findByText('<html>retry content</html>')).toBeInTheDocument();
  expect(fetchArtifactRawHtml).toHaveBeenCalledTimes(2);
});
