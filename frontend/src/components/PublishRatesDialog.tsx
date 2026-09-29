import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, CalendarCheck, Loader2, Lock, X } from 'lucide-react'
import DateField from './DateField'
import { notify } from './AskDialog'
import {
  previewRulePublish, publishRules,
  type PublishDay, type PublishPreview,
} from '../api'
import { errorText } from '../lib/forms'

/**
 * Turn the rules into the prices the system actually quotes.
 *
 * A rate rule used to describe weekend and festival pricing and change nothing
 * anybody paid: the booking engine, the OTA push and the front desk all read
 * the rate calendar, and nothing wrote rules into it. This is the step that
 * does, so it shows exactly what will change before it changes it.
 *
 * Three things the preview is careful to say out loud, because each is a way
 * somebody could be surprised by their own prices:
 *
 * * **Hand-typed nights are left alone.** Someone set 4,500 for a wedding
 *   party; a rule that computes 2,600 does not know about the wedding. Those
 *   are listed, not hidden, so nobody wonders why one night ignored the rule.
 * * **Nights a rule no longer covers are cleared, not left.** Otherwise last
 *   season's surcharge quietly outlives the rule that made it.
 * * **A night no rule touches is not written at all.** Writing the base rate
 *   into the calendar would pin it — raise the room's price later and those
 *   nights would still sell at the old one.
 */

const money = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 })
const rupees = (v: string | null) => v === null ? '—' : `₹${money.format(Number(v))}`

function iso(d: Date) { return d.toISOString().slice(0, 10) }

