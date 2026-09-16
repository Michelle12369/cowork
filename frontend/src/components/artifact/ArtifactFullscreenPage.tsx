import React, { useRef } from 'react';
import ErrorBoundary from '@/components/common/ErrorBoundary';
import SuspenseLoader from '@/components/common/SuspenseLoader';
import { useMcpBridge } from '@/hooks/useMcpBridge';
import ArtifactFrame from './ArtifactFrame';

interface ArtifactFullscreenPageProps {
  artifactId: string;
}

/** 全螢幕殼頁：app 自身的頁面（可帶 auth header），內部仍以 sandbox srcdoc 關住 artifact。
 *  取代直接 window.open /api HTML——導覽請求帶不了 auth header，blob 又會同源逃逸。
 *  只接 mcp() 呼叫, 不接修復卡. */
const ArtifactFullscreenPage: React.FC<ArtifactFullscreenPageProps> = ({ artifactId }) => {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  useMcpBridge(iframeRef, artifactId);

  return (
    <div className="relative h-screen w-screen">
      <ErrorBoundary>
        <SuspenseLoader>
          <ArtifactFrame
            artifactId={artifactId}
            reloadNonce={0}
            title="Dashboard"
            iframeRef={iframeRef}
          />
        </SuspenseLoader>
      </ErrorBoundary>
    </div>
  );
};

export default ArtifactFullscreenPage;
