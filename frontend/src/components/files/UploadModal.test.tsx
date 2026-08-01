import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { App } from 'antd';
import { vi } from 'vitest';
import { useAppConfig } from '@/hooks/useAppConfig';
import { fetchSampleDatasets } from '@/api/samplesApi';
import UploadModal from './UploadModal';
import type { UploadedFileInfo } from '@/types';
import type { AppConfig } from '@/api/configApi';

vi.mock('@/api/fileApi');
vi.mock('@/hooks/useAppConfig');
vi.mock('@/api/samplesApi');

const GB = 1024 ** 3;
const MB = 1024 ** 2;

const DEFAULT_LIMITS: AppConfig = {
  retentionDays: 30,
  maxFiles: 5,
  maxSessionBytes: 5 * GB,
  singleFileLimits: {
    csv: 2 * GB,
    xlsx: 200 * MB,
  },
};

function Wrapper({ children }: { children: React.ReactNode }): React.ReactElement {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={queryClient}>
      <App>{children}</App>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  vi.mocked(useAppConfig).mockReturnValue(DEFAULT_LIMITS);
  // Default: no sample datasets, so the picker section renders nothing and
  // pre-existing assertions in this file are unaffected.
  vi.mocked(fetchSampleDatasets).mockResolvedValue([]);
});

test('shows Dragger hint text with dynamic limits when modal is open', () => {
  render(<UploadModal open sessionId="s1" existingFiles={[]} onClose={() => {}} />, {
    wrapper: Wrapper,
  });
  expect(screen.getByText('Drag & drop files here, or browse')).toBeInTheDocument();
  expect(screen.getByText(/Up to 5 files · 5\.0 GB total per chat/)).toBeInTheDocument();
});

test('shows dynamic hint with custom limits', () => {
  vi.mocked(useAppConfig).mockReturnValue({
    ...DEFAULT_LIMITS,
    maxFiles: 3,
    maxSessionBytes: 2 * GB,
  });

  render(<UploadModal open sessionId="s1" existingFiles={[]} onClose={() => {}} />, {
    wrapper: Wrapper,
  });
  expect(screen.getByText(/Up to 3 files · 2\.0 GB total per chat/)).toBeInTheDocument();
});

test('shows warning bar when validation fails', async () => {
  // 5 active files already attached — one more file breaks the count rule
  const existingFiles: UploadedFileInfo[] = Array.from({ length: 5 }, (_, index) => ({
    id: `f${index}`,
    name: `file${index}.csv`,
    alias: `f${index}`,
    sizeBytes: 1_000,
    type: 'csv',
    rowCount: null,
    expired: false,
  }));

  render(<UploadModal open sessionId="s1" existingFiles={existingFiles} onClose={() => {}} />, {
    wrapper: Wrapper,
  });

  const input = document.querySelector('input[type="file"]') as HTMLInputElement;
  const file = new File(['a,b\n1,2'], 'extra.csv', { type: 'text/csv' });
  fireEvent.change(input, { target: { files: [file] } });

  await waitFor(() => {
    expect(screen.getByText('You can attach up to 5 files per chat.')).toBeInTheDocument();
  });
});

test('shows Attached · 2/5 header and file entries when existingFiles has two active files', () => {
  const existingFiles: UploadedFileInfo[] = [
    {
      id: 'f1',
      name: 'sales.csv',
      alias: 'f1',
      sizeBytes: 2_048,
      type: 'csv',
      rowCount: 100,
      expired: false,
    },
    {
      id: 'f2',
      name: 'data.xlsx',
      alias: 'f2',
      sizeBytes: 1_048_576,
      type: 'xlsx',
      rowCount: null,
      expired: false,
    },
  ];

  render(<UploadModal open sessionId="s1" existingFiles={existingFiles} onClose={() => {}} />, {
    wrapper: Wrapper,
  });

  expect(screen.getByText('Attached · 2/5')).toBeInTheDocument();
  expect(screen.getByText('sales.csv')).toBeInTheDocument();
  expect(screen.getByText(/2\.0 KB/)).toBeInTheDocument();
  expect(screen.getByText(/100 rows/)).toBeInTheDocument();
  expect(screen.getByText('data.xlsx')).toBeInTheDocument();
  expect(screen.getByText('1.0 MB')).toBeInTheDocument();
});

test('shows No files yet. when existingFiles is empty', () => {
  render(<UploadModal open sessionId="s1" existingFiles={[]} onClose={() => {}} />, {
    wrapper: Wrapper,
  });

  expect(screen.getByText('No files yet.')).toBeInTheDocument();
});

test('header shows dynamic maxFiles from config', () => {
  vi.mocked(useAppConfig).mockReturnValue({ ...DEFAULT_LIMITS, maxFiles: 10 });

  const existingFiles: UploadedFileInfo[] = [
    {
      id: 'f1',
      name: 'sales.csv',
      alias: 'f1',
      sizeBytes: 1_000,
      type: 'csv',
      rowCount: null,
      expired: false,
    },
  ];

  render(<UploadModal open sessionId="s1" existingFiles={existingFiles} onClose={() => {}} />, {
    wrapper: Wrapper,
  });

  expect(screen.getByText('Attached · 1/10')).toBeInTheDocument();
});

test('degrades silently when the sample dataset list fails to load — upload UI stays intact', async () => {
  // The picker is decorative next to the core upload flow; its own local error
  // boundary must swallow this and never surface the app-wide "Failed to load" panel.
  const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => undefined);
  vi.mocked(fetchSampleDatasets).mockRejectedValue(new Error('samples endpoint down'));

  render(<UploadModal open sessionId="s1" existingFiles={[]} onClose={() => {}} />, {
    wrapper: Wrapper,
  });

  await waitFor(() => {
    expect(screen.getByText('No files yet.')).toBeInTheDocument();
  });
  // Core upload affordances render normally, unaffected by the picker's failure.
  expect(screen.getByText('Drag & drop files here, or browse')).toBeInTheDocument();
  expect(document.querySelector('input[type="file"]')).toBeInTheDocument();
  // No global error fallback leaked through, and the picker itself is gone (silent degrade).
  expect(screen.queryByText('Failed to load')).not.toBeInTheDocument();
  expect(screen.queryByText('或使用示範資料集')).not.toBeInTheDocument();

  consoleErrorSpy.mockRestore();
});
