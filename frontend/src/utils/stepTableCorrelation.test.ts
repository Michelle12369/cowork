import { groupTablesByStepKey } from './stepTableCorrelation';
import type { StepItem, TableResult } from '@/types';

function makeStep(stepKey: string): StepItem {
  return { stepKey, title: 'title', description: null, status: 'SUCCESS' };
}

function makeTable(tableId: string): TableResult {
  return { tableId, intent: 'intent', columns: ['a'], rows: [[1]], truncated: false };
}

test('a table whose tableId shares its run id with a stepKey is grouped under that step', () => {
  const steps = [makeStep('tool_run_sql_abc-123')];
  const tables = [makeTable('tbl_abc-123')];

  const grouped = groupTablesByStepKey(steps, tables);

  expect(grouped.get('tool_run_sql_abc-123')).toEqual(tables[0]);
  expect(grouped.size).toBe(1);
});

test('a tool name containing underscores does not break the run-id suffix match', () => {
  // stepKey = tool_<toolName>_<runId>; toolName itself may contain underscores
  // (e.g. "flag_outliers") — the match must key off the trailing run id, not split on "_".
  const steps = [makeStep('tool_flag_outliers_run-42')];
  const tables = [makeTable('tbl_run-42')];

  const grouped = groupTablesByStepKey(steps, tables);

  expect(grouped.get('tool_flag_outliers_run-42')).toEqual(tables[0]);
});

test('multiple tables are each grouped under their own matching step', () => {
  const steps = [makeStep('tool_run_sql_r1'), makeStep('tool_trend_3sigma_r2')];
  const tables = [makeTable('tbl_r1'), makeTable('tbl_r2')];

  const grouped = groupTablesByStepKey(steps, tables);

  expect(grouped.get('tool_run_sql_r1')?.tableId).toBe('tbl_r1');
  expect(grouped.get('tool_trend_3sigma_r2')?.tableId).toBe('tbl_r2');
  expect(grouped.size).toBe(2);
});

test('a table with no matching step is silently skipped (defensive, should not happen on wire)', () => {
  const steps = [makeStep('tool_run_sql_r1')];
  const tables = [makeTable('tbl_unrelated')];

  const grouped = groupTablesByStepKey(steps, tables);

  expect(grouped.size).toBe(0);
});

test('a malformed tableId (no tbl_ prefix) is silently skipped', () => {
  const steps = [makeStep('tool_run_sql_r1')];
  const tables = [makeTable('not-a-table-id')];

  const grouped = groupTablesByStepKey(steps, tables);

  expect(grouped.size).toBe(0);
});

test('undefined tables returns an empty map', () => {
  expect(groupTablesByStepKey([makeStep('tool_run_sql_r1')], undefined).size).toBe(0);
});

test('empty tables array returns an empty map', () => {
  expect(groupTablesByStepKey([makeStep('tool_run_sql_r1')], []).size).toBe(0);
});
