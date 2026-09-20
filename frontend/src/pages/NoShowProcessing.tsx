import { useState } from 'react'
import Select from '../components/Select'
import { useNavigate, useParams, Link } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { fmtDate, fmtWeekday } from '../lib/dates'
import {
  AlertTriangle, BedDouble, CalendarClock, CheckCircle2, Info,
  Loader2, Receipt, UserX,
} from 'lucide-react'
import {
  listNoShowCandidates, getNoShowView, markNoShow, 
} from '../api'
import { Crumbs } from '../components/Crumbs'
import { useActivePropertyId } from '../hooks/useProperty'

/**
 * Screen 051 — No-Show Processing.
 *
 * A no-show is not a cancellation. The guest did not call it off; they simply
 * did not come, and the difference matters — to the penalty, and to any later
 * question about why a room sat empty. So the unit moves to `no_show`, a status
 * the schema has always allowed and nothing had ever set.
 *
 * The penalty and its tax come from the property's own policy and tax rules, so
 * the figure here and the figure on the guest's bill are the same figure.
 * Charging nothing is a waiver, and the button for it only appears to someone
 * who holds the right to waive.
 */

const money = new Intl.NumberFormat('en-IN', {
  style: 'currency', currency: 'INR', maximumFractionDigits: 0,
})
const exact = new Intl.NumberFormat('en-IN', {
  style: 'currency', currency: 'INR', minimumFractionDigits: 2,
})
const day = (s?: string | null) => {
  if (!s) return '—'
  const d = new Date(`${s.slice(0, 10)}T00:00:00`)
  return fmtDate(d)
}
const longDay = (s?: string | null) =>
  s ? `${day(s)} (${fmtWeekday(s)})` : '—'

const input = 'w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700 outline-none focus:border-brand'
const label = 'mb-1 block text-sm font-medium text-slate-600'


