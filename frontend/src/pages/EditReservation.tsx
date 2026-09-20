/**
 * Edit Reservation — the booking as a form, laid out to the reference.
 *
 * The distinction this screen rests on, and the reason it is not one big
 * Save:
 *
 * **Booking information is a correction.** Source, segment, company, agent,
 * reference, notes — changing any of them changes how the booking is
 * *described*, costs nothing and affects nobody's room. It saves here.
 *
 * **Stay details are a sale.** Dates, room type and occupancy decide what is
 * sold and what it costs, so a change has to be priced against the rate
 * resolution the rest of the system uses and checked against availability —
 * which can refuse it. Typing a new departure date into a box and pressing
 * Save would either silently mis-price the booking or fail in a way nobody
 * could act on. So those fields are shown, and changing them hands over to
 * Modify Stay, which quotes first and shows the difference before committing.
 *
 * The reference draws both as plain inputs side by side. That is the one
 * place it is worth departing from: a field that looks identical to its
 * neighbour but behaves completely differently is a trap, and the trap costs
 * money.
 */
import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle, ArrowLeft, BedDouble, Building2, CalendarDays, Clock,
  FileText, Gift, Loader2, MessageSquare, Pencil, Save, ShieldCheck,
  Users, XCircle,
} from 'lucide-react'
import Select from '../components/Select'
import ListSelect from '../components/ListSelect'
import { PURPOSES_OF_STAY } from '../lib/options'
import { fmtDate, fmtDateTime } from '../lib/dates'
import { useOrgId } from '../hooks/useProperty'
import {
  getReservationFull, updateBookingDetails, listBookingAttributes,
  BOOKING_SOURCES,
} from '../api'

const field = 'w-full rounded-lg border border-slate-200 px-3 py-2.5 text-sm outline-none focus:border-brand'
const lbl = 'mb-1 block text-xs font-medium text-slate-600'
const readOnly = 'w-full rounded-lg border border-slate-200 bg-slate-50 px-3 py-2.5 text-sm text-slate-600'

/** A stored time as "12:00 PM". */
function hhmm(t?: string | null): string {
  if (!t) return 'Not given'
  const [h, m] = t.split(':').map(Number)
  if (Number.isNaN(h)) return 'Not given'
  return `${h % 12 === 0 ? 12 : h % 12}:${String(m ?? 0).padStart(2, '0')} ${h < 12 ? 'AM' : 'PM'}`
}

