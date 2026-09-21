import { useEffect, useRef, useState } from 'react'
import Select from '../components/Select'
import ListSelect, { CityInput, PostalCodeInput, StateField } from '../components/ListSelect'
import { COUNTRIES, postalCodeProblem } from '../lib/options'
import { useNavigate, useParams, useSearchParams, Link } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle, ArrowLeft, BedDouble, CheckCircle2, Info, KeyRound, Loader2,
  Camera, Search, ShieldCheck, Upload, UserPlus, X,
} from 'lucide-react'
import {
  listArrivals, getCheckInView, completeCheckIn, saveCheckInGuest,
  uploadGuestDocument,
  deleteGuestDocument, ID_TYPES,
  getFormC, saveFormC, openRegistrationCard,
  type GuestDoc,
} from '../api'
import { Departures } from './GuestCheckOut'
import DateField from '../components/DateField'
import { StayLayoutToggle, StayView, useStayLayout } from '../lib/listLayout'
import CameraCapture from '../components/CameraCapture'
import RowActions from '../components/RowActions'
import AssignRoomsPanel from '../components/AssignRoomsPanel'

import { fmtDate } from '../lib/dates'
import { Crumbs } from '../components/Crumbs'
import { useActivePropertyId, usePropertyToday } from '../hooks/useProperty'
import FormCFields from '../components/FormCFields'
import { needsFormC, type FormCValues } from '../lib/formC'
import { usePaymentMethods } from '../lib/paymentMethods'
/**
 * Screen 005 — Guest Check-In.
 *
 * Three panels because check-in asks three questions: who is this, where are
 * they sleeping, and what do they owe. The button at the end does all of it in
 * one transaction — identity, room, deposit, stay — so a half-finished
 * check-in cannot exist.
 *
 * The room list only offers rooms that are genuinely free for the whole stay,
 * but that is a convenience: the exclusion constraint in the database is what
 * actually stops two desks putting two guests in one room, and a collision
 * comes back as a plain "pick another".
 *
 * "Issue Key" and "Welcome message" are recorded, not performed. There is no
 * door-lock integration and no mail transport, and the screen says so rather
 * than letting a tick imply something happened.
 */

const money = new Intl.NumberFormat('en-IN', {
  style: 'currency', currency: 'INR', maximumFractionDigits: 0,
})
const day = (s?: string | null) => {
  if (!s) return '—'
  const d = new Date(`${s.slice(0, 10)}T00:00:00`)
  return fmtDate(d)
}

const input = 'w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700 outline-none focus:border-brand'
const label = 'mb-1 block text-xs font-medium text-slate-500'
const req = <span className="text-rose-500">*</span>


/* ------------------------------------------------------------ front desk --- */
/**
 * Arrivals and Departures are the same desk looking at the same day from two
 * ends, so they live on one screen. The tab is in the URL because a checkout
 * needs somewhere to send the operator back to.
 */
export function FrontDesk() {
  const [params, setParams] = useSearchParams()
  const tab = params.get('tab') === 'departures' ? 'departures' : 'arrivals'
  return (
    <div className="space-y-4">
      <div>
        <h1 className="mt-1 text-display text-ink">Reservations</h1>
        <p className="text-slate-500">
          Today's arrivals and departures.
        </p>
      </div>
      <div className="flex gap-1 border-b border-slate-200">
        {(['arrivals', 'departures'] as const).map((t) => (
          <button key={t} onClick={() => setParams(t === 'arrivals' ? {} : { tab: t })}
            className={`border-b-2 px-4 py-2.5 text-sm font-semibold capitalize ${
              tab === t ? 'border-brand text-brand'
                : 'border-transparent text-slate-500 hover:text-slate-700'}`}>
            {t}
          </button>
        ))}
      </div>
      {tab === 'arrivals' ? <Arrivals /> : <Departures />}
    </div>
  )
}

