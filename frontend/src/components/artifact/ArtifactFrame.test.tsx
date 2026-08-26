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
