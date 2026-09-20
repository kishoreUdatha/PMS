import { useEffect, useState } from 'react'
import Select from '../components/Select'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useParams } from 'react-router-dom'
import {
  AlertCircle, AlertTriangle, ArrowLeft, BarChart3, Copy, Loader2, Pause,
  Power, Save, Send,
} from 'lucide-react'
import SimulateRateDialog from '../components/SimulateRateDialog'
import DateField from '../components/DateField'
import { fmtDate } from '../lib/dates'
import {
  getRateRule, createRateRule, updateRateRule, setRateRuleStatus,
  duplicateRateRule, listRuleConflicts, listManagedRoomTypes, listRatePlans,
  RULE_STATUSES, RULE_TYPES, WEEKDAYS,
  type RateRule,
} from '../api'
import { useActivePropertyId } from '../hooks/useProperty'

/**
 * Screen 119 — creating and editing one rate rule.
 *
 * A page of its own rather than a drawer beside the list: seven tabs of
 * configuration needs the width, and a rule being written is a task in its own
 * right, not a preview of a row.
 */


const TABS = ['Rule Details', 'Date & Stay', 'Room & Rate Plan', 'Pricing',
  'Restrictions', 'Preview & Conflicts', 'Audit'] as const
type Tab = typeof TABS[number]

const input = 'w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700'

const day = (s: string | null) => {
  if (!s) return '—'
  const d = new Date(`${s.slice(0, 10)}T00:00:00`)
  return fmtDate(d)
}
const today = () => new Date().toISOString().slice(0, 10)

const STATUS_STYLE: Record<string, string> = {
  published: 'bg-emerald-100 text-emerald-700',
  scheduled: 'bg-sky-100 text-sky-700',
  draft: 'bg-slate-200 text-slate-600',
  paused: 'bg-amber-100 text-amber-800',
  inactive: 'bg-slate-100 text-slate-500',
}

function apiError(e: unknown): string {
  const d = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  if (typeof d === 'string') return d
  if (Array.isArray(d)) return 'Some fields are invalid. Check the highlighted values.'
  return 'Could not save. Please try again.'
}

function Field({ label, required, hint, children }: {
  label: string; required?: boolean; hint?: string; children: React.ReactNode
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-sm font-medium text-slate-700">
        {label}{required && <span className="text-red-500"> *</span>}
      </span>
      {children}
      {hint && <span className="mt-1 block text-xs text-slate-400">{hint}</span>}
    </label>
  )
}

interface FormState {
  name: string; description: string; rule_type: string; status: string
  priority: number; applicable_for: string
  date_from: string; date_to: string; weekdays: number[]
  adjustment_direction: string; adjustment_type: string; adjustment_value: string
  fixed_rate: string
  min_stay: string; max_stay: string
  advance_days_min: string; advance_days_max: string
  occupancy_min: string; occupancy_max: string
  closed_to_arrival: boolean; closed_to_departure: boolean; stop_sell: boolean
  room_type_ids: string[]; rate_plan_ids: string[]
}

const EMPTY: FormState = {
  name: '', description: '', rule_type: 'derived_adjustment', status: 'draft',
  priority: 10, applicable_for: 'all_rate_plans',
  date_from: today(), date_to: today(), weekdays: [],
  adjustment_direction: 'decrease', adjustment_type: 'percent',
  adjustment_value: '10', fixed_rate: '',
  min_stay: '', max_stay: '', advance_days_min: '', advance_days_max: '',
  occupancy_min: '', occupancy_max: '',
  closed_to_arrival: false, closed_to_departure: false, stop_sell: false,
  room_type_ids: [], rate_plan_ids: [],
}

const numOrNull = (v: string) => (v === '' ? null : Number(v))

