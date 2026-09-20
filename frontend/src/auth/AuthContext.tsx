import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import {
  login as apiLogin, loginWithPassword as apiLoginWithPassword,
  platformLogin as apiPlatformLogin,
  platformLoginVerify as apiPlatformVerify, fetchMe, type Session,
} from '../api'

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

  // Restore session on load and re-validate against the backend.
  useEffect(() => {
    const raw = localStorage.getItem('session')
    const token = localStorage.getItem('session_token')
    if (raw && token) {
      setSession(JSON.parse(raw))
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
      setLoading(false)
    }
  }, [])

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
    clearSession()
    setSession(null)
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
