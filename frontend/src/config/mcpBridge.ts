import type { McpErrorCode } from '@/types';

/** Per-call host timeout, counted from the moment the bridge sends the request; includes
 *  browser connection queueing, accepted. Not part of the contract. */
export const MCP_BRIDGE_TIMEOUT_MS = 60_000;

export const MCP_ERROR_CODES: readonly McpErrorCode[] = [
  'AUTH',
  'RETRYABLE',
  'TOOL_ERROR',
  'INVALID_CALL',
  'CONNECTOR_UNAVAILABLE',
];
