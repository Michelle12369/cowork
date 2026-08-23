import { apiClient } from './apiClient';

/** One connector group entry from `GET /api/connectors` (§11 connector selection). Empty list
 *  means the connector feature is off (no `AGENT_CONNECTORS_FILE` configured server-side). */
export interface ConnectorGroup {
  name: string;
  display: string;
  description: string;
}

export function fetchConnectors(): Promise<ConnectorGroup[]> {
  return apiClient.get<ConnectorGroup[]>('/connectors').then((res) => res.data);
}
