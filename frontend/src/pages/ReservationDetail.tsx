import { useState } from 'react'
import FolioWorkspace from '../components/FolioWorkspace'
import { StayInformationTab, TasksTab } from '../components/ReservationTabs'
import ActionsMenu from '../components/ActionsMenu'
import { fmtDate, fmtDateLong, fmtDateTime } from '../lib/dates'
import Select from '../components/Select'
import ListSelect from '../components/ListSelect'
import { PURPOSES_OF_STAY } from '../lib/options'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ArrowLeft, Loader2, Pencil, BedDouble, XCircle, CheckCircle2, Calendar,
  Moon, Users, FileText, MessageSquare, History, CalendarDays,
  Wallet, Info, Mail, Phone, MapPin, Pencil as PencilIcon, X, Loader2 as Spin,
  Printer, Wrench,
  AlertTriangle,
} from 'lucide-react'
import {
  getReservationFull, updateBookingDetails, openFolioPdf, BOOKING_SOURCES,
  listProperties, listBookingAttributes,
  type ReservationFull,
} from '../api'
import { errorText } from '../lib/forms'

const money = (v: string | number, cur = 'INR') =>
  new Intl.NumberFormat('en-IN', {
    style: 'currency', currency: cur, maximumFractionDigits: 0,
  }).format(Number(v))
const exact = (v: string | number) =>
  new Intl.NumberFormat('en-IN', { minimumFractionDigits: 2 }).format(Number(v))
// The app-wide spelling. These were three local `toLocaleDateString('en-IN')`
// calls, which write September as "Sept" -- so this screen read "Sun, 13 Sept,
// 2026" under a header saying "Sun, 13 Sep 2026".
const day = (d: string | null) => fmtDateLong(d)
const shortDay = (d: string | null) => fmtDate(d)
const stamp = (d: string) => fmtDateTime(d)


// Named as the reference names them. Only the wording changed: "Overview" is
// the booking's details, "Activity" is the audit trail, and calling them what
// the design calls them costs nothing and saves a person translating.
//
// Five, and each of them answers a question somebody actually arrives with:
// what do they owe, what did they book, who is staying, what needs doing,
// what happened. Ten tabs answered the same five questions twice over.
//
// Four are gone because they were the same data under another heading. Room
// Charges was this ledger filtered to debits; Credit Card was this ledger
// filtered to card payments; both are options in the folio's own filter now,
// which is one table to keep right instead of three. Stay Information was the
// other half of what was booked, so it joins Booking Details. Communications
// was one line of history, so it joins the history.
const TABS = [
  // Folio first. The desk opens a booking to answer "what do they owe" far
  // more often than anything else, and the tab that answers it was two clicks
  // away on another screen.
  { key: 'folio', label: 'Folio & Charges', icon: Wallet },
  { key: 'overview', label: 'Booking Details', icon: FileText },
  { key: 'guests', label: 'Guest Details', icon: Users },
  { key: 'tasks', label: 'Tasks', icon: Wrench },
  { key: 'activity', label: 'Audit Trail', icon: History },
] as const

type TabKey = typeof TABS[number]['key']

