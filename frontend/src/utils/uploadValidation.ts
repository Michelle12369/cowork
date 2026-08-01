import type { AppConfig } from '@/api/configApi';
import type { UploadedFileInfo } from '@/types';

/** Pre-flight validation mirroring backend upload limits; returns an error message for the
 *  first violated rule, or null when valid. Expired files count toward neither limit. */
export function validateFiles(
  existing: UploadedFileInfo[],
  incoming: File[],
  limits: AppConfig,
): string | null {
  const activeExisting = existing.filter((file) => !file.expired);

  if (activeExisting.length + incoming.length > limits.maxFiles) {
    return `You can attach up to ${limits.maxFiles} files per chat.`;
  }

  for (const file of incoming) {
    const ext = file.name.includes('.') ? file.name.split('.').pop()!.toLowerCase() : '';
    const singleLimit = limits.singleFileLimits[ext];
    if (singleLimit === undefined) {
      return `Unsupported file type: .${ext}`;
    }
    if (file.size > singleLimit) {
      return `${file.name} exceeds the size limit.`;
    }
  }

  const existingBytes = activeExisting.reduce((sum, file) => sum + file.sizeBytes, 0);
  const incomingBytes = incoming.reduce((sum, file) => sum + file.size, 0);
  if (existingBytes + incomingBytes > limits.maxSessionBytes) {
    return `Total attachments would exceed ${fmtSize(limits.maxSessionBytes)}.`;
  }

  return null;
}

const KB = 1024;
const MB = KB * 1024;
const GB = MB * 1024;

function fmtSize(bytes: number): string {
  if (bytes >= GB) return `${(bytes / GB).toFixed(1)} GB`;
  if (bytes >= MB) return `${(bytes / MB).toFixed(1)} MB`;
  if (bytes >= KB) return `${(bytes / KB).toFixed(1)} KB`;
  return `${bytes} B`;
}
