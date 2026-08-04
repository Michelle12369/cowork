import type { InternalAxiosRequestConfig } from 'axios';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { apiClient, getUserId, setAuthHeaderProvider } from './apiClient';
import { streamAgentMessage } from './agentApi';

/** 用 adapter 攔截：走完整的 interceptor 鏈，但不發出真實請求。 */
function captureRequests(): InternalAxiosRequestConfig[] {
  const captured: InternalAxiosRequestConfig[] = [];
  apiClient.defaults.adapter = async (config) => {
    captured.push(config);
    return { data: {}, status: 200, statusText: 'OK', headers: {}, config };
  };
  return captured;
}

const defaultProvider = (): Record<string, string> => ({ 'X-User-Id': getUserId() });

describe('getUserId', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('getUserId_noStoredValue_generatesAndPersistsId', () => {
    const id = getUserId();
    expect(id).toBeTruthy();
    expect(getUserId()).toBe(id);
  });
});

describe('auth header provider', () => {
  beforeEach(() => {
    localStorage.clear();
    setAuthHeaderProvider(defaultProvider);
  });

  afterEach(() => {
    setAuthHeaderProvider(defaultProvider);
    vi.unstubAllGlobals();
  });

  it('defaultProvider_axiosRequest_carriesXUserIdHeader', async () => {
    const captured = captureRequests();

    await apiClient.get('/config');

    expect(captured[0]?.headers['X-User-Id']).toBe(getUserId());
  });

  it('setAuthHeaderProvider_customProvider_axiosRequestCarriesProviderHeaders', async () => {
    setAuthHeaderProvider(() => ({
      'Internal-Header-One': 'token-a',
      'Internal-Header-Two': 'token-b',
    }));
    const captured = captureRequests();

    await apiClient.get('/config');

    expect(captured[0]?.headers['Internal-Header-One']).toBe('token-a');
    expect(captured[0]?.headers['Internal-Header-Two']).toBe('token-b');
  });

  it('setAuthHeaderProvider_providerWithoutXUserId_axiosRequestOmitsXUserId', async () => {
    setAuthHeaderProvider(() => ({ 'Internal-Header-One': 'token-a' }));
    const captured = captureRequests();

    await apiClient.get('/config');

    expect(captured[0]?.headers['X-User-Id']).toBeUndefined();
  });

  it('setAuthHeaderProvider_twoAxiosRequests_providerCalledOncePerRequestWithFreshValue', async () => {
    const provider = vi
      .fn<() => Record<string, string>>()
      .mockReturnValueOnce({ 'Internal-Header-One': 'token-1' })
      .mockReturnValueOnce({ 'Internal-Header-One': 'token-2' });
    setAuthHeaderProvider(provider);
    const captured = captureRequests();

    await apiClient.get('/config');
    await apiClient.get('/config');

    expect(provider).toHaveBeenCalledTimes(2);
    expect(captured[0]?.headers['Internal-Header-One']).toBe('token-1');
    expect(captured[1]?.headers['Internal-Header-One']).toBe('token-2');
  });

  it('agentStreamFetch_customProvider_carriesProviderHeadersInsteadOfXUserId', async () => {
    setAuthHeaderProvider(() => ({ 'Internal-Header-One': 'token-a' }));
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
    expect(requestInit.headers['Internal-Header-One']).toBe('token-a');
    expect(requestInit.headers['X-User-Id']).toBeUndefined();
  });

  it('agentStreamFetch_twoRequests_providerCalledOncePerRequestWithFreshValue', async () => {
    const provider = vi
      .fn<() => Record<string, string>>()
      .mockReturnValueOnce({ 'Internal-Header-One': 'token-1' })
      .mockReturnValueOnce({ 'Internal-Header-One': 'token-2' });
    setAuthHeaderProvider(provider);
    const mockFetch = vi.fn().mockResolvedValue({ ok: true, body: null });
    vi.stubGlobal('fetch', mockFetch);

    await streamAgentMessage({
      sessionId: 'session-1',
      question: 'first',
      signal: new AbortController().signal,
    }).next();
    await streamAgentMessage({
      sessionId: 'session-1',
      question: 'second',
      signal: new AbortController().signal,
    }).next();

    expect(provider).toHaveBeenCalledTimes(2);
    const firstCall = mockFetch.mock.calls[0][1] as { headers: Record<string, string> };
    const secondCall = mockFetch.mock.calls[1][1] as { headers: Record<string, string> };
    expect(firstCall.headers['Internal-Header-One']).toBe('token-1');
    expect(secondCall.headers['Internal-Header-One']).toBe('token-2');
  });
});
