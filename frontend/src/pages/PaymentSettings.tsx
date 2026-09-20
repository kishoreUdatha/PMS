import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  CreditCard, Loader2, Save, AlertTriangle, CheckCircle2, Info, Copy,
  RefreshCw, Check, KeyRound, Lock,
} from 'lucide-react'
import Select from '../components/Select'
import {
  getPaymentCredentials, savePaymentCredentials, rotatePaymentCallback,
  type PaymentCredentials,
} from '../api'
import { Crumbs } from '../components/Crumbs'

/**
 * Where a tenant enters their own payment gateway.
 *
 * This screen exists because of one sentence in the schema: a booking engine
 * that collects other people's money into your account is not a feature, it is
 * a liability. Until a tenant saves their own keys here, their guests' card
 * payments settle into whichever merchant account the *deployment* was
 * configured with — so the loudest thing on the page is the banner that says
 * so.
 *
 * Three rules it keeps, all of them inherited from the API:
 *
 * * **A secret that goes in never comes back out.** There is no reveal and no
 *   round-trip. A stored secret shows as "Saved", and an empty box on save
 *   means keep it, not clear it. If a tenant loses a key, Razorpay reissues
 *   it; we are not a second copy for an attacker to fetch.
 * * **Whose account is in use is always stated.** Never inferred from whether
 *   the form looks filled in — the server says, and this renders what it says.
 * * **Half-entered is not live.** Razorpay cannot be switched on until all
 *   three credentials are saved, because the failure mode of a half-configured
 *   gateway is a guest paying into nothing.
 */

const EMPTY: PaymentCredentials = {
  provider: 'mock', source: 'none', enabled: false, key_id: '',
  key_secret_set: false, webhook_secret_set: false, webhook_url: null,
  can_store_secrets: true, using_deployment_account: false,
}

