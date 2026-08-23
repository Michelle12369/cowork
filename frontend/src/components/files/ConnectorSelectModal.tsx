import React, { useCallback, useMemo, useState } from 'react';
import { Button, Checkbox, Modal, Tag, Tooltip } from 'antd';
import { DatabaseOutlined } from '@ant-design/icons';
import { useSuspenseQuery } from '@tanstack/react-query';
import { fetchConnectors } from '@/api/connectorApi';

/** Tooltip shown on the trigger once the session's connector selection is locked (§11.6). */
const LOCKED_TOOLTIP = '資料源已鎖定——換資料源請開新對話';

export interface ConnectorSelectModalProps {
  /** Currently selected connector group names; empty means "all groups" (backend default). */
  selectedGroups: string[];
  onChange(groups: string[]): void;
  /** True once the session has captured its definitive selectedGroups (§11.6 session-lock —
   *  set once on the session's first message, immutable thereafter). Disables the trigger and
   *  surfaces why via tooltip; the already-locked selection still renders as tags. */
  locked?: boolean;
}

/** Upload-area "選擇資料源" button + multi-select modal (§11.4/§11.5 connector selection).
 *  Fetches the connector group catalog once (cached); the trigger button — and the whole
 *  feature — is hidden entirely when the catalog is empty (connector feature disabled
 *  server-side, i.e. no `AGENT_CONNECTORS_FILE` configured). Mount this behind a local
 *  `Suspense`/`ErrorBoundary` (fallback null) so a fetch failure never blocks the chat panel —
 *  same pattern as SampleDatasetPicker. */
const ConnectorSelectModal: React.FC<ConnectorSelectModalProps> = ({
  selectedGroups,
  onChange,
  locked = false,
}) => {
  const [open, setOpen] = useState(false);

  const { data: groups } = useSuspenseQuery({
    queryKey: ['connectors'],
    queryFn: fetchConnectors,
    staleTime: Infinity,
  });

  const displayByName = useMemo(
    () => new Map(groups.map((group) => [group.name, group.display])),
    [groups],
  );

  const handleOpen = useCallback((): void => setOpen(true), []);
  const handleClose = useCallback((): void => setOpen(false), []);

  const handleChange = useCallback(
    (checkedValues: string[]): void => {
      onChange(checkedValues);
    },
    [onChange],
  );

  // Feature off (no connector groups configured) — hide the affordance entirely.
  if (groups.length === 0) {
    return null;
  }

  const options = groups.map((group) => ({ label: group.display, value: group.name }));

  const trigger = (
    <Button icon={<DatabaseOutlined />} onClick={handleOpen} disabled={locked}>
      選擇資料源
      {selectedGroups.length > 0 && (
        <Tag className="ml-1.5" color="blue">
          {selectedGroups.map((name) => displayByName.get(name) ?? name).join('、')}
        </Tag>
      )}
    </Button>
  );

  return (
    <>
      {locked ? (
        <Tooltip title={LOCKED_TOOLTIP}>
          {/* antd Button honors the native `disabled` attribute, which swallows mouse events —
              the extra span keeps the Tooltip's hover trigger reachable. */}
          <span>{trigger}</span>
        </Tooltip>
      ) : (
        trigger
      )}

      <Modal
        title="選擇資料源"
        open={open}
        onCancel={handleClose}
        footer={
          <Button type="primary" onClick={handleClose}>
            完成
          </Button>
        }
        width={440}
      >
        <div className="mb-2 text-xs text-gray-400">建議選擇單一資料源；如需跨系統分析可複選。</div>
        <Checkbox.Group
          className="flex flex-col gap-2.5"
          options={options}
          value={selectedGroups}
          onChange={handleChange}
        />
        {selectedGroups.length > 1 && (
          <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-2.5 text-xs text-amber-800">
            跨系統分析可能需明確指定關聯欄位
          </div>
        )}
      </Modal>
    </>
  );
};

export default ConnectorSelectModal;
