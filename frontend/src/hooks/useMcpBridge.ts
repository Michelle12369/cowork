import { useEffect, type RefObject } from 'react';
import { callArtifactMcp } from '@/api/artifactApi';
import { MCP_BRIDGE_TIMEOUT_MS } from '@/config/mcpBridge';
import { foldMcpFailure } from '@/utils/mcpResult';
import type { McpCallMessage, McpResult } from '@/types';

interface PendingCall {
  /** The iframe window that issued the call; a remounted iframe gets a new one and old
   *  results must not reach it (call ids restart at 1 per document). */
  sourceWindow: Window;
  timer: ReturnType<typeof setTimeout>;
}

function isMcpCallMessage(data: unknown): data is McpCallMessage {
  if (typeof data !== 'object' || data === null) return false;
  const candidate = data as Record<string, unknown>;
  return (
    candidate.type === 'erd-mcp-call' &&
    typeof candidate.id === 'string' &&
    typeof candidate.connector === 'string' &&
    typeof candidate.tool === 'string' &&
    typeof candidate.args === 'object' &&
    candidate.args !== null
  );
}

/** Host half of mcp(): answers erd-mcp-call from the sandboxed dashboard iframe by calling the
 *  Java proxy and posting erd-mcp-result back. Never reads data; one result per call id. */
export function useMcpBridge(
  iframeRef: RefObject<HTMLIFrameElement>,
  artifactId: string | undefined,
): void {
  useEffect(() => {
    const pendingById = new Map<string, PendingCall>();

    const postResult = (callId: string, pending: PendingCall, result: McpResult): void => {
      // A later call reusing the id (new document after remount) supersedes this one; drop it.
      if (pendingById.get(callId) !== pending) return;
      pendingById.delete(callId);
      clearTimeout(pending.timer);
      if (iframeRef.current?.contentWindow !== pending.sourceWindow) return;
      pending.sourceWindow.postMessage({ type: 'erd-mcp-result', id: callId, result }, '*');
    };

    const handleMessage = (event: MessageEvent): void => {
      if (!artifactId) return;
      const sourceWindow = iframeRef.current?.contentWindow;
      if (!sourceWindow || event.source !== sourceWindow) return;
      if (!isMcpCallMessage(event.data)) return;

      const { id: callId, connector, tool, args } = event.data;
      const previous = pendingById.get(callId);
      if (previous) clearTimeout(previous.timer);
      const pending: PendingCall = {
        sourceWindow,
        timer: setTimeout(() => {
          postResult(callId, pending, {
            error: {
              code: 'RETRYABLE',
              message: `host timeout after ${MCP_BRIDGE_TIMEOUT_MS / 1000} s`,
            },
          });
        }, MCP_BRIDGE_TIMEOUT_MS),
      };
      pendingById.set(callId, pending);

      callArtifactMcp(artifactId, { connector, tool, args })
        .then((result) => postResult(callId, pending, result))
        .catch((error: unknown) => postResult(callId, pending, foldMcpFailure(error)));
    };

    window.addEventListener('message', handleMessage);
    return (): void => {
      window.removeEventListener('message', handleMessage);
      for (const pending of pendingById.values()) clearTimeout(pending.timer);
      pendingById.clear();
    };
  }, [iframeRef, artifactId]);
}
