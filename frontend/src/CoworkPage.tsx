import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useSuspenseQuery, useQueryClient } from '@tanstack/react-query';
import { App } from 'antd';
import axios from 'axios';
import ChatHistorySidebar from './components/chat/ChatHistorySidebar';
import ArtifactPanel from '@/components/artifact/ArtifactPanel';
import type { ArtifactRef } from '@/components/artifact/ArtifactPanel';
import SuspenseLoader from '@/components/common/SuspenseLoader';
import ChatPanel from '@/components/chat/ChatPanel';
import { listSessions } from '@/api/sessionApi';
import { repairArtifact } from '@/api/artifactApi';
import type { ArtifactVersion, BrowserJsError, SessionDetail, SessionSummary } from '@/types';
import { DRAFT_SESSION_TITLE } from '@/constants/messages';

/** Client-side draft matching the real SessionDetail shape; title MUST match the backend
 *  default so the sidebar label is correct before the first question renames the session. */
function emptySessionDetail(id: string, createdAt: string): SessionDetail {
  return {
    id,
    title: DRAFT_SESSION_TITLE,
    createdAt,
    messages: [],
    files: [],
  };
}

interface RepairOffer {
  artifactId: string;
  errors: BrowserJsError[];
  status: 'pending' | 'repairing' | 'failed';
}