export default function ReservationDetail() {
  const { reservationId = '' } = useParams()
  const nav = useNavigate()
  const qc = useQueryClient()
  const [tab, setTab] = useState<TabKey>('folio')

  const { data: r, isLoading } = useQuery({
    queryKey: ['reservation-full', reservationId],
    queryFn: () => getReservationFull(reservationId),
    enabled: reservationId !== '',
  })

  if (isLoading) {
    return <p className="py-20 text-center text-slate-400">
      <Loader2 className="mx-auto animate-spin" />
    </p>
  }
  if (!r) {
    return <p className="py-20 text-center text-sm text-slate-400">
      Reservation not found.
    </p>
  }

  const counts: Partial<Record<TabKey, number>> = {
    guests: r.guests.length,
    activity: r.activity.length,
  }

  return (
    <div className="space-y-3">
      {/* One card, not three bands.
          A back link on its own line, a title row under it and a facts card
          under that is three horizontal rules of chrome before anything a
          person came to read. The back arrow belongs beside the thing it goes
          back from, and the actions belong beside what they act on, so all of
          it is one card with a rule through the middle.

          One line, not three.
          The title band used to stack a big heading, a subtitle and a row of
          buttons above a card that then repeated most of it: the reservation
          number appeared three times (heading, "Confirmation #", and a column
          in the strip) and the status three times (beside the heading, beside
          the guest, and as the badge). Four bands of chrome before any content,
          most of it the same two facts. This is the same information once. */}
      <div className="rounded-2xl border border-slate-100 bg-white">
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b border-slate-100 px-5 py-3">
        <div className="flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-1">
          <Link to="/reservations/list" title="Back to reservations"
            className="self-center rounded-lg p-1 text-slate-400 hover:bg-slate-75 hover:text-brand">
            <ArrowLeft size={17} />
          </Link>
          <h1 className="text-xl font-bold text-ink">
            Reservation {r.number}
          </h1>
          <span className="text-sm text-slate-500">
            Booked {shortDay(r.created_at)}
            {r.booking.source_label && (
              <>
                <span className="px-1.5 text-slate-300">|</span>
                {r.booking.source_label}
              </>
            )}
          </span>
        </div>
        {/* One button and a menu, not five buttons.

            Five controls of equal weight is five decisions, and four of them
            were wrong for whoever is looking: a desk editing a misspelt name
            does not want Cancel one tab-stop away from Edit. Edit is the one
            people came for, so it keeps the fill; the rest sit under the dots
            in the order they get used, with Cancel last and in red because it
            is the one you cannot take back. */}
        <div className="flex flex-wrap items-center gap-2">
          <button onClick={() => nav(`/reservations/${r.id}/edit`)}
            className="flex items-center gap-1.5 rounded-xl bg-brand px-3.5 py-2 text-sm font-semibold text-white outline-none hover:bg-brand-dark focus-visible:ring-2 focus-visible:ring-brand/40">
            <Pencil size={15} /> Edit
          </button>
          <ActionsMenu label="More actions for this booking" items={[
            ...(r.can_modify ? [{
              label: 'Modify stay',
              icon: CalendarDays,
              group: 'stay',
              description: 'Change dates, rooms or rate — re-quoted first',
              onSelect: () => nav(`/reservations/${r.id}/modify`),
            }] : []),
            ...(r.can_assign ? [{
              label: 'Assign room',
              icon: BedDouble,
              group: 'stay',
              description: 'Pick a room on the rack',
              onSelect: () => nav('/rooms/rack'),
            }] : []),
            ...(r.financials.folio_id ? [{
              label: 'Open folio',
              icon: Wallet,
              group: 'folio',
              onSelect: () => nav(`/reservations/${r.id}/folio`),
            }, {
              label: 'Print / send folio',
              icon: Printer,
              group: 'folio',
              description: 'The booking, not whichever tab is open',
              onSelect: () => void openFolioPdf(
                r.property_id, r.financials.folio_id!),
            }] : []),
            ...(r.can_cancel ? [{
              label: 'Cancel booking',
              icon: XCircle,
              group: 'end',
              tone: 'danger' as const,
              onSelect: () => nav(`/reservations/${r.id}/modify`),
            }] : []),
          ]} />
        </div>
      </div>

      {/* One strip, because a desk reads these together: who, when, which
          room, and what state the booking is in. Separate cards made it four
          glances. Divided rather than boxed so the eye runs along it. */}
      <div className="flex flex-wrap items-stretch gap-x-6 gap-y-4 px-5 py-3.5">
        <div className="flex min-w-[15rem] flex-1 items-start gap-3">
          <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-full bg-brand-light text-sm font-bold text-brand">
            {(r.guest?.full_name ?? '?').trim().split(/\s+/)
              .map((w) => w[0]).slice(0, 2).join('').toUpperCase()}
          </span>
          <span className="min-w-0">
            <span className="flex flex-wrap items-center gap-2">
              <span className="text-lg font-semibold text-ink">
                {r.guest?.full_name ?? 'No guest recorded'}
              </span>
              <span className="flex items-center gap-1 text-xs text-slate-500">
                <Users size={11} />
                {r.adults} adult{r.adults === 1 ? '' : 's'}
                {r.children > 0 && `, ${r.children} child${r.children === 1 ? '' : 'ren'}`}
              </span>
            </span>
            {r.guest?.phone && (
              <span className="flex items-center gap-1.5 text-xs text-slate-500">
                <Phone size={11} /> {r.guest.phone}
              </span>
            )}
            {r.guest?.email && (
              <span className="flex items-center gap-1.5 text-xs text-slate-500">
                <Mail size={11} /> {r.guest.email}
              </span>
            )}
            {(r.guest?.city || r.guest?.country) && (
              <span className="flex items-center gap-1.5 text-xs text-slate-500">
                <MapPin size={11} />
                {[r.guest.city, r.guest.country].filter(Boolean).join(', ')}
              </span>
            )}
          </span>
        </div>

        <Strip icon={Calendar} label="Arrival" value={day(r.arrival_date)}
          sub={hhmm(r.units[0]?.expected_arrival_time)} />
        <Strip icon={Calendar} label="Departure" value={day(r.departure_date)}
          sub={hhmm(r.units[0]?.expected_departure_time)} />
        <Strip icon={Moon} label="Nights" value={String(r.nights ?? '—')} />
        <Strip icon={BedDouble} label="Room type"
          value={r.units[0]?.room_type ?? '—'} />
        <Strip icon={BedDouble} label="Room"
          value={r.units.map((u) => u.room_code).filter(Boolean).join(', ')
            || 'Not assigned'} />

        <div className="flex items-center">
          <StatusBadge status={r.status} />
        </div>
      </div>
      </div>

      {/* The active tab is a filled panel, not an underline: on a screen this
          dense a 2px rule is easy to lose, and which view you are in is the
          thing a person checks most often. Print/Send sits at the far end
          because it acts on whatever is on screen, not on a tab. */}
      <div className="border-b border-slate-200">
        {/* Wraps rather than scrolls. With the overflow menu gone there are
            ten tabs, and a scrolling strip hid the last of them behind an
            edge — a tab nobody can see is a tab nobody uses. */}
        <div className="flex flex-wrap gap-1">
          {TABS.map((t) => (
            <button key={t.key} onClick={() => setTab(t.key)}
              className={`flex items-center gap-1.5 whitespace-nowrap rounded-t-xl px-4 py-2.5 text-sm font-semibold ${
                tab === t.key
                  ? 'bg-brand text-white'
                  : 'text-slate-500 hover:bg-slate-50 hover:text-slate-700'}`}>
              <t.icon size={15} /> {t.label}
              {counts[t.key] !== undefined && counts[t.key]! > 0 && (
                <span className={`rounded-full px-1.5 text-xs ${
                  tab === t.key ? 'bg-white/20 text-white' : 'bg-slate-75 text-slate-500'}`}>
                  {counts[t.key]}
                </span>
              )}
            </button>
          ))}
        </div>
      </div>

      {tab === 'folio' && (
        <FolioWorkspace r={r}
          onPosted={() => void qc.invalidateQueries(
            { queryKey: ['reservation-full'] })} />
      )}
      {/* What was booked, in one place: the commercial terms and then the
          stay they buy. They were two tabs, and the join between them — this
          rate plan, those nights — was a click. */}
      {tab === 'overview' && (
        <div className="space-y-5">
          <Overview r={r} />
          <StayInformationTab r={r} />
        </div>
      )}
      {tab === 'guests' && <GuestsTab r={r} />}
      {tab === 'tasks' && <TasksTab r={r} />}
      {tab === 'activity' && <ActivityTab r={r} />}
    </div>
  )
}

