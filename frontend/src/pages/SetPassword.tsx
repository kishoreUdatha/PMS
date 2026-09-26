import { useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { AlertTriangle, Check, KeyRound, Loader2 } from 'lucide-react'
import { useAuth } from '../auth/AuthContext'
import { setPassword as apiSetPassword } from '../api'
import { errorText } from '../lib/forms'

/**
 * Where a welcome or reset link lands: choose a password, and you are in.
 *
 * Signing in straight afterwards is deliberate. Sending somebody back to a
 * form to retype what they have just chosen is how a password gets written on
 * a sticky note — and the server has just proved they hold a valid link, so
 * there is nothing further to establish.
 */
export default function SetPassword() {
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const { adopt } = useAuth()
  const token = params.get('token') ?? ''

  const [password, setPasswordValue] = useState('')
  const [again, setAgain] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const field = 'w-full rounded-lg border border-slate-200 px-3 py-2.5 text-sm '
    + 'outline-none focus:border-brand'

  // Checked here only to save a round trip; the server decides.
  const tooShort = password.length > 0 && password.length < 10
  const mismatch = again.length > 0 && again !== password
  const ready = password.length >= 10 && again === password && !busy

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setError(''); setBusy(true)
    try {
      adopt(await apiSetPassword(token, password))
      // "/" is not always the dashboard: OnboardingGate sends anyone whose
      // property is still in setup to the wizard instead. Deciding it there
      // rather than here means the rule holds for a bookmark and a normal
      // sign-in too, not only for somebody arriving from a welcome link.
      navigate('/', { replace: true })
    } catch (err) {
      setError(errorText(err, 'That password could not be set. Ask for a new link.'))
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
        </div>

        {token === '' ? (
          <>
            <h1 className="text-xl font-semibold text-ink">
              This link is incomplete
            </h1>
            <p className="mt-1 text-sm text-slate-500">
              Open the link from your email in full, or ask for a new one from
              the sign-in page.
            </p>
            <button onClick={() => navigate('/login')}
              className="mt-5 w-full rounded-lg bg-brand px-4 py-2.5 text-sm font-semibold text-white hover:bg-brand-dark">
              Go to sign in
            </button>
          </>
        ) : (<>
          <h1 className="flex items-center gap-2 text-xl font-semibold text-ink">
            <KeyRound size={18} className="text-brand" /> Choose a password
          </h1>
          <p className="mt-1 text-sm text-slate-500">
            At least 10 characters. A few words you will remember beat one word
            with symbols in it.
          </p>

          {error && (
            <div className="mt-4 flex items-start gap-2 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">
              <AlertTriangle size={15} className="mt-0.5 shrink-0" /> {error}
            </div>
          )}

          <form onSubmit={submit} className="mt-5 space-y-4">
            <label className="block">
              <span className="mb-1 block text-sm font-medium text-slate-600">
                New password
              </span>
              <input type="password" value={password} autoFocus
                autoComplete="new-password" className={field}
                onChange={(e) => setPasswordValue(e.target.value)} />
              {tooShort && (
                <span className="mt-1 block text-xs text-amber-700">
                  {10 - password.length} more character
                  {10 - password.length === 1 ? '' : 's'} to go.
                </span>
              )}
            </label>
            <label className="block">
              <span className="mb-1 block text-sm font-medium text-slate-600">
                Type it again
              </span>
              <input type="password" value={again} autoComplete="new-password"
                className={field} onChange={(e) => setAgain(e.target.value)} />
              {mismatch && (
                <span className="mt-1 block text-xs text-amber-700">
                  These two do not match.
                </span>
              )}
            </label>

            <button type="submit" disabled={!ready}
              className="flex w-full items-center justify-center gap-2 rounded-lg bg-brand px-4 py-2.5 text-sm font-semibold text-white hover:bg-brand-dark disabled:opacity-50">
              {busy ? <Loader2 size={16} className="animate-spin" />
                    : <Check size={16} />}
              Set password and sign in
            </button>
          </form>
        </>)}
      </div>
    </div>
  )
}
