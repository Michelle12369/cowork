/**
 * ChatPanel tests — user-picked artifact selection survives a later query invalidation,
 * and onArtifactsChange version-list reporting. The `currentArtifact` prop guards the
 * history-fallback branch so it only fires when nothing is explicitly selected yet.
 */
import React from 'react';
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
// Stub away components that carry their own heavy side-effects in tests
vi.mock('@/components/files/AttachmentsPopover', () => ({ default: () => null }));
vi.mock('@/components/files/UploadModal', () => ({ default: () => null }));

import ChatPanel from './ChatPanel';
import { useSessionDetail } from '@/hooks/useSessionDetail';
import { useAgentStream } from '@/hooks/useAgentStream';
import { QUICK_PROMPTS } from '@/config/quickPrompts';
import type { Message, SessionDetail, AgentStreamState } from '@/types';

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
};

function makeMessage(
  id: string,
  artifactId: string | null,
  artifactTitle: string | null,
  text: string,
): Message {
  return {
    id,
    sender: 'AI',
    text,
    stepsJson: null,
    artifactId,
    artifactTitle,
    questionsJson: null,
    referencedTablesJson: null,
    createdAt: '2026-01-01T00:00:00Z',
  };
}

function makeSession(messages: Message[]): SessionDetail {
  return {
    id: 's1',
    title: 'Test Session',
    createdAt: '2026-01-01T00:00:00Z',
    messages,
    files: [],
  };
}

// ── Wrapper ───────────────────────────────────────────────────────────────────

function makeWrapper(qc: QueryClient): React.FC<{ children: React.ReactNode }> {
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={qc}>
        <App>{children}</App>
      </QueryClientProvider>
    );
  };
}

// ── Race condition tests ───────────────────────────────────────────────────────

describe('ChatPanel — user-picked artifact race condition', () => {
  // Titles follow the backend V8+ "Version N" convention so the artifact card label
  // matches what MessageBubble renders (artifact.title shown directly, no computed version).
  const OLD_ARTIFACT = { artifactId: 'art-old', title: 'Version 1' };
  const NEW_ARTIFACT = { artifactId: 'art-new', title: 'Version 2' };

  const OLD_MSG = makeMessage('m1', OLD_ARTIFACT.artifactId, OLD_ARTIFACT.title, 'Analysis done.');
  const NEW_MSG = makeMessage('m2', NEW_ARTIFACT.artifactId, NEW_ARTIFACT.title, 'Regenerated.');

  beforeEach(() => {
    vi.mocked(useAgentStream).mockReturnValue({
      state: IDLE_STATE,
      send: vi.fn(),
      stop: vi.fn(),
      reset: vi.fn(),
    });
  });

  it('history-fallback fires onArtifactChange with the latest message artifact on mount', () => {
    vi.mocked(useSessionDetail).mockReturnValue(makeSession([OLD_MSG]));

    const onArtifactChange = vi.fn();
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    render(<ChatPanel sessionId="s1" onArtifactChange={onArtifactChange} />, {
      wrapper: makeWrapper(qc),
    });

    expect(onArtifactChange).toHaveBeenCalledWith(OLD_ARTIFACT);
  });

  it('user-picked artifact is preserved when session.messages updates after invalidation', async () => {
    vi.mocked(useSessionDetail).mockReturnValue(makeSession([OLD_MSG]));

    const onArtifactChange = vi.fn();
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const Wrapper = makeWrapper(qc);

    const { rerender } = render(
      <ChatPanel sessionId="s1" onArtifactChange={onArtifactChange} currentArtifact={null} />,
      { wrapper: Wrapper },
    );

    // Initial history-fallback: OLD_ARTIFACT reported
    expect(onArtifactChange).toHaveBeenCalledWith(OLD_ARTIFACT);
    const callsAfterMount = onArtifactChange.mock.calls.length;

    // User explicitly clicks the old artifact card (now shows "Version 1" as label)
    const user = userEvent.setup();
    await act(async () => {
      await user.click(screen.getByText('Version 1'));
    });

    // handleArtifactClick fires — one more call with OLD_ARTIFACT
    expect(onArtifactChange).toHaveBeenLastCalledWith(OLD_ARTIFACT);
    const callsAfterClick = onArtifactChange.mock.calls.length;
    expect(callsAfterClick).toBeGreaterThan(callsAfterMount);

    // Simulate query invalidation (new message) plus CoworkPage echoing back currentArtifact=OLD_ARTIFACT.
    vi.mocked(useSessionDetail).mockReturnValue(makeSession([OLD_MSG, NEW_MSG]));

    await act(async () => {
      rerender(
        <ChatPanel
          sessionId="s1"
          onArtifactChange={onArtifactChange}
          currentArtifact={OLD_ARTIFACT}
        />,
      );
    });

    // History-fallback must NOT fire again because currentArtifact is set
    expect(onArtifactChange).not.toHaveBeenCalledWith(NEW_ARTIFACT);
    expect(onArtifactChange.mock.calls.length).toBe(callsAfterClick);
  });

  it('live stream ARTIFACT event resets user-pick and reports the new artifact', async () => {
    vi.mocked(useSessionDetail).mockReturnValue(makeSession([OLD_MSG]));

    const liveArtifact = { artifactId: 'art-live', title: 'Live Dashboard' };
    const onArtifactChange = vi.fn();
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const Wrapper = makeWrapper(qc);

    const { rerender } = render(
      <ChatPanel sessionId="s1" onArtifactChange={onArtifactChange} currentArtifact={null} />,
      { wrapper: Wrapper },
    );

    // User clicks the old artifact card (now shows "Version 1" as label)
    const user = userEvent.setup();
    await act(async () => {
      await user.click(screen.getByText('Version 1'));
    });

    // Now a live stream produces an ARTIFACT event → should take over
    vi.mocked(useAgentStream).mockReturnValue({
      state: { ...IDLE_STATE, artifact: liveArtifact },
      send: vi.fn(),
      stop: vi.fn(),
      reset: vi.fn(),
    });

    await act(async () => {
      rerender(
        <ChatPanel
          sessionId="s1"
          onArtifactChange={onArtifactChange}
          currentArtifact={OLD_ARTIFACT}
        />,
      );
    });

    expect(onArtifactChange).toHaveBeenCalledWith(liveArtifact);
  });
});

