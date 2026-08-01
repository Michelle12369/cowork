import React, { Suspense } from 'react';
import { Spin } from 'antd';

interface Props {
  children: React.ReactNode;
}

const SuspenseLoader: React.FC<Props> = ({ children }) => (
  <Suspense
    fallback={
      <div className="flex h-full items-center justify-center p-8">
        <Spin />
      </div>
    }
  >
    {children}
  </Suspense>
);

export default SuspenseLoader;
