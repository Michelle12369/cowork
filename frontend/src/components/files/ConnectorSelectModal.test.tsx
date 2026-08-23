import React, { Suspense, useState } from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { App } from 'antd';
import { vi } from 'vitest';
import { fetchConnectors } from '@/api/connectorApi';
import ConnectorSelectModal from './ConnectorSelectModal';
import type { ConnectorGroup } from '@/api/connectorApi';

vi.mock('@/api/connectorApi');

const GROUPS: ConnectorGroup[] = [
  { name: 'mes', display: 'MES 製造執行系統', description: '產線良率、缺陷、產能' },
  { name: 'erp', display: 'ERP 系統', description: '訂單與庫存' },
];

function Wrapper({ children }: { children: React.ReactNode }): React.ReactElement {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={queryClient}>
      <App>
        <Suspense fallback={<div>loading</div>}>{children}</Suspense>
      </App>
    </QueryClientProvider>
  );
}

/** Stateful harness so the modal's controlled `selectedGroups` prop reflects real selection
 *  changes, the way ChatPanel would drive it. */
function StatefulHarness(): React.ReactElement {
  const [selectedGroups, setSelectedGroups] = useState<string[]>([]);
  return <ConnectorSelectModal selectedGroups={selectedGroups} onChange={setSelectedGroups} />;
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(fetchConnectors).mockResolvedValue(GROUPS);
});

test('opening the modal fetches connectors and renders group display names', async () => {
  render(<ConnectorSelectModal selectedGroups={[]} onChange={() => {}} />, { wrapper: Wrapper });

  const trigger = await screen.findByRole('button', { name: /選擇資料源/ });
  fireEvent.click(trigger);

  expect(await screen.findByText('MES 製造執行系統')).toBeInTheDocument();
  expect(screen.getByText('ERP 系統')).toBeInTheDocument();
  expect(fetchConnectors).toHaveBeenCalledTimes(1);
});

test('selecting more than one group shows the cross-system hint', async () => {
  render(<StatefulHarness />, { wrapper: Wrapper });

  const trigger = await screen.findByRole('button', { name: /選擇資料源/ });
  fireEvent.click(trigger);

  expect(screen.queryByText('跨系統分析可能需明確指定關聯欄位')).not.toBeInTheDocument();

  fireEvent.click(await screen.findByText('MES 製造執行系統'));
  expect(screen.queryByText('跨系統分析可能需明確指定關聯欄位')).not.toBeInTheDocument();

  fireEvent.click(screen.getByText('ERP 系統'));
  expect(await screen.findByText('跨系統分析可能需明確指定關聯欄位')).toBeInTheDocument();
});

test('selected groups are summarized in a tag next to the trigger button', async () => {
  render(<ConnectorSelectModal selectedGroups={['mes']} onChange={() => {}} />, {
    wrapper: Wrapper,
  });

  expect(await screen.findByText('MES 製造執行系統')).toBeInTheDocument();
});

test('renders nothing (button hidden) when no connectors are configured', async () => {
  vi.mocked(fetchConnectors).mockResolvedValue([]);
  const { container } = render(<ConnectorSelectModal selectedGroups={[]} onChange={() => {}} />, {
    wrapper: Wrapper,
  });

  await waitFor(() => {
    expect(screen.queryByText('loading')).not.toBeInTheDocument();
  });
  expect(container.querySelector('button')).not.toBeInTheDocument();
});

// ── session-lock (§11.6) ─────────────────────────────────────────────────────

test('locked disables the trigger while still showing the locked selection as tags', async () => {
  render(<ConnectorSelectModal selectedGroups={['mes']} onChange={() => {}} locked />, {
    wrapper: Wrapper,
  });

  const trigger = await screen.findByRole('button', { name: /選擇資料源/ });
  expect(trigger).toBeDisabled();
  expect(screen.getByText('MES 製造執行系統')).toBeInTheDocument();
});

test('locked trigger shows the "資料源已鎖定" tooltip on hover', async () => {
  render(<ConnectorSelectModal selectedGroups={[]} onChange={() => {}} locked />, {
    wrapper: Wrapper,
  });

  const trigger = await screen.findByRole('button', { name: /選擇資料源/ });
  const user = userEvent.setup();
  await user.hover(trigger);

  expect(await screen.findByText('資料源已鎖定——換資料源請開新對話')).toBeInTheDocument();
});

test('locked=false (default) leaves the trigger enabled and clickable', async () => {
  render(<ConnectorSelectModal selectedGroups={[]} onChange={() => {}} />, { wrapper: Wrapper });

  const trigger = await screen.findByRole('button', { name: /選擇資料源/ });
  expect(trigger).toBeEnabled();

  fireEvent.click(trigger);
  expect(await screen.findByText('MES 製造執行系統')).toBeInTheDocument();
});
