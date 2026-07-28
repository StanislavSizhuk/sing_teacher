import { useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'

import { currentAccessToken, type Analysis } from '../../api/client'
import { wsBaseUrl } from '../../api/env'
import { analysisQueryKey } from './useAnalysisStatus'

type ServerEvent =
  | { type: 'queued'; position: number }
  | { type: 'stage'; name: string; index: number; total: number }
  | { type: 'done'; analysis_id: string }
  | { type: 'failed'; error_code: string; message: string }

const MAX_BACKOFF_MS = 8000
const MAX_RECONNECT_ATTEMPTS = 5

/** Pushes low-latency queue-position updates over the WS status channel
 * (spec 8.3), with capped exponential-backoff reconnect. If the socket
 * never manages to connect, {@link useAnalysisStatus}'s REST poll keeps the
 * UI current regardless -- this hook is a latency optimization, not a
 * dependency. */
export function useAnalysisQueueSocket(analysisId: string, enabled: boolean): void {
  const queryClient = useQueryClient()

  useEffect(() => {
    if (!enabled) return

    let socket: WebSocket | null = null
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined
    let attempt = 0
    let stopped = false

    function connect() {
      const token = currentAccessToken()
      if (!token || stopped) return

      socket = new WebSocket(`${wsBaseUrl}/ws/analyses/${analysisId}`)

      socket.onopen = () => {
        attempt = 0
        socket?.send(JSON.stringify({ token }))
      }

      socket.onmessage = (event) => {
        let payload: ServerEvent
        try {
          payload = JSON.parse(event.data as string) as ServerEvent
        } catch {
          return
        }
        if (payload.type === 'queued') {
          queryClient.setQueryData<Analysis>(analysisQueryKey(analysisId), (prev) =>
            prev ? { ...prev, status: 'queued', queuePosition: payload.position } : prev,
          )
        } else {
          // stage/done/failed carry fields REST already owns end-to-end; a
          // refetch is simpler and less error-prone than hand-merging them.
          void queryClient.invalidateQueries({ queryKey: analysisQueryKey(analysisId) })
        }
      }

      socket.onclose = () => {
        if (stopped || attempt >= MAX_RECONNECT_ATTEMPTS) return
        const delay = Math.min(1000 * 2 ** attempt, MAX_BACKOFF_MS)
        attempt += 1
        reconnectTimer = setTimeout(connect, delay)
      }

      socket.onerror = () => socket?.close()
    }

    connect()

    return () => {
      stopped = true
      clearTimeout(reconnectTimer)
      socket?.close()
    }
  }, [analysisId, enabled, queryClient])
}