export default function PaymentSettings() {
  const [saved, setSaved] = useState<PaymentCredentials>(EMPTY)
  // Draft state for the three fields the server will accept. The two secrets
  // start empty on every load and after every save -- they are not readable,
  // so there is nothing to put in them, and an empty box is the honest
  // representation of "stored, and you cannot see it".
  const [provider, setProvider] = useState<'mock' | 'razorpay'>('mock')
  const [keyId, setKeyId] = useState('')
  const [keySecret, setKeySecret] = useState('')
  const [webhookSecret, setWebhookSecret] = useState('')
  const [enabled, setEnabled] = useState(false)

  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [ok, setOk] = useState('')
  const [copied, setCopied] = useState(false)
  const [confirmRotate, setConfirmRotate] = useState(false)

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['payment-credentials'],
    queryFn: getPaymentCredentials,
    retry: false,
  })

  useEffect(() => { if (data) adopt(data) }, [data])

  function adopt(c: PaymentCredentials) {
    setSaved(c)
    setProvider(c.provider)
    setKeyId(c.key_id)
    setEnabled(c.enabled)
    setKeySecret('')
    setWebhookSecret('')
  }

  // The same rule the table's CHECK constraint enforces, applied here first so
  // a tenant finds out before a round trip -- and so the switch that would
  // fail is visibly the thing blocking them. A secret already stored counts as
  // present; one being typed now counts too.
  const hasSecret = saved.key_secret_set || keySecret.trim() !== ''
  const hasHook = saved.webhook_secret_set || webhookSecret.trim() !== ''
  const missing = provider === 'razorpay'
    ? [
      ...(keyId.trim() ? [] : ['Key ID']),
      ...(hasSecret ? [] : ['Key Secret']),
      ...(hasHook ? [] : ['Webhook Secret']),
    ]
    : []
  const canGoLive = missing.length === 0

  async function save() {
    setErr(''); setOk('')
    if (enabled && !canGoLive) {
      setErr(`Razorpay cannot be switched on until every credential is saved. `
        + `Still missing: ${missing.join(', ')}.`)
      return
    }
    setBusy(true)
    try {
      const next = await savePaymentCredentials({
        provider,
        key_id: keyId.trim() || null,
        // Empty means keep. Sending "" would read as a deliberate blanking,
        // and the one thing worse than losing a secret is overwriting a good
        // one with nothing.
        key_secret: keySecret.trim() || null,
        webhook_secret: webhookSecret.trim() || null,
        enabled,
      })
      adopt(next)
      setOk(next.enabled && next.provider === 'razorpay'
        ? 'Saved. Guests now pay into your Razorpay account.'
        : 'Saved.')
    } catch (e) {
      setErr(msg(e, 'Could not save the gateway settings.'))
    } finally { setBusy(false) }
  }

  async function rotate() {
    setErr(''); setOk(''); setConfirmRotate(false); setBusy(true)
    try {
      adopt(await rotatePaymentCallback())
      setOk('A new callback URL has been issued. Payments will fail until you '
        + 'save it in your Razorpay dashboard.')
    } catch (e) {
      setErr(msg(e, 'Could not rotate the callback URL.'))
    } finally { setBusy(false) }
  }

  function copy(text: string) {
    void navigator.clipboard.writeText(text)
      .then(() => { setCopied(true); setTimeout(() => setCopied(false), 2000) })
      .catch(() => setErr('The clipboard is not available.'))
  }

  if (isLoading) {
    return <p className="py-20 text-center text-slate-400">
      <Loader2 className="mx-auto animate-spin" />
    </p>
  }

  // A 403 here is the normal answer for most of the staff list, not a fault.
  // These keys are the tenant's bank account by proxy, so the permission sits
  // with Administrator and Property IT Administrator and nobody else.
  if (error) {
    const m = msg(error, '')
    const denied = m.toLowerCase().includes('permission')
      || m.toLowerCase().includes('membership')
    return (
      <div className="space-y-4">
        <Header />
        <p className="flex items-start gap-2 rounded-xl bg-amber-50 px-4 py-3 text-sm text-caution">
          <Lock size={16} className="mt-0.5 shrink-0" />
          {denied
            ? 'You do not have permission to view or change the payment '
              + 'gateway. This is limited to Administrators and Property IT '
              + 'Administrators, because these keys decide where your guests’ '
              + 'money is paid.'
            : m || 'The gateway settings could not be loaded.'}
        </p>
        {!denied && (
          <button onClick={() => void refetch()}
            className="rounded-xl border border-slate-200 px-4 py-2 text-sm font-medium text-slate-600 hover:border-brand hover:text-brand">
            Try again
          </button>
        )}
      </div>
    )
  }

  const cls = 'w-full rounded-lg border border-slate-200 px-3 py-2.5 text-sm outline-none focus:border-brand'
  const dirty = provider !== saved.provider || keyId !== saved.key_id
    || enabled !== saved.enabled || keySecret !== '' || webhookSecret !== ''

  return (
    <div className="space-y-5">
      <Header />

      {err && (
        <p className="flex items-start gap-2 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700">
          <AlertTriangle size={16} className="mt-0.5 shrink-0" /> {err}
        </p>
      )}
      {ok && (
        <p className="flex items-start gap-2 rounded-xl bg-emerald-50 px-4 py-3 text-sm text-emerald-700">
          <CheckCircle2 size={16} className="mt-0.5 shrink-0" /> {ok}
        </p>
      )}

      <StatusBanner c={saved} />

      {!saved.can_store_secrets && (
        <p className="flex items-start gap-2 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700">
          <AlertTriangle size={16} className="mt-0.5 shrink-0" />
          <span>
            <b>This deployment cannot store gateway secrets.</b> Its encryption
            key (<code className="rounded bg-red-100 px-1">
              CREDENTIAL_ENCRYPTION_KEYS</code>) is not set, so saving a secret
            is refused rather than written to the database in the clear. Ask
            whoever runs this installation to set one; nothing on this page
            will save a secret until they do.
          </span>
        </p>
      )}

      <div className="rounded-2xl border border-slate-100 bg-white p-5">
        <h2 className="mb-1 text-lg font-semibold text-ink">Provider</h2>
        <p className="mb-4 text-sm text-slate-500">
          Which gateway takes card payments for your booking engine.
        </p>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <label className="block">
            <span className="mb-1 block text-sm font-medium text-slate-600">
              Payment provider
            </span>
            <Select className={cls} value={provider}
              onChange={(e) => setProvider(e.target.value as 'mock' | 'razorpay')}>
              <option value="mock">No gateway — simulate payments</option>
              <option value="razorpay">Razorpay</option>
            </Select>
            <span className="mt-1 block text-xs text-slate-400">
              {provider === 'mock'
                ? 'Orders come back with obviously fake ids, so you can run a '
                  + 'booking end to end without taking a payment. This is not '
                  + 'the same as Razorpay’s own Test Mode — for that, choose '
                  + 'Razorpay below and use your rzp_test_ keys.'
                : 'Guests pay through your own Razorpay account, using the '
                  + 'keys below. Razorpay test keys (rzp_test_…) work here: '
                  + 'real checkout, test cards, no real money.'}
            </span>
          </label>
        </div>
      </div>

      {provider === 'razorpay' && (
        <div className="rounded-2xl border border-slate-100 bg-white p-5">
          <h2 className="mb-1 flex items-center gap-2 text-lg font-semibold text-ink">
            <KeyRound size={18} className="text-brand" /> Razorpay credentials
          </h2>
          <p className="mb-4 text-sm text-slate-500">
            From your Razorpay dashboard, under Settings › API Keys. A saved
            secret is never shown again — leave a box empty to keep what is
            already stored.
          </p>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <label className="block sm:col-span-2 lg:col-span-1">
              <span className="mb-1 block text-sm font-medium text-slate-600">
                Key ID <Required when={enabled} />
              </span>
              <input value={keyId} className={cls} maxLength={160}
                placeholder="rzp_live_…"
                onChange={(e) => setKeyId(e.target.value)} />
              <span className="mt-1 block text-xs text-slate-400">
                The publishable half. It is sent to the guest’s browser, so it
                is not a secret.
              </span>
            </label>

            <SecretField
              label="Key Secret" required={enabled} stored={saved.key_secret_set}
              value={keySecret} onChange={setKeySecret} cls={cls}
              disabled={!saved.can_store_secrets}
              hint="Issued by Razorpay, beside the Key ID. Pairs with it to authorise charges on your account." />

            <SecretField
              label="Webhook Secret" required={enabled} stored={saved.webhook_secret_set}
              value={webhookSecret} onChange={setWebhookSecret} cls={cls}
              disabled={!saved.can_store_secrets}
              hint="Not issued by Razorpay — invent one. Any long random string will do, so long as you paste the same one into Razorpay's webhook settings. It signs the callbacks; without it a payment cannot be believed." />
          </div>
        </div>
      )}

      {provider === 'razorpay' && (
        <div className="rounded-2xl border border-slate-100 bg-white p-5">
          <h2 className="mb-1 text-lg font-semibold text-ink">
            Your callback URL
          </h2>
          <p className="mb-4 text-sm text-slate-500">
            Paste this into your Razorpay dashboard under Settings › Webhooks.
            It is unique to you: it is how we know a payment notification is
            yours before we check its signature.
          </p>

          {saved.webhook_url ? (
            <>
              <div className="flex items-center gap-2">
                <code className="flex-1 overflow-x-auto whitespace-nowrap rounded-lg bg-slate-50 px-3 py-2.5 text-xs text-slate-700">
                  {saved.webhook_url}
                </code>
                <button onClick={() => copy(saved.webhook_url ?? '')}
                  className="flex shrink-0 items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-2.5 text-sm font-medium text-slate-600 hover:border-brand hover:text-brand">
                  {copied ? <Check size={15} /> : <Copy size={15} />}
                  {copied ? 'Copied' : 'Copy'}
                </button>
              </div>

              {confirmRotate ? (
                <div className="mt-3 rounded-xl bg-amber-50 px-4 py-3 text-sm text-caution">
                  <p className="flex items-start gap-2">
                    <AlertTriangle size={16} className="mt-0.5 shrink-0" />
                    <span>
                      <b>Payments will fail until you save the new URL in
                      Razorpay.</b> The current URL stops being recognised the
                      moment it is replaced — that is the point of rotating it.
                    </span>
                  </p>
                  <div className="mt-3 flex gap-2">
                    <button onClick={() => void rotate()} disabled={busy}
                      className="rounded-lg bg-amber-600 px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-50">
                      Rotate it anyway
                    </button>
                    <button onClick={() => setConfirmRotate(false)}
                      className="rounded-lg border border-amber-300 px-3 py-1.5 text-xs font-medium text-caution">
                      Keep the current URL
                    </button>
                  </div>
                </div>
              ) : (
                <button onClick={() => setConfirmRotate(true)} disabled={busy}
                  className="mt-3 flex items-center gap-1.5 text-xs font-medium text-slate-500 hover:text-brand disabled:opacity-50">
                  <RefreshCw size={13} /> Issue a new callback URL
                </button>
              )}
              <p className="mt-2 text-xs text-slate-400">
                Rotate if this URL has been somewhere it should not — a support
                ticket, a screenshot, a shared document.
              </p>
            </>
          ) : (
            <p className="flex items-start gap-2 rounded-xl bg-slate-50 px-4 py-3 text-sm text-slate-500">
              <Info size={16} className="mt-0.5 shrink-0" />
              Your callback URL is created when you first save these settings.
            </p>
          )}
        </div>
      )}

      <div className="rounded-2xl border border-slate-100 bg-white p-5">
        <h2 className="mb-4 text-lg font-semibold text-ink">
          Online payments
        </h2>
        <label className="flex items-start gap-3">
          <input type="checkbox" checked={enabled} className="mt-1 size-4"
            onChange={(e) => setEnabled(e.target.checked)} />
          <span>
            <span className="block text-sm font-medium text-slate-700">
              Take payments through this gateway
            </span>
            <span className="block text-xs text-slate-500">
              {provider === 'mock'
                ? 'With no gateway selected this stays a rehearsal — '
                  + 'no money moves, whichever way this is set.'
                : 'Off, guests can hold a room but cannot pay online.'}
            </span>
          </span>
        </label>

        {enabled && !canGoLive && (
          <p className="mt-3 flex items-start gap-2 rounded-xl bg-amber-50 px-4 py-3 text-sm text-caution">
            <AlertTriangle size={16} className="mt-0.5 shrink-0" />
            Still missing: {missing.join(', ')}. A half-configured gateway
            cannot be switched on, because the failure mode is a guest paying
            into nothing.
          </p>
        )}
      </div>

      <div className="flex items-center justify-end gap-3">
        {dirty && <span className="text-xs text-slate-400">Unsaved changes</span>}
        <button onClick={() => void save()} disabled={busy || !dirty}
          className="flex items-center gap-2 rounded-xl bg-brand px-5 py-2.5 text-sm font-medium text-white enabled:hover:bg-brand/90 disabled:opacity-50">
          {busy ? <Loader2 size={16} className="animate-spin" /> : <Save size={16} />}
          Save Settings
        </button>
      </div>
    </div>
  )
}

