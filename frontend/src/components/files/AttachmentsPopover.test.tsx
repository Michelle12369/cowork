import { render, screen, fireEvent } from '@testing-library/react';
import { vi } from 'vitest';
import AttachmentsPopover from './AttachmentsPopover';
import type { UploadedFileInfo } from '@/types';

const files: UploadedFileInfo[] = [
  {
    id: 'f1',
    name: 'data.csv',
    alias: 'data',
    sizeBytes: 1_572_864, // 1.5 MB (1.5 * 1024^2)
    type: 'csv',
    rowCount: 1200,
    expired: false,
  },
  {
    id: 'f2',
    name: 'report.xlsx',
    alias: 'report',
    sizeBytes: 500_000,
    type: 'xlsx',
    rowCount: null,
    expired: true,
  },
];

test('renders file name, size and rows meta in popover', async () => {
  render(<AttachmentsPopover files={files} onRemove={() => {}} onAttach={() => {}} />);
  fireEvent.click(screen.getByRole('button', { name: /attachments/i }));
  expect(await screen.findByText('data.csv')).toBeInTheDocument();
  // meta row shows size and row count together
  expect(screen.getByText(/1\.5 MB · 1200 rows/)).toBeInTheDocument();
});

test('calls onRemove with id when remove button is clicked', async () => {
  const onRemove = vi.fn();
  render(<AttachmentsPopover files={files} onRemove={onRemove} onAttach={() => {}} />);
  fireEvent.click(screen.getByRole('button', { name: /attachments/i }));
  fireEvent.click(await screen.findByRole('button', { name: /remove data\.csv/i }));
  expect(onRemove).toHaveBeenCalledWith('f1');
});

test('expired file row shows filename and 已過期清除 meta without remove button', async () => {
  render(<AttachmentsPopover files={files} onRemove={() => {}} onAttach={() => {}} />);
  fireEvent.click(screen.getByRole('button', { name: /attachments/i }));
  // filename is still visible
  expect(await screen.findByText('report.xlsx')).toBeInTheDocument();
  // meta shows 已過期清除
  expect(screen.getByText('已過期清除')).toBeInTheDocument();
  // no remove button for expired file
  expect(screen.queryByRole('button', { name: /remove report\.xlsx/i })).not.toBeInTheDocument();
});

test('header stats exclude expired files: 1 active + 1 expired → shows 1/5', async () => {
  render(<AttachmentsPopover files={files} onRemove={() => {}} onAttach={() => {}} />);
  fireEvent.click(screen.getByRole('button', { name: /attachments/i }));
  expect(await screen.findByText(/1\/5/)).toBeInTheDocument();
});

test('badge count excludes expired files', () => {
  render(<AttachmentsPopover files={files} onRemove={() => {}} onAttach={() => {}} />);
  // 1 active file → badge shows 1
  expect(screen.getByText('1')).toBeInTheDocument();
  // expired file is not counted
  expect(screen.queryByText('2')).not.toBeInTheDocument();
});

test('badge does not show 0 when files is empty', () => {
  render(<AttachmentsPopover files={[]} onRemove={() => {}} onAttach={() => {}} />);
  expect(screen.queryByText('0')).not.toBeInTheDocument();
});
