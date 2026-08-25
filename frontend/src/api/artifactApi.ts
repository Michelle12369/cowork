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

/** Fetches the assembled artifact HTML through the authenticated apiClient channel
 *  (iframe src 導覽帶不了 auth header，srcdoc 呈現前先在這裡抓)。
 *  responseType 'text' 保證回傳一律是字串；nonce > 0 時附 cache-buster 對齊 repair reload 行為。 */
export async function fetchArtifactHtml(artifactId: string, reloadNonce: number): Promise<string> {
  const response = await apiClient.get<string>(`/artifacts/${encodeURIComponent(artifactId)}`, {
    responseType: 'text',
    params: reloadNonce > 0 ? { r: reloadNonce } : undefined,
  });
  return response.data;
}

/** Calls the repair endpoint; returns false if the LLM produced no improvement. */
export async function repairArtifact(id: string, errors: BrowserJsError[]): Promise<boolean> {
  const response = await apiClient.post<{ repaired: boolean }>(`/artifacts/${id}/repair`, {
    errors,
  });
  return response.data.repaired;
}
