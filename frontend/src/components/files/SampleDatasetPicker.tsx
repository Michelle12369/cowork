import React, { useCallback, useState } from 'react';
import { App, Button } from 'antd';
import { DatabaseOutlined } from '@ant-design/icons';
import { useMutation, useQueryClient, useSuspenseQuery } from '@tanstack/react-query';
import { isAxiosError } from 'axios';
import { fetchSampleDatasets, loadSampleDataset } from '@/api/samplesApi';
import type { SampleDataset } from '@/api/samplesApi';

export interface SampleDatasetPickerProps {
  sessionId: string;
  /** Disables all load buttons, e.g. while a regular upload is in progress. */
  disabled?: boolean;
}

interface SampleDatasetItemProps {
  sample: SampleDataset;
  loading: boolean;
  disabled: boolean;
  onLoad(sampleName: string): void;
}

const SampleDatasetItem: React.FC<SampleDatasetItemProps> = ({
  sample,
  loading,
  disabled,
  onLoad,
}) => {
  const handleClick = useCallback(() => onLoad(sample.name), [sample.name, onLoad]);

  return (
    <div className="flex items-center gap-[10px] rounded-[9px] border border-gray-200 bg-gray-50 px-[11px] py-[9px]">
      <span className="flex-none text-gray-400">
        <DatabaseOutlined style={{ fontSize: 18 }} />
      </span>
      <div className="min-w-0 flex-1">
        <div className="overflow-hidden text-ellipsis whitespace-nowrap text-[13px] font-medium">
          {sample.title}
        </div>
        <div className="overflow-hidden text-ellipsis whitespace-nowrap text-[11px] text-gray-400">
          {sample.description}
        </div>
      </div>
      <Button
        size="small"
        aria-label="載入"
        onClick={handleClick}
        loading={loading}
        disabled={disabled}
      >
        載入
      </Button>
    </div>
  );
};

const SampleDatasetPicker: React.FC<SampleDatasetPickerProps> = ({
  sessionId,
  disabled = false,
}) => {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const [loadingSample, setLoadingSample] = useState<string | null>(null);

  const { data: samples } = useSuspenseQuery({
    queryKey: ['sampleDatasets'],
    queryFn: fetchSampleDatasets,
    staleTime: Infinity,
  });

  const loadMutation = useMutation({
    mutationFn: (sampleName: string) => loadSampleDataset(sessionId, sampleName),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['session', sessionId] }),
        // Invalidate the sidebar list so the new session appears after the first load
        // materializes it server-side, mirroring the regular upload flow.
        queryClient.invalidateQueries({ queryKey: ['sessions'] }),
      ]);
      message.success('示範資料集已載入');
    },
    onError: (err) => {
      const backendMessage =
        isAxiosError(err) && typeof err.response?.data?.message === 'string'
          ? (err.response.data.message as string)
          : null;
      message.error(backendMessage ?? '示範資料集載入失敗，請再試一次。');
    },
    onSettled: () => {
      setLoadingSample(null);
    },
  });

  const handleLoad = useCallback(
    (sampleName: string): void => {
      if (loadMutation.isPending) return;
      setLoadingSample(sampleName);
      loadMutation.mutate(sampleName);
    },
    [loadMutation],
  );

  if (samples.length === 0) return null;

  return (
    <div className="mt-3">
      <div className="mb-2 text-[12px] font-semibold">或使用示範資料集</div>
      <div className="flex flex-col gap-[7px]">
        {samples.map((sample) => (
          <SampleDatasetItem
            key={sample.name}
            sample={sample}
            loading={loadingSample === sample.name}
            disabled={disabled || loadMutation.isPending}
            onLoad={handleLoad}
          />
        ))}
      </div>
    </div>
  );
};

export default SampleDatasetPicker;