function Overview({ r }: { r: ReservationFull }) {
  const b = r.booking
  const f = r.financials
  const [editing, setEditing] = useState(false)
  return (
    <div className="grid gap-5 lg:grid-cols-3">
      <Card title="Booking Information" icon={FileText}
        action={r.can_modify ? (
          <button onClick={() => setEditing(true)}
            className="flex items-center gap-1.5 rounded-lg border border-slate-200 px-2.5 py-1 text-xs font-semibold text-slate-600 hover:bg-slate-50">
            <PencilIcon size={13} /> Edit
          </button>
        ) : null}>
        {editing && (
          <BookingEditModal r={r} onClose={() => setEditing(false)} />
        )}
        <Facts rows={[
          ['Booking Source', b.source_label],
          ['Business Source', b.business_source],
          ['Rate Plan', b.rate_plan],
          ['Meal Plan', b.meal_plan],
          ['Package', b.package],
          ['Market Segment', b.market_segment],
          ['Purpose of Stay', b.purpose_of_stay],
          ['Agent / Company', b.company_name],
          ['Travel Agent', b.travel_agent],
          ['Reference', b.reference],
          ['Remarks', b.remarks],
        ]} collapseEmpty />
      </Card>

      {/* Stay Details and Cancellation Policy used to sit here as well.
          Both now live in Stay Information, which is the tab named for them —
          the same rooms, dates, rate plan, board and policy rendered twice
          under two headings is not thoroughness, it is two places to correct
          when something changes. This tab keeps its own question: how the
          booking reached us, and where it stands financially. */}
      <div className="space-y-5">
        <Card title="Financial Summary" icon={Wallet}>
          <dl className="space-y-2 text-sm">
            <Line k="Total Amount" v={money(f.total_charges, f.currency)} />
            <Line k="Amount Paid" v={money(f.total_paid, f.currency)} tone="emerald" />
            <Line k="Balance Due" v={money(f.balance_due, f.currency)}
              tone={Number(f.balance_due) > 0 ? 'red' : 'emerald'} strong />
          </dl>
          <div className="mt-4 rounded-xl bg-brand-light/40 px-4 py-3">
            <p className="flex items-baseline justify-between text-sm">
              {/* "0% Paid" against a zero bill reads like an unpaid account. */}
              <span className="font-semibold text-brand">
                {Number(f.total_charges) === 0
                  ? 'Nothing charged' : `${f.paid_percent}% Paid`}
              </span>
              <span className="text-xs text-slate-500">
                {exact(f.total_paid)} / {exact(f.total_charges)}
              </span>
            </p>
            <div className="mt-2 h-2 overflow-hidden rounded-full bg-white">
              <div className="h-full rounded-full bg-emerald-500"
                style={{ width: `${f.paid_percent}%` }} />
            </div>
          </div>
        </Card>

      </div>
    </div>
  )
}

