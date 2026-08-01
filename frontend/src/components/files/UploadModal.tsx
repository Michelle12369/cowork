import React, { Suspense, useCallback, useRef, useState } from 'react';
import { App, Button, Modal, Progress, Upload } from 'antd';
import { CloseOutlined, CloudUploadOutlined, WarningOutlined } from '@ant-design/icons';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { isAxiosError } from 'axios';
import { deleteFile, uploadFiles } from '@/api/fileApi';
import { validateFiles } from '@/utils/uploadValidation';
import { getFileIcon } from '@/utils/fileIcon';
import { fmtSize } from '@/utils/format';
import { useAppConfig } from '@/hooks/useAppConfig';
import ErrorBoundary from '@/components/common/ErrorBoundary';
import SampleDatasetPicker from './SampleDatasetPicker';
import type { UploadedFileInfo } from '@/types';

export interface UploadModalProps {
  open: boolean;
  sessionId: string;
  existingFiles: UploadedFileInfo[];
  onClose(): void;
}

const UploadModal: React.FC<UploadModalProps> = ({ open, sessionId, existingFiles, onClose }) => {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const [warning, setWarning] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [percent, setPercent] = useState(0);

  const limits = useAppConfig();
  const activeFiles = existingFiles.filter((file) => !file.expired);
  const supportedExtensions = Object.keys(limits.singleFileLimits)
    .map((ext) => `.${ext}`)
    .join(' ');
  const acceptAttr = Object.keys(limits.singleFileLimits)
    .map((ext) => `.${ext}`)
    .join(',');

  const deleteMutation = useMutation({
    mutationFn: (fileId: string) => deleteFile(sessionId, fileId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['session', sessionId] });
    },
    onError: () => {
      message.error('Failed to remove file');
    },
  });

  // antd calls beforeUpload once per file in a multi-select/drop batch;
  // collect the whole batch before validating and uploading.
  const batchRef = useRef<File[]>([]);

  const doUpload = useCallback(
    async (files: File[]): Promise<void> => {
      setUploading(true);
      setPercent(0);
      try {
        await uploadFiles(sessionId, files, setPercent);
        await Promise.all([
          queryClient.invalidateQueries({ queryKey: ['session', sessionId] }),
          // Invalidate the sidebar list so the new session appears after the first upload
          // materializes it server-side (the session did not exist until this POST).
          queryClient.invalidateQueries({ queryKey: ['sessions'] }),
        ]);
        message.success('Files uploaded successfully');
        setWarning(null);
      } catch (err) {
        const backendMessage =
          isAxiosError(err) && typeof err.response?.data?.message === 'string'
            ? (err.response.data.message as string)
            : null;
        setWarning(backendMessage ?? 'Upload failed. Please try again.');
      } finally {
        setUploading(false);
        setPercent(0);
      }
    },
    [sessionId, queryClient, message],
  );

  const handleBeforeUpload = useCallback(
    (file: File, fileList: File[]): false => {
      batchRef.current.push(file);
      // Act only once the entire batch has been collected
      if (batchRef.current.length === fileList.length) {
        const batch = [...batchRef.current];
        batchRef.current = [];
        const error = validateFiles(existingFiles, batch, limits);
        if (error) {
          setWarning(error);
        } else {
          setWarning(null);
          void doUpload(batch);
        }
      }
      return false; // never auto-upload
    },
    [existingFiles, limits, doUpload],
  );

  const handleClose = useCallback((): void => {
    if (uploading) return;
    setWarning(null);
    setPercent(0);
    batchRef.current = [];
    onClose();
  }, [uploading, onClose]);

  return (
    <Modal
      title="Attach files"
      open={open}
      onCancel={handleClose}
      footer={
        <Button type="primary" onClick={handleClose} disabled={uploading}>
          Done
        </Button>
      }
      width={520}
    >
      <Upload.Dragger
        multiple
        accept={acceptAttr}
        beforeUpload={handleBeforeUpload}
        fileList={[]}
        showUploadList={false}
        disabled={uploading}
      >
        <p className="ant-upload-drag-icon">
          <CloudUploadOutlined />
        </p>
        <p className="ant-upload-text">Drag &amp; drop files here, or browse</p>
        <p className="ant-upload-hint">
          Up to {limits.maxFiles} files · {fmtSize(limits.maxSessionBytes)} total per chat ·{' '}
          {supportedExtensions} and more
        </p>
      </Upload.Dragger>

      {/* Decorative/optional: a fetch failure here must never take down the whole
          upload modal, so it gets its own local, silent-degrade error boundary. */}
      <ErrorBoundary fallback={null}>
        <Suspense fallback={null}>
          <SampleDatasetPicker sessionId={sessionId} disabled={uploading} />
        </Suspense>
      </ErrorBoundary>

      {warning && (
        <div className="mt-3 flex items-center gap-2 rounded-lg border border-yellow-300 bg-yellow-50 px-3 py-2 text-xs text-[#874d00]">
          <WarningOutlined style={{ color: '#faad14', flexShrink: 0 }} />
          <span>{warning}</span>
        </div>
      )}

      {uploading && (
        <div className="mt-3">
          <Progress percent={percent} status="active" />
        </div>
      )}

      {/* Attached section */}
      <div className="mt-4">
        {/* Header row */}
        <div className="mb-2 flex items-center justify-between">
          <span className="text-[12px] font-semibold">
            Attached · {activeFiles.length}/{limits.maxFiles}
          </span>
          <span className="text-[11px] text-gray-400">
            {fmtSize(activeFiles.reduce((sum, file) => sum + file.sizeBytes, 0))} /{' '}
            {fmtSize(limits.maxSessionBytes)}
          </span>
        </div>

        {/* File list */}
        <div className="flex max-h-[200px] flex-col gap-[7px] overflow-y-auto">
          {activeFiles.length === 0 ? (
            <div className="py-[14px] text-center text-[12px] text-gray-400">No files yet.</div>
          ) : (
            activeFiles.map((file) => (
              <div
                key={file.id}
                className="flex items-center gap-[10px] rounded-[9px] border border-gray-200 bg-gray-50 px-[11px] py-[9px]"
              >
                {/* Icon */}
                <span className="flex-none">{getFileIcon(file.type, 18)}</span>

                {/* Name + meta */}
                <div className="min-w-0 flex-1">
                  <div className="overflow-hidden text-ellipsis whitespace-nowrap text-[13px] font-medium">
                    {file.name}
                  </div>
                  <div className="text-[11px] text-gray-400">
                    {fmtSize(file.sizeBytes)}
                    {file.rowCount != null ? ` · ${file.rowCount} rows` : ''}
                  </div>
                </div>

                {/* Remove */}
                <button
                  type="button"
                  aria-label={`Remove ${file.name}`}
                  onClick={() => deleteMutation.mutate(file.id)}
                  className="flex h-[22px] w-[22px] flex-none cursor-pointer items-center justify-center rounded-[6px] border-0 bg-transparent text-[12px] text-gray-400 hover:text-gray-600"
                >
                  <CloseOutlined />
                </button>
              </div>
            ))
          )}
        </div>
      </div>
    </Modal>
  );
};

export default UploadModal;
