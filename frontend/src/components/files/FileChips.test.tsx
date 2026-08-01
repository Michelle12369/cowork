import { render, screen, fireEvent } from '@testing-library/react';
import { vi } from 'vitest';
import FileChips from './FileChips';
import type { UploadedFileInfo } from '@/types';

const files: UploadedFileInfo[] = [
  {
    id: 'f1',
    name: 'data.csv',
    alias: 'data',
    sizeBytes: 1_500_000,
    type: 'csv',
    rowCount: 1200,
    expired: false,
  },
  {
    id: 'f2',
    name: 'expired.xlsx',
    alias: 'expired',
    sizeBytes: 500_000,
    type: 'xlsx',
    rowCount: null,
    expired: true,
  },
];

test('renders non-expired file chips', () => {
  render(<FileChips files={files} onRemove={() => {}} />);
  expect(screen.getByText('data.csv')).toBeInTheDocument();
});

test('renders expired file chips with 已過期 label', () => {
  render(<FileChips files={files} onRemove={() => {}} />);
  // Expired file name is still shown
  expect(screen.getByText('expired.xlsx')).toBeInTheDocument();
  // Expiry badge is rendered
  expect(screen.getByText('已過期')).toBeInTheDocument();
});

test('calls onRemove with file id when remove is clicked on active chip', () => {
  const onRemove = vi.fn();
  render(<FileChips files={files} onRemove={onRemove} />);
  fireEvent.click(screen.getByRole('button', { name: /remove data\.csv/i }));
  expect(onRemove).toHaveBeenCalledWith('f1');
});

test('calls onRemove with file id when remove is clicked on expired chip', () => {
  const onRemove = vi.fn();
  render(<FileChips files={files} onRemove={onRemove} />);
  fireEvent.click(screen.getByRole('button', { name: /remove expired\.xlsx/i }));
  expect(onRemove).toHaveBeenCalledWith('f2');
});

test('returns null when files list is empty', () => {
  const { container } = render(<FileChips files={[]} onRemove={() => {}} />);
  expect(container.firstChild).toBeNull();
});