export default function PublishRatesDialog({ propertyId, onClose }: {
  propertyId: string; onClose: () => void
}) {
  const qc = useQueryClient()
  const today = new Date()
  const in30 = new Date(today.getTime() + 30 * 86400000)
  const [from, setFrom] = useState(iso(today))
  const [to, setTo] = useState(iso(in30))
  const [confirmed, setConfirmed] = useState<PublishPreview | null>(null)

  const preview = useQuery({
    queryKey: ['publish-preview', propertyId, from, to],
    queryFn: () => previewRulePublish(propertyId, { date_from: from, date_to: to }),
    enabled: propertyId !== '' && from !== '' && to !== '' && from <= to,
    retry: false,
  })

  const apply = useMutation({
    mutationFn: () => publishRules(propertyId, { date_from: from, date_to: to }),
    onSuccess: (r) => {
      void qc.invalidateQueries({ queryKey: ['rateCalendar'] })
      void qc.invalidateQueries({ queryKey: ['publish-preview'] })
      notify(r.message, 'Rates published')
      onClose()
    },
    onError: (e) => {
      notify(errorText(e, 'Nothing was published.'),
        'That did not go through')
    },
  })

  const data = preview.data
  // Only the nights that actually move are worth a row. A month across a dozen
  // room types is thousands of lines, almost all of them "no change".
  const rows: PublishDay[] = useMemo(
    () => (data?.days ?? []).filter((d) => d.action === 'write'
      || d.action === 'clear' || d.skipped !== null)
      .filter((d) => d.action !== 'write' || d.current_rate !== d.new_rate),
    [data],
  )
  const err = preview.error as { response?: { data?: { detail?: string } } } | null

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-slate-900/50 p-4"
      role="dialog" aria-modal="true" onClick={onClose}>
      <div className="flex max-h-[90vh] w-full max-w-3xl flex-col rounded-2xl bg-white shadow-xl"
        onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start justify-between border-b border-slate-100 px-5 py-4">
          <div>
            <h2 className="flex items-center gap-2 text-base font-semibold text-ink">
              <CalendarCheck size={18} className="text-brand" /> Publish rules to the calendar
            </h2>
            <p className="mt-0.5 text-sm text-slate-500">
              Your published rules become the rates the booking engine, the
              front desk and your channels all quote.
            </p>
          </div>
          <button onClick={onClose} aria-label="Close"
            className="rounded-lg p-1 text-slate-400 hover:bg-slate-100">
            <X size={18} />
          </button>
        </div>

        <div className="flex flex-wrap items-end gap-3 border-b border-slate-100 px-5 py-3">
          <div>
            <label className="mb-1 block text-xs font-medium text-slate-500">From</label>
            <DateField value={from} onChange={setFrom} max={to || undefined}
              label="Publish from" />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-slate-500">To</label>
            <DateField value={to} onChange={setTo} min={from || undefined}
              label="Publish to" />
          </div>
          {data && (
            <div className="ml-auto flex flex-wrap items-center gap-2 text-xs">
              <Chip tone="brand" n={data.changing} label="changing" />
              <Chip tone="slate" n={data.clearing} label="back to base" />
              <Chip tone="amber" n={data.protected} label="set by hand" />
              <Chip tone="slate" n={data.unchanged} label="unchanged" />
            </div>
          )}
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-3">
          {preview.isLoading && (
            <Loader2 className="mx-auto my-10 animate-spin text-slate-400" />
          )}
          {err && (
            <p className="flex items-start gap-2 rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">
              <AlertTriangle size={16} className="mt-0.5 shrink-0" />
              {errorText(err, 'That range could not be previewed.')}
            </p>
          )}

          {data && rows.length === 0 && (
            <p className="py-10 text-center text-sm text-slate-400">
              Nothing would change in this range. Either no published rule
              covers it, or its rates are already on the calendar.
            </p>
          )}

          {rows.length > 0 && (
            <table className="w-full text-sm">
              <thead className="border-b border-slate-100">
                <tr className="text-left text-xs font-semibold uppercase tracking-wide text-slate-500">
                  <th className="whitespace-nowrap py-2">Date</th>
                  <th className="whitespace-nowrap py-2">Room type</th>
                  <th className="whitespace-nowrap py-2 text-right">Now</th>
                  <th className="whitespace-nowrap py-2 text-right">Sold</th>
                  <th className="whitespace-nowrap py-2 text-right">Becomes</th>
                  <th className="whitespace-nowrap py-2 pl-4">Why</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-50">
                {rows.map((d) => (
                  <tr key={`${d.room_type_id}-${d.stay_date}`}
                    className={d.skipped ? 'text-slate-400' : 'text-slate-700'}>
                    <td className="whitespace-nowrap py-2 tabular-nums">{d.stay_date}</td>
                    <td className="py-2">{d.room_type_name}</td>
                    <td className="py-2 text-right tabular-nums">
                      {rupees(d.current_rate)}
                    </td>
                    <td className="py-2 text-right tabular-nums text-xs text-slate-400">
                      {d.occupancy === null ? '—' : `${d.occupancy}%`}
                    </td>
                    <td className="py-2 text-right font-semibold tabular-nums">
                      {d.skipped ? rupees(d.current_rate) : rupees(d.new_rate)}
                    </td>
                    <td className="py-2 pl-4">
                      {d.skipped ? (
                        <span className="flex items-center gap-1 text-xs text-caution">
                          <Lock size={12} /> {d.skipped}
                        </span>
                      ) : d.action === 'clear' ? (
                        <span className="text-xs text-slate-500">
                          No rule covers it now — back to the room's base rate
                        </span>
                      ) : (
                        <span className="text-xs font-medium text-brand">
                          {d.applied.join(' → ')}
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        <div className="flex items-center justify-between gap-3 border-t border-slate-100 px-5 py-3">
          <p className="text-xs text-slate-400">
            Nights you priced by hand are never overwritten. Rules that
            depend on how full a date is keep re-pricing as rooms sell.
          </p>
          <div className="flex items-center gap-2">
            <button onClick={onClose}
              className="rounded-lg border border-slate-200 px-3 py-2 text-sm font-semibold text-slate-600 hover:bg-slate-50">
              Cancel
            </button>
            <button
              onClick={() => { setConfirmed(data ?? null); apply.mutate() }}
              disabled={apply.isPending || !data
                || (data.changing === 0 && data.clearing === 0)}
              className="flex items-center gap-2 rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white hover:bg-brand-dark disabled:opacity-50">
              {apply.isPending && <Loader2 size={14} className="animate-spin" />}
              {data && (data.changing || data.clearing)
                ? `Publish ${data.changing + data.clearing} night(s)`
                : 'Publish'}
            </button>
          </div>
        </div>
        {confirmed === null && null}
      </div>
    </div>
  )
}

function Chip({ n, label, tone }: {
  n: number; label: string; tone: 'brand' | 'amber' | 'slate'
}) {
  if (!n) return null
  const cls = {
    brand: 'bg-brand-light text-brand',
    amber: 'bg-amber-50 text-caution',
    slate: 'bg-slate-75 text-slate-500',
  }[tone]
  return (
    <span className={`rounded-full px-2 py-1 font-semibold tabular-nums ${cls}`}>
      {n} {label}
    </span>
  )
}
