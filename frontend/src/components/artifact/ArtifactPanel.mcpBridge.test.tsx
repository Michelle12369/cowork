import { render, act, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Suspense } from 'react';
import { AxiosError, type AxiosResponse } from 'axios';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import ArtifactPanel from './ArtifactPanel';
import type { Props as ArtifactPanelProps } from './ArtifactPanel';
import * as artifactApiModule from '@/api/artifactApi';
import { MCP_BRIDGE_TIMEOUT_MS } from '@/config/mcpBridge';
import type { McpResult } from '@/types';

vi.mock('@/api/artifactApi', () => ({
  fetchArtifactRawHtml: vi.fn(),
  fetchArtifactHtml: vi.fn().mockResolvedValue('<head></head><body>DASH</body>'),
  repairArtifact: vi.fn(),
  callArtifactMcp: vi.fn(),
}));

const ARTIFACT = { artifactId: 'art-42', title: 'Dashboard' };

function renderPanel(props: ArtifactPanelProps): ReturnType<typeof render> {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <Suspense fallback={<div>loading</div>}>
        <ArtifactPanel {...props} />
      </Suspense>
    </QueryClientProvider>,
  );
}

async function findIframe(container: HTMLElement): Promise<HTMLIFrameElement> {
  return waitFor(() => {
    const iframe = container.querySelector('iframe');
    if (!iframe) throw new Error('iframe not yet mounted');
    return iframe as HTMLIFrameElement;
  });
}

function mcpCall(iframe: HTMLIFrameElement, callId = '1'): MessageEvent {
  return new MessageEvent('message', {
    data: {
      type: 'erd-mcp-call',
      id: callId,
      connector: 'sales',
      tool: 'list_orders',
      args: { days: 30 },
    },
    source: iframe.contentWindow,
  });
}

async function dispatch(event: MessageEvent): Promise<void> {
  await act(async () => {
    window.dispatchEvent(event);
  });
}

