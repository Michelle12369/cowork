import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { App as AntdApp } from 'antd';
import { vi } from 'vitest';
import App from './App';

vi.mock('@/hooks/useAppConfig', () => ({
  useAppConfig: vi.fn().mockReturnValue({
    retentionDays: 30,
    maxFiles: 5,
    maxSessionBytes: 5_368_709_120,
    singleFileLimits: {
      csv: 2_147_483_648,
      xlsx: 209_715_200,
    },
  }),
}));

vi.mock('@/api/sessionApi', () => ({
  listSessions: vi.fn().mockResolvedValue([]),
  getSession: vi.fn().mockResolvedValue({
    id: 's1',
    title: 'New analysis',
    createdAt: new Date().toISOString(),
    messages: [],
    files: [],
  }),
}));

test('renders cowork title', async () => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <AntdApp>
        <App />
      </AntdApp>
    </QueryClientProvider>,
  );
  expect(await screen.findByText(/Cowork · Data studio/i)).toBeInTheDocument();
});
