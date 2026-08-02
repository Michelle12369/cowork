import React from 'react';
import { ThoughtChain } from '@ant-design/x';
import type { ThoughtChainItemType } from '@ant-design/x';
import type { StepItem, StepStatus } from '@/types';

interface Props {
  steps: StepItem[];
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

const StepChain: React.FC<Props> = ({ steps }) => {
  const items: ThoughtChainItemType[] = steps.map((step) => ({
    key: step.stepKey,
    title: step.title,
    description: step.description ?? undefined,
    status: mapStatus(step.status),
  }));

  return <ThoughtChain items={items} />;
};

export default StepChain;
