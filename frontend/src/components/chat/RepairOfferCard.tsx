import React, { useCallback } from 'react';
import { LoadingOutlined } from '@ant-design/icons';

interface RepairOfferCardProps {
  errorCount: number;
  firstErrorMessage: string;
  status: 'pending' | 'repairing' | 'failed';
  onConfirm: () => void;
  onDismiss: () => void;
}

const RepairOfferCard: React.FC<RepairOfferCardProps> = ({
  errorCount,
  firstErrorMessage,
  status,
  onConfirm,
  onDismiss,
}) => {
  const handleConfirm = useCallback((): void => {
    onConfirm();
  }, [onConfirm]);

  const handleDismiss = useCallback((): void => {
    onDismiss();
  }, [onDismiss]);

  const truncatedMessage =
    firstErrorMessage.length > 120 ? `${firstErrorMessage.slice(0, 120)}…` : firstErrorMessage;

  return (
    <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs">
      <div className="font-medium text-amber-800">⚠ 偵測到儀表板執行錯誤（{errorCount} 個）</div>
      {firstErrorMessage && (
        <div className="mt-1 break-all font-mono text-[11px] text-gray-500">{truncatedMessage}</div>
      )}

      {status === 'pending' && (
        <div className="mt-2 flex items-center gap-2">
          <button
            onClick={handleConfirm}
            className="rounded border border-blue-400 bg-blue-500 px-2 py-1 text-xs text-white transition-colors hover:bg-blue-600"
          >
            修復
          </button>
          <button onClick={handleDismiss} className="text-xs text-gray-500 hover:text-gray-700">
            忽略
          </button>
        </div>
      )}

      {status === 'repairing' && (
        <div className="mt-2 flex items-center gap-1.5 text-xs text-gray-500">
          <LoadingOutlined spin />
          <span>修復中，請稍候…</span>
        </div>
      )}

      {status === 'failed' && (
        <div className="mt-2 flex items-center gap-2">
          <span className="text-xs text-amber-700">修復未成功</span>
          <button
            onClick={handleConfirm}
            className="rounded border border-blue-400 bg-blue-500 px-2 py-1 text-xs text-white transition-colors hover:bg-blue-600"
          >
            再試一次
          </button>
          <button onClick={handleDismiss} className="text-xs text-gray-500 hover:text-gray-700">
            忽略
          </button>
        </div>
      )}
    </div>
  );
};

export default RepairOfferCard;
