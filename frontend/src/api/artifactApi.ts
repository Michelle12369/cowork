/** API functions for artifact delivery endpoints. */

import axios from 'axios';
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

/** Discriminated outcome of {@link refreshArtifactData} — lets the caller distinguish a
 *  successful replay from the two backend-defined rejection shapes without inspecting
 *  axios error internals. */
export type RefreshArtifactDataResult =
  | { status: 'success'; html: string }
  | { status: 'not_refreshable'; message: string }
  | { status: 'source_error'; code: string; message: string }
  | { status: 'unknown_error'; message: string };

/** Shape of the backend's uniform error body ({@link ErrorResponseDto}: {code, message}). */
interface BackendErrorBody {
  code?: string;
  message?: string;
}

const UNKNOWN_ERROR_MESSAGE = '更新資料失敗，請稍後再試';

/**
 * Replays the artifact's stored data-fetch recipe against current upstream data
 * (`POST /artifacts/{id}/refresh`) and returns freshly re-injected HTML. The result is
 * transient — the backend never persists it, so callers must render it (e.g. iframe
 * `srcDoc`) rather than reload the artifact's stored src.
 *
 * Never throws for the two backend-defined rejections (409 no-recipe/unsupported, 502
 * upstream source error) — both come back as a typed `status` the caller can switch on.
 */
export async function refreshArtifactData(id: string): Promise<RefreshArtifactDataResult> {
  try {
    const response = await apiClient.post<{ html: string }>(`/artifacts/${id}/refresh`);
    return { status: 'success', html: response.data.html };
  } catch (err: unknown) {
    if (axios.isAxiosError(err)) {
      const body = err.response?.data as BackendErrorBody | undefined;
      const backendMessage = body?.message ?? UNKNOWN_ERROR_MESSAGE;
      if (err.response?.status === 409) {
        return { status: 'not_refreshable', message: backendMessage };
      }
      if (err.response?.status === 502) {
        return { status: 'source_error', code: body?.code ?? 'UNKNOWN', message: backendMessage };
      }
    }
    return { status: 'unknown_error', message: UNKNOWN_ERROR_MESSAGE };
  }
}
