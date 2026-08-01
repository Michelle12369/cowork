import React, { useMemo } from 'react';
import { ThoughtChain } from '@ant-design/x';
import type { ThoughtChainItemType } from '@ant-design/x';
import CollapsibleResultTable from './CollapsibleResultTable';
import { groupTablesByStepKey } from '@/utils/stepTableCorrelation';
import type { StepItem, StepStatus, TableResult } from '@/types';

interface Props {
  steps: StepItem[];
  /** Intermediate TABLE results rendered collapsed under the producing step; excludes any
   *  table the answer references inline via a `[[table:id]]` marker (see MessageBubble). */
  tables?: TableResult[];
}

function mapStatus(status: StepStatus): ThoughtChainItemType['status'] {
  switch (status) {
    case 'RUNNING':
      return 'loading';
    case 'SUCCESS':
      return 'success';
    case 'ERROR':
      return 'error';
    case 'PENDING':
    default:
      return undefined;
  }
}

const StepChain: React.FC<Props> = ({ steps, tables }) => {
  const tableByStepKey = useMemo(() => groupTablesByStepKey(steps, tables), [steps, tables]);

  const items: ThoughtChainItemType[] = steps.map((step) => {
    const matchedTable = tableByStepKey.get(step.stepKey);
    return {
      key: step.stepKey,
      title: step.title,
      description: step.description ?? undefined,
      status: mapStatus(step.status),
      content: matchedTable ? <CollapsibleResultTable table={matchedTable} /> : undefined,
    };
  });

  return <ThoughtChain items={items} />;
};

export default StepChain;
