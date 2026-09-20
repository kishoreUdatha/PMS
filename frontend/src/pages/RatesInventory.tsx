import { Fragment, useEffect, useMemo, useState } from 'react'
import Select from '../components/Select'
import { FILTER_SELECT } from '../lib/controls'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import DateField from '../components/DateField'
import { fmtDate, fmtDayMonth, fmtWeekday } from '../lib/dates'
import {
  AlertCircle, BarChart3, ChevronLeft, ChevronRight, Copy,
  Ellipsis, Eye, Info, Loader2, Pencil, RefreshCw, Send, X,
} from 'lucide-react'
import {
  getRateCalendar, updateCalendarCell, bulkUpdateRates, copyRates,
  listManagedRoomTypes, listRatePlans,
  type RateCalendar, type CalendarCell, type BulkUpdateResult,
} from '../api'

/**
 * Screen 034 — Rates, Restrictions and Inventory Calendar.
 *
 * The grid is a view over the room types: a cell shows the room type's own rate
 * until someone overrides that date, and overridden cells are marked so it is
 * always clear which figures were set by hand. Availability is derived, never
 * entered.
 *
 * The right-hand panel previews and publishes with the same request — only the
 * `preview_only` flag differs — so the numbers shown are the numbers applied.
 */


const input = 'w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700'

const iso = (d: Date) => {
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}
const parse = (s: string) => new Date(`${s}T00:00:00`)
const addDays = (s: string, n: number) => {
  const d = parse(s); d.setDate(d.getDate() + n); return iso(d)
}
const isWeekend = (s: string) => [0, 6].includes(parse(s).getDay())
const dayLabel = (s: string) => ({ top: fmtDayMonth(s), bottom: fmtWeekday(s) })
const longDate = (s: string) => `${fmtDate(s)} (${fmtWeekday(s)})`
const money = (v: string | null) =>
  v === null ? '—' : Number(v).toLocaleString('en-IN', { maximumFractionDigits: 0 })

function apiError(e: unknown): string {
  const d = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  if (typeof d === 'string') return d
  if (Array.isArray(d)) return 'Some values are invalid. Check the highlighted fields.'
  return 'Could not apply the change. Please try again.'
}

/* --------------------------------------------------------------- rate cell --- */
function RateCell({
  cell, onCommit, disabled,
}: { cell: CalendarCell; onCommit: (v: number | null) => void; disabled: boolean }) {
  const show = (v: string | null) => (v === null ? '' : String(Number(v)))
  const [draft, setDraft] = useState(show(cell.rate))
  useEffect(() => { setDraft(show(cell.rate)) }, [cell.rate])

  const commit = () => {
    const raw = String(draft).trim()
    if (raw === '') { if (cell.rate_is_override) onCommit(null); return }
    const n = Number(raw)
    if (Number.isNaN(n) || n < 0) { setDraft(show(cell.rate)); return }
    if (cell.rate !== null && n === Number(cell.rate)) return
    onCommit(n)
  }

  return (
    <input
      value={draft} disabled={disabled} inputMode="numeric"
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === 'Enter') (e.target as HTMLInputElement).blur()
        if (e.key === 'Escape') { setDraft(show(cell.rate)); (e.target as HTMLInputElement).blur() }
      }}
      title={cell.rate_is_override
        ? 'Set for this date. Clear the box to go back to the room type rate.'
        : 'Inherited from the room type'}
      className={`w-full rounded border px-1.5 py-1 text-center text-sm tabular-nums outline-none transition
        focus:border-brand focus:ring-1 focus:ring-brand/40 disabled:opacity-50 ${
        cell.rate_is_override
          ? 'border-brand/40 bg-brand-light/40 font-semibold text-slate-800'
          : 'border-transparent bg-transparent text-slate-700 hover:border-slate-200'
      }`} />
  )
}

