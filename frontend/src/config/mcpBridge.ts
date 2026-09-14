import type { McpErrorCode } from '@/types';

/** Per-call host timeout, counted from the moment the bridge sends the request; includes
 *  browser connection queueing, accepted. Not part of the contract. */
export const MCP_BRIDGE_TIMEOUT_MS = 60_000;

/** Object form so `satisfies` rejects a missing or extra code at compile time. */
const MCP_ERROR_CODE_FLAGS = {
  AUTH: true,
  RETRYABLE: true,
  TOOL_ERROR: true,
  INVALID_CALL: true,
  CONNECTOR_UNAVAILABLE: true,
} satisfies Record<McpErrorCode, true>;

export const MCP_ERROR_CODES: readonly McpErrorCode[] = Object.keys(
  MCP_ERROR_CODE_FLAGS,
) as McpErrorCode[];