// ── Session switch: history-fallback picks latest artifact ────────────────────

describe('ChatPanel — session switch selects latest artifact', () => {
  beforeEach(() => {
    vi.mocked(useAgentStream).mockReturnValue({
      state: IDLE_STATE,
      send: vi.fn(),
      stop: vi.fn(),
      reset: vi.fn(),
    });
  });

  it('history-fallback calls onArtifactChange with the LAST artifact when session has multiple artifact messages', () => {
    const msg1 = makeMessage('m1', 'art-1', 'Dashboard v1', 'First analysis.');
    const msg2 = makeMessage('m2', 'art-2', 'Dashboard v2', 'Second analysis.');
    const msg3 = makeMessage('m3', 'art-3', 'Dashboard v3', 'Third analysis.');
    vi.mocked(useSessionDetail).mockReturnValue(makeSession([msg1, msg2, msg3]));

    const onArtifactChange = vi.fn();
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    render(
      <ChatPanel sessionId="s1" onArtifactChange={onArtifactChange} currentArtifact={null} />,
      { wrapper: makeWrapper(qc) },
    );

    // Must be called with the LAST artifact (msg3 / art-3), not the first
    expect(onArtifactChange).toHaveBeenCalledWith({ artifactId: 'art-3', title: 'Dashboard v3' });
    expect(onArtifactChange).not.toHaveBeenCalledWith({
      artifactId: 'art-1',
      title: 'Dashboard v1',
    });
    expect(onArtifactChange).not.toHaveBeenCalledWith({
      artifactId: 'art-2',
      title: 'Dashboard v2',
    });
  });
});

// ── onArtifactsChange version-list reporting ──────────────────────────────────

