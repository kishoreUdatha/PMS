import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import {
  login as apiLogin, loginWithPassword as apiLoginWithPassword,
  platformLogin as apiPlatformLogin,
  platformLoginVerify as apiPlatformVerify, fetchMe, logoutSession,
  type Session,
} from '../api'
import { onSessionExpired } from './sessionEvents'

interface AuthState {
  session: Session | null
  loading: boolean
  /** The dev shortcut: a subject, no password. */
  login: (subject: string) => Promise<void>
  /** The real one. */
  signIn: (propertyCode: string, email: string, password: string) => Promise<void>
  /** Platform staff: email and password, no property code. */
  signInPlatform: (email: string, password: string) => Promise<void>
  /** Platform staff, step two: challenge + authenticator code. */
  verifyPlatform: (challenge: string, code: string) => Promise<void>
  /** Adopt a session handed back by the set-password link. */
  adopt: (session: Session) => void
  logout: () => void
}

const AuthContext = createContext<AuthState | undefined>(undefined)

function persist(session: Session) {
  localStorage.setItem('session_token', session.token)
  localStorage.setItem('session', JSON.stringify(session))
  // The active property must belong to whoever just signed in.
  //
  // This used to keep whatever was already stored, on the reasonable-sounding
  // grounds of not overriding a choice. But the stored value survives one
  // session ending and another beginning without a sign-out -- setting a
  // password, or signing in while a previous tenant's data is still in the
  // browser -- so the next person inherited a property from somebody else's
  // tenant. Every request carrying it was then refused with "Property outside
  // caller tenant": every screen 403, on a session that was perfectly valid.
  const mine = new Set(session.memberships.flatMap((m) => m.property_ids))
  const stored = localStorage.getItem('property_id')
  if (!stored || !mine.has(stored)) {
    const firstProp = session.memberships[0]?.property_ids[0]
    if (firstProp) localStorage.setItem('property_id', firstProp)
    else localStorage.removeItem('property_id')
  }
}

/** The stored session, or null when there is none or it cannot be read. A
 *  truncated or hand-edited value used to throw out of JSON.parse during the
 *  first render and leave a blank page; now it is simply "signed out". */
function readStoredSession(): Session | null {
  const raw = localStorage.getItem('session')
  if (!raw) return null
  try {
    const s = JSON.parse(raw) as Session | null
    return s && typeof s === 'object' && typeof s.token === 'string' ? s : null
  } catch {
    return null
  }
}

function clearSession() {
  localStorage.removeItem('session_token')
  localStorage.removeItem('session')
  // The active property goes too. Leaving it behind meant signing out of a
  // tenant and into the platform console carried the old property along, and
  // any screen that read it would act on a tenant nobody had selected.
  localStorage.removeItem('property_id')
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null)
  const [loading, setLoading] = useState(true)
  const queryClient = useQueryClient()
  const navigate = useNavigate()

  // Restore session on load and re-validate against the backend.
  useEffect(() => {
    const stored = readStoredSession()
    const token = localStorage.getItem('session_token')
    if (stored && token) {
      setSession(stored)
      fetchMe()
        .then((s) => {
          setSession(s)
          persist(s)
        })
        .catch(() => {
          clearSession()
          setSession(null)
        })
        .finally(() => setLoading(false))
    } else {
      // Half a session (or an unreadable one) is no session.
      if (token || localStorage.getItem('session')) clearSession()
      setLoading(false)
    }
  }, [])

  // The HTTP client saw a 401 on an ordinary call: the session is over.
  // Drop it, drop every cached screen (it belongs to that session), and send
  // the user to sign in, remembering where they were. Several requests in
  // flight will all fail together; only the first one acts.
  useEffect(() => onSessionExpired(() => {
    if (!localStorage.getItem('session_token')) return
    clearSession()
    setSession(null)
    queryClient.clear()
    const { pathname, search } = window.location
    const platform = pathname.startsWith('/platform')
    const loginPath = platform ? '/platform/login' : '/login'
    if (pathname === loginPath) return
    navigate(`${loginPath}?from=${encodeURIComponent(pathname + search)}`, {
      replace: true,
      state: { from: pathname + search, expired: true },
    })
  }), [queryClient, navigate])

  async function login(subject: string) {
    const s = await apiLogin(subject)
    persist(s)
    setSession(s)
  }

  async function signIn(propertyCode: string, email: string, password: string) {
    const s = await apiLoginWithPassword(propertyCode, email, password)
    persist(s)
    setSession(s)
  }

  async function signInPlatform(email: string, password: string) {
    const s = await apiPlatformLogin(email, password)
    persist(s)
    setSession(s)
  }

  async function verifyPlatform(challenge: string, code: string) {
    const s = await apiPlatformVerify(challenge, code)
    persist(s)
    setSession(s)
  }

  function adopt(s: Session) {
    persist(s)
    setSession(s)
  }

  function logout() {
    const token = localStorage.getItem('session_token')
    const platform = session?.is_platform ?? false
    // Tell the server, but never wait on it or fail because of it: an older
    // backend has no such endpoint (404) and a dead network must not keep
    // anybody signed in on a shared front-desk machine.
    if (token) logoutSession(token).catch(() => undefined)
    clearSession()
    setSession(null)
    queryClient.clear()
    navigate(platform ? '/platform/login' : '/login', { replace: true })
  }

  return (
    <AuthContext.Provider value={{ session, loading, login, signIn, signInPlatform, verifyPlatform, adopt, logout }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}
