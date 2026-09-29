import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  BedDouble, CalendarDays, CheckCircle2, ClipboardCheck, CreditCard,
  Loader2, MapPin, MessageSquare, Phone, Upload,
} from 'lucide-react'
import {
  ID_TYPES, createStayRequest, openStay, submitWebCheckin, uploadStayDocument,
  type GuestStay,
} from '../api'
import { FORM_C_REQUIRED, needsFormC } from '../lib/formC'
import { errorText, inputCls } from '../lib/forms'
import { COUNTRIES, INDIAN_STATES } from '../lib/options'

/** The guest portal: a guest's own booking, opened from a private link.
 *
 *  No login. The link is the credential, the property comes from its URL, and
 *  a wrong or expired link shows the same "not valid" page as one that never
 *  existed (see portal_routes.py).
 *
 *  Written for a phone, because that is where the link arrives: one column,
 *  big targets, the booking first and the actions under it.
 *
 *  Online check-in is a request, not a check-in. What the guest types waits
 *  for the desk, which checks the ID in person before applying it, and the
 *  page says so rather than telling the guest they are checked in.
 */

const REQUEST_KINDS = [
  { value: 'housekeeping', label: 'Housekeeping' },
  { value: 'amenity', label: 'Towels, toiletries or extras' },
  { value: 'food', label: 'Food & drinks' },
  { value: 'maintenance', label: 'Something needs fixing' },
  { value: 'late_checkout', label: 'Late check-out' },
  { value: 'transport', label: 'Taxi or transfer' },
  { value: 'other', label: 'Something else' },
]
const REQUEST_STATUS: Record<string, string> = {
  open: 'Sent', in_progress: 'On it', done: 'Done', declined: 'Not possible',
}

