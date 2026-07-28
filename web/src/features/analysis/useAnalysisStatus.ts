import { useQuery, type QueryKey } from '@tanstack/react-query'

import { getAnalysis, type Analysis } from '../../api/client'

const TERMINAL_STATUSES: ReadonlySet<Analysis['status']> = new Set(['done', 'failed', 'canceled'])
const POLL_INTERVAL_MS = 4000

export function analysisQueryKey(id: string): QueryKey {
  return ['analysis', id]
}

/** REST is always the source of truth and fallback transport (spec 8.3);
 * the WebSocket hook layers low-latency push updates on top of this poll,
 * it never replaces it. Polling stops once the job reaches a terminal
 * state. */
export function useAnalysisStatus(id: string) {
  return useQuery({
    queryKey: analysisQueryKey(id),
    queryFn: () => getAnalysis(id),
    refetchInterval: (query) =>
      query.state.data && TERMINAL_STATUSES.has(query.state.data.status) ? false : POLL_INTERVAL_MS,
  })
}
