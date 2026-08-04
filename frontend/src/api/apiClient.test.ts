import type { InternalAxiosRequestConfig } from 'axios';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { apiClient, getUserId, setUserId } from './apiClient';
import { streamAgentMessage } from './agentApi';

describe('setUserId', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('setUserId_thenGetUserId_returnsSameValue', () => {
    setUserId('sso-user-1');
    expect(getUserId()).toBe('sso-user-1');
  });

  it('setUserId_axiosRequest_carriesNewIdHeader', async () => {
    setUserId('sso-user-2');
    let captured: InternalAxiosRequestConfig | undefined;
    // 用 adapter 攔截：走完整的 interceptor 鏈，但不發出真實請求。
    apiClient.defaults.adapter = async (config) => {
      captured = config;
      return { data: {}, status: 200, statusText: 'OK', headers: {}, config };
    };

    await apiClient.get('/config');

    expect(captured?.headers['X-User-Id']).toBe('sso-user-2');
  });

  it('setUserId_agentStreamFetch_carriesNewIdHeader', async () => {
    setUserId('sso-user-3');
    // body: null 讓 streamAgentMessage 在建立 reader 前就結束，只驗證送出的 header。
    const mockFetch = vi.fn().mockResolvedValue({ ok: true, body: null });
    vi.stubGlobal('fetch', mockFetch);

    const stream = streamAgentMessage({
      sessionId: 'session-1',
      question: 'hello',
      signal: new AbortController().signal,
    });
    await stream.next();

    const requestInit = mockFetch.mock.calls[0][1] as { headers: Record<string, string> };
    expect(requestInit.headers['X-User-Id']).toBe('sso-user-3');
    vi.unstubAllGlobals();
  });
});
