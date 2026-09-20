import { useState } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import { AlertTriangle, Loader2, LogIn, Mail } from 'lucide-react'
import { useAuth } from '../auth/AuthContext'
import { forgotPassword } from '../api'

/**
 * Signing in.
 *
 * Three things are asked for, because a person works at a property rather
 * than at an installation: the property code tells the system which one, and
 * the same address may belong to different people at different properties.
 *
 * The developer shortcut — a bare subject with no password — is still here
 * behind a link, because every seeded fixture in this repository uses it. The
 * server refuses it in production and refuses it for any account that has a
 * password, so it cannot become a way around the credential.
 */
export default function Login() {
  const { login, signIn } = useAuth()
  const navigate = useNavigate()
  const location = useLocation() as { state?: { from?: string } }

  const [mode, setMode] = useState<'password' | 'subject' | 'forgot'>('password')
  const [propertyCode, setPropertyCode] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [subject, setSubject] = useState('admin-user')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const field = 'w-full rounded-lg border border-slate-200 px-3 py-2.5 text-sm '
    + 'outline-none focus:border-brand'

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(''); setNotice(''); setBusy(true)
    try {
      if (mode === 'forgot') {
        await forgotPassword(propertyCode.trim(), email.trim())
        setNotice('If that address belongs to this property, a reset link is '
          + 'on its way. It expires in two hours.')
        setBusy(false)
        return
      }
      if (mode === 'subject') await login(subject.trim())
      else await signIn(propertyCode.trim(), email.trim(), password)
      navigate(location.state?.from ?? '/', { replace: true })
    } catch (err) {
      const e2 = err as { response?: { status?: number; data?: { detail?: string } } }
      setError(e2.response?.data?.detail
        ?? (e2.response?.status === 401
          ? 'Those sign-in details were not recognised.'
          : 'Sign in failed.'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-gradient-to-br from-teal-800 to-teal-950 p-4">
      <div className="w-full max-w-md rounded-2xl bg-white p-8 shadow-xl">
        <div className="mb-6 text-center">
          <div className="text-2xl font-bold tracking-wide text-brand-dark">CHIRALA BAY</div>
          <div className="text-[10px] tracking-[0.3em] text-brand-accent">R E S O R T</div>
          <div className="mt-1 text-xs text-slate-400">Property Management System</div>
        </div>

        <h1 className="text-xl font-semibold text-ink">
          {mode === 'forgot' ? 'Reset your password' : 'Sign in'}
        </h1>
        <p className="mt-1 text-sm text-slate-500">
          {mode === 'forgot'
            ? 'We will email a link to choose a new one.'
            : mode === 'subject'
              ? 'Developer sign-in. Refused in production.'
              : 'Use the 6-digit property code and email from your welcome message.'}
        </p>

        {error && (
          <div className="mt-4 flex items-start gap-2 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">
            <AlertTriangle size={15} className="mt-0.5 shrink-0" /> {error}
          </div>
        )}
        {notice && (
          <div className="mt-4 flex items-start gap-2 rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
            <Mail size={15} className="mt-0.5 shrink-0" /> {notice}
          </div>
        )}

        <form onSubmit={handleSubmit} className="mt-5 space-y-4">
          {mode === 'subject' ? (
            <label className="block">
              <span className="mb-1 block text-sm font-medium text-slate-600">
                User / Subject
              </span>
              <input value={subject} onChange={(e) => setSubject(e.target.value)}
                placeholder="e.g. admin-user" className={field} autoFocus />
            </label>
          ) : (<>
            <label className="block">
              <span className="mb-1 block text-sm font-medium text-slate-600">
                Property code
              </span>
              {/* Six digits now, so upper-casing is meaningless and a
                  numeric keypad is the right one on a phone. Non-digits are
                  dropped as they are typed rather than rejected on submit. */}
              <input value={propertyCode} autoFocus inputMode="numeric"
                maxLength={6} autoComplete="off"
                onChange={(e) =>
                  setPropertyCode(e.target.value.replace(/\D/g, ''))}
                placeholder="6-digit code"
                className={`${field} tracking-[0.3em]`} />
            </label>
            <label className="block">
              <span className="mb-1 block text-sm font-medium text-slate-600">
                Email
              </span>
              <input value={email} type="email" autoComplete="username"
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@example.com" className={field} />
            </label>
            {mode === 'password' && (
              <label className="block">
                <span className="mb-1 block text-sm font-medium text-slate-600">
                  Password
                </span>
                <input value={password} type="password"
                  autoComplete="current-password"
                  onChange={(e) => setPassword(e.target.value)}
                  className={field} />
              </label>
            )}
          </>)}

          <button type="submit" disabled={busy}
            className="flex w-full items-center justify-center gap-2 rounded-lg bg-brand px-4 py-2.5 text-sm font-semibold text-white hover:bg-brand-dark disabled:opacity-50">
            {busy ? <Loader2 size={16} className="animate-spin" /> : <LogIn size={16} />}
            {mode === 'forgot' ? 'Email me a link' : 'Sign in'}
          </button>
        </form>

        <div className="mt-4 flex items-center justify-between text-xs">
          {mode === 'password' ? (
            <button onClick={() => { setMode('forgot'); setError(''); setNotice('') }}
              className="font-medium text-brand hover:underline">
              Forgot password?
            </button>
          ) : (
            <button onClick={() => { setMode('password'); setError(''); setNotice('') }}
              className="font-medium text-brand hover:underline">
              Back to sign in
            </button>
          )}
          <button
            onClick={() => {
              setMode(mode === 'subject' ? 'password' : 'subject')
              setError(''); setNotice('')
            }}
            className="text-slate-400 hover:text-slate-600 hover:underline">
            {mode === 'subject' ? 'Use a password' : 'Developer sign-in'}
          </button>
        </div>
      </div>
    </div>
  )
}
