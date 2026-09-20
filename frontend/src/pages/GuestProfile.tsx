import { useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ArrowLeft, BedDouble, Briefcase, Cake, CalendarDays, Check, ChevronRight,
  CreditCard, DoorOpen, FileText, Info, Loader2, LogIn, LogOut, Mail,
  MapPin, MessageSquare, Pencil, Phone, Plus, Repeat, Send, ShieldCheck,
  StickyNote, Tag, Trash2, X,
} from 'lucide-react'
import {
  addGuestNote, addGuestTag, getGuestProfile, removeGuestTag,
  setGuestPreferences,
  type GuestProfile as Profile, type ProfileActivity, type ProfileStay,
} from '../api'
import ActionsMenu from '../components/ActionsMenu'
import { CONTROL, CONTROL_TYPE, FILTER_BOX } from '../lib/controls'
import { fmtDate, fmtDateTime } from '../lib/dates'
import { useOrgId } from '../hooks/useProperty'

/**
 * Screen 009 — Guest Profile.
 *
 * Everything on the left of this screen is *derived* — total stays, lifetime
 * value, the current reservation, the timeline — and everything on the right
 * is *recorded*: preferences, tags, notes. The split is worth knowing while
 * reading this file, because it decides what can be edited. Nobody edits
 * "8 stays"; it is what the stays say.
 *
 * Two panels of the mockup are missing, and the screen says so rather than
 * drawing them empty: an average rating (nothing collects guest feedback) and
 * a communications log (nothing records the mail this system sends, and
 * nothing sends WhatsApp at all). The server supplies the reason for each, so
 * the explanation lives with the thing that knows it rather than being
 * repeated here.
 */

const money = (v: string | number) =>
  `₹${Number(v).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`

/** Reservation status, in the words a hotel uses, with the tone it deserves. */
const STATUS: Record<string, { label: string; tone: string }> = {
  checked_in: { label: 'In House', tone: 'bg-emerald-50 text-emerald-700' },
  checked_out: { label: 'Checked Out', tone: 'bg-slate-75 text-slate-600' },
  confirmed: { label: 'Confirmed', tone: 'bg-sky-50 text-sky-700' },
  tentative: { label: 'Tentative', tone: 'bg-amber-50 text-amber-700' },
  cancelled: { label: 'Cancelled', tone: 'bg-rose-50 text-rose-700' },
  no_show: { label: 'No Show', tone: 'bg-rose-50 text-rose-700' },
}

const ACTIVITY_ICON: Record<ProfileActivity['kind'], {
  icon: typeof LogIn; tone: string
}> = {
  booking: { icon: CalendarDays, tone: 'bg-sky-100 text-sky-700' },
  checkin: { icon: LogIn, tone: 'bg-emerald-100 text-emerald-700' },
  checkout: { icon: LogOut, tone: 'bg-slate-75 text-slate-600' },
  move: { icon: DoorOpen, tone: 'bg-violet-100 text-violet-700' },
  document: { icon: FileText, tone: 'bg-amber-100 text-amber-700' },
  note: { icon: StickyNote, tone: 'bg-brand/10 text-brand' },
}

/**
 * A preference's icon, matched on the words people actually type.
 *
 * The kind is free text — a hotel's preferences are its own, and a fixed enum
 * would be wrong at the second property — so this guesses from the label and
 * falls back to a neutral mark. Guessing wrong costs an icon; constraining
 * the list would cost a hotel the preference it actually cares about.
 */
function prefIcon(label: string) {
  const l = label.toLowerCase()
  if (/sea|ocean|view|balcon/.test(l)) return { icon: MapPin, tone: 'text-sky-600' }
  if (/floor|high|low|lift|elevator/.test(l)) return { icon: BedDouble, tone: 'text-violet-600' }
  if (/veg|food|meal|diet|allerg/.test(l)) return { icon: Check, tone: 'text-emerald-600' }
  if (/pickup|airport|transfer|cab|taxi/.test(l)) return { icon: MapPin, tone: 'text-amber-600' }
  if (/birthday|anniversar|celebrat|occasion/.test(l)) return { icon: Cake, tone: 'text-rose-600' }
  if (/quiet|noise|smok/.test(l)) return { icon: Info, tone: 'text-slate-500' }
  return { icon: Check, tone: 'text-slate-400' }
}