/* ------------------------------------------------------------ candidates --- */
export function NoShowList() {
  const navigate = useNavigate()
  const propertyId = useActivePropertyId()
  const q = useQuery({
    queryKey: ['noShowCandidates', propertyId],
    queryFn: () => listNoShowCandidates(propertyId),
    enabled: propertyId !== '',
  })
  const rows = q.data ?? []

  return (
    <div className="space-y-4">
      <Crumbs title="No-show Processing" trail={[
        { label: 'Reservations', to: '/reservations' },
        { label: 'No-show Processing' }]} />
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <h1 className="flex items-center gap-2 text-display text-ink">
          <UserX size={26} className="text-brand" /> No-show Processing
        </h1>
      </div>

      <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
        <table className="w-full min-w-[760px] text-left">
          <thead>
            <tr className="border-b border-slate-200 bg-slate-50/60 text-sm text-slate-600">
              {['Reservation', 'Guest', 'Room Type', 'Expected', 'Overdue',
                'Stay Value', 'Paid', ''].map((h) => (
                <th key={h} className="px-4 py-3 font-semibold">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {q.isLoading && (
              <tr><td colSpan={8} className="px-4 py-12 text-center text-slate-400">
                <Loader2 size={18} className="mx-auto animate-spin" />
              </td></tr>
            )}
            {!q.isLoading && rows.length === 0 && (
              <tr><td colSpan={8} className="px-4 py-12 text-center text-sm text-slate-500">
                No missed arrivals. Every guest due so far has either checked in
                or been dealt with.
              </td></tr>
            )}
            {rows.map((r) => (
              <tr key={r.reservation_unit_id}
                className="border-b border-slate-100 last:border-0 hover:bg-slate-50">
                <td className="px-4 py-3 font-medium text-slate-800">{r.number}</td>
                <td className="px-4 py-3">
                  {r.guest_name
                    ? <span className="text-slate-700">{r.guest_name}</span>
                    : <span className="italic text-slate-400">No guest on file</span>}
                </td>
                <td className="px-4 py-3 text-sm text-slate-600">{r.room_type}</td>
                <td className="whitespace-nowrap px-4 py-3 text-sm text-slate-600">
                  {day(r.arrival_date)}
                </td>
                <td className="px-4 py-3">
                  <span className="rounded-full bg-amber-100 px-2.5 py-1 text-xs font-medium text-caution">
                    {r.days_overdue} day{r.days_overdue === 1 ? '' : 's'}
                  </span>
                </td>
                <td className="px-4 py-3 text-sm tabular-nums text-slate-700">
                  {money.format(r.total_amount)}
                </td>
                <td className="px-4 py-3 text-sm tabular-nums text-emerald-600">
                  {money.format(r.paid)}
                </td>
                <td className="px-4 py-3 text-right">
                  <button onClick={() => navigate(
                    `/reservations/no-show/${r.reservation_unit_id}`)}
                    className="rounded-lg bg-brand px-3 py-1.5 text-sm font-semibold text-white hover:bg-brand-dark">
                    Process
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

/* --------------------------------------------------------------- process --- */
export default function NoShowProcessing() {
  const { unitId = '' } = useParams()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const propertyId = useActivePropertyId()

  const [basis, setBasis] = useState('one_night_tax')
  const [deposit, setDeposit] = useState('retain_as_penalty')
  const [release, setRelease] = useState(true)
  const [reason, setReason] = useState('did_not_arrive')
  const [notes, setNotes] = useState('')
  const [error, setError] = useState('')
  const [done, setDone] = useState<{
    penalty: number; refund: number; room: string | null; warnings: string[]
  } | null>(null)

  const q = useQuery({
    queryKey: ['noShowView', unitId, propertyId],
    queryFn: () => getNoShowView(unitId, propertyId),
    enabled: unitId !== '' && propertyId !== '',
    refetchOnWindowFocus: false,
  })
  const v = q.data
  const chosen = v?.penalty_options.find((o) => o.code === basis)

  const refundPreview = (() => {
    if (!v || !chosen) return 0
    if (deposit === 'refund_full') return v.advance_received
    if (deposit === 'retain_full') return 0
    return Math.max(v.advance_received - chosen.total, 0)
  })()

  const mark = useMutation({
    mutationFn: () => markNoShow(unitId, propertyId, {
      penalty_basis: basis, deposit_action: deposit, release_room: release,
      reason, notes: notes || null,
    }),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ['noShowCandidates'] })
      qc.invalidateQueries({ queryKey: ['arrivals'] })
      qc.invalidateQueries({ queryKey: ['rack'] })
      setError('')
      setDone({ penalty: r.penalty_charged, refund: r.refund_due,
        room: r.room_released, warnings: r.warnings })
    },
    onError: (e) => {
      const er = e as { response?: { data?: { detail?: string } } }
      setError(er.response?.data?.detail ?? 'That could not be completed.')
    },
  })

  if (q.isLoading) {
    return (
      <p className="flex items-center gap-2 p-8 text-sm text-slate-400">
        <Loader2 size={16} className="animate-spin" /> Loading…
      </p>
    )
  }
  if (q.isError || !v) {
    return (
      <p className="rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700">
        That arrival could not be loaded.
      </p>
    )
  }

  return (
    <div className="space-y-4">
      <div>
        <p className="text-sm text-slate-400">
          <Link to="/reservations/no-show" className="text-slate-500 hover:text-brand">
            No-show Processing
          </Link>
          <span className="px-2">›</span>
          <span className="font-semibold text-slate-700">{v.number}</span>
        </p>
        <div className="mt-1 flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="text-display text-ink">No-show Processing</h1>
            <p className="text-slate-500">
              Handle missed arrivals, apply penalties, and release inventory.
            </p>
          </div>
          <span className="flex items-center gap-2 rounded-full bg-amber-50 px-4 py-2 text-sm font-semibold text-caution">
            <CalendarClock size={16} /> Expected Arrival Missed
          </span>
        </div>
      </div>

      {/* Guest strip */}
      <div className="flex flex-wrap gap-x-10 gap-y-4 rounded-2xl border border-slate-100 bg-white p-4 shadow-sm">
        <div>
          <p className="text-lg font-semibold text-slate-800">
            {v.guest_name ?? <span className="italic text-slate-400">No guest on file</span>}
          </p>
          <p className="text-sm text-slate-500">
            {[v.guest_email, v.guest_phone].filter(Boolean).join(' · ') || v.number}
          </p>
          {v.guest_city && <p className="text-xs text-slate-400">{v.guest_city}</p>}
        </div>
        <Head label="Check-in (Expected)" value={longDay(v.arrival_date)} />
        <Head label="Check-out" value={longDay(v.departure_date)} />
        <Head label="Nights" value={String(v.nights)} />
        <Head label="Room Type" value={v.room_type}
          sub={v.room ? `Room ${v.room}` : 'No room assigned'} />
        <Head label="Adults / Children" value={`${v.adults} / ${v.children}`} />
      </div>

      {v.blocked_reason && (
        <p className="flex items-start gap-2 rounded-xl bg-amber-50 px-4 py-3 text-sm text-amber-900">
          <Info size={16} className="mt-0.5 shrink-0" /> {v.blocked_reason}
        </p>
      )}
      {error && (
        <p className="flex items-start gap-2 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700">
          <AlertTriangle size={16} className="mt-0.5 shrink-0" /> {error}
        </p>
      )}
      {done && (
        <div className="rounded-xl bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
          <p className="flex items-center gap-2 font-semibold">
            <CheckCircle2 size={16} /> Marked as a no-show.
            {done.penalty > 0 && <> Penalty {money.format(done.penalty)} charged.</>}
            {done.room && <> Room {done.room} released.</>}
          </p>
          {done.warnings.map((w) => (
            <p key={w} className="mt-1 flex items-start gap-2">
              <Info size={14} className="mt-0.5 shrink-0" /> {w}
            </p>
          ))}
          <button onClick={() => navigate('/reservations/no-show')}
            className="mt-2 rounded-lg bg-brand px-3 py-1.5 text-xs font-semibold text-white">
            Back to missed arrivals
          </button>
        </div>
      )}

      {!done && (
        <>
          <div className="grid gap-4 xl:grid-cols-3">
            {/* --------------------------------------- financial summary --- */}
            <section className="rounded-2xl border border-slate-100 bg-white p-5 shadow-sm">
              <h2 className="mb-3 flex items-center gap-2 font-semibold text-ink">
                <Receipt size={17} className="text-brand" /> Stay &amp; Financial Summary
              </h2>
              <dl className="space-y-2 text-sm">
                <Row label="Rate (per night)" value={exact.format(v.nightly_rate)} />
                <Row label={`Total Room Charges (${v.nights} nights)`}
                  value={exact.format(v.room_charges)} />
                <Row label="Taxes & levies" value={exact.format(v.taxes)}
                  sub={v.tax_label} />
                <div className="flex justify-between border-t border-slate-100 pt-2 font-semibold text-slate-800">
                  <dt>Total Stay Amount</dt>
                  <dd className="tabular-nums">{exact.format(v.total_stay_amount)}</dd>
                </div>
                <Row label="Advance / Payments Received"
                  value={exact.format(v.advance_received)} tone="emerald" />
                <div className="flex justify-between border-t border-slate-100 pt-2 font-semibold text-slate-800">
                  <dt>Balance Due (if stay proceeds)</dt>
                  <dd className="tabular-nums">{exact.format(v.balance_if_proceeds)}</dd>
                </div>
              </dl>
            </section>

            {/* ------------------------------------------ penalty --- */}
            <section className="rounded-2xl border border-slate-100 bg-white p-5 shadow-sm">
              <h2 className="mb-3 font-semibold text-ink">
                No-show Penalty Calculation
              </h2>
              <p className="mb-3 flex items-start gap-2 rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-600">
                <Info size={13} className="mt-0.5 shrink-0" />
                <span><strong className="font-semibold">Hotel policy: </strong>
                  {v.policy_text}</span>
              </p>
              <label className="mb-3 block">
                <span className={label}>Penalty Basis</span>
                <Select className={input} value={basis}
                  onWheel={(e) => (e.target as HTMLSelectElement).blur()}
                  onChange={(e) => setBasis(e.target.value)}>
                  {v.penalty_options
                    .filter((o) => o.code !== 'none' || v.can_waive)
                    .map((o) => (
                      <option key={o.code} value={o.code}>{o.label}</option>
                    ))}
                </Select>
                {!v.can_waive && (
                  <span className="mt-1 block text-xs text-slate-400">
                    Waiving the penalty needs approve permission.
                  </span>
                )}
              </label>
              {chosen && (
                <div className="rounded-lg bg-slate-50 px-4 py-3">
                  <p className="text-sm text-slate-500">Penalty Amount</p>
                  <p className="text-2xl font-bold tabular-nums text-slate-800">
                    {exact.format(chosen.total)}
                  </p>
                  {chosen.tax_amount > 0 && (
                    <p className="text-xs text-slate-500">
                      ({exact.format(chosen.base_amount)} +{' '}
                      {exact.format(chosen.tax_amount)} tax)
                    </p>
                  )}
                </div>
              )}
            </section>

            {/* --------------------------- room release + deposit --- */}
            <div className="space-y-4">
              <section className="rounded-2xl border border-slate-100 bg-white p-5 shadow-sm">
                <h2 className="mb-3 flex items-center gap-2 font-semibold text-ink">
                  <BedDouble size={17} className="text-brand" /> Room Release &amp; Inventory
                </h2>
                <label className="flex cursor-pointer items-start gap-2 text-sm">
                  <input type="checkbox" checked={release} className="mt-0.5"
                    onChange={(e) => setRelease(e.target.checked)} />
                  <span className="text-slate-700">
                    Release the room to inventory
                    <span className="block text-xs text-slate-400">
                      The nights become sellable again immediately.
                    </span>
                  </span>
                </label>
                {release && (
                  <p className="mt-3 flex items-start gap-2 rounded-lg bg-emerald-50 px-3 py-2.5 text-sm text-emerald-800">
                    <CheckCircle2 size={15} className="mt-0.5 shrink-0" />
                    <span>
                      {v.room ? <><strong>Room {v.room}</strong> will be released</>
                        : 'No room was assigned'} — {v.nights} night
                      {v.nights === 1 ? '' : 's'} returned to {v.room_type}.
                    </span>
                  </p>
                )}
              </section>

              <section className="rounded-2xl border border-slate-100 bg-white p-5 shadow-sm">
                <h2 className="mb-3 font-semibold text-ink">
                  Refund / Deposit Handling
                </h2>
                <label className="mb-3 block">
                  <span className={label}>Action on Advance / Deposit</span>
                  <Select className={input} value={deposit}
                    onWheel={(e) => (e.target as HTMLSelectElement).blur()}
                    onChange={(e) => setDeposit(e.target.value)}>
                    {v.deposit_actions.map((d) => (
                      <option key={d.code} value={d.code}>{d.label}</option>
                    ))}
                  </Select>
                </label>
                <div className="flex justify-between rounded-lg bg-slate-50 px-3 py-2 text-sm">
                  <span className="text-slate-500">Refund Amount</span>
                  <strong className="tabular-nums text-slate-800">
                    {exact.format(refundPreview)}
                  </strong>
                </div>
                <p className="mt-1.5 text-xs text-slate-400">
                  {deposit === 'retain_as_penalty'
                    ? 'The advance is set against the penalty; anything over comes back.'
                    : deposit === 'refund_full'
                      ? 'The whole advance is returned.'
                      : 'Nothing is returned.'}
                  {' '}Any refund is processed from the folio — this screen does
                  not move money.
                </p>
              </section>
            </div>
          </div>

          {/* ------------------------------------------ reason + action --- */}
          <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
            <section className="rounded-2xl border border-slate-100 bg-white p-5 shadow-sm">
              <h2 className="mb-3 font-semibold text-ink">Reason &amp; Notes</h2>
              <div className="grid gap-3 sm:grid-cols-2">
                <label>
                  <span className={label}>
                    Reason for No-show <span className="text-rose-500">*</span>
                  </span>
                  <Select className={input} value={reason}
                    onWheel={(e) => (e.target as HTMLSelectElement).blur()}
                    onChange={(e) => setReason(e.target.value)}>
                    {v.reasons.map((r) => (
                      <option key={r.code} value={r.code}>{r.label}</option>
                    ))}
                  </Select>
                </label>
                <label>
                  <span className={label}>Additional Notes</span>
                  <textarea className={`${input} h-20 resize-none`} value={notes}
                    maxLength={500}
                    onChange={(e) => setNotes(e.target.value)} />
                  <span className="mt-1 block text-right text-xs text-slate-400">
                    {notes.length}/500
                  </span>
                </label>
              </div>
            </section>

            <section className="flex flex-col justify-between rounded-2xl border border-slate-100 bg-white p-5 shadow-sm">
              <p className="flex items-start gap-2 text-xs text-slate-500">
                <Info size={13} className="mt-0.5 shrink-0" />
                Marking a no-show charges the penalty to the folio, frees the
                room and closes the booking out. It cannot be undone from here.
              </p>
              <div className="mt-4 space-y-2">
                <button onClick={() => navigate('/reservations/no-show')}
                  className="w-full rounded-lg border border-slate-200 px-4 py-2.5 text-sm font-semibold text-slate-600 hover:bg-slate-50">
                  Keep Reservation
                </button>
                <button disabled={Boolean(v.blocked_reason) || mark.isPending}
                  onClick={() => mark.mutate()}
                  className="flex w-full items-center justify-center gap-2 rounded-lg bg-brand px-4 py-2.5 text-sm font-semibold text-white hover:bg-brand-dark disabled:opacity-50">
                  {mark.isPending ? <Loader2 size={15} className="animate-spin" />
                    : <UserX size={15} />} Mark No-show
                </button>
              </div>
            </section>
          </div>
        </>
      )}
    </div>
  )
}

/* ------------------------------------------------------------------ bits --- */
function Head({ label: l, value, sub }: {
  label: string; value: string; sub?: string | null
}) {
  return (
    <div>
      <p className="text-xs text-slate-400">{l}</p>
      <p className="font-semibold text-slate-800">{value}</p>
      {sub && <p className="text-xs text-slate-500">{sub}</p>}
    </div>
  )
}

function Row({ label: l, value, sub, tone }: {
  label: string; value: string; sub?: string; tone?: 'emerald'
}) {
  return (
    <div className="flex items-start justify-between gap-3">
      <dt className="text-slate-500">
        {l}
        {sub && <span className="block text-xs text-slate-400">{sub}</span>}
      </dt>
      <dd className={`shrink-0 tabular-nums ${
        tone === 'emerald' ? 'text-emerald-600' : 'text-slate-800'}`}>
        {value}
      </dd>
    </div>
  )
}