describe('ChatPanel — onArtifactsChange', () => {
  beforeEach(() => {
    vi.mocked(useAgentStream).mockReturnValue({
      state: IDLE_STATE,
      send: vi.fn(),
      stop: vi.fn(),
      reset: vi.fn(),
    });
  });

  it('reports [v1, v2, v3] with correct artifactIds when session has 3 artifact messages', () => {
    const msgs = [
      makeMessage('m1', 'art-1', 'Dash 1', 'First analysis.'),
      makeMessage('m2', 'art-2', 'Dash 2', 'Second analysis.'),
      makeMessage('m3', 'art-3', 'Dash 3', 'Third analysis.'),
    ];
    vi.mocked(useSessionDetail).mockReturnValue(makeSession(msgs));

    const onArtifactsChange = vi.fn();
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    render(<ChatPanel sessionId="s1" onArtifactsChange={onArtifactsChange} />, {
      wrapper: makeWrapper(qc),
    });

    expect(onArtifactsChange).toHaveBeenCalledWith([
      { artifactId: 'art-1', title: 'Dash 1', version: 1 },
      { artifactId: 'art-2', title: 'Dash 2', version: 2 },
      { artifactId: 'art-3', title: 'Dash 3', version: 3 },
    ]);
  });

  it('rerender with updated messages goes old→new without intermediate [] call', async () => {
    // onArtifactsChange must never be called with [] mid-lifecycle — only unmount clears the list.
    const msg1 = makeMessage('m1', 'art-1', 'Dash 1', 'First analysis.');
    vi.mocked(useSessionDetail).mockReturnValue(makeSession([msg1]));

    const onArtifactsChange = vi.fn();
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const Wrapper = makeWrapper(qc);

    const { rerender, unmount } = render(
      <ChatPanel sessionId="s1" onArtifactsChange={onArtifactsChange} />,
      { wrapper: Wrapper },
    );

    // Initial report — v1 only
    expect(onArtifactsChange).toHaveBeenCalledWith([
      { artifactId: 'art-1', title: 'Dash 1', version: 1 },
    ]);
    onArtifactsChange.mockClear();

    // Simulate query invalidation adding a second artifact message
    const msg2 = makeMessage('m2', 'art-2', 'Dash 2', 'Second analysis.');
    vi.mocked(useSessionDetail).mockReturnValue(makeSession([msg1, msg2]));

    await act(async () => {
      rerender(<ChatPanel sessionId="s1" onArtifactsChange={onArtifactsChange} />);
    });

    // Must jump directly from [v1] to [v1, v2] — [] must never appear
    expect(onArtifactsChange).not.toHaveBeenCalledWith([]);
    expect(onArtifactsChange).toHaveBeenCalledWith([
      { artifactId: 'art-1', title: 'Dash 1', version: 1 },
      { artifactId: 'art-2', title: 'Dash 2', version: 2 },
    ]);

    // Only on unmount should [] be reported
    onArtifactsChange.mockClear();
    unmount();
    expect(onArtifactsChange).toHaveBeenCalledWith([]);
    expect(onArtifactsChange).toHaveBeenCalledTimes(1);
  });
});

// ── QuickChips prefill behavior ────────────────────────────────────────────────

describe('ChatPanel — QuickChips prefill instead of send', () => {
  beforeEach(() => {
    vi.mocked(useSessionDetail).mockReturnValue(makeSession([]));
  });

  it('clicking a chip fills the textarea with the prompt text but does NOT call send', async () => {
    const mockSend = vi.fn().mockResolvedValue(undefined);
    vi.mocked(useAgentStream).mockReturnValue({
      state: IDLE_STATE,
      send: mockSend,
      stop: vi.fn(),
      reset: vi.fn(),
    });

    const user = userEvent.setup();
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<ChatPanel sessionId="s1" />, { wrapper: makeWrapper(qc) });

    await act(async () => {
      await user.click(screen.getByText(QUICK_PROMPTS[0].label));
    });

    // send must NOT have been called — chip only prefills the textarea
    expect(mockSend).not.toHaveBeenCalled();

    // The textarea should now contain the chip's prompt text
    const textarea = screen.getByPlaceholderText(/Ask eRD AI/i);
    expect((textarea as HTMLTextAreaElement).value).toBe(QUICK_PROMPTS[0].prompt);
  });

  it('after chip prefill, pressing Enter submits exactly the prefill text', async () => {
    const mockSend = vi.fn().mockResolvedValue(undefined);
    vi.mocked(useAgentStream).mockReturnValue({
      state: IDLE_STATE,
      send: mockSend,
      stop: vi.fn(),
      reset: vi.fn(),
    });

    const user = userEvent.setup();
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<ChatPanel sessionId="s1" />, { wrapper: makeWrapper(qc) });

    // Click a chip to prefill
    await act(async () => {
      await user.click(screen.getByText(QUICK_PROMPTS[0].label));
    });

    // Submit via Enter
    const textarea = screen.getByPlaceholderText(/Ask eRD AI/i);
    await act(async () => {
      await user.type(textarea, '{Enter}');
    });

    // currentArtifact not passed → undefined; send receives (text, undefined)
    expect(mockSend).toHaveBeenCalledWith(QUICK_PROMPTS[0].prompt, undefined);
  });
});

