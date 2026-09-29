import { useEffect, useState } from 'react'
import Select from '../components/Select'
import { useNavigate, useParams, Link, useSearchParams } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import DateField from '../components/DateField'
import { fmtDate, fmtWeekday } from '../lib/dates'
import {
  AlertTriangle, ArrowLeft, ArrowRight, BedDouble, CalendarDays, CheckCircle2,
  Info, Loader2, Mail, Moon, Pencil, Phone, Save, Users, XCircle,
} from 'lucide-react'
import {
  getChangeView, getModifyQuote, getCancelQuote, modifyReservation,
  cancelReservation, 
  type ModifyQuote, type CancelQuote, type StaySide,
} from '../api'
import { useActivePropertyId } from '../hooks/useProperty'
import { errorText } from '../lib/forms'

/**
 * Screen 029 — Modify or Cancel Reservation.
 *
 * Both halves quote before they commit. A modification is priced against the
 * same rate resolution the rest of the system uses and re-checks availability,
 * so a change that cannot be accommodated is refused rather than half-applied.
 * A cancellation is priced against the property's written policy, shown beside
 * the number, so the penalty is never a figure the screen invented.
 *
 * A cancellation whose refund exceeds the policy threshold is *requested*, not
 * performed — the booking stays live until a manager decides, because
 * cancelling on an imagined approval would leave a guest with neither a room
 * nor their money.
 */

const money = new Intl.NumberFormat('en-IN', {
  style: 'currency', currency: 'INR', maximumFractionDigits: 0,
})
const day = (s?: string | null) =>
  s ? `${fmtDate(s)} (${fmtWeekday(s)})` : '—'

const input = 'w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700 outline-none focus:border-brand'
const label = 'mb-1 block text-sm font-medium text-slate-600'


