import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { App as AntdApp } from 'antd';
import '@fontsource-variable/inter/wght.css';
import '@fontsource/noto-sans-tc/400.css';
import '@fontsource/noto-sans-tc/500.css';
import '@fontsource/noto-sans-tc/700.css';
import './index.css';
import App from './App';
import { initInternalRuntime } from './bootstrap/internal';

const queryClient = new QueryClient();

function mountApp(): void {
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <AntdApp>
          <App />
        </AntdApp>
      </QueryClientProvider>
    </StrictMode>,
  );
}

// internal 環境的初始化 MUST 在 mount 前完成（SSO 決定 X-User-Id）；預設環境是 no-op 立即 resolve。
// 刻意不 catch：初始化失敗時讓 rejection 浮上 console 且不 mount，NEVER 以匿名身分繼續。
void initInternalRuntime().then(mountApp);
