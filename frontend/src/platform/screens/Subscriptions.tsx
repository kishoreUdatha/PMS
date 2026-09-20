import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { AlertTriangle, ArrowUpRight, X } from 'lucide-react'
import {
  listSubscriptions, listPlans, previewChange, applyChange, money,
  type SubscriptionRow, type Plan, type ChangePreview,
} from '../billingApi'
import {
  Busy, Button, Card, DataTable, ErrorNote, Field, FilterBar, Metrics, Note,
  Page, Pill, Select, Td, errorText, inputClass,
} from '../ui'

/** Change a tenant's plan — preview first, always.
 *
 *  The spec asks a plan change to show scope, limits, effective date and
 *  billing effect before it is confirmed, and this dialog will not let you
 *  confirm anything you have not been shown: the Apply button does not exist
 *  until a preview has returned.
 */
function ChangePlan({ row, plans, onClose, onDone }: {
  row: SubscriptionRow
  plans: Plan[]
  onClose: () => void
  onDone: () => void
}) {
  const [planCode, setPlanCode] = useState(plans[0]?.code ?? '')
  const [reason, setReason] = useState('')
  const [preview, setPreview] = useState<ChangePreview | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  // A preview belongs to the plan it previewed. Changing the selection has to
  // discard it, or Apply would send a code the operator never saw costed.
  function choose(code: string) {
    setPlanCode(code)
    setPreview(null)
  }

  async function doPreview() {
    setErr(''); setBusy(true)
    try {
      setPreview(await previewChange(row.organization_id, planCode,
        reason.trim() || 'plan change'))
    } catch (e) { setErr(errorText(e, 'Could not price that change.')) }
    finally { setBusy(false) }
  }

  async function doApply() {
    if (!preview?.can_apply) return
    setErr(''); setBusy(true)
    try {
      await applyChange(row.organization_id, planCode, reason.trim())
      onDone()
    } catch (e) { setErr(errorText(e, 'The change was refused.')) }
    finally { setBusy(false) }
  }

  const proration = Number(preview?.proration_amount ?? 0)

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center overflow-y-auto bg-black/60 p-4">
      <Card className="max-h-[90vh] w-full max-w-lg overflow-y-auto p-5">
        <div className="mb-1 flex items-start justify-between gap-3">
          <h2 className="text-base font-semibold text-pf-navy">
            Change plan — {row.organization_name}
          </h2>
          <button onClick={onClose} className="text-pf-muted hover:text-pf-body">
            <X size={18} />
          </button>
        </div>
        <p className="mb-4 text-pf-help text-pf-muted">
          {row.plan_code
            ? `Currently ${row.plan_name} at ${money(row.amount)} × ${row.quantity}`
            : 'This tenant is not on a plan.'}
        </p>

        <ErrorNote>{err}</ErrorNote>

        <div className="space-y-3">
          <Field label="New plan">
            <select id="plan-code" className={inputClass} value={planCode}
              onChange={(e) => choose(e.target.value)}>
              {plans.map((p) => (
                <option key={p.code} value={p.code}>
                  {p.name} — {money(p.amount)} per tenant / {p.billing_cycle}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Reason (recorded in the tenant's audit log)">
            <input id="change-reason" className={inputClass} value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="Upgrade requested by the owner" />
          </Field>
        </div>

        {preview && (
          <div className="mt-4 rounded-lg border border-pf-divider bg-pf-bg p-3">
            <div className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
              <span className="text-pf-muted">Properties billed</span>
              <span className="text-right text-pf-navy">{preview.quantity}</span>
              <span className="text-pf-muted">New period total</span>
              <span className="text-right font-medium text-pf-navy">
                {money(preview.new_period_total)}
              </span>
              <span className="text-pf-muted">
                {proration >= 0 ? 'Due now (pro rata)' : 'Credit (pro rata)'}
              </span>
              <span className={`text-right ${
                proration > 0 ? 'text-pf-warn-text' : 'text-pf-body'}`}>
                {money(Math.abs(proration))}
              </span>
              <span className="text-pf-muted">Effective</span>
              <span className="text-right text-pf-body">
                {preview.effective_on}
              </span>
            </div>

            <div className="mt-3 border-t border-pf-divider pt-3">
              <div className="mb-1.5 text-xs font-medium text-pf-body">
                Usage against the new limits
              </div>
              <div className="space-y-1">
                {Object.entries(preview.limits).map(([metric, limit]) => {
                  const have = preview.usage[metric] ?? 0
                  const over = limit !== null && have > limit
                  return (
                    <div key={metric} className="flex justify-between text-xs">
                      <span className="text-pf-muted">{metric}</span>
                      <span className={over ? 'font-medium text-pf-err-text' : 'text-pf-body'}>
                        {have} of {limit === null ? 'unlimited' : limit}
                      </span>
                    </div>
                  )
                })}
              </div>
            </div>

            {!preview.can_apply && (
              <div className="mt-3 flex items-start gap-2 rounded-lg bg-pf-err-bg px-3 py-2 text-sm text-pf-err-text">
                <AlertTriangle size={15} className="mt-0.5 shrink-0" />
                <span>
                  This downgrade would strand data.{' '}
                  {preview.over_limit.map((b) =>
                    `${b.metric} is ${b.over_by} over`).join(', ')}. Reduce it
                  first — there is no override.
                </span>
              </div>
            )}
          </div>
        )}

        <div className="mt-4 flex justify-end gap-2">
          <Button onClick={onClose}>Cancel</Button>
          {!preview ? (
            <Button tone="primary" onClick={doPreview} disabled={busy || !planCode}>
              {busy ? 'Pricing…' : 'Preview change'}
            </Button>
          ) : (
            <Button tone="primary" onClick={doApply}
              disabled={busy || !preview.can_apply}>
              {busy ? 'Applying…' : 'Apply change'}
            </Button>
          )}
        </div>
      </Card>
    </div>
  )
}

/** "01 Oct · renewal" — the pack's format for the Next event column. */
function nextEvent(r: SubscriptionRow): string {
  const on = (d: string) => new Date(d).toLocaleDateString('en-IN',
    { day: '2-digit', month: 'short' })
  if (r.trial_ends_on) return `${on(r.trial_ends_on)} · trial end`
  if (r.grace_until) return `${on(r.grace_until)} · grace end`
  if (r.current_period_end) return `${on(r.current_period_end)} · renewal`
  return 'No renewal'
}


export default function Subscriptions() {
  const navigate = useNavigate()
  const [rows, setRows] = useState<SubscriptionRow[]>([])
  const [plans, setPlans] = useState<Plan[]>([])
  const [status, setStatus] = useState('')
  const [planFilter, setPlanFilter] = useState('')
  const [busy, setBusy] = useState(true)
  const [applying, setApplying] = useState(false)
  const [err, setErr] = useState('')
  const [changing, setChanging] = useState<SubscriptionRow | null>(null)

  function load() {
    setApplying(true)
    Promise.all([listSubscriptions(status || undefined), listPlans()])
      .then(([s, p]) => {
        // The plan filter has no server-side parameter, so it is applied
        // here. Said plainly rather than hidden: the record count still
        // reflects what is on screen, which is what the count is for.
        setRows(planFilter ? s.filter((r) => r.plan_code === planFilter) : s)
        setPlans(p)
      })
      .catch((e) => setErr(errorText(e, 'Could not load subscriptions.')))
      .finally(() => { setBusy(false); setApplying(false) })
  }
  useEffect(load, [])

  if (busy) return <Busy />

  const mrr = rows.reduce((t, r) => t + Number(r.period_total ?? 0), 0)
  const count = (s: string) => rows.filter((r) => r.status === s).length
  const unplanned = rows.filter((r) => !r.subscription_id).length

  return (
    <Page
      eyebrow="Billing"
      crumbs={[{ label: 'Overview', to: '/platform' },
        { label: 'Subscriptions' }]}
      title="Subscriptions"
      subtitle="Manage software subscriptions, trials, renewals and grace periods."
      actions={
        <Button tone="primary" className="px-5 py-2.5"
          onClick={() => navigate('/platform/plans')}>
          Manage plans
        </Button>
      }>
      <ErrorNote>{err}</ErrorNote>

      <Metrics items={[
        { label: 'Contracted MRR', value: money(mrr),
          caption: `Before tax · ${count('active')} paid` },
        { label: 'Paid subscriptions', value: count('active'),
          caption: 'Billing normally' },
        { label: 'Trials', value: count('trialing'),
          caption: 'Not yet invoiced' },
        { label: 'Past due / no plan', value: count('past_due') + unplanned,
          tone: (count('past_due') + unplanned) ? 'warn' : 'default',
          caption: 'Need a decision' },
      ]} />

      <FilterBar onApply={() => load()} applying={applying}>
        <Select id="s-status" label="Subscription status" value={status}
          onChange={setStatus} options={[
            { value: '', label: 'All statuses' },
            { value: 'active', label: 'Active' },
            { value: 'trialing', label: 'Trial' },
            { value: 'past_due', label: 'Past due' },
            { value: 'grace', label: 'Grace' },
          ]} />
        <Select id="s-plan" label="Plan" value={planFilter}
          onChange={setPlanFilter} options={[
            { value: '', label: 'All plans' },
            ...plans.map((p) => ({ value: p.code, label: p.name })),
          ]} />
      </FilterBar>

      <DataTable
        title="Subscriptions"
        count={rows.length}
        head={['Tenant', 'Plan', 'Billing cycle', 'Monthly recurring',
          'Next event', 'Status', '']}
        footnote={`Showing ${rows.length} of ${rows.length} records`}
        action={
          <Link to="/platform/invoices" className="text-pf-deep hover:underline">
            View record details
          </Link>
        }
        empty="No subscriptions match those filters.">
        {rows.map((r) => (
          <tr key={r.organization_id} className="group hover:bg-pf-bg">
            <Td className="text-pf-navy">
              <Link to={`/platform/tenants/${r.organization_id}`}
                className="hover:text-pf-deep hover:underline">
                {r.organization_name}
              </Link>
            </Td>
            <Td>{r.plan_name || (
              <span className="text-pf-warn-text">not subscribed</span>)}</Td>
            <Td className="text-pf-muted">{r.billing_cycle || '—'}</Td>
            <Td className="tabular-nums">
              {r.period_total ? money(r.period_total) : '—'}
            </Td>
            <Td className="text-pf-muted">
              {/* Date first, then what happens on it: the column is scanned
                  for *when*, and a trial ending and a period renewing are
                  different events the operator is deciding between. */}
              {nextEvent(r)}
            </Td>
            <Td>{r.status ? <Pill value={r.status} /> : (
              <span className="text-pf-help text-pf-muted">no plan</span>)}</Td>
            <Td className="text-right">
              <div className="flex items-center justify-end gap-1">
                <Button onClick={() => setChanging(r)}
                  className="opacity-0 transition group-hover:opacity-100">
                  {r.plan_code ? 'Change plan' : 'Subscribe'}
                </Button>
                <button onClick={() =>
                  navigate('/platform/tenants/' + r.organization_id)}
                  title={'Open ' + r.organization_name}
                  className="rounded p-1 text-pf-placeholder hover:bg-pf-soft hover:text-pf-deep">
                  <ArrowUpRight size={15} />
                </button>
              </div>
            </Td>
          </tr>
        ))}
      </DataTable>

      <Note>
        Plan changes preview their effective date, usage limits and billing
        effect before they are applied. A downgrade that would strand data is
        refused rather than warned about.
      </Note>

      {changing && plans.length > 0 && (
        <ChangePlan row={changing} plans={plans}
          onClose={() => setChanging(null)}
          onDone={() => { setChanging(null); load() }} />
      )}
    </Page>
  )
}