/* ------------------------------------------------------------------ page --- */
export default function ModifyReservation() {
  const { reservationId = '' } = useParams()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const propertyId = useActivePropertyId()

  // Deep-linkable, so a list can offer "Cancel booking" and land on the tab
  // that quotes the penalty rather than on the modify form.
  const [params] = useSearchParams()
  const [tab, setTab] = useState<'modify' | 'cancel'>(
    params.get('tab') === 'cancel' ? 'cancel' : 'modify')
  const [form, setForm] = useState({
    arrival_date: '', departure_date: '', room_type_id: '',
    adults: 1, children: 0,
  })
  const [charge, setCharge] = useState(true)
  const [modReason, setModReason] = useState('guest_request')
  const [modNotes, setModNotes] = useState('')
  const [cancelReason, setCancelReason] = useState('travel_plans')
  const [cancelNotes, setCancelNotes] = useState('')
  const [waive, setWaive] = useState(false)
  const [error, setError] = useState('')
  const [done, setDone] = useState<{
    kind: string; status: string; warnings: string[]
  } | null>(null)

  const viewQ = useQuery({
    queryKey: ['changeView', reservationId, propertyId],
    queryFn: () => getChangeView(reservationId, propertyId),
    enabled: reservationId !== '' && propertyId !== '',
    refetchOnWindowFocus: false,
  })
  const v = viewQ.data

  // Seed the proposed stay from the current one, once.
  useEffect(() => {
    if (!v) return
    setForm({
      arrival_date: v.current.arrival_date,
      departure_date: v.current.departure_date,
      room_type_id: v.current.room_type_id ?? '',
      adults: v.current.adults || 1, children: v.current.children,
    })
  }, [v?.reservation_id]) // eslint-disable-line react-hooks/exhaustive-deps

  const changed = Boolean(v) && (
    form.arrival_date !== v!.current.arrival_date
    || form.departure_date !== v!.current.departure_date
    || form.room_type_id !== (v!.current.room_type_id ?? '')
    || Number(form.adults) !== v!.current.adults
    || Number(form.children) !== v!.current.children)

  const quoteQ = useQuery({
    queryKey: ['modifyQuote', reservationId, propertyId, form],
    queryFn: () => getModifyQuote(reservationId, propertyId, {
      arrival_date: form.arrival_date, departure_date: form.departure_date,
      room_type_id: form.room_type_id || null,
      adults: Number(form.adults), children: Number(form.children),
    }),
    enabled: Boolean(v) && form.arrival_date !== '' && changed,
  })
  const quote: ModifyQuote | undefined = quoteQ.data

  const cancelQ = useQuery({
    queryKey: ['cancelQuote', reservationId, propertyId],
    queryFn: () => getCancelQuote(reservationId, propertyId),
    enabled: Boolean(v) && tab === 'cancel',
  })
  const cq: CancelQuote | undefined = cancelQ.data

  function finish(kind: string) {
    return (r: { status: string; warnings: string[] }) => {
      qc.invalidateQueries({ queryKey: ['changeView', reservationId] })
      qc.invalidateQueries({ queryKey: ['reservations'] })
      qc.invalidateQueries({ queryKey: ['rack'] })
      setError('')
      setDone({ kind, status: r.status, warnings: r.warnings })
    }
  }
  function fail(e: unknown) {
    setDone(null)
    setError(errorText(e, 'That could not be completed.'))
  }

  const save = useMutation({
    mutationFn: () => modifyReservation(reservationId, propertyId, {
      arrival_date: form.arrival_date, departure_date: form.departure_date,
      room_type_id: form.room_type_id || null,
      adults: Number(form.adults), children: Number(form.children),
      reason: modReason, notes: modNotes || null, charge_difference: charge,
    }),
    onSuccess: finish('modification'), onError: fail,
  })
  const kill = useMutation({
    mutationFn: () => cancelReservation(reservationId, propertyId, {
      reason: cancelReason, notes: cancelNotes || null, waive_penalty: waive,
    }),
    onSuccess: finish('cancellation'), onError: fail,
  })

  if (viewQ.isLoading) {
    return (
      <p className="flex items-center gap-2 p-8 text-sm text-slate-400">
        <Loader2 size={16} className="animate-spin" /> Loading reservation…
      </p>
    )
  }
  if (viewQ.isError || !v) {
    return (
      <p className="rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700">
        That reservation could not be loaded.
      </p>
    )
  }

  return (
    <div className="space-y-4">
      {/* -------------------------------------------------------- header --- */}
      <div>
        <Link to="/reservations/list"
          className="mb-1 flex items-center gap-1 text-sm text-brand">
          <ArrowLeft size={14} /> Back to Reservations
        </Link>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="flex items-center gap-2 text-display text-ink">
              Modify Reservation {v.number}
              <span className={`rounded-full px-3 py-1 text-xs font-semibold capitalize ${
                v.status === 'cancelled' ? 'bg-rose-100 text-rose-700'
                  : 'bg-emerald-100 text-emerald-700'}`}>
                {v.status}
              </span>
            </h1>
            <p className="mt-1 flex flex-wrap items-center gap-x-4 text-sm text-slate-500">
              <span className="font-medium text-slate-700">
                {v.guest_name ?? 'No guest on file'}
              </span>
              {v.guest_phone && (
                <span className="flex items-center gap-1"><Phone size={12} /> {v.guest_phone}</span>
              )}
              {v.guest_email && (
                <span className="flex items-center gap-1"><Mail size={12} /> {v.guest_email}</span>
              )}
              <span>{v.current.nights} Nights · {v.current.rooms} Room
                {v.current.rooms === 1 ? '' : 's'}</span>
            </p>
          </div>
          <button onClick={() => navigate(`/reservations/${reservationId}`)}
            className="rounded-lg border border-slate-200 px-4 py-2.5 text-sm font-semibold text-slate-600 hover:bg-slate-50">
            View Reservation
          </button>
        </div>
      </div>

      {/* ---------------------------------------------------------- tabs --- */}
      <div className="flex gap-1 border-b border-slate-200">
        {([['modify', 'Modify Stay', Pencil],
           ['cancel', 'Cancel Reservation', XCircle]] as const).map(
          ([k, t, Icon]) => (
            <button key={k} onClick={() => setTab(k)}
              className={`flex items-center gap-2 border-b-2 px-4 py-2.5 text-sm font-semibold ${
                tab === k ? 'border-brand text-brand'
                  : 'border-transparent text-slate-500 hover:text-slate-700'}`}>
              <Icon size={15} /> {t}
            </button>
          ))}
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
        <div className={`rounded-xl px-4 py-3 text-sm ${
          done.status === 'applied' ? 'bg-emerald-50 text-emerald-800'
            : 'bg-amber-50 text-amber-900'}`}>
          <p className="flex items-center gap-2 font-semibold">
            {done.status === 'applied'
              ? <><CheckCircle2 size={16} /> {done.kind === 'cancellation'
                  ? 'Reservation cancelled.' : 'Modification saved.'}</>
              : <><Info size={16} /> Cancellation requested — the booking is
                  still live until a manager approves.</>}
          </p>
          {done.warnings.map((w) => (
            <p key={w} className="mt-1 flex items-start gap-2">
              <Info size={14} className="mt-0.5 shrink-0" /> {w}
            </p>
          ))}
          <button onClick={() => navigate('/reservations/list')}
            className="mt-2 rounded-lg bg-brand px-3 py-1.5 text-xs font-semibold text-white">
            Back to Reservations
          </button>
        </div>
      )}

      {!done && tab === 'modify' && (
        <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_340px]">
          {/* ------------------------------------------- comparison --- */}
          <section className="rounded-2xl border border-slate-100 bg-white p-5 shadow-sm">
            <h2 className="text-lg font-semibold text-ink">
              Stay Details Comparison
            </h2>
            <p className="mb-4 text-sm text-slate-500">
              Review the changes and the price difference before saving.
            </p>

            <div className="grid items-start gap-3 lg:grid-cols-[1fr_auto_1fr]">
              <StayCard title="Current Stay" side={v.current} />
              <span className="hidden place-self-center rounded-full bg-brand p-2 text-white lg:grid">
                <ArrowRight size={16} />
              </span>
              <div className="rounded-xl border border-amber-200 bg-amber-50/50">
                <p className="rounded-t-xl bg-amber-100/70 px-4 py-2.5 font-semibold text-amber-900">
                  Proposed Stay
                </p>
                <div className="space-y-3 p-4">
                  <div className="grid gap-3 sm:grid-cols-2">
                    <label>
                      <span className={label}>Check-in</span>
                      <DateField value={form.arrival_date} onChange={(v) => setForm((f) => ({ ...f, arrival_date: v }))} disabled={!v.can_modify} className={input} />
                    </label>
                    <label>
                      <span className={label}>Check-out</span>
                      <DateField value={form.departure_date} onChange={(v) => setForm((f) => ({ ...f, departure_date: v }))} disabled={!v.can_modify} className={input} />
                    </label>
                  </div>
                  <label>
                    <span className={label}>Room Type</span>
                    <Select className={input} value={form.room_type_id}
                      disabled={!v.can_modify}
                      onWheel={(e) => (e.target as HTMLSelectElement).blur()}
                      onChange={(e) => setForm((f) => ({ ...f, room_type_id: e.target.value }))}>
                      {v.room_types.map((t) => (
                        <option key={t.id} value={t.id}>{t.name}</option>
                      ))}
                    </Select>
                  </label>
                  <div className="grid gap-3 sm:grid-cols-2">
                    <label>
                      <span className={label}>Adults</span>
                      <input type="number" min={1} className={input} value={form.adults}
                        disabled={!v.can_modify}
                        onChange={(e) => setForm((f) => ({ ...f, adults: Number(e.target.value) }))} />
                    </label>
                    <label>
                      <span className={label}>Children</span>
                      <input type="number" min={0} className={input} value={form.children}
                        disabled={!v.can_modify}
                        onChange={(e) => setForm((f) => ({ ...f, children: Number(e.target.value) }))} />
                    </label>
                  </div>
                  {quote && (
                    <p className="flex justify-between border-t border-amber-200 pt-2 text-sm">
                      <span className="text-amber-900">Total Stay Amount</span>
                      <strong className="tabular-nums text-amber-900">
                        {money.format(quote.proposed.total_amount)}
                        <span className="ml-1 text-xs font-normal">
                          ({money.format(quote.proposed.nightly_rate)} ×{' '}
                          {quote.proposed.nights} nights)
                        </span>
                      </strong>
                    </p>
                  )}
                </div>
              </div>
            </div>

            {/* difference banner */}
            {!changed ? (
              <p className="mt-4 rounded-xl bg-slate-50 px-4 py-3 text-sm text-slate-500">
                Change a date, room type or guest count to see the difference.
              </p>
            ) : quoteQ.isLoading || !quote ? (
              <p className="mt-4 py-3 text-center text-slate-400">
                <Loader2 size={16} className="mx-auto animate-spin" />
              </p>
            ) : !quote.available ? (
              <p className="mt-4 flex items-start gap-2 rounded-xl bg-rose-50 px-4 py-3 text-sm text-rose-800">
                <AlertTriangle size={16} className="mt-0.5 shrink-0" />
                <span>
                  <strong className="block font-semibold">
                    That stay is not available.
                  </strong>
                  {quote.unavailable_reason}
                </span>
              </p>
            ) : (
              <div className={`mt-4 flex flex-wrap items-center justify-between gap-3 rounded-xl px-4 py-3 ${
                quote.difference > 0 ? 'bg-emerald-50' : quote.difference < 0
                  ? 'bg-sky-50' : 'bg-slate-50'}`}>
                <span className="text-sm">
                  <strong className="block font-semibold text-slate-800">
                    Difference {quote.difference > 0 ? '(Additional Charge)'
                      : quote.difference < 0 ? '(Refund Due)' : ''}
                  </strong>
                  <span className="text-slate-600">
                    {quote.difference > 0
                      ? 'New stay amount is higher than the current amount.'
                      : quote.difference < 0
                        ? 'New stay amount is lower than the current amount.'
                        : 'The price is unchanged.'}
                  </span>
                </span>
                <strong className={`text-2xl tabular-nums ${
                  quote.difference > 0 ? 'text-positive'
                    : quote.difference < 0 ? 'text-sky-700' : 'text-slate-700'}`}>
                  {quote.difference > 0 ? '+' : ''}{money.format(quote.difference)}
                </strong>
              </div>
            )}
          </section>

          {/* --------------------------------- modification summary --- */}
          <section className="rounded-2xl border border-slate-100 bg-white p-5 shadow-sm">
            <h2 className="mb-3 font-semibold text-ink">
              Modification Summary
            </h2>
            <label className="mb-3 block">
              <span className={label}>Reason</span>
              <Select className={input} value={modReason}
                onWheel={(e) => (e.target as HTMLSelectElement).blur()}
                onChange={(e) => setModReason(e.target.value)}>
                {v.cancel_reasons.map((r) => (
                  <option key={r.code} value={r.code}>{r.label}</option>
                ))}
              </Select>
            </label>
            <label className="mb-3 block">
              <span className={label}>Notes</span>
              <textarea className={`${input} h-20 resize-none`} value={modNotes}
                maxLength={600}
                onChange={(e) => setModNotes(e.target.value)} />
            </label>
            {quote && quote.difference !== 0 && (
              <label className="flex cursor-pointer items-start gap-2 text-sm">
                <input type="checkbox" checked={charge} className="mt-0.5"
                  onChange={(e) => setCharge(e.target.checked)} />
                <span className="text-slate-700">
                  Post the difference to the folio
                  {v.folio_id === null && (
                    <span className="block text-xs text-amber-700">
                      This reservation has no folio yet, so nothing will post.
                    </span>
                  )}
                </span>
              </label>
            )}
            <p className="mt-4 flex items-start gap-2 rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-500">
              <Info size={13} className="mt-0.5 shrink-0" />
              Availability is re-checked when you save. If a room is already
              assigned it will be released, because the new dates may not suit it.
            </p>
            <div className="mt-4 flex gap-2">
              <button onClick={() => navigate('/reservations/list')}
                className="flex-1 rounded-lg border border-slate-200 px-4 py-2.5 text-sm font-semibold text-slate-600 hover:bg-slate-50">
                Cancel
              </button>
              <button
                disabled={!v.can_modify || !changed || !quote?.available || save.isPending}
                onClick={() => save.mutate()}
                className="flex flex-1 items-center justify-center gap-2 rounded-lg bg-brand px-4 py-2.5 text-sm font-semibold text-white hover:bg-brand-dark disabled:opacity-50">
                {save.isPending ? <Loader2 size={15} className="animate-spin" />
                  : <Save size={15} />} Save Modification
              </button>
            </div>
          </section>
        </div>
      )}

      {!done && tab === 'cancel' && (
        <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_340px]">
          <section className="rounded-2xl border border-slate-100 bg-white p-5 shadow-sm">
            <h2 className="mb-3 text-lg font-semibold text-ink">
              Cancellation Details
            </h2>
            {cancelQ.isLoading || !cq ? (
              <p className="py-8 text-center text-slate-400">
                <Loader2 size={16} className="mx-auto animate-spin" />
              </p>
            ) : (
              <>
                <p className="mb-4 flex items-start gap-3 rounded-xl bg-rose-50 px-4 py-3 text-sm">
                  <AlertTriangle size={18} className="mt-0.5 shrink-0 text-rose-600" />
                  <span>
                    <strong className="block font-semibold text-rose-800">
                      Cancellation Policy
                    </strong>
                    <span className="text-rose-900">{cq.policy.policy_text}</span>
                  </span>
                </p>
                <dl className="space-y-1.5 text-sm">
                  <Row label="Original Check-in" value={day(cq.original.arrival_date)} />
                  <Row label="Original Check-out" value={day(cq.original.departure_date)} />
                  <Row label="Rooms"
                    value={`${cq.original.rooms} × ${cq.original.room_type}`} />
                  <Row label="Days before arrival"
                    value={cq.days_before_arrival >= 0
                      ? `${cq.days_before_arrival}`
                      : `${Math.abs(cq.days_before_arrival)} days after arrival`} />
                  <div className="flex items-start justify-between rounded-lg bg-slate-50 px-3 py-2">
                    <dt className="text-slate-600">
                      Applicable Penalty
                      <span className="block text-xs text-slate-400">
                        {cq.within_penalty_window
                          ? `${cq.policy.penalty_nights} night per room`
                          : 'Outside the penalty window'}
                      </span>
                    </dt>
                    <dd className="font-semibold tabular-nums text-slate-800">
                      {money.format(waive ? 0 : cq.penalty_amount)}
                    </dd>
                  </div>
                  <div className="flex items-start justify-between rounded-lg bg-emerald-50 px-3 py-2">
                    <dt className="text-emerald-800">
                      Refund Estimate
                      <span className="block text-xs text-positive">
                        Total paid {money.format(cq.amount_paid)} − penalty{' '}
                        {money.format(waive ? 0 : cq.penalty_amount)}
                      </span>
                    </dt>
                    <dd className="font-semibold tabular-nums text-emerald-800">
                      {money.format(Math.max(
                        cq.amount_paid - (waive ? 0 : cq.penalty_amount), 0))}
                    </dd>
                  </div>
                </dl>
                {v.can_approve && cq.penalty_amount > 0 && (
                  <label className="mt-3 flex cursor-pointer items-start gap-2 text-sm">
                    <input type="checkbox" checked={waive} className="mt-0.5"
                      onChange={(e) => setWaive(e.target.checked)} />
                    <span className="text-slate-700">
                      Waive the penalty
                      <span className="block text-xs text-slate-400">
                        Needs reservations approve permission, which you have.
                      </span>
                    </span>
                  </label>
                )}
              </>
            )}
          </section>

          <section className="rounded-2xl border border-slate-100 bg-white p-5 shadow-sm">
            <h2 className="mb-3 font-semibold text-ink">Cancel Reservation</h2>
            <label className="mb-3 block">
              <span className={label}>Reason for Cancellation</span>
              <Select className={input} value={cancelReason}
                onWheel={(e) => (e.target as HTMLSelectElement).blur()}
                onChange={(e) => setCancelReason(e.target.value)}>
                {v.cancel_reasons.map((r) => (
                  <option key={r.code} value={r.code}>{r.label}</option>
                ))}
              </Select>
            </label>
            <label className="mb-3 block">
              <span className={label}>Additional Notes <span className="text-slate-400">(optional)</span></span>
              <textarea className={`${input} h-20 resize-none`} value={cancelNotes}
                maxLength={600}
                onChange={(e) => setCancelNotes(e.target.value)} />
            </label>

            {cq?.requires_approval && !v.can_approve && (
              <p className="mb-3 flex items-start gap-2 rounded-lg bg-amber-50 px-3 py-2.5 text-sm text-amber-900">
                <Info size={15} className="mt-0.5 shrink-0" />
                <span>
                  <strong className="block font-semibold">Manager approval required</strong>
                  A refund above {money.format(cq.policy.approval_above)} needs a
                  manager. The booking will stay live until one decides.
                </span>
              </p>
            )}
            <p className="mb-3 flex items-start gap-2 rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-500">
              <Info size={13} className="mt-0.5 shrink-0" />
              Cancelling releases the rooms and returns the nights to inventory.
              Any refund has to be processed from the folio — this does not move
              money back on its own.
            </p>
            <button
              disabled={!v.can_cancel || kill.isPending || cancelQ.isLoading}
              onClick={() => kill.mutate()}
              className="flex w-full items-center justify-center gap-2 rounded-lg border-2 border-rose-500 px-4 py-2.5 text-sm font-semibold text-rose-600 hover:bg-rose-50 disabled:opacity-50">
              {kill.isPending ? <Loader2 size={15} className="animate-spin" />
                : <XCircle size={15} />}
              {cq?.requires_approval && !v.can_approve
                ? 'Request Cancellation Approval' : 'Cancel Reservation'}
            </button>
          </section>
        </div>
      )}
    </div>
  )
}

