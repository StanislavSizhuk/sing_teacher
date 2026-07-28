import { useSyncExternalStore } from 'react'

import { getSession, subscribeSession, type Session } from '../../api/sessionStore'

/** Reactive read of the current session; re-renders on login/logout/refresh. */
export function useSession(): Session | null {
  return useSyncExternalStore(subscribeSession, getSession)
}

export function useIsAuthenticated(): boolean {
  return useSession() !== null
}
