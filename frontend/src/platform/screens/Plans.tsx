import { useEffect, useState } from 'react'
import { Check, CheckSquare, X } from 'lucide-react'
import {
  listPlans, createPlanVersion, publishPlanVersion, money,
  type Plan, type PlanLimits,
} from '../billingApi'
import {
  Busy, Button, Card, ErrorNote, Field, Note, Page, Panel,
  errorText, inputClass,
} from '../ui'

const METRICS = ['properties', 'rooms', 'active_users']
const MODULES = [
  'front_desk', 'reservations', 'housekeeping', 'guests', 'reports',
  'distribution', 'rates', 'pos', 'ai_center', 'administration',
]

/** Screen 12 — price a plan by creating a new version of it.
 *
 *  There is deliberately no way to edit a published version. That is the whole
 *  mechanism behind "a changed catalog price does not silently change existing
 *  contracts": a price is a new row, and every subscription keeps pointing at
 *  the version it was sold until somebody moves it on purpose.
 */
function NewVersion({ plan, onClose, onDone }: {
  plan: Plan; onClose: () => void; onDone: () => void
}) {
  const [amount, setAmount] = useState(plan.amount)
  const [cycle, setCycle] = useState<'monthly' | 'annual'>(
    plan.billing_cycle === 'annual' ? 'annual' : 'monthly')
  const [trial, setTrial] = useState(String(plan.trial_days))
  const [limits, setLimits] = useState<PlanLimits>(() => {
    const l: PlanLimits = {}
    METRICS.forEach((m) => { l[m] = plan.limits[m] ?? null })
    return l
  })
  const [modules, setModules] = useState<string[]>(plan.modules)
  const [reason, setReason] = useState('')
  const [publish, setPublish] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  function setLimit(metric: string, raw: string) {
    setLimits({ ...limits, [metric]: raw.trim() === '' ? null : Number(raw) })
  }

  async function save() {
    if (reason.trim().length < 3) {
      setErr('Give a reason of at least three characters.')
      return
    }
    setErr(''); setBusy(true)
    try {
      const v = await createPlanVersion(plan.plan_id, {
        amount, billing_cycle: cycle, trial_days: Number(trial) || 0,
        limits, modules, reason: reason.trim(),
      })
      if (publish) await publishPlanVersion(v.id, reason.trim())
      onDone()
    } catch (e) { setErr(errorText(e, 'The version could not be saved.')) }
    finally { setBusy(false) }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center overflow-y-auto bg-black/60 p-4">
      <Card className="max-h-[90vh] w-full max-w-2xl overflow-y-auto p-5">
        <div className="mb-1 flex items-start justify-between gap-3">
          <h2 className="text-base font-semibold text-pf-navy">
            {plan.name} — new version
          </h2>
          <button onClick={onClose} className="text-pf-muted hover:text-pf-body">
            <X size={18} />
          </button>
        </div>
        <p className="mb-4 text-pf-help text-pf-muted">
          This becomes v{plan.version_no + 1}. Tenants on v{plan.version_no}{' '}
          keep what they are paying until they are moved.
        </p>

        <ErrorNote>{err}</ErrorNote>

        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="Price per tenant">
            <input id="pv-amount" className={inputClass} value={amount}
              inputMode="decimal"
              onChange={(e) => setAmount(e.target.value)} />
          </Field>
          <Field label="Billing cycle">
            <select id="pv-cycle" className={inputClass} value={cycle}
              onChange={(e) => setCycle(e.target.value as 'monthly' | 'annual')}>
              <option value="monthly">Monthly</option>
              <option value="annual">Annual</option>
            </select>
          </Field>
          <Field label="Trial days">
            <input id="pv-trial" className={inputClass} value={trial}
              inputMode="numeric"
              onChange={(e) => setTrial(e.target.value.replace(/\D/g, ''))} />
          </Field>
        </div>

        <div className="mt-4">
          <div className="mb-1.5 text-xs font-medium text-pf-body">
            Limits — leave blank for unlimited
          </div>
          <div className="grid gap-3 sm:grid-cols-3">
            {METRICS.map((m) => (
              <Field key={m} label={m.replace('_', ' ')}>
                <input id={`pv-${m}`} className={inputClass}
                  inputMode="numeric" placeholder="unlimited"
                  value={limits[m] === null || limits[m] === undefined
                    ? '' : String(limits[m])}
                  onChange={(e) => setLimit(m, e.target.value.replace(/\D/g, ''))} />
              </Field>
            ))}
          </div>
        </div>

        <div className="mt-4">
          <div className="mb-1.5 text-xs font-medium text-pf-body">
            Included modules
          </div>
          <div className="flex flex-wrap gap-1.5">
            {MODULES.map((m) => {
              const on = modules.includes(m)
              return (
                <button key={m} type="button"
                  onClick={() => setModules(on
                    ? modules.filter((x) => x !== m) : [...modules, m])}
                  className={`rounded-lg border px-2.5 py-1 text-xs transition ${
                    on
                      ? 'border-pf-teal bg-pf-soft font-medium text-pf-deep'
                      : 'border-pf-border text-pf-muted hover:bg-pf-bg'
                  }`}>
                  {on && <Check size={11} className="mr-1 inline" />}{m}
                </button>
              )
            })}
          </div>
        </div>

        <div className="mt-4">
          <Field label="Reason">
            <input id="pv-reason" className={inputClass} value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="Why this price is changing" />
          </Field>
        </div>

        <label htmlFor="pv-publish"
          className="mt-3 flex cursor-pointer items-start gap-2 text-sm text-pf-body">
          <input id="pv-publish" type="checkbox" checked={publish}
            onChange={(e) => setPublish(e.target.checked)} className="mt-1" />
          <span>
            Publish immediately.
            <span className="block text-pf-help text-pf-muted">
              Leave this off to save a draft — a draft cannot be sold, so a
              price can be reviewed before anybody is put on it.
            </span>
          </span>
        </label>

        <div className="mt-4 flex justify-end gap-2">
          <Button onClick={onClose}>Cancel</Button>
          <Button tone="primary" onClick={save} disabled={busy}>
            {busy ? 'Saving…' : publish ? 'Create and publish' : 'Save draft'}
          </Button>
        </div>
      </Card>
    </div>
  )
}

