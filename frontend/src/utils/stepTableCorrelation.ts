import type { StepItem, TableResult } from '@/types';

/**
 * Correlates a TABLE event to the STEP whose tool call produced it. Both ids embed the same
 * LangChain tool run_id (`stepKey = tool_<toolName>_<runId>`, `tableId = tbl_<runId>`) — a
 * structural wire-contract invariant, not an arrival-order guess.
 */
export function groupTablesByStepKey(
  steps: StepItem[],
  tables: TableResult[] | undefined,
): Map<string, TableResult> {
  const tableByStepKey = new Map<string, TableResult>();
  if (!tables || tables.length === 0) return tableByStepKey;

  for (const table of tables) {
    const runIdMatch = /^tbl_(.+)$/.exec(table.tableId);
    if (!runIdMatch) continue;
    const runId = runIdMatch[1];
    const matchingStep = steps.find((step) => step.stepKey.endsWith(`_${runId}`));
    if (matchingStep) {
      tableByStepKey.set(matchingStep.stepKey, table);
    }
  }
  return tableByStepKey;
}
