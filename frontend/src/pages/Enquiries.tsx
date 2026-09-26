import { useEffect, useMemo, useState } from 'react'
import { fmtDate, fmtDateTime } from '../lib/dates'
import Select from '../components/Select'
import { CONTROL_TYPE, FILTER_BOX, FILTER_SELECT } from '../lib/controls'
import { Link } from 'react-router-dom'
import { useQuery, useQueryClient, keepPreviousData } from '@tanstack/react-query'
import DateField from '../components/DateField'
import {
  Mail, Hourglass, Clock, CheckCircle2, Search, Plus, Loader2, X,
  AlertTriangle, Info, Sparkles, BedDouble, ArrowRight, Calendar,
  IndianRupee, Globe,
} from 'lucide-react'
import {
  getEnquiryBoard, createEnquiry, moveEnquiry, getEnquiryMatch, convertEnquiry,
  BOOKING_SOURCES, type EnquiryCard, type EnquiryMatch,
} from '../api'
import { Crumbs } from '../components/Crumbs'
import { useActivePropertyId } from '../hooks/useProperty'
import { errorText } from '../lib/forms'

const plain = (v: string | number | null) => v === null
  ? null
  : new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 }).format(Number(v))
const day = (d: string | null) => fmtDate(d)
const stamp = (d: string | null) => (d ? fmtDateTime(d) : null)

const COL_TONE: Record<string, string> = {
  new: 'bg-blue-50 text-blue-700',
  contacted: 'bg-amber-50 text-amber-700',
  waitlisted: 'bg-purple-50 text-purple-700',
  offered: 'bg-emerald-50 text-emerald-700',
  converted: 'bg-teal-50 text-teal-700',
}
// Where a card can go from where it is. Converted is absent on purpose: it is
// reached only through Convert to Booking, which creates a real reservation.
const NEXT: Record<string, string[]> = {
  new: ['contacted', 'waitlisted', 'lost'],
  contacted: ['waitlisted', 'offered', 'lost'],
  waitlisted: ['offered', 'contacted', 'lost'],
  offered: ['waitlisted', 'contacted', 'lost'],
  converted: [],
}
const MOVE_LABEL: Record<string, string> = {
  contacted: 'Contacted', waitlisted: 'Waitlist', offered: 'Offered',
  lost: 'Lost',
}

