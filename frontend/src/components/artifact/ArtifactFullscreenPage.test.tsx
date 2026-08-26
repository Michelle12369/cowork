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