/* ------------------------------------------------------------- bulk panel --- */
function BulkPanel({
  propertyId, selected, roomTypeIds, onClose, onApplied,
}: {
  propertyId: string
  selected: string[]
  roomTypeIds: string[]
  onClose: () => void
  onApplied: () => void
}) {
  const [change, setChange] = useState<'increase' | 'decrease' | 'set_to'>('increase')
  const [valueType, setValueType] = useState<'percent' | 'amount'>('percent')
  const [value, setValue] = useState('10')
  const [updateMinStay, setUpdateMinStay] = useState(false)
  const [minStay, setMinStay] = useState(2)
  const [updateStopSell, setUpdateStopSell] = useState(false)
  const [stopSell, setStopSell] = useState(true)
  const [preview, setPreview] = useState<BulkUpdateResult | null>(null)
  const [error, setError] = useState<string | null>(null)

  const body = (previewOnly: boolean) => ({
    room_type_ids: roomTypeIds,
    dates: selected,
    change,
    value_type: change === 'set_to' ? 'amount' : valueType,
    value: value === '' ? 0 : Number(value),
    update_min_stay: updateMinStay,
    min_stay: updateMinStay ? minStay : null,
    update_stop_sell: updateStopSell,
    stop_sell: updateStopSell ? stopSell : null,
    preview_only: previewOnly,
  })

  const run = useMutation({
    mutationFn: (previewOnly: boolean) => bulkUpdateRates(propertyId, body(previewOnly)),
    onSuccess: (res) => {
      setPreview(res)
      setError(null)
      if (res.applied) onApplied()
    },
    onError: (e) => { setError(apiError(e)); setPreview(null) },
  })

  // Keep the preview honest: any change to the inputs invalidates it.
  useEffect(() => { setPreview(null) },
    [change, valueType, value, updateMinStay, minStay, updateStopSell, stopSell,
      selected.join(), roomTypeIds.join()])

  const span = selected.length === 0 ? null
    : selected.length === 1 ? longDate(selected[0])
      : `${longDate(selected[0])} – ${longDate(selected[selected.length - 1])}`

  return (
    <aside className="flex h-full min-h-0 flex-col rounded-xl border border-slate-200 bg-white">
      <div className="flex items-start justify-between gap-3 border-b border-slate-100 px-5 py-4">
        <div>
          <h2 className="text-xl font-bold text-ink">Selected Dates</h2>
          {span && <p className="mt-1 text-sm text-slate-600">{span}</p>}
        </div>
        <div className="flex items-center gap-2">
          <span className="rounded-md bg-slate-75 px-2.5 py-1 text-xs font-semibold text-slate-600">
            {selected.length} date{selected.length === 1 ? '' : 's'}
          </span>
          <button onClick={onClose} aria-label="Clear selection"
            className="rounded p-1 text-slate-400 hover:bg-slate-100">
            <X className="h-4 w-4" />
          </button>
        </div>
      </div>

      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-5 py-4">
        <div>
          <h3 className="mb-2 font-bold text-ink">Rate Change Preview</h3>
          {preview ? (
            <table className="w-full text-left">
              <thead>
                <tr className="border-b border-slate-200">
                  <th className="py-2 text-xs font-semibold text-slate-500">Room Type</th>
                  <th className="py-2 text-right text-xs font-semibold text-slate-500">
                    Current Rate (Avg)
                  </th>
                  <th className="py-2 text-right text-xs font-semibold text-slate-500">
                    New Rate (Avg)
                  </th>
                </tr>
              </thead>
              <tbody>
                {preview.rows.map((r) => (
                  <tr key={r.room_type_id} className="border-b border-slate-100 last:border-0">
                    <td className="py-2 text-sm text-slate-700">{r.room_type_name}</td>
                    <td className="py-2 text-right text-sm tabular-nums text-slate-500">
                      ₹ {money(r.current_avg)}
                    </td>
                    <td className="py-2 text-right text-sm font-semibold tabular-nums text-slate-800">
                      ₹ {money(r.new_avg)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="rounded-lg bg-slate-50 px-3 py-3 text-sm text-slate-500">
              Choose a change below, then Preview Only to see what it would do.
            </p>
          )}
        </div>

        <div>
          <span className="mb-1 block text-sm font-medium text-slate-700">Change</span>
          <div className="flex overflow-hidden rounded-lg border border-slate-200">
            {([['increase', 'Increase'], ['decrease', 'Decrease'],
               ['set_to', 'Set to']] as const).map(([v, l]) => (
              <button key={v} onClick={() => setChange(v)}
                className={`flex-1 px-3 py-2 text-sm font-semibold transition ${
                  change === v ? 'bg-brand text-white' : 'bg-white text-slate-500 hover:bg-slate-50'
                }`}>
                {l}
              </button>
            ))}
          </div>
        </div>

        <div>
          <span className="mb-1 block text-sm font-medium text-slate-700">
            {change === 'set_to' ? 'New rate (₹)'
              : valueType === 'percent' ? 'Percentage' : 'Amount (₹)'}
          </span>
          <div className="flex gap-2">
            <input type="number" min={0} className={input} value={value}
              onChange={(e) => setValue(e.target.value)} />
            {change !== 'set_to' && (
              <div className="flex shrink-0 overflow-hidden rounded-lg border border-slate-200">
                {(['percent', 'amount'] as const).map((t) => (
                  <button key={t} onClick={() => setValueType(t)}
                    className={`px-3 py-2 text-sm font-semibold transition ${
                      valueType === t
                        ? 'bg-brand text-white' : 'bg-white text-slate-500 hover:bg-slate-50'
                    }`}>
                    {t === 'percent' ? '%' : '₹'}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>

        <label className="flex items-center gap-2 text-sm font-medium text-slate-700">
          <input type="checkbox" checked={updateMinStay}
            onChange={(e) => setUpdateMinStay(e.target.checked)}
            className="h-4 w-4 rounded border-slate-300 text-brand" />
          Update Min Stay as well
        </label>
        {updateMinStay && (
          <Select className={input} value={minStay}
            onChange={(e) => setMinStay(Number(e.target.value))}>
            {[1, 2, 3, 4, 5, 7].map((n) => (
              <option key={n} value={n}>{n} night{n === 1 ? '' : 's'}</option>
            ))}
          </Select>
        )}

        <label className="flex items-center gap-2 text-sm font-medium text-slate-700">
          <input type="checkbox" checked={updateStopSell}
            onChange={(e) => setUpdateStopSell(e.target.checked)}
            className="h-4 w-4 rounded border-slate-300 text-brand" />
          Update Stop Sell
        </label>
        {updateStopSell && (
          <Select className={input} value={stopSell ? 'on' : 'off'}
            onChange={(e) => setStopSell(e.target.value === 'on')}>
            <option value="on">Stop selling these dates</option>
            <option value="off">Resume selling these dates</option>
          </Select>
        )}

        <p className="flex items-start gap-2 rounded-lg bg-sky-50 px-3 py-2 text-sm text-sky-800">
          <Info className="mt-0.5 h-4 w-4 shrink-0" />
          {preview
            ? `This will update rates for ${preview.room_type_count} room type`
              + `${preview.room_type_count === 1 ? '' : 's'} across ${preview.date_count} `
              + `date${preview.date_count === 1 ? '' : 's'} (${preview.cell_count} rate cells).`
            : `${roomTypeIds.length === 0 ? 'All' : roomTypeIds.length} room type`
              + `${roomTypeIds.length === 1 ? '' : 's'} × ${selected.length} `
              + `date${selected.length === 1 ? '' : 's'} are selected.`}
        </p>

        {preview?.applied && (
          <p className="rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
            Published. {preview.cell_count} rate cell
            {preview.cell_count === 1 ? '' : 's'} updated.
          </p>
        )}
        {error && (
          <p className="flex items-start gap-2 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" /> {error}
          </p>
        )}
      </div>

      <div className="space-y-2 border-t border-slate-100 px-5 py-4">
        <button onClick={() => run.mutate(false)} disabled={run.isPending}
          className="flex w-full items-center justify-center gap-2 rounded-lg bg-brand px-4 py-3 font-semibold text-white hover:bg-brand-dark disabled:opacity-60">
          {run.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
          Publish Changes
        </button>
        <button onClick={() => run.mutate(true)} disabled={run.isPending}
          className="flex w-full items-center justify-center gap-2 rounded-lg border border-brand px-4 py-3 font-semibold text-brand hover:bg-brand-light disabled:opacity-60">
          <Eye className="h-4 w-4" /> Preview Only
        </button>
      </div>
    </aside>
  )
}

/* -------------------------------------------------------------- copy modal --- */
function CopyDialog({
  propertyId, from, to, onClose, onDone,
}: {
  propertyId: string; from: string; to: string
  onClose: () => void; onDone: () => void
}) {
  const [sourceFrom, setSourceFrom] = useState(from)
  const [sourceTo, setSourceTo] = useState(to)
  const [targetFrom, setTargetFrom] = useState(addDays(to, 1))
  const [restrictions, setRestrictions] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<{ cells: number; targetTo: string } | null>(null)

  const run = useMutation({
    mutationFn: (previewOnly: boolean) => copyRates(propertyId, {
      source_from: sourceFrom, source_to: sourceTo, target_from: targetFrom,
      include_restrictions: restrictions, preview_only: previewOnly,
    }),
    onSuccess: (res) => {
      setError(null)
      setResult({ cells: res.cell_count, targetTo: res.target_to })
      if (res.applied) onDone()
    },
    onError: (e) => { setError(apiError(e)); setResult(null) },
  })

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-slate-900/40 p-4">
      <div className="w-full max-w-lg rounded-xl bg-white shadow-xl">
        <div className="flex items-center justify-between border-b border-slate-100 px-5 py-4">
          <h3 className="text-lg font-bold text-ink">Copy Rates</h3>
          <button onClick={onClose} aria-label="Close"
            className="rounded p-1 text-slate-400 hover:bg-slate-100">
            <X className="h-5 w-5" />
          </button>
        </div>
        <div className="space-y-4 px-5 py-4">
          <p className="text-sm text-slate-500">
            Copies each day's rate and restrictions forward in order. The target
            range must not overlap the source.
          </p>
          <div className="grid grid-cols-2 gap-3">
            <label>
              <span className="mb-1 block text-sm font-medium text-slate-700">Copy from</span>
              <DateField value={sourceFrom} onChange={(v) => setSourceFrom(v)} className={input} />
            </label>
            <label>
              <span className="mb-1 block text-sm font-medium text-slate-700">Copy to</span>
              <DateField value={sourceTo} onChange={(v) => setSourceTo(v)} className={input} />
            </label>
          </div>
          <label className="block">
            <span className="mb-1 block text-sm font-medium text-slate-700">
              Paste starting on
            </span>
            <DateField value={targetFrom} onChange={(v) => setTargetFrom(v)} className={input} />
          </label>
          <label className="flex items-center gap-2 text-sm font-medium text-slate-700">
            <input type="checkbox" checked={restrictions}
              onChange={(e) => setRestrictions(e.target.checked)}
              className="h-4 w-4 rounded border-slate-300 text-brand" />
            Copy min stay and stop sell too
          </label>
          {result && (
            <p className="rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
              {result.cells} cell{result.cells === 1 ? '' : 's'} copied, ending{' '}
              {longDate(result.targetTo)}.
            </p>
          )}
          {error && (
            <p className="flex items-start gap-2 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" /> {error}
            </p>
          )}
        </div>
        <div className="flex justify-end gap-2 border-t border-slate-100 px-5 py-4">
          <button onClick={onClose}
            className="rounded-lg border border-slate-200 px-4 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50">
            Cancel
          </button>
          <button onClick={() => run.mutate(false)} disabled={run.isPending}
            className="flex items-center gap-2 rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white hover:bg-brand-dark disabled:opacity-60">
            {run.isPending && <Loader2 className="h-4 w-4 animate-spin" />} Copy Rates
          </button>
        </div>
      </div>
    </div>
  )
}

/* -------------------------------------------------------------------- page --- */
export default function RatesInventory({ propertyId }: { propertyId: string }) {
  const qc = useQueryClient()
  const today = iso(new Date())
  const [from, setFrom] = useState(today)
  const [to, setTo] = useState(addDays(today, 13))
  const [roomTypeId, setRoomTypeId] = useState('')
  const [ratePlanId, setRatePlanId] = useState('')
  const [selected, setSelected] = useState<string[]>([])
  const [copying, setCopying] = useState(false)
  const [more, setMore] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const calQ = useQuery({
    queryKey: ['rateCalendar', propertyId, from, to, roomTypeId, ratePlanId],
    queryFn: () => getRateCalendar(propertyId, from, to,
      roomTypeId || undefined, ratePlanId || undefined),
    enabled: propertyId !== '',
  })
  const plansQ = useQuery({
    queryKey: ['ratePlans', propertyId, {}],
    queryFn: () => listRatePlans(propertyId), enabled: propertyId !== '',
  })
  const typesQ = useQuery({
    queryKey: ['managedRoomTypes', propertyId],
    queryFn: () => listManagedRoomTypes(propertyId), enabled: propertyId !== '',
  })

  const cal: RateCalendar | undefined = calQ.data
  // Viewing a plan shows derived prices, so nothing in the grid is editable.
  const locked = cal ? !cal.editable : false
  const refresh = () => qc.invalidateQueries({ queryKey: ['rateCalendar', propertyId] })

  const cell = useMutation({
    mutationFn: (body: Parameters<typeof updateCalendarCell>[1]) =>
      updateCalendarCell(propertyId, body),
    onSuccess: refresh,
    onError: (e) => setError(apiError(e)),
  })

  const forecast = useMemo(
    () => Object.fromEntries((cal?.forecast ?? []).map((f) => [f.stay_date, f.occupancy_pct])),
    [cal],
  )

  const shift = (days: number) => {
    setFrom(addDays(from, days)); setTo(addDays(to, days)); setSelected([])
  }
  const toggleDate = (d: string) =>
    setSelected((s) => s.includes(d) ? s.filter((x) => x !== d) : [...s, d].sort())

  const visibleTypeIds = roomTypeId ? [roomTypeId] : []

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-display text-ink">Rates &amp; Inventory</h1>
        <p className="text-slate-500">
          Manage room rates, availability and restrictions. Keep your inventory
          optimized for higher revenue.
        </p>
      </div>

      {/* toolbar */}
      <div className="flex flex-wrap items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 py-2">
        <button onClick={() => shift(-((parse(to).getTime() - parse(from).getTime()) / 86400000 + 1))}
          aria-label="Previous period"
          className="grid h-9 w-9 place-items-center rounded-lg border border-slate-200 text-slate-500 hover:bg-slate-50">
          <ChevronLeft className="h-4 w-4" />
        </button>
        {/* Two fields, each in its own box. They used to be borderless inputs
            sharing one bordered pill, which stopped working the moment they
            became DateFields: `border-0` and `border` both set border-width,
            and which one lands is decided by the order Tailwind emits them,
            not by the order they are written. */}
        <span className="flex items-center gap-2">
          <DateField value={from} onChange={(v) => { setFrom(v); setSelected([]) }}
            max={to || undefined} label="Range start" />
          <span className="text-slate-300">–</span>
          <DateField value={to} onChange={(v) => { setTo(v); setSelected([]) }}
            min={from || undefined} label="Range end" />
        </span>
        <button onClick={() => shift((parse(to).getTime() - parse(from).getTime()) / 86400000 + 1)}
          aria-label="Next period"
          className="grid h-9 w-9 place-items-center rounded-lg border border-slate-200 text-slate-500 hover:bg-slate-50">
          <ChevronRight className="h-4 w-4" />
        </button>

        <Select blankIsChoice value={roomTypeId} onChange={(e) => setRoomTypeId(e.target.value)}
          className={FILTER_SELECT}>
          <option value="">All Room Types</option>
          {(typesQ.data ?? []).map((t) => (
            <option key={t.id} value={t.id}>{t.name}</option>
          ))}
        </Select>

        <Select value={ratePlanId}
          onChange={(e) => { setRatePlanId(e.target.value); setSelected([]) }}
          title="Show what a rate plan sells for on each date"
          className={FILTER_SELECT}>
          <option value="">Room type rate</option>
          {(plansQ.data?.items ?? []).map((p) => (
            <option key={p.id} value={p.id}>{p.name}</option>
          ))}
        </Select>

        <div className="ml-auto flex flex-wrap items-center gap-2">
          <button onClick={() => setSelected(cal?.dates ?? [])} disabled={locked}
            title={locked ? 'Switch to Room type rate to change prices' : undefined}
            className="flex items-center gap-2 rounded-lg border border-brand px-3 py-2 text-sm font-semibold text-brand hover:bg-brand-light disabled:cursor-not-allowed disabled:border-slate-200 disabled:text-slate-400 disabled:hover:bg-transparent">
            <Pencil className="h-4 w-4" /> Bulk Update
          </button>
          <button
            onClick={() => { window.location.href = '/rates/rules' }}
            title="Open Rate Rules"
            className="flex items-center gap-2 rounded-lg border border-slate-200 px-3 py-2 text-sm font-semibold text-slate-600 hover:bg-slate-50">
            <BarChart3 className="h-4 w-4" /> Yield Rules
          </button>
          <button onClick={() => setCopying(true)} disabled={locked}
            title={locked ? 'Switch to Room type rate to copy prices' : undefined}
            className="flex items-center gap-2 rounded-lg border border-slate-200 px-3 py-2 text-sm font-semibold text-slate-600 hover:bg-slate-50 disabled:cursor-not-allowed disabled:text-slate-300">
            <Copy className="h-4 w-4" /> Copy Rates
          </button>
          <div className="relative">
            <button onClick={() => setMore((v) => !v)} aria-label="More actions"
              className="grid h-9 w-9 place-items-center rounded-lg border border-slate-200 text-slate-500 hover:bg-slate-50">
              <Ellipsis className="h-4 w-4" />
            </button>
            {more && (
              <div className="absolute right-0 z-20 mt-1 w-56 overflow-hidden rounded-lg border border-slate-200 bg-white shadow-lg">
                <button onClick={() => { setMore(false); setSelected(cal?.dates.filter(isWeekend) ?? []) }}
                  className="block w-full px-4 py-2 text-left text-sm text-slate-700 hover:bg-slate-50">
                  Select weekends only
                </button>
                <button onClick={() => { setMore(false); setSelected([]) }}
                  className="block w-full px-4 py-2 text-left text-sm text-slate-700 hover:bg-slate-50">
                  Clear selection
                </button>
                <button onClick={() => { setMore(false); refresh() }}
                  className="block w-full px-4 py-2 text-left text-sm text-slate-700 hover:bg-slate-50">
                  Refresh
                </button>
                {/* Moved in here off the button row. With it out there the bar
                    needed 1303px inside 1286px and wrapped onto a second line;
                    trimming padding instead left five pixels of slack, which
                    wraps again on any narrower window. This is the one of the
                    four that does not act on the calendar. */}
                <button onClick={() => { setMore(false); setNotice('Channel sync belongs to Distribution (screens 024 and 120), which is not built yet. Rates published here are stored, but nothing pushes them to an OTA.') }}
                  className="flex w-full items-center gap-2 border-t border-slate-100 px-4 py-2 text-left text-sm text-slate-700 hover:bg-slate-50">
                  <RefreshCw className="h-4 w-4 text-slate-400" /> Sync Channels
                </button>
              </div>
            )}
          </div>
        </div>
      </div>

      {locked && cal && (
        <p className="flex items-start gap-2 rounded-lg bg-brand-light/60 px-4 py-3 text-sm text-slate-700">
          <Info className="mt-0.5 h-4 w-4 shrink-0 text-brand" />
          {/* One flex child, or each bold run becomes its own column. */}
          <span>
            Showing what <strong className="font-semibold">{cal.rate_plan_name}</strong>{' '}
            sells for on each date. These prices come from the room type rate plus
            the plan's pricing, so they are read-only here — switch back to{' '}
            <strong className="font-semibold">Room type rate</strong> to change them,
            or edit the plan itself on Rate Plans.
          </span>
        </p>
      )}
      {notice && (
        <p className="flex items-start justify-between gap-3 rounded-lg bg-sky-50 px-4 py-3 text-sm text-sky-800">
          <span className="flex items-start gap-2">
            <Info className="mt-0.5 h-4 w-4 shrink-0" /> {notice}
          </span>
          <button onClick={() => setNotice(null)} aria-label="Dismiss"><X className="h-4 w-4" /></button>
        </p>
      )}
      {error && (
        <p className="flex items-start justify-between gap-3 rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">
          <span className="flex items-start gap-2">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" /> {error}
          </span>
          <button onClick={() => setError(null)} aria-label="Dismiss"><X className="h-4 w-4" /></button>
        </p>
      )}

      <div className={`grid gap-4 ${selected.length ? 'xl:grid-cols-[minmax(0,1fr)_360px]' : ''}`}>
        <div className="min-w-0 overflow-x-auto rounded-xl border border-slate-200 bg-white">
          {calQ.isLoading && (
            <div className="grid h-56 place-items-center text-slate-400">
              <Loader2 className="h-6 w-6 animate-spin" />
            </div>
          )}
          {calQ.isError && (
            <p className="px-4 py-10 text-center text-sm text-red-700">
              {apiError(calQ.error)}
            </p>
          )}
          {cal && cal.rows.length === 0 && (
            <p className="px-4 py-14 text-center text-sm text-slate-500">
              No active room types to price.
            </p>
          )}
          {cal && cal.rows.length > 0 && (
            <table className="w-full border-collapse text-sm">
              <thead>
                <tr>
                  <th colSpan={2}
                    className="sticky left-0 z-10 min-w-[230px] border-b border-slate-200 bg-white px-4 py-3 text-left font-semibold text-slate-600">
                    Room Type
                  </th>
                  {cal.dates.map((d) => {
                    const { top, bottom } = dayLabel(d)
                    const on = selected.includes(d)
                    return (
                      <th key={d} onClick={() => { if (!locked) toggleDate(d) }}
                        title={locked
                          ? 'Switch to Room type rate to select dates'
                          : 'Click to select this date for a bulk update'}
                        className={`min-w-[74px] select-none border-b border-slate-200 px-2 py-2 text-center font-semibold transition ${
                          locked ? 'cursor-default' : 'cursor-pointer'} ${
                          on ? 'bg-brand text-white'
                            : isWeekend(d) ? 'bg-amber-50/70 text-slate-600 hover:bg-amber-100/70'
                              : 'bg-white text-slate-600 hover:bg-slate-50'
                        }`}>
                        <div className="whitespace-nowrap text-xs">{top}</div>
                        <div className={`text-xs font-normal ${on ? 'text-white/80' : 'text-slate-400'}`}>
                          {bottom}
                        </div>
                      </th>
                    )
                  })}
                </tr>
                <tr>
                  <th colSpan={2}
                    className="sticky left-0 z-10 border-y border-slate-200 bg-slate-50/70 px-4 py-2 text-left text-sm font-semibold text-slate-600">
                    Occupancy Forecast
                  </th>
                  {cal.dates.map((d) => (
                    <td key={d}
                      className={`border-y border-slate-200 px-2 py-2 text-center text-sm tabular-nums text-slate-600 ${
                        selected.includes(d) ? 'bg-brand-light/60'
                          : isWeekend(d) ? 'bg-amber-50/70' : 'bg-slate-50/70'
                      }`}>
                      {(forecast[d] ?? 0).toFixed(0)}%
                    </td>
                  ))}
                </tr>
              </thead>

              <tbody>
                {cal.rows.map((row) => {
                  // Each room type is four aligned rows, so every sub-row label
                  // sits on the same line as the cells it describes.
                  const tint = (d: string) => selected.includes(d)
                    ? 'bg-brand-light/40' : isWeekend(d) ? 'bg-amber-50/50' : ''
                  const label = 'sticky left-[150px] z-10 whitespace-nowrap bg-white px-3 py-1.5 text-right text-xs font-medium text-slate-500'
                  const cellCls = (d: string) =>
                    `border-l border-slate-100 px-1.5 py-1.5 ${tint(d)}`

                  return (
                    <Fragment key={row.room_type_id}>
                      <tr className="border-t-4 border-slate-100">
                        <th rowSpan={4} scope="rowgroup"
                          className="sticky left-0 z-20 w-[150px] min-w-[150px] bg-white px-4 py-3 text-left align-top">
                          <div className="flex flex-col gap-2">
                            {row.photo_url && (
                              <img src={row.photo_url} alt=""
                                className="h-12 w-16 rounded-lg object-cover" />
                            )}
                            <div>
                              <p className="font-bold leading-tight text-slate-800">
                                {row.room_type_name}
                              </p>
                              <p className="text-xs font-normal text-slate-500">
                                {row.units} unit{row.units === 1 ? '' : 's'}
                              </p>
                            </div>
                          </div>
                        </th>
                        <th scope="row" className={label}>Rate (₹)</th>
                        {row.days.map((c) => (
                          <td key={c.stay_date} className={cellCls(c.stay_date)}>
                            <RateCell cell={c} disabled={cell.isPending || locked}
                              onCommit={(v) => cell.mutate({
                                room_type_id: row.room_type_id, stay_date: c.stay_date,
                                ...(v === null ? { clear_rate: true } : { rate: v }),
                              })} />
                          </td>
                        ))}
                      </tr>

                      <tr>
                        <th scope="row" className={label}>Available</th>
                        {row.days.map((c) => (
                          <td key={c.stay_date}
                            className={`${cellCls(c.stay_date)} text-center`}>
                            <span
                              title={`${c.capacity} units, ${c.reserved} sold or held, ${c.out_of_service} out of service`}
                              className={`text-sm tabular-nums ${
                                c.available === 0 ? 'font-bold text-red-600' : 'text-slate-700'}`}>
                              {c.available}
                            </span>
                          </td>
                        ))}
                      </tr>

                      <tr>
                        <th scope="row" className={label}>Min Stay</th>
                        {row.days.map((c) => (
                          <td key={c.stay_date} className={cellCls(c.stay_date)}>
                            <Select value={c.min_stay} disabled={cell.isPending || locked}
                              aria-label={`Minimum stay for ${row.room_type_name} on ${c.stay_date}`}
                              // A focused <Select> can be changed by a stray
                              // wheel event in some browsers. In a grid this
                              // dense that writes a restriction nobody chose,
                              // so scrolling drops focus instead.
                              onWheel={(e) => (e.target as HTMLSelectElement).blur()}
                              onChange={(e) => cell.mutate({
                                room_type_id: row.room_type_id, stay_date: c.stay_date,
                                min_stay: Number(e.target.value),
                              })}
                              className={`w-full rounded border px-1 py-0.5 text-center text-sm outline-none disabled:opacity-50 ${
                                c.min_stay_is_override
                                  ? 'border-brand/40 bg-brand-light/40 font-semibold text-slate-800'
                                  : 'border-slate-200 bg-white text-slate-600'}`}>
                              {[1, 2, 3, 4, 5, 7].map((n) => <option key={n} value={n}>{n}</option>)}
                            </Select>
                          </td>
                        ))}
                      </tr>

                      <tr>
                        <th scope="row" className={label}>Stop Sell</th>
                        {row.days.map((c) => (
                          <td key={c.stay_date} className={cellCls(c.stay_date)}>
                            <button
                              onClick={() => cell.mutate({
                                room_type_id: row.room_type_id, stay_date: c.stay_date,
                                stop_sell: !c.stop_sell,
                              })}
                              disabled={cell.isPending || locked}
                              role="switch" aria-checked={c.stop_sell}
                              aria-label={`Stop sell ${row.room_type_name} on ${c.stay_date}`}
                              className={`mx-auto flex h-5 w-9 items-center rounded-full p-0.5 transition disabled:opacity-50 ${
                                c.stop_sell ? 'bg-brand' : 'bg-slate-200'}`}>
                              <span className={`h-4 w-4 rounded-full bg-white shadow transition ${
                                c.stop_sell ? 'translate-x-4' : ''}`} />
                            </button>
                          </td>
                        ))}
                      </tr>
                    </Fragment>
                  )
                })}
              </tbody>
            </table>
          )}
        </div>

        {selected.length > 0 && (
          <div className="xl:sticky xl:top-4 xl:max-h-[calc(100vh-6rem)]">
            <BulkPanel propertyId={propertyId} selected={selected}
              roomTypeIds={visibleTypeIds} onClose={() => setSelected([])}
              onApplied={refresh} />
          </div>
        )}
      </div>

      {cal && cal.rows.length > 0 && (
        <p className="text-xs text-slate-400">
          {locked
            ? "Rate plan prices are derived from the room type rate, so they cannot be edited here."
            : "Click a date heading to select it. Highlighted cells were set for that date; clear a rate box to go back to the room type's own rate."}
        </p>
      )}

      {copying && (
        <CopyDialog propertyId={propertyId} from={from} to={to}
          onClose={() => setCopying(false)} onDone={refresh} />
      )}
    </div>
  )
}