/* ------------------------------------------------------------- arrivals --- */
export function Arrivals() {
  const [layout, chooseLayout] = useStayLayout()
  // The row, the card and the "⋮" all open the same panel. Without this the
  // three front-desk tabs answered a click on the row with nothing, while the
  // Reservations tab opened the actions -- the same list, reached by a
  // different tab, behaving differently.
  const [actionsFor, setActionsFor] = useState('')
  const propertyId = useActivePropertyId()
  // The property's own day, not the browser's UTC one: after 18:30 in
  // India `toISOString()` is already tomorrow's date in UTC terms, so
  // this list showed YESTERDAY's arrivals all evening.
  const propertyToday = usePropertyToday()
  const [onDate, setOnDate] = useState('')
  useEffect(() => { if (!onDate && propertyToday) setOnDate(propertyToday) },
    [propertyToday, onDate])
  const [search, setSearch] = useState('')
  const [includeIn, setIncludeIn] = useState(false)
  // Arrivals is where an unassigned room actually gets noticed, so the
  // assignment panel belongs on this list as well as on the bookings list.
  const [assignOpen, setAssignOpen] = useState<string | null>(null)
  const [assignToast, setAssignToast] = useState('')

  const q = useQuery({
    queryKey: ['arrivals', propertyId, onDate, search, includeIn],
    queryFn: () => listArrivals(propertyId, {
      on_date: onDate, search, include_checked_in: includeIn }),
    enabled: propertyId !== '',
  })
  const rows = q.data ?? []

  return (
    <div className="space-y-3">
      <p className="text-sm text-slate-500">
        Guests due in. Anyone still not checked in from an earlier day stays on
        the list.
      </p>

      <div className="flex flex-wrap items-center gap-2">
        <DateField value={onDate} onChange={setOnDate}           label="Arrivals on" />
        <button onClick={() => setOnDate(propertyToday)}
          className="rounded-lg border border-slate-200 px-3 py-2 text-sm font-medium text-slate-600 hover:bg-slate-50">
          Today
        </button>
        <label className="relative min-w-[220px] flex-1">
          <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
          <input value={search} onChange={(e) => setSearch(e.target.value)}
            placeholder="Search by number or guest…"
            className={`${input} pl-9`} />
        </label>
        <label className="flex items-center gap-2 text-sm text-slate-500">
          <input type="checkbox" checked={includeIn}
            onChange={(e) => setIncludeIn(e.target.checked)} />
          Show checked in
        </label>
        <span className="ml-auto text-sm text-slate-500">
          {rows.length} arrival{rows.length === 1 ? '' : 's'}
        </span>
        <StayLayoutToggle layout={layout} onChange={chooseLayout} />
      </div>

      <StayView
        layout={layout}
        loading={q.isLoading}
        empty={`Nobody is due to arrive on ${day(onDate)}.`}
        rows={rows.map((r) => ({
          key: r.reservation_unit_id,
          onClick: () => setActionsFor(r.reservation_unit_id),
          selected: actionsFor === r.reservation_unit_id,
          number: r.number,
          guestName: r.guest_name,
          roomType: r.room_type,
          roomCodes: r.assigned_room ? [r.assigned_room] : [],
          plan: r.plan,
          arrival: r.arrival_date,
          departure: r.departure_date,
          nights: r.nights,
          hasFolio: r.has_folio,
          total: r.total, paid: r.paid, balance: r.balance,
          status: (
            <span className={`rounded-full px-2.5 py-1 text-xs font-medium ${
              r.checked_in ? 'bg-emerald-50 text-emerald-600'
                : 'bg-sky-50 text-sky-600'}`}>
              {r.checked_in ? 'Checked in' : 'Due in'}
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
              roomId: r.assigned_room_id,
              room: r.assigned_room,
              state: r.unit_status,
              guestName: r.guest_name,
              roomType: r.room_type,
              departureDate: r.departure_date,
              nights: r.nights,
              adults: r.adults,
              children: r.children,
              total: Number(r.total),
              paid: Number(r.paid),
              statusLabel: r.checked_in ? 'Checked in' : 'Due in',
              balance: Number(r.balance),
              arrivalDate: r.arrival_date,
              businessDate: onDate,
              onDone: () => void q.refetch(),
              onAssignRoom: () => setAssignOpen(r.arrival_date),
              // Nothing is invoiced before the guest arrives, and the
              // stay-level actions belong to the tabs that have a stay.
              show: { checkOut: false, adjustFolio: false, postCharge: false,
                      housekeeping: false, taxInvoice: false },
            }} />
          ),
        }))}
      />

      {assignToast && (
        <p className="mt-3 flex items-center gap-2 rounded-xl bg-emerald-50 px-4 py-3 text-sm text-emerald-700">
          <CheckCircle2 size={16} /> {assignToast}
        </p>
      )}
      {assignOpen !== null && (
        <AssignRoomsPanel propertyId={propertyId}
          startDate={assignOpen || undefined}
          onClose={() => setAssignOpen(null)}
          onAssigned={(m) => {
            setAssignToast(m)
            setTimeout(() => setAssignToast(''), 4000)
            void q.refetch()
          }} />
      )}
    </div>
  )
}

