import { useState } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import { AlertTriangle, Loader2, ShieldCheck } from 'lucide-react'
import { useAuth } from '../auth/AuthContext'
import { returnPath } from '../auth/sessionEvents'
import { MfaRequired } from '../api'
import { errorText } from '../lib/forms'

/**
 * Platform sign-in.
 *
 * Two fields, not three. A platform operator belongs to no organisation and
 * therefore has no property code — the field the tenant screen asks for first
 * is one they could never fill in, which is why this is a separate page rather
 * than a mode of the other.
 *
 * Same brand as the rest of the application. What marks the tier is the badge
 * and the line about what this access is, not a different palette.
 */
export default function PlatformLogin() {
  const { signInPlatform, verifyPlatform } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()

  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  // Set once the password is accepted and the device has not been proved.
  // Holding it flips this page to step two; it is worth nothing on its own.
  const [challenge, setChallenge] = useState('')
  const [code, setCode] = useState('')

  const field = 'w-full rounded-md border border-pf-border px-3 py-2.5 text-pf-input '
    + 'text-pf-body outline-none placeholder:text-pf-placeholder focus:border-pf-teal'

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(''); setBusy(true)
    try {
      if (challenge) {
        await verifyPlatform(challenge, code.trim())
      } else {
        await signInPlatform(email.trim(), password)
      }
      navigate(returnPath(location, '/platform'), { replace: true })
    } catch (err) {
      if (err instanceof MfaRequired) {
        setChallenge(err.challenge)
        setPassword('')
        setError('')
        return
      }
      setError(errorText(err, 'Sign-in failed.'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="scroll-slim flex h-full items-center justify-center overflow-y-auto bg-gradient-to-br from-teal-800 to-teal-950 p-4 font-platform">
      <div className="w-full max-w-md rounded-2xl bg-white p-8 shadow-xl">
        <div className="mb-6 text-center">
          <div className="text-2xl font-bold tracking-wide text-pf-deep">
            CHIRALA BAY
          </div>
          <div className="text-[10px] tracking-[0.3em] text-pf-deep-accent">
            R E S O R T
          </div>
          <div className="mt-2 inline-flex items-center gap-1.5 rounded-full bg-pf-soft px-2.5 py-0.5 text-[11px] font-medium text-pf-deep">
            <ShieldCheck size={12} /> Platform Console
          </div>
        </div>

        <h1 className="text-pf-login text-pf-navy">
          {challenge ? 'Verify your identity' : 'Sign in'}
        </h1>
        <p className="mt-2 text-pf-desc text-pf-muted">
          {challenge
            ? 'Enter the six-digit code from your authenticator. If you do not have it, one of your recovery codes works instead.'
            : <>Platform staff only. Property staff sign in{' '}
                <a href="/login" className="text-pf-deep hover:underline">here</a>.
              </>}
        </p>

        {error && (
          <div className="mt-4 flex items-start gap-2 rounded-lg bg-pf-err-bg px-3 py-2 text-sm text-pf-err-text">
            <AlertTriangle size={15} className="mt-0.5 shrink-0" /> {error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="mt-5 space-y-4">
          {challenge ? (
            <label className="block">
              <span className="mb-1.5 block text-pf-label text-pf-muted">
                Authentication code
              </span>
              <input value={code} autoFocus inputMode="text" maxLength={10}
                autoComplete="one-time-code"
                onChange={(e) => setCode(e.target.value.toUpperCase())}
                placeholder="123456"
                className={`${field} tracking-[0.3em]`} />
            </label>
          ) : (<>
          <label className="block">
            <span className="mb-1.5 block text-pf-label text-pf-muted">
              Email
            </span>
            <input value={email} type="email" autoComplete="username" autoFocus
              onChange={(e) => setEmail(e.target.value)}
              placeholder="ops@example.com" className={field} />
          </label>
          <label className="block">
            <span className="mb-1.5 block text-pf-label text-pf-muted">
              Password
            </span>
            <input value={password} type="password"
              autoComplete="current-password"
              onChange={(e) => setPassword(e.target.value)}
              className={field} />
          </label>
          </>)}

          <button type="submit" disabled={busy}
            className="flex w-full items-center justify-center gap-2 rounded-lg bg-pf-teal px-4 py-2.5 text-pf-btn text-white hover:bg-pf-hover disabled:opacity-50">
            {busy
              ? <Loader2 size={16} className="animate-spin" />
              : <ShieldCheck size={16} />}
            {challenge ? 'Verify and continue' : 'Sign in'}
          </button>
          {challenge && (
            <button type="button"
              onClick={() => { setChallenge(''); setCode(''); setError('') }}
              className="w-full text-center text-pf-help text-pf-muted hover:text-pf-body">
              Start again
            </button>
          )}
        </form>

        <p className="mt-6 text-xs leading-relaxed text-pf-muted">
          Every action taken from this console is recorded against the tenant
          it affects, and appears in that tenant's own audit log.
        </p>
      </div>
    </div>
  )
}