// ── QUESTION event → QuestionCards → send with baseArtifactId ─────────────────

describe('ChatPanel — QUESTION event and QuestionCards integration', () => {
  // Stub MessageList so we can easily detect QuestionCards without rendering the full tree
  // The real MessageList passes questions/onAnswer down; we test the wiring here.
  beforeEach(() => {
    vi.mocked(useSessionDetail).mockReturnValue(makeSession([]));
  });

  it('passes onAnswer and questionsDisabled=false to MessageList when questions are live', () => {
    const questionsState: AgentStreamState = {
      ...IDLE_STATE,
      isStreaming: false,
      liveText: 'Please choose:',
      questions: [{ text: 'Which chart?', options: ['Bar', 'Line'], multiSelect: false }],
    };
    vi.mocked(useAgentStream).mockReturnValue({
      state: questionsState,
      send: vi.fn().mockResolvedValue(undefined),
      stop: vi.fn(),
      reset: vi.fn(),
    });

    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<ChatPanel sessionId="s1" />, { wrapper: makeWrapper(qc) });

    // MessageBubble is rendered with the live state; QuestionCards option should be visible
    // (we don't mock MessageList/MessageBubble here so the real path is exercised)
    expect(screen.getByText('Please choose:')).toBeInTheDocument();
    // Question text appears in the real QuestionCards
    expect(screen.getByText('Which chart?')).toBeInTheDocument();
  });

  it('clicking a QuestionCards option calls send with the option text and the active artifact id', async () => {
    const mockSend = vi.fn().mockResolvedValue(undefined);
    const questionsState: AgentStreamState = {
      ...IDLE_STATE,
      isStreaming: false,
      liveText: 'Please pick:',
      questions: [{ text: 'Chart type?', options: ['Bar', 'Line'], multiSelect: false }],
    };
    vi.mocked(useAgentStream).mockReturnValue({
      state: questionsState,
      send: mockSend,
      stop: vi.fn(),
      reset: vi.fn(),
    });

    const activeArtifact = { artifactId: 'art-42', title: 'My Dashboard' };
    const user = userEvent.setup();
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    render(<ChatPanel sessionId="s1" currentArtifact={activeArtifact} />, {
      wrapper: makeWrapper(qc),
    });

    await act(async () => {
      await user.click(screen.getByText('Bar'));
    });

    expect(mockSend).toHaveBeenCalledWith('Bar', 'art-42');
  });

  it('send includes undefined baseArtifactId when no artifact is selected', async () => {
    const mockSend = vi.fn().mockResolvedValue(undefined);
    const questionsState: AgentStreamState = {
      ...IDLE_STATE,
      isStreaming: false,
      liveText: 'Pick:',
      questions: [{ text: 'Pick?', options: ['A', 'B'], multiSelect: false }],
    };
    vi.mocked(useAgentStream).mockReturnValue({
      state: questionsState,
      send: mockSend,
      stop: vi.fn(),
      reset: vi.fn(),
    });

    const user = userEvent.setup();
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<ChatPanel sessionId="s1" currentArtifact={null} />, { wrapper: makeWrapper(qc) });

    await act(async () => {
      await user.click(screen.getByText('A'));
    });

    expect(mockSend).toHaveBeenCalledWith('A', undefined);
  });
});

// ── Repair offer card ──────────────────────────────────────────────────────────

