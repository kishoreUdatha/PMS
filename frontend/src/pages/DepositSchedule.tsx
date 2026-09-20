import { useMemo, useState } from 'react'
import Select from '../components/Select'
import { useParams, useNavigate, Link } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import DateField from '../components/DateField'
import { fmtDate } from '../lib/dates'
import {
  AlertTriangle, ArrowLeft, BellRing, Check, CheckCircle2, Info, Loader2,
  Plus, Printer, Receipt, RotateCcw, ShieldQuestion, Undo2, Wallet, X,
} from 'lucide-react'
import {
  getReservationDetail, getFolioView, getDepositSchedule,
  generateDepositSchedule, listProperties,
  addDepositInstallment, updateDepositInstallment, cancelDepositInstallment,
  collectDeposit, refundDeposit, waiveDeposit, decideDepositWaiver,
  setDepositReminder, DUE_RULES,
  type DepositInstallment, type DepositSchedule, type InstallmentInput,
} from '../api'
import { useActivePropertyId } from '../hooks/useProperty'
import { usePaymentMethods } from '../lib/paymentMethods'

/**
 * Screen 058 — Deposit Schedule.
 *
 * The schedule is the plan; the ledger is the money. Every figure in the
 * "Received" column comes from a payment that exists in Payments and posted a
 * credit to the folio, so this screen can never claim a guest paid something
 * the folio disagrees with.
 *
 * Two panels say plainly what the system can and cannot do yet. A reminder is
 * *recorded*, not sent — nothing here mails anybody — and a waiver over the
 * threshold is *requested*, leaving the balance untouched until someone who can
 * approve it does. Showing either as done would be the screen lying about the
 * state of the booking.
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
const today = () => new Date().toISOString().slice(0, 10)
const num = (v: unknown) => Number(v ?? 0)

const STATUS_STYLE: Record<string, string> = {
  paid: 'bg-emerald-100 text-emerald-700',
  partially_paid: 'bg-amber-100 text-amber-800',
  pending: 'bg-slate-200 text-slate-600',
  waived: 'bg-sky-100 text-sky-700',
  cancelled: 'bg-slate-75 text-slate-400 line-through',
}

const input = 'w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700'
const label = 'mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500'

/**
 * The tab strip from the mockup. A tab is a link only where there is something
 * real to open; the rest are greyed with a note saying why, rather than going
 * nowhere. Targets depend on the reservation, so this is built per render.
 */
function buildTabs(opts: {
  reservationId: string
  roomId: string | null
  auditHref: string | null
}) {
  const { reservationId, roomId, auditHref } = opts
  return [
    { key: 'overview', label: 'Overview',
      to: `/reservations/${reservationId}` },
    { key: 'guest', label: 'Guest Details', to: null,
      why: 'There is no per-guest profile screen yet.' },
    { key: 'room', label: 'Room Details',
      to: roomId ? `/rooms/${roomId}` : null,
      why: 'No room is assigned to this reservation yet.' },
    { key: 'folio', label: 'Folio', to: `/reservations/${reservationId}/folio` },
    { key: 'deposits', label: 'Deposit Schedule',
      to: `/reservations/${reservationId}/deposits` },
    { key: 'payments', label: 'Payments', to: null,
      why: 'There is no per-reservation payments screen yet.' },
    { key: 'documents', label: 'Documents', to: null,
      why: 'Document storage for a reservation is not built yet.' },
    { key: 'audit', label: 'Audit Trail', to: auditHref },
  ]
}

/** The default split the mockup shows: 20 / 10 / balance on arrival. */
const DEFAULT_SPLITS: InstallmentInput[] = [
  { label: 'Booking Deposit (20%)', percent: 20, due_rule: 'at_booking',
    due_date: today() },
  { label: 'Second Installment (10%)', percent: 10, due_rule: 'fixed_date',
    due_date: null },
  { label: 'Balance on Arrival (70%)', percent: 70, due_rule: 'at_checkin' },
]


/** The org that owns the active property — writes are scoped to it. */
function useOrganizationId(propertyId: string): string {
  const { data } = useQuery({ queryKey: ['properties'], queryFn: listProperties })
  return data?.find((p) => p.id === propertyId)?.organization_id ?? ''
}

