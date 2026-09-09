/**
 * CoworkPage tests — draft session creation and session-switch artifact handling.
 * Draft: "New chat" must not POST /sessions; a UUID is generated client-side and
 * ChatPanel renders from a seeded empty SessionDetail shell until the first send/upload.
 */
import React from 'react';
import { render, screen, act, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { App } from 'antd';
import { vi, describe, it, expect, beforeEach } from 'vitest';
import type { ArtifactRef } from '@/components/artifact/ArtifactPanel';
import type { SessionDetail, SessionSummary } from '@/types';

// ── Module mocks (must precede dynamic imports) ──────────────────────────────

vi.mock('@/api/sessionApi', () => ({
  listSessions: vi.fn().mockResolvedValue([]),
  getSession: vi.fn(),
}));

vi.mock('@/api/artifactApi', () => ({
  repairArtifact: vi.fn(),
}));

// Stub away components with heavy side-effects so we can focus on CoworkPage logic.
// The sidebar mock renders accessible buttons so tests can assert on draft entries.
vi.mock('./components/chat/ChatHistorySidebar', () => ({
  default: ({
    sessions: sidebarSessions,
    activeId,
    onSelect,
    onNew,
  }: {
    sessions: SessionSummary[];
    activeId: string | null;
    onSelect: (sessionId: string) => void;
    onNew: () => void;
    onCollapse: () => void;
  }) => (
    <div>
      <button type="button" onClick={onNew}>
        New chat
      </button>
      {sidebarSessions.map((session) => (
        <button
          key={session.id}
          type="button"
          onClick={() => onSelect(session.id)}
          aria-pressed={session.id === activeId}
        >
          {session.title}
        </button>
      ))}
    </div>
  ),
}));
// Mocked as vi.fn() so tests can inspect the artifact prop CoworkPage passed to it.
vi.mock('@/components/artifact/ArtifactPanel', () => ({
  default: vi.fn(() => null),
}));
vi.mock('@/components/files/AttachmentsPopover', () => ({ default: () => null }));
vi.mock('@/components/files/UploadModal', () => ({ default: () => null }));
// ConnectorPicker fetches via useSuspenseQuery; stubbed here since these tests don't
// exercise connector selection (see ChatPanel.connectors.test.tsx for that coverage).
vi.mock('@/components/connectors/ConnectorPicker', () => ({ default: () => null }));

vi.mock('@/hooks/useAgentStream', () => ({
  useAgentStream: vi.fn().mockReturnValue({
    state: {
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
      durationMs: null,
      startedAt: null,
    },
    send: vi.fn(),
    stop: vi.fn(),
    reset: vi.fn(),
  }),
}));

vi.mock('@/hooks/useAppConfig', () => ({
  useAppConfig: vi.fn().mockReturnValue({
    retentionDays: 30,
    maxFiles: 5,
    maxSessionBytes: 5_368_709_120,
    singleFileLimits: { csv: 2_147_483_648, xlsx: 209_715_200 },
  }),
}));

import CoworkPage from './CoworkPage';
import { listSessions, getSession } from '@/api/sessionApi';
import ArtifactPanelDefault from '@/components/artifact/ArtifactPanel';

// ── Helpers ───────────────────────────────────────────────────────────────────

function makeQueryClient(): QueryClient {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

/** Wrapper providing QueryClient and antd App context. */
function makeWrapper(queryClient: QueryClient): React.FC<{ children: React.ReactNode }> {
  return function Wrapper({ children }: { children: React.ReactNode }): React.ReactElement {
    return (
      <QueryClientProvider client={queryClient}>
        <App>{children}</App>
      </QueryClientProvider>
    );
  };
}

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('CoworkPage — draft session creation', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listSessions).mockResolvedValue([]);
  });

  it('newChat_click_rendersChatPanelWithoutAnyCreateCall: auto-draft shows empty state without POST to /sessions', async () => {
    const queryClient = makeQueryClient();
    // Pre-seed sessions so useSuspenseQuery doesn't suspend in tests
    queryClient.setQueryData(['sessions'], []);

    render(<CoworkPage />, { wrapper: makeWrapper(queryClient) });

    // Allow effects and state updates to flush
    await act(async () => {});

    // The ChatPanel empty state should be visible — confirms a draft was started
    expect(screen.getByText(/Ask eRD AI to analyze your data/i)).toBeInTheDocument();

    // getSession must NOT have been called: the cache was seeded by startDraftSession
    // and useSessionDetail's staleTime: Infinity prevents any background refetch.
    expect(vi.mocked(getSession)).not.toHaveBeenCalled();
  });

  it('emptySessionsList_onLoad_autoStartsDraftExactlyOnce: StrictMode double-invoke still renders a single draft panel with zero API creates', async () => {
    const queryClient = makeQueryClient();
    queryClient.setQueryData(['sessions'], []);

    render(
      <React.StrictMode>
        <CoworkPage />
      </React.StrictMode>,
      { wrapper: makeWrapper(queryClient) },
    );

    await act(async () => {});

    // Exactly one empty-state placeholder in the DOM — only one ChatPanel rendered
    const emptyStatePlaceholders = screen.getAllByText(/Ask eRD AI to analyze your data/i);
    expect(emptyStatePlaceholders).toHaveLength(1);

    // No server create call — session materializes lazily on first send/upload
    expect(vi.mocked(getSession)).not.toHaveBeenCalled();
  });

  it('newChat_draftActive_sidebarShowsNewAnalysisEntry: auto-draft prepends one active New analysis entry to the sidebar', async () => {
    const queryClient = makeQueryClient();
    queryClient.setQueryData(['sessions'], []);

    render(<CoworkPage />, { wrapper: makeWrapper(queryClient) });
    await act(async () => {});

    // Exactly one sidebar button titled 'New analysis'
    const draftButtons = screen.getAllByRole('button', { name: /^New analysis$/i });
    expect(draftButtons).toHaveLength(1);

    // The entry must be highlighted as active
    expect(draftButtons[0]).toHaveAttribute('aria-pressed', 'true');
  });

  it('clickDraftEntry_selectsSameSession_stateRetained: clicking the draft sidebar entry keeps panel intact and fires no network requests', async () => {
    const queryClient = makeQueryClient();
    queryClient.setQueryData(['sessions'], []);

    render(<CoworkPage />, { wrapper: makeWrapper(queryClient) });
    await act(async () => {});

    const draftButton = screen.getByRole('button', { name: /^New analysis$/i });

    // Click the draft entry — handleSelectSession's same-id guard returns early, so no reset fires
    await act(async () => {
      fireEvent.click(draftButton);
    });

    // No crash — ChatPanel still visible
    expect(screen.getByText(/Ask eRD AI to analyze your data/i)).toBeInTheDocument();

    // getSession was never called (cache-seeded session, no network request)
    expect(vi.mocked(getSession)).not.toHaveBeenCalled();

    // Entry remains highlighted as active
    expect(screen.getByRole('button', { name: /^New analysis$/i })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
  });

  it('newChatClick_whileDraftActive_doesNotCreateSecondDraft: clicking New chat while a draft is active yields exactly one draft entry', async () => {
    const queryClient = makeQueryClient();
    queryClient.setQueryData(['sessions'], []);

    render(<CoworkPage />, { wrapper: makeWrapper(queryClient) });
    await act(async () => {});

    // Click the New chat button while a draft is already active
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /^New chat$/i }));
    });

    // Still exactly one 'New analysis' entry — no second draft was created
    const draftButtons = screen.getAllByRole('button', { name: /^New analysis$/i });
    expect(draftButtons).toHaveLength(1);

    // Still exactly one ChatPanel (single empty-state placeholder)
    expect(screen.getAllByText(/Ask eRD AI to analyze your data/i)).toHaveLength(1);
  });
});

