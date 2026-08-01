import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import StepChain from './StepChain';
import type { StepItem, TableResult } from '@/types';

const makeStep = (stepKey: string, title: string, status: StepItem['status']): StepItem => ({
  stepKey,
  title,
  description: null,
  status,
});

test('renders PENDING step title', () => {
  render(<StepChain steps={[makeStep('s1', 'Pending Step', 'PENDING')]} />);
  expect(screen.getByText('Pending Step')).toBeInTheDocument();
});

test('renders RUNNING step title', () => {
  render(<StepChain steps={[makeStep('s2', 'Running Step', 'RUNNING')]} />);
  expect(screen.getByText('Running Step')).toBeInTheDocument();
});

test('renders SUCCESS step title', () => {
  render(<StepChain steps={[makeStep('s3', 'Success Step', 'SUCCESS')]} />);
  expect(screen.getByText('Success Step')).toBeInTheDocument();
});

test('renders ERROR step title', () => {
  render(<StepChain steps={[makeStep('s4', 'Error Step', 'ERROR')]} />);
  expect(screen.getByText('Error Step')).toBeInTheDocument();
});

test('renders dynamic step with d* key alongside static steps', () => {
  const steps: StepItem[] = [
    makeStep('s1', 'Static Step', 'SUCCESS'),
    makeStep('d1', 'Dynamic Step A', 'RUNNING'),
    makeStep('d2', 'Dynamic Step B', 'PENDING'),
  ];
  render(<StepChain steps={steps} />);
  expect(screen.getByText('Static Step')).toBeInTheDocument();
  expect(screen.getByText('Dynamic Step A')).toBeInTheDocument();
  expect(screen.getByText('Dynamic Step B')).toBeInTheDocument();
});

// ── tables collapsed under their step ──────────────────────────────────────────

const makeTable = (tableId: string, intent: string): TableResult => ({
  tableId,
  intent,
  columns: ['col'],
  rows: [['v']],
  truncated: false,
});

test('a step with a matching table (shared run id) renders a collapsed expander under it', () => {
  const steps = [makeStep('tool_run_sql_run-1', '查詢資料', 'SUCCESS')];
  const tables = [makeTable('tbl_run-1', '各機台不良率')];

  render(<StepChain steps={steps} tables={tables} />);

  expect(screen.getByText('查詢資料')).toBeInTheDocument();
  // Collapsed by default — the intent chip shows, but the table body is not yet in the DOM.
  expect(screen.getByText(/各機台不良率/)).toBeInTheDocument();
  expect(screen.queryByText('v')).toBeNull();
});

test('clicking the expander under a step reveals the full table', async () => {
  const user = userEvent.setup();
  const steps = [makeStep('tool_run_sql_run-1', '查詢資料', 'SUCCESS')];
  const tables = [makeTable('tbl_run-1', '各機台不良率')];

  render(<StepChain steps={steps} tables={tables} />);

  await user.click(screen.getByRole('button', { name: /各機台不良率/ }));
  expect(screen.getByText('v')).toBeInTheDocument();
});

test('a step with no matching table renders no expander', () => {
  const steps = [makeStep('tool_run_sql_run-1', '查詢資料', 'SUCCESS')];

  render(<StepChain steps={steps} />);

  expect(screen.queryByTestId('collapsible-result-table')).toBeNull();
});

test("two steps each get their own matching table, not the other one's", () => {
  const steps = [
    makeStep('tool_run_sql_run-1', '查詢一', 'SUCCESS'),
    makeStep('tool_run_sql_run-2', '查詢二', 'SUCCESS'),
  ];
  const tables = [makeTable('tbl_run-1', '第一份表格'), makeTable('tbl_run-2', '第二份表格')];

  render(<StepChain steps={steps} tables={tables} />);

  expect(screen.getAllByTestId('collapsible-result-table')).toHaveLength(2);
  expect(screen.getByText(/第一份表格/)).toBeInTheDocument();
  expect(screen.getByText(/第二份表格/)).toBeInTheDocument();
});
