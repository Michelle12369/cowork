/** API functions for artifact delivery endpoints. */

import { apiClient, getAuthHeaders } from './apiClient';
import type { BrowserJsError } from '@/types';

/** Fetches the raw (pre-assembly) HTML for an artifact; throws on non-2xx (e.g. 404).
 *  走 raw fetch 而非 apiClient，故 MUST 自行帶 auth header——axios interceptor 不會經過這裡。 */
export async function fetchArtifactRawHtml(id: string, signal?: AbortSignal): Promise<string> {
  const res = await fetch(`/api/artifacts/${id}/raw`, { signal, headers: getAuthHeaders() });
  if (!res.ok) {
    throw new Error(`Failed to fetch raw HTML: ${res.status}`);
  }
  return res.text();
}

/** Calls the repair endpoint; returns false if the LLM produced no improvement. */
export async function repairArtifact(id: string, errors: BrowserJsError[]): Promise<boolean> {
  const response = await apiClient.post<{ repaired: boolean }>(`/artifacts/${id}/repair`, {
    errors,
  });
  return response.data.repaired;
}