/* ------------------------------------------------------------------ page --- */
export default function DepositSchedule() {
  const { reservationId = '' } = useParams()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const propertyId = useActivePropertyId()
  const organizationId = useOrganizationId(propertyId)

  const [showCancelled, setShowCancelled] = useState(false)
  const [panel, setPanel] = useState<'' | 'add' | 'collect' | 'waive' | 'refund'>('')
  const [editing, setEditing] = useState<DepositInstallment | null>(null)
  const [focused, setFocused] = useState<string>('')
  const [toast, setToast] = useState('')
  const [error, setError] = useState('')

  const resQ = useQuery({
    queryKey: ['reservationDetail', reservationId],
    queryFn: () => getReservationDetail(reservationId),
    enabled: reservationId !== '',
  })
  const bookingValue = num(resQ.data?.booking_value)

  // The folio belongs to the reservation, not to the schedule, so it is
  // resolved the same way the Folio tab resolves it. Without this the first
  // instalment would be created with nowhere to post a payment to.
  const folioQ = useQuery({
    queryKey: ['folioView', reservationId],
    queryFn: () => getFolioView(reservationId),
    enabled: reservationId !== '', retry: false,
  })

  const schedQ = useQuery({
    queryKey: ['depositSchedule', reservationId, propertyId, bookingValue,
      showCancelled],
    queryFn: () => getDepositSchedule(reservationId, propertyId, bookingValue,
      showCancelled),
    enabled: reservationId !== '' && propertyId !== '' && resQ.isSuccess,
  })

  const res = resQ.data
  const sched = schedQ.data
  const folioId = sched?.folio_id ?? folioQ.data?.folio_id ?? null
  const rows = sched?.installments ?? []
  const arrival = res?.units[0]?.arrival_date ?? null
  const departure = res?.units.reduce(
    (a, u) => (u.departure_date > a ? u.departure_date : a),
    res?.units[0]?.departure_date ?? '') || null
  const nights = res?.units.reduce((a, u) => Math.max(a, u.nights), 0) ?? 0
  const guests = res?.units.reduce(
    (a, u) => ({ adults: a.adults + u.adults, children: a.children + u.children }),
    { adults: 0, children: 0 })

  function flash(m: string) { setError(''); setToast(m); setTimeout(() => setToast(''), 3000) }
  function fail(e: unknown) {
    const er = e as { response?: { data?: { detail?: string } } }
    setToast('')
    setError(er.response?.data?.detail ?? 'That did not work. Please try again.')
  }
  function done(message: string) {
    return () => {
      qc.invalidateQueries({ queryKey: ['depositSchedule', reservationId] })
      qc.invalidateQueries({ queryKey: ['folioView', reservationId] })
      setPanel('')
      setEditing(null)
      flash(message)
    }
  }

  const generate = useMutation({
    mutationFn: (splits: InstallmentInput[]) => generateDepositSchedule(
      reservationId,
      { propertyId, organizationId },
      { booking_value: bookingValue, arrival_date: arrival,
        folio_id: folioId, splits },
    ),
    onSuccess: done('Deposit schedule created.'), onError: fail,
  })
  const add = useMutation({
    mutationFn: (body: InstallmentInput) => addDepositInstallment(
      reservationId,
      { propertyId, organizationId, bookingValue, folioId },
      body),
    onSuccess: done('Instalment added.'), onError: fail,
  })
  const edit = useMutation({
    mutationFn: ({ id, body }: { id: string; body: InstallmentInput & { version: number } }) =>
      updateDepositInstallment(id, propertyId, bookingValue, body),
    onSuccess: done('Instalment updated.'), onError: fail,
  })
  const drop = useMutation({
    mutationFn: (i: DepositInstallment) =>
      cancelDepositInstallment(i.id, propertyId, i.version),
    onSuccess: done('Instalment cancelled.'), onError: fail,
  })
  const collect = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Parameters<typeof collectDeposit>[3] }) =>
      collectDeposit(id, propertyId, folioId, body),
    onSuccess: done('Payment collected and posted to the folio.'), onError: fail,
  })
  const refund = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Parameters<typeof refundDeposit>[2] }) =>
      refundDeposit(id, propertyId, body),
    onSuccess: done('Refund recorded and posted to the folio.'), onError: fail,
  })
  const waive = useMutation({
    mutationFn: ({ id, body }: { id: string; body: { amount: number; reason: string } }) =>
      waiveDeposit(id, propertyId, body),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ['depositSchedule', reservationId] })
      setPanel('')
      flash(r.waiver_status === 'requested'
        ? 'Waiver requested. The balance is unchanged until it is approved.'
        : 'Waiver applied.')
    },
    onError: fail,
  })
  const decide = useMutation({
    mutationFn: ({ id, decision }: { id: string; decision: 'approved' | 'rejected' }) =>
      decideDepositWaiver(id, propertyId, { decision }),
    onSuccess: done('Waiver decision recorded.'), onError: fail,
  })
  const remind = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Parameters<typeof setDepositReminder>[2] }) =>
      setDepositReminder(id, propertyId, body),
    onSuccess: done('Reminder updated.'), onError: fail,
  })

  // The trail for a reservation is the reservation row plus its instalments:
  // deposit events are recorded against the instalment they happened to.
  const auditIds = [reservationId, ...rows.map((i) => i.id)]
  const tabs = buildTabs({
    reservationId,
    roomId: res?.units.find((u) => u.assigned_room_id)?.assigned_room_id ?? null,
    // Always available: an empty trail is an answer ("nothing has happened to
    // this reservation yet"), and the audit page says so and offers a way out.
    auditHref: `/admin/audit?entity_id=${auditIds.join(',')}`
      + `&scope_label=${encodeURIComponent(`Reservation ${res?.number ?? ''}`)}`,
  })

  const collectible = rows.filter((i) => i.balance_due > 0 && i.status !== 'cancelled')
  const waivable = rows.filter((i) => i.balance_due > 0 && i.status !== 'cancelled')
  const refundable = useMemo(
    () => rows.flatMap((i) => i.allocations
      .filter((a) => !a.is_reversal && !a.reversed)
      .map((a) => ({ installment: i, alloc: a }))),
    [rows])
  const pendingWaivers = rows.filter((i) => i.waiver_status === 'requested')
  const totals = sched?.totals

  if (resQ.isLoading || (schedQ.isLoading && resQ.isSuccess)) {
    return (
      <p className="flex items-center gap-2 p-8 text-sm text-slate-400">
        <Loader2 className="h-4 w-4 animate-spin" /> Loading deposit schedule…
      </p>
    )
  }
  if (resQ.isError || !res) {
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
        <p className="text-sm text-slate-400">
          <Link to="/reservations" className="text-slate-500 hover:text-brand">
            Reservations
          </Link>
          <span className="px-2">›</span>
          <span className="text-slate-500">{res.number}</span>
          <span className="px-2">›</span>
          <span className="font-semibold text-slate-700">Deposit Schedule</span>
        </p>
        <div className="mt-1 flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="flex items-center gap-2 text-display text-ink">
              Reservation {res.number}
              <span className="rounded-full bg-emerald-100 px-3 py-1 text-xs font-semibold capitalize text-emerald-700">
                {res.status}
              </span>
            </h1>
            <p className="text-slate-500">
              {res.guest_name ?? 'No guest on file'}
              {guests && guests.adults > 0 && (
                <> · {guests.adults} Adult{guests.adults === 1 ? '' : 's'}
                  {guests.children > 0
                    && `, ${guests.children} Child${guests.children === 1 ? '' : 'ren'}`}
                </>
              )}
              {res.units[0] && <> · {res.units[0].room_type}</>}
              {arrival && <> · {day(arrival)} – {day(departure)} ({nights} Night{nights === 1 ? '' : 's'})</>}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button onClick={() => navigate('/reservations')}
              className="flex items-center gap-2 rounded-lg border border-slate-200 px-4 py-2.5 text-sm font-semibold text-slate-600 hover:bg-slate-50">
              <ArrowLeft className="h-4 w-4" /> View Reservation
            </button>
            <button onClick={() => { setPanel('collect'); setFocused(collectible[0]?.id ?? '') }}
              disabled={collectible.length === 0}
              className="flex items-center gap-2 rounded-lg bg-brand px-4 py-2.5 text-sm font-semibold text-white hover:bg-brand-dark disabled:opacity-50">
              <Wallet className="h-4 w-4" /> Collect Payment
            </button>
          </div>
        </div>
      </div>

      {/* ---------------------------------------------------------- tabs --- */}
      <div className="flex gap-1 overflow-x-auto border-b border-slate-200">
        {tabs.map((t) => {
          const active = t.key === 'deposits'
          if (!t.to) {
            return (
              <span key={t.key} title={t.why ?? 'This tab is not built yet.'}
                className="cursor-not-allowed whitespace-nowrap px-4 py-2.5 text-sm text-slate-300">
                {t.label}
              </span>
            )
          }
          return (
            <Link key={t.key} to={t.to}
              className={`whitespace-nowrap border-b-2 px-4 py-2.5 text-sm font-semibold ${
                active ? 'border-brand text-brand'
                  : 'border-transparent text-slate-500 hover:text-slate-700'}`}>
              {t.label}
            </Link>
          )
        })}
      </div>

      {toast && (
        <p className="flex items-center gap-2 rounded-xl bg-emerald-50 px-4 py-3 text-sm text-emerald-700">
          <CheckCircle2 className="h-4 w-4 shrink-0" />{toast}
        </p>
      )}
      {error && (
        <p className="flex items-start gap-2 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{error}</span>
          <button onClick={() => setError('')} className="ml-auto text-red-400">
            <X className="h-4 w-4" />
          </button>
        </p>
      )}

      {/* ---------------------------------------------------------- KPIs --- */}
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
        <Kpi title="Total Booking Value" value={money.format(bookingValue)}
          note={`${nights} night${nights === 1 ? '' : 's'} of room revenue`} />
        <Kpi title="Total Deposit Required"
          value={money.format(num(totals?.deposit_required))}
          note={totals?.deposit_percent != null
            ? `${num(totals.deposit_percent)}% of the booking` : 'No schedule yet'} />
        <Kpi title="Total Received" value={money.format(num(totals?.received))}
          tone="emerald"
          note={totals?.received_percent != null
            ? `${num(totals.received_percent)}% of what is scheduled` : '—'} />
        <Kpi title="Balance Due" value={money.format(num(totals?.balance_due))}
          tone={num(totals?.balance_due) > 0 ? 'red' : 'emerald'}
          note={num(totals?.waived) > 0
            ? `after ${money.format(num(totals?.waived))} waived` : 'across all instalments'} />
        <Kpi title="Next Due Date"
          value={totals?.next_due_date ? day(totals.next_due_date) : '—'}
          tone={totals?.next_due_in_days != null && totals.next_due_in_days < 0
            ? 'red' : 'slate'}
          note={dueNote(totals?.next_due_in_days)} />
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
        <div className="min-w-0 space-y-4">
          {/* ------------------------------------------------------ table --- */}
          <section className="rounded-xl border border-slate-200 bg-white">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 px-4 py-3">
              <h2 className="flex items-center gap-2 font-semibold text-ink">
                <Receipt className="h-4 w-4 text-brand" /> Deposit Schedule
              </h2>
              <label className="flex items-center gap-2 text-sm text-slate-500">
                <input type="checkbox" checked={showCancelled}
                  onChange={(e) => setShowCancelled(e.target.checked)} />
                Show cancelled
              </label>
            </div>

            {rows.length === 0 ? (
              <EmptySchedule bookingValue={bookingValue} arrival={arrival}
                busy={generate.isPending}
                onCreate={(splits) => generate.mutate(splits)} />
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[880px] text-left">
                  <thead>
                    <tr className="border-b border-slate-200 bg-slate-50/60 text-sm text-slate-600">
                      <th className="px-3 py-3 font-semibold">#</th>
                      <th className="px-3 py-3 font-semibold">Instalment</th>
                      <th className="px-3 py-3 text-right font-semibold">Amount</th>
                      <th className="px-3 py-3 font-semibold">Due Date</th>
                      <th className="px-3 py-3 font-semibold">Status</th>
                      <th className="px-3 py-3 font-semibold">Received On</th>
                      <th className="px-3 py-3 font-semibold">Method</th>
                      <th className="px-3 py-3 font-semibold">Reminder</th>
                      <th className="px-3 py-3 text-right font-semibold">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((i) => (
                      <Row key={i.id} row={i}
                        canApprove={sched?.can_approve_waiver ?? false}
                        folioHref={folioId
                          ? `/reservations/${reservationId}/folio` : null}
                        onCollect={() => { setFocused(i.id); setPanel('collect') }}
                        onEdit={() => { setEditing(i); setPanel('add') }}
                        onWaive={() => { setFocused(i.id); setPanel('waive') }}
                        onCancel={() => drop.mutate(i)}
                        onDecide={(d) => decide.mutate({ id: i.id, decision: d })}
                        onRemind={(body) => remind.mutate({ id: i.id, body })} />
                    ))}
                  </tbody>
                  <tfoot>
                    <tr className="border-t-2 border-slate-200 bg-slate-50/60 font-semibold text-slate-700">
                      <td className="px-3 py-3" colSpan={2}>Total</td>
                      <td className="px-3 py-3 text-right tabular-nums">
                        {money.format(num(totals?.deposit_required))}
                      </td>
                      <td className="px-3 py-3 text-sm font-normal text-slate-500">
                        {totals?.deposit_percent != null
                          && `${num(totals.deposit_percent)}% of booking`}
                      </td>
                      <td className="px-3 py-3" />
                      <td className="px-3 py-3 text-right tabular-nums text-emerald-600" colSpan={2}>
                        {money.format(num(totals?.received))} received
                      </td>
                      <td className="px-3 py-3 text-right tabular-nums text-red-600" colSpan={2}>
                        {money.format(num(totals?.balance_due))} due
                      </td>
                    </tr>
                  </tfoot>
                </table>
              </div>
            )}

            {rows.length > 0 && (
              <div className="flex flex-wrap gap-2 border-t border-slate-200 px-4 py-3">
                <Action icon={<Plus className="h-4 w-4" />} label="Add Installment"
                  onClick={() => { setEditing(null); setPanel('add') }} />
                <Action icon={<Wallet className="h-4 w-4" />} label="Collect Payment"
                  disabled={collectible.length === 0}
                  onClick={() => { setFocused(collectible[0]?.id ?? ''); setPanel('collect') }} />
                <Action icon={<ShieldQuestion className="h-4 w-4" />} label="Waive Deposit"
                  disabled={waivable.length === 0}
                  onClick={() => { setFocused(waivable[0]?.id ?? ''); setPanel('waive') }} />
                <Action icon={<Undo2 className="h-4 w-4" />} label="Refund / Reverse"
                  disabled={refundable.length === 0}
                  onClick={() => setPanel('refund')} />
                <Action icon={<Printer className="h-4 w-4" />} label="Print Schedule"
                  onClick={() => window.print()} />
              </div>
            )}
          </section>

          {/* ----------------------------------------------------- panels --- */}
          {panel === 'add' && (
            <InstallmentForm
              title={editing ? `Edit ${editing.label}` : 'Add Installment'}
              initial={editing} bookingValue={bookingValue}
              busy={add.isPending || edit.isPending}
              onClose={() => { setPanel(''); setEditing(null) }}
              onSubmit={(body) => (editing
                ? edit.mutate({ id: editing.id,
                                body: { ...body, version: editing.version } })
                : add.mutate(body))} />
          )}
          {panel === 'collect' && (
            <CollectForm rows={collectible} focused={focused} setFocused={setFocused}
              busy={collect.isPending} onClose={() => setPanel('')}
              onSubmit={(id, body) => collect.mutate({ id, body })} />
          )}
          {panel === 'waive' && (
            <WaiveForm rows={waivable} focused={focused} setFocused={setFocused}
              threshold={num(sched?.waiver_threshold)}
              canApprove={sched?.can_approve_waiver ?? false}
              busy={waive.isPending} onClose={() => setPanel('')}
              onSubmit={(id, body) => waive.mutate({ id, body })} />
          )}
          {panel === 'refund' && (
            <RefundForm options={refundable} busy={refund.isPending}
              onClose={() => setPanel('')}
              onSubmit={(id, body) => refund.mutate({ id, body })} />
          )}
        </div>

        {/* ------------------------------------------------------ side rail --- */}
        <aside className="space-y-4">
          <section className="rounded-xl border border-slate-200 bg-white p-4">
            <h3 className="mb-1 flex items-center gap-2 font-semibold text-ink">
              <BellRing className="h-4 w-4 text-brand" /> Reminder Status
            </h3>
            <p className="mb-3 text-xs text-slate-500">
              Reminders are recorded here, not sent. This system has no mail or
              SMS transport yet, so a reminder is either a date you have noted or
              a note that someone sent one.
            </p>
            {rows.length === 0 && <p className="text-sm text-slate-400">No instalments yet.</p>}
            <ol className="space-y-2.5">
              {rows.map((i) => (
                <li key={i.id} className="flex gap-2.5 text-sm">
                  <span className={`mt-1 h-2 w-2 shrink-0 rounded-full ${
                    i.reminder_state === 'sent' ? 'bg-emerald-500'
                      : i.reminder_state === 'scheduled' ? 'bg-sky-500' : 'bg-slate-300'}`} />
                  <span>
                    <span className="font-medium text-slate-700">{i.label}</span>
                    <span className="block text-slate-500">{i.reminder_label}</span>
                  </span>
                </li>
              ))}
            </ol>
          </section>

          <section className="rounded-xl border border-amber-200 bg-amber-50 p-4">
            <h3 className="mb-1 flex items-center gap-2 font-semibold text-amber-900">
              <Info className="h-4 w-4" /> Approval Required
            </h3>
            <p className="text-sm text-amber-900">
              Waivers above {money.format(num(sched?.waiver_threshold) || 5000)} need
              a decision from a Front Office Manager or General Manager. Until one
              is made the balance stays as it is — a requested waiver has not
              happened yet.
            </p>
            {sched && !sched.can_approve_waiver && (
              <p className="mt-2 text-xs text-caution">
                You can request a waiver but not decide one.
              </p>
            )}
            {pendingWaivers.length > 0 && (
              <ul className="mt-3 space-y-2">
                {pendingWaivers.map((i) => (
                  <li key={i.id} className="rounded-lg bg-white p-2.5 text-sm">
                    <p className="font-medium text-slate-700">{i.label}</p>
                    <p className="text-slate-500">
                      {exact.format(i.waived_amount)} — {i.waiver_reason}
                    </p>
                    {sched?.can_approve_waiver && (
                      <div className="mt-2 flex gap-2">
                        <button
                          onClick={() => decide.mutate({ id: i.id, decision: 'approved' })}
                          className="flex items-center gap-1 rounded-md bg-emerald-600 px-2.5 py-1 text-xs font-semibold text-white hover:bg-emerald-700">
                          <Check className="h-3 w-3" /> Approve
                        </button>
                        <button
                          onClick={() => decide.mutate({ id: i.id, decision: 'rejected' })}
                          className="flex items-center gap-1 rounded-md border border-slate-200 px-2.5 py-1 text-xs font-semibold text-slate-600 hover:bg-slate-50">
                          <X className="h-3 w-3" /> Reject
                        </button>
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="rounded-xl border border-slate-200 bg-white p-4">
            <h3 className="mb-1 font-semibold text-ink">Where the money went</h3>
            <p className="text-sm text-slate-500">
              Every collection here posts a credit to this reservation's folio and
              appears in Payments. Nothing on this screen holds money of its own.
            </p>
            {folioId ? (
              <Link to={`/reservations/${reservationId}/folio`}
                className="mt-2 inline-flex items-center gap-1.5 text-sm font-semibold text-brand">
                <Receipt className="h-4 w-4" /> Open the folio
              </Link>
            ) : (
              <p className="mt-2 text-sm text-amber-700">
                This reservation has no folio yet, so nothing can be collected
                against it. Open it from Reservations to have one created.
              </p>
            )}
          </section>
        </aside>
      </div>
    </div>
  )
}

/* ---------------------------------------------------------------- bits --- */
function dueNote(days?: number | null): string {
  if (days == null) return 'Nothing outstanding'
  if (days < 0) return `Overdue by ${Math.abs(days)} day${Math.abs(days) === 1 ? '' : 's'}`
  if (days === 0) return 'Due today'
  return `In ${days} day${days === 1 ? '' : 's'}`
}

function Kpi({ title, value, note, tone = 'slate' }: {
  title: string; value: string; note?: string; tone?: 'slate' | 'emerald' | 'red'
}) {
  const colour = tone === 'emerald' ? 'text-emerald-600'
    : tone === 'red' ? 'text-red-600' : 'text-slate-800'
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4">
      <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
        {title}
      </p>
      <p className={`mt-1 text-2xl font-bold tabular-nums ${colour}`}>{value}</p>
      {note && <p className="text-xs text-slate-500">{note}</p>}
    </div>
  )
}

function Action({ icon, label: text, onClick, disabled }: {
  icon: React.ReactNode; label: string; onClick: () => void; disabled?: boolean
}) {
  return (
    <button onClick={onClick} disabled={disabled}
      className="flex items-center gap-2 rounded-lg border border-slate-200 px-3 py-2 text-sm font-semibold text-slate-600 hover:bg-slate-50 disabled:opacity-40">
      {icon} {text}
    </button>
  )
}

function Row({ row, canApprove, folioHref, onCollect, onEdit, onWaive, onCancel,
  onDecide, onRemind }: {
  row: DepositInstallment
  canApprove: boolean
  folioHref: string | null
  onCollect: () => void
  onEdit: () => void
  onWaive: () => void
  onCancel: () => void
  onDecide: (d: 'approved' | 'rejected') => void
  onRemind: (body: { action: 'schedule' | 'mark_sent' | 'clear'; due_on?: string }) => void
}) {
  const partial = row.status === 'partially_paid'
  return (
    <>
      <tr className="border-b border-slate-100 align-top">
        <td className="px-3 py-3 text-sm text-slate-500">{row.seq}</td>
        <td className="px-3 py-3">
          <p className="font-medium text-slate-800">{row.label}</p>
          {row.notes && <p className="text-sm text-slate-500">{row.notes}</p>}
          {row.waiver_status === 'requested' && (
            <p className="mt-0.5 text-xs font-semibold text-amber-700">
              Waiver of {exact.format(row.waived_amount)} awaiting approval —
              balance unchanged
            </p>
          )}
          {row.waiver_status === 'approved' && row.waived_amount > 0 && (
            <p className="mt-0.5 text-xs text-sky-700">
              {exact.format(row.waived_amount)} waived — {row.waiver_reason}
            </p>
          )}
        </td>
        <td className="px-3 py-3 text-right font-medium tabular-nums text-slate-800">
          {money.format(row.amount)}
          {row.percent != null && (
            <span className="block text-xs font-normal text-slate-400">
              {num(row.percent)}%
            </span>
          )}
        </td>
        <td className="whitespace-nowrap px-3 py-3 text-sm text-slate-600">
          {row.due_label}
        </td>
        <td className="px-3 py-3">
          <span className={`whitespace-nowrap rounded-full px-3 py-1 text-xs font-semibold ${
            STATUS_STYLE[row.status] ?? 'bg-slate-75 text-slate-600'}`}>
            {row.status_label}
          </span>
        </td>
        <td className="whitespace-nowrap px-3 py-3 text-sm text-slate-600">
          {day(row.received_on)}
        </td>
        <td className="px-3 py-3 text-sm text-slate-600">
          {row.payment_method ?? '—'}
        </td>
        <td className="whitespace-nowrap px-3 py-3 text-sm">
          <p className="text-slate-600">{row.reminder_label}</p>
          {row.status !== 'cancelled' && row.balance_due > 0 && (
            <button onClick={() => onRemind({ action: 'mark_sent' })}
              className="text-xs font-semibold text-brand hover:underline">
              Mark sent
            </button>
          )}
        </td>
        <td className="whitespace-nowrap px-3 py-3 text-right text-sm">
          <div className="flex flex-col items-end gap-1">
            {row.balance_due > 0 && row.status !== 'cancelled' && (
              <button onClick={onCollect}
                className="font-semibold text-brand hover:underline">
                Collect
              </button>
            )}
            {/* The mockup's "View Receipt". A receipt is a folio document,
                so it opens the folio rather than inventing a second one. */}
            {row.paid_amount > 0 && folioHref && (
              <Link to={folioHref} className="text-slate-500 hover:underline">
                Receipt
              </Link>
            )}
            {row.balance_due > 0 && row.status !== 'cancelled'
              && row.waiver_status !== 'requested' && (
              <button onClick={onWaive} className="text-slate-500 hover:underline">
                Waive
              </button>
            )}
            {row.waiver_status === 'requested' && canApprove && (
              <span className="flex gap-2">
                <button onClick={() => onDecide('approved')}
                  className="font-semibold text-emerald-600 hover:underline">Approve</button>
                <button onClick={() => onDecide('rejected')}
                  className="text-slate-500 hover:underline">Reject</button>
              </span>
            )}
            {row.status !== 'cancelled' && (
              <button onClick={onEdit} className="text-slate-500 hover:underline">
                Edit
              </button>
            )}
            {row.paid_amount === 0 && row.status !== 'cancelled' && (
              <button onClick={onCancel} className="text-slate-400 hover:underline">
                Cancel
              </button>
            )}
          </div>
        </td>
      </tr>
      {/* The mockup's sub-row: what is settled and what is still owed. */}
      {(partial || row.allocations.length > 0) && (
        <tr className="border-b border-slate-100 bg-slate-50/40 text-sm">
          <td />
          <td className="px-3 pb-3 text-slate-500" colSpan={8}>
            <span className="mr-4">
              Paid Amount{' '}
              <strong className="font-semibold text-slate-700">
                {exact.format(row.paid_amount)}
              </strong>
            </span>
            <span className="mr-4">
              Balance Due{' '}
              <strong className={`font-semibold ${
                row.balance_due > 0 ? 'text-red-600' : 'text-emerald-600'}`}>
                {exact.format(row.balance_due)}
              </strong>
            </span>
            {row.allocations.map((a) => (
              <span key={a.id} className="mr-3 text-xs text-slate-400">
                {a.is_reversal ? 'Refund' : a.method} {exact.format(Math.abs(a.amount))}
                {' '}on {day(a.received_on)}
                {a.reference && ` · ${a.reference}`}
                {a.reversed && ' · refunded'}
              </span>
            ))}
          </td>
        </tr>
      )}
    </>
  )
}

/* ------------------------------------------------------------- no rows --- */
function EmptySchedule({ bookingValue, arrival, busy, onCreate }: {
  bookingValue: number; arrival: string | null; busy: boolean
  onCreate: (splits: InstallmentInput[]) => void
}) {
  const splits = DEFAULT_SPLITS.map((s) => ({
    ...s,
    due_date: s.due_rule === 'fixed_date'
      ? (arrival ? shiftDays(arrival, -3) : today())
      : s.due_date,
  }))
  return (
    <div className="px-4 py-10 text-center">
      <p className="text-sm text-slate-500">
        This reservation has no deposit schedule yet.
      </p>
      {bookingValue > 0 ? (
        <>
          <p className="mt-1 text-sm text-slate-400">
            The standard split of {money.format(bookingValue)} is 20% at booking,
            10% three days before arrival, and the balance on check-in.
          </p>
          <button onClick={() => onCreate(splits)} disabled={busy}
            className="mt-4 inline-flex items-center gap-2 rounded-lg bg-brand px-4 py-2.5 text-sm font-semibold text-white hover:bg-brand-dark disabled:opacity-50">
            {busy ? <Loader2 className="h-4 w-4 animate-spin" />
              : <Plus className="h-4 w-4" />}
            Create the standard schedule
          </button>
        </>
      ) : (
        <p className="mt-1 text-sm text-amber-700">
          The booking has no room revenue yet, so there is nothing to take a
          percentage of. Price the reservation first.
        </p>
      )}
    </div>
  )
}

function shiftDays(iso: string, days: number): string {
  const d = new Date(`${iso.slice(0, 10)}T00:00:00`)
  d.setDate(d.getDate() + days)
  return d.toISOString().slice(0, 10)
}

/* --------------------------------------------------------------- forms --- */
function Panel({ title, hint, children, onClose }: {
  title: string; hint?: string; children: React.ReactNode; onClose: () => void
}) {
  return (
    <section className="rounded-xl border border-slate-200 bg-white">
      <div className="flex items-start justify-between gap-2 border-b border-slate-200 px-4 py-3">
        <div>
          <h2 className="font-semibold text-ink">{title}</h2>
          {hint && <p className="text-sm text-slate-500">{hint}</p>}
        </div>
        <button onClick={onClose} aria-label="Close"
          className="rounded-lg p-1 text-slate-400 hover:bg-slate-100">
          <X className="h-4 w-4" />
        </button>
      </div>
      <div className="p-4">{children}</div>
    </section>
  )
}

function InstallmentForm({ title, initial, bookingValue, busy, onClose, onSubmit }: {
  title: string; initial: DepositInstallment | null; bookingValue: number
  busy: boolean; onClose: () => void
  onSubmit: (body: InstallmentInput) => void
}) {
  // An instalment created from a percentage keeps editing as a percentage, so
  // re-saving it does not silently freeze the figure the percentage produced.
  const [form, setForm] = useState({
    label: initial?.label ?? '',
    basis: (initial?.percent != null ? 'percent' : 'amount') as 'amount' | 'percent',
    amount: initial ? String(initial.amount) : '',
    percent: initial?.percent != null ? String(initial.percent) : '',
    due_rule: initial?.due_rule ?? 'fixed_date',
    due_date: initial?.due_date ?? today(),
    notes: initial?.notes ?? '',
  })
  const set = (k: string, v: string) => setForm((f) => ({ ...f, [k]: v }))
  const preview = form.basis === 'percent' && Number(form.percent) > 0
    ? bookingValue * Number(form.percent) / 100 : null

  return (
    <Panel title={title} onClose={onClose}
      hint="An instalment is either a stated amount or a share of the booking value.">
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="sm:col-span-2">
          <span className={label}>Instalment</span>
          <input className={input} value={form.label} placeholder="e.g. Second Installment"
            onChange={(e) => set('label', e.target.value)} />
        </label>
        <label>
          <span className={label}>Basis</span>
          <Select className={input} value={form.basis}
            onWheel={(e) => (e.target as HTMLSelectElement).blur()}
            onChange={(e) => set('basis', e.target.value)}>
            <option value="amount">A fixed amount</option>
            <option value="percent">A percentage of the booking</option>
          </Select>
        </label>
        {form.basis === 'amount' ? (
          <label>
            <span className={label}>Amount (INR)</span>
            <input className={input} type="number" min={0} value={form.amount}
              onChange={(e) => set('amount', e.target.value)} />
          </label>
        ) : (
          <label>
            <span className={label}>Percentage</span>
            <input className={input} type="number" min={0} max={100} value={form.percent}
              onChange={(e) => set('percent', e.target.value)} />
            {preview != null && (
              <span className="mt-1 block text-xs text-slate-500">
                = {exact.format(preview)} of {money.format(bookingValue)}
              </span>
            )}
          </label>
        )}
        <label>
          <span className={label}>Due</span>
          <Select className={input} value={form.due_rule}
            onWheel={(e) => (e.target as HTMLSelectElement).blur()}
            onChange={(e) => set('due_rule', e.target.value)}>
            {DUE_RULES.map((r) => (
              <option key={r.code} value={r.code}>{r.label}</option>
            ))}
          </Select>
        </label>
        <label>
          <span className={label}>Due date</span>
          <DateField value={form.due_date} onChange={(v) => set('due_date', v)} className={input} />
          {form.due_rule === 'at_checkin' && (
            <span className="mt-1 block text-xs text-slate-500">
              Check-in instalments follow the arrival date.
            </span>
          )}
        </label>
        <label className="sm:col-span-2">
          <span className={label}>Notes</span>
          <input className={input} value={form.notes}
            onChange={(e) => set('notes', e.target.value)} />
        </label>
      </div>
      <div className="mt-4 flex justify-end gap-2">
        <button onClick={onClose}
          className="rounded-lg border border-slate-200 px-4 py-2 text-sm font-semibold text-slate-600">
          Cancel
        </button>
        <button disabled={busy || form.label.trim() === ''}
          onClick={() => onSubmit({
            label: form.label.trim(),
            amount: form.basis === 'amount' ? Number(form.amount) : null,
            percent: form.basis === 'percent' ? Number(form.percent) : null,
            due_rule: form.due_rule,
            due_date: form.due_date || null,
            notes: form.notes || null,
          })}
          className="flex items-center gap-2 rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white hover:bg-brand-dark disabled:opacity-50">
          {busy && <Loader2 className="h-4 w-4 animate-spin" />}
          {initial ? 'Save Changes' : 'Add Instalment'}
        </button>
      </div>
    </Panel>
  )
}

function CollectForm({ rows, focused, setFocused, busy, onClose, onSubmit }: {
  rows: DepositInstallment[]; focused: string; setFocused: (v: string) => void
  busy: boolean; onClose: () => void
  onSubmit: (id: string, body: { amount: number; method: string;
                                 received_on?: string; reference?: string }) => void
}) {
  const row = rows.find((r) => r.id === focused) ?? rows[0]
  // Served, not restated. The list this screen used to keep offered "Link",
  // which is a way of asking for money rather than a way of receiving it, and
  // spelled the rest in title case so the ledger recorded a second "Card".
  const { methods } = usePaymentMethods(useActivePropertyId())
  const [amount, setAmount] = useState('')
  const [method, setMethod] = useState('')
  const [on, setOn] = useState(today())
  const [reference, setReference] = useState('')
  const value = Number(amount || row?.balance_due || 0)

  if (!row) {
    return (
      <Panel title="Collect Payment" onClose={onClose}>
        <p className="text-sm text-slate-500">Nothing is outstanding.</p>
      </Panel>
    )
  }
  return (
    <Panel title="Collect Payment" onClose={onClose}
      hint="This posts a credit to the folio through the same path as every other payment.">
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="sm:col-span-2">
          <span className={label}>Instalment</span>
          <Select className={input} value={row.id}
            onWheel={(e) => (e.target as HTMLSelectElement).blur()}
            onChange={(e) => { setFocused(e.target.value); setAmount('') }}>
            {rows.map((r) => (
              <option key={r.id} value={r.id}>
                {r.seq}. {r.label} — {exact.format(r.balance_due)} due
              </option>
            ))}
          </Select>
        </label>
        <label>
          <span className={label}>Amount (INR)</span>
          <input className={input} type="number" min={0} value={amount}
            placeholder={String(row.balance_due)}
            onChange={(e) => setAmount(e.target.value)} />
          <span className="mt-1 block text-xs text-slate-500">
            Leave blank to take the whole {exact.format(row.balance_due)}.
          </span>
        </label>
        <label>
          <span className={label}>Method</span>
          <Select className={input} value={method}
            onWheel={(e) => (e.target as HTMLSelectElement).blur()}
            onChange={(e) => setMethod(e.target.value)}>
            <option value="">Choose a method…</option>
            {methods.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
          </Select>
        </label>
        <label>
          <span className={label}>Received on</span>
          <DateField value={on} onChange={(v) => setOn(v)} className={input} />
        </label>
        <label>
          <span className={label}>Reference</span>
          <input className={input} value={reference} placeholder="e.g. GPay txn id"
            onChange={(e) => setReference(e.target.value)} />
        </label>
      </div>
      <div className="mt-4 flex items-center justify-between gap-2">
        <p className="text-sm text-slate-500">
          Taking <strong className="font-semibold text-slate-700">
            {exact.format(value)}</strong> against {row.label}.
        </p>
        {/* A method is now chosen rather than defaulted, so it has to be
            required — money taken under no method at all is worse than money
            taken under the wrong one. */}
        <button disabled={busy || value <= 0 || method === ''}
          onClick={() => onSubmit(row.id, {
            amount: value, method, received_on: on,
            reference: reference || undefined,
          })}
          className="flex items-center gap-2 rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white hover:bg-brand-dark disabled:opacity-50">
          {busy ? <Loader2 className="h-4 w-4 animate-spin" />
            : <Wallet className="h-4 w-4" />} Collect
        </button>
      </div>
    </Panel>
  )
}

function WaiveForm({ rows, focused, setFocused, threshold, canApprove, busy,
  onClose, onSubmit }: {
  rows: DepositInstallment[]; focused: string; setFocused: (v: string) => void
  threshold: number; canApprove: boolean; busy: boolean; onClose: () => void
  onSubmit: (id: string, body: { amount: number; reason: string }) => void
}) {
  const row = rows.find((r) => r.id === focused) ?? rows[0]
  const [amount, setAmount] = useState('')
  const [reason, setReason] = useState('')
  const value = Number(amount || 0)
  const needsApproval = value > threshold && !canApprove

  if (!row) {
    return (
      <Panel title="Waive Deposit" onClose={onClose}>
        <p className="text-sm text-slate-500">There is nothing outstanding to waive.</p>
      </Panel>
    )
  }
  return (
    <Panel title="Waive Deposit" onClose={onClose}
      hint="Writing off part of an instalment. The reason is recorded against it.">
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="sm:col-span-2">
          <span className={label}>Instalment</span>
          <Select className={input} value={row.id}
            onWheel={(e) => (e.target as HTMLSelectElement).blur()}
            onChange={(e) => setFocused(e.target.value)}>
            {rows.map((r) => (
              <option key={r.id} value={r.id}>
                {r.seq}. {r.label} — {exact.format(r.balance_due)} due
              </option>
            ))}
          </Select>
        </label>
        <label>
          <span className={label}>Waiver amount (INR)</span>
          <input className={input} type="number" min={0} value={amount}
            onChange={(e) => setAmount(e.target.value)} />
        </label>
        <label>
          <span className={label}>Approval</span>
          <p className={`${input} bg-slate-50`}>
            {value <= threshold || canApprove
              ? 'Applies immediately' : 'Needs a manager'}
          </p>
        </label>
        <label className="sm:col-span-2">
          <span className={label}>Reason</span>
          <input className={input} value={reason}
            placeholder="e.g. Goodwill — delayed room readiness"
            onChange={(e) => setReason(e.target.value)} />
        </label>
      </div>
      {needsApproval && (
        <p className="mt-3 flex items-start gap-2 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-900">
          <Info className="mt-0.5 h-4 w-4 shrink-0" />
          <span>
            {money.format(value)} is above the {money.format(threshold)} threshold,
            so this will be recorded as a request. The balance does not change
            until a manager approves it.
          </span>
        </p>
      )}
      <div className="mt-4 flex justify-end gap-2">
        <button onClick={onClose}
          className="rounded-lg border border-slate-200 px-4 py-2 text-sm font-semibold text-slate-600">
          Cancel
        </button>
        <button disabled={busy || value <= 0 || reason.trim() === ''}
          onClick={() => onSubmit(row.id, { amount: value, reason: reason.trim() })}
          className="flex items-center gap-2 rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white hover:bg-brand-dark disabled:opacity-50">
          {busy && <Loader2 className="h-4 w-4 animate-spin" />}
          {needsApproval ? 'Request Waiver' : 'Waive'}
        </button>
      </div>
    </Panel>
  )
}

function RefundForm({ options, busy, onClose, onSubmit }: {
  options: { installment: DepositInstallment
             alloc: DepositInstallment['allocations'][number] }[]
  busy: boolean; onClose: () => void
  onSubmit: (id: string, body: { allocation_id: string; amount: number;
                                 reason: string }) => void
}) {
  const [pick, setPick] = useState(options[0]?.alloc.id ?? '')
  const [amount, setAmount] = useState('')
  const [reason, setReason] = useState('')
  const chosen = options.find((o) => o.alloc.id === pick) ?? options[0]
  const value = Number(amount || chosen?.alloc.amount || 0)

  if (!chosen) {
    return (
      <Panel title="Refund / Reverse Payment" onClose={onClose}>
        <p className="text-sm text-slate-500">There are no payments to reverse.</p>
      </Panel>
    )
  }
  return (
    <Panel title="Refund / Reverse Payment" onClose={onClose}
      hint="The original payment stays on the record; the refund is posted beside it.">
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="sm:col-span-2">
          <span className={label}>Payment</span>
          <Select className={input} value={pick}
            onWheel={(e) => (e.target as HTMLSelectElement).blur()}
            onChange={(e) => { setPick(e.target.value); setAmount('') }}>
            {options.map((o) => (
              <option key={o.alloc.id} value={o.alloc.id}>
                {o.installment.label} — {exact.format(o.alloc.amount)}{' '}
                {o.alloc.method} on {day(o.alloc.received_on)}
              </option>
            ))}
          </Select>
        </label>
        <label>
          <span className={label}>Refund amount (INR)</span>
          <input className={input} type="number" min={0} value={amount}
            placeholder={String(chosen.alloc.amount)}
            onChange={(e) => setAmount(e.target.value)} />
        </label>
        <label>
          <span className={label}>Reason</span>
          <input className={input} value={reason}
            placeholder="e.g. Duplicate charge"
            onChange={(e) => setReason(e.target.value)} />
        </label>
      </div>
      <div className="mt-4 flex justify-end gap-2">
        <button onClick={onClose}
          className="rounded-lg border border-slate-200 px-4 py-2 text-sm font-semibold text-slate-600">
          Cancel
        </button>
        <button disabled={busy || value <= 0 || reason.trim() === ''}
          onClick={() => onSubmit(chosen.installment.id, {
            allocation_id: chosen.alloc.id, amount: value, reason: reason.trim(),
          })}
          className="flex items-center gap-2 rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white hover:bg-brand-dark disabled:opacity-50">
          {busy ? <Loader2 className="h-4 w-4 animate-spin" />
            : <RotateCcw className="h-4 w-4" />} Process Refund
        </button>
      </div>
    </Panel>
  )
}
