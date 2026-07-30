/** Formats a duration given in seconds as "12s" or "1m 05s", for the
 * per-stage timer and completed-stage duration list (QueueStatus). */
export function formatDurationSeconds(totalSeconds: number): string {
  const seconds = Math.max(0, Math.floor(totalSeconds))
  const minutes = Math.floor(seconds / 60)
  const remainder = seconds % 60
  if (minutes === 0) return `${remainder}s`
  return `${minutes}m ${String(remainder).padStart(2, '0')}s`
}