/* ------------------------------------------------------------------ bits --- */
function StayCard({ title, side }: { title: string; side: StaySide }) {
  return (
    <div className="rounded-xl border border-sky-200 bg-sky-50/40">
      <p className="rounded-t-xl bg-sky-100/70 px-4 py-2.5 font-semibold text-sky-900">
        {title}
      </p>
      <dl className="space-y-2.5 p-4 text-sm">
        <Line icon={<CalendarDays size={14} />} label="Check-in"
          value={day(side.arrival_date)} />
        <Line icon={<CalendarDays size={14} />} label="Check-out"
          value={day(side.departure_date)} />
        <Line icon={<Moon size={14} />} label="Nights" value={`${side.nights} Nights`} />
        <Line icon={<BedDouble size={14} />} label="Room Type" value={side.room_type} />
        <Line icon={<Users size={14} />} label="Guests"
          value={`${side.adults} Adults${side.children ? `, ${side.children} Children` : ''}`} />
        <div className="flex items-start justify-between border-t border-slate-200 pt-2">
          <dt className="text-slate-500">Total Stay Amount</dt>
          <dd className="text-right">
            <span className="font-semibold tabular-nums text-slate-800">
              {money.format(side.total_amount)}
            </span>
            <span className="block text-xs text-slate-400">
              ({money.format(side.nightly_rate)} × {side.nights} nights)
            </span>
          </dd>
        </div>
      </dl>
    </div>
  )
}

function Line({ icon, label: l, value }: {
  icon: React.ReactNode; label: string; value: string
}) {
  return (
    <div className="flex items-start justify-between gap-3">
      <dt className="flex items-center gap-2 text-slate-500">{icon} {l}</dt>
      <dd className="text-right font-medium text-slate-800">{value}</dd>
    </div>
  )
}

function Row({ label: l, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between">
      <dt className="text-slate-500">{l}</dt>
      <dd className="text-right font-medium text-slate-800">{value}</dd>
    </div>
  )
}