export default function Plans() {
  const [plans, setPlans] = useState<Plan[]>([])
  const [busy, setBusy] = useState(true)
  const [err, setErr] = useState('')
  const [editing, setEditing] = useState<Plan | null>(null)

  function load() {
    setBusy(true)
    listPlans().then(setPlans)
      .catch((e) => setErr(errorText(e, 'Could not load plans.')))
      .finally(() => setBusy(false))
  }
  useEffect(load, [])

  if (busy) return <Busy />

  // The pack highlights the middle card as the suggested package. Taken from
  // sort order rather than hard-coded to "Growth", so renaming or reordering
  // the catalogue moves the highlight with it.
  const suggested = plans.length === 3 ? plans[1]?.code : undefined

  return (
    <Page
      eyebrow="Billing"
      crumbs={[{ label: 'Overview', to: '/platform' }, { label: 'Billing' }]}
      title="Plans &amp; pricing"
      subtitle="Define the software packages available to hotel and resort tenants."
      actions={
        suggested ? (
          <Button tone="primary" className="px-5 py-2.5"
            onClick={() => setEditing(
              plans.find((x) => x.code === suggested) ?? null)}>
            Edit {plans.find((x) => x.code === suggested)?.name} plan
          </Button>
        ) : undefined
      }>
      <ErrorNote>{err}</ErrorNote>

      <div className="grid gap-4 md:grid-cols-3">
        {plans.map((x) => {
          const isSuggested = x.code === suggested
          return (
            <div key={x.plan_id}
              className={`relative flex flex-col rounded-lg border bg-white p-6 ${
                isSuggested
                  ? 'border-pf-teal ring-1 ring-pf-teal/30'
                  : 'border-pf-border'}`}>
              {isSuggested && (
                <span className="mb-4 self-start rounded bg-pf-info-bg px-2.5 py-1 text-pf-badge text-pf-info-text">
                  Suggested growth package
                </span>
              )}

              <h2 className="text-[24px] font-bold leading-tight text-pf-navy">
                {x.name}
              </h2>
              <div className="mt-2 text-[35px] font-bold leading-none text-pf-deep">
                {money(x.amount)}
              </div>
              <div className="mt-2 text-pf-td text-pf-muted">
                per tenant / {x.billing_cycle === 'annual' ? 'year' : 'month'}
                {' · before tax'}
              </div>
              <p className="mt-4 text-pf-td text-pf-body">{x.summary}</p>

              <ul className="mt-5 space-y-2.5 border-t border-pf-divider pt-5">
                {/* Limits first, then modules: the pack lists what the plan
                    allows before what it includes, because the number is what
                    a customer is choosing between. */}
                {METRICS.map((m) => (
                  <li key={m} className="flex items-center gap-2.5 text-pf-td text-pf-body">
                    <CheckSquare size={15} className="shrink-0 text-pf-deep" />
                    {x.limits[m] === null || x.limits[m] === undefined
                      ? 'Unlimited ' + m.replace('_', ' ')
                      : x.limits[m] + ' ' + m.replace('_', ' ')}
                  </li>
                ))}
                {x.modules.slice(0, 3).map((m) => (
                  <li key={m} className="flex items-center gap-2.5 text-pf-td text-pf-body">
                    <CheckSquare size={15} className="shrink-0 text-pf-deep" />
                    {m.replace(/_/g, ' ')}
                  </li>
                ))}
                {x.modules.length > 3 && (
                  <li className="flex items-center gap-2.5 text-pf-td text-pf-body">
                    <CheckSquare size={15} className="shrink-0 text-pf-deep" />
                    {x.modules.length - 3} more module
                    {x.modules.length - 3 === 1 ? '' : 's'}
                  </li>
                )}
              </ul>

              <div className="mt-6 pt-1">
                <button onClick={() => setEditing(x)}
                  className={`w-full rounded-md px-4 py-3 text-pf-btn transition ${
                    isSuggested
                      ? 'bg-pf-teal text-white hover:bg-pf-hover'
                      : 'border border-pf-border text-pf-navy hover:bg-pf-bg'}`}>
                  Edit plan
                </button>
              </div>
            </div>
          )
        })}
      </div>

      <div className="mt-5">
        <Panel
          title="Catalog controls"
          rows={[
            { label: 'Pricing basis', value: 'Per tenant / month' },
            { label: 'Currency', value: 'INR' },
            { label: 'Amounts', value: 'Before tax' },
            { label: 'Versioning', value: 'New version per change' },
            { label: 'Existing subscribers', value: 'Keep their version',
              tone: 'good' },
          ]}
        />
      </div>

      <Note>
        Changing a price creates a new version. Tenants stay on the version
        they were sold until somebody moves them, which is a separate and
        previewed action on the Subscriptions screen.
      </Note>

      {editing && (
        <NewVersion plan={editing} onClose={() => setEditing(null)}
          onDone={() => { setEditing(null); load() }} />
      )}
    </Page>
  )
}