export default function Enquiries() {
  const propertyId = useActivePropertyId()
  const qc = useQueryClient()
  const [search, setSearch] = useState('')
  const [q, setQ] = useState('')
  const [channel, setChannel] = useState('')
  const [roomType, setRoomType] = useState('')
  const [due, setDue] = useState(false)
  const [newOpen, setNewOpen] = useState(false)
  const [selected, setSelected] = useState<EnquiryCard | null>(null)
  const [toast, setToast] = useState('')
  const [err, setErr] = useState('')

  useEffect(() => {
    const t = setTimeout(() => setQ(search), 300)
    return () => clearTimeout(t)
  }, [search])

  const params = useMemo(() => ({
    q: q || undefined, channel: channel || undefined,
    room_type_id: roomType || undefined, due: due || undefined,
  }), [q, channel, roomType, due])

  const { data, isLoading, isFetching } = useQuery({
    queryKey: ['enquiries', propertyId, params],
    queryFn: () => getEnquiryBoard(propertyId, params),
    enabled: propertyId !== '',
    placeholderData: keepPreviousData,
  })

  function refresh() { qc.invalidateQueries({ queryKey: ['enquiries'] }) }
  function flash(m: string) { setErr(''); setToast(m); setTimeout(() => setToast(''), 3000) }
  function fail(e: unknown) {
    setToast('')
    setErr(errorText(e, 'That did not work.'))
  }

  async function move(card: EnquiryCard, status: string) {
    if (status === 'lost') { setSelected(null); setLost(card); return }
    try {
      await moveEnquiry(card.id, { property_id: propertyId, status })
      flash(`${card.full_name} moved to ${MOVE_LABEL[status] ?? status}.`)
      refresh(); setSelected(null)
    } catch (e) { fail(e) }
  }
  const [lost, setLost] = useState<EnquiryCard | null>(null)

  const k = data?.kpis
  const cards = [
    { label: 'New Enquiries', value: k?.new_enquiries.value, delta: k?.new_enquiries.delta,
      icon: Mail, tint: 'text-blue-600', bg: 'bg-blue-50' },
    { label: 'Waitlisted', value: k?.waitlisted.value, delta: k?.waitlisted.delta,
      icon: Hourglass, tint: 'text-amber-600', bg: 'bg-amber-50' },
    { label: 'Follow-ups Due', value: k?.follow_ups_due, delta: undefined,
      icon: Clock, tint: 'text-rose-600', bg: 'bg-rose-50' },
    { label: 'Converted', value: k?.converted.value, delta: k?.converted.delta,
      icon: CheckCircle2, tint: 'text-emerald-600', bg: 'bg-emerald-50' },
  ]

  const sel = `${FILTER_SELECT} bg-white outline-none focus:border-brand`

  return (
    <div className="space-y-4">
      <Crumbs title="Waitlist & Enquiries" trail={[{ label: 'Reservations', to: '/reservations' },
        { label: 'Waitlist & Enquiries' }]} />
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <h1 className="flex items-center gap-2 text-display text-ink">
          <Hourglass size={26} className="text-brand" /> Waitlist &amp; Enquiries
        </h1>
        <div className="flex flex-wrap items-center gap-2">
          {data?.can_create !== false && (
            <button onClick={() => setNewOpen(true)}
              className="flex items-center gap-1.5 whitespace-nowrap rounded-lg bg-brand px-3 py-2 text-sm font-medium text-white hover:bg-brand-dark disabled:opacity-40">
              <Plus size={15} /> New Enquiry
            </button>
          )}
        </div>
      </div>

      {toast && (
        <p className="flex items-start gap-2 rounded-xl bg-emerald-50 px-4 py-3 text-sm text-emerald-700">
          <CheckCircle2 size={16} className="mt-0.5 shrink-0" /> {toast}
        </p>
      )}
      {err && (
        <p className="flex items-start gap-2 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700">
          <AlertTriangle size={16} className="mt-0.5 shrink-0" /> {err}
        </p>
      )}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {cards.map((c) => (
          <div key={c.label}
            className="flex items-center gap-4 rounded-2xl border border-slate-100 bg-white px-5 py-4">
            <span className={`flex h-12 w-12 shrink-0 items-center justify-center rounded-full ${c.bg}`}>
              <c.icon size={22} className={c.tint} />
            </span>
            <span className="min-w-0">
              <span className="block text-sm text-slate-600">{c.label}</span>
              <span className="block text-2xl font-bold text-slate-800">
                {c.value === undefined
                  ? <span className="text-slate-300">&mdash;</span> : c.value}
              </span>
              {/* Absent until there are two weeks to compare, rather than a
                  +0% that reads like flat demand. */}
              {c.delta !== undefined && c.delta !== null && (
                <span className={`block text-[11px] ${
                  c.delta > 0 ? 'text-emerald-600' : c.delta < 0 ? 'text-rose-600'
                    : 'text-slate-400'}`}>
                  {c.delta > 0 ? '+' : ''}{c.delta} vs last week
                </span>
              )}
            </span>
          </div>
        ))}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Select blankIsChoice value={channel} onChange={(e) => setChannel(e.target.value)}
          aria-label="Channel" className={sel}>
          <option value="">All Channels</option>
          {(data?.channels ?? []).map((c) => (
            <option key={c.value} value={c.value}>{c.label} ({c.count})</option>
          ))}
        </Select>
        <Select blankIsChoice value={roomType} onChange={(e) => setRoomType(e.target.value)}
          aria-label="Room type" className={sel}>
          <option value="">All Room Types</option>
          {(data?.room_types ?? []).map((r) => (
            <option key={r.value} value={r.value}>{r.label}</option>
          ))}
        </Select>
        {/* An on/off filter, filled green when on like every selected control. */}
        <button onClick={() => setDue(!due)} aria-pressed={due}
          className={`flex items-center gap-1.5 whitespace-nowrap ${FILTER_BOX} ${CONTROL_TYPE} ${
            due ? 'border-brand bg-brand text-white'
              : 'bg-white text-slate-600 hover:bg-slate-50'}`}>
          <Clock size={15} /> Follow-ups due
        </button>
        <div className="flex min-w-[240px] flex-1 items-center justify-end gap-2">
          {isFetching && !isLoading && (
            <Loader2 size={15} className="animate-spin text-slate-300" />
          )}
          <div className="relative min-w-0 max-w-sm flex-1">
            <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <input value={search} onChange={(e) => setSearch(e.target.value)}
              placeholder="Search name, phone or email…"
              className="w-full rounded-lg border border-slate-200 bg-slate-50 py-2 pl-9 pr-3 text-sm outline-none focus:border-brand" />
          </div>
        </div>
      </div>

      {isLoading ? (
        <p className="py-20 text-center text-slate-400">
          <Loader2 className="mx-auto animate-spin" />
        </p>
      ) : (
        <div className="flex gap-4 overflow-x-auto pb-2">
          {(data?.columns ?? []).map((col) => (
            <div key={col.key} className="w-72 shrink-0">
              <div className={`mb-3 flex items-center justify-between rounded-xl px-4 py-2.5 text-sm font-semibold ${COL_TONE[col.key]}`}>
                {col.label}
                <span className="opacity-70">({col.count})</span>
              </div>
              <div className="space-y-3">
                {col.cards.length === 0 && (
                  <p className="rounded-xl border border-dashed border-slate-200 py-6 text-center text-xs text-slate-400">
                    Nothing here
                  </p>
                )}
                {col.cards.map((c) => (
                  <button key={c.id} onClick={() => { setSelected(c); setErr('') }}
                    className="w-full rounded-xl border border-slate-100 bg-white p-3 text-left hover:border-brand hover:shadow-sm">
                    <span className="block font-semibold text-slate-800">
                      {c.full_name}
                    </span>
                    <span className="block text-xs text-slate-500">
                      {c.adults} Adult{c.adults === 1 ? '' : 's'}
                      {c.children > 0 && ` · ${c.children} Child${c.children === 1 ? '' : 'ren'}`}
                    </span>
                    <span className="mt-1.5 block space-y-0.5 text-xs text-slate-600">
                      {c.arrival_date && (
                        <span className="flex items-center gap-1.5">
                          <Calendar size={11} className="text-slate-400" />
                          {day(c.arrival_date)} – {day(c.departure_date)}
                        </span>
                      )}
                      {c.room_type && (
                        <span className="flex items-center gap-1.5">
                          <BedDouble size={11} className="text-slate-400" /> {c.room_type}
                        </span>
                      )}
                      {(c.budget_min || c.budget_max) && (
                        <span className="flex items-center gap-1.5">
                          <IndianRupee size={11} className="text-slate-400" />
                          {plain(c.budget_min) ?? '…'} – {plain(c.budget_max) ?? '…'}
                        </span>
                      )}
                      {c.channel_label && (
                        <span className="flex items-center gap-1.5">
                          <Globe size={11} className="text-slate-400" /> {c.channel_label}
                        </span>
                      )}
                    </span>
                    {c.reservation_number && (
                      <span className="mt-1.5 flex items-center gap-1.5 text-xs font-semibold text-teal-700">
                        <CheckCircle2 size={11} /> {c.reservation_number}
                      </span>
                    )}
                    {c.next_follow_up_at && !c.reservation_number && (
                      <span className={`mt-1.5 flex items-center gap-1.5 text-xs font-medium ${
                        c.follow_up_due ? 'text-rose-600' : 'text-slate-500'}`}>
                        <Clock size={11} />
                        Follow up: {stamp(c.next_follow_up_at)}
                      </span>
                    )}
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      {(data?.lost_count ?? 0) > 0 && (
        <p className="text-xs text-slate-400">
          {data!.lost_count} enquir{data!.lost_count === 1 ? 'y' : 'ies'} closed as
          lost, and off the board.
        </p>
      )}

      {/* Said out loud rather than filled in with a plausible number. */}
      <p className="flex items-start gap-2 text-xs text-slate-400">
        <Info size={13} className="mt-0.5 shrink-0" />
        The availability match is real inventory, not a suggestion — the same
        arithmetic the booking screen uses. There is no Send Offer button because
        there is no mail or messaging transport, and no conversion-probability
        score because there is no model behind one.
      </p>

      {newOpen && (
        <NewEnquiry propertyId={propertyId} roomTypes={data?.room_types ?? []}
          onClose={() => setNewOpen(false)}
          onDone={(m) => { flash(m); refresh() }} onFail={fail} />
      )}
      {selected && (
        <Drawer card={selected} propertyId={propertyId}
          canConvert={data?.can_convert !== false}
          onClose={() => setSelected(null)} onMove={move}
          onDone={(m) => { flash(m); refresh(); setSelected(null) }} onFail={fail} />
      )}
      {lost && (
        <LostModal card={lost} propertyId={propertyId}
          onClose={() => setLost(null)}
          onDone={(m) => { flash(m); refresh() }} onFail={fail} />
      )}
    </div>
  )
}

const inputCls = 'w-full rounded-lg border border-slate-200 px-3 py-2.5 text-sm outline-none focus:border-brand'

function Shell({ title, onClose, wide, children }: {
  title: string; onClose: () => void; wide?: boolean; children: React.ReactNode
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4"
      onClick={onClose}>
      <div className={`w-full ${wide ? 'max-w-2xl' : 'max-w-md'} max-h-[90vh] overflow-y-auto rounded-2xl bg-white p-6 shadow-xl`}
        onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold text-ink">{title}</h2>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600">
            <X size={18} />
          </button>
        </div>
        {children}
      </div>
    </div>
  )
}

function NewEnquiry({ propertyId, roomTypes, onClose, onDone, onFail }: {
  propertyId: string; roomTypes: { value: string; label: string }[]
  onClose: () => void; onDone: (m: string) => void; onFail: (e: unknown) => void
}) {
  const [f, setF] = useState({
    full_name: '', phone: '', email: '', arrival_date: '', departure_date: '',
    adults: 2, children: 0, room_type_id: '', budget_min: '', budget_max: '',
    channel: 'phone', next_follow_up_at: '', notes: '',
  })
  const [busy, setBusy] = useState(false)
  const set = (k: string, v: string | number) => setF((p) => ({ ...p, [k]: v }))
  const datesOk = !f.arrival_date || !f.departure_date
    || f.departure_date > f.arrival_date
  const valid = f.full_name.trim().length > 1 && datesOk

  async function submit() {
    setBusy(true)
    try {
      await createEnquiry({
        property_id: propertyId, full_name: f.full_name.trim(),
        phone: f.phone || null, email: f.email || null,
        arrival_date: f.arrival_date || null,
        departure_date: f.departure_date || null,
        adults: f.adults, children: f.children,
        room_type_id: f.room_type_id || null,
        budget_min: f.budget_min ? Number(f.budget_min) : null,
        budget_max: f.budget_max ? Number(f.budget_max) : null,
        channel: f.channel || null,
        next_follow_up_at: f.next_follow_up_at
          ? new Date(f.next_follow_up_at).toISOString() : null,
        notes: f.notes || null,
      })
      onDone(`Enquiry from ${f.full_name.trim()} recorded.`)
      onClose()
    } catch (e) { onFail(e); onClose() } finally { setBusy(false) }
  }

  return (
    <Shell title="New Enquiry" onClose={onClose} wide>
      <p className="mt-3 text-sm text-slate-500">
        Contact details stay on the enquiry. A guest record is created only if
        this becomes a booking, so the directory does not fill up with people
        who never came.
      </p>
      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        <L label="Name" required>
          <input value={f.full_name} onChange={(e) => set('full_name', e.target.value)}
            className={inputCls} autoFocus />
        </L>
        <L label="Channel">
          <Select value={f.channel} onChange={(e) => set('channel', e.target.value)}
            className={inputCls}>
            {BOOKING_SOURCES.map((s) => (
              <option key={s.value} value={s.value}>{s.label}</option>
            ))}
          </Select>
        </L>
        <L label="Phone">
          <input value={f.phone} onChange={(e) => set('phone', e.target.value)}
            className={inputCls} />
        </L>
        <L label="Email">
          <input value={f.email} onChange={(e) => set('email', e.target.value)}
            className={inputCls} />
        </L>
        <L label="Arrival">
          <DateField value={f.arrival_date} onChange={(v) => set('arrival_date', v)} className={inputCls} />
        </L>
        <L label="Departure">
          <DateField value={f.departure_date} onChange={(v) => set('departure_date', v)} className={inputCls} />
        </L>
        <L label="Adults">
          <input type="number" min={1} value={f.adults} className={inputCls}
            onChange={(e) => set('adults', Number(e.target.value))} />
        </L>
        <L label="Children">
          <input type="number" min={0} value={f.children} className={inputCls}
            onChange={(e) => set('children', Number(e.target.value))} />
        </L>
        <L label="Room Type">
          <Select value={f.room_type_id} className={inputCls}
            onChange={(e) => set('room_type_id', e.target.value)}>
            <option value="">No preference</option>
            {roomTypes.map((r) => (
              <option key={r.value} value={r.value}>{r.label}</option>
            ))}
          </Select>
        </L>
        <L label="Next follow-up">
          <input type="datetime-local" value={f.next_follow_up_at} className={inputCls}
            onChange={(e) => set('next_follow_up_at', e.target.value)} />
        </L>
        <L label="Budget from" hint="For the whole stay, not per night.">
          <input type="number" value={f.budget_min} className={inputCls}
            onChange={(e) => set('budget_min', e.target.value)} />
        </L>
        <L label="Budget to">
          <input type="number" value={f.budget_max} className={inputCls}
            onChange={(e) => set('budget_max', e.target.value)} />
        </L>
        <L label="Notes" wide>
          <textarea value={f.notes} rows={2} className={`${inputCls} resize-none`}
            placeholder="What did they ask for?"
            onChange={(e) => set('notes', e.target.value)} />
        </L>
      </div>
      {!datesOk && (
        <p className="mt-2 text-sm text-red-600">Departure must be after arrival.</p>
      )}
      <div className="mt-5 flex justify-end gap-2">
        <button onClick={onClose}
          className="rounded-lg border border-slate-200 px-4 py-2 text-sm text-slate-600 hover:bg-slate-50">
          Cancel
        </button>
        <button onClick={submit} disabled={!valid || busy}
          className="flex items-center gap-2 rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white enabled:hover:bg-brand/90 disabled:opacity-50">
          {busy && <Loader2 size={15} className="animate-spin" />} Record Enquiry
        </button>
      </div>
    </Shell>
  )
}

function Drawer({ card, propertyId, canConvert, onClose, onMove, onDone, onFail }: {
  card: EnquiryCard; propertyId: string; canConvert: boolean
  onClose: () => void; onMove: (c: EnquiryCard, s: string) => void
  onDone: (m: string) => void; onFail: (e: unknown) => void
}) {
  const [busy, setBusy] = useState(false)
  const { data: match, isLoading } = useQuery({
    queryKey: ['enquiry-match', card.id],
    queryFn: () => getEnquiryMatch(card.id, propertyId),
    enabled: card.status !== 'converted',
  })

  async function convert(roomTypeId?: string) {
    setBusy(true)
    try {
      const out = await convertEnquiry(card.id, {
        property_id: propertyId, room_type_id: roomTypeId,
      })
      onDone(`${card.full_name} booked as ${out.reservation_number}.`)
    } catch (e) { onFail(e); onClose() } finally { setBusy(false) }
  }

  return (
    <Shell title={card.full_name} onClose={onClose} wide>
      <p className="mt-1 text-sm text-slate-500">
        {[card.phone, card.email].filter(Boolean).join(' · ') || 'No contact details'}
      </p>
      <dl className="mt-4 grid gap-2 text-sm sm:grid-cols-2">
        <F k="Dates" v={card.arrival_date
          ? `${day(card.arrival_date)} – ${day(card.departure_date)} (${card.nights}n)`
          : 'Not given'} />
        <F k="Party" v={`${card.adults} adults, ${card.children} children`} />
        <F k="Room type" v={card.room_type ?? 'No preference'} />
        <F k="Channel" v={card.channel_label ?? '—'} />
        <F k="Budget" v={(card.budget_min || card.budget_max)
          ? `${plain(card.budget_min) ?? '…'} – ${plain(card.budget_max) ?? '…'}`
          : 'Not given'} />
        <F k="Follow-up" v={stamp(card.next_follow_up_at) ?? 'None set'} />
      </dl>
      {card.notes && (
        <p className="mt-3 rounded-xl bg-slate-50 px-4 py-3 text-sm text-slate-600">
          {card.notes}
        </p>
      )}

      {card.reservation_number ? (
        <p className="mt-4 flex items-center gap-2 rounded-xl bg-teal-50 px-4 py-3 text-sm text-teal-800">
          <CheckCircle2 size={16} />
          Converted to{' '}
          <Link to={`/reservations/${card.converted_reservation_id}`}
            className="font-semibold underline">{card.reservation_number}</Link>
        </p>
      ) : (
        <>
          <h3 className="mt-5 flex items-center gap-2 font-semibold text-ink">
            <Sparkles size={16} className="text-brand" /> Availability
          </h3>
          {isLoading ? (
            <p className="py-4 text-center"><Loader2 size={16} className="mx-auto animate-spin text-slate-400" /></p>
          ) : match ? <MatchPanel match={match} busy={busy} canConvert={canConvert}
            onConvert={convert} /> : null}

          <div className="mt-5 flex flex-wrap gap-2 border-t border-slate-100 pt-4">
            {(NEXT[card.status] ?? []).map((s) => (
              <button key={s} onClick={() => onMove(card, s)}
                className={`rounded-lg border px-4 py-2 text-sm font-medium ${
                  s === 'lost' ? 'border-red-200 text-red-600 hover:bg-red-50'
                    : 'border-slate-200 text-slate-600 hover:bg-slate-50'}`}>
                Move to {MOVE_LABEL[s]}
              </button>
            ))}
          </div>
        </>
      )}
    </Shell>
  )
}

function MatchPanel({ match, busy, canConvert, onConvert }: {
  match: EnquiryMatch; busy: boolean; canConvert: boolean
  onConvert: (id?: string) => void
}) {
  if (!match.checked) {
    return (
      <p className="mt-2 flex items-start gap-2 rounded-xl bg-amber-50 px-4 py-3 text-sm text-caution">
        <Info size={16} className="mt-0.5 shrink-0" /> {match.note}
      </p>
    )
  }
  const r = match.requested
  const free = r && r.blocked_reason === null
  return (
    <div className="mt-2 space-y-3">
      <p className={`flex items-start gap-2 rounded-xl px-4 py-3 text-sm ${
        free ? 'bg-emerald-50 text-emerald-800' : 'bg-amber-50 text-caution'}`}>
        {free ? <CheckCircle2 size={16} className="mt-0.5 shrink-0" />
          : <Info size={16} className="mt-0.5 shrink-0" />}
        {match.note}
      </p>
      {r && <Option rt={r} busy={busy} canConvert={canConvert} onConvert={onConvert} />}
      {match.alternatives.length > 0 && (
        <>
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">
            Also free for these dates
          </p>
          {match.alternatives.map((a) => (
            <Option key={a.room_type_id} rt={a} busy={busy}
              canConvert={canConvert} onConvert={onConvert} />
          ))}
        </>
      )}
    </div>
  )
}

function Option({ rt, busy, canConvert, onConvert }: {
  rt: EnquiryMatch['alternatives'][number]; busy: boolean
  canConvert: boolean; onConvert: (id?: string) => void
}) {
  const blocked = rt.blocked_reason !== null
  return (
    <div className={`flex items-center gap-3 rounded-xl border p-3 ${
      blocked ? 'border-slate-100 bg-slate-50/60' : 'border-slate-200'}`}>
      {rt.photo_url
        ? <img src={rt.photo_url} alt="" className="h-14 w-20 shrink-0 rounded-lg object-cover" />
        : <span className="flex h-14 w-20 shrink-0 items-center justify-center rounded-lg bg-slate-75">
            <BedDouble size={18} className="text-slate-400" />
          </span>}
      <span className="min-w-0 flex-1">
        <span className="flex flex-wrap items-center gap-2 font-semibold text-slate-800">
          {rt.name}
          {rt.is_requested && (
            <span className="rounded-full bg-brand-light px-2 py-0.5 text-[11px] text-brand">
              Requested
            </span>
          )}
        </span>
        <span className="block text-xs text-slate-500">
          {blocked
            ? (rt.blocked_reason === 'sold_out'
              ? 'Sold out for these dates'
              : `Not on sale — ${rt.nights_loaded}/${rt.nights} nights loaded`)
            : `${rt.sellable} available for all ${rt.nights} night${rt.nights === 1 ? '' : 's'}`}
        </span>
        {rt.rate && (
          <span className="block text-xs text-slate-600">
            {plain(rt.rate)} per night · {plain(rt.stay_total)} for the stay
            {rt.within_budget !== null && (
              <span className={rt.within_budget ? ' text-emerald-600' : ' text-amber-600'}>
                {' '}({rt.within_budget ? 'within budget' : 'outside budget'})
              </span>
            )}
          </span>
        )}
      </span>
      {!blocked && canConvert && (
        <button onClick={() => onConvert(rt.room_type_id)} disabled={busy}
          className="flex shrink-0 items-center gap-1.5 rounded-lg bg-brand px-3 py-2 text-xs font-semibold text-white enabled:hover:bg-brand/90 disabled:opacity-50">
          {busy ? <Loader2 size={13} className="animate-spin" /> : <ArrowRight size={13} />}
          Book
        </button>
      )}
    </div>
  )
}

function LostModal({ card, propertyId, onClose, onDone, onFail }: {
  card: EnquiryCard; propertyId: string; onClose: () => void
  onDone: (m: string) => void; onFail: (e: unknown) => void
}) {
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  async function submit() {
    setBusy(true)
    try {
      await moveEnquiry(card.id, {
        property_id: propertyId, status: 'lost', reason: reason.trim(),
      })
      onDone(`${card.full_name} closed as lost.`)
      onClose()
    } catch (e) { onFail(e); onClose() } finally { setBusy(false) }
  }
  return (
    <Shell title="Close as lost" onClose={onClose}>
      <p className="mt-3 text-sm text-slate-500">
        The enquiry comes off the board. Say why — an unexplained loss teaches
        nobody anything, and these are the only record of demand you turned away.
      </p>
      <textarea value={reason} onChange={(e) => setReason(e.target.value)} rows={3}
        className={`${inputCls} mt-3 resize-none`} autoFocus
        placeholder="e.g. Booked a competitor, dates no longer suit, over budget" />
      <div className="mt-5 flex justify-end gap-2">
        <button onClick={onClose}
          className="rounded-lg border border-slate-200 px-4 py-2 text-sm text-slate-600 hover:bg-slate-50">
          Keep it
        </button>
        <button onClick={submit} disabled={reason.trim().length < 3 || busy}
          className="flex items-center gap-2 rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white enabled:hover:bg-red-700 disabled:opacity-50">
          {busy && <Loader2 size={15} className="animate-spin" />} Close as lost
        </button>
      </div>
    </Shell>
  )
}

function L({ label, required, hint, wide, children }: {
  label: string; required?: boolean; hint?: string; wide?: boolean
  children: React.ReactNode
}) {
  return (
    <label className={`block ${wide ? 'sm:col-span-2' : ''}`}>
      <span className="mb-1 block text-sm font-medium text-slate-600">
        {label} {required && <span className="text-red-500">*</span>}
      </span>
      {children}
      {hint && <span className="mt-1 block text-xs text-slate-400">{hint}</span>}
    </label>
  )
}

function F({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex justify-between gap-3 border-b border-slate-50 pb-1.5">
      <dt className="text-slate-500">{k}</dt>
      <dd className="text-right font-medium text-slate-800">{v}</dd>
    </div>
  )
}
