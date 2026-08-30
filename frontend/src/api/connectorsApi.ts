import type { ConnectorInfo } from '@/types';
import { apiClient } from './apiClient';

/** Fetches the API connector directory. Graceful-empty backend: an empty array means the
 *  connector feature is hidden entirely, never an error state. */
export function fetchConnectors(): Promise<ConnectorInfo[]> {
  return apiClient.get<ConnectorInfo[]>('/connectors').then((response) => response.data);
}
