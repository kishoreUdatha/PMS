import { useEffect, useState } from 'react'
import { AlertTriangle, Check, Loader2, Receipt } from 'lucide-react'
import { api } from '../api'
import { errorText } from '../lib/forms'

/** A tenant's own subscription — the customer side of the SaaS product.
 *
 *  Deliberately not the platform console in miniature. A tenant sees what they
 *  are on, what it costs, how their usage sits against their limits, and their
 *  own invoices. They cannot change what they owe: the API refuses it and so
 *  does row-level security, so nothing here pretends otherwise.
 */

interface PlanLimits { [metric: string]: number | null }

interface Plan {
  code: string; name: string; summary: string
  amount: string; billing_cycle: string; trial_days: number
  version_no: number; limits: PlanLimits; modules: string[]
}

interface Subscription {
  plan_code: string; plan_name: string; status: string
  amount: string; quantity: number; billing_cycle: string
  trial_ends_on: string | null; current_period_end: string | null
  period_total: string; version_no: number
}

interface Invoice {
  id: string; series: string; number: number; status: string
  period_start: string | null; period_end: string | null
  total: string; amount_paid: string; issued_at: string | null
}

const money = (v: string | number | null | undefined) =>
  Number(v ?? 0).toLocaleString('en-IN', {
    style: 'currency', currency: 'INR', minimumFractionDigits: 2,
  })

