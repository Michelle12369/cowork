import React, { useCallback, useMemo } from 'react';
import { Select, Tooltip } from 'antd';
import { useSuspenseQuery } from '@tanstack/react-query';
import { fetchConnectors } from '@/api/connectorsApi';

export interface ConnectorPickerProps {
  /** Currently selected connector ids (pre-lock, local to the draft/session). */
  selectedIds: string[];
  onChange(ids: string[]): void;
  /** True when the session has active (non-expired) files — files and connectors are
   *  mutually exclusive, so the picker is disabled while any file is attached. */
  hasActiveFiles?: boolean;
  /** The session's locked-in connector ids. Non-empty means the session already decided on
   *  connector mode; the picker becomes a read-only label. */
  lockedConnectorIds?: string[];
}

const ConnectorPicker: React.FC<ConnectorPickerProps> = ({
  selectedIds,
  onChange,
  hasActiveFiles = false,
  lockedConnectorIds,
}) => {
  const { data: connectors } = useSuspenseQuery({
    queryKey: ['connectors'],
    queryFn: fetchConnectors,
    // The catalog rarely changes within a session; avoid refetch churn on every mount.
    staleTime: Infinity,
  });

  const options = useMemo(
    () => connectors.map((connector) => ({ label: connector.name, value: connector.id })),
    [connectors],
  );

  const handleChange = useCallback(
    (values: string[]): void => {
      onChange(values);
    },
    [onChange],
  );

  // Graceful-empty: the connector feature is entirely hidden when the catalog is empty.
  if (connectors.length === 0) return null;

  const isLocked = (lockedConnectorIds?.length ?? 0) > 0;

  if (isLocked) {
    return (
      <span className="text-xs text-gray-400" role="status">
        資料源已鎖定——換資料源請開新對話
      </span>
    );
  }

  return (
    <Tooltip title={hasActiveFiles ? '已附加檔案，請先移除檔案才能選擇資料源連接器' : undefined}>
      <Select
        mode="multiple"
        value={selectedIds}
        onChange={handleChange}
        options={options}
        disabled={hasActiveFiles}
        placeholder="選擇資料源連接器"
        style={{ minWidth: 200 }}
        allowClear
        aria-label="資料源連接器"
      />
    </Tooltip>
  );
};

export default ConnectorPicker;