const day = (d: string | null) => d
  ? new Date(`${d}T00:00:00`).toLocaleDateString('en-IN',
    { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' })
  : '—'
const money = (n: number, cur = 'INR') =>
  new Intl.NumberFormat('en-IN', { style: 'currency', currency: cur }).format(n)

export default function GuestPortal() {
  const { code = '', token = '' } = useParams()
  const { data: stay, isLoading, isError } = useQuery({
    queryKey: ['stay', code, token],
    queryFn: () => openStay(code, token),
    retry: false,
  })

  return (
    <div className="h-full overflow-y-auto bg-slate-50">
      <div className="mx-auto max-w-lg px-4 pb-16 pt-6">
        {isLoading && (
          <div className="flex justify-center py-24 text-slate-400">
            <Loader2 className="animate-spin" />
          </div>
        )}
        {isError && (
          <div className="mt-16 rounded-2xl border border-slate-100 bg-white p-8 text-center">
            <p className="text-lg font-semibold text-ink">This link is not valid</p>
            <p className="mt-2 text-sm text-slate-500">
              It may have expired or been replaced by a newer one. Please ask
              the hotel to send you a new link.
            </p>
          </div>
        )}
        {stay && <Stay stay={stay} code={code} token={token} />}
        <p className="mt-10 text-center text-xs text-slate-400">Powered by MyGuest</p>
      </div>
    </div>
  )
}

function Card({ icon: Icon, title, children }: {
  icon: typeof BedDouble; title: string; children: React.ReactNode
}) {
  return (
    <section className="mt-4 rounded-2xl border border-slate-100 bg-white p-5 shadow-sm">
      <h2 className="flex items-center gap-2 font-semibold text-ink">
        <Icon size={18} className="text-brand" /> {title}
      </h2>
      <div className="mt-3">{children}</div>
    </section>
  )
}

function Stay({ stay, code, token }: { stay: GuestStay; code: string; token: string }) {
  const first = (stay.guest_name || '').split(/\s+/)[0]
  const inHouse = stay.status === 'checked_in'
  return (
    <>
      <header>
        <p className="text-sm font-medium text-brand">{stay.property_name}</p>
        <h1 className="mt-1 text-2xl font-bold tracking-tight text-ink">
          {first ? `Hello, ${first}` : 'Your stay'}
        </h1>
      </header>

      <Card icon={CalendarDays} title={`Booking ${stay.reservation_number}`}>
        <div className="grid grid-cols-2 gap-3 text-sm">
          <div>
            <p className="text-xs text-slate-400">Check-in</p>
            <p className="font-medium text-ink">{day(stay.arrival)}</p>
            {stay.checkin_time && <p className="text-xs text-slate-500">from {stay.checkin_time}</p>}
          </div>
          <div>
            <p className="text-xs text-slate-400">Check-out</p>
            <p className="font-medium text-ink">{day(stay.departure)}</p>
            {stay.checkout_time && <p className="text-xs text-slate-500">by {stay.checkout_time}</p>}
          </div>
        </div>
        <ul className="mt-4 space-y-1.5 border-t border-slate-100 pt-3 text-sm">
          {stay.rooms.map((r, i) => (
            <li key={i} className="flex items-center justify-between gap-2">
              <span className="flex items-center gap-2 text-slate-700">
                <BedDouble size={15} className="text-slate-400" /> {r.room_type}
              </span>
              <span className="text-xs text-slate-500">
                {r.room ? `Room ${r.room} · ` : ''}{r.adults ?? 0} adult{r.adults === 1 ? '' : 's'}
                {r.children ? `, ${r.children} child${r.children === 1 ? '' : 'ren'}` : ''}
              </span>
            </li>
          ))}
        </ul>
      </Card>

      {stay.payment && stay.payment.url && (
        <Card icon={CreditCard} title="Payment due">
          <p className="text-sm text-slate-600">
            {stay.payment.purpose || 'An amount is due for your booking.'}
          </p>
          <a href={stay.payment.url} target="_blank" rel="noopener noreferrer"
            className="mt-4 flex items-center justify-center gap-2 rounded-xl bg-brand px-4 py-3.5 font-semibold text-white hover:bg-brand-dark">
            Pay {money(stay.payment.amount, stay.payment.currency)} securely
          </a>
          <p className="mt-2 text-center text-xs text-slate-400">
            Card, UPI or net banking, through Razorpay
          </p>
        </Card>
      )}
      {!stay.payment && stay.paid && (
        <p className="mt-4 flex items-center gap-2 rounded-2xl bg-emerald-50 px-5 py-3 text-sm text-emerald-800">
          <CheckCircle2 size={16} /> We have received your payment. Thank you.
        </p>
      )}

      {!inHouse && <WebCheckin stay={stay} code={code} token={token} />}
      <Requests stay={stay} code={code} token={token} />

      <Card icon={Phone} title="Contact the hotel">
        <div className="space-y-2 text-sm">
          {stay.property_phone && (
            <a href={`tel:${stay.property_phone.replace(/[^\d+]/g, '')}`}
              className="flex items-center gap-2 font-medium text-brand">
              <Phone size={15} /> {stay.property_phone}
            </a>
          )}
          {stay.property_address && (
            <p className="flex items-start gap-2 text-slate-600">
              <MapPin size={15} className="mt-0.5 shrink-0 text-slate-400" /> {stay.property_address}
            </p>
          )}
        </div>
      </Card>
    </>
  )
}

function WebCheckin({ stay, code, token }: { stay: GuestStay; code: string; token: string }) {
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const p = stay.prefill
  const [f, setF] = useState<Record<string, string>>({
    full_name: p.full_name ?? '', email: p.email ?? '', phone: p.phone ?? '',
    nationality: p.nationality ?? 'India', address_line: p.address_line ?? '',
    city: p.city ?? '', state: p.state ?? '', postal_code: p.postal_code ?? '',
    country: p.country ?? 'India', id_type: 'aadhaar', id_number: '',
    arrival_time: '', purpose_of_visit: '', special_requests: '',
    sex: '', date_of_birth: '', passport_number: '', passport_issue_place: '',
    passport_issue_date: '', passport_expiry_date: '', visa_number: '',
    visa_type: '', visa_issue_place: '', visa_issue_date: '', visa_expiry_date: '',
    arrived_in_india_on: '', arrived_in_india_at: '', next_destination: '',
  })
  const [agree, setAgree] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) =>
    setF((v) => ({ ...v, [k]: e.target.value }))
  const foreign = needsFormC(f.nationality, f.id_type)

  const submit = useMutation({
    mutationFn: () => {
      const body: Record<string, string | boolean | null> = { policies_accepted: agree }
      for (const [k, v] of Object.entries(f)) body[k] = v.trim() || null
      if (!foreign) {
        for (const k of Object.keys(body)) {
          if (k.startsWith('passport_') || k.startsWith('visa_') || k.startsWith('arrived_')
            || ['sex', 'date_of_birth', 'next_destination'].includes(k)) body[k] = null
        }
      }
      return submitWebCheckin(code, token, body)
    },
    onSuccess: (r) => {
      setMsg({ ok: true, text: r.detail }); setOpen(false)
      qc.invalidateQueries({ queryKey: ['stay', code, token] })
    },
    onError: (e) => setMsg({ ok: false, text: errorText(e, 'Could not send your details.') }),
  })

  const done = stay.web_checkin !== 'none'
  const missingFormC = foreign && FORM_C_REQUIRED.some((k) => !f[k]?.trim())

  return (
    <Card icon={ClipboardCheck} title="Check in online">
      {done && !open ? (
        <p className="flex items-start gap-2 text-sm text-emerald-800">
          <CheckCircle2 size={16} className="mt-0.5 shrink-0" />
          Your details are with the front desk. Bring your ID, and check-in
          will only take a moment.
        </p>
      ) : (
        <p className="text-sm text-slate-600">
          Share your details before you arrive and skip the paperwork at the desk.
        </p>
      )}
      {msg && (
        <p className={`mt-3 rounded-lg px-3 py-2 text-sm ${msg.ok ? 'bg-emerald-50 text-emerald-800' : 'bg-red-50 text-red-700'}`}>
          {msg.text}
        </p>
      )}
      {!open && (
        <button type="button" onClick={() => setOpen(true)}
          className="mt-4 w-full rounded-xl border border-brand px-4 py-3 font-semibold text-brand hover:bg-brand-light/40">
          {done ? 'Update my details' : 'Start online check-in'}
        </button>
      )}
      {open && (
        <form className="mt-4 space-y-3" onSubmit={(e) => { e.preventDefault(); submit.mutate() }}>
          <L label="Full name (as on your ID)"><input className={inputCls} value={f.full_name} onChange={set('full_name')} required minLength={2} /></L>
          <div className="grid grid-cols-2 gap-3">
            <L label="Mobile"><input className={inputCls} type="tel" value={f.phone} onChange={set('phone')} required /></L>
            <L label="Email"><input className={inputCls} type="email" value={f.email} onChange={set('email')} /></L>
          </div>
          <L label="Nationality">
            <select className={inputCls} value={f.nationality} onChange={set('nationality')}>
              {COUNTRIES.map((c) => <option key={c}>{c}</option>)}
            </select>
          </L>
          <L label="Address"><input className={inputCls} value={f.address_line} onChange={set('address_line')} /></L>
          <div className="grid grid-cols-2 gap-3">
            <L label="City"><input className={inputCls} value={f.city} onChange={set('city')} /></L>
            {f.nationality === 'India' ? (
              <L label="State">
                <select className={inputCls} value={f.state} onChange={set('state')}>
                  <option value="">Select</option>
                  {INDIAN_STATES.map((s) => <option key={s}>{s}</option>)}
                </select>
              </L>
            ) : (
              <L label="Country of residence">
                <select className={inputCls} value={f.country} onChange={set('country')}>
                  {COUNTRIES.map((c) => <option key={c}>{c}</option>)}
                </select>
              </L>
            )}
          </div>
          <div className="grid grid-cols-2 gap-3">
            <L label="ID type">
              <select className={inputCls} value={f.id_type} onChange={set('id_type')}>
                {ID_TYPES.map((t) => <option key={t.code} value={t.code}>{t.label}</option>)}
              </select>
            </L>
            <L label="ID number"><input className={inputCls} value={f.id_number} onChange={set('id_number')} /></L>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <L label="Expected arrival time"><input className={inputCls} type="time" value={f.arrival_time} onChange={set('arrival_time')} /></L>
            <L label="Purpose of visit"><input className={inputCls} value={f.purpose_of_visit} onChange={set('purpose_of_visit')} placeholder="Holiday" /></L>
          </div>

          {foreign && (
            <div className="space-y-3 rounded-xl bg-slate-50 p-3">
              <p className="text-xs text-slate-600">
                Indian law requires the hotel to report foreign guests to the
                Bureau of Immigration (Form C). Please add your passport and visa details.
              </p>
              <div className="grid grid-cols-2 gap-3">
                <L label="Passport number *"><input className={inputCls} value={f.passport_number} onChange={set('passport_number')} /></L>
                <L label="Date of birth"><input className={inputCls} type="date" value={f.date_of_birth} onChange={set('date_of_birth')} /></L>
                <L label="Passport issued at"><input className={inputCls} value={f.passport_issue_place} onChange={set('passport_issue_place')} /></L>
                <L label="Passport expires"><input className={inputCls} type="date" value={f.passport_expiry_date} onChange={set('passport_expiry_date')} /></L>
                <L label="Visa number *"><input className={inputCls} value={f.visa_number} onChange={set('visa_number')} /></L>
                <L label="Visa type *"><input className={inputCls} value={f.visa_type} onChange={set('visa_type')} placeholder="Tourist / e-Visa" /></L>
                <L label="Visa expires"><input className={inputCls} type="date" value={f.visa_expiry_date} onChange={set('visa_expiry_date')} /></L>
                <L label="Arrived in India on *"><input className={inputCls} type="date" value={f.arrived_in_india_on} onChange={set('arrived_in_india_on')} /></L>
                <L label="Arrived at (airport / port) *"><input className={inputCls} value={f.arrived_in_india_at} onChange={set('arrived_in_india_at')} /></L>
                <L label="Next destination"><input className={inputCls} value={f.next_destination} onChange={set('next_destination')} /></L>
              </div>
            </div>
          )}

          <L label="Anything we should know?">
            <textarea className={`${inputCls} min-h-[70px]`} value={f.special_requests} onChange={set('special_requests')} maxLength={500} />
          </L>
          <label className="flex items-start gap-2 text-sm text-slate-600">
            <input type="checkbox" checked={agree} onChange={(e) => setAgree(e.target.checked)} className="mt-0.5 h-4 w-4 accent-brand" />
            I confirm these details are correct and accept the hotel's policies.
          </label>
          {missingFormC && (
            <p className="text-xs text-amber-700">Fields marked * are needed for Form C.</p>
          )}
          <button type="submit" disabled={!agree || submit.isPending}
            className="flex w-full items-center justify-center gap-2 rounded-xl bg-brand px-4 py-3.5 font-semibold text-white hover:bg-brand-dark disabled:opacity-50">
            {submit.isPending && <Loader2 size={16} className="animate-spin" />}
            Send my details
          </button>
        </form>
      )}
      {done && <IdUpload stay={stay} code={code} token={token} />}
    </Card>
  )
}

function IdUpload({ stay, code, token }: { stay: GuestStay; code: string; token: string }) {
  const qc = useQueryClient()
  const [err, setErr] = useState('')
  const up = useMutation({
    mutationFn: ({ kind, file }: { kind: string; file: File }) => uploadStayDocument(code, token, kind, file),
    onSuccess: () => { setErr(''); qc.invalidateQueries({ queryKey: ['stay', code, token] }) },
    onError: (e) => setErr(errorText(e, 'The upload failed.')),
  })
  return (
    <div className="mt-4 border-t border-slate-100 pt-4">
      <p className="text-sm font-medium text-ink">Photo of your ID (optional)</p>
      <div className="mt-2 grid grid-cols-2 gap-3">
        {[['id_front', 'Front'], ['id_back', 'Back']].map(([kind, label]) => {
          const have = stay.documents.includes(kind)
          return (
            <label key={kind} className={`flex cursor-pointer flex-col items-center gap-1 rounded-xl border border-dashed p-4 text-sm ${
              have ? 'border-emerald-300 bg-emerald-50 text-emerald-800' : 'border-slate-300 text-slate-500 hover:border-brand hover:text-brand'}`}>
              {have ? <CheckCircle2 size={18} /> : <Upload size={18} />}
              {have ? `${label} received` : `Upload ${label.toLowerCase()}`}
              {!have && (
                <input type="file" accept="image/jpeg,image/png,image/webp,application/pdf" className="hidden"
                  onChange={(e) => { const file = e.target.files?.[0]; if (file) up.mutate({ kind, file }) }} />
              )}
            </label>
          )
        })}
      </div>
      {up.isPending && <p className="mt-2 text-xs text-slate-500">Uploading…</p>}
      {err && <p className="mt-2 text-xs text-red-600">{err}</p>}
    </div>
  )
}

function Requests({ stay, code, token }: { stay: GuestStay; code: string; token: string }) {
  const qc = useQueryClient()
  const [kind, setKind] = useState('housekeeping')
  const [message, setMessage] = useState('')
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const send = useMutation({
    mutationFn: () => createStayRequest(code, token, { kind, message: message.trim() }),
    onSuccess: (r) => {
      setMsg({ ok: true, text: r.detail }); setMessage('')
      qc.invalidateQueries({ queryKey: ['stay', code, token] })
    },
    onError: (e) => setMsg({ ok: false, text: errorText(e, 'Could not send your request.') }),
  })
  return (
    <Card icon={MessageSquare} title="Ask the front desk">
      <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); send.mutate() }}>
        <select className={inputCls} value={kind} onChange={(e) => setKind(e.target.value)}>
          {REQUEST_KINDS.map((k) => <option key={k.value} value={k.value}>{k.label}</option>)}
        </select>
        <textarea className={`${inputCls} min-h-[70px]`} value={message} required minLength={2} maxLength={500}
          onChange={(e) => setMessage(e.target.value)} placeholder="Tell us what you need" />
        <button type="submit" disabled={message.trim().length < 2 || send.isPending}
          className="w-full rounded-xl bg-ink px-4 py-3 font-semibold text-white disabled:opacity-50">
          Send request
        </button>
      </form>
      {msg && (
        <p className={`mt-3 rounded-lg px-3 py-2 text-sm ${msg.ok ? 'bg-emerald-50 text-emerald-800' : 'bg-red-50 text-red-700'}`}>
          {msg.text}
        </p>
      )}
      {stay.requests.length > 0 && (
        <ul className="mt-4 space-y-2 border-t border-slate-100 pt-3">
          {stay.requests.map((r, i) => (
            <li key={i} className="text-sm">
              <div className="flex items-center justify-between gap-2">
                <span className="text-slate-700">{REQUEST_KINDS.find((k) => k.value === r.kind)?.label ?? r.kind}</span>
                <span className="text-xs font-semibold text-brand">{REQUEST_STATUS[r.status] ?? r.status}</span>
              </div>
              <p className="text-xs text-slate-500">{r.message}</p>
              {r.staff_note && <p className="mt-0.5 text-xs text-slate-700">Hotel: {r.staff_note}</p>}
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

function L({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block text-xs font-medium text-slate-600">
      {label}
      <div className="mt-1">{children}</div>
    </label>
  )
}
