import { useEffect, useState } from 'react'
import { AlertTriangle, Copy, KeyRound, ShieldCheck } from 'lucide-react'
import { mfaStatus, mfaEnrol, mfaConfirm, type MfaStatus } from '../api'
import {
  Busy, Button, Card, ErrorNote, Field, Page, errorText, inputClass,
} from '../ui'

/** Screen 02 / 23 — the operator's own second factor.
 *
 *  The secret is shown once, here, and never again: there is no endpoint that
 *  reads it back, because a route that could return a TOTP seed would make
 *  every session capable of minting the second factor it is supposed to be
 *  checked against.
 *
 *  The QR is drawn from the otpauth URI through a public chart service in most
 *  products. Not here — that would send the seed to a third party. The URI is
 *  shown as text and as a manual key instead, which every authenticator app
 *  accepts.
 */
export default function Security() {
  const [state, setState] = useState<MfaStatus | null>(null)
  const [busy, setBusy] = useState(true)
  const [err, setErr] = useState('')
  const [enrolling, setEnrolling] = useState<{
    secret: string; otpauth_uri: string; recovery_codes: string[]
  } | null>(null)
  const [code, setCode] = useState('')
  const [current, setCurrent] = useState('')
  const [saved, setSaved] = useState(false)
  const [copied, setCopied] = useState('')

  function load() {
    mfaStatus().then(setState)
      .catch((e) => setErr(errorText(e, 'Could not read your MFA status.')))
      .finally(() => setBusy(false))
  }
  useEffect(load, [])

  async function start() {
    setErr(''); setSaved(false); setCode('')
    try {
      const replacing = state?.status === 'active'
      setEnrolling(await mfaEnrol(replacing ? current.trim() : undefined))
      setCurrent('')
    } catch (e) { setErr(errorText(e, 'Enrolment could not be started.')) }
  }

  async function confirm() {
    setErr('')
    try {
      await mfaConfirm(code.trim())
      setEnrolling(null); setCode('')
      load()
    } catch (e) { setErr(errorText(e, 'That code was not accepted.')) }
  }

  function copy(text: string, what: string) {
    navigator.clipboard?.writeText(text)
      .then(() => { setCopied(what); setTimeout(() => setCopied(''), 1500) })
      .catch(() => setErr('Could not copy — select the text and copy it by hand.'))
  }

  if (busy) return <Busy />

  const active = state?.status === 'active'

  return (
    <Page
      eyebrow="Audit & security"
      crumbs={[{ label: 'Overview', to: '/platform' },
        { label: 'Security' }]}
      title="Your second factor"
      subtitle="A platform account can read every tenant. One password is not enough to protect it.">
      <ErrorNote>{err}</ErrorNote>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="p-5 lg:col-span-2">
          {!enrolling ? (
            <>
              <div className="flex items-start gap-3">
                <div className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-lg ${
                  active ? 'bg-pf-ok-bg text-pf-ok-text' : 'bg-pf-warn-bg text-pf-warn-text'}`}>
                  {active ? <ShieldCheck size={19} /> : <AlertTriangle size={19} />}
                </div>
                <div>
                  <div className="font-medium text-pf-navy">
                    {active
                      ? 'Two-factor authentication is on'
                      : 'Two-factor authentication is off'}
                  </div>
                  <p className="mt-1 text-sm text-pf-muted">
                    {active
                      ? `Sign-in asks for a code from your authenticator. ${state?.recovery_codes_unused} recovery code(s) unused.`
                      : 'Your account is protected by a password alone. Anyone who learns it can suspend any tenant on this platform.'}
                  </p>
                </div>
              </div>

              {state?.required && !active && (
                <div className="mt-4 flex items-start gap-2 rounded-lg bg-pf-err-bg px-3 py-2 text-sm text-pf-err-text">
                  <AlertTriangle size={15} className="mt-0.5 shrink-0" />
                  This platform requires a second factor. Sign-in will be
                  refused until you enrol.
                </div>
              )}

              <div className="mt-5">
                {active && (
                  <label className="mb-3 block max-w-xs text-sm text-pf-muted">
                    Current code or a recovery code
                    <input value={current} inputMode="numeric" autoComplete="one-time-code"
                      maxLength={10} onChange={(e) => setCurrent(e.target.value)}
                      className="mt-1 w-full rounded-lg border border-pf-divider px-3 py-2 font-mono text-sm text-pf-navy outline-none focus:border-pf-navy" />
                  </label>
                )}
                <Button tone="primary" onClick={start}
                  disabled={active && current.trim().length < 6}>
                  {active ? 'Replace my authenticator' : 'Set up two-factor'}
                </Button>
                {active && (
                  <p className="mt-2 text-pf-help text-pf-muted">
                    Replacing issues a new secret and new recovery codes. The
                    old ones stop working immediately.
                  </p>
                )}
              </div>
            </>
          ) : (
            <>
              <h2 className="text-pf-desc font-semibold text-pf-navy">
                1 · Add this to your authenticator
              </h2>
              <p className="mt-1 text-sm text-pf-muted">
                Enter the key by hand in Google Authenticator, 1Password, Authy
                or similar. It is shown once and cannot be retrieved later.
              </p>

              <div className="mt-3 rounded-lg border border-pf-divider bg-pf-bg p-3">
                <div className="text-xs font-medium uppercase tracking-wide text-pf-muted">
                  Setup key
                </div>
                <div className="mt-1 flex items-center gap-2">
                  <code className="flex-1 break-all font-mono text-sm text-pf-navy">
                    {enrolling.secret}
                  </code>
                  <Button onClick={() => copy(enrolling.secret, 'secret')}>
                    <span className="flex items-center gap-1.5">
                      <Copy size={13} /> {copied === 'secret' ? 'Copied' : 'Copy'}
                    </span>
                  </Button>
                </div>
              </div>

              <h2 className="mt-5 text-sm font-semibold text-pf-navy">
                2 · Save your recovery codes
              </h2>
              <p className="mt-1 text-sm text-pf-muted">
                Each works once, in place of a code, on the day you do not have
                your phone. Store them somewhere other than the phone.
              </p>
              <div className="mt-2 grid grid-cols-2 gap-1.5 rounded-lg border border-pf-divider bg-pf-bg p-3 sm:grid-cols-5">
                {enrolling.recovery_codes.map((c) => (
                  <code key={c} className="font-mono text-sm text-pf-body">{c}</code>
                ))}
              </div>
              <div className="mt-2 flex items-center gap-3">
                <Button onClick={() =>
                  copy(enrolling.recovery_codes.join('\n'), 'codes')}>
                  <span className="flex items-center gap-1.5">
                    <Copy size={13} /> {copied === 'codes' ? 'Copied' : 'Copy all'}
                  </span>
                </Button>
                <label htmlFor="saved-codes"
                  className="flex cursor-pointer items-center gap-2 text-sm text-pf-body">
                  <input id="saved-codes" type="checkbox" checked={saved}
                    onChange={(e) => setSaved(e.target.checked)} />
                  I have saved these
                </label>
              </div>

              <h2 className="mt-5 text-sm font-semibold text-pf-navy">
                3 · Confirm
              </h2>
              <p className="mt-1 text-sm text-pf-muted">
                Enter the six-digit code your authenticator is showing now. The
                factor is not switched on until this succeeds — so an app that
                did not take the key cannot lock you out.
              </p>
              <div className="mt-3 flex max-w-xs items-end gap-2">
                <Field label="Code">
                  <input id="mfa-code" className={`${inputClass} tracking-[0.3em]`}
                    inputMode="numeric" maxLength={6} autoFocus value={code}
                    onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))}
                    onKeyDown={(e) => e.key === 'Enter' && saved && confirm()} />
                </Field>
                <Button tone="primary" onClick={confirm}
                  disabled={!saved || code.length !== 6}>
                  Turn it on
                </Button>
              </div>
              <div className="mt-3">
                <Button onClick={() => setEnrolling(null)}>Cancel</Button>
              </div>
            </>
          )}
        </Card>

        <Card className="p-5">
          <h2 className="mb-2 flex items-center gap-2 text-sm font-semibold text-pf-navy">
            <KeyRound size={14} className="text-pf-deep" /> How it works
          </h2>
          <ul className="space-y-2.5 text-sm text-pf-muted">
            <li>
              Your password proves the credential. The code proves the device.
              Sign-in needs both.
            </li>
            <li>
              The secret is encrypted at rest with the same key material that
              protects tenant payment credentials.
            </li>
            <li>
              A code cannot be used twice, even inside its own thirty-second
              window.
            </li>
            <li>
              Wrong codes count towards the same lockout as wrong passwords —
              six digits fall quickly to unlimited guessing.
            </li>
          </ul>
        </Card>
      </div>
    </Page>
  )
}
