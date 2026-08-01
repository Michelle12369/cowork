import type { SessionDetail, SessionSummary } from '@/types';
import { apiClient } from './apiClient';

export function listSessions(): Promise<SessionSummary[]> {
  return apiClient.get<SessionSummary[]>('/sessions').then((res) => res.data);
}

export function getSession(id: string): Promise<SessionDetail> {
  return apiClient.get<SessionDetail>(`/sessions/${id}`).then((res) => res.data);
}
