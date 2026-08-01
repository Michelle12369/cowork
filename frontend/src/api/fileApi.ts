import type { UploadedFileInfo } from '@/types';
import { apiClient } from './apiClient';

export function uploadFiles(
  sessionId: string,
  files: File[],
  onProgress?: (percent: number) => void,
): Promise<UploadedFileInfo[]> {
  const formData = new FormData();
  files.forEach((file) => formData.append('files', file));
  return apiClient
    .post<UploadedFileInfo[]>(`/sessions/${sessionId}/files`, formData, {
      onUploadProgress: (event) => {
        if (onProgress && event.total) {
          onProgress(Math.round((event.loaded / event.total) * 100));
        }
      },
    })
    .then((res) => res.data);
}

export function deleteFile(sessionId: string, fileId: string): Promise<void> {
  return apiClient.delete(`/sessions/${sessionId}/files/${fileId}`).then(() => undefined);
}
