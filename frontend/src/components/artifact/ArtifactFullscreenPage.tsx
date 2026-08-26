import React from 'react';
import ErrorBoundary from '@/components/common/ErrorBoundary';
import SuspenseLoader from '@/components/common/SuspenseLoader';
import ArtifactFrame from './ArtifactFrame';

interface ArtifactFullscreenPageProps {
  artifactId: string;
}

/** 全螢幕殼頁：app 自身的頁面（可帶 auth header），內部仍以 sandbox srcdoc 關住 artifact。
 *  取代直接 window.open /api HTML——導覽請求帶不了 auth header，blob 又會同源逃逸。 */
const ArtifactFullscreenPage: React.FC<ArtifactFullscreenPageProps> = ({ artifactId }) => (
  <div className="relative h-screen w-screen">
    <ErrorBoundary>
      <SuspenseLoader>
        <ArtifactFrame artifactId={artifactId} reloadNonce={0} title="Dashboard" />
      </SuspenseLoader>
    </ErrorBoundary>
  </div>
);

export default ArtifactFullscreenPage;
