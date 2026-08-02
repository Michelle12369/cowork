/** Formats a turn duration for display: sub-minute as 「N s」, otherwise 「M min S s」.
 *  Sub-second durations floor to 1 second so the label never reads 「0 s」. */
export function formatDuration(durationMs: number): string {
  const totalSeconds = Math.max(1, Math.round(durationMs / 1000));
  if (totalSeconds < 60) {
    return `${totalSeconds} s`;
  }
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes} min ${seconds} s`;
}
