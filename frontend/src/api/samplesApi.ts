import type { UploadedFileInfo } from '@/types';
import { apiClient } from './apiClient';

export interface SampleDataset {
  name: string;
  title: string;
  description: string;
  fileAliases: string[];
}

export function fetchSampleDatasets(): Promise<SampleDataset[]> {
  return apiClient.get<SampleDataset[]>('/samples').then((res) => res.data);
}

export function loadSampleDataset(
  sessionId: string,
  sampleName: string,
): Promise<UploadedFileInfo[]> {
  return apiClient
    .post<UploadedFileInfo[]>(`/sessions/${sessionId}/files/samples/${sampleName}`)
    .then((res) => res.data);
}