/* ------------------------------------------------------------- check-in --- */
export default function GuestCheckIn() {
  const { unitId = '' } = useParams()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const propertyId = useActivePropertyId()
  const { methods } = usePaymentMethods(propertyId)
  const [error, setError] = useState('')
  const [done, setDone] = useState<
    { room: string; warnings: string[]; deposit: number } | null>(null)

  const q = useQuery({
    queryKey: ['checkInView', unitId, propertyId],
    queryFn: () => getCheckInView(unitId, propertyId),
    enabled: unitId !== '' && propertyId !== '',
    // This screen is a form being filled in, not a dashboard. Refetching
    // because the window regained focus would be pure downside.
    refetchOnWindowFocus: false,
  })
  const v = q.data

  const [form, setForm] = useState({
    full_name: '', email: '', phone: '', nationality: '', address_line: '',
    city: '', state: '', postal_code: '', country: 'India',
    id_type: 'aadhaar', id_number: '',
    room_id: '', adults: 1, children: 0,
    deposit_amount: '', deposit_method: '',
    signature_captured: false, policies_accepted: false,
    welcome_sent: false, key_issued: false,
  })
  // Form C is a separate record with its own table, so it gets its own
  // state rather than being folded into the guest fields: it is saved to a
  // different endpoint, and a foreign guest's visa is not a property of the
  // guest row the way their phone number is.
  const [formC, setFormC] = useState<FormCValues>({})
  const setFc = (k: keyof FormCValues, v: string) =>
    setFormC((f) => ({ ...f, [k]: v }))

  // The list arrives from the server, so the default cannot be chosen until
  // it does. Previously this file named the default itself — "UPI" — which is
  // the spelling that reached the ledger whenever nobody touched the buttons.
  useEffect(() => {
    if (methods.length === 0) return
    setForm((f) => (f.deposit_method ? f : { ...f, deposit_method: methods[0].value }))
  }, [methods])

  // Seed the form from the booking exactly once per unit. A background refetch
  // must never overwrite what someone has typed — losing a half-filled check-in
  // to a window-focus refresh is how a guest ends up standing at the desk twice.
  const seededFor = useRef<string>('')
  useEffect(() => {
    if (!v || seededFor.current === v.reservation_unit_id) return
    seededFor.current = v.reservation_unit_id
    setForm((f) => ({
      ...f,
      full_name: v.guest_name ?? '', email: v.email ?? '', phone: v.phone ?? '',
      nationality: v.nationality ?? '', address_line: v.address_line ?? '',
      city: v.city ?? '', state: v.state ?? '', postal_code: v.postal_code ?? '',
      country: v.country ?? 'India',
      id_type: v.id_type ?? 'aadhaar', id_number: v.id_number ?? '',
      room_id: v.assigned_room_id ?? '',
      adults: v.adults, children: v.children,
    }))
    // Whatever was already collected for this stay, so a half-filled form
    // survives the clerk walking away and coming back.
    getFormC(v.reservation_unit_id, propertyId)
      .then((d) => setFormC({
        passport_number: d.passport_number, visa_number: d.visa_number,
        passport_issue_place: d.passport_issue_place,
        passport_issue_date: d.passport_issue_date,
        passport_expiry_date: d.passport_expiry_date,
        visa_type: d.visa_type, visa_issue_place: d.visa_issue_place,
        visa_issue_date: d.visa_issue_date,
        visa_expiry_date: d.visa_expiry_date,
        arrived_in_india_on: d.arrived_in_india_on,
        arrived_in_india_at: d.arrived_in_india_at,
        address_in_india: d.address_in_india,
        next_destination: d.next_destination,
      }))
      // A missing Form C is the normal case, not an error worth a banner.
      .catch(() => undefined)
  }, [v, propertyId])

  const set = (k: string, val: unknown) => setForm((f) => ({ ...f, [k]: val }))

  function fail(e: unknown) {
    const er = e as { response?: { data?: { detail?: string } } }
    setError(er.response?.data?.detail ?? 'That did not work. Please try again.')
  }

  // Saved beside the guest, never instead of it. A failure here must not
  // lose the address the clerk just typed, so it runs after the guest save
  // has succeeded and reports separately -- and it is skipped entirely for
  // a guest who is not a foreign national, so no empty row appears on the
  // compliance register.
  async function persistFormC() {
    if (!needsFormC(form.nationality, form.id_type)) return
    try {
      await saveFormC(unitId, propertyId, {
        ...formC,
        full_name: form.full_name.trim() || null,
        nationality: form.nationality || null,
        permanent_address: [form.address_line, form.city, form.state,
                            form.postal_code, form.country]
          .filter(Boolean).join(', ') || null,
      })
    } catch (e) {
      const er = e as { response?: { data?: { detail?: string } } }
      setError(er.response?.data?.detail
        ?? 'The guest was saved, but the Form C details were not.')
    }
  }

  const saveGuest = useMutation({
    mutationFn: () => saveCheckInGuest(unitId, propertyId, {
      full_name: form.full_name.trim(), email: form.email || null,
      phone: form.phone || null, nationality: form.nationality || null,
      address_line: form.address_line || null, city: form.city || null,
      state: form.state || null, postal_code: form.postal_code || null,
      country: form.country || null,
      id_type: form.id_type || null, id_number: form.id_number || null,
    }),
    onSuccess: async () => { setError(''); await persistFormC(); q.refetch() },
    onError: fail,
  })

  const submit = useMutation({
    mutationFn: () => completeCheckIn(unitId, propertyId, {
      guest: {
        full_name: form.full_name.trim(), email: form.email || null,
        phone: form.phone || null, nationality: form.nationality || null,
        address_line: form.address_line || null, city: form.city || null,
        state: form.state || null, postal_code: form.postal_code || null,
        country: form.country || null,
        id_type: form.id_type || null, id_number: form.id_number || null,
      },
      room_id: v?.assigned_room_id ? null : (form.room_id || null),
      adults: Number(form.adults), children: Number(form.children),
      deposit_amount: Number(form.deposit_amount || 0),
      deposit_method: form.deposit_amount ? form.deposit_method : null,
      id_verified: Boolean(form.id_number),
      signature_captured: form.signature_captured,
      policies_accepted: form.policies_accepted,
      welcome_sent: form.welcome_sent,
      key_issued: form.key_issued,
    }),
    onSuccess: async (r) => {
      // After the check-in, not before: this is the moment the 24-hour
      // clock actually starts, and a Form C saved against a stay that then
      // failed to check in would sit on the register owed by nobody.
      await persistFormC()
      qc.invalidateQueries({ queryKey: ['arrivals'] })
      qc.invalidateQueries({ queryKey: ['rack'] })
      qc.invalidateQueries({ queryKey: ['checkInView', unitId] })
      qc.invalidateQueries({ queryKey: ['form-c-register'] })
      setError('')
      setDone({ room: r.room_code, warnings: r.warnings,
                deposit: Number(r.deposit_amount || 0) })
    },
    onError: (e) => {
      const er = e as { response?: { data?: { detail?: string } } }
      setError(er.response?.data?.detail ?? 'The check-in could not be completed.')
    },
  })

  if (q.isLoading) {
    return (
      <p className="flex items-center gap-2 p-8 text-sm text-slate-400">
        <Loader2 size={16} className="animate-spin" /> Loading check-in…
      </p>
    )
  }
  if (q.isError || !v) {
    return (
      <p className="rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700">
        That booking could not be loaded.
      </p>
    )
  }

  const room = v.assigned_room
    ?? v.available_rooms.find((r) => r.id === form.room_id)?.code

  // The same list the API enforces. Checking only in the browser would not be
  // checking at all — the screen is not the only way in — so this mirrors the
  // server rather than replacing it.
  const missing: string[] = []
  if (form.full_name.trim() === '') missing.push('Full Name')
  if (form.phone.trim() === '') missing.push('Mobile Number')
  if (form.address_line.trim() === '') missing.push('Address')
  if (form.city.trim() === '') missing.push('City')
  if (form.country.trim() === '') missing.push('Country')
  const postalProblem = postalCodeProblem(form.postal_code, form.country)
  if (postalProblem) missing.push('A valid Postal Code')
  if (form.id_type.trim() === '') missing.push('ID Type')
  if (form.id_number.trim() === '') missing.push('ID Number')
  if (!v.documents.some((d) => d.kind === 'id_front')) missing.push('ID Proof (Front)')
  if (v.assigned_room_id === null && form.room_id === '') missing.push('Assigned Room')
  if (!form.policies_accepted) missing.push('Resort policies accepted')

  const canSubmit = missing.length === 0 && !v.already_checked_in

  return (
    <div className="space-y-4">
      {/* -------------------------------------------------------- header --- */}
      <div>
        <Crumbs title="Guest Check-in" trail={[
          { label: 'Reservations', to: '/reservations/list' },
          { label: 'Arrivals', to: '/reservations/list?tab=arrivals' },
          { label: 'Check-in' }]} />
        <h1 className="mt-1 text-display text-ink">Guest Check-in</h1>
      </div>

      {/* Booking strip */}
      <div className="flex flex-wrap items-center gap-x-8 gap-y-3 rounded-2xl border border-slate-100 bg-white p-4 shadow-sm">
        <Head label="Reservation" value={v.number}
          badge={v.reservation_status} />
        <Head label="Guest" value={v.guest_name ?? 'No guest on file'} />
        <Head label="Room Type" value={v.room_type}
          sub={room ? `Room ${room}` : 'No room yet'} />
        <Head label="Stay Dates"
          value={`${day(v.arrival_date)} – ${day(v.departure_date)}`}
          sub={`${v.nights} Night${v.nights === 1 ? '' : 's'}`} />
      </div>

      {/* Only when the guest was already in before this screen was opened.
          Showing it straight after a successful check-in put "already checked
          in" directly under the button that had just done it, which reads as a
          rejection rather than a success — the done-block below says it. */}
      {v.already_checked_in && !submit.isSuccess && (
        <p className="flex items-center gap-2 rounded-xl bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
          <CheckCircle2 size={16} /> This guest is already checked in
          {v.assigned_room && <> — room {v.assigned_room}</>}.
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
      {/* What the desk needs the moment a check-in lands.
          
          This said "Checked in — room 301." and stopped. True, and no use:
          it did not name the guest, so a clerk working two arrivals at once
          could not tell which one had gone through; it did not say how long
          they are staying, which is the next thing said out loud to them;
          and it did not say whether any money is still owed, which is the
          one fact that decides whether the guest can simply be handed keys
          and sent up. All three were already on the screen and were thrown
          away at the moment they mattered most. */}
      {/* A modal, not a banner on the page.
          
          This was an inline strip near the top of the screen while
          "Complete Check-in" sits in the footer, three hundred lines of
          form below it. So the desk pressed the button, the confirmation
          appeared above the fold behind them, and the honest report was
          "no message after check-in" -- the message was there and nobody
          could ever have seen it.
          
          A modal because this is a finished transaction with things still
          to do -- print the card, take the balance -- not a notice that
          can fade. It has to be dismissed, which is also how the desk
          says "yes, I have dealt with this guest". */}
      {done && (
        <div className="fixed inset-0 z-50 grid place-items-center bg-slate-900/50 p-4"
          role="dialog" aria-modal="true"
          aria-label={`Checked in to room ${done.room}`}>
        <div className="w-full max-w-md rounded-2xl bg-white p-5 text-sm text-emerald-900 shadow-xl">
          <p className="flex items-center gap-2 text-lg font-semibold text-emerald-800">
            <CheckCircle2 size={20} />
            {v.guest_name || 'Guest'} is in room {done.room}
          </p>
          <p className="mt-1 text-emerald-900">
            {v.nights} night{v.nights === 1 ? '' : 's'} · out {day(v.departure_date)}
            {done.deposit > 0 && ` · ${money.format(done.deposit)} deposit taken`}
          </p>
          {/* Money, said plainly. A balance is not a warning -- it is normal
              and settled at check-out -- so it is stated rather than
              flagged, and only the zero case gets the reassuring wording. */}
          <p className="mt-0.5 text-emerald-900">
            {v.balance > 0
              ? `${money.format(v.balance)} to settle at check-out.`
              : 'Nothing outstanding.'}
          </p>
          {done.warnings.map((w) => (
            <p key={w} className="mt-1 flex items-start gap-2 text-emerald-900">
              <Info size={14} className="mt-0.5 shrink-0" /> {w}
            </p>
          ))}
          {/* The registration card first: it is the piece of paper the guest
              signs and the property files, and the only one of these three
              that has to happen before they walk away from the desk. */}
          <div className="mt-3 flex flex-wrap gap-2">
            <button
              onClick={() => void openRegistrationCard(
                propertyId, v.reservation_unit_id)}
              className="rounded-lg bg-brand px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-50">
              Registration card
            </button>
            <button
              onClick={() => navigate(`/reservations/${v.reservation_id}/folio`)}
              className="rounded-lg border border-emerald-300 px-3 py-1.5 text-xs font-semibold text-emerald-800">
              Open folio
            </button>
            <button onClick={() => navigate('/reservations/list?tab=arrivals')}
              className="rounded-lg border border-emerald-300 px-3 py-1.5 text-xs font-semibold text-emerald-800">
              Next arrival
            </button>
            {/* Staying on the check-in screen is a real choice -- the desk
                often wants to look at what it just recorded -- so closing
                is offered rather than forced by navigation. */}
            <button onClick={() => setDone(null)}
              className="rounded-lg px-3 py-1.5 text-xs font-semibold text-slate-500 hover:text-slate-700">
              Close
            </button>
          </div>
        </div>
        </div>
      )}

      <div className="grid gap-4 xl:grid-cols-3">
        {/* ------------------------------------------ 1. verification --- */}
        <Panel n={1} title="Guest Verification"
          right={v.id_verified && (
            <span className="flex items-center gap-1 rounded-full bg-emerald-100 px-2.5 py-1 text-xs font-semibold text-emerald-700">
              <ShieldCheck size={12} /> Verified
            </span>
          )}>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label={<>Full Name {req}</>}>
              <input className={input} value={form.full_name}
                onChange={(e) => set('full_name', e.target.value)} />
            </Field>
            <Field label={<>Mobile Number {req}</>}>
              <input className={input} value={form.phone}
                onChange={(e) => set('phone', e.target.value)} />
            </Field>
            <Field label="Email">
              <input className={input} value={form.email}
                onChange={(e) => set('email', e.target.value)} />
            </Field>
            <Field label="Nationality">
              <ListSelect className={input} value={form.nationality} options={COUNTRIES}
                placeholder="Select nationality"
                onChange={(v) => set('nationality', v)} />
            </Field>
            <Field label="Address" wide>
              <input className={input} value={form.address_line}
                placeholder="Street address"
                onChange={(e) => set('address_line', e.target.value)} />
            </Field>
            <Field label={<>City {req}</>}>
              <CityInput className={input} value={form.city} state={form.state}
                onChange={(v) => set('city', v)} />
            </Field>
            <Field label="State">
              <StateField className={input} value={form.state} country={form.country}
                onChange={(v) => set('state', v)} />
            </Field>
            <Field label="Postal Code">
              <PostalCodeInput className={input} value={form.postal_code}
                country={form.country} state={form.state} onChange={(v) => set('postal_code', v)} />
            </Field>
            <Field label={<>Country {req}</>}>
              <ListSelect className={input} value={form.country} options={COUNTRIES}
                placeholder="Select country"
                onChange={(v) => set('country', v)} />
            </Field>
            <Field label={<>ID Type {req}</>}>
              <Select className={input} value={form.id_type}
                onWheel={(e) => (e.target as HTMLSelectElement).blur()}
                onChange={(e) => set('id_type', e.target.value)}>
                {ID_TYPES.map((t) => (
                  <option key={t.code} value={t.code}>{t.label}</option>
                ))}
              </Select>
            </Field>
            <Field label={<>ID Number {req}</>}>
              <input className={input} value={form.id_number}
                onChange={(e) => set('id_number', e.target.value)} />
            </Field>
          </div>

          {/* Appears the moment a non-Indian nationality is chosen above,
              with the passport still on the counter. An Indian guest never
              sees it. */}
          {needsFormC(form.nationality, form.id_type) && (
            <FormCFields values={formC} onChange={setFc} idPrefix="ci" />
          )}

          {/* The guest's own photo sits first because it is the one taken
              from the camera every time; the ID sides follow. */}
          <div className="mt-3 grid gap-3 sm:grid-cols-3">
            <DocSlot guestId={v.guest_id} propertyId={propertyId}
              kind="guest_photo" title="Guest Photo" mirror cameraFirst
              doc={v.documents.find((d) => d.kind === 'guest_photo')}
              onChanged={() => q.refetch()} />
            <DocSlot guestId={v.guest_id} propertyId={propertyId}
              kind="id_front" title="ID Proof (Front)"
              doc={v.documents.find((d) => d.kind === 'id_front')}
              onChanged={() => q.refetch()} />
            <DocSlot guestId={v.guest_id} propertyId={propertyId}
              kind="id_back" title="ID Proof (Back)"
              doc={v.documents.find((d) => d.kind === 'id_back')}
              onChanged={() => q.refetch()} />
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <button
              disabled={form.full_name.trim() === '' || saveGuest.isPending
                        || v.already_checked_in || postalProblem !== null}
              onClick={() => saveGuest.mutate()}
              className="flex items-center gap-1.5 rounded-lg border border-brand px-3 py-1.5 text-sm font-semibold text-brand hover:bg-brand-light disabled:opacity-50">
              {saveGuest.isPending
                ? <Loader2 size={14} className="animate-spin" />
                : <UserPlus size={14} />}
              {v.guest_id === null ? 'Save guest details' : 'Update guest details'}
            </button>
            {v.guest_id === null && (
              <span className="text-xs text-slate-500">
                Save first to attach ID scans.
              </span>
            )}
          </div>
        </Panel>

        {/* ------------------------------------------------ 2. stay --- */}
        <Panel n={2} title="Stay &amp; Room">
          <Field label={<>Assigned Room {req}</>}>
            {v.assigned_room_id ? (
              <>
                <p className={`${input} bg-slate-50`}>{v.assigned_room}</p>
                <Link to={`/front-desk/room-move/${v.reservation_unit_id}`}
                  className="mt-1 inline-block text-xs font-semibold text-brand hover:underline">
                  Move to a different room
                </Link>
              </>
            ) : v.available_rooms.length === 0 ? (
              <p className="rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-900">
                No {v.room_type} room is free for these dates. Free one up, or
                move the booking to another room type.
              </p>
            ) : (
              <Select className={input} value={form.room_id}
                onWheel={(e) => (e.target as HTMLSelectElement).blur()}
                onChange={(e) => set('room_id', e.target.value)}>
                <option value="">Choose a room…</option>
                {v.available_rooms.map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.code}{r.floor ? ` · Floor ${r.floor}` : ''}
                    {r.view_type ? ` · ${r.view_type}` : ''}
                  </option>
                ))}
              </Select>
            )}
            {!v.assigned_room_id && v.available_rooms.length > 0 && (
              <span className="mt-1 block text-xs text-slate-500">
                {v.available_rooms.length} free for the whole stay.
              </span>
            )}
          </Field>
          <Field label="Room Type">
            <p className={`${input} bg-slate-50`}>{v.room_type}</p>
          </Field>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label={<>Adults {req}</>}>
              <input type="number" min={1} className={input} value={form.adults}
                onChange={(e) => set('adults', e.target.value)} />
            </Field>
            <Field label="Children">
              <input type="number" min={0} className={input} value={form.children}
                onChange={(e) => set('children', e.target.value)} />
            </Field>
          </div>
          {v.room_description && (
            <p className="flex items-start gap-2 rounded-lg bg-sky-50 p-3 text-sm">
              <BedDouble size={18} className="mt-0.5 shrink-0 text-sky-600" />
              <span>
                <strong className="block font-semibold text-slate-800">
                  {v.room_type}
                </strong>
                <span className="text-slate-600">{v.room_description}</span>
              </span>
            </p>
          )}
        </Panel>

        {/* --------------------------------------- 3. payment/deposit --- */}
        <Panel n={3} title="Payment &amp; Deposit">
          <dl className="space-y-1.5 text-sm">
            <Line label="Quoted stay total" value={money.format(v.total_stay_amount)} />
            <Line label="Amount Paid" value={money.format(v.amount_paid)} tone="emerald" />
            <Line label="Still to pay" value={money.format(v.balance)}
              tone={v.balance > 0 ? 'amber' : 'emerald'} />
          </dl>
          {/* Room revenue accrues nightly, so the folio is near zero at
              check-in and grows as the guest stays. These are the *quoted*
              figures for the whole stay -- calling this "Balance" put a
              second, much larger number under the same word as the folio
              balance a cashier collects against. */}
          <p className="mt-1.5 text-[11px] leading-snug text-slate-400">
            Quoted for the whole stay. Room charges post one night at a time,
            so the folio balance today will be lower.
          </p>
          <hr className="my-3 border-slate-100" />

          <Field label="Security Deposit (INR)">
            <input type="number" min={0} className={input}
              value={form.deposit_amount} placeholder="0"
              onChange={(e) => set('deposit_amount', e.target.value)} />
            <span className="mt-1 block text-xs text-slate-500">
              Refundable at check-out, subject to damage inspection.
            </span>
          </Field>
          {Number(form.deposit_amount) > 0 && (
            <>
              <Field label="Payment Method">
                <div className="flex flex-wrap gap-2">
                  {methods.map((m) => (
                    <button key={m.value} onClick={() => set('deposit_method', m.value)}
                      className={`flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-sm font-medium ${
                        form.deposit_method === m.value
                          ? 'border-brand bg-brand-light text-brand'
                          : 'border-slate-200 text-slate-600 hover:bg-slate-50'}`}>
                      {m.icon}{m.label}
                    </button>
                  ))}
                </div>
              </Field>
              {v.folio_id === null && (
                <p className="rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-500">
                  A folio will be opened for this stay when you complete the
                  check-in, and the deposit posted to it.
                </p>
              )}
            </>
          )}

          <div className="mt-3 space-y-2">
            <Check checked={form.signature_captured}
              onChange={(b) => set('signature_captured', b)}
              label="Guest signature captured" />
            <Check checked={form.policies_accepted}
              onChange={(b) => set('policies_accepted', b)}
              label={<>Resort policies accepted {req}</>} />
            <Check checked={form.welcome_sent}
              onChange={(b) => set('welcome_sent', b)}
              label="Welcome message sent"
              note="Recorded only — there is no mail or SMS transport yet." />
            <Check checked={form.key_issued}
              onChange={(b) => set('key_issued', b)}
              label="Room key issued"
              note="Recorded only — no door-lock integration, nothing is encoded." />
          </div>
        </Panel>
      </div>

      {/* -------------------------------------------------------- footer --- */}
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-slate-100 bg-white p-4 shadow-sm">
        <div className="text-sm text-slate-500">
          {v.already_checked_in && !submit.isSuccess
            ? 'Already checked in.' : canSubmit ? (
            <>Checking <strong className="text-slate-700">
              {form.full_name || 'this guest'}</strong> into room{' '}
              <strong className="text-slate-700">{room}</strong>.</>
          ) : (
            <span className="flex flex-wrap items-center gap-x-1.5">
              <AlertTriangle size={14} className="text-amber-500" />
              Still needed:
              {missing.map((m) => (
                <span key={m}
                  className="rounded bg-amber-50 px-1.5 py-0.5 text-xs font-medium text-caution">
                  {m}
                </span>
              ))}
            </span>
          )}
        </div>
        <div className="flex gap-2">
          <button onClick={() => navigate('/reservations/list?tab=arrivals')}
            className="flex items-center gap-2 rounded-lg border border-slate-200 px-4 py-2.5 text-sm font-semibold text-slate-600 hover:bg-slate-50">
            <ArrowLeft size={15} /> Cancel
          </button>
          <button disabled={!canSubmit || submit.isPending}
            onClick={() => submit.mutate()}
            className="flex items-center gap-2 rounded-lg bg-brand px-5 py-2.5 text-sm font-semibold text-white hover:bg-brand-dark disabled:opacity-50">
            {submit.isPending
              ? <Loader2 size={16} className="animate-spin" />
              : <KeyRound size={16} />}
            Complete Check-in
          </button>
        </div>
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------ bits --- */
function Head({ label: l, value, sub, badge }: {
  label: string; value: string; sub?: string; badge?: string
}) {
  return (
    <div>
      <p className="text-xs text-slate-400">{l}</p>
      <p className="flex items-center gap-2 font-semibold text-slate-800">
        {value}
        {badge && (
          <span className="rounded-full bg-emerald-100 px-2.5 py-0.5 text-xs font-semibold capitalize text-emerald-700">
            {badge}
          </span>
        )}
      </p>
      {sub && <p className="text-xs text-slate-500">{sub}</p>}
    </div>
  )
}

function Panel({ n, title, right, children }: {
  n: number; title: string; right?: React.ReactNode; children: React.ReactNode
}) {
  return (
    <section className="rounded-2xl border border-slate-100 bg-white p-4 shadow-sm">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 className="flex items-center gap-2 font-semibold text-ink">
          <span className="grid h-7 w-7 place-items-center rounded-full bg-brand text-xs font-bold text-white">
            {n}
          </span>
          {title}
        </h2>
        {right}
      </div>
      <div className="space-y-3">{children}</div>
    </section>
  )
}

function Field({ label: l, children, wide }: {
  label: React.ReactNode; children: React.ReactNode; wide?: boolean
}) {
  return (
    <div className={wide ? 'sm:col-span-2' : ''}>
      <span className={label}>{l}</span>
      {children}
    </div>
  )
}

function Line({ label: l, value, tone }: {
  label: string; value: string; tone?: 'emerald' | 'amber'
}) {
  const colour = tone === 'emerald' ? 'text-emerald-600'
    : tone === 'amber' ? 'text-amber-600' : 'text-slate-800'
  return (
    <div className="flex items-center justify-between">
      <dt className="text-slate-500">{l}</dt>
      <dd className={`font-semibold tabular-nums ${colour}`}>{value}</dd>
    </div>
  )
}

function Check({ checked, onChange, label: l, note }: {
  checked: boolean; onChange: (b: boolean) => void
  label: React.ReactNode; note?: string
}) {
  return (
    <label className="flex cursor-pointer items-start gap-2 text-sm">
      <input type="checkbox" checked={checked} className="mt-0.5"
        onChange={(e) => onChange(e.target.checked)} />
      <span>
        <span className="text-slate-700">{l}</span>
        {note && <span className="block text-xs text-slate-400">{note}</span>}
      </span>
    </label>
  )
}

/** An ID scan slot: shows what is stored, or takes one. */
function DocSlot({ guestId, propertyId, kind, title, doc, onChanged,
  mirror = false, cameraFirst = false }: {
  guestId: string | null
  propertyId: string
  kind: string
  title: string
  doc?: GuestDoc
  onChanged: () => void
  /** Mirror the live preview — natural for a face, wrong for a document. */
  mirror?: boolean
  /** Lead with the camera where that is how the slot is normally filled. */
  cameraFirst?: boolean
}) {
  const fileRef = useRef<HTMLInputElement>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [camera, setCamera] = useState(false)

  async function pick(file: File | undefined) {
    if (!file || !guestId) return
    setBusy(true)
    setErr('')
    try {
      await uploadGuestDocument(guestId, propertyId, kind, file)
      onChanged()
    } catch (e) {
      const er = e as { response?: { data?: { detail?: string } } }
      setErr(er.response?.data?.detail ?? 'That file could not be uploaded.')
    } finally { setBusy(false) }
  }

  async function remove() {
    if (!doc || !guestId) return
    setBusy(true)
    try {
      await deleteGuestDocument(guestId, doc.id, propertyId)
      onChanged()
    } finally { setBusy(false) }
  }

  return (
    <div>
      <span className={label}>{title}</span>
      <div className="relative overflow-hidden rounded-lg border border-dashed border-slate-200 bg-slate-50">
        {doc?.url ? (
          <>
            {doc.content_type === 'application/pdf' ? (
              <a href={doc.url} target="_blank" rel="noreferrer"
                className="flex h-24 items-center justify-center text-sm font-medium text-brand">
                Open PDF
              </a>
            ) : (
              <img src={doc.url} alt={title} className="h-24 w-full object-cover" />
            )}
            <button onClick={remove} disabled={busy} aria-label={`Remove ${title}`}
              className="absolute right-1 top-1 grid h-6 w-6 place-items-center rounded-full bg-white/90 text-slate-500 hover:text-red-600">
              <X size={13} />
            </button>
          </>
        ) : (
          <div className="flex h-24 w-full items-center justify-center gap-2 text-xs text-slate-500">
            {busy ? <Loader2 size={16} className="animate-spin" /> : !guestId
              ? <span>Save the guest first</span>
              : (
                <>
                  {/* The camera is how a desk actually fills these — the guest
                      is standing there and their ID is in hand. The file
                      picker stays for scans that arrived by email. */}
                  <button onClick={() => setCamera(true)}
                    className={`flex flex-col items-center gap-1 rounded-lg px-3 py-2 ${
                      cameraFirst
                        ? 'bg-brand text-white hover:bg-brand-dark'
                        : 'text-slate-500 hover:bg-slate-100'}`}>
                    <Camera size={16} />
                    Camera
                  </button>
                  <button onClick={() => fileRef.current?.click()}
                    className="flex flex-col items-center gap-1 rounded-lg px-3 py-2 text-slate-500 hover:bg-slate-100">
                    <Upload size={16} />
                    File
                  </button>
                </>
              )}
          </div>
        )}
      </div>
      {doc?.url && (
        <span className="mt-1 flex gap-3 text-xs font-medium">
          <button onClick={() => setCamera(true)} disabled={busy}
            className="text-brand hover:underline">Retake</button>
          <button onClick={() => fileRef.current?.click()} disabled={busy}
            className="text-brand hover:underline">Replace with file</button>
        </span>
      )}
      {err && <p className="mt-1 text-xs text-red-600">{err}</p>}
      <input ref={fileRef} type="file" hidden
        accept="image/jpeg,image/png,image/webp,application/pdf"
        onChange={(e) => pick(e.target.files?.[0])} />
      <CameraCapture open={camera} title={title} mirror={mirror}
        onCancel={() => setCamera(false)}
        onCapture={(file) => { setCamera(false); void pick(file) }} />
    </div>
  )
}
