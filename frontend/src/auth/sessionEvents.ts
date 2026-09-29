/** A tiny bus between the HTTP client and the auth context.
 *
 *  api.ts sees a 401 but cannot reach React state, and AuthContext imports
 *  api.ts, so a direct call the other way would be a circular import. The
 *  client raises an event on window instead and AuthProvider listens for it. */

const SESSION_EXPIRED = 'pms:session-expired'

export function announceSessionExpired(): void {
  window.dispatchEvent(new Event(SESSION_EXPIRED))
}

export function onSessionExpired(handler: () => void): () => void {
  window.addEventListener(SESSION_EXPIRED, handler)
  return () => window.removeEventListener(SESSION_EXPIRED, handler)
}

/** Where to go after signing in: the `from` the guard or the expiry handler
 *  left (router state, or ?from= after a reload). Only a same-origin path is
 *  accepted, so a crafted link cannot bounce a fresh sign-in off-site. */
export function returnPath(
  location: { search: string; state?: unknown }, fallback: string,
): string {
  const state = location.state as { from?: unknown } | null | undefined
  const candidate = typeof state?.from === 'string' && state.from
    ? state.from
    : new URLSearchParams(location.search).get('from')
  if (!candidate || !candidate.startsWith('/') || candidate.startsWith('//')) return fallback
  if (candidate.startsWith('/login') || candidate.startsWith('/platform/login')) return fallback
  return candidate
}
