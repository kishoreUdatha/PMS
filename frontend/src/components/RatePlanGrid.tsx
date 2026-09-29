import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertCircle, Info, Layers, Loader2, Plus, Save, Trash2, X } from 'lucide-react'
import Select from './Select'
import DateField from './DateField'
import { fmtDate, fmtDayMonth, fmtWeekday } from '../lib/dates'
import {
  getRatePlanCalendar, setRatePlanCalendar,
  type PlanChange, type PlanNight,
} from '../api'
import { errorText } from '../lib/forms'

/**
 * A rate plan's own calendar: price, minimum and maximum stay, closed to
 * arrival, closed to departure and stop sell, night by night.
 *
 * Each value shown is what the plan actually sells with -- the same
 * calculation the channel manager is sent. A value the plan set for itself is
 * highlighted; the rest come from the room type, the rate rules and the
 * plan's defaults, and "Reset" hands a night back to them.
 *
 * Edits to single nights save as they are made. The range panel is for
 * everything wider (a season, only weekends, several plans with different
 * values): it collects changes and saves them together in one request, so
 * the channel manager receives them as one update.
 */

type Plan = { id: string; name: string }

const FLAGS = [
  ['closed_to_arrival', 'Closed to arrival', 'CTA'],
  ['closed_to_departure', 'Closed to departure', 'CTD'],
  ['stop_sell', 'Stop sell', 'Stop sell'],
] as const
type Flag = typeof FLAGS[number][0]

const LABEL: Record<string, string> = {
  rate: 'Price', min_stay: 'Min stay', max_stay: 'Max stay',
  closed_to_arrival: 'CTA', closed_to_departure: 'CTD', stop_sell: 'Stop sell',
}
const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

const parse = (s: string) => new Date(`${s}T00:00:00`)
const isWeekend = (s: string) => [0, 6].includes(parse(s).getDay())

function apiError(e: unknown): string {
  return errorText(e, 'Could not save the change. Please try again.')
}

/* ----------------------------------------------------------- number cell --- */
function NumberCell({
  value, own, disabled, label, onCommit, decimals = false, blankLabel = '',
}: {
  value: string | number | null
  own: boolean
  disabled: boolean
  label: string
  onCommit: (v: number | null) => void
  decimals?: boolean
  blankLabel?: string
}) {
  const show = (v: string | number | null) =>
    v === null || v === '' ? '' : String(Number(v))
  const [draft, setDraft] = useState(show(value))
  useEffect(() => { setDraft(show(value)) }, [value])

  const commit = () => {
    const raw = draft.trim()
    if (raw === '') { if (own) onCommit(null); else setDraft(show(value)); return }
    const n = Number(raw)
    if (Number.isNaN(n) || n < 0 || (!decimals && !Number.isInteger(n))) {
      setDraft(show(value)); return
    }
    if (value !== null && n === Number(value)) return
    onCommit(n)
  }
  return (
    <input value={draft} disabled={disabled} aria-label={label}
      placeholder={blankLabel}
      inputMode={decimals ? 'decimal' : 'numeric'}
      onChange={(e) => setDraft(e.target.value)} onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === 'Enter') (e.target as HTMLInputElement).blur()
        if (e.key === 'Escape') { setDraft(show(value)); (e.target as HTMLInputElement).blur() }
      }}
      title={own ? 'Set on this plan. Clear the box to reset it.' : 'Inherited'}
      className={`w-full rounded border px-1.5 py-1 text-center text-sm tabular-nums outline-none transition
        focus:border-brand focus:ring-1 focus:ring-brand/40 disabled:opacity-50 ${
        own ? 'border-brand/40 bg-brand-light/40 font-semibold text-slate-800'
          : 'border-transparent bg-transparent text-slate-700 hover:border-slate-200'}`} />
  )
}