function fromRule(r: RateRule): FormState {
  return {
    name: r.name, description: r.description ?? '', rule_type: r.rule_type,
    status: r.status, priority: r.priority, applicable_for: r.applicable_for,
    date_from: r.date_from, date_to: r.date_to, weekdays: r.weekdays,
    adjustment_direction: r.adjustment_direction,
    adjustment_type: r.adjustment_type,
    adjustment_value: String(Number(r.adjustment_value)),
    fixed_rate: r.fixed_rate !== null ? String(Number(r.fixed_rate)) : '',
    min_stay: r.min_stay?.toString() ?? '', max_stay: r.max_stay?.toString() ?? '',
    advance_days_min: r.advance_days_min?.toString() ?? '',
    advance_days_max: r.advance_days_max?.toString() ?? '',
    occupancy_min: r.occupancy_min?.toString() ?? '',
    occupancy_max: r.occupancy_max?.toString() ?? '',
    closed_to_arrival: r.closed_to_arrival,
    closed_to_departure: r.closed_to_departure, stop_sell: r.stop_sell,
    room_type_ids: r.room_type_ids, rate_plan_ids: r.rate_plan_ids,
  }
}

export default function RateRuleEditor() {
  const { ruleId } = useParams()
  const creating = !ruleId
  const propertyId = useActivePropertyId()
  const navigate = useNavigate()
  const qc = useQueryClient()

  const [tab, setTab] = useState<Tab>('Rule Details')
  const [form, setForm] = useState<FormState>(EMPTY)
  const [error, setError] = useState<string | null>(null)
  const [simulating, setSimulating] = useState(false)

  const ruleQ = useQuery({
    queryKey: ['rateRule', propertyId, ruleId],
    queryFn: () => getRateRule(propertyId, ruleId!),
    enabled: propertyId !== '' && !!ruleId,
  })
  const conflictQ = useQuery({
    queryKey: ['ruleConflicts', propertyId],
    queryFn: () => listRuleConflicts(propertyId), enabled: propertyId !== '',
  })
  const typesQ = useQuery({
    queryKey: ['managedRoomTypes', propertyId],
    queryFn: () => listManagedRoomTypes(propertyId), enabled: propertyId !== '',
  })
  const plansQ = useQuery({
    queryKey: ['ratePlans', propertyId, {}],
    queryFn: () => listRatePlans(propertyId), enabled: propertyId !== '',
  })

  const rule = ruleQ.data ?? null
  useEffect(() => { if (rule) setForm(fromRule(rule)) }, [rule])

  const roomTypes = (typesQ.data ?? []).map((t) => ({ id: t.id, name: t.name }))
  const ratePlans = (plansQ.data?.items ?? []).map(
    (p) => ({ id: p.id, code: p.code, name: p.name }))
  const mine = rule
    ? (conflictQ.data ?? []).filter(
        (c) => c.rule_id === rule.id || c.other_id === rule.id)
    : []

  const back = () => navigate('/rates/rules')
  const done = () => {
    qc.invalidateQueries({ queryKey: ['rateRules', propertyId] })
    qc.invalidateQueries({ queryKey: ['ruleConflicts', propertyId] })
    qc.invalidateQueries({ queryKey: ['rateRule', propertyId] })
    back()
  }

  const payload = () => ({
    name: form.name.trim(), description: form.description.trim() || null,
    rule_type: form.rule_type, status: form.status, priority: form.priority,
    // Derived, not chosen: ticking plans narrows the rule, ticking none
    // leaves it applying to all — the same convention as room types.
    applicable_for: form.rate_plan_ids.length > 0
      ? 'specific_rate_plans' : 'all_rate_plans',
    date_from: form.date_from, date_to: form.date_to, weekdays: form.weekdays,
    adjustment_direction: form.adjustment_direction,
    adjustment_type: form.adjustment_type,
    adjustment_value: form.adjustment_value === '' ? 0 : Number(form.adjustment_value),
    // The API rejects a fixed rate on an adjustment rule and vice versa.
    fixed_rate: form.rule_type === 'fixed_rate' ? numOrNull(form.fixed_rate) : null,
    min_stay: numOrNull(form.min_stay), max_stay: numOrNull(form.max_stay),
    advance_days_min: numOrNull(form.advance_days_min),
    advance_days_max: numOrNull(form.advance_days_max),
    occupancy_min: numOrNull(form.occupancy_min),
    occupancy_max: numOrNull(form.occupancy_max),
    closed_to_arrival: form.closed_to_arrival,
    closed_to_departure: form.closed_to_departure, stop_sell: form.stop_sell,
    channel_scope: 'all',
    room_type_ids: form.room_type_ids,
    rate_plan_ids: form.rate_plan_ids,
  })

  const save = useMutation({
    mutationFn: (status?: string) => {
      const body = { ...payload(), ...(status ? { status } : {}) }
      return rule
        ? updateRateRule(propertyId, rule.id, { ...body, version: rule.version })
        : createRateRule(propertyId, body)
    },
    onSuccess: done,
    onError: (e) => setError(apiError(e)),
  })
  const changeStatus = useMutation({
    mutationFn: (status: string) => setRateRuleStatus(propertyId, rule!.id, {
      status, version: rule!.version,
    }),
    onSuccess: done,
    onError: (e) => setError(apiError(e)),
  })
  const duplicate = useMutation({
    mutationFn: () => duplicateRateRule(propertyId, rule!.id),
    onSuccess: done,
    onError: (e) => setError(apiError(e)),
  })

  const busy = save.isPending || changeStatus.isPending || duplicate.isPending
  const set = <K extends keyof FormState>(k: K, v: FormState[K]) =>
    setForm((f) => ({ ...f, [k]: v }))

  if (ruleId && ruleQ.isLoading) {
    return (
      <div className="grid h-64 place-items-center text-slate-400">
        <Loader2 className="h-6 w-6 animate-spin" />
      </div>
    )
  }
  if (ruleId && ruleQ.isError) {
    return (
      <div className="space-y-4">
        <button onClick={back}
          className="inline-flex items-center gap-2 text-sm font-semibold text-brand hover:underline">
          <ArrowLeft className="h-4 w-4" /> Back to Rate Rules
        </button>
        <p className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">
          This rule could not be loaded. It may have been removed.
        </p>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <button onClick={back}
        className="inline-flex items-center gap-2 text-sm font-semibold text-brand hover:underline">
        <ArrowLeft className="h-4 w-4" /> Back to Rate Rules
      </button>

      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm text-slate-400">
            <button onClick={back} className="text-brand hover:underline">Rate Rules</button>
            <span className="px-2">›</span>
            <span className="font-semibold text-slate-700">
              {creating ? 'New Rule' : rule?.name}
            </span>
          </p>
          <div className="mt-1 flex flex-wrap items-center gap-3">
            <h1 className="text-display text-ink">
              {creating ? 'Create Rate Rule' : rule?.name}
            </h1>
            {rule && (
              <span className={`rounded-full px-3 py-1 text-xs font-semibold ${
                STATUS_STYLE[rule.status] ?? 'bg-slate-75 text-slate-600'}`}>
                {rule.status_label}
              </span>
            )}
          </div>
          <p className="text-slate-500">
            {creating
              ? 'Define how this rule adjusts rates, and when it applies'
              : rule?.description || 'Update how this rule adjusts rates'}
          </p>
        </div>
        <button onClick={() => setSimulating(true)} disabled={roomTypes.length === 0}
          className="flex items-center gap-2 rounded-lg border border-brand px-4 py-2.5 text-sm font-semibold text-brand hover:bg-brand-light disabled:opacity-50">
          <BarChart3 className="h-4 w-4" /> Simulate Rate
        </button>
      </div>

      <div className="rounded-xl border border-slate-200 bg-white">
        <div className="flex gap-1 overflow-x-auto border-b border-slate-200 px-3">
          {TABS.map((t) => (
            <button key={t} onClick={() => setTab(t)}
              className={`whitespace-nowrap border-b-2 px-4 py-3 text-sm font-semibold transition ${
                tab === t ? 'border-brand text-brand'
                  : 'border-transparent text-slate-500 hover:text-slate-700'
              }`}>
              {t}
              {t === 'Preview & Conflicts' && mine.length > 0 && (
                <span className="ml-1.5 rounded-full bg-amber-100 px-1.5 text-xs text-caution">
                  {mine.length}
                </span>
              )}
            </button>
          ))}
        </div>

        <div className="space-y-5 p-6">
          {tab === 'Rule Details' && (
            <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
              <div className="space-y-5">
                <Field label="Rule Name" required>
                  <input className={input} value={form.name} maxLength={100}
                    onChange={(e) => set('name', e.target.value)} />
                  <span className="mt-1 block text-right text-xs text-slate-400">
                    {form.name.length}/100
                  </span>
                </Field>
                <Field label="Description">
                  <textarea className={`${input} h-24`} value={form.description}
                    maxLength={500}
                    onChange={(e) => set('description', e.target.value)} />
                  <span className="mt-1 block text-right text-xs text-slate-400">
                    {form.description.length}/500
                  </span>
                </Field>
                <Field label="Status">
                  <Select className={input} value={form.status}
                    onChange={(e) => set('status', e.target.value)}>
                    {RULE_STATUSES.map((s) => (
                      <option key={s.code} value={s.code}>{s.label}</option>
                    ))}
                  </Select>
                </Field>
                <Field label="Priority" required hint="Lower number = higher priority">
                  <input type="number" min={0} className={input} value={form.priority}
                    onChange={(e) => set('priority', Number(e.target.value))} />
                </Field>
              </div>
              <div className="space-y-5">
                <Field label="Rule Type" hint="How this rule changes the rate">
                  <Select className={input} value={form.rule_type}
                    onChange={(e) => set('rule_type', e.target.value)}>
                    {RULE_TYPES.map((t) => (
                      <option key={t.code} value={t.code}>{t.label}</option>
                    ))}
                  </Select>
                </Field>
                <Field label="Channels"
                  hint="Channel scope needs Distribution (screens 024 / 120), which is not built — every rule applies to all booking sources.">
                  <Select className={`${input} disabled:bg-slate-50`} value="all" disabled>
                    <option value="all">All Channels</option>
                  </Select>
                </Field>
              </div>
            </div>
          )}

          {tab === 'Date & Stay' && (
            <div className="max-w-3xl space-y-5">
              <div className="grid grid-cols-2 gap-4">
                <Field label="Stay from" required>
                  <DateField value={form.date_from} onChange={(v) => set('date_from', v)} className={input} />
                </Field>
                <Field label="Stay to" required>
                  <DateField value={form.date_to} onChange={(v) => set('date_to', v)} className={input} />
                </Field>
              </div>
              <div>
                <span className="mb-1 block text-sm font-medium text-slate-700">Days</span>
                <div className="flex flex-wrap gap-2">
                  {WEEKDAYS.map((d) => {
                    const on = form.weekdays.includes(d.n)
                    return (
                      <button key={d.n}
                        // Derived from the previous state, not the closure's
                        // copy: two quick clicks would otherwise both read the
                        // same value and the second would undo the first.
                        onClick={() => setForm((f) => ({
                          ...f,
                          weekdays: f.weekdays.includes(d.n)
                            ? f.weekdays.filter((x) => x !== d.n)
                            : [...f.weekdays, d.n].sort((a, b) => a - b),
                        }))}
                        className={`w-16 rounded-lg border px-2 py-2.5 text-sm font-semibold transition ${
                          on ? 'border-brand bg-brand text-white'
                            : 'border-slate-200 text-slate-600 hover:bg-slate-50'
                        }`}>
                        {d.label}
                      </button>
                    )
                  })}
                </div>
                <p className="mt-1 text-xs text-slate-400">
                  Select none to apply on every day of the week.
                </p>
              </div>
              <div className="grid grid-cols-2 gap-4">
                <Field label="Minimum stay (nights)">
                  <input type="number" min={1} className={input} value={form.min_stay}
                    placeholder="Any" onChange={(e) => set('min_stay', e.target.value)} />
                </Field>
                <Field label="Maximum stay (nights)">
                  <input type="number" min={1} className={input} value={form.max_stay}
                    placeholder="Any" onChange={(e) => set('max_stay', e.target.value)} />
                </Field>
              </div>
            </div>
          )}

          {tab === 'Room & Rate Plan' && (
            <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
              <div>
                <span className="mb-1 block text-sm font-medium text-slate-700">
                  Room Types
                </span>
                <p className="mb-2 text-xs text-slate-400">
                  Select none to apply to every room type.
                </p>
                <div className="space-y-1 rounded-lg border border-slate-200 p-4">
                  {roomTypes.map((t) => (
                    <label key={t.id}
                      className="flex items-center gap-2 py-0.5 text-sm text-slate-700">
                      <input type="checkbox" checked={form.room_type_ids.includes(t.id)}
                        onChange={(e) => setForm((f) => ({
                          ...f,
                          room_type_ids: e.target.checked
                            ? [...f.room_type_ids, t.id]
                            : f.room_type_ids.filter((x) => x !== t.id),
                        }))}
                        className="h-4 w-4 rounded border-slate-300 text-brand" />
                      {t.name}
                    </label>
                  ))}
                </div>
              </div>
              <div>
                <span className="mb-1 block text-sm font-medium text-slate-700">
                  Rate Plans
                </span>
                {/* Same rule as Room Types beside it: ticking none means all.
                    A separate mode switch to unlock the list was one control
                    too many, and made two identical boxes behave differently. */}
                <p className="mb-2 text-xs text-slate-400">
                  Select none to apply to every rate plan.
                </p>
                <div className="space-y-1 rounded-lg border border-slate-200 p-4">
                  {ratePlans.map((p) => (
                    <label key={p.id}
                      className="flex items-center gap-2 py-0.5 text-sm text-slate-700">
                      <input type="checkbox"
                        checked={form.rate_plan_ids.includes(p.id)}
                        onChange={(e) => setForm((f) => ({
                          ...f,
                          rate_plan_ids: e.target.checked
                            ? [...f.rate_plan_ids, p.id]
                            : f.rate_plan_ids.filter((x) => x !== p.id),
                        }))}
                        className="h-4 w-4 rounded border-slate-300 text-brand" />
                      {p.name} <span className="text-slate-400">({p.code})</span>
                    </label>
                  ))}
                </div>
              </div>
            </div>
          )}

          {tab === 'Pricing' && (
            <div className="max-w-2xl">
              {form.rule_type === 'fixed_rate' ? (
                <Field label="Fixed nightly rate (₹)" required
                  hint="Replaces whatever the rate would otherwise be for these nights.">
                  <input type="number" min={0} step="0.01" className={input}
                    value={form.fixed_rate}
                    onChange={(e) => set('fixed_rate', e.target.value)} />
                </Field>
              ) : (
                <div>
                  <span className="mb-1 block text-sm font-medium text-slate-700">
                    Rate Adjustment
                  </span>
                  <div className="flex gap-2">
                    <Select className={`${input} w-44`} value={form.adjustment_direction}
                      onChange={(e) => set('adjustment_direction', e.target.value)}>
                      <option value="decrease">Decrease by</option>
                      <option value="increase">Increase by</option>
                    </Select>
                    <input type="number" min={0} step="0.01" className={input}
                      value={form.adjustment_value}
                      onChange={(e) => set('adjustment_value', e.target.value)} />
                    <div className="flex shrink-0 overflow-hidden rounded-lg border border-slate-200">
                      {(['percent', 'amount'] as const).map((t) => (
                        <button key={t} onClick={() => set('adjustment_type', t)}
                          className={`px-5 py-2 text-sm font-semibold transition ${
                            form.adjustment_type === t
                              ? 'bg-brand text-white'
                              : 'bg-white text-slate-500 hover:bg-slate-50'
                          }`}>
                          {t === 'percent' ? '%' : 'INR'}
                        </button>
                      ))}
                    </div>
                  </div>
                  <p className="mt-1 text-xs text-slate-400">
                    Applied after the rate plan's own pricing, in priority order.
                  </p>
                </div>
              )}
            </div>
          )}

          {tab === 'Restrictions' && (
            <div className="max-w-3xl space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <Field label="Book at least N days ahead">
                  <input type="number" min={0} className={input}
                    value={form.advance_days_min} placeholder="Any"
                    onChange={(e) => set('advance_days_min', e.target.value)} />
                </Field>
                <Field label="Book within N days of arrival">
                  <input type="number" min={0} className={input}
                    value={form.advance_days_max} placeholder="Any"
                    onChange={(e) => set('advance_days_max', e.target.value)} />
                </Field>
              </div>
              {/* Occupancy sits with the other conditions, because that is
                  what it is: one more reason a rule declines to apply. The
                  two ends are the two halves of yield — charge more as a date
                  fills, charge less while it is empty — and the second is the
                  one people forget to build. */}
              <div className="rounded-xl border border-slate-200 p-4">
                <h3 className="text-sm font-semibold text-slate-800">
                  Only when the date is this full
                </h3>
                <p className="mb-3 mt-0.5 text-xs text-slate-500">
                  Percent of rooms already sold, counting only rooms you can
                  actually sell. Leave both blank to price regardless of
                  demand. These dates are re-priced on their own as rooms sell.
                </p>
                <div className="grid grid-cols-2 gap-4">
                  <Field label="At least % sold">
                    <input type="number" min={0} max={100} className={input}
                      value={form.occupancy_min} placeholder="Any"
                      onChange={(e) => set('occupancy_min', e.target.value)} />
                  </Field>
                  <Field label="At most % sold">
                    <input type="number" min={0} max={100} className={input}
                      value={form.occupancy_max} placeholder="Any"
                      onChange={(e) => set('occupancy_max', e.target.value)} />
                  </Field>
                </div>
                <p className="mt-2 text-xs text-slate-400">
                  e.g. <span className="font-medium">at least 80</span> with a
                  20% increase raises the rate once a date is nearly full;{' '}
                  <span className="font-medium">at most 25</span> with a
                  decrease discounts a date that is not selling.
                </p>
              </div>

              {([['closed_to_arrival', 'Closed to arrival — no check-ins on these dates'],
                 ['closed_to_departure', 'Closed to departure — no check-outs on these dates'],
                 ['stop_sell', 'Stop sell — do not sell these dates at all']] as const)
                .map(([key, label]) => (
                  <label key={key} className="flex items-center gap-2 text-sm text-slate-700">
                    <input type="checkbox" checked={form[key]}
                      onChange={(e) => set(key, e.target.checked)}
                      className="h-4 w-4 rounded border-slate-300 text-brand" />
                    {label}
                  </label>
                ))}
            </div>
          )}

          {tab === 'Preview & Conflicts' && (
            creating ? (
              <p className="rounded-lg bg-slate-50 px-4 py-3 text-sm text-slate-600">
                Conflicts are checked against published rules once this one is saved.
              </p>
            ) : mine.length === 0 ? (
              <p className="rounded-lg bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
                No conflicts. No other published rule shares this one's priority
                over the same dates, room types and rate plans.
              </p>
            ) : (
              <div className="space-y-3">
                {mine.map((c, i) => (
                  <div key={i}
                    className="flex items-start gap-3 rounded-lg bg-amber-50 px-4 py-3 text-sm text-amber-900">
                    <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                    <div>
                      <p className="font-semibold">{c.rule_name} vs {c.other_name}</p>
                      <p className="mt-0.5">{c.detail}</p>
                    </div>
                  </div>
                ))}
              </div>
            )
          )}

          {tab === 'Audit' && (
            rule ? (
              <div className="max-w-2xl space-y-2 text-sm text-slate-600">
                <p>Created {day(rule.created_at)}</p>
                <p>
                  Last updated {day(rule.updated_at)}
                  {rule.updated_by_name && ` by ${rule.updated_by_name}`}
                </p>
                <p>Revision {rule.version}</p>
                <p className="mt-3 rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-500">
                  Every change to a rule is written to the audit log
                  (Administration › Audit Log) with the actor, timestamp and what
                  changed.
                </p>
              </div>
            ) : (
              <p className="text-sm text-slate-500">
                Audit history appears once the rule has been saved.
              </p>
            )
          )}

          {error && (
            <p className="flex items-start gap-2 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" /> {error}
            </p>
          )}
        </div>

        <div className="grid grid-cols-2 gap-x-4 gap-y-2 border-t border-slate-100 bg-slate-50/60 px-6 py-3 text-xs sm:grid-cols-5">
          {[
            ['Stay dates', `${day(form.date_from)} – ${day(form.date_to)}`],
            ['Days', form.weekdays.length === 0 ? 'All days'
              : form.weekdays.map((n) => WEEKDAYS[n - 1].label).join(', ')],
            ['Applies to', form.room_type_ids.length === 0 ? 'All room types'
              : `${form.room_type_ids.length} room type(s)`],
            ['Adjustment', form.rule_type === 'fixed_rate'
              ? `Set to ₹${form.fixed_rate || 0}`
              : `${form.adjustment_direction === 'decrease' ? '-' : '+'}`
                + `${form.adjustment_value || 0}`
                + `${form.adjustment_type === 'percent' ? '%' : ' INR'}`],
            ['Rate plans', form.rate_plan_ids.length === 0 ? 'All rate plans'
              : `${form.rate_plan_ids.length} plan(s)`],
          ].map(([label, value]) => (
            <div key={label}>
              <p className="text-slate-500">{label}</p>
              <p className="font-semibold text-slate-700">{value}</p>
            </div>
          ))}
        </div>

        <div className="flex flex-wrap items-center justify-end gap-2 border-t border-slate-100 px-6 py-4">
          <button onClick={back}
            className="rounded-lg border border-slate-200 px-4 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50">
            Cancel
          </button>
          <button onClick={() => { setError(null); save.mutate('draft') }} disabled={busy}
            className="flex items-center gap-2 rounded-lg border border-slate-200 px-4 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-50">
            <Save className="h-4 w-4" /> Save Draft
          </button>
          {rule && (
            <>
              <button onClick={() => changeStatus.mutate('paused')} disabled={busy}
                className="flex items-center gap-2 rounded-lg border border-amber-300 px-4 py-2 text-sm font-semibold text-caution hover:bg-amber-50 disabled:opacity-50">
                <Pause className="h-4 w-4" /> Pause
              </button>
              <button onClick={() => changeStatus.mutate('inactive')} disabled={busy}
                className="flex items-center gap-2 rounded-lg border border-red-200 px-4 py-2 text-sm font-semibold text-red-600 hover:bg-red-50 disabled:opacity-50">
                <Power className="h-4 w-4" /> Deactivate
              </button>
              <button onClick={() => duplicate.mutate()} disabled={busy}
                className="flex items-center gap-2 rounded-lg border border-slate-200 px-4 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-50">
                <Copy className="h-4 w-4" /> Duplicate
              </button>
            </>
          )}
          <button onClick={() => { setError(null); save.mutate('published') }}
            disabled={busy}
            className="flex items-center gap-2 rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white hover:bg-brand-dark disabled:opacity-50">
            {busy ? <Loader2 className="h-4 w-4 animate-spin" />
              : <Send className="h-4 w-4" />}
            Publish
          </button>
        </div>
      </div>

      {simulating && (
        <SimulateRateDialog propertyId={propertyId} roomTypes={roomTypes}
          ratePlans={ratePlans} onClose={() => setSimulating(false)} />
      )}
    </div>
  )
}