describe('ChatPanel — repair offer card', () => {
  beforeEach(() => {
    vi.mocked(useAgentStream).mockReturnValue({
      state: IDLE_STATE,
      send: vi.fn(),
      stop: vi.fn(),
      reset: vi.fn(),
    });
  });

  it('renders RepairOfferCard when repairOffer prop is provided and there are messages', () => {
    const msg = makeMessage('m1', 'art-1', 'Dashboard', 'Analysis done.');
    vi.mocked(useSessionDetail).mockReturnValue(makeSession([msg]));

    const repairOffer = {
      errors: [{ message: 'ReferenceError: foo is not defined', line: 1, col: 0 }],
      status: 'pending' as const,
    };

    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<ChatPanel sessionId="s1" repairOffer={repairOffer} />, { wrapper: makeWrapper(qc) });

    expect(screen.getByText(/偵測到儀表板執行錯誤/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '修復' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '忽略' })).toBeInTheDocument();
  });

  it('does not render RepairOfferCard when repairOffer is null', () => {
    const msg = makeMessage('m1', 'art-1', 'Dashboard', 'Analysis done.');
    vi.mocked(useSessionDetail).mockReturnValue(makeSession([msg]));

    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<ChatPanel sessionId="s1" repairOffer={null} />, { wrapper: makeWrapper(qc) });

    expect(screen.queryByText(/偵測到儀表板執行錯誤/)).toBeNull();
  });

  it('clicking 修復 button calls onRepairConfirm', async () => {
    const msg = makeMessage('m1', 'art-1', 'Dashboard', 'Analysis done.');
    vi.mocked(useSessionDetail).mockReturnValue(makeSession([msg]));

    const onRepairConfirm = vi.fn();
    const repairOffer = {
      errors: [{ message: 'ReferenceError: foo is not defined', line: 1, col: 0 }],
      status: 'pending' as const,
    };

    const user = userEvent.setup();
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <ChatPanel sessionId="s1" repairOffer={repairOffer} onRepairConfirm={onRepairConfirm} />,
      { wrapper: makeWrapper(qc) },
    );

    await act(async () => {
      await user.click(screen.getByRole('button', { name: '修復' }));
    });

    expect(onRepairConfirm).toHaveBeenCalledTimes(1);
  });
});

// ── Expired files guard ────────────────────────────────────────────────────────

describe('ChatPanel — expired files banner and send guard', () => {
  beforeEach(() => {
    vi.mocked(useAgentStream).mockReturnValue({
      state: IDLE_STATE,
      send: vi.fn(),
      stop: vi.fn(),
      reset: vi.fn(),
    });
  });

  function makeSessionWithExpiredFile(): ReturnType<typeof makeSession> {
    return {
      id: 's1',
      title: 'Test Session',
      createdAt: '2026-01-01T00:00:00Z',
      messages: [],
      files: [
        {
          id: 'f1',
          name: 'old-data.csv',
          alias: 'old_data',
          sizeBytes: 100_000,
          type: 'csv',
          rowCount: null,
          expired: true,
        },
      ],
    };
  }

  it('shows amber expired-files banner when session has expired files', () => {
    vi.mocked(useSessionDetail).mockReturnValue(makeSessionWithExpiredFile());

    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<ChatPanel sessionId="s1" />, { wrapper: makeWrapper(qc) });

    expect(screen.getByText(/請移除下方標示「已過期」的檔案並重新上傳/)).toBeInTheDocument();
  });

  it('does not show the banner when no files are expired', () => {
    vi.mocked(useSessionDetail).mockReturnValue(makeSession([]));

    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<ChatPanel sessionId="s1" />, { wrapper: makeWrapper(qc) });

    expect(screen.queryByText(/請移除下方標示「已過期」的檔案並重新上傳/)).toBeNull();
  });

  it('QuickChips buttons are disabled when expired files are present', () => {
    vi.mocked(useSessionDetail).mockReturnValue(makeSessionWithExpiredFile());

    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<ChatPanel sessionId="s1" />, { wrapper: makeWrapper(qc) });

    // QuickChips receives disabled=true and sets disabled attribute on its buttons.
    const firstChip = screen.getByText(QUICK_PROMPTS[0].label);
    expect(firstChip).toBeDisabled();
  });

  it('send is not called when expired files are present and user tries to submit', async () => {
    const mockSend = vi.fn().mockResolvedValue(undefined);
    vi.mocked(useAgentStream).mockReturnValue({
      state: IDLE_STATE,
      send: mockSend,
      stop: vi.fn(),
      reset: vi.fn(),
    });
    vi.mocked(useSessionDetail).mockReturnValue(makeSessionWithExpiredFile());

    const user = userEvent.setup();
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<ChatPanel sessionId="s1" />, { wrapper: makeWrapper(qc) });

    const textarea = screen.getByPlaceholderText(/Ask eRD AI/i);
    // Textarea is disabled so typing and Enter do nothing
    await act(async () => {
      await user.type(textarea, 'hello{Enter}');
    });

    expect(mockSend).not.toHaveBeenCalled();
  });
});