function GuestsTab({ r }: { r: ReservationFull }) {
  return (
    <Card title="Guests" icon={Users}>
      {r.guests.length === 0 ? (
        <p className="py-6 text-center text-sm text-slate-400">
          No guest is linked to this booking.
        </p>
      ) : (
        <ul className="divide-y divide-slate-50">
          {r.guests.map((g) => (
            <li key={g.id ?? g.full_name} className="flex flex-wrap items-start justify-between gap-3 py-3">
              <span className="min-w-0">
                <span className="flex items-center gap-2 font-semibold text-slate-800">
                  {g.full_name ?? 'Unnamed'}
                  {g.is_primary && (
                    <span className="rounded-full bg-brand-light px-2 py-0.5 text-[11px] font-semibold text-brand">
                      Primary
                    </span>
                  )}
                </span>
                <span className="block text-xs text-slate-500">
                  {[g.phone, g.email].filter(Boolean).join(' · ') || '—'}
                </span>
                <span className="block text-xs text-slate-400">
                  {[g.city, g.country, g.nationality].filter(Boolean).join(', ') || '—'}
                </span>
              </span>
              <span className="shrink-0 text-right text-xs">
                <span className="block text-slate-500">
                  {g.id_type ? g.id_type.replace('_', ' ') : 'No ID recorded'}
                </span>
                <span className={`inline-flex items-center gap-1 ${
                  g.id_verified ? 'text-emerald-600' : 'text-amber-600'}`}>
                  <CheckCircle2 size={11} />
                  {g.id_verified ? 'ID verified' : 'Not verified'}
                </span>
              </span>
            </li>
          ))}
        </ul>
      )}
      {/* Said out loud rather than shown as an empty list. */}
      <p className="mt-3 flex items-start gap-2 border-t border-slate-100 pt-3 text-xs text-slate-400">
        <Info size={13} className="mt-0.5 shrink-0" />
        Only the lead guest is linked to a booking. There is no additional-occupant
        table, so a second adult on the same room cannot be named or ID-checked yet.
      </p>
    </Card>
  )
}

