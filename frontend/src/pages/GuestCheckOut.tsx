import { useEffect, useState } from 'react'
import { useNavigate, useParams, Link } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import DateField from '../components/DateField'
import { StayLayoutToggle, StayView, useStayLayout } from '../lib/listLayout'
import RowActions from '../components/RowActions'
import { fmtDate } from '../lib/dates'
import {
  AlertTriangle, ArrowLeft, CheckCircle2, Info,
  Loader2, LogOut, Printer, Receipt, X,
} from 'lucide-react'
import {
  listDepartures, getCheckOutView, completeCheckOut, 
  openFolioPdf,
} from '../api'
import { useActivePropertyId, usePropertyToday } from '../hooks/useProperty'
import { usePaymentMethods } from '../lib/paymentMethods'

/**
 * Screen 007 — Checkout & Guest Folio.
 *
 * The other half of check-in. Settling the bill, closing the stay and freeing
 * the room happen in one transaction, so a guest is never half-departed and a
 * payment is never taken for a checkout that then fails.
 *
 * A balance does not block departure. Guests do leave owing money — a company
 * is invoiced, a charge is disputed — and refusing to check them out would not
 * stop that; it would only stop it being recorded. So an outstanding balance
 * has to be confirmed deliberately, and it is written into the checkout record
 * rather than waved through.
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

const input = 'w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700 outline-none focus:border-brand'


/** How urgent a departure is, in the mockup's words plus the overdue case. */
function dueTone(label: string): string {
  if (label.startsWith('Overdue')) return 'bg-red-100 text-red-700'
  if (label === 'Due Out Today') return 'bg-amber-100 text-amber-800'
  if (label === 'Checked out') return 'bg-emerald-100 text-emerald-700'
  return 'bg-slate-75 text-slate-600'
}


/* ----------------------------------------------------------- departures --- */
export function Departures() {
  const [layout, chooseLayout] = useStayLayout()
  // The row, the card and the "⋮" all open the same panel. Without this the
  // three front-desk tabs answered a click on the row with nothing, while the
  // Reservations tab opened the actions -- the same list, reached by a
  // different tab, behaving differently.
  const [actionsFor, setActionsFor] = useState('')
  const propertyId = useActivePropertyId()
  // The property's own day, not the browser's UTC one: after 18:30 in
  // India `toISOString()` is already tomorrow's date in UTC terms, so
  // this list showed YESTERDAY's departures all evening.
  const propertyToday = usePropertyToday()
  const [onDate, setOnDate] = useState('')
  useEffect(() => { if (!onDate && propertyToday) setOnDate(propertyToday) },
    [propertyToday, onDate])
  // Departures for a date means everyone leaving that date, whether or not
  // they have gone yet. Hiding the finished ones turned the list into a
  // to-do queue that emptied as the morning went on — and left the tab
  // counting departures the list would not show. Unticking narrows it to
  // those still to process, which is the filter this always was.
  const [includeOut, setIncludeOut] = useState(true)

  const q = useQuery({
    queryKey: ['departures', propertyId, onDate, includeOut],
    queryFn: () => listDepartures(propertyId, {
      on_date: onDate, include_checked_out: includeOut }),
    enabled: propertyId !== '',
  })
  const rows = q.data ?? []

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <DateField value={onDate} onChange={setOnDate}           label="Departures on" />
        <button onClick={() => setOnDate(propertyToday)}
          className="rounded-lg border border-slate-200 px-3 py-2 text-sm font-medium text-slate-600 hover:bg-slate-50">
          Today
        </button>
        <label className="flex items-center gap-2 text-sm text-slate-500">
          <input type="checkbox" checked={!includeOut}
            onChange={(e) => setIncludeOut(!e.target.checked)} />
          Still to check out
        </label>
        <span className="ml-auto text-sm text-slate-500">
          {rows.length} departure{rows.length === 1 ? '' : 's'}
        </span>
        <StayLayoutToggle layout={layout} onChange={chooseLayout} />
      </div>

      <StayView
        layout={layout}
        loading={q.isLoading}
        empty={`Nobody is due to leave on ${day(onDate)}.`}
        rows={rows.map((r) => ({
          key: r.reservation_unit_id,
          onClick: () => setActionsFor(r.reservation_unit_id),
          selected: actionsFor === r.reservation_unit_id,
          number: r.number,
          guestName: r.guest_name,
          roomType: r.room_type,
          roomCodes: r.room ? [r.room] : [],
          plan: r.plan,
          arrival: r.arrival_date,
          departure: r.departure_date,
          nights: r.nights,
          hasFolio: r.has_folio,
          total: r.total, paid: r.paid, balance: r.balance,
          status: (
            <span className={`whitespace-nowrap rounded-full px-2.5 py-1 text-xs font-medium ${
              dueTone(r.due_label)}`}>
              {r.due_label}
            </span>
          ),
          action: (
            <RowActions
              open={actionsFor === r.reservation_unit_id}
              onOpenChange={(v) => setActionsFor(v ? r.reservation_unit_id : '')}
              ctx={{
              propertyId,
              organizationId: r.organization_id,
              reservationId: r.reservation_id,
              reservationUnitId: r.reservation_unit_id,
              number: r.number,
              folioId: r.folio_id,
              hasFolio: r.has_folio,
              roomId: r.room_id,
              room: r.room,
              state: r.unit_status,
              guestName: r.guest_name,
              roomType: r.room_type,
              departureDate: r.departure_date,
              nights: r.nights,
              adults: r.adults,
              children: r.children,
              total: Number(r.total),
              paid: Number(r.paid),
              statusLabel: r.due_label,
              balance: Number(r.balance),
              arrivalDate: r.arrival_date,
              businessDate: onDate,
              onDone: () => void q.refetch(),
              show: { checkIn: false, assignRoom: false, noShow: false, deposits: false },
            }} />
          ),
        }))}
      />
    </div>
  )
}

