import React, { useCallback, useState } from 'react';
import ResultTable from './ResultTable';
import type { TableResult } from '@/types';

export interface Props {
  table: TableResult;
}

/** Collapsed-by-default row for one intermediate TABLE event (per-tool result tables would
 *  otherwise clutter the streaming narrative); click expands to the full ResultTable. */
const CollapsibleResultTable: React.FC<Props> = ({ table }) => {
  const [expanded, setExpanded] = useState(false);

  const toggleExpanded = useCallback(() => {
    setExpanded((previousExpanded) => !previousExpanded);
  }, []);

  return (
    <div className="mt-2" data-testid="collapsible-result-table">
      <button
        onClick={toggleExpanded}
        className="flex w-full cursor-pointer items-center gap-1 rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-left text-xs text-gray-500 hover:bg-gray-50"
      >
        <span aria-hidden="true">{expanded ? '▾' : '▸'}</span>
        <span>
          {table.intent}({table.rows.length} 列)
        </span>
      </button>
      {expanded && (
        <ResultTable
          intent={table.intent}
          columns={table.columns}
          rows={table.rows}
          truncated={table.truncated}
        />
      )}
    </div>
  );
};

export default React.memo(CollapsibleResultTable);