// All seven of the mockup's tabs. The last two are shown and disabled rather
// than dropped: the module's shape is part of the design, and a tab strip that
// silently loses two entries tells the next person nothing. Hovering either
// gives the reason, which comes from the server so it stays true if the
// answer changes.
const TABS = [
  { key: 'overview', label: 'Overview', built: true },
  { key: 'stays', label: 'Stay History', built: true },
  { key: 'preferences', label: 'Preferences', built: true },
  { key: 'documents', label: 'Documents', built: true },
  { key: 'payments', label: 'Payments', built: true },
  { key: 'feedback', label: 'Feedback', built: false, why: 'rating' },
  { key: 'communications', label: 'Communications', built: false,
    why: 'communications' },
] as const
type TabKey = typeof TABS[number]['key']

function Card({ title, action, children, className = '' }: {
  title: string; action?: React.ReactNode
  children: React.ReactNode; className?: string
}) {
  return (
    <section className={`rounded-xl border border-slate-100 bg-white ${className}`}>
      <header className="flex items-center justify-between gap-3 border-b border-slate-100 px-4 py-3">
        <h2 className="text-section font-semibold text-slate-800">{title}</h2>
        {action}
      </header>
      <div className="p-4">{children}</div>
    </section>
  )
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-1.5">
      <span className="text-sm text-slate-500">{label}</span>
      <span className="text-sm font-medium text-slate-800">{value}</span>
    </div>
  )
}

/** A panel the mockup has and this system cannot fill.
 *
 *  Drawn deliberately rather than omitted silently: somebody comparing the
 *  screen against the design should find out here why it is missing, not by
 *  reading a migration. */
function Unavailable({ title, why }: { title: string; why: string }) {
  return (
    <div className="rounded-xl border border-dashed border-slate-200 p-4">
      <p className="flex items-center gap-2 text-sm font-medium text-slate-600">
        <Info size={15} className="shrink-0 text-slate-400" /> {title}
      </p>
      <p className="mt-1 text-sm text-slate-400">{why}</p>
    </div>
  )
}

function StayCard({ s }: { s: ProfileStay }) {
  const st = STATUS[s.status] ?? { label: s.status, tone: 'bg-slate-75 text-slate-600' }
  return (
    <div className="space-y-3">
      <div className="flex items-start gap-3">
        {/* The mockup shows a photograph of the room. Nothing in this system
            stores room or room-type imagery, so this is a tile carrying the
            room number instead -- which is the thing the desk is looking for
            in that corner anyway. A stock interior would be decoration that
            lies about which room the guest is in. */}
        <span className="grid h-14 w-14 shrink-0 place-items-center rounded-lg bg-brand/10 text-brand">
          {s.room
            ? <span className="text-sm font-semibold">{s.room}</span>
            : <BedDouble size={20} />}
        </span>
        <div className="min-w-0 flex-1">
          <Link to={`/reservations/${s.reservation_id}`}
            className="text-sm font-semibold text-brand hover:underline">
            {s.number}
          </Link>
          <p className="truncate text-sm text-slate-800">
            {s.room_type ?? 'Room type removed'}
          </p>
          {s.property_name && (
            <p className="truncate text-xs text-slate-400">{s.property_name}</p>
          )}
        </div>
        <span className={`shrink-0 rounded-md px-2 py-1 text-xs font-medium ${st.tone}`}>
          {st.label}
        </span>
      </div>
      <dl className="space-y-1">
        <Row label="Check-in" value={fmtDate(s.arrival_date)} />
        <Row label="Check-out"
          value={`${fmtDate(s.departure_date)} (${s.nights} night${s.nights === 1 ? '' : 's'})`} />
        <Row label="Room" value={s.room ?? <span className="text-slate-400">Not assigned</span>} />
        <Row label="Guests"
          value={`${s.adults} adult${s.adults === 1 ? '' : 's'}${
            s.children ? `, ${s.children} child${s.children === 1 ? '' : 'ren'}` : ''}`} />
      </dl>
      <div className="flex gap-2">
        <Link to={`/reservations/${s.reservation_id}`}
          className={`flex-1 rounded-lg border border-slate-200 px-3 py-2 text-center ${CONTROL} hover:bg-slate-50`}>
          View Reservation
        </Link>
        {/* Only for a stay that can still change. Offering "Modify" on a
            departed guest is an invitation to a screen that will refuse. */}
        {['confirmed', 'tentative', 'checked_in'].includes(s.status) && (
          <Link to={`/reservations/${s.reservation_id}/modify`}
            className={`flex-1 rounded-lg bg-brand px-3 py-2 text-center ${CONTROL_TYPE} text-white hover:bg-brand-dark`}>
            Modify Stay
          </Link>
        )}
      </div>
    </div>
  )
}