function ActivityTab({ r }: { r: ReservationFull }) {
  return (
    <div className="space-y-5">
      {/* Communications was a tab holding a single sentence. What was sent to
          a guest and what was done to their booking are both the answer to
          "what happened here", so they are one tab and read in order. */}
      <Card title="Communications" icon={MessageSquare}>
        <p className="flex items-start gap-2 py-1 text-sm text-slate-500">
          <Info size={16} className="mt-0.5 shrink-0 text-slate-400" />
          {r.communications_note}
        </p>
      </Card>
    <Card title="Activity" icon={History}>
      {r.activity.length === 0 ? (
        <p className="py-6 text-center text-sm text-slate-400">
          Nothing has been recorded against this booking.
        </p>
      ) : (
        <ul className="space-y-4">
          {r.activity.map((a, i) => (
            <li key={i} className="flex gap-3">
              <span className="flex flex-col items-center">
                <span className="mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full bg-brand" />
                {i < r.activity.length - 1 && (
                  <span className="mt-1 w-px flex-1 bg-slate-75" />
                )}
              </span>
              <span className="min-w-0 pb-1">
                <span className="block text-sm font-medium capitalize text-slate-800">
                  {a.label}
                </span>
                {a.detail && (
                  <span className="block text-xs text-slate-500">{a.detail}</span>
                )}
                {a.reason && (
                  <span className="block text-xs italic text-slate-400">
                    “{a.reason}”
                  </span>
                )}
                <span className="block text-[11px] text-slate-400">
                  {stamp(a.at)} · {a.actor}
                </span>
              </span>
            </li>
          ))}
        </ul>
      )}
      <p className="mt-3 flex items-start gap-2 border-t border-slate-100 pt-3 text-xs text-slate-400">
        <Info size={13} className="mt-0.5 shrink-0" />
        Read straight from the audit log, so it shows what was actually written —
        including anything done outside this screen.
      </p>
    </Card>
    </div>
  )
}

function Card({ title, icon: Icon, action, children }: {
  title: string; icon?: typeof FileText; action?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <div className="rounded-2xl border border-slate-100 bg-white p-5">
      <div className="mb-3 flex items-center justify-between gap-3">
        <h2 className="flex items-center gap-2 text-lg font-semibold text-ink">
          {Icon && <Icon size={18} className="text-brand" />} {title}
        </h2>
        {action}
      </div>
      {children}
    </div>
  )
}

