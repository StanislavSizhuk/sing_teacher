import { useQuery } from '@tanstack/react-query'

import { getProgress } from '../../api/client'

/** FR-35: the caller's overall_score points, oldest first, for the progress
 * chart. No polling -- this list only grows when an analysis finishes, and
 * the screens that make that happen (QueueStatus) already live elsewhere. */
export function useProgress() {
  return useQuery({ queryKey: ['progress'], queryFn: getProgress })
}
