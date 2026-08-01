import React, { useCallback, useState } from 'react';
import { Badge, Button, Popover } from 'antd';
import { PaperClipOutlined, CloseOutlined, PlusOutlined } from '@ant-design/icons';
import { fmtSize } from '@/utils/format';
import { getFileIcon } from '@/utils/fileIcon';
import type { UploadedFileInfo } from '@/types';

interface AttachmentsPopoverProps {
  files: UploadedFileInfo[];
  onRemove(id: string): void;
  onAttach(): void;
}

const AttachmentsPopover: React.FC<AttachmentsPopoverProps> = ({ files, onRemove, onAttach }) => {
  const [open, setOpen] = useState(false);

  const handleRemove = useCallback(
    (id: string) => {
      onRemove(id);
    },
    [onRemove],
  );

  const handleAttach = useCallback(() => {
    setOpen(false);
    onAttach();
  }, [onAttach]);

  const activeFiles = files.filter((file) => !file.expired);
  const totalSize = activeFiles.reduce((sum, file) => sum + file.sizeBytes, 0);

  const content = (
    <div style={{ width: 290 }}>
      <div className="flex items-center justify-between border-b border-gray-100 px-3 py-2.5">
        <span className="text-xs font-semibold">Attachments</span>
        <span className="text-[11px] text-gray-400">
          {activeFiles.length}/5 · {fmtSize(totalSize)}
        </span>
      </div>
      <div className="max-h-[220px] overflow-y-auto">
        {files.length === 0 && (
          <div className="py-4 text-center text-xs text-gray-400">No files attached yet.</div>
        )}
        {files.map((file) => (
          <div key={file.id} className="flex items-center gap-2 border-b border-gray-50 px-3 py-2">
            <span style={file.expired ? { opacity: 0.4 } : undefined}>
              {getFileIcon(file.type, 17)}
            </span>
            <div className="min-w-0 flex-1">
              <div
                className={`truncate text-[12.5px] font-medium ${file.expired ? 'text-gray-400' : ''}`}
              >
                {file.name}
              </div>
              <div className="text-[10.5px] text-gray-400">
                {file.expired
                  ? '已過期清除'
                  : `${fmtSize(file.sizeBytes)}${file.rowCount !== null ? ` · ${file.rowCount} rows` : ''}`}
              </div>
            </div>
            {!file.expired && (
              <button
                onClick={() => handleRemove(file.id)}
                aria-label={`Remove ${file.name}`}
                className="flex h-5 w-5 flex-none items-center justify-center rounded text-gray-300 hover:text-gray-500"
              >
                <CloseOutlined style={{ fontSize: 12 }} />
              </button>
            )}
          </div>
        ))}
      </div>
      <div className="border-t border-gray-100 px-3 py-2">
        <button
          onClick={handleAttach}
          className="flex h-8 w-full items-center justify-center gap-1.5 rounded-lg bg-blue-50 text-[12.5px] font-medium text-blue-500 hover:bg-blue-100"
        >
          <PlusOutlined />
          Attach files
        </button>
      </div>
    </div>
  );

  return (
    <Popover
      content={content}
      trigger="click"
      open={open}
      onOpenChange={setOpen}
      placement="bottomRight"
      styles={{ container: { padding: 0 } }}
    >
      <Badge count={activeFiles.length} size="small">
        <Button aria-label="Attachments" icon={<PaperClipOutlined />} title="Attachments" />
      </Badge>
    </Popover>
  );
};

export default AttachmentsPopover;
