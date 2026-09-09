import React, { Suspense, useCallback, useEffect, useRef, useState } from 'react';
import { App, Button } from 'antd';
import { MenuUnfoldOutlined } from '@ant-design/icons';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useSessionDetail } from '@/hooks/useSessionDetail';
import { useAgentStream } from '@/hooks/useAgentStream';
import { useAppConfig } from '@/hooks/useAppConfig';
import { deleteFile } from '@/api/fileApi';
import AttachmentsPopover from '@/components/files/AttachmentsPopover';
import FileChips from '@/components/files/FileChips';
import ErrorBoundary from '@/components/common/ErrorBoundary';
import ConnectorPicker from '@/components/connectors/ConnectorPicker';
import QuickChips from './QuickChips';
import PromptSender from './PromptSender';
import UploadModal from '@/components/files/UploadModal';
import MessageList from './MessageList';
import RepairOfferCard from './RepairOfferCard';
import type { ArtifactRef } from '@/components/artifact/ArtifactPanel';
import type { ArtifactVersion, BrowserJsError } from '@/types';

const noop = (): void => {};

interface ChatPanelProps {
  sessionId: string;
  sidebarOpen?: boolean;
  onExpandSidebar?: () => void;
  /** Called whenever the active artifact changes (live stream or session history). */
  onArtifactChange?: (artifact: ArtifactRef | null) => void;
  /** Called whenever isStreaming flips. */
  onStreamingChange?: (isStreaming: boolean) => void;
  /** Called on mount and whenever session.messages changes; reports the ordered list of artifact versions.
   *  Called with [] on cleanup (unmount / session switch). */
  onArtifactsChange?: (list: ArtifactVersion[]) => void;
  /** Active artifact from the parent (CoworkPage); gates the history-fallback effect and is
   *  used as baseArtifactId so iteration builds on the selected version. */
  currentArtifact?: ArtifactRef | null;
  /** When set, a repair offer card is shown at the bottom of the chat thread. */
  repairOffer?: { errors: BrowserJsError[]; status: 'pending' | 'repairing' | 'failed' } | null;
  onRepairConfirm?: () => void;
  onRepairDismiss?: () => void;
  /** Connector ids picked before the session is locked; owned by CoworkPage so a session
   *  switch/new draft resets it alongside the rest of the session-scoped state. */
  selectedConnectorIds?: string[];
  onSelectedConnectorsChange?: (ids: string[]) => void;
}

