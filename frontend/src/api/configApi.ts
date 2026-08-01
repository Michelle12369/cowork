import { apiClient } from './apiClient';

export interface AppConfig {
  retentionDays: number;
  maxFiles: number;
  maxSessionBytes: number;
  /** Keys are lowercase file extensions; values are byte limits matching backend enforcement. */
  singleFileLimits: Record<string, number>;
}

export const fetchAppConfig = (): Promise<AppConfig> =>
  apiClient.get<AppConfig>('/config').then((response) => response.data);
