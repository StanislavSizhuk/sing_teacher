import { useEffect, useState } from 'react'

/** Ticks once a second, returning whole seconds elapsed since `since` (an
 * ISO timestamp), so a multi-minute pipeline stage reads as visibly live
 * rather than a static label. Returns undefined while `since` is unset.
 * Re-derives from wall-clock time on every tick instead of counting ticks,
 * so it stays correct across tab throttling or a slow render. */
export function useElapsedSeconds(since: string | undefined): number | undefined {
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    if (!since) return
    const interval = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(interval)
  }, [since])

  if (!since) return undefined
  return Math.max(0, (now - new Date(since).getTime()) / 1000)
}