const ChatPanel: React.FC<ChatPanelProps> = ({
  sessionId,
  sidebarOpen = true,
  onExpandSidebar,
  onArtifactChange,
  onStreamingChange,
  onArtifactsChange,
  currentArtifact,
  repairOffer,
  onRepairConfirm,
  onRepairDismiss,
  selectedConnectorIds = [],
  onSelectedConnectorsChange = noop,
}) => {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const { retentionDays } = useAppConfig();
  const session = useSessionDetail(sessionId);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [pendingQuestion, setPendingQuestion] = useState<string | null>(null);
  const [prefill, setPrefill] = useState('');
  const [questionsAnswered, setQuestionsAnswered] = useState(false);
  const [lastTurnDurationMs, setLastTurnDurationMs] = useState<number | null>(null);
  const { state, send, stop, reset } = useAgentStream(sessionId);
  const prevStreamingRef = useRef(false);
  // Ref that always holds the latest onArtifactsChange so the unmount cleanup
  // can call it without becoming a dep of the unmount-only effect.
  const onArtifactsChangeRef = useRef(onArtifactsChange);
  useEffect(() => {
    onArtifactsChangeRef.current = onArtifactsChange;
  });

  const deleteMutation = useMutation({
    mutationFn: (fileId: string) => deleteFile(sessionId, fileId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['session', sessionId] });
    },
    onError: () => {
      message.error('Failed to remove file');
    },
  });

  // Clear pending question when streaming ends; defer reset() while questions are present
  // so the user can still interact with QuestionCards.
  useEffect(() => {
    if (prevStreamingRef.current && !state.isStreaming) {
      setPendingQuestion(null);
      setLastTurnDurationMs(state.durationMs);
      if (!state.questions) {
        reset();
      }
    }
    prevStreamingRef.current = state.isStreaming;
  }, [state.isStreaming, state.questions, state.durationMs, reset]);

  // Reset questionsAnswered and the captured duration when a new stream starts
  useEffect(() => {
    if (state.isStreaming) {
      setQuestionsAnswered(false);
      setLastTurnDurationMs(null);
    }
  }, [state.isStreaming]);

  // Show error messages
  useEffect(() => {
    if (state.error) {
      void message.error(state.error.message);
    }
  }, [state.error, message]);

  // Report active artifact: live stream takes priority, else fall back to history only when
  // currentArtifact is null (prevents invalidation from overriding the parent's selection).
  useEffect(() => {
    if (onArtifactChange) {
      if (state.artifact) {
        // A brand-new live-stream artifact always takes over.
        onArtifactChange(state.artifact);
      } else if (!currentArtifact) {
        // History fallback: only when the parent has nothing selected yet.
        const lastArtifactMsg = [...session.messages].reverse().find((msg) => msg.artifactId);
        if (lastArtifactMsg?.artifactId) {
          onArtifactChange({
            artifactId: lastArtifactMsg.artifactId,
            title: lastArtifactMsg.artifactTitle ?? lastArtifactMsg.text.slice(0, 50),
          });
        } else {
          onArtifactChange(null);
        }
      }
    }
  }, [state.artifact, session.messages, onArtifactChange, currentArtifact]);

  // No cleanup here — clearing happens in the unmount-only effect below so dep-change
  // re-runs never emit a transient [] between the old and new lists.
  useEffect(() => {
    const versions: ArtifactVersion[] = session.messages
      .filter((msg) => msg.artifactId != null)
      .map((msg, index) => ({
        artifactId: msg.artifactId!,
        title: msg.artifactTitle ?? msg.text.slice(0, 50),
        version: index + 1,
      }));
    onArtifactsChange?.(versions);
  }, [session.messages, onArtifactsChange]);

  // Report [] only when the component truly unmounts (session switch / page leave).
  // Uses a ref so this effect stays dep-free and never re-runs mid-lifecycle.
  useEffect(() => {
    return () => {
      onArtifactsChangeRef.current?.([]);
    };
  }, []);

  // Report streaming state changes
  useEffect(() => {
    onStreamingChange?.(state.isStreaming);
  }, [state.isStreaming, onStreamingChange]);

  const handleRemove = useCallback(
    (fileId: string) => {
      deleteMutation.mutate(fileId);
    },
    [deleteMutation.mutate],
  );

  const handleOpenUpload = useCallback((): void => setUploadOpen(true), []);
  const handleCloseUpload = useCallback((): void => setUploadOpen(false), []);

  // QuickChips onPick: fill the input so the user can review/edit before sending.
  const handleChipPick = useCallback((prompt: string): void => {
    setPrefill(prompt);
  }, []);

  const handlePrefillConsumed = useCallback((): void => {
    setPrefill('');
  }, []);

  // Send a message, passing the currently selected artifact as the base for iteration and
  // any pre-lock connector selection (ignored by the backend once locked/if files exist).
  const handleSend = useCallback(
    (text: string) => {
      if (state.isStreaming) return;
      setPendingQuestion(text);
      void send(
        text,
        currentArtifact?.artifactId,
        selectedConnectorIds.length > 0 ? selectedConnectorIds : undefined,
      );
    },
    [state.isStreaming, send, currentArtifact, selectedConnectorIds],
  );

  // Called when the user answers a QuestionCard; disables cards before triggering send.
  const handleAnswer = useCallback(
    (text: string) => {
      setQuestionsAnswered(true);
      handleSend(text);
    },
    [handleSend],
  );

  const handleArtifactClick = useCallback(
    (artifact: ArtifactRef) => {
      onArtifactChange?.(artifact);
    },
    [onArtifactChange],
  );

  const hasMessages = session.messages.length > 0;
  // Include pending questions in hasLive so the live bubble persists after streaming ends
  // (allowing the user to interact with QuestionCards before the bubble resets).
  const hasLive =
    state.isStreaming ||
    !!state.liveText ||
    state.steps.length > 0 ||
    !!state.questions ||
    state.stopped ||
    state.networkError;
  const liveState = hasLive ? state : null;

  // Non-expired file names forwarded to the live bubble for the "using files" row.
  const fileNames = session.files.filter((file) => !file.expired).map((file) => file.name);

  // True when the session has at least one file that was removed by the retention policy.
  // In this state the user must clean up expired files before sending a new message.
  const hasExpiredFiles = session.files.some((file) => file.expired);

  // True when the session already has any active (non-expired) file attached — disables the
  // connector picker (files and connectors don't mix).
  const hasActiveFiles = fileNames.length > 0;

  // True once the session is locked into connector mode server-side (non-empty selectedConnectors).
  const isConnectorsLocked = (session.selectedConnectors?.length ?? 0) > 0;

  // True when connectors are chosen (locally, pre-lock) or already locked — disables the
  // upload entry, mirroring the picker's own disabled-on-active-files behavior.
  const connectorsChosen = isConnectorsLocked || selectedConnectorIds.length > 0;

  // Show optimistic user bubble if pending question not yet in history
  const lastHistoryQuestion =
    hasMessages && session.messages[session.messages.length - 1]?.sender === 'USER'
      ? session.messages[session.messages.length - 1].text
      : null;
  const optimisticUserText =
    pendingQuestion !== null && pendingQuestion !== lastHistoryQuestion ? pendingQuestion : null;

  return (
    <div className="flex min-w-[360px] max-w-[560px] flex-1 flex-col border-r border-gray-200 bg-white">
      {/* Header */}
      <div className="flex items-center gap-2 border-b border-gray-200 px-5 py-3">
        {!sidebarOpen && onExpandSidebar && (
          <Button size="small" icon={<MenuUnfoldOutlined />} onClick={onExpandSidebar} />
        )}
        <div className="min-w-0 flex-1">
          <div className="text-[15px] font-semibold">Cowork · Data studio</div>
          <div className="mt-0.5 text-xs text-gray-400">
            Import data, prompt eRD AI, get a dashboard or a deck.
          </div>
        </div>
        <div className="flex flex-none items-center gap-2">
          <ErrorBoundary fallback={null}>
            <Suspense fallback={null}>
              <ConnectorPicker
                selectedIds={selectedConnectorIds}
                onChange={onSelectedConnectorsChange}
                hasActiveFiles={hasActiveFiles}
                lockedConnectorIds={session.selectedConnectors}
              />
            </Suspense>
          </ErrorBoundary>
          <AttachmentsPopover
            files={session.files}
            onRemove={handleRemove}
            onAttach={handleOpenUpload}
            disabled={connectorsChosen}
            disabledReason="已選擇資料源連接器，如需上傳檔案請先開新對話"
          />
        </div>
      </div>

      {/* Thread area */}
      {!hasMessages && !hasLive ? (
        <div className="flex flex-1 items-center justify-center text-sm text-gray-400">
          Ask eRD AI to analyze your data…
        </div>
      ) : (
        <div className="flex flex-1 flex-col overflow-hidden">
          <MessageList
            messages={session.messages}
            live={liveState}
            optimisticUserText={optimisticUserText}
            onArtifactClick={handleArtifactClick}
            onAnswer={handleAnswer}
            questionsDisabled={questionsAnswered}
            fileNames={fileNames}
            lastTurnDurationMs={lastTurnDurationMs}
            bottomSlot={
              repairOffer ? (
                <div className="pb-3">
                  <RepairOfferCard
                    errorCount={repairOffer.errors.length}
                    firstErrorMessage={repairOffer.errors[0]?.message ?? ''}
                    status={repairOffer.status}
                    onConfirm={onRepairConfirm ?? noop}
                    onDismiss={onRepairDismiss ?? noop}
                  />
                </div>
              ) : null
            }
          />
        </div>
      )}

      {/* Footer */}
      <div className="flex-none border-t border-gray-200 px-5 py-3">
        {hasExpiredFiles && (
          <div className="mb-2 rounded-lg border border-amber-200 bg-amber-50 p-2.5 text-xs text-amber-800">
            部分檔案已超過 {retentionDays}{' '}
            天未活動，內容已被系統清除。請移除下方標示「已過期」的檔案並重新上傳，即可繼續對話。
          </div>
        )}
        <FileChips files={session.files} onRemove={handleRemove} />
        <QuickChips onPick={handleChipPick} disabled={state.isStreaming || hasExpiredFiles} />
        <PromptSender
          onSend={handleSend}
          onStop={stop}
          onAttach={connectorsChosen ? undefined : handleOpenUpload}
          disabled={state.isStreaming || hasExpiredFiles}
          isStreaming={state.isStreaming}
          prefill={prefill}
          onPrefillConsumed={handlePrefillConsumed}
        />
      </div>

      <UploadModal
        open={uploadOpen}
        sessionId={sessionId}
        existingFiles={session.files}
        onClose={handleCloseUpload}
      />
    </div>
  );
};

export default ChatPanel;