export default function Billing() {
  const [plans, setPlans] = useState<Plan[]>([])
  const [sub, setSub] = useState<Subscription | null>(null)
  const [usage, setUsage] = useState<Record<string, number>>({})
  const [limits, setLimits] = useState<PlanLimits>({})
  const [invoices, setInvoices] = useState<Invoice[]>([])
  const [busy, setBusy] = useState(true)
  const [saving, setSaving] = useState('')
  const [err, setErr] = useState('')

  function load() {
    setBusy(true)
    Promise.all([
      api.get<Plan[]>('/iam/billing/plans'),
      api.get('/iam/billing/subscription'),
      api.get<Invoice[]>('/iam/billing/invoices'),
    ])
      .then(([p, s, i]) => {
        setPlans(p.data)
        setSub(s.data.subscription)
        setUsage(s.data.usage || {})
        setLimits(s.data.limits || {})
        setInvoices(i.data)
      })
      .catch((e) => setErr(errorText(e, 'Could not load billing.')))
      .finally(() => setBusy(false))
  }
  useEffect(load, [])

  async function subscribe(code: string) {
    setErr(''); setSaving(code)
    try {
      await api.post('/iam/billing/subscribe', { plan_code: code })
      load()
    } catch (e) {
      setErr(errorText(e, 'That plan could not be selected.'))
    } finally { setSaving('') }
  }

  if (busy) {
    return (
      <div className="flex items-center justify-center py-20 text-slate-300">
        <Loader2 className="animate-spin" />
      </div>
    )
  }

  return (
    // No padding of its own: this screen renders inside AppLayout, which
    // already pads. It was the only page that padded twice, so it sat 40px
    // from the sidebar while every other screen sat at 16.
    <div>
      <h1 className="text-section text-ink">Subscription</h1>
      <p className="mt-0.5 text-sm text-slate-400">
        Your plan for the property management software.
      </p>

      {err && (
        <div className="mt-4 flex items-start gap-2 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">
          <AlertTriangle size={15} className="mt-0.5 shrink-0" /> {err}
        </div>
      )}

      {sub ? (
        <div className="mt-5 rounded-xl border border-slate-100 bg-white p-5">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <div className="flex items-center gap-2">
                <span className="text-lg font-semibold text-ink">
                  {sub.plan_name}
                </span>
                <span className="rounded-full bg-brand-light px-2 py-0.5 text-xs font-medium text-brand-dark">
                  {sub.status}
                </span>
              </div>
              <p className="mt-1 text-sm text-slate-500">
                {money(sub.amount)} per tenant, billed {sub.billing_cycle}.
                Your plan's property, room and user counts are limits, shown
                below.
              </p>
              {sub.trial_ends_on && (
                <p className="mt-1 text-sm text-caution">
                  Trial ends {sub.trial_ends_on}
                </p>
              )}
            </div>
            <div className="text-right">
              <div className="text-xs uppercase tracking-wide text-slate-500">
                Per {sub.billing_cycle === 'annual' ? 'year' : 'month'}
              </div>
              <div className="text-2xl font-semibold text-ink">
                {money(sub.period_total)}
              </div>
            </div>
          </div>

          {Object.keys(limits).length > 0 && (
            <div className="mt-4 border-t border-slate-100 pt-4">
              <div className="mb-2 text-xs font-medium text-slate-600">
                Your usage
              </div>
              <div className="grid gap-3 sm:grid-cols-3">
                {Object.entries(limits).map(([metric, limit]) => {
                  const have = usage[metric] ?? 0
                  const pct = limit ? Math.min(100, (have / limit) * 100) : 0
                  const over = limit !== null && have > limit
                  return (
                    <div key={metric}>
                      <div className="flex justify-between text-xs">
                        <span className="text-slate-500">
                          {metric.replace('_', ' ')}
                        </span>
                        <span className={over ? 'font-medium text-red-700' : 'text-slate-600'}>
                          {have} / {limit === null ? '∞' : limit}
                        </span>
                      </div>
                      <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-slate-100">
                        <div
                          className={`h-full rounded-full ${
                            over ? 'bg-red-600' : 'bg-brand'}`}
                          style={{ width: `${limit === null ? 4 : pct}%` }} />
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          <p className="mt-4 border-t border-slate-100 pt-3 text-xs text-slate-400">
            To change plan, contact support. Your invoices and what you owe are
            set by us and cannot be edited here.
          </p>
        </div>
      ) : (
        <div className="mt-5 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-caution">
          You are not on a plan yet. Choose one below to get started.
        </div>
      )}

      <h2 className="mt-8 text-sm font-medium text-slate-600">
        {sub ? 'Other plans' : 'Choose a plan'}
      </h2>
      <div className="mt-2 grid gap-3 md:grid-cols-3">
        {plans.map((p) => {
          const current = sub?.plan_code === p.code
          return (
            <div key={p.code}
              className={`flex flex-col rounded-xl border bg-white p-4 ${
                current ? 'border-brand ring-1 ring-brand/20' : 'border-slate-100'
              }`}>
              <div className="flex items-baseline justify-between">
                <span className="font-medium text-ink">{p.name}</span>
                {current && (
                  <span className="text-xs font-medium text-brand">current</span>
                )}
              </div>
              <p className="mt-1 text-sm text-slate-500">{p.summary}</p>
              <div className="mt-3 flex items-baseline gap-1.5">
                <span className="text-2xl font-semibold text-ink">
                  {money(p.amount)}
                </span>
                <span className="text-xs text-slate-400">
                  per tenant / {p.billing_cycle}
                </span>
              </div>
              {p.trial_days > 0 && !sub && (
                <div className="mt-1 text-xs text-positive">
                  {p.trial_days}-day free trial
                </div>
              )}
              <div className="mt-3 flex-1 space-y-1 border-t border-slate-100 pt-3">
                {Object.entries(p.limits).map(([m, l]) => (
                  <div key={m} className="flex justify-between text-xs">
                    <span className="text-slate-500">{m.replace('_', ' ')}</span>
                    <span className="text-slate-700">
                      {l === null ? 'unlimited' : l}
                    </span>
                  </div>
                ))}
              </div>
              <button
                disabled={current || saving !== ''}
                onClick={() => subscribe(p.code)}
                className={`mt-4 flex w-full items-center justify-center gap-1.5 rounded-lg px-3 py-2 text-sm font-medium transition disabled:opacity-50 ${
                  current
                    ? 'border border-slate-200 text-slate-400'
                    : 'bg-brand text-white hover:bg-brand-dark'
                }`}>
                {saving === p.code
                  ? <Loader2 size={14} className="animate-spin" />
                  : current ? <Check size={14} /> : null}
                {current ? 'Your plan' : sub ? 'Switch to this' : 'Choose'}
              </button>
            </div>
          )
        })}
      </div>

      <h2 className="mt-8 text-sm font-medium text-slate-600">Invoices</h2>
      <div className="mt-2 overflow-x-auto rounded-xl border border-slate-100 bg-white">
        {invoices.length === 0 ? (
          <div className="flex flex-col items-center gap-2 px-6 py-10 text-center">
            <Receipt size={20} className="text-slate-300" />
            <p className="text-sm text-slate-500">
              No invoices yet. These are bills for the software — your guests'
              bills live in the folio screens.
            </p>
          </div>
        ) : (
          <table className="w-full min-w-[520px] text-sm">
            <thead>
              <tr className="border-b border-slate-100 text-left text-xs uppercase tracking-wide text-slate-400">
                <th className="px-4 py-3 font-medium">Invoice</th>
                <th className="px-4 py-3 font-medium">Period</th>
                <th className="px-4 py-3 font-medium">Status</th>
                <th className="px-4 py-3 font-medium">Total</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {invoices.map((i) => (
                <tr key={i.id}>
                  <td className="px-4 py-3 font-mono text-xs text-slate-600">
                    {i.series}-{i.number}
                  </td>
                  <td className="px-4 py-3 text-slate-400">
                    {i.period_start ? `${i.period_start} → ${i.period_end}` : '—'}
                  </td>
                  <td className="px-4 py-3 text-slate-600">{i.status}</td>
                  <td className="px-4 py-3 tabular-nums text-slate-700">
                    {money(i.total)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
