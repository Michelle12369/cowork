import { afterEach, describe, expect, it, vi } from 'vitest';
import { apiClient, setAuthHeaderProvider } from './apiClient';
import { callArtifactMcp, fetchArtifactHtml, fetchArtifactRawHtml } from './artifactApi';

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

describe('fetchArtifactHtml', () => {
  it('fetchArtifactHtml_default_requestsArtifactHtmlAsTextViaApiClient', async () => {
    const getSpy = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: '<html>dashboard</html>' });

    const html = await fetchArtifactHtml('artifact-1', 0);

    expect(html).toBe('<html>dashboard</html>');
    expect(getSpy).toHaveBeenCalledWith('/artifacts/artifact-1', {
      responseType: 'text',
      params: undefined,
    });
    getSpy.mockRestore();
  });

  it('fetchArtifactHtml_positiveNonce_appendsCacheBuster', async () => {
    const getSpy = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: 'x' });

    await fetchArtifactHtml('artifact-1', 3);

    expect(getSpy).toHaveBeenCalledWith('/artifacts/artifact-1', {
      responseType: 'text',
      params: { r: 3 },
    });
    getSpy.mockRestore();
  });
});

describe('callArtifactMcp', () => {
  it('posts connector, tool and args unchanged and returns the body as-is', async () => {
    const body = { data: { result: [{ qty: 1.1 }] } };
    const postSpy = vi.spyOn(apiClient, 'post').mockResolvedValue({ data: body });

    const result = await callArtifactMcp('art-1', {
      connector: 'sales',
      tool: 'list_orders',
      args: { days: 30, region: 'TW' },
    });

    expect(postSpy).toHaveBeenCalledWith('/artifacts/art-1/mcp-call', {
      connector: 'sales',
      tool: 'list_orders',
      args: { days: 30, region: 'TW' },
    });
    expect(result).toBe(body);
    postSpy.mockRestore();
  });

  it('encodes the artifact id in the path', async () => {
    const postSpy = vi.spyOn(apiClient, 'post').mockResolvedValue({ data: { data: [] } });

    await callArtifactMcp('a b', { connector: 'sales', tool: 'list_orders', args: {} });

    expect(postSpy).toHaveBeenCalledWith('/artifacts/a%20b/mcp-call', expect.anything());
    postSpy.mockRestore();
  });
});
