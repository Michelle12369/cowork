/** Formats a turn duration for display: sub-minute as 「N 秒」, otherwise 「M 分 S 秒」.
 *  Sub-second durations floor to 1 second so the label never reads 「0 秒」. */
export function formatDuration(durationMs: number): string {
  const totalSeconds = Math.max(1, Math.round(durationMs / 1000));
  if (totalSeconds < 60) {
    return `${totalSeconds} 秒`;
  }
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes} 分 ${seconds} 秒`;
}
