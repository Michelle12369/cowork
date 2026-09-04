/**
 * ChatPanel — connector picker wiring: selecting a connector disables the upload
 * entry (with an explanatory tooltip), a locked session shows the read-only locked text, an
 * active-files session disables the picker, and the first-message send payload carries the
 * locally selected connector ids. Unlike ChatPanel.test.tsx, this file does NOT stub
 * AttachmentsPopover/ConnectorPicker so the real mutual-exclusion UX is exercised.
 */
import React, { useCallback, useState } from 'react';
import { render, screen, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { App } from 'antd';
import { vi, describe, it, expect, beforeEach } from 'vitest';

// ── Module mocks (must be before dynamic imports) ─────────────────────────────

vi.mock('@/hooks/useSessionDetail');
vi.mock('@/hooks/useAgentStream');
vi.mock('@/hooks/useAppConfig', () => ({
  useAppConfig: vi.fn().mockReturnValue({ retentionDays: 30 }),
}));
vi.mock('@/api/fileApi', () => ({
  deleteFile: vi.fn().mockResolvedValue(undefined),
}));
vi.mock('@/components/files/UploadModal', () => ({ default: () => null }));

import ChatPanel from './ChatPanel';
import { useSessionDetail } from '@/hooks/useSessionDetail';
import { useAgentStream } from '@/hooks/useAgentStream';
import type { SessionDetail, AgentStreamState, ConnectorInfo } from '@/types';

// ── Fixtures ──────────────────────────────────────────────────────────────────

const IDLE_STATE: AgentStreamState = {
  isStreaming: false,
  stopped: false,
  networkError: false,
  steps: [],
  liveText: '',
  answer: null,
  artifact: null,
  error: null,
  thinking: '',
  questions: null,
  codeText: '',
  tables: [],
  durationMs: null,
  startedAt: null,
};

const CONNECTORS: ConnectorInfo[] = [
  { id: 'salesforce', name: 'Salesforce CRM' },
  { id: 'jira', name: 'Jira' },
];

function makeSession(overrides: Partial<SessionDetail> = {}): SessionDetail {
  return {
    id: 's1',
    title: 'Test Session',
    createdAt: '2026-01-01T00:00:00Z',
    messages: [],
    files: [],
    ...overrides,
  };
}

/** Wrapper providing QueryClient + antd App context; pre-seeds ['connectors'] so
 *  ConnectorPicker's useSuspenseQuery resolves synchronously without a real fetch. */
function makeWrapper(connectors: ConnectorInfo[]): {
  Wrapper: React.FC<{ children: React.ReactNode }>;
  queryClient: QueryClient;
} {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  queryClient.setQueryData(['connectors'], connectors);
  const Wrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => (
    <QueryClientProvider client={queryClient}>
      <App>{children}</App>
    </QueryClientProvider>
  );
  return { Wrapper, queryClient };
}

/** Mirrors CoworkPage's ownership of selectedConnectorIds so the real ConnectorPicker
 *  selection flow can be exercised end to end against a live ChatPanel. */
const Harness: React.FC<{ initialIds?: string[] }> = ({ initialIds = [] }) => {
  const [selectedConnectorIds, setSelectedConnectorIds] = useState<string[]>(initialIds);
  const handleChange = useCallback((ids: string[]): void => setSelectedConnectorIds(ids), []);
  return (
    <ChatPanel
      sessionId="s1"
      selectedConnectorIds={selectedConnectorIds}
      onSelectedConnectorsChange={handleChange}
    />
  );
};

beforeEach(() => {
  vi.mocked(useAgentStream).mockReturnValue({
    state: IDLE_STATE,
    send: vi.fn().mockResolvedValue(undefined),
    stop: vi.fn(),
    reset: vi.fn(),
  });
});

describe('ChatPanel — connector selection disables the upload entry', () => {
  it('selectingConnector_click_disablesAttachmentsButtonWithTooltip', async () => {
    vi.mocked(useSessionDetail).mockReturnValue(makeSession());
    const { Wrapper } = makeWrapper(CONNECTORS);
    const user = userEvent.setup();

    render(<Harness />, { wrapper: Wrapper });

    // Attachments entry starts enabled.
    const attachmentsButton = screen.getByRole('button', { name: /attachments/i });
    expect(attachmentsButton).not.toBeDisabled();

    // Select a connector via the real ConnectorPicker.
    await act(async () => {
      await user.click(screen.getByRole('combobox'));
      await user.click(await screen.findByText('Salesforce CRM'));
    });

    // Upload entry is now disabled...
    expect(screen.getByRole('button', { name: /attachments/i })).toBeDisabled();

    // ...and hovering reveals the explanatory tooltip text.
    await user.hover(screen.getByRole('button', { name: /attachments/i }));
    expect(
      await screen.findByText('已選擇資料源連接器，如需上傳檔案請先開新對話'),
    ).toBeInTheDocument();

    // The footer's inline attach button is disabled too (onAttach becomes undefined).
    expect(screen.getByRole('button', { name: /attach files/i })).toBeDisabled();
  });
});

describe('ChatPanel — locked session shows read-only connector text', () => {
  it('sessionSelectedConnectorsNonEmpty_render_showsLockedReadOnlyText', () => {
    vi.mocked(useSessionDetail).mockReturnValue(
      makeSession({ selectedConnectors: ['salesforce'] }),
    );
    const { Wrapper } = makeWrapper(CONNECTORS);

    render(<Harness />, { wrapper: Wrapper });

    expect(screen.getByText('資料源已鎖定——換資料源請開新對話')).toBeInTheDocument();
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
    // Upload entry stays disabled because the session is locked into connector mode.
    expect(screen.getByRole('button', { name: /attachments/i })).toBeDisabled();
  });
});

describe('ChatPanel — active files disable the connector picker', () => {
  it('sessionHasActiveFile_render_connectorComboboxDisabled', () => {
    vi.mocked(useSessionDetail).mockReturnValue(
      makeSession({
        files: [
          {
            id: 'f1',
            name: 'data.csv',
            alias: 'data',
            sizeBytes: 1000,
            type: 'csv',
            rowCount: 10,
            expired: false,
          },
        ],
      }),
    );
    const { Wrapper } = makeWrapper(CONNECTORS);

    render(<Harness />, { wrapper: Wrapper });

    expect(screen.getByRole('combobox')).toBeDisabled();
  });
});

describe('ChatPanel — first message payload carries selected connector ids', () => {
  it('sendFirstMessage_connectorsSelected_sendCalledWithSelectedIds', async () => {
    vi.mocked(useSessionDetail).mockReturnValue(makeSession());
    const mockSend = vi.fn().mockResolvedValue(undefined);
    vi.mocked(useAgentStream).mockReturnValue({
      state: IDLE_STATE,
      send: mockSend,
      stop: vi.fn(),
      reset: vi.fn(),
    });
    const { Wrapper } = makeWrapper(CONNECTORS);
    const user = userEvent.setup();

    render(<Harness initialIds={['salesforce', 'jira']} />, { wrapper: Wrapper });

    const textarea = screen.getByPlaceholderText(/Ask eRD AI/i);
    await act(async () => {
      await user.type(textarea, 'Pull last quarter opportunities{Enter}');
    });

    expect(mockSend).toHaveBeenCalledWith('Pull last quarter opportunities', undefined, [
      'salesforce',
      'jira',
    ]);
  });

  it('sendFirstMessage_noConnectorsSelected_sendCalledWithUndefinedConnectors', async () => {
    vi.mocked(useSessionDetail).mockReturnValue(makeSession());
    const mockSend = vi.fn().mockResolvedValue(undefined);
    vi.mocked(useAgentStream).mockReturnValue({
      state: IDLE_STATE,
      send: mockSend,
      stop: vi.fn(),
      reset: vi.fn(),
    });
    const { Wrapper } = makeWrapper(CONNECTORS);
    const user = userEvent.setup();

    render(<Harness />, { wrapper: Wrapper });

    const textarea = screen.getByPlaceholderText(/Ask eRD AI/i);
    await act(async () => {
      await user.type(textarea, 'Summarize the uploaded file{Enter}');
    });

    expect(mockSend).toHaveBeenCalledWith('Summarize the uploaded file', undefined, undefined);
  });
});
