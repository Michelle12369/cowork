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

    const postResult = (callId: string, result: McpResult): void => {
      const pending = pendingById.get(callId);
      if (!pending) return;
      pendingById.delete(callId);
      clearTimeout(pending.timer);
      const currentWindow = iframeRef.current?.contentWindow;
      if (!currentWindow || currentWindow !== pending.sourceWindow) return;
      currentWindow.postMessage({ type: 'erd-mcp-result', id: callId, result }, '*');
    };

    const handleMessage = (event: MessageEvent): void => {
      if (!artifactId) return;
      const sourceWindow = iframeRef.current?.contentWindow;
      if (!sourceWindow || event.source !== sourceWindow) return;
      if (!isMcpCallMessage(event.data)) return;

      const { id: callId, connector, tool, args } = event.data;
      const previous = pendingById.get(callId);
      if (previous) clearTimeout(previous.timer);
      const timer = setTimeout(() => {
        postResult(callId, {
          error: {
            code: 'RETRYABLE',
            message: `host timeout after ${MCP_BRIDGE_TIMEOUT_MS / 1000} s`,
          },
        });
      }, MCP_BRIDGE_TIMEOUT_MS);
      pendingById.set(callId, { sourceWindow, timer });

      callArtifactMcp(artifactId, { connector, tool, args })
        .then((result) => postResult(callId, result))
        .catch((error: unknown) => postResult(callId, foldMcpFailure(error)));
    };

    window.addEventListener('message', handleMessage);
    return (): void => {
      window.removeEventListener('message', handleMessage);
      for (const pending of pendingById.values()) clearTimeout(pending.timer);
      pendingById.clear();
    };
  }, [iframeRef, artifactId]);
}
