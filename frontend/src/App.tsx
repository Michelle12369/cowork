import React from 'react';
import { ConfigProvider } from 'antd';
import { FONT_FAMILY } from '@/theme/fonts';
import CoworkPage from './CoworkPage';
import ArtifactFullscreenPage from './components/artifact/ArtifactFullscreenPage';
import ErrorBoundary from './components/common/ErrorBoundary';
import SuspenseLoader from './components/common/SuspenseLoader';

// 單頁 app 無 router；全螢幕殼頁以 query param 分流（載入時讀一次即可，殼頁無 in-app 導覽）。
const fullscreenArtifactId = new URLSearchParams(window.location.search).get('artifactView');

const App: React.FC = () => (
  <ConfigProvider theme={{ token: { fontFamily: FONT_FAMILY } }}>
    <ErrorBoundary>
      <SuspenseLoader>
        {fullscreenArtifactId ? (
          <ArtifactFullscreenPage artifactId={fullscreenArtifactId} />
        ) : (
          <CoworkPage />
        )}
      </SuspenseLoader>
    </ErrorBoundary>
  </ConfigProvider>
);

export default App;