describe('useMcpBridge via ArtifactPanel', () => {
  beforeEach(() => {
    vi.mocked(artifactApiModule.callArtifactMcp).mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  test('erd-mcp-call from the iframe is forwarded and the result is posted back once', async () => {
    const body: McpResult = { data: { result: [{ qty: 1.1 }] } };
    vi.mocked(artifactApiModule.callArtifactMcp).mockResolvedValue(body);
    const { container } = renderPanel({ artifact: ARTIFACT });
    const iframe = await findIframe(container);
    const postSpy = vi.spyOn(iframe.contentWindow as Window, 'postMessage');

    await dispatch(mcpCall(iframe));

    expect(artifactApiModule.callArtifactMcp).toHaveBeenCalledWith('art-42', {
      connector: 'sales',
      tool: 'list_orders',
      args: { days: 30 },
    });
    await waitFor(() => expect(postSpy).toHaveBeenCalledTimes(1));
    expect(postSpy).toHaveBeenCalledWith({ type: 'erd-mcp-result', id: '1', result: body }, '*');
  });

  test('HTTP failure is folded and posted back as an error envelope', async () => {
    vi.mocked(artifactApiModule.callArtifactMcp).mockRejectedValue(
      new AxiosError('nope', 'ERR_BAD_RESPONSE', undefined, undefined, {
        status: 404,
      } as AxiosResponse),
    );
    const { container } = renderPanel({ artifact: ARTIFACT });
    const iframe = await findIframe(container);
    const postSpy = vi.spyOn(iframe.contentWindow as Window, 'postMessage');

    await dispatch(mcpCall(iframe));

    await waitFor(() => expect(postSpy).toHaveBeenCalledTimes(1));
    const [message] = postSpy.mock.calls[0];
    expect(message).toMatchObject({
      type: 'erd-mcp-result',
      id: '1',
      result: { error: { code: 'AUTH' } },
    });
  });

  test('message with wrong type is ignored', async () => {
    const { container } = renderPanel({ artifact: ARTIFACT });
    const iframe = await findIframe(container);

    await dispatch(
      new MessageEvent('message', {
        data: { type: 'erd-artifact-error', errors: [] },
        source: iframe.contentWindow,
      }),
    );

    expect(artifactApiModule.callArtifactMcp).not.toHaveBeenCalled();
  });

  test('message from a foreign source is ignored', async () => {
    const { container } = renderPanel({ artifact: ARTIFACT });
    await findIframe(container);

    await dispatch(
      new MessageEvent('message', {
        data: { type: 'erd-mcp-call', id: '1', connector: 'sales', tool: 'list_orders', args: {} },
      }),
    );

    expect(artifactApiModule.callArtifactMcp).not.toHaveBeenCalled();
  });

  test('timeout posts RETRYABLE and a late result is dropped', async () => {
    let resolveLate: (value: McpResult) => void = () => undefined;
    vi.mocked(artifactApiModule.callArtifactMcp).mockImplementation(
      () =>
        new Promise<McpResult>((resolve) => {
          resolveLate = resolve;
        }),
    );
    const { container } = renderPanel({ artifact: ARTIFACT });
    const iframe = await findIframe(container);
    const postSpy = vi.spyOn(iframe.contentWindow as Window, 'postMessage');
    vi.useFakeTimers();

    await dispatch(mcpCall(iframe));
    await act(async () => {
      vi.advanceTimersByTime(MCP_BRIDGE_TIMEOUT_MS);
    });

    expect(postSpy).toHaveBeenCalledTimes(1);
    expect(postSpy.mock.calls[0][0]).toMatchObject({
      type: 'erd-mcp-result',
      id: '1',
      result: { error: { code: 'RETRYABLE', message: expect.stringContaining('timeout') } },
    });

    await act(async () => {
      resolveLate({ data: [] });
      await Promise.resolve();
    });
    expect(postSpy).toHaveBeenCalledTimes(1);
  });

  test('result for a call issued to a previous iframe instance is dropped after remount', async () => {
    let resolveLate: (value: McpResult) => void = () => undefined;
    vi.mocked(artifactApiModule.callArtifactMcp).mockImplementation(
      () =>
        new Promise<McpResult>((resolve) => {
          resolveLate = resolve;
        }),
    );
    const { container, rerender } = renderPanel({ artifact: ARTIFACT, reloadNonce: 0 });
    const firstIframe = await findIframe(container);
    const firstPostSpy = vi.spyOn(firstIframe.contentWindow as Window, 'postMessage');

    await dispatch(mcpCall(firstIframe));

    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    rerender(
      <QueryClientProvider client={queryClient}>
        <Suspense fallback={<div>loading</div>}>
          <ArtifactPanel artifact={ARTIFACT} reloadNonce={1} />
        </Suspense>
      </QueryClientProvider>,
    );
    const secondIframe = await waitFor(() => {
      const iframe = container.querySelector('iframe');
      if (!iframe || iframe === firstIframe) throw new Error('remounted iframe not yet present');
      return iframe as HTMLIFrameElement;
    });
    const secondPostSpy = vi.spyOn(secondIframe.contentWindow as Window, 'postMessage');

    await act(async () => {
      resolveLate({ data: [] });
      await Promise.resolve();
    });

    expect(firstPostSpy).not.toHaveBeenCalled();
    expect(secondPostSpy).not.toHaveBeenCalled();
  });

  test("after a remount, an old call's late result never reaches the new iframe that reused the id", async () => {
    const resolvers: Array<(value: McpResult) => void> = [];
    vi.mocked(artifactApiModule.callArtifactMcp).mockImplementation(
      () =>
        new Promise<McpResult>((resolve) => {
          resolvers.push(resolve);
        }),
    );
    const { container, rerender } = renderPanel({ artifact: ARTIFACT, reloadNonce: 0 });
    const firstIframe = await findIframe(container);

    await dispatch(mcpCall(firstIframe, '1'));
    expect(artifactApiModule.callArtifactMcp).toHaveBeenCalledTimes(1);

    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    rerender(
      <QueryClientProvider client={queryClient}>
        <Suspense fallback={<div>loading</div>}>
          <ArtifactPanel artifact={ARTIFACT} reloadNonce={1} />
        </Suspense>
      </QueryClientProvider>,
    );
    const secondIframe = await waitFor(() => {
      const iframe = container.querySelector('iframe');
      if (!iframe || iframe === firstIframe) throw new Error('remounted iframe not yet present');
      return iframe as HTMLIFrameElement;
    });
    const secondPostSpy = vi.spyOn(secondIframe.contentWindow as Window, 'postMessage');

    await dispatch(mcpCall(secondIframe, '1'));
    expect(artifactApiModule.callArtifactMcp).toHaveBeenCalledTimes(2);

    const [resolveFirst, resolveSecond] = resolvers;
    await act(async () => {
      resolveFirst({ data: 'old' });
      await Promise.resolve();
    });
    expect(secondPostSpy).not.toHaveBeenCalled();

    await act(async () => {
      resolveSecond({ data: 'new' });
      await Promise.resolve();
    });
    expect(secondPostSpy).toHaveBeenCalledTimes(1);
    expect(secondPostSpy).toHaveBeenCalledWith(
      { type: 'erd-mcp-result', id: '1', result: { data: 'new' } },
      '*',
    );
  });

  test('after the iframe loads a second document, erd-mcp-call is ignored', async () => {
    const { container } = renderPanel({ artifact: ARTIFACT });
    const iframe = await findIframe(container);

    await act(async () => {
      iframe.dispatchEvent(new Event('load'));
      iframe.dispatchEvent(new Event('load'));
    });
    await dispatch(mcpCall(iframe));

    expect(artifactApiModule.callArtifactMcp).not.toHaveBeenCalled();
  });

  test('a result pending when the iframe navigates away is not posted', async () => {
    let resolveLate: (value: McpResult) => void = () => undefined;
    vi.mocked(artifactApiModule.callArtifactMcp).mockImplementation(
      () =>
        new Promise<McpResult>((resolve) => {
          resolveLate = resolve;
        }),
    );
    const { container } = renderPanel({ artifact: ARTIFACT });
    const iframe = await findIframe(container);
    const postSpy = vi.spyOn(iframe.contentWindow as Window, 'postMessage');

    await dispatch(mcpCall(iframe));
    await act(async () => {
      iframe.dispatchEvent(new Event('load'));
      iframe.dispatchEvent(new Event('load'));
    });
    await act(async () => {
      resolveLate({ data: [] });
      await Promise.resolve();
    });

    expect(postSpy).not.toHaveBeenCalled();
  });

  test('unmount removes every message listener registered during mount', async () => {
    const addSpy = vi.spyOn(window, 'addEventListener');
    const removeSpy = vi.spyOn(window, 'removeEventListener');
    try {
      const { container, unmount } = renderPanel({ artifact: ARTIFACT });
      await findIframe(container);
      const messageHandlers = addSpy.mock.calls
        .filter(([type]) => type === 'message')
        .map(([, handler]) => handler);

      unmount();

      // The panel registers two: useMcpBridge and the erd-artifact-error effect.
      expect(messageHandlers.length).toBeGreaterThanOrEqual(2);
      for (const messageHandler of messageHandlers) {
        expect(removeSpy).toHaveBeenCalledWith('message', messageHandler);
      }
    } finally {
      addSpy.mockRestore();
      removeSpy.mockRestore();
    }
  });
});
