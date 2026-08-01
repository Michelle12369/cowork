import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import CollapsibleResultTable from './CollapsibleResultTable';
import type { TableResult } from '@/types';

const TABLE: TableResult = {
  tableId: 'tbl_1',
  intent: '各團隊請假時數統計',
  columns: ['team', 'hours'],
  rows: [
    ['A', 10],
    ['B', 20],
    ['C', 30],
    ['D', 40],
  ],
  truncated: false,
};

test('renders a collapsed chip with intent and row count, and does not render the table yet', () => {
  render(<CollapsibleResultTable table={TABLE} />);
  expect(screen.getByText(/各團隊請假時數統計/)).toBeInTheDocument();
  expect(screen.getByText(/4 列/)).toBeInTheDocument();
  // Collapsed by default: the antd Table body (a cell value) must not be in the DOM yet.
  expect(screen.queryByText('A')).toBeNull();
});

test('clicking the chip expands it to the full ResultTable', async () => {
  const user = userEvent.setup();
  render(<CollapsibleResultTable table={TABLE} />);
  await user.click(screen.getByRole('button'));
  expect(screen.getByText('A')).toBeInTheDocument();
  expect(screen.getByText('B')).toBeInTheDocument();
});

test('clicking the chip again collapses it back', async () => {
  const user = userEvent.setup();
  render(<CollapsibleResultTable table={TABLE} />);
  const toggle = screen.getByRole('button');
  await user.click(toggle);
  expect(screen.getByText('A')).toBeInTheDocument();
  await user.click(toggle);
  expect(screen.queryByText('A')).toBeNull();
});