export default function EditReservation() {
  const { reservationId = '' } = useParams()
  const nav = useNavigate()
  const qc = useQueryClient()
  const orgId = useOrgId()

  const { data: r, isLoading } = useQuery({
    queryKey: ['reservation-full', reservationId],
    queryFn: () => getReservationFull(reservationId),
    enabled: reservationId !== '',
  })

  const sources = useQuery({
    queryKey: ['attrs', orgId, 'business_source'],
    queryFn: () => listBookingAttributes(orgId,
      { kind: 'business_source', status: 'active' }),
    enabled: orgId !== '',
  })
  const segments = useQuery({
    queryKey: ['attrs', orgId, 'market_segment'],
    queryFn: () => listBookingAttributes(orgId,
      { kind: 'market_segment', status: 'active' }),
    enabled: orgId !== '',
  })

  const [f, setF] = useState<Record<string, string> | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [saved, setSaved] = useState(false)

  if (isLoading || !r) {
    return (
      <p className="py-16 text-center text-sm text-slate-400">
        <Loader2 className="mx-auto mb-2 h-6 w-6 animate-spin" /> Loading…
      </p>
    )
  }

  const b = r.booking
  const form = f ?? {
    source: b.source ?? '',
    business_source_id: b.business_source_id ?? '',
    market_segment_id: b.market_segment_id ?? '',
    purpose_of_stay: b.purpose_of_stay ?? '',
    company_name: b.company_name ?? '',
    travel_agent: b.travel_agent ?? '',
    reference: b.reference ?? '',
    remarks: b.remarks ?? '',
    special_requests: b.special_requests ?? '',
  }
  const set = (k: string, v: string) => {
    setSaved(false)
    setF({ ...form, [k]: v })
  }
  const dirty = f !== null

  async function save() {
    if (!r) return
    setErr(''); setBusy(true)
    try {
      await updateBookingDetails(r.id, {
        source: form.source || null,
        business_source_id: form.business_source_id || null,
        market_segment_id: form.market_segment_id || null,
        purpose_of_stay: form.purpose_of_stay || null,
        company_name: form.company_name || null,
        travel_agent: form.travel_agent || null,
        reference: form.reference || null,
        remarks: form.remarks || null,
        special_requests: form.special_requests || null,
      })
      await qc.invalidateQueries({ queryKey: ['reservation-full'] })
      setF(null); setSaved(true)
    } catch (e) {
      const ax = e as { response?: { data?: { detail?: string } } }
      setErr(ax?.response?.data?.detail ?? 'The changes could not be saved.')
    } finally { setBusy(false) }
  }

  const unit = r.units[0]

  return (
    <div className="space-y-4">
      <Link to={`/reservations/${r.id}`}
        className="inline-flex items-center gap-1.5 text-sm text-slate-500 hover:text-brand">
        <ArrowLeft size={15} /> Back to reservation
      </Link>

      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <div className="flex min-w-0 flex-wrap items-baseline gap-x-3">
          <h1 className="text-2xl font-bold text-ink">
            Edit {r.number}
          </h1>
          <span className="text-sm text-slate-500">
            {r.guest?.full_name ?? 'No guest recorded'}
          </span>
        </div>
        <div className="flex items-center gap-2">
          {saved && (
            <span className="text-sm font-medium text-emerald-700">Saved</span>
          )}
          <button onClick={() => void save()} disabled={!dirty || busy}
            className="flex items-center gap-1.5 rounded-xl bg-brand px-4 py-2 text-sm font-semibold text-white enabled:hover:bg-brand-dark disabled:opacity-40">
            {busy ? <Loader2 size={15} className="animate-spin" />
              : <Save size={15} />}
            Save changes
          </button>
        </div>
      </div>

      {err && (
        <p className="flex items-start gap-2 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700">
          <AlertTriangle size={16} className="mt-0.5 shrink-0" />{err}
        </p>
      )}

      {/* lg for the same reason as the folio: xl fires at 1280 and a
          150%-scaled 1920 screen is 1277. */}
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,20rem)]">
        <div className="min-w-0 space-y-4">
          <Card title="Booking information" icon={FileText}>
            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
              <Field label="Booking source">
                <Select className={field} value={form.source}
                  onChange={(e) => set('source', e.target.value)}>
                  <option value="">Not recorded</option>
                  {BOOKING_SOURCES.map((s) => (
                    <option key={s.value} value={s.value}>{s.label}</option>
                  ))}
                </Select>
              </Field>
              <Field label="Confirmation no.">
                {/* The booking's identity. Changing it would orphan every
                    reference to it — on a folio, in a guest's email, in an
                    OTA's extranet. */}
                <p className={readOnly}>{r.number}</p>
              </Field>
              <Field label="Booking status">
                <p className={`${readOnly} capitalize`}>
                  {r.status.replace(/_/g, ' ')}
                </p>
              </Field>
              <Field label="Purpose of stay">
                <ListSelect value={form.purpose_of_stay}
                  onChange={(v) => set('purpose_of_stay', v)}
                  options={PURPOSES_OF_STAY} placeholder="Not recorded"
                  className={field} />
              </Field>
              <Field label="Rate plan">
                {/* Priced, so it moves through Modify Stay. */}
                <p className={readOnly}>
                  {unit?.rate_plan ?? 'Room’s own rate'}
                </p>
              </Field>
              <Field label="Market segment">
                <Select className={field} value={form.market_segment_id}
                  onChange={(e) => set('market_segment_id', e.target.value)}>
                  <option value="">Not recorded</option>
                  {(segments.data?.rows ?? []).map((a) => (
                    <option key={a.id} value={a.id}>{a.name}</option>
                  ))}
                </Select>
              </Field>
              <Field label="Channel / business source">
                <Select className={field} value={form.business_source_id}
                  onChange={(e) => set('business_source_id', e.target.value)}>
                  <option value="">Not recorded</option>
                  {(sources.data?.rows ?? []).map((a) => (
                    <option key={a.id} value={a.id}>{a.name}</option>
                  ))}
                </Select>
              </Field>
              <Field label="Their reference">
                <input className={field} value={form.reference}
                  placeholder="PO or OTA booking number"
                  onChange={(e) => set('reference', e.target.value)} />
              </Field>
              <Field label="Company">
                <input className={field} value={form.company_name}
                  placeholder="Booking on behalf of"
                  onChange={(e) => set('company_name', e.target.value)} />
              </Field>
              <Field label="Travel agent">
                <input className={field} value={form.travel_agent}
                  placeholder="Agency that made the booking"
                  onChange={(e) => set('travel_agent', e.target.value)} />
              </Field>
            </div>
          </Card>

          <Card title="Stay details" icon={BedDouble}
            action={
              r.can_modify ? (
                <button onClick={() => nav(`/reservations/${r.id}/modify`)}
                  className="flex items-center gap-1.5 rounded-lg bg-brand-light px-3 py-1.5 text-xs font-semibold text-brand hover:bg-brand/15">
                  <Pencil size={13} /> Change stay
                </button>
              ) : undefined
            }>
            {/* Shown, not typed into. Every one of these decides what is sold
                and what it costs — Modify Stay prices the change and checks
                availability, and can refuse. A box that looked editable and
                then silently mis-priced a booking would be worse than no box. */}
            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
              <Field label="Arrival">
                <p className={readOnly}>
                  {r.arrival_date ? fmtDate(r.arrival_date) : '—'}
                  <span className="ml-2 text-xs text-slate-500">
                    {hhmm(unit?.expected_arrival_time)}
                  </span>
                </p>
              </Field>
              <Field label="Departure">
                <p className={readOnly}>
                  {r.departure_date ? fmtDate(r.departure_date) : '—'}
                  <span className="ml-2 text-xs text-slate-500">
                    {hhmm(unit?.expected_departure_time)}
                  </span>
                </p>
              </Field>
              <Field label="Nights">
                <p className={readOnly}>{r.nights ?? '—'}</p>
              </Field>
              <Field label="Room type">
                <p className={readOnly}>{unit?.room_type ?? '—'}</p>
              </Field>
              <Field label="Adults">
                <p className={readOnly}>{r.adults}</p>
              </Field>
              <Field label="Children">
                <p className={readOnly}>{r.children}</p>
              </Field>
              <Field label="Rooms">
                <p className={readOnly}>{r.rooms}</p>
              </Field>
              <Field label="Board">
                <p className={readOnly}>{unit?.meal_plan ?? 'Room only'}</p>
              </Field>
            </div>
          </Card>

          <div className="grid gap-4 lg:grid-cols-2">
            <Card title="Package &amp; inclusions" icon={Gift}>
              <Field label="Package">
                <p className={readOnly}>{unit?.package ?? 'None'}</p>
              </Field>
              <div className="mt-3">
                <Field label="Inclusions">
                  <p className={readOnly}>{unit?.meal_plan ?? 'Room only'}</p>
                </Field>
              </div>
            </Card>

            <Card title="Cancellation" icon={ShieldCheck}>
              <p className="text-sm font-medium text-ink">
                {r.policy.name ?? 'Property default'}
              </p>
              <p className="mt-1.5 text-sm text-slate-600">
                {r.policy.text
                  ?? (r.policy.free_until_days !== null
                    ? `Free cancellation up to ${r.policy.free_until_days} days `
                      + `before arrival; ${r.policy.penalty_nights ?? 0} night`
                      + `${r.policy.penalty_nights === 1 ? '' : 's'} after that.`
                    : 'The property’s default policy applies at the time of '
                      + 'cancellation.')}
              </p>
            </Card>
          </div>

          <Card title="Special requests &amp; notes" icon={MessageSquare}>
            <div className="grid gap-4 lg:grid-cols-2">
              <Field label="Special requests">
                <textarea rows={3} className={field}
                  value={form.special_requests}
                  placeholder="What the guest asked for"
                  onChange={(e) => set('special_requests', e.target.value)} />
              </Field>
              <Field label="Internal notes">
                {/* Not shown to the guest, and the label has to say so —
                    somebody will otherwise write what they would not say. */}
                <textarea rows={3} className={field} value={form.remarks}
                  placeholder="Seen by staff only"
                  onChange={(e) => set('remarks', e.target.value)} />
              </Field>
            </div>
          </Card>
        </div>

        {/* ------------------------------------------------------ side -- */}
        <aside className="space-y-4">
          <Card title="Stay summary" icon={CalendarDays}>
            <dl className="space-y-2 text-sm">
              <Row k="Arrival" v={r.arrival_date ? fmtDate(r.arrival_date) : '—'} />
              <Row k="Departure" v={r.departure_date ? fmtDate(r.departure_date) : '—'} />
              <Row k="Nights" v={String(r.nights ?? '—')} />
              <Row k="Room type" v={unit?.room_type ?? '—'} />
              <Row k="Room" v={r.units.map((u) => u.room_code)
                .filter(Boolean).join(', ') || 'Not assigned'} />
              <Row k="Occupancy"
                v={`${r.adults} adult${r.adults === 1 ? '' : 's'}`
                  + (r.children ? `, ${r.children} child${r.children === 1 ? '' : 'ren'}` : '')} />
            </dl>
          </Card>

          <Card title="Company / travel agent" icon={Building2}>
            <dl className="space-y-2 text-sm">
              <Row k="Company" v={b.company_name ?? 'None'} />
              <Row k="Travel agent" v={b.travel_agent ?? 'None'} />
              <Row k="Reference" v={b.reference ?? '—'} />
            </dl>
          </Card>

          <Card title="Booking timeline" icon={Clock}>
            {r.activity.length === 0 ? (
              <p className="text-sm text-slate-500">Nothing recorded yet.</p>
            ) : (
              <ol className="space-y-3">
                {r.activity.slice(0, 6).map((a, i) => (
                  <li key={i} className="flex gap-3">
                    <span className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-brand" />
                    <span className="min-w-0">
                      <span className="block text-sm font-medium text-ink">
                        {a.label}
                      </span>
                      <span className="block text-xs text-slate-500">
                        {fmtDateTime(a.at)} · {a.actor}
                      </span>
                    </span>
                  </li>
                ))}
              </ol>
            )}
          </Card>

          <Card title="Quick actions" icon={Pencil}>
            <div className="space-y-2">
              {r.can_modify && (
                <button onClick={() => nav(`/reservations/${r.id}/modify`)}
                  className="flex w-full items-center gap-2 rounded-xl bg-brand-light px-3.5 py-2.5 text-sm font-semibold text-brand hover:bg-brand/15">
                  <Pencil size={15} /> Modify stay
                </button>
              )}
              <Link to={`/reservations/${r.id}`}
                className="flex w-full items-center gap-2 rounded-xl bg-slate-75 px-3.5 py-2.5 text-sm font-semibold text-slate-600 hover:bg-slate-100">
                <Users size={15} /> View reservation
              </Link>
              {r.can_cancel && (
                <button onClick={() => nav(`/reservations/${r.id}/modify`)}
                  className="flex w-full items-center gap-2 rounded-xl bg-red-50 px-3.5 py-2.5 text-sm font-semibold text-red-600 hover:bg-red-100">
                  <XCircle size={15} /> Cancel reservation
                </button>
              )}
            </div>
          </Card>
        </aside>
      </div>
    </div>
  )
}

function Card({ title, icon: Icon, action, children }: {
  title: string
  icon: typeof FileText
  action?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <div className="rounded-2xl border border-slate-100 bg-white p-5">
      <div className="mb-4 flex items-center justify-between gap-3">
        <h2 className="flex items-center gap-2 text-sm font-semibold text-ink">
          <Icon size={16} className="text-brand" /> {title}
        </h2>
        {action}
      </div>
      {children}
    </div>
  )
}

function Field({ label, children }: {
  label: string; children: React.ReactNode
}) {
  return <label className="block"><span className={lbl}>{label}</span>{children}</label>
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="text-slate-500">{k}</dt>
      <dd className="truncate text-right font-medium text-ink">{v}</dd>
    </div>
  )
}