const CoworkPage: React.FC = () => {
  const queryClient = useQueryClient();
  const { message } = App.useApp();
  const { data: sessions } = useSuspenseQuery({
    queryKey: ['sessions'],
    queryFn: listSessions,
  });

  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  /** ISO timestamp captured when the current draft was created; used as the synthetic sidebar entry's updatedAt. */
  const [draftStartedAt, setDraftStartedAt] = useState<string | null>(null);

  // Active artifact displayed on the right panel
  const [activeArtifact, setActiveArtifact] = useState<ArtifactRef | null>(null);
  // Ordered version list from the current session's history
  const [artifacts, setArtifacts] = useState<ArtifactVersion[]>([]);
  // Whether ChatPanel is currently streaming (drives ArtifactPanel's regenerating state)
  const [isStreaming, setIsStreaming] = useState(false);

  // Repair offer state — set when the iframe reports errors; cleared on confirm/dismiss/session switch
  const [repairOffer, setRepairOffer] = useState<RepairOffer | null>(null);
  const [repairReloadNonce, setRepairReloadNonce] = useState(0);
  // Tracks artifacts the user has dismissed so we don't re-show the card for the same artifact
  const dismissedArtifactsRef = useRef<Set<string>>(new Set());

  // Connector ids picked before the session locks in; session-scoped, reset on session
  // switch/new draft alongside the rest of resetSessionScopedState.
  const [selectedConnectorIds, setSelectedConnectorIds] = useState<string[]>([]);

  /** True when the active session id is absent from the server list — i.e. the user is in a
   *  client-side draft that has not yet been persisted. Derived, not duplicated as state. */
  const isDraftActive =
    activeSessionId !== null && !sessions.some((session) => session.id === activeSessionId);

  /** Combined list for the sidebar: prepends a synthetic SessionSummary for an active draft so
   *  the user can see it before the first send/upload materializes it server-side. */
  const displayedSessions = useMemo((): SessionSummary[] => {
    if (!isDraftActive || activeSessionId === null || draftStartedAt === null) return sessions;
    const syntheticEntry: SessionSummary = {
      id: activeSessionId,
      title: DRAFT_SESSION_TITLE,
      // Same timestamp as the cache shell's createdAt, set in one batched render in startDraftSession.
      updatedAt: draftStartedAt,
    };
    return [syntheticEntry, ...sessions];
  }, [sessions, activeSessionId, isDraftActive, draftStartedAt]);

  /** Clears artifact/repair/streaming state scoped to a session. Must run synchronously with
   *  the session switch, before any new ChatPanel mounts and runs its auto-show effects. */
  const resetSessionScopedState = useCallback((): void => {
    setActiveArtifact(null);
    setArtifacts([]);
    setIsStreaming(false);
    setRepairOffer(null);
    setRepairReloadNonce(0);
    dismissedArtifactsRef.current.clear();
    setSelectedConnectorIds([]);
  }, []);

  /** Seeds the query cache with an empty draft; the session is NOT created server-side until
   *  the first send/upload (both endpoints upsert on missing session). */
  const startDraftSession = useCallback((): void => {
    const draftSessionId = crypto.randomUUID();
    // Capture timestamp once — reused for both the cache shell's createdAt and the
    // synthetic sidebar entry's updatedAt, so Date is only called once.
    const timestamp = new Date().toISOString();
    resetSessionScopedState();
    setDraftStartedAt(timestamp);
    queryClient.setQueryData(
      ['session', draftSessionId],
      emptySessionDetail(draftSessionId, timestamp),
    );
    setActiveSessionId(draftSessionId);
  }, [queryClient, resetSessionScopedState]);

  // Auto-start a draft when the list is empty; runs at most once since activeSessionId
  // becomes non-null after the first call.
  useEffect(() => {
    if (sessions.length === 0 && activeSessionId === null) {
      startDraftSession();
    }
  }, [sessions, activeSessionId, startDraftSession]);

  // Default active to first session when list changes and nothing is selected
  useEffect(() => {
    if (sessions.length > 0 && activeSessionId === null) {
      setActiveSessionId(sessions[0].id);
    }
  }, [sessions, activeSessionId]);

  // Session-scoped state resets synchronously in handleSelectSession/startDraftSession, not an
  // effect keyed on activeSessionId — an effect raced ChatPanel's auto-show effect.

  // Clear the repair offer when the active artifact changes to one that doesn't match the offer
  useEffect(() => {
    if (repairOffer && repairOffer.artifactId !== activeArtifact?.artifactId) {
      setRepairOffer(null);
    }
  }, [activeArtifact?.artifactId, repairOffer]);

  /** Called by ArtifactPanel when the iframe reports JS errors. Shows the repair offer card. */
  const handleRuntimeErrors = useCallback(
    (artifactId: string, errors: BrowserJsError[]): void => {
      if (artifactId !== activeArtifact?.artifactId) return;
      if (dismissedArtifactsRef.current.has(artifactId)) return;
      if (repairOffer !== null) return;
      setRepairOffer({ artifactId, errors, status: 'pending' });
    },
    [activeArtifact?.artifactId, repairOffer],
  );

  /** Called when the user confirms the repair offer in the chat panel. */
  const handleRepairConfirm = useCallback(async (): Promise<void> => {
    if (!repairOffer) return;
    const { artifactId, errors } = repairOffer;
    setRepairOffer((prev) => (prev ? { ...prev, status: 'repairing' } : null));
    try {
      const repaired = await repairArtifact(artifactId, errors);
      if (repaired) {
        setRepairOffer(null);
        void queryClient.invalidateQueries({ queryKey: ['session', activeSessionId] });
        dismissedArtifactsRef.current.delete(artifactId);
        setRepairReloadNonce((prev) => prev + 1);
        void message.success('已修復，儀表板已重新載入');
      } else {
        setRepairOffer((prev) => (prev ? { ...prev, status: 'failed' } : null));
        void queryClient.invalidateQueries({ queryKey: ['session', activeSessionId] });
      }
    } catch (err: unknown) {
      const isFilesExpired =
        axios.isAxiosError(err) &&
        (err.response?.data as { code?: string } | undefined)?.code === 'FILES_EXPIRED';
      if (isFilesExpired) {
        void message.error('檔案已過期，無法修復此儀表板');
        setRepairOffer(null);
      } else {
        setRepairOffer((prev) => (prev ? { ...prev, status: 'failed' } : null));
        void queryClient.invalidateQueries({ queryKey: ['session', activeSessionId] });
      }
    }
  }, [repairOffer, activeSessionId, message, queryClient]);

  /** Called when the user dismisses the repair offer. Suppresses future offers for this artifact. */
  const handleRepairDismiss = useCallback((): void => {
    if (!repairOffer) return;
    dismissedArtifactsRef.current.add(repairOffer.artifactId);
    setRepairOffer(null);
  }, [repairOffer]);

  const handleNew = useCallback((): void => {
    // No-op while already in a draft — prevents orphaned cache seeds stacking up.
    if (isDraftActive) return;
    startDraftSession();
  }, [isDraftActive, startDraftSession]);

  /** Resets session-scoped state synchronously before updating activeSessionId, so the wipe
   *  happens before any new ChatPanel mounts. Clicking the active entry is a no-op. */
  const handleSelectSession = useCallback(
    (sessionId: string): void => {
      if (sessionId === activeSessionId) return;
      resetSessionScopedState();
      setActiveSessionId(sessionId);
    },
    [activeSessionId, resetSessionScopedState],
  );

  const handleCollapse = useCallback(() => setSidebarOpen(false), []);
  const handleExpand = useCallback(() => setSidebarOpen(true), []);

  const handleArtifactChange = useCallback((artifact: ArtifactRef | null): void => {
    setActiveArtifact(artifact);
  }, []);

  const handleArtifactsChange = useCallback((list: ArtifactVersion[]): void => {
    setArtifacts(list);
  }, []);

  /** Called when the user picks a version from the ArtifactPanel dropdown. */
  const handleSelectArtifact = useCallback((artifact: ArtifactRef): void => {
    setActiveArtifact(artifact);
  }, []);

  const handleStreamingChange = useCallback((streaming: boolean): void => {
    setIsStreaming(streaming);
  }, []);

  const handleSelectedConnectorsChange = useCallback((ids: string[]): void => {
    setSelectedConnectorIds(ids);
  }, []);

  return (
    <div className="flex h-screen overflow-hidden bg-gray-100">
      {sidebarOpen && (
        <ChatHistorySidebar
          sessions={displayedSessions}
          activeId={activeSessionId}
          onSelect={handleSelectSession}
          onNew={handleNew}
          onCollapse={handleCollapse}
        />
      )}
      {activeSessionId && (
        <SuspenseLoader>
          <ChatPanel
            key={activeSessionId}
            sessionId={activeSessionId}
            sidebarOpen={sidebarOpen}
            onExpandSidebar={handleExpand}
            onArtifactChange={handleArtifactChange}
            onStreamingChange={handleStreamingChange}
            onArtifactsChange={handleArtifactsChange}
            currentArtifact={activeArtifact}
            repairOffer={
              repairOffer ? { errors: repairOffer.errors, status: repairOffer.status } : null
            }
            onRepairConfirm={handleRepairConfirm}
            onRepairDismiss={handleRepairDismiss}
            selectedConnectorIds={selectedConnectorIds}
            onSelectedConnectorsChange={handleSelectedConnectorsChange}
          />
        </SuspenseLoader>
      )}
      <ArtifactPanel
        artifact={activeArtifact}
        artifacts={artifacts}
        onSelectArtifact={handleSelectArtifact}
        regenerating={isStreaming}
        onRuntimeErrors={handleRuntimeErrors}
        reloadNonce={repairReloadNonce}
      />
    </div>
  );
};

export default CoworkPage;