/**
 * A label/value list, optionally hiding the rows nothing has been entered in.
 *
 * Booking Information carries eleven fields and all but one of them are
 * optional classification the desk fills in when it matters -- market segment,
 * purpose of stay, travel agent. A booking taken on the website supplies none
 * of them, because a guest is never asked for a market segment, so the card
 * rendered as one value above ten em-dashes and read as a screen that had
 * failed to load.
 *
 * Empty rows are folded away rather than dropped: they are still the fields
 * Edit will offer, and a count is the honest way to say "there are more, and
 * none of them are filled in".
 */
function Facts({ rows, collapseEmpty = false }: {
  rows: [string, string | null | undefined][]
  collapseEmpty?: boolean
}) {
  const [showAll, setShowAll] = useState(false)
  const empty = rows.filter(([, v]) => !v)
  const shown = !collapseEmpty || showAll ? rows : rows.filter(([, v]) => v)
  return (
    <>
      <dl className="divide-y divide-slate-50 text-sm">
        {shown.map(([k, v]) => (
          <div key={k} className="flex justify-between gap-4 py-2">
            <dt className="text-slate-500">{k}</dt>
            <dd className={`text-right ${v ? 'font-medium text-slate-800' : 'text-slate-300'}`}>
              {v || '—'}
            </dd>
          </div>
        ))}
      </dl>
      {collapseEmpty && shown.length === 0 && (
        <p className="py-2 text-sm text-slate-400">Nothing recorded yet.</p>
      )}
      {collapseEmpty && empty.length > 0 && (
        <button type="button" onClick={() => setShowAll(!showAll)}
          className="mt-2 flex items-center gap-1 text-xs font-semibold text-brand hover:underline">
          {showAll ? 'Hide' : `Show ${empty.length} field${
            empty.length === 1 ? '' : 's'} not set`}
        </button>
      )}
    </>
  )
}

function Line({ k, v, tone, strong }: {
  k: string; v: string; tone?: 'emerald' | 'red'; strong?: boolean
}) {
  return (
    <div className={`flex justify-between gap-4 ${strong ? 'border-t border-slate-100 pt-2' : ''}`}>
      <dt className="text-slate-500">{k}</dt>
      <dd className={`text-right ${
        tone === 'emerald' ? 'text-emerald-600' : tone === 'red' ? 'text-red-600'
          : 'text-slate-800'} ${strong ? 'text-lg font-bold' : 'font-medium'}`}>
        {v}
      </dd>
    </div>
  )
}

/** A stored time as "12:00 PM", or nothing when none was given. */
function hhmm(t?: string | null): string | undefined {
  if (!t) return undefined
  const [h, m] = t.split(':').map(Number)
  if (Number.isNaN(h)) return undefined
  const suffix = h < 12 ? 'AM' : 'PM'
  const hour = h % 12 === 0 ? 12 : h % 12
  return `${hour}:${String(m ?? 0).padStart(2, '0')} ${suffix}`
}

/** One fact in the header strip, with a rule separating it from the last. */
function Strip({ icon: Icon, label, value, sub }: {
  icon: typeof Calendar; label: string; value: string; sub?: string
}) {
  return (
    <div className="border-l border-slate-100 pl-6">
      <span className="flex items-center gap-1.5 text-xs text-slate-500">
        <Icon size={13} className="text-brand" /> {label}
      </span>
      <span className="mt-0.5 block whitespace-nowrap font-semibold text-ink">
        {value}
      </span>
      {/* Shown, not editable: the expected time is captured when the booking
          is taken and no endpoint changes it afterwards. A pencil that opens
          nothing is worse than no pencil. */}
      {sub && (
        <span className="block whitespace-nowrap text-xs text-slate-500">
          {sub}
        </span>
      )}
    </div>
  )
}

