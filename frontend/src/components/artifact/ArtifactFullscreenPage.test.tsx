import { render, screen, act, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { expect, test, vi } from 'vitest';
import ArtifactFullscreenPage from './ArtifactFullscreenPage';
import * as artifactApiModule from '@/api/artifactApi';

vi.mock('@/api/artifactApi', () => ({
  fetchArtifactHtml: vi.fn().mockResolvedValue('<head></head><body>FULL</body>'),
  callArtifactMcp: vi.fn(),
}));

function renderPage(): void {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <ArtifactFullscreenPage artifactId="artifact-9" />
    </QueryClientProvider>,
  );
}

test('renders full-viewport sandboxed frame for the artifact', async () => {
  renderPage();
  const iframe = (await screen.findByTitle('Dashboard')) as HTMLIFrameElement;
  expect(iframe.getAttribute('sandbox')).toBe('allow-scripts');
  expect(iframe.getAttribute('srcdoc')).toContain('FULL');
});

test('answers erd-mcp-call from its own iframe', async () => {
  vi.mocked(artifactApiModule.callArtifactMcp).mockResolvedValue({ data: [] });
  renderPage();
  const iframe = (await screen.findByTitle('Dashboard')) as HTMLIFrameElement;
  const postSpy = vi.spyOn(iframe.contentWindow as Window, 'postMessage');

  await act(async () => {
    window.dispatchEvent(
      new MessageEvent('message', {
        data: { type: 'erd-mcp-call', id: '1', connector: 'sales', tool: 'list_orders', args: {} },
        source: iframe.contentWindow,
      }),
    );
  });

  expect(artifactApiModule.callArtifactMcp).toHaveBeenCalledWith('artifact-9', {
    connector: 'sales',
    tool: 'list_orders',
    args: {},
  });
  await waitFor(() =>
    expect(postSpy).toHaveBeenCalledWith(
      { type: 'erd-mcp-result', id: '1', result: { data: [] } },
      '*',
    ),
  );
});