/* ------------------------------------------------------------ check-out --- */
export default function GuestCheckOut() {
  const { unitId = '' } = useParams()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const propertyId = useActivePropertyId()
  // Served, never restated. See lib/paymentMethods for why five copies of
  // this list put the same method into the ledger under two spellings.
  const { methods } = usePaymentMethods(propertyId)

  const [printing, setPrinting] = useState(false)
  const [printErr, setPrintErr] = useState('')

  // Empty until the served list arrives. Naming a default here meant a
  // cashier who never touched the selector posted the literal 'UPI' — the
  // title-cased spelling the ledger stores as a separate method.
  const [method, setMethod] = useState('')
  useEffect(() => {
    if (methods.length > 0) setMethod((m) => m || methods[0].value)
  }, [methods])
  const [amount, setAmount] = useState('')
  const [reference, setReference] = useState('')
  const [refundDeposit, setRefundDeposit] = useState(false)
  const [keyReturned, setKeyReturned] = useState(false)
  const [hkNotified, setHkNotified] = useState(false)
  const [feedback, setFeedback] = useState(false)
  const [allowBalance, setAllowBalance] = useState(false)
  const [error, setError] = useState('')
  const [done, setDone] = useState<{ room: string | null; warnings: string[] } | null>(null)

  const q = useQuery({
    queryKey: ['checkOutView', unitId, propertyId],
    queryFn: () => getCheckOutView(unitId, propertyId),
    enabled: unitId !== '' && propertyId !== '',
    refetchOnWindowFocus: false,
  })
  const v = q.data

  // What will actually be owed once this payment and any refund are applied.
  const pay = Number(amount || 0)
  const refund = refundDeposit ? Number(v?.deposit_held ?? 0) : 0
  const projected = Number(v?.balance_due ?? 0) - pay + refund

  const submit = useMutation({
    mutationFn: () => completeCheckOut(unitId, propertyId, {
      payment_amount: pay,
      payment_method: pay > 0 ? method : null,
      reference: reference || null,
      refund_deposit: refundDeposit,
      key_returned: keyReturned,
      housekeeping_notified: hkNotified,
      feedback_scheduled: feedback,
      allow_outstanding_balance: allowBalance,
    }),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ['departures'] })
      qc.invalidateQueries({ queryKey: ['rack'] })
      qc.invalidateQueries({ queryKey: ['checkOutView', unitId] })
      setError('')
      setDone({ room: r.room, warnings: r.warnings })
    },
    onError: (e) => {
      const er = e as { response?: { data?: { detail?: string } } }
      setError(er.response?.data?.detail ?? 'The checkout could not be completed.')
    },
  })

  if (q.isLoading) {
    return (
      <p className="flex items-center gap-2 p-8 text-sm text-slate-400">
        <Loader2 size={16} className="animate-spin" /> Loading folio…
      </p>
    )
  }
  if (q.isError || !v) {
    return (
      <p className="rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700">
        That stay could not be loaded.
      </p>
    )
  }

  const blocked = projected > 0 && !allowBalance
  const canSubmit = !v.already_checked_out && !blocked

  return (
    <div className="space-y-4">
      <div>
        <p className="text-sm text-slate-400">
          <Link to="/reservations/list?tab=arrivals" className="text-slate-500 hover:text-brand">
            Reservations
          </Link>
          <span className="px-2">/</span>
          <Link to="/reservations/list?tab=departures" className="text-slate-500 hover:text-brand">
            Departures
          </Link>
          <span className="px-2">/</span>
          <span className="font-semibold text-slate-700">
            {v.room ? `Room ${v.room}` : v.number}
          </span>
        </p>
        <h1 className="mt-1 flex items-center gap-2 text-display text-ink">
          <Receipt size={26} className="text-brand" /> Checkout &amp; Guest Folio
        </h1>
      </div>

      {/* Guest strip */}
      <div className="flex flex-wrap items-center gap-x-8 gap-y-3 rounded-2xl border border-slate-100 bg-white p-4 shadow-sm">
        <div className="flex items-center gap-3">
          <span className="grid h-12 w-12 place-items-center rounded-full bg-brand text-sm font-semibold text-white">
            {(v.guest_name ?? '?').split(' ').map((w) => w[0]).slice(0, 2)
              .join('').toUpperCase()}
          </span>
          <div>
            <p className="text-lg font-semibold text-slate-800">
              {v.guest_name ?? <span className="italic text-slate-400">No guest on file</span>}
            </p>
            <p className="text-sm text-slate-500">
              {v.room ? `Room ${v.room} — ` : ''}{v.room_type}
            </p>
          </div>
        </div>
        <Head label="Check-in / Check-out"
          value={`${day(v.arrival_date)} – ${day(v.departure_date)}`}
          sub={`${v.nights} Night${v.nights === 1 ? '' : 's'}`} />
        <Head label="Reservation No." value={v.number} />
        <div>
          <p className="text-xs text-slate-400">Status</p>
          <span className={`inline-block rounded-full px-3 py-1 text-xs font-semibold ${
            dueTone(v.due_label)}`}>
            {v.due_label}
          </span>
        </div>
      </div>

      {v.already_checked_out && (
        <p className="flex items-center gap-2 rounded-xl bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
          <CheckCircle2 size={16} /> This guest has already checked out
          {v.room && <> — room {v.room} is released</>}.
        </p>
      )}
      {error && (
        <p className="flex items-start gap-2 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700">
          <AlertTriangle size={16} className="mt-0.5 shrink-0" />
          <span>{error}</span>
          <button onClick={() => setError('')} className="ml-auto text-red-400">
            <X size={15} />
          </button>
        </p>
      )}
      {done && (
        <div className="rounded-xl bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
          <p className="flex items-center gap-2 font-semibold">
            <CheckCircle2 size={16} /> Checked out
            {done.room && <> — room {done.room} is now free</>}.
          </p>
          {done.warnings.map((w) => (
            <p key={w} className="mt-1 flex items-start gap-2">
              <Info size={14} className="mt-0.5 shrink-0" /> {w}
            </p>
          ))}
          <button onClick={() => navigate('/reservations/list?tab=departures')}
            className="mt-2 rounded-lg bg-brand px-3 py-1.5 text-xs font-semibold text-white">
            Back to departures
          </button>
        </div>
      )}

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
        {/* ------------------------------------------------------ folio --- */}
        <section className="rounded-2xl border border-slate-100 bg-white shadow-sm">
          <div className="flex items-center justify-between border-b border-slate-100 px-5 py-4">
            <h2 className="flex items-center gap-2 text-lg font-semibold text-ink">
              <Receipt size={18} className="text-brand" /> Guest Folio
            </h2>
            {/* The rendered folio, not window.print(). Printing the page
                gives whatever the browser makes of this screen — sidebar,
                buttons and all — which is not a document anyone can hand a
                guest. The server renders the same A4 tax invoice the folio
                screen prints, so a provisional bill and the final one are the
                same document at two moments. */}
            <button
              disabled={!v.folio_id || printing}
              title={v.folio_id ? undefined : 'Nothing has been posted yet.'}
              onClick={async () => {
                if (!v.folio_id) return
                setPrinting(true)
                setPrintErr('')
                try {
                  await openFolioPdf(propertyId, v.folio_id)
                } catch {
                  setPrintErr('The bill could not be produced.')
                } finally { setPrinting(false) }
              }}
              className="flex items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-1.5 text-sm font-medium text-slate-600 hover:bg-slate-50 disabled:opacity-50">
              {printing
                ? <Loader2 size={15} className="animate-spin" />
                : <Printer size={15} />}
              Print Provisional Bill
            </button>
          </div>
          {printErr && (
            <p className="border-b border-red-100 bg-red-50 px-5 py-2 text-xs text-red-600">
              {printErr}
            </p>
          )}
          {v.lines.length === 0 ? (
            <p className="px-5 py-10 text-center text-sm text-slate-500">
              Nothing has been posted to this folio yet.
            </p>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-100 text-left text-slate-600">
                  <th className="px-5 py-2.5 font-semibold">Date</th>
                  <th className="px-3 py-2.5 font-semibold">Description</th>
                  <th className="px-3 py-2.5 font-semibold">Department</th>
                  <th className="px-3 py-2.5 text-center font-semibold">Qty</th>
                  <th className="px-5 py-2.5 text-right font-semibold">Amount</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-50">
                {v.lines.map((l) => (
                  <tr key={l.id} className="text-slate-700">
                    <td className="whitespace-nowrap px-5 py-2.5 text-xs text-slate-500">
                      {day(l.business_date)}
                    </td>
                    <td className="px-3 py-2.5">{l.description}</td>
                    <td className="px-3 py-2.5 text-slate-500">{l.department}</td>
                    <td className="px-3 py-2.5 text-center text-slate-500">{l.qty}</td>
                    <td className={`px-5 py-2.5 text-right font-medium tabular-nums ${
                      l.entry_type === 'credit' ? 'text-emerald-600' : 'text-slate-800'}`}>
                      {l.entry_type === 'credit' ? '−' : ''}{money.format(l.amount)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>

        {/* --------------------------------------------- payment summary --- */}
        <section className="rounded-2xl border border-slate-100 bg-white p-5 shadow-sm">
          <h2 className="mb-4 text-lg font-semibold text-ink">Payment Summary</h2>
          <dl className="space-y-2 text-sm">
            <Row label="Subtotal" value={money.format(v.subtotal)} />
            <Row label="Taxes & Charges" value={exact.format(v.taxes)} />
            <div className="flex justify-between rounded-lg bg-slate-50 px-3 py-2 text-base font-semibold text-slate-800">
              <dt>Grand Total</dt><dd className="tabular-nums">{money.format(v.grand_total)}</dd>
            </div>
            <Row label="Advance Paid" value={`− ${money.format(v.advance_paid)}`}
              tone="emerald" />
            <div className={`flex justify-between rounded-lg px-3 py-2 text-base font-semibold ${
              v.balance_due > 0 ? 'bg-amber-50 text-caution'
                : 'bg-emerald-50 text-emerald-700'}`}>
              <dt>Balance Due</dt>
              <dd className="tabular-nums">{money.format(v.balance_due)}</dd>
            </div>
            {v.deposit_held > 0 && (
              <Row label="Security deposit held" value={money.format(v.deposit_held)} />
            )}
          </dl>

          {!v.already_checked_out && (
            <>
              <hr className="my-4 border-slate-100" />
              <p className="mb-2 text-sm font-medium text-slate-600">
                Select Payment Method
              </p>
              <div className="grid grid-cols-2 gap-2">
                {methods.map((m) => (
                  <button key={m.value} onClick={() => setMethod(m.value)}
                    className={`flex flex-col items-center gap-1 rounded-lg border px-3 py-2.5 text-xs font-medium ${
                      method === m.value
                        ? 'border-brand bg-brand-light text-brand'
                        : 'border-slate-200 text-slate-600 hover:bg-slate-50'}`}>
                    {m.icon} {m.label}
                  </button>
                ))}
              </div>

              <div className="mt-3 grid gap-3 sm:grid-cols-2">
                <label>
                  <span className="mb-1 block text-xs text-slate-500">Amount (₹)</span>
                  <input type="number" min={0} className={input} value={amount}
                    placeholder={String(Math.max(v.balance_due, 0))}
                    onChange={(e) => setAmount(e.target.value)} />
                </label>
                <label>
                  <span className="mb-1 block text-xs text-slate-500">
                    Transaction Reference
                  </span>
                  <input className={input} value={reference}
                    placeholder="UPI ID / Txn no."
                    onChange={(e) => setReference(e.target.value)} />
                </label>
              </div>
              {v.balance_due > 0 && (
                <button onClick={() => setAmount(String(v.balance_due))}
                  className="mt-1.5 text-xs font-semibold text-brand hover:underline">
                  Charge the full {money.format(v.balance_due)}
                </button>
              )}

              {v.deposit_held > 0 && (
                <label className="mt-3 flex cursor-pointer items-start gap-2 text-sm">
                  <input type="checkbox" checked={refundDeposit} className="mt-0.5"
                    onChange={(e) => setRefundDeposit(e.target.checked)} />
                  <span className="text-slate-700">
                    Refund the {money.format(v.deposit_held)} security deposit
                    <span className="block text-xs text-slate-400">
                      Posts a debit back to the folio.
                    </span>
                  </span>
                </label>
              )}

              <p className="mt-3 flex justify-between rounded-lg bg-slate-50 px-3 py-2 text-sm">
                <span className="text-slate-500">Balance after this</span>
                <strong className={`tabular-nums ${
                  projected > 0 ? 'text-red-600' : 'text-emerald-600'}`}>
                  {money.format(projected)}
                </strong>
              </p>
            </>
          )}
        </section>
      </div>

      {/* -------------------------------------------------------- footer --- */}
      {!v.already_checked_out && (
        <div className="space-y-3 rounded-2xl border border-slate-100 bg-white p-4 shadow-sm">
          {projected > 0 && (
            <label className="flex cursor-pointer items-start gap-2 rounded-lg bg-amber-50 px-3 py-2.5 text-sm">
              <input type="checkbox" checked={allowBalance} className="mt-0.5"
                onChange={(e) => setAllowBalance(e.target.checked)} />
              <span className="text-amber-900">
                Let this guest leave owing{' '}
                <strong className="font-semibold">{money.format(projected)}</strong>
                <span className="block text-xs">
                  The folio stays open and the amount is recorded against the
                  checkout.
                </span>
              </span>
            </label>
          )}
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex flex-wrap gap-x-6 gap-y-2">
              <Check checked={keyReturned} onChange={setKeyReturned}
                label="Room key returned" />
              <Check checked={hkNotified} onChange={setHkNotified}
                label="Housekeeping notified"
                note="Marks the room dirty for cleaning." />
              <Check checked={feedback} onChange={setFeedback}
                label="Feedback link scheduled"
                note="Recorded only — no mail or SMS transport yet." />
            </div>
            <div className="flex gap-2">
              <button onClick={() => navigate('/reservations/list?tab=departures')}
                className="flex items-center gap-2 rounded-lg border border-slate-200 px-4 py-2.5 text-sm font-semibold text-slate-600 hover:bg-slate-50">
                <ArrowLeft size={15} /> Cancel
              </button>
              <button disabled={!canSubmit || submit.isPending}
                onClick={() => submit.mutate()}
                className="flex items-center gap-2 rounded-lg bg-brand px-5 py-2.5 text-sm font-semibold text-white hover:bg-brand-dark disabled:opacity-50">
                {submit.isPending
                  ? <Loader2 size={16} className="animate-spin" />
                  : <LogOut size={16} />}
                {pay > 0 ? 'Collect Payment & Complete Checkout'
                  : 'Complete Checkout'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

/* ------------------------------------------------------------------ bits --- */
function Head({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div>
      <p className="text-xs text-slate-400">{label}</p>
      <p className="font-semibold text-slate-800">{value}</p>
      {sub && <p className="text-xs text-slate-500">{sub}</p>}
    </div>
  )
}

function Row({ label, value, tone }: {
  label: string; value: string; tone?: 'emerald'
}) {
  return (
    <div className="flex justify-between">
      <dt className="text-slate-500">{label}</dt>
      <dd className={`tabular-nums ${
        tone === 'emerald' ? 'text-emerald-600' : 'text-slate-800'}`}>
        {value}
      </dd>
    </div>
  )
}

function Check({ checked, onChange, label, note }: {
  checked: boolean; onChange: (b: boolean) => void; label: string; note?: string
}) {
  return (
    <label className="flex cursor-pointer items-start gap-2 text-sm">
      <input type="checkbox" checked={checked} className="mt-0.5"
        onChange={(e) => onChange(e.target.checked)} />
      <span>
        <span className="text-slate-700">{label}</span>
        {note && <span className="block text-xs text-slate-400">{note}</span>}
      </span>
    </label>
  )
}
