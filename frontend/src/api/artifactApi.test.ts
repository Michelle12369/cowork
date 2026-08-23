import type { InternalAxiosRequestConfig } from 'axios';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { apiClient, setAuthHeaderProvider } from './apiClient';
import { fetchArtifactRawHtml, refreshArtifactData } from './artifactApi';

/** Mocks the axios adapter to resolve/reject as a real request would for the given status —
 *  exercises the same interceptor + settle() path as production, per apiClient.test.ts. */
function mockArtifactRefreshResponse(status: number, data: unknown): void {
  apiClient.defaults.adapter = (config: InternalAxiosRequestConfig) => {
    if (status >= 200 && status < 300) {
      return Promise.resolve({ data, status, statusText: 'OK', headers: {}, config });
    }
    return Promise.reject(
      Object.assign(new Error('Request failed'), {
        isAxiosError: true,
        response: { data, status, statusText: '', headers: {}, config },
        config,
        toJSON: (): Record<string, never> => ({}),
      }),
    );
  };
}

describe('fetchArtifactRawHtml', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    setAuthHeaderProvider(() => ({ 'X-User-Id': 'restored' }));
  });

  it('fetchArtifactRawHtml_default_carriesAuthHeaders', async () => {
    const mockFetch = vi.fn().mockResolvedValue({ ok: true, text: async () => '<html></html>' });
    vi.stubGlobal('fetch', mockFetch);
    setAuthHeaderProvider(() => ({ 'Internal-Header-One': 'token-1' }));

    await fetchArtifactRawHtml('artifact-1');

    const requestInit = mockFetch.mock.calls[0][1] as { headers: Record<string, string> };
    expect(requestInit.headers['Internal-Header-One']).toBe('token-1');
  });

  it('fetchArtifactRawHtml_twoCalls_providerCalledOncePerRequestWithFreshValue', async () => {
    const mockFetch = vi.fn().mockResolvedValue({ ok: true, text: async () => '<html></html>' });
    vi.stubGlobal('fetch', mockFetch);
    const provider = vi
      .fn<() => Record<string, string>>()
      .mockReturnValueOnce({ 'Internal-Header-One': 'token-1' })
      .mockReturnValueOnce({ 'Internal-Header-One': 'token-2' });
    setAuthHeaderProvider(provider);

    await fetchArtifactRawHtml('artifact-1');
    await fetchArtifactRawHtml('artifact-2');

    expect(provider).toHaveBeenCalledTimes(2);
    const secondInit = mockFetch.mock.calls[1][1] as { headers: Record<string, string> };
    expect(secondInit.headers['Internal-Header-One']).toBe('token-2');
  });
});

describe('refreshArtifactData', () => {
  it('refreshArtifactData_200_returnsSuccessWithHtml', async () => {
    mockArtifactRefreshResponse(200, { html: '<html><body>fresh</body></html>' });

    const result = await refreshArtifactData('artifact-1');

    expect(result).toEqual({ status: 'success', html: '<html><body>fresh</body></html>' });
  });

  it('refreshArtifactData_409_returnsNotRefreshableWithBackendMessage', async () => {
    mockArtifactRefreshResponse(409, {
      code: 'CONFLICT',
      message: '此版本不支援重新整理（無 API 資料源配方）',
    });

    const result = await refreshArtifactData('artifact-1');

    expect(result).toEqual({
      status: 'not_refreshable',
      message: '此版本不支援重新整理（無 API 資料源配方）',
    });
  });

  it('refreshArtifactData_502_returnsSourceErrorWithCodeAndBackendMessage', async () => {
    mockArtifactRefreshResponse(502, {
      code: 'SOURCE_SCHEMA_CHANGED',
      message: '來源資料結構已變更，無法更新',
    });

    const result = await refreshArtifactData('artifact-1');

    expect(result).toEqual({
      status: 'source_error',
      code: 'SOURCE_SCHEMA_CHANGED',
      message: '來源資料結構已變更，無法更新',
    });
  });

  it('refreshArtifactData_unexpectedStatus_returnsUnknownErrorWithGenericMessage', async () => {
    mockArtifactRefreshResponse(500, { code: 'INTERNAL', message: 'boom' });

    const result = await refreshArtifactData('artifact-1');

    expect(result).toEqual({ status: 'unknown_error', message: '更新資料失敗，請稍後再試' });
  });
});