/* ------------------------------------------------------------------ grid --- */
export default function RatePlanGrid({
  propertyId, ratePlanId, plans, from, to,
}: {
  propertyId: string
  ratePlanId: string
  plans: Plan[]
  from: string
  to: string
}) {
  const qc = useQueryClient()
  const [error, setError] = useState<string | null>(null)
  const [rangeOpen, setRangeOpen] = useState(false)

  const q = useQuery({
    queryKey: ['ratePlanCalendar', propertyId, ratePlanId, from, to],
    queryFn: () => getRatePlanCalendar(propertyId, ratePlanId, from, to),
    enabled: Boolean(propertyId && ratePlanId && from && to),
  })
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['ratePlanCalendar', propertyId] })
    qc.invalidateQueries({ queryKey: ['rateCalendar', propertyId] })
  }
  const save = useMutation({
    mutationFn: (changes: PlanChange[]) => setRatePlanCalendar(propertyId, changes),
    onSuccess: () => { setError(null); refresh() },
    onError: (e) => setError(apiError(e)),
  })

  const one = (d: string, patch: Partial<PlanChange>) => save.mutate([{
    rate_plan_id: ratePlanId, date_from: d, date_to: d, ...patch,
  }])
  const numeric = (field: 'rate' | 'min_stay' | 'max_stay') =>
    (d: string) => (v: number | null) =>
      one(d, v === null ? { clear: [field] } : { [field]: v })

  const cal = q.data
  const busy = save.isPending
  const tint = (d: string) => isWeekend(d) ? 'bg-amber-50/50' : ''
  const label = 'sticky left-0 z-10 w-[170px] min-w-[170px] whitespace-nowrap bg-white px-4 py-1.5 text-left text-xs font-medium text-slate-500'
  const cellCls = (d: string) => `border-l border-slate-100 px-1.5 py-1.5 ${tint(d)}`
  const own = (n: PlanNight, f: string) => n.overridden.includes(f)

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3 rounded-lg bg-brand-light/60 px-4 py-3 text-sm text-slate-700">
        <span className="flex items-start gap-2">
          <Info className="mt-0.5 h-4 w-4 shrink-0 text-brand" />
          <span>
            Editing <strong className="font-semibold">{cal?.rate_plan_name ?? 'rate plan'}</strong>
            {cal?.room_type_name && <> on <strong className="font-semibold">{cal.room_type_name}</strong></>}.
            Highlighted values are set on this plan; the rest come from the room
            type, rate rules and plan defaults. Clear a box (or use Reset in the
            range panel) to hand a night back to them.
          </span>
        </span>
        <button onClick={() => setRangeOpen(true)}
          className="flex shrink-0 items-center gap-2 rounded-lg border border-brand bg-white px-3 py-2 text-sm font-semibold text-brand hover:bg-brand-light">
          <Layers className="h-4 w-4" /> Edit a date range
        </button>
      </div>

      {error && (
        <p className="flex items-start justify-between gap-3 rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">
          <span className="flex items-start gap-2"><AlertCircle className="mt-0.5 h-4 w-4 shrink-0" /> {error}</span>
          <button onClick={() => setError(null)} aria-label="Dismiss"><X className="h-4 w-4" /></button>
        </p>
      )}

      <div className={`grid gap-4 ${rangeOpen ? 'xl:grid-cols-[minmax(0,1fr)_380px]' : ''}`}>
        <div className="min-w-0 overflow-x-auto rounded-xl border border-slate-200 bg-white">
          {q.isLoading && (
            <div className="grid h-56 place-items-center text-slate-400">
              <Loader2 className="h-6 w-6 animate-spin" />
            </div>
          )}
          {q.isError && (
            <p className="px-4 py-10 text-center text-sm text-red-700">{apiError(q.error)}</p>
          )}
          {cal && (
            <table className="w-full border-collapse text-sm">
              <thead>
                <tr>
                  <th className="sticky left-0 z-10 border-b border-slate-200 bg-white px-4 py-3 text-left font-semibold text-slate-600">
                    {cal.rate_plan_name}
                  </th>
                  {cal.nights.map((n) => (
                    <th key={n.stay_date}
                      className={`min-w-[88px] border-b border-slate-200 px-2 py-2 text-center font-semibold text-slate-600 ${
                        isWeekend(n.stay_date) ? 'bg-amber-50/70' : 'bg-white'}`}>
                      <div className="whitespace-nowrap text-xs">{fmtDayMonth(n.stay_date)}</div>
                      <div className="text-xs font-normal text-slate-400">{fmtWeekday(n.stay_date)}</div>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                <tr>
                  <th scope="row" className={label}>Price</th>
                  {cal.nights.map((n) => (
                    <td key={n.stay_date} className={cellCls(n.stay_date)}>
                      <NumberCell value={n.rate} own={own(n, 'rate')} decimals
                        disabled={busy} label={`Price on ${n.stay_date}`}
                        blankLabel="—" onCommit={numeric('rate')(n.stay_date)} />
                    </td>
                  ))}
                </tr>
                <tr>
                  <th scope="row" className={label}>Min stay</th>
                  {cal.nights.map((n) => (
                    <td key={n.stay_date} className={cellCls(n.stay_date)}>
                      <NumberCell value={n.min_stay} own={own(n, 'min_stay')}
                        disabled={busy} label={`Minimum stay on ${n.stay_date}`}
                        onCommit={numeric('min_stay')(n.stay_date)} />
                    </td>
                  ))}
                </tr>
                <tr>
                  <th scope="row" className={label}>Max stay <span className="font-normal text-slate-400">(0 = none)</span></th>
                  {cal.nights.map((n) => (
                    <td key={n.stay_date} className={cellCls(n.stay_date)}>
                      <NumberCell value={n.max_stay} own={own(n, 'max_stay')}
                        disabled={busy} label={`Maximum stay on ${n.stay_date}`}
                        onCommit={numeric('max_stay')(n.stay_date)} />
                    </td>
                  ))}
                </tr>
                {FLAGS.map(([field, long, short]) => (
                  <tr key={field}>
                    <th scope="row" className={label} title={long}>{long}</th>
                    {cal.nights.map((n) => {
                      const on = n[field as Flag]
                      return (
                        <td key={n.stay_date} className={cellCls(n.stay_date)}>
                          <button role="switch" aria-checked={on} disabled={busy}
                            aria-label={`${short} on ${n.stay_date}`}
                            title={own(n, field) ? 'Set on this plan' : 'Inherited'}
                            onClick={() => one(n.stay_date, { [field]: !on })}
                            className={`mx-auto flex h-5 w-9 items-center rounded-full p-0.5 transition disabled:opacity-50 ${
                              on ? 'bg-brand' : 'bg-slate-200'} ${
                              own(n, field) ? 'ring-2 ring-brand/30 ring-offset-1' : ''}`}>
                            <span className={`h-4 w-4 rounded-full bg-white shadow transition ${on ? 'translate-x-4' : ''}`} />
                          </button>
                        </td>
                      )
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {rangeOpen && (
          <div className="xl:sticky xl:top-4 xl:max-h-[calc(100vh-6rem)]">
            <RangePanel ratePlanId={ratePlanId} plans={plans} from={from} to={to}
              saving={busy}
              onSave={(changes, done) => save.mutate(changes, { onSuccess: done })}
              onClose={() => setRangeOpen(false)} />
          </div>
        )}
      </div>
    </div>
  )
}

/* ----------------------------------------------------------- range panel --- */
type Tri = '' | 'true' | 'false' | 'reset'

function RangePanel({
  ratePlanId, plans, from, to, saving, onSave, onClose,
}: {
  ratePlanId: string
  plans: Plan[]
  from: string
  to: string
  saving: boolean
  onSave: (changes: PlanChange[], done: () => void) => void
  onClose: () => void
}) {
  const [plan, setPlan] = useState(ratePlanId)
  const [dFrom, setDFrom] = useState(from)
  const [dTo, setDTo] = useState(to)
  const [days, setDays] = useState<number[]>([])
  const [rate, setRate] = useState('')
  const [minStay, setMinStay] = useState('')
  const [maxStay, setMaxStay] = useState('')
  const [flags, setFlags] = useState<Record<Flag, Tri>>(
    { closed_to_arrival: '', closed_to_departure: '', stop_sell: '' })
  const [reset, setReset] = useState<string[]>([])
  const [batch, setBatch] = useState<PlanChange[]>([])
  const [problem, setProblem] = useState<string | null>(null)
  const [saved, setSaved] = useState<string | null>(null)

  useEffect(() => { setPlan(ratePlanId) }, [ratePlanId])

  const planName = (id: string) => plans.find((p) => p.id === id)?.name ?? 'Rate plan'

  const build = (): PlanChange | null => {
    if (!plan || !dFrom || !dTo || dTo < dFrom) { setProblem('Choose a valid date range.'); return null }
    const ch: PlanChange = { rate_plan_id: plan, date_from: dFrom, date_to: dTo }
    if (days.length) ch.weekdays = [...days].sort()
    const clear = [...reset]
    const num = (raw: string, field: 'rate' | 'min_stay' | 'max_stay', decimals: boolean) => {
      if (raw.trim() === '' || clear.includes(field)) return true
      const n = Number(raw)
      if (Number.isNaN(n) || n < 0 || (!decimals && !Number.isInteger(n))) {
        setProblem(`${LABEL[field]} is not a valid number.`); return false
      }
      ch[field] = n
      return true
    }
    if (!num(rate, 'rate', true) || !num(minStay, 'min_stay', false)
      || !num(maxStay, 'max_stay', false)) return null
    for (const [f] of FLAGS) {
      const v = flags[f]
      if (v === 'reset') clear.push(f)
      else if (v) ch[f] = v === 'true'
    }
    if (clear.length) ch.clear = clear
    const sets = Object.keys(ch).filter((k) => k in LABEL)
    if (!sets.length && !clear.length) { setProblem('Nothing to change: fill in at least one value.'); return null }
    setProblem(null)
    return ch
  }

  const resetForm = () => {
    setRate(''); setMinStay(''); setMaxStay(''); setReset([])
    setFlags({ closed_to_arrival: '', closed_to_departure: '', stop_sell: '' })
  }

  const describe = (c: PlanChange) => {
    const parts = Object.entries(c)
      .filter(([k, v]) => k in LABEL && v !== undefined && v !== null)
      .map(([k, v]) => `${LABEL[k]} ${typeof v === 'boolean' ? (v ? 'on' : 'off') : v}`)
    if (c.clear?.length) parts.push(`reset ${c.clear.map((f) => LABEL[f]).join(', ')}`)
    return parts.join(' · ')
  }

  const saveAll = (extra: PlanChange | null) => {
    const changes = extra ? [...batch, extra] : batch
    if (!changes.length) return
    onSave(changes, () => {
      setSaved(`Saved ${changes.length} change${changes.length === 1 ? '' : 's'} together.`)
      setBatch([]); resetForm()
    })
  }

  const inputCls = 'w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700'

  return (
    <section aria-label="Edit a date range"
      className="flex max-h-full flex-col rounded-xl border border-slate-200 bg-white">
      <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
        <h2 className="font-semibold text-ink">Edit a date range</h2>
        <button onClick={onClose} aria-label="Close" className="text-slate-400 hover:text-slate-600">
          <X className="h-4 w-4" />
        </button>
      </div>
      <div className="space-y-4 overflow-y-auto px-4 py-4 text-sm">
        <label className="block">
          <span className="mb-1 block text-xs font-medium text-slate-500">Rate plan</span>
          <Select value={plan} onChange={(e) => setPlan(e.target.value)} className={inputCls}>
            {plans.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </Select>
        </label>
        <div className="grid grid-cols-2 gap-2">
          <div>
            <span className="mb-1 block text-xs font-medium text-slate-500">From</span>
            <DateField value={dFrom} onChange={setDFrom} max={dTo || undefined} label="From" />
          </div>
          <div>
            <span className="mb-1 block text-xs font-medium text-slate-500">To (inclusive)</span>
            <DateField value={dTo} onChange={setDTo} min={dFrom || undefined} label="To" />
          </div>
        </div>
        <div>
          <span className="mb-1 block text-xs font-medium text-slate-500">
            Only on {days.length ? '' : '(every night)'}
          </span>
          <div className="flex flex-wrap gap-1">
            {WEEKDAYS.map((w, i) => {
              const on = days.includes(i + 1)
              return (
                <button key={w} type="button"
                  onClick={() => setDays(on ? days.filter((x) => x !== i + 1) : [...days, i + 1])}
                  className={`rounded-md border px-2 py-1 text-xs font-medium ${
                    on ? 'border-brand bg-brand text-white' : 'border-slate-200 text-slate-600 hover:bg-slate-50'}`}>
                  {w}
                </button>
              )
            })}
          </div>
        </div>

        <p className="text-xs text-slate-400">Leave a field empty to keep what each night has.</p>
        <div className="grid grid-cols-3 gap-2">
          {([['Price', rate, setRate, 'rate'], ['Min stay', minStay, setMinStay, 'min_stay'],
            ['Max stay', maxStay, setMaxStay, 'max_stay']] as const).map(([l, v, set, f]) => (
            <label key={f} className="block">
              <span className="mb-1 block text-xs font-medium text-slate-500">{l}</span>
              <input value={v} onChange={(e) => set(e.target.value)} disabled={reset.includes(f)}
                inputMode={f === 'rate' ? 'decimal' : 'numeric'} className={`${inputCls} disabled:bg-slate-50`} />
              <span className="mt-1 flex items-center gap-1 text-xs text-slate-500">
                <input type="checkbox" checked={reset.includes(f)}
                  onChange={(e) => setReset(e.target.checked ? [...reset, f] : reset.filter((x) => x !== f))} />
                Reset
              </span>
            </label>
          ))}
        </div>
        {FLAGS.map(([f, long]) => (
          <label key={f} className="flex items-center justify-between gap-3">
            <span className="text-slate-600">{long}</span>
            <Select value={flags[f]} onChange={(e) => setFlags({ ...flags, [f]: e.target.value as Tri })}
              className="w-40 rounded-lg border border-slate-200 px-2 py-1.5 text-sm text-slate-700">
              <option value="">Keep as is</option>
              <option value="true">Yes</option>
              <option value="false">No</option>
              <option value="reset">Reset</option>
            </Select>
          </label>
        ))}

        {problem && <p className="text-xs text-red-600">{problem}</p>}
        {saved && <p className="text-xs font-medium text-emerald-700">{saved}</p>}

        {batch.length > 0 && (
          <div className="rounded-lg border border-slate-200">
            <p className="border-b border-slate-100 px-3 py-2 text-xs font-semibold text-slate-600">
              Waiting to save together ({batch.length})
            </p>
            <ul className="divide-y divide-slate-100">
              {batch.map((c, i) => (
                <li key={i} className="flex items-start gap-2 px-3 py-2 text-xs">
                  <div className="min-w-0 flex-1">
                    <p className="font-medium text-slate-700">{planName(c.rate_plan_id)}</p>
                    <p className="text-slate-500">
                      {fmtDate(c.date_from)}{c.date_to !== c.date_from && <> → {fmtDate(c.date_to)}</>}
                      {c.weekdays?.length ? ` · ${c.weekdays.map((d) => WEEKDAYS[d - 1]).join(', ')}` : ''}
                    </p>
                    <p className="text-slate-500">{describe(c)}</p>
                  </div>
                  <button onClick={() => setBatch(batch.filter((_, j) => j !== i))}
                    aria-label="Remove" className="text-slate-400 hover:text-red-600">
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
      <div className="flex flex-wrap gap-2 border-t border-slate-100 px-4 py-3">
        <button disabled={saving}
          onClick={() => { const c = build(); if (c) { setBatch([...batch, c]); setSaved(null); resetForm() } }}
          title="Queue this and add another, e.g. a different plan or dates"
          className="flex items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-2 text-sm font-semibold text-slate-600 hover:bg-slate-50 disabled:opacity-50">
          <Plus className="h-4 w-4" /> Add another
        </button>
        <button disabled={saving}
          onClick={() => {
            const pending = rate || minStay || maxStay || reset.length
              || Object.values(flags).some(Boolean)
            if (pending) { const c = build(); if (c) saveAll(c) } else saveAll(null)
          }}
          className="ml-auto flex items-center gap-1.5 rounded-lg bg-brand px-3 py-2 text-sm font-semibold text-white hover:bg-brand-dark disabled:opacity-50">
          {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
          Save{batch.length ? ` all (${batch.length + (rate || minStay || maxStay || reset.length || Object.values(flags).some(Boolean) ? 1 : 0)})` : ''}
        </button>
      </div>
    </section>
  )
}