function Header() {
  return (
    <div>
      <Crumbs title="Payment Gateway" trail={[
        { label: 'Administration', to: '/admin' },
        { label: 'Payment Gateway' }]} />
      <h1 className="mt-1 flex items-center gap-2 text-display text-ink">
        <CreditCard size={26} className="text-brand" /> Payment Gateway
      </h1>
      <p className="text-slate-500">
        The account your guests’ online payments are paid into.
      </p>
    </div>
  )
}

/**
 * Whose account is in use, stated rather than implied.
 *
 * `using_deployment_account` is the case this whole banner exists for: the
 * tenant has entered nothing, so the deployment's own keys are in effect and
 * their guests' money is landing somewhere else. The API reports it because a
 * silent fallback is what makes a fallback dangerous.
 */
function StatusBanner({ c }: { c: PaymentCredentials }) {
  if (c.using_deployment_account) {
    return (
      <p className="flex items-start gap-2 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700">
        <AlertTriangle size={16} className="mt-0.5 shrink-0" />
        <span>
          <b>Payments are not reaching you.</b> You have no gateway of your own,
          so guests are paying into the account this installation was set up
          with — not yours. Enter your own Razorpay keys below to take your own
          money.
        </span>
      </p>
    )
  }
  if (c.enabled && c.provider === 'razorpay' && c.source === 'tenant') {
    return (
      <p className="flex items-start gap-2 rounded-xl bg-emerald-50 px-4 py-3 text-sm text-emerald-700">
        <CheckCircle2 size={16} className="mt-0.5 shrink-0" />
        <span>
          <b>Live.</b> Guests pay through your own Razorpay account, and
          settlements go to the bank account registered with Razorpay.
        </span>
      </p>
    )
  }
  return (
    <p className="flex items-start gap-2 rounded-xl bg-slate-50 px-4 py-3 text-sm text-slate-600">
      <Info size={16} className="mt-0.5 shrink-0" />
      <span>
        <b>No money is being taken online.</b> Guests can hold a room through
        the booking engine, but cannot pay for it. The desk can still take
        payment in person.
      </span>
    </p>
  )
}