/** The booking's state, in the colour the rest of the application uses for it. */
function StatusBadge({ status }: { status: string }) {
  const tone: Record<string, string> = {
    confirmed: 'bg-emerald-50 text-emerald-700 ring-emerald-200',
    held: 'bg-amber-50 text-caution ring-amber-200',
    checked_in: 'bg-brand-light text-brand ring-brand/30',
    checked_out: 'bg-slate-75 text-slate-600 ring-slate-200',
    cancelled: 'bg-red-50 text-red-700 ring-red-200',
    no_show: 'bg-red-50 text-red-700 ring-red-200',
  }
  return (
    <span className={`rounded-xl px-4 py-2 text-sm font-semibold capitalize ring-1 ${
      tone[status] ?? 'bg-slate-75 text-slate-600 ring-slate-200'}`}>
      {status.replace(/_/g, ' ')}
    </span>
  )
}

const inputCls = 'w-full rounded-lg border border-slate-200 px-3 py-2.5 text-sm outline-none focus:border-brand'

/**
 * Corrects how a booking was recorded — source, segment, who it is for.
 *
 * Dates, room type and guest counts are absent on purpose: changing those
 * changes what is sold and what it costs, so they go through Modify Stay
 * where availability is re-checked and the price difference is shown.
 */
function BookingEditModal({ r, onClose }: {
  r: ReservationFull; onClose: () => void
}) {
  const qc = useQueryClient()
  const b = r.booking
  // The two classification masters, so these fields pick rather than accept
  // whatever is typed. Active only — a retired entry stays on the bookings
  // that carry it but is not offered again.
  const { data: properties } = useQuery({
    queryKey: ['properties'], queryFn: listProperties,
  })
  const organizationId =
    properties?.find((p) => p.id === r.property_id)?.organization_id
    ?? properties?.[0]?.organization_id ?? ''
  const { data: sources } = useQuery({
    queryKey: ['booking-attributes', organizationId, 'business_source'],
    queryFn: () => listBookingAttributes(organizationId,
      { kind: 'business_source', status: 'active' }),
    enabled: organizationId !== '',
  })
  const { data: segments } = useQuery({
    queryKey: ['booking-attributes', organizationId, 'market_segment'],
    queryFn: () => listBookingAttributes(organizationId,
      { kind: 'market_segment', status: 'active' }),
    enabled: organizationId !== '',
  })
  const [f, setF] = useState({
    source: b.source ?? '',
    business_source_id: b.business_source_id ?? '',
    market_segment_id: b.market_segment_id ?? '',
    purpose_of_stay: b.purpose_of_stay ?? '',
    company_name: b.company_name ?? '',
    travel_agent: b.travel_agent ?? '',
    reference: b.reference ?? '',
    remarks: b.remarks ?? '',
    special_requests: b.special_requests ?? '',
  })
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const set = (k: string, v: string) => setF((p) => ({ ...p, [k]: v }))

  async function save() {
    setErr(''); setBusy(true)
    try {
      await updateBookingDetails(r.id, {
        ...f,
        source: f.source || null,
        business_source_id: f.business_source_id || null,
        market_segment_id: f.market_segment_id || null,
        purpose_of_stay: f.purpose_of_stay || null,
        company_name: f.company_name || null,
        travel_agent: f.travel_agent || null,
        reference: f.reference || null,
        remarks: f.remarks || null,
        special_requests: f.special_requests || null,
      })
      qc.invalidateQueries({ queryKey: ['reservation-full', r.id] })
      qc.invalidateQueries({ queryKey: ['reservations'] })
      onClose()
    } catch (e) {
      setErr(errorText(e, 'Could not save.'))
    } finally { setBusy(false) }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4"
      onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()}
        className="max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-2xl bg-white p-6 shadow-xl">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold text-ink">
            Edit Booking Details
          </h2>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600">
            <X size={18} />
          </button>
        </div>
        <p className="mt-2 text-sm text-slate-500">
          How this booking was recorded. To change dates, room type or guest
          numbers use{' '}
          <Link to={`/reservations/${r.id}/modify`} className="font-semibold text-brand hover:underline">
            Modify Stay
          </Link>
          {' '}— those re-price the stay.
        </p>
        {err && (
          <p className="mt-3 flex items-start gap-2 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">
            <AlertTriangle size={15} className="mt-0.5 shrink-0" /> {err}
          </p>
        )}
        <div className="mt-4 grid gap-3 sm:grid-cols-2">
          <label className="block">
            <span className="mb-1 block text-sm font-medium text-slate-600">
              Booking Source
            </span>
            <Select value={f.source} onChange={(e) => set('source', e.target.value)}
              className={inputCls}>
              <option value="">Not recorded</option>
              {BOOKING_SOURCES.map((s) => (
                <option key={s.value} value={s.value}>{s.label}</option>
              ))}
            </Select>
          </label>
          {/* Both are master rows now, so these pick from the vocabulary
              rather than accepting whatever is typed. */}
          <label className="block">
            <span className="mb-1 block text-xs font-medium text-slate-500">
              Business Source
            </span>
            <Select value={f.business_source_id}
              onChange={(e) => set('business_source_id', e.target.value)}
              className={inputCls}>
              <option value="">Not recorded</option>
              {(sources?.rows ?? []).map((s) => (
                <option key={s.id} value={s.id}>{s.name}</option>
              ))}
            </Select>
          </label>
          <label className="block">
            <span className="mb-1 block text-xs font-medium text-slate-500">
              Market Segment
            </span>
            <Select value={f.market_segment_id}
              onChange={(e) => set('market_segment_id', e.target.value)}
              className={inputCls}>
              <option value="">Not recorded</option>
              {(segments?.rows ?? []).map((s) => (
                <option key={s.id} value={s.id}>{s.name}</option>
              ))}
            </Select>
          </label>
          <label className="block">
            <span className="mb-1 block text-sm font-medium text-slate-600">Purpose of Stay</span>
            <ListSelect value={f.purpose_of_stay} options={PURPOSES_OF_STAY}
              placeholder="Not recorded" className={inputCls}
              onChange={(v) => set('purpose_of_stay', v)} />
          </label>
          <Text label="Reference" v={f.reference} on={(v) => set('reference', v)}
            hint="Their PO or OTA booking number" />
          <Text label="Agent / Company" v={f.company_name}
            on={(v) => set('company_name', v)} />
          <Text label="Travel Agent" v={f.travel_agent}
            on={(v) => set('travel_agent', v)} />
          <label className="block sm:col-span-2">
            <span className="mb-1 block text-sm font-medium text-slate-600">
              Special Requests
            </span>
            <textarea value={f.special_requests} rows={2} maxLength={1000}
              onChange={(e) => set('special_requests', e.target.value)}
              className={`${inputCls} resize-none`}
              placeholder="What the guest asked for, in their words" />
          </label>
          <label className="block sm:col-span-2">
            <span className="mb-1 block text-sm font-medium text-slate-600">
              Remarks
            </span>
            <textarea value={f.remarks} rows={2} maxLength={1000}
              onChange={(e) => set('remarks', e.target.value)}
              className={`${inputCls} resize-none`}
              placeholder="Internal note about this booking" />
          </label>
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <button onClick={onClose}
            className="rounded-lg border border-slate-200 px-4 py-2 text-sm text-slate-600 hover:bg-slate-50">
            Cancel
          </button>
          <button onClick={save} disabled={busy}
            className="flex items-center gap-2 rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white enabled:hover:bg-brand/90 disabled:opacity-50">
            {busy && <Spin size={15} className="animate-spin" />} Save Changes
          </button>
        </div>
      </div>
    </div>
  )
}

function Text({ label, v, on, hint }: {
  label: string; v: string; on: (v: string) => void; hint?: string
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-sm font-medium text-slate-600">{label}</span>
      <input value={v} onChange={(e) => on(e.target.value)} className={inputCls} />
      {hint && <span className="mt-1 block text-xs text-slate-400">{hint}</span>}
    </label>
  )
}
