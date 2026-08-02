import React, { useCallback } from 'react';
import { CloseOutlined } from '@ant-design/icons';
import { getFileIcon } from '@/utils/fileIcon';
import type { UploadedFileInfo } from '@/types';

interface FileChipsProps {
  files: UploadedFileInfo[];
  onRemove(id: string): void;
}

interface FileChipProps {
  file: UploadedFileInfo;
  onRemove(id: string): void;
}

const FileChip: React.FC<FileChipProps> = ({ file, onRemove }) => {
  const handleRemove = useCallback(() => onRemove(file.id), [file.id, onRemove]);

  const containerClass = file.expired
    ? 'inline-flex max-w-[230px] items-center gap-1.5 rounded-lg border border-gray-300 bg-gray-100 px-2 py-1 text-[11.5px] opacity-70'
    : 'inline-flex max-w-[190px] items-center gap-1.5 rounded-lg border border-gray-200 bg-gray-50 px-2 py-1 text-[11.5px]';

  return (
    <div className={containerClass}>
      {getFileIcon(file.name, 14)}
      <span className={`truncate ${file.expired ? 'text-gray-500' : 'text-gray-800'}`}>
        {file.name}
      </span>
      {file.expired && (
        <span className="flex-none rounded bg-gray-300 px-1 py-0.5 text-[10px] leading-none text-gray-600">
          已過期
        </span>
      )}
      <button
        onClick={handleRemove}
        aria-label={`Remove ${file.name}`}
        className="flex h-4 w-4 flex-none items-center justify-center rounded text-gray-400 hover:text-gray-600"
      >
        <CloseOutlined style={{ fontSize: 10 }} />
      </button>
    </div>
  );
};

const FileChips: React.FC<FileChipsProps> = ({ files, onRemove }) => {
  if (files.length === 0) return null;

  return (
    <div className="mb-2 flex flex-wrap gap-1.5">
      {files.map((file) => (
        <FileChip key={file.id} file={file} onRemove={onRemove} />
      ))}
    </div>
  );
};

export default FileChips;
