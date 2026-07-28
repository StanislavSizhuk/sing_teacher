/** The in-memory access token and its expiry. Never persisted: a page
 * reload re-mints one from the httpOnly refresh cookie via /auth/refresh,
 * exactly like the Google OAuth callback flow already does (spec 8.3). */
export interface Session {
  accessToken: string
  expiresAt: number
}

type Listener = () => void

let session: Session | null = null
const listeners = new Set<Listener>()

export function getSession(): Session | null {
  return session
}

export function setSession(next: Session | null): void {
  session = next
  for (const listener of listeners) listener()
}

export function subscribeSession(listener: Listener): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}