export default function GuestProfile() {
  const { guestId = '' } = useParams()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const organizationId = useOrgId()
  const [tab, setTab] = useState<TabKey>('overview')
  const [noteDraft, setNoteDraft] = useState('')
  const [tagDraft, setTagDraft] = useState('')
  const [addingTag, setAddingTag] = useState(false)
  const [editingPrefs, setEditingPrefs] = useState(false)
  const [prefDraft, setPrefDraft] = useState<string[]>([])
  const noteRef = useRef<HTMLTextAreaElement>(null)

  const key = ['guest-profile', organizationId, guestId]
  const q = useQuery({
    queryKey: key,
    queryFn: () => getGuestProfile(organizationId, guestId),
    enabled: Boolean(organizationId && guestId),
  })
  const g: Profile | undefined = q.data

  const refresh = () => qc.invalidateQueries({ queryKey: key })

  const noteM = useMutation({
    mutationFn: (body: string) => addGuestNote(organizationId, guestId, body),
    onSuccess: () => { setNoteDraft(''); refresh() },
  })
  const tagAddM = useMutation({
    mutationFn: (tag: string) => addGuestTag(organizationId, guestId, tag),
    onSuccess: () => { setTagDraft(''); setAddingTag(false); refresh() },
  })
  const tagDelM = useMutation({
    mutationFn: (tag: string) => removeGuestTag(organizationId, guestId, tag),
    onSuccess: refresh,
  })
  const prefM = useMutation({
    mutationFn: (items: string[]) => setGuestPreferences(
      organizationId, guestId, items.map((label) => ({ kind: 'other', label }))),
    onSuccess: () => { setEditingPrefs(false); refresh() },
  })

  // Cancelled stays are kept out of the headline history but counted, because
  // "8 stays and 3 cancellations" is a different guest from "8 stays".
  const realStays = useMemo(
    () => (g?.stays ?? []).filter((s) => s.status !== 'cancelled'), [g])

  if (q.isLoading) {
    return (
      <p className="flex items-center gap-2 py-16 text-sm text-slate-400">
        <Loader2 size={15} className="animate-spin" /> Loading guest…
      </p>
    )
  }
  if (q.isError || !g) {
    return (
      <div className="space-y-3 py-16 text-center">
        <p className="text-sm text-slate-500">
          That guest could not be loaded. They may belong to another property
          group, or have been removed.
        </p>
        <Link to="/guests" className="text-sm font-medium text-brand hover:underline">
          Back to Guests
        </Link>
      </div>
    )
  }

  const contact: [typeof Phone, string | null][] = [
    [Phone, g.phone], [Mail, g.email],
    [MapPin, [g.city, g.state, g.country].filter(Boolean).join(', ') || null],
    [Briefcase, g.occupation],
    [Cake, g.date_of_birth ? fmtDate(g.date_of_birth) : null],
  ]

  return (
    <div className="space-y-4">
      {/* ---------------------------------------------------------- head --- */}
      <nav className="flex items-center gap-1.5 text-xs text-slate-400">
        <Link to="/guests" className="hover:text-brand">Guests</Link>
        <ChevronRight size={12} />
        <span className="text-slate-500">Guest Profile</span>
      </nav>

      <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-display text-ink">Guest Profile — {g.full_name}</h1>
            {/* The crown in the mockup marked a membership tier. There is no
                loyalty programme, so the badge carries what the stays prove
                instead: here now, or been here before. */}
            {g.in_house && (
              <span className="rounded-md bg-emerald-50 px-2 py-1 text-xs font-medium text-emerald-700">
                In House
              </span>
            )}
            {g.stats.total_stays > 1 && (
              <span className="flex items-center gap-1 rounded-md bg-sky-50 px-2 py-1 text-xs font-medium text-sky-700">
                <Repeat size={12} /> Returning
              </span>
            )}
          </div>
          {/* The identifiers a desk reads aloud on the phone. Member since is
              when the record was created -- with no loyalty programme there is
              no other date it could mean. */}
          <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-slate-500">
            <span>Guest ID <strong className="font-medium text-slate-600">
              {/* Falls back to the uuid only for a row written before the
                  reference existed; the trigger means there should be none. */}
              {g.reference ?? g.id.slice(0, 8).toUpperCase()}</strong></span>
            <span className="text-slate-300">|</span>
            <span>Member since <strong className="font-medium text-slate-600">
              {fmtDate(g.member_since)}</strong></span>
            <span className="text-slate-300">|</span>
            <span>Last stay <strong className="font-medium text-slate-600">
              {g.last_stay ? fmtDate(g.last_stay) : 'never'}</strong></span>
            {g.next_stay && (
              <>
                <span className="text-slate-300">|</span>
                <span>Next stay <strong className="font-medium text-slate-600">
                  {fmtDate(g.next_stay)}</strong></span>
              </>
            )}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {g.can_book && (
            <button onClick={() => navigate(`/reservations/new?guest=${g.id}`)}
              className={`flex items-center gap-2 rounded-lg bg-brand px-4 py-2 ${CONTROL_TYPE} text-white hover:bg-brand-dark`}>
              <CalendarDays size={15} /> New Booking
            </button>
          )}
          {/* Opens the user's own mail client rather than pretending to send.
              Nothing in this system sends a free-text message to a guest, and
              a button that silently did nothing would be worse than one that
              hands the job to something that works. Disabled with a reason
              when there is no address to send to. */}
          <a href={g.email ? `mailto:${g.email}` : undefined}
            title={g.email ? `Email ${g.email}`
              : 'No email address on file for this guest'}
            aria-disabled={!g.email}
            onClick={(e) => { if (!g.email) e.preventDefault() }}
            className={`flex items-center gap-2 ${FILTER_BOX} ${CONTROL} ${
              g.email ? 'hover:bg-slate-50'
                : 'cursor-not-allowed text-slate-300'}`}>
            <Send size={15} /> Send Message
          </a>
          {g.can_edit && (
            <button onClick={() => {
              setTab('overview')
              // The note box is in the third column and usually below the
              // fold, so the button takes you to it and puts the cursor in it.
              requestAnimationFrame(() => {
                noteRef.current?.scrollIntoView({ block: 'center' })
                noteRef.current?.focus()
              })
            }}
              className={`flex items-center gap-2 ${FILTER_BOX} ${CONTROL} hover:bg-slate-50`}>
              <StickyNote size={15} /> Add Note
            </button>
          )}
          <ActionsMenu items={[
            { label: 'Back to Guests', icon: ArrowLeft,
              onSelect: () => navigate('/guests') },
            { label: 'Edit guest details', icon: Pencil,
              onSelect: () => navigate(`/guests?edit=${g.id}`),
              disabled: !g.can_edit,
              hint: 'You do not have permission to edit guests.' },
            { label: 'View folio', icon: CreditCard,
              onSelect: () => navigate(
                `/reservations/${(g.current ?? g.upcoming ?? g.stays[0])?.reservation_id}/folio`),
              disabled: g.stays.length === 0,
              hint: 'This guest has no reservation to bill.' },
          ]} />
        </div>
      </div>

      {/* -------------------------------------------------------- identity --- */}
      <section className="flex flex-wrap items-center gap-x-8 gap-y-4 rounded-xl border border-slate-100 bg-white p-4">
        {/* A monogram, not a portrait: nothing in this system captures a guest
            photograph, and a stock silhouette would be pretending. */}
        <div className="flex min-w-0 items-center gap-4">
          <span className="grid h-16 w-16 shrink-0 place-items-center rounded-full bg-brand/10 text-xl font-semibold text-brand">
            {g.initials}
          </span>
          <div className="min-w-0">
            <p className="truncate text-subject font-semibold text-slate-800">
              {[g.title, g.full_name].filter(Boolean).join(' ')}
            </p>
            <p className="text-sm text-slate-500">
              {g.nationality ?? 'Nationality not recorded'}
            </p>
            {g.id_type && (
              <p className="mt-1 flex items-center gap-1.5 text-xs text-slate-400">
                <ShieldCheck size={13}
                  className={g.id_verified ? 'text-emerald-600' : 'text-slate-300'} />
                {g.id_type.replace(/_/g, ' ')}
                {g.id_verified ? ' verified' : ' not verified'}
              </p>
            )}
          </div>
        </div>

        <dl className="min-w-[220px] flex-1 space-y-1.5">
          {contact.map(([Icon, value], i) => (
            <div key={i} className="flex items-center gap-2.5 text-sm">
              <Icon size={15} className="shrink-0 text-slate-400" />
              <span className={value ? 'text-slate-700' : 'text-slate-300'}>
                {value ?? 'Not recorded'}
              </span>
            </div>
          ))}
        </dl>

        {/* Three numbers, none of them stored. Where the mockup had an average
            rating there is a stay length instead: this system collects no
            feedback, and a rating had to come from somewhere. */}
        <div className="flex flex-wrap gap-3">
          {[
            { label: 'Total Stays', value: g.stats.total_stays },
            { label: 'Lifetime Value', value: money(g.stats.lifetime_value) },
            {
              label: 'Avg Stay',
              value: g.stats.total_stays
                ? `${g.stats.average_nights} nights` : '—',
            },
          ].map((s) => (
            <div key={s.label}
              className="min-w-[110px] rounded-lg bg-slate-50 px-4 py-3 text-center">
              <p className="text-subject font-semibold text-slate-800">{s.value}</p>
              <p className="mt-0.5 text-xs text-slate-500">{s.label}</p>
            </div>
          ))}
        </div>
      </section>

      {/* ------------------------------------------------------------ tabs --- */}
      <div className="flex gap-1 overflow-x-auto border-b border-slate-200">
        {TABS.map((t) => (t.built ? (
          <button key={t.key} onClick={() => setTab(t.key)}
            className={`whitespace-nowrap px-4 py-2.5 ${CONTROL_TYPE} ${
              tab === t.key
                ? 'border-b-2 border-brand text-brand'
                : 'text-slate-500 hover:text-slate-700'}`}>
            {t.label}
            {t.key === 'documents' && g.documents > 0 && (
              <span className="ml-1.5 text-xs text-slate-400">{g.documents}</span>
            )}
          </button>
        ) : (
          <span key={t.key} title={g.unavailable[t.why] ?? 'Not built yet.'}
            className={`cursor-not-allowed whitespace-nowrap px-4 py-2.5 ${CONTROL_TYPE} text-slate-300`}>
            {t.label}
          </span>
        )))}
      </div>

      {tab === 'overview' && (
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)_minmax(0,1fr)]">
          {/* ------------------------------------------------- left column --- */}
          <div className="space-y-4">
            <Card title={g.current ? 'Current Reservation' : 'Next Reservation'}>
              {g.current ? <StayCard s={g.current} />
                : g.upcoming ? <StayCard s={g.upcoming} />
                : <p className="text-sm text-slate-400">
                    No stay in progress and nothing booked ahead.
                  </p>}
            </Card>

            <Card title="Guest Summary">
              <dl>
                <Row label="Total Stays" value={g.stats.total_stays} />
                <Row label="Nights Stayed" value={g.stats.nights_total} />
                <Row label="Lifetime Value" value={money(g.stats.lifetime_value)} />
                <Row label="Average Stay"
                  value={g.stats.total_stays ? `${g.stats.average_nights} nights` : '—'} />
                <Row label="Last Stay"
                  value={g.last_stay ? fmtDate(g.last_stay)
                    : <span className="text-slate-400">Never</span>} />
                <Row label="Next Booking"
                  value={g.next_stay ? fmtDate(g.next_stay)
                    : <span className="text-slate-400">None</span>} />
                <Row label="Cancellations"
                  value={g.stats.cancelled || <span className="text-slate-400">None</span>} />
                <Row label="Source"
                  value={realStays[0]?.source ?? g.stays[0]?.source
                    ?? <span className="text-slate-400">Unknown</span>} />
              </dl>
            </Card>
          </div>

          {/* ---------------------------------------------------- timeline --- */}
          <Card title="Recent Activity" action={
            g.activity.length >= 8 ? (
              <button onClick={() => setTab('stays')}
                className="text-sm font-medium text-brand hover:underline">
                View All
              </button>
            ) : null
          }>
            {g.activity.length === 0 ? (
              <p className="text-sm text-slate-400">
                Nothing has happened for this guest yet.
              </p>
            ) : (
              <ol className="space-y-4">
                {g.activity.map((a, i) => {
                  const { icon: Icon, tone } = ACTIVITY_ICON[a.kind]
                    ?? { icon: Info, tone: 'bg-slate-75 text-slate-500' }
                  return (
                    <li key={i} className="flex gap-3">
                      <span className="relative flex flex-col items-center">
                        <span className={`grid h-8 w-8 shrink-0 place-items-center rounded-full ${tone}`}>
                          <Icon size={15} />
                        </span>
                        {/* The rail is drawn between entries, not after the
                            last one, so the timeline ends rather than trailing
                            off into nothing. */}
                        {i < g.activity.length - 1 && (
                          <span className="mt-1 w-px flex-1 bg-slate-75" />
                        )}
                      </span>
                      <div className="min-w-0 flex-1 pb-1">
                        <div className="flex flex-wrap items-baseline justify-between gap-x-3">
                          <p className="text-sm font-medium text-slate-800">{a.title}</p>
                          <p className="text-xs text-slate-400">{fmtDateTime(a.at)}</p>
                        </div>
                        {a.detail && (
                          <p className="mt-0.5 break-words text-sm text-slate-500">
                            {a.detail}
                          </p>
                        )}
                      </div>
                    </li>
                  )
                })}
              </ol>
            )}
          </Card>

          {/* ------------------------------------------------ right column --- */}
          <div className="space-y-4">
            <Card title="Guest Preferences" action={
              g.can_edit && !editingPrefs ? (
                <button
                  onClick={() => {
                    setPrefDraft(g.preferences.map((p) => p.label))
                    setEditingPrefs(true)
                  }}
                  className="text-sm font-medium text-brand hover:underline">
                  Edit
                </button>
              ) : null
            }>
              {editingPrefs ? (
                <div className="space-y-2">
                  {prefDraft.map((label, i) => (
                    <div key={i} className="flex items-center gap-2">
                      <input value={label} autoFocus={i === prefDraft.length - 1}
                        onChange={(e) => setPrefDraft(
                          prefDraft.map((v, j) => (j === i ? e.target.value : v)))}
                        placeholder="Sea view room"
                        className={`w-full ${FILTER_BOX} text-sm text-slate-700 outline-none focus:border-brand`} />
                      <button onClick={() => setPrefDraft(
                        prefDraft.filter((_, j) => j !== i))}
                        aria-label="Remove preference"
                        className="shrink-0 rounded-lg p-2 text-slate-400 hover:bg-slate-50 hover:text-rose-600">
                        <Trash2 size={15} />
                      </button>
                    </div>
                  ))}
                  <button onClick={() => setPrefDraft([...prefDraft, ''])}
                    className="flex items-center gap-1.5 text-sm font-medium text-brand hover:underline">
                    <Plus size={14} /> Add preference
                  </button>
                  <div className="flex gap-2 pt-2">
                    <button
                      disabled={prefM.isPending}
                      onClick={() => prefM.mutate(
                        prefDraft.map((p) => p.trim()).filter(Boolean))}
                      className={`flex items-center gap-1.5 rounded-lg bg-brand px-3 py-2 ${CONTROL_TYPE} text-white hover:bg-brand-dark disabled:opacity-50`}>
                      {prefM.isPending ? <Loader2 size={14} className="animate-spin" />
                        : <Check size={14} />} Save
                    </button>
                    <button onClick={() => setEditingPrefs(false)}
                      className={`rounded-lg border border-slate-200 px-3 py-2 ${CONTROL} hover:bg-slate-50`}>
                      Cancel
                    </button>
                  </div>
                </div>
              ) : g.preferences.length === 0 ? (
                <p className="text-sm text-slate-400">
                  Nothing recorded yet. Anything the desk should know before
                  this guest arrives belongs here.
                </p>
              ) : (
                <ul className="space-y-2">
                  {g.preferences.map((p) => {
                    const { icon: Icon, tone } = prefIcon(p.label)
                    return (
                      <li key={p.id} className="flex items-center gap-2.5 text-sm text-slate-700">
                        <Icon size={15} className={`shrink-0 ${tone}`} />
                        <span className="min-w-0 break-words">{p.label}</span>
                      </li>
                    )
                  })}
                </ul>
              )}
            </Card>

            <Card title="Tags" action={
              g.can_edit && !addingTag ? (
                <button onClick={() => setAddingTag(true)}
                  className="text-sm font-medium text-brand hover:underline">
                  Add Tag
                </button>
              ) : null
            }>
              {addingTag && (
                <form
                  onSubmit={(e) => {
                    e.preventDefault()
                    if (tagDraft.trim()) tagAddM.mutate(tagDraft.trim())
                  }}
                  className="mb-3 flex gap-2">
                  <input value={tagDraft} autoFocus maxLength={40}
                    onChange={(e) => setTagDraft(e.target.value)}
                    placeholder="VIP Gold"
                    className={`w-full ${FILTER_BOX} text-sm text-slate-700 outline-none focus:border-brand`} />
                  <button type="submit" disabled={tagAddM.isPending}
                    className={`shrink-0 rounded-lg bg-brand px-3 py-2 ${CONTROL_TYPE} text-white disabled:opacity-50`}>
                    Add
                  </button>
                  <button type="button"
                    onClick={() => { setAddingTag(false); setTagDraft('') }}
                    aria-label="Cancel"
                    className="shrink-0 rounded-lg p-2 text-slate-400 hover:bg-slate-50">
                    <X size={15} />
                  </button>
                </form>
              )}
              {g.tags.length === 0 && !addingTag ? (
                <p className="text-sm text-slate-400">
                  No tags. These are labels somebody puts on a guest — there is
                  no loyalty programme behind them.
                </p>
              ) : (
                <ul className="flex flex-wrap gap-2">
                  {g.tags.map((t) => (
                    <li key={t}
                      className="flex items-center gap-1.5 rounded-full bg-slate-75 py-1 pl-3 pr-1.5 text-sm font-medium text-slate-700">
                      <Tag size={12} className="text-slate-400" />
                      {t}
                      {g.can_edit && (
                        <button onClick={() => tagDelM.mutate(t)}
                          aria-label={`Remove tag ${t}`}
                          className="rounded-full p-0.5 text-slate-400 hover:bg-white hover:text-rose-600">
                          <X size={13} />
                        </button>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </Card>

            <Card title="Internal Notes" action={
              g.can_edit ? (
                <button onClick={() => noteRef.current?.focus()}
                  className="text-sm font-medium text-brand hover:underline">
                  Add Note
                </button>
              ) : null
            }>
              {g.can_edit && (
                <form
                  onSubmit={(e) => {
                    e.preventDefault()
                    if (noteDraft.trim()) noteM.mutate(noteDraft.trim())
                  }}
                  className="mb-3 space-y-2">
                  <textarea ref={noteRef} value={noteDraft} rows={2} maxLength={4000}
                    onChange={(e) => setNoteDraft(e.target.value)}
                    placeholder="Anything the next person on this desk should know…"
                    className={`w-full ${FILTER_BOX} text-sm text-slate-700 outline-none focus:border-brand`} />
                  <button type="submit"
                    disabled={!noteDraft.trim() || noteM.isPending}
                    className={`flex items-center gap-1.5 rounded-lg bg-brand px-3 py-2 ${CONTROL_TYPE} text-white hover:bg-brand-dark disabled:opacity-40`}>
                    {noteM.isPending ? <Loader2 size={14} className="animate-spin" />
                      : <MessageSquare size={14} />} Add Note
                  </button>
                </form>
              )}
              {g.notes.length === 0 ? (
                <p className="text-sm text-slate-400">No notes yet.</p>
              ) : (
                <ul className="space-y-3">
                  {g.notes.map((n) => (
                    <li key={n.id} className="rounded-lg bg-amber-50/60 p-3">
                      {/* Attributed and dated, because a note nobody owns is
                          one nobody can ask about. Notes cannot be edited or
                          deleted -- see the endpoint for why. */}
                      <p className="text-xs text-slate-500">
                        {fmtDateTime(n.created_at)}
                        {n.author ? ` · ${n.author}` : ''}
                      </p>
                      <p className="mt-1 whitespace-pre-wrap break-words text-sm text-slate-700">
                        {n.body}
                      </p>
                    </li>
                  ))}
                </ul>
              )}
            </Card>

            <Unavailable title="Feedback &amp; rating"
              why={g.unavailable.rating ?? 'Not available.'} />
            <Unavailable title="Communications"
              why={g.unavailable.communications ?? 'Not available.'} />
          </div>
        </div>
      )}

      {tab === 'stays' && (
        <Card title={`Stay History (${g.stays.length})`}>
          {g.stays.length === 0 ? (
            <p className="text-sm text-slate-400">This guest has never booked.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[820px] text-sm">
                <thead className="border-b border-slate-100 text-left text-xs uppercase tracking-wide text-slate-500">
                  <tr>
                    {['Reservation', 'Property', 'Room', 'Arrival', 'Departure',
                      'Nights', 'Status', 'Charges', 'Paid'].map((h) => (
                      <th key={h} className="px-3 py-2.5 font-medium">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {g.stays.map((s) => {
                    const st = STATUS[s.status]
                      ?? { label: s.status, tone: 'bg-slate-75 text-slate-600' }
                    return (
                      <tr key={s.unit_id}
                        className="border-b border-slate-100 last:border-0 hover:bg-slate-50/60">
                        <td className="px-3 py-2.5">
                          <Link to={`/reservations/${s.reservation_id}`}
                            className="font-medium text-brand hover:underline">
                            {s.number}
                          </Link>
                        </td>
                        <td className="px-3 py-2.5 text-slate-600">
                          {s.property_name ?? '—'}
                        </td>
                        <td className="px-3 py-2.5 text-slate-600">
                          {s.room ?? s.room_type ?? '—'}
                        </td>
                        <td className="px-3 py-2.5 text-slate-600">{fmtDate(s.arrival_date)}</td>
                        <td className="px-3 py-2.5 text-slate-600">{fmtDate(s.departure_date)}</td>
                        <td className="px-3 py-2.5 text-slate-600">{s.nights}</td>
                        <td className="px-3 py-2.5">
                          <span className={`rounded-md px-2 py-1 text-xs font-medium ${st.tone}`}>
                            {st.label}
                          </span>
                        </td>
                        <td className="px-3 py-2.5 text-slate-600">{money(s.charges)}</td>
                        <td className="px-3 py-2.5 text-slate-600">{money(s.paid)}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      )}

      {tab === 'preferences' && (
        <Card title="Preferences">
          {g.preferences.length === 0 ? (
            <p className="text-sm text-slate-400">
              Nothing recorded. Use Edit on the Overview tab to add some.
            </p>
          ) : (
            <ul className="space-y-2">
              {g.preferences.map((p) => {
                const { icon: Icon, tone } = prefIcon(p.label)
                return (
                  <li key={p.id} className="flex items-center gap-2.5 text-sm text-slate-700">
                    <Icon size={15} className={`shrink-0 ${tone}`} /> {p.label}
                  </li>
                )
              })}
            </ul>
          )}
        </Card>
      )}

      {tab === 'documents' && (
        <Card title={`Documents (${g.documents})`}>
          {/* Upload and preview already exist on Check-in, against the same
              table. Rebuilding that flow here would mean two places to fix a
              bug in it, so this points at the one that works. */}
          <p className="text-sm text-slate-500">
            {g.documents === 0
              ? 'No identity documents are on file for this guest.'
              : `${g.documents} document${g.documents === 1 ? '' : 's'} on file.`}
          </p>
          <p className="mt-2 text-sm text-slate-400">
            Documents are captured and viewed during check-in, where the desk
            has the guest and their ID in front of them.
          </p>
        </Card>
      )}

      {tab === 'payments' && (
        <Card title="Payments">
          {realStays.length === 0 ? (
            <p className="text-sm text-slate-400">Nothing has been billed yet.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[560px] text-sm">
                <thead className="border-b border-slate-100 text-left text-xs uppercase tracking-wide text-slate-500">
                  <tr>
                    {['Reservation', 'Stay', 'Charges', 'Paid', 'Balance'].map((h) => (
                      <th key={h} className="px-3 py-2.5 font-medium">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {realStays.map((s) => {
                    const balance = Number(s.charges) - Number(s.paid)
                    return (
                      <tr key={s.unit_id} className="border-b border-slate-100 last:border-0">
                        <td className="px-3 py-2.5">
                          <Link to={`/reservations/${s.reservation_id}`}
                            className="font-medium text-brand hover:underline">
                            {s.number}
                          </Link>
                        </td>
                        <td className="px-3 py-2.5 text-slate-600">
                          {fmtDate(s.arrival_date)} – {fmtDate(s.departure_date)}
                        </td>
                        <td className="px-3 py-2.5 text-slate-600">{money(s.charges)}</td>
                        <td className="px-3 py-2.5 text-slate-600">{money(s.paid)}</td>
                        <td className={`px-3 py-2.5 font-medium ${
                          balance > 0 ? 'text-caution' : 'text-positive'}`}>
                          {money(balance)}
                        </td>
                      </tr>
                    )
                  })}
                  <tr className="bg-slate-50/60 font-medium text-slate-800">
                    <td className="px-3 py-2.5" colSpan={2}>Lifetime</td>
                    <td className="px-3 py-2.5">{money(g.stats.lifetime_value)}</td>
                    <td className="px-3 py-2.5">
                      {money(realStays.reduce((t, s) => t + Number(s.paid), 0))}
                    </td>
                    <td className="px-3 py-2.5">
                      {money(realStays.reduce(
                        (t, s) => t + Number(s.charges) - Number(s.paid), 0))}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          )}
          <p className="mt-3 flex items-start gap-2 text-sm text-slate-400">
            <CreditCard size={15} className="mt-0.5 shrink-0" />
            Individual payments, refunds and their methods live on each
            reservation&rsquo;s folio.
          </p>
        </Card>
      )}
    </div>
  )
}
