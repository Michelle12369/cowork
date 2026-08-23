import { getAuthHeaders } from '@/api/apiClient';
import { createSseParser } from '@/utils/sseParser';
import type { AgentEvent } from '@/types';

export interface SendMessageArgs {
  sessionId: string;
  question: string;
  baseArtifactId?: string;
  /** User-selected connector group names (§11 connector selection) scoping which data-source
   *  tools the analysis provider may use this turn. Omitted/empty means all groups — backend
   *  treats a missing/empty selectedGroups as backward-compatible default. */
  selectedGroups?: string[];
  signal: AbortSignal;
}

export class AgentStreamHttpError extends Error {
  readonly code: string;
  constructor(code: string, message: string) {
    super(message);
    this.name = 'AgentStreamHttpError';
    this.code = code;
  }
}

/** POSTs a chat message and yields decoded AgentEvents until the stream closes. Throws
 *  AgentStreamHttpError on non-2xx before the stream opens; AbortError propagates as-is. */
export async function* streamAgentMessage(
  args: SendMessageArgs,
): AsyncGenerator<AgentEvent, void, void> {
  const { sessionId, question, baseArtifactId, selectedGroups, signal } = args;

  const bodyPayload: { question: string; baseArtifactId?: string; selectedGroups?: string[] } = {
    question,
  };
  if (baseArtifactId !== undefined) {
    bodyPayload.baseArtifactId = baseArtifactId;
  }
  if (selectedGroups !== undefined && selectedGroups.length > 0) {
    bodyPayload.selectedGroups = selectedGroups;
  }

  const response = await fetch(`/api/sessions/${sessionId}/messages`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
      ...getAuthHeaders(),
    },
    body: JSON.stringify(bodyPayload),
    signal,
  });

  if (!response.ok) {
    let code = String(response.status);
    let message = 'Unknown error';
    try {
      const json = (await response.json()) as { code?: string; message?: string };
      if (json.code) code = json.code;
      if (json.message) message = json.message;
    } catch {
      // body was not JSON — keep defaults
    }
    throw new AgentStreamHttpError(code, message);
  }

  if (!response.body) {
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();

  const pending: AgentEvent[] = [];
  const parser = createSseParser((event) => {
    pending.push(event);
  });

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      parser.feed(decoder.decode(value, { stream: true }));
      while (pending.length > 0) {
        yield pending.shift()!;
      }
    }
    // Flush remaining buffered bytes, then finalize
    parser.feed(decoder.decode());
    parser.flush();
    while (pending.length > 0) {
      yield pending.shift()!;
    }
  } finally {
    // Cancel before releasing the lock so the browser frees the connection immediately;
    // errors are swallowed since the caller already has what it needs.
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
