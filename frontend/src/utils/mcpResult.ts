import axios from 'axios';
import type { McpResult } from '@/types';

/** Folds a failed /mcp-call request into the error envelope the page expects. Only a safety net:
 *  Java already folds agent-service failures, so this sees Java's own 404/400/5xx and network. */
export function foldMcpFailure(error: unknown): McpResult {
  if (!axios.isAxiosError(error)) {
    return { error: { code: 'RETRYABLE', message: 'unexpected host failure; retry' } };
  }
  const status = error.response?.status;
  if (status === undefined) {
    return { error: { code: 'RETRYABLE', message: 'network error reaching the host; retry' } };
  }
  if (status === 401 || status === 403 || status === 404) {
    return {
      error: { code: 'AUTH', message: `host returned HTTP ${status}; sign in again or reload` },
    };
  }
  if (status === 400 || status === 422) {
    return {
      error: {
        code: 'INVALID_CALL',
        message: `host rejected the call (HTTP ${status}); fix the dashboard`,
      },
    };
  }
  return { error: { code: 'RETRYABLE', message: `host returned HTTP ${status}; retry` } };
}
