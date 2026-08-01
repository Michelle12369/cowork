import React, { Suspense } from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { App } from 'antd';
import { vi } from 'vitest';
import { AxiosError } from 'axios';
import { fetchSampleDatasets, loadSampleDataset } from '@/api/samplesApi';
import SampleDatasetPicker from './SampleDatasetPicker';
import type { SampleDataset } from '@/api/samplesApi';
import type { UploadedFileInfo } from '@/types';

vi.mock('@/api/samplesApi');

const SAMPLE: SampleDataset = {
  name: 'product-usage-feedback',
  title: '產品使用行為與回饋',
  description: '使用行為紀錄與使用者回饋，適合分析功能採用度與滿意度關聯',
  fileAliases: ['usage_log', 'feedback'],
};

const LOADED_FILES: UploadedFileInfo[] = [
  {
    id: 'f1',
    name: 'usage_log.csv',
    alias: 'usage_log',
    sizeBytes: 100,
    type: 'csv',
    rowCount: 2000,
    expired: false,
  },
  {
    id: 'f2',
    name: 'feedback.csv',
    alias: 'feedback',
    sizeBytes: 100,
    type: 'csv',
    rowCount: 300,
    expired: false,
  },
];

function Wrapper({ children }: { children: React.ReactNode }): React.ReactElement {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={queryClient}>
      <App>
        <Suspense fallback={<div>loading</div>}>{children}</Suspense>
      </App>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(fetchSampleDatasets).mockResolvedValue([SAMPLE]);
});

test('lists available sample datasets with title and description', async () => {
  render(<SampleDatasetPicker sessionId="s1" />, { wrapper: Wrapper });

  expect(await screen.findByText('產品使用行為與回饋')).toBeInTheDocument();
  expect(
    screen.getByText('使用行為紀錄與使用者回饋，適合分析功能採用度與滿意度關聯'),
  ).toBeInTheDocument();
  expect(screen.getByText('或使用示範資料集')).toBeInTheDocument();
});

test('renders nothing when there are no sample datasets', async () => {
  vi.mocked(fetchSampleDatasets).mockResolvedValue([]);
  const { container } = render(<SampleDatasetPicker sessionId="s1" />, { wrapper: Wrapper });

  await waitFor(() => {
    expect(screen.queryByText('loading')).not.toBeInTheDocument();
  });
  expect(container.querySelector('.mt-3')).not.toBeInTheDocument();
});

test('clicking 載入 posts the load request and shows a success message', async () => {
  vi.mocked(loadSampleDataset).mockResolvedValue(LOADED_FILES);

  render(<SampleDatasetPicker sessionId="s1" />, { wrapper: Wrapper });

  const loadButton = await screen.findByRole('button', { name: '載入' });
  fireEvent.click(loadButton);

  await waitFor(() => {
    expect(loadSampleDataset).toHaveBeenCalledWith('s1', 'product-usage-feedback');
  });
  await waitFor(() => {
    expect(screen.getByText('示範資料集已載入')).toBeInTheDocument();
  });
});

test('invalidates the session and sidebar queries after a successful load', async () => {
  vi.mocked(loadSampleDataset).mockResolvedValue(LOADED_FILES);
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');

  render(
    <QueryClientProvider client={queryClient}>
      <App>
        <Suspense fallback={<div>loading</div>}>
          <SampleDatasetPicker sessionId="s1" />
        </Suspense>
      </App>
    </QueryClientProvider>,
  );

  const loadButton = await screen.findByRole('button', { name: '載入' });
  fireEvent.click(loadButton);

  await waitFor(() => {
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['session', 's1'] });
  });
  expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['sessions'] });
});

test('shows the backend error message when loading fails, e.g. upload limit exceeded', async () => {
  const axiosError = new AxiosError('Request failed');
  axiosError.response = {
    status: 400,
    statusText: 'Bad Request',
    headers: {},
    // @ts-expect-error -- minimal config stub for the mocked rejection
    config: {},
    data: { message: 'You can attach up to 5 files per chat.' },
  };
  vi.mocked(loadSampleDataset).mockRejectedValue(axiosError);

  render(<SampleDatasetPicker sessionId="s1" />, { wrapper: Wrapper });

  const loadButton = await screen.findByRole('button', { name: '載入' });
  fireEvent.click(loadButton);

  await waitFor(() => {
    expect(screen.getByText('You can attach up to 5 files per chat.')).toBeInTheDocument();
  });
});

test('shows a generic error message when the backend gives no message', async () => {
  vi.mocked(loadSampleDataset).mockRejectedValue(new Error('network down'));

  render(<SampleDatasetPicker sessionId="s1" />, { wrapper: Wrapper });

  const loadButton = await screen.findByRole('button', { name: '載入' });
  fireEvent.click(loadButton);

  await waitFor(() => {
    expect(screen.getByText('示範資料集載入失敗，請再試一次。')).toBeInTheDocument();
  });
});

test('disables the load button while a load is in flight to prevent double-click', async () => {
  let resolveLoad: (value: UploadedFileInfo[]) => void = () => {};
  vi.mocked(loadSampleDataset).mockReturnValue(
    new Promise((resolve) => {
      resolveLoad = resolve;
    }),
  );

  render(<SampleDatasetPicker sessionId="s1" />, { wrapper: Wrapper });

  const loadButton = await screen.findByRole('button', { name: '載入' });
  fireEvent.click(loadButton);

  await waitFor(() => {
    expect(loadButton).toBeDisabled();
  });
  // A second click while pending must not trigger a second request.
  fireEvent.click(loadButton);
  expect(loadSampleDataset).toHaveBeenCalledTimes(1);

  resolveLoad(LOADED_FILES);
  await waitFor(() => {
    expect(loadButton).not.toBeDisabled();
  });
});

test('disables the load button when the disabled prop is set, e.g. during a regular upload', async () => {
  render(<SampleDatasetPicker sessionId="s1" disabled />, { wrapper: Wrapper });

  const loadButton = await screen.findByRole('button', { name: '載入' });
  expect(loadButton).toBeDisabled();
});