/**
 * A write-only secret.
 *
 * It shows whether one is stored and lets a new one replace it. It never shows
 * the value, because the API will not return it — there is no reveal to build
 * even if somebody asked for one.
 */
/**
 * The asterisk, shown only when the field actually is required.
 *
 * Which is not always. These credentials are only mandatory to switch the
 * gateway *on*; a tenant part-way through entering them can save what they
 * have and come back, and the server accepts that. Marking them required
 * regardless states a rule nothing enforces, and a form that asks for more
 * than it needs is one people abandon or fill with rubbish.
 */
function Required({ when }: { when: boolean }) {
  if (!when) return null
  return <span className="text-red-500" aria-hidden="true">*</span>
}

function SecretField({ label, stored, value, onChange, cls, hint, disabled,
                       required }: {
  label: string; stored: boolean; value: string
  onChange: (v: string) => void; cls: string; hint: string; disabled?: boolean
  required: boolean
}) {
  const typed = value.trim() !== ''
  return (
    <label className="block">
      <span className="mb-1 flex items-center gap-2 text-sm font-medium text-slate-600">
        {label} <Required when={required} />
        {/* Three states, not two. The badge reports what is *stored*, so a
            secret typed but not yet saved used to read "Not set" underneath a
            box full of dots -- which looks like the typing did not register.
            Saying "unsaved" instead tells the truth and names the next step. */}
        <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${
          stored ? 'bg-emerald-100 text-emerald-700'
            : typed ? 'bg-amber-100 text-amber-700'
              : 'bg-slate-75 text-slate-500'}`}>
          {stored ? 'Saved' : typed ? 'Unsaved' : 'Not set'}
        </span>
      </span>
      <input type="password" value={value} className={cls} maxLength={400}
        autoComplete="new-password" disabled={disabled}
        placeholder={stored ? 'Leave empty to keep the saved one' : ''}
        onChange={(e) => onChange(e.target.value)} />
      <span className="mt-1 block text-xs text-slate-400">{hint}</span>
    </label>
  )
}

function msg(e: unknown, fallback: string): string {
  const er = e as { response?: { data?: { detail?: string } }; message?: string }
  return er.response?.data?.detail ?? er.message ?? fallback
}