// ── Race-condition fix: session-switch artifact reset ─────────────────────────

/** Builds a minimal SessionDetail with one artifact-bearing AI message. */
function makeSessionDetailWithArtifact(sessionId: string, artifactId: string): SessionDetail {
  return {
    id: sessionId,
    title: 'Session with artifact',
    createdAt: '2026-01-01T00:00:00Z',
    messages: [
      {
        id: 'msg-1',
        sender: 'AI',
        text: 'Here is your dashboard.',
        stepsJson: null,
        artifactId,
        artifactTitle: 'My Dashboard',
        questionsJson: null,
        createdAt: '2026-01-01T00:00:00Z',
      },
    ],
    files: [],
  };
}

/** Builds a minimal SessionDetail with no artifact messages. */
function makeEmptySessionDetail(sessionId: string): SessionDetail {
  return {
    id: sessionId,
    title: 'Empty session',
    createdAt: '2026-01-01T00:00:01Z',
    messages: [],
    files: [],
  };
}

describe('CoworkPage — session-switch artifact race-condition fix', () => {
  const SESSION_A_ID = 'session-a';
  const SESSION_B_ID = 'session-b';
  const ARTIFACT_ID = 'art-123';

  const SESSION_A_SUMMARY: SessionSummary = {
    id: SESSION_A_ID,
    title: 'Session with artifact',
    updatedAt: '2026-01-01T00:00:00Z',
  };
  const SESSION_B_SUMMARY: SessionSummary = {
    id: SESSION_B_ID,
    title: 'Empty session',
    updatedAt: '2026-01-01T00:00:01Z',
  };

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listSessions).mockResolvedValue([SESSION_A_SUMMARY, SESSION_B_SUMMARY]);
    vi.mocked(ArtifactPanelDefault).mockClear();
  });

  it('switchBackToSessionWithArtifact_cachedDetail_autoShowsLatestArtifact: re-entering a cached session with an artifact surfaces that artifact in ArtifactPanel', async () => {
    // Arrange: both session details pre-seeded in the query cache so useSuspenseQuery
    // never suspends — this simulates the "warm cache / re-entry" scenario.
    const queryClient = makeQueryClient();
    queryClient.setQueryData(['sessions'], [SESSION_A_SUMMARY, SESSION_B_SUMMARY]);
    queryClient.setQueryData(
      ['session', SESSION_A_ID],
      makeSessionDetailWithArtifact(SESSION_A_ID, ARTIFACT_ID),
    );
    queryClient.setQueryData(['session', SESSION_B_ID], makeEmptySessionDetail(SESSION_B_ID));

    render(<CoworkPage />, { wrapper: makeWrapper(queryClient) });
    await act(async () => {});

    // Initial state: session A is auto-selected (first in list), artifact should surface.
    const artifactPropAfterInitial = (
      vi.mocked(ArtifactPanelDefault).mock.lastCall as [{ artifact: ArtifactRef | null }]
    )?.[0]?.artifact;
    expect(artifactPropAfterInitial).toEqual({ artifactId: ARTIFACT_ID, title: 'My Dashboard' });

    // Act: switch to session B (no artifact) then back to session A.
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Empty session/i }));
    });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Session with artifact/i }));
    });

    // ArtifactPanel must receive the artifact again after switching back to a cached session.
    const artifactPropAfterReEntry = (
      vi.mocked(ArtifactPanelDefault).mock.lastCall as [{ artifact: ArtifactRef | null }]
    )?.[0]?.artifact;
    expect(artifactPropAfterReEntry).toEqual({ artifactId: ARTIFACT_ID, title: 'My Dashboard' });
  });

  it('clickActiveEntry_noReset: selecting the already-active session does not clear the artifact panel', async () => {
    // Arrange: session A is active with a known artifact already displayed.
    const queryClient = makeQueryClient();
    queryClient.setQueryData(['sessions'], [SESSION_A_SUMMARY, SESSION_B_SUMMARY]);
    queryClient.setQueryData(
      ['session', SESSION_A_ID],
      makeSessionDetailWithArtifact(SESSION_A_ID, ARTIFACT_ID),
    );
    queryClient.setQueryData(['session', SESSION_B_ID], makeEmptySessionDetail(SESSION_B_ID));

    render(<CoworkPage />, { wrapper: makeWrapper(queryClient) });
    await act(async () => {});

    // Confirm artifact is displayed initially.
    const artifactBefore = (
      vi.mocked(ArtifactPanelDefault).mock.lastCall as [{ artifact: ArtifactRef | null }]
    )?.[0]?.artifact;
    expect(artifactBefore).toEqual({ artifactId: ARTIFACT_ID, title: 'My Dashboard' });

    // Count how many times ArtifactPanel was called before the no-op click.
    const callCountBefore = vi.mocked(ArtifactPanelDefault).mock.calls.length;

    // Act: click the already-active session A entry.
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Session with artifact/i }));
    });

    // Assert: ArtifactPanel was NOT re-rendered with null — the artifact is still set.
    // (handleSelectSession is a no-op when the same id is clicked, so no reset fires.)
    const allArtifactArgs = vi
      .mocked(ArtifactPanelDefault)
      .mock.calls.slice(callCountBefore)
      .map((callArgs) => (callArgs as [{ artifact: ArtifactRef | null }])[0]?.artifact);
    expect(allArtifactArgs).not.toContain(null);
  });
});
