import React from 'react';
import { ConfigProvider } from 'antd';
import { FONT_FAMILY } from '@/theme/fonts';
import CoworkPage from './CoworkPage';
import ErrorBoundary from './components/common/ErrorBoundary';
import SuspenseLoader from './components/common/SuspenseLoader';

const App: React.FC = () => (
  <ConfigProvider theme={{ token: { fontFamily: FONT_FAMILY } }}>
    <ErrorBoundary>
      <SuspenseLoader>
        <CoworkPage />
      </SuspenseLoader>
    </ErrorBoundary>
  </ConfigProvider>
);

export default App;
