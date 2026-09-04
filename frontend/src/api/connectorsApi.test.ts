import { describe, expect, it, vi } from 'vitest';
import { apiClient } from './apiClient';
import { fetchConnectors } from './connectorsApi';

describe('fetchConnectors', () => {
  it('fetchConnectors_default_getsConnectorsEndpointViaApiClient', async () => {
    const connectors = [
      { id: 'salesforce', name: 'Salesforce CRM' },
      { id: 'jira', name: 'Jira' },
    ];
    const getSpy = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: connectors });

    const result = await fetchConnectors();

    expect(getSpy).toHaveBeenCalledWith('/connectors');
    expect(result).toEqual(connectors);
    getSpy.mockRestore();
  });

  it('fetchConnectors_emptyCatalog_returnsEmptyArray', async () => {
    const getSpy = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: [] });

    const result = await fetchConnectors();

    expect(result).toEqual([]);
    getSpy.mockRestore();
  });
});
