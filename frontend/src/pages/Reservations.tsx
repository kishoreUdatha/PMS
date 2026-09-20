import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import {
  CalendarDays, CheckCircle2, Home, Hourglass, List, LogIn, LogOut, Plus, Search, Tag,
} from 'lucide-react'
import {
  listReservations, 
  getReservationCounts,
} from '../api'
import AssignRoomsPanel from '../components/AssignRoomsPanel'
import { Arrivals } from './GuestCheckIn'
import { Departures } from './GuestCheckOut'
import InHouse from './InHouse'
import DateRangeFilter from '../components/DateRangeFilter'
import { StayLayoutToggle, StayView, useStayLayout } from '../lib/listLayout'
import RowActions from '../components/RowActions'

import { fmtClock, fmtDate } from '../lib/dates'
import { useActivePropertyId } from '../hooks/useProperty'

const STATUSES = [
  { key: '', label: 'All' },
  { key: 'held', label: 'Held' },
  { key: 'confirmed', label: 'Confirmed' },
  { key: 'cancelled', label: 'Cancelled' },
  { key: 'completed', label: 'Completed' },
]


/** "6 Sep 2026" — the format every other screen uses. */
function day(iso?: string | null): string {
  if (!iso) return '—'
  const d = new Date(`${iso.slice(0, 10)}T00:00:00`)
  return fmtDate(d)
}

const todayIso = () => new Date().toISOString().slice(0, 10)

function statusPill(s: string) {
  const map: Record<string, string> = {
    held: 'bg-amber-50 text-amber-600',
    confirmed: 'bg-emerald-50 text-emerald-600',
    cancelled: 'bg-red-50 text-red-600',
    completed: 'bg-slate-75 text-slate-500',
    draft: 'bg-slate-75 text-slate-500',
  }
  return map[s] ?? 'bg-slate-75 text-slate-500'
}

/** The three ways this screen's bookings can be looked at. One list, so a
 *  fourth view is a line here rather than another copy of the markup. */
const VIEWS = [
  { label: 'Stayview', to: '/reservations', Icon: CalendarDays, active: false },
  { label: 'Reservations', to: '/reservations/list', Icon: List, active: true },
  { label: 'Enquiries', to: '/reservations/enquiries', Icon: Hourglass, active: false },
]

export default function Reservations() {
  const navigate = useNavigate()
  const propertyId = useActivePropertyId()
  const [params, setParams] = useSearchParams()
  const [status, setStatus] = useState('')
  // Seeded from `?q=`, so a link can land on a search rather than on the
  // unfiltered list. The Rooming panel links here by reservation number and
  // had been doing exactly that -- to a parameter nothing read, which loaded
  // 255 bookings and left you to find the one you clicked.
  const [query, setQuery] = useState(params.get('q') ?? '')
  const [assignOpen, setAssignOpen] = useState<string | null>(null)
  const [assignToast, setAssignToast] = useState('')
  // The API has taken arrival_from/arrival_to since US-028; the screen never
  // offered them, so 35 bookings arrived as one undifferentiated list.
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  // Only `tab` lives in the URL here. This used to carry `?id=` as well, for
  // the summary drawer that opened over the list -- see the note beside
  // RowActions below for why that drawer went. Nothing reads `id` any more,
  // and three screens went on linking to `/reservations/list?id=<uuid>` long
  // after it stopped meaning anything: the link worked, the page loaded, and
  // the booking you asked for was simply not shown. A booking's own screen is
  // `/reservations/:reservationId`. `q` IS read -- see the search box state
  // above.
  // Which row's actions panel is open. Local state, not the URL.
  //
  // It was a search param, which meant closing the panel was a router
  // navigation -- and the panel closes itself the instant an action is
  // chosen, one line before that action navigates. Two navigations in the
  // same tick, and the second lost: every action in the panel appeared to do
  // nothing at all. Nothing needs this in the address bar.
  const [actionsFor, setActionsFor] = useState('')
  // Shared with the other three tabs; see lib/listLayout for why it is not
  // local state.
  const [layout, chooseLayout] = useStayLayout()

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['reservations', propertyId, status, query, from, to],
    queryFn: () => listReservations(propertyId, {
      status: status || undefined, query: query || undefined,
      arrival_from: from || undefined, arrival_to: to || undefined,
    }),
    enabled: propertyId !== '',
  })

  const rows = data ?? []
  const filtered = status !== '' || query !== '' || from !== '' || to !== ''
  const setRange = (a: string, b: string) => { setFrom(a); setTo(b) }

  // The four states a booking passes through. They were split across two nav
  // sections, which meant knowing a guest's state before you knew where to
  // look for them; one screen now answers "where is this guest" whatever the
  // answer happens to be. Which action a guest is offered follows from the tab
  // they are in, so somebody already in a room is never shown a check-in that
  // could only tell them it had already happened.
  // An unrecognised ?tab — a stale bookmark, a typo, a link from elsewhere —
  // used to match none of the branches and render the tab strip above an
  // empty page. Anything unknown falls back to the full list.
  const TABS = ['all', 'arrivals', 'in-house', 'departures']
  const requestedTab = params.get('tab') ?? 'all'
  const stateTab = TABS.includes(requestedTab) ? requestedTab : 'all'
  const setStateTab = (t: string) => {
    const next = new URLSearchParams(params)
    if (t === 'all') next.delete('tab')
    else next.set('tab', t)
    setParams(next)
  }
  // The workload behind each tab, so a desk can see it without clicking
  // through four lists to find three of them empty.
  const { data: counts } = useQuery({
    queryKey: ['reservation-counts', propertyId],
    queryFn: () => getReservationCounts(propertyId),
    enabled: propertyId !== '',
  })
  const STATE_TABS = [
    { key: 'all', label: 'Reservations', Icon: List, n: counts?.reservations,
      hint: 'Every booking, whatever its state' },
    { key: 'arrivals', label: 'Arrivals', Icon: LogIn, n: counts?.arrivals,
      hint: 'Due in today, not yet checked in' },
    { key: 'in-house', label: 'In-house', Icon: Home, n: counts?.in_house,
      hint: 'Currently in a room' },
    { key: 'departures', label: 'Departures', Icon: LogOut, n: counts?.departures,
      hint: 'Due out, and what they still owe' },
  ]

  return (
    <div className="space-y-4">
      {/* Title and actions share one line. The tagline under the title told
          a desk that opens this screen all day nothing it did not know, and
          cost a line of bookings to say it. */}
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <h1 className="flex min-w-0 items-center gap-2 text-display text-ink"><CalendarDays size={26} className="text-brand" /> Reservations</h1>
        {/* One row. These were flex-wrap, and the three controls came to
            759px against the 684px left over beside the title, so the
            view toggle dropped to a second line under the two buttons --
            two rows of controls for one screen. Trimmed to fit instead
            of allowed to stack. */}
        <div className="flex shrink-0 items-center gap-2">
          {/* The vocabularies bookings are classified by. Alongside the
              list rather than in the view toggle beside it — that toggle
              switches how these bookings are shown, and this is not a view
              of them. */}
          <Link to="/reservations/sources"
            className="flex items-center gap-1.5 whitespace-nowrap rounded-lg border border-slate-200 px-2.5 py-1.5 text-sm font-medium text-slate-600 hover:bg-slate-50">
            <Tag size={14} /> Sources &amp; Segments
          </Link>
          <button onClick={() => navigate('/reservations/new')}
            className="flex items-center gap-1.5 whitespace-nowrap rounded-lg bg-brand px-3 py-1.5 text-sm font-medium text-white hover:bg-brand/90">
            <Plus size={15} /> New Reservation
          </button>
          {/* Three views of one set of bookings, as one bordered track so
              they read as alternatives rather than three more actions. The
              current one is filled brand green like every selected control
              in the app. */}
          <span className="inline-flex items-center gap-0.5 rounded-lg border border-slate-200 bg-white p-0.5">
            {VIEWS.map(({ label, to, Icon, active }) => active ? (
              <span key={label}
                className="flex items-center gap-1.5 whitespace-nowrap rounded-md bg-brand px-2.5 py-1 text-sm font-semibold text-white">
                <Icon size={14} /> {label}
              </span>
            ) : (
              <button key={label} onClick={() => navigate(to)}
                className="flex items-center gap-1.5 whitespace-nowrap rounded-md px-2.5 py-1 text-sm font-medium text-slate-500 hover:bg-slate-50 hover:text-slate-700">
                <Icon size={14} /> {label}
              </button>
            ))}
          </span>
        </div>
      </div>

      <div className="flex flex-wrap items-end gap-1 border-b border-slate-200">
        {STATE_TABS.map(({ key, label, Icon, n, hint }) => (
          <button key={key} onClick={() => setStateTab(key)} title={hint}
            className={`flex items-center gap-1.5 border-b-2 px-4 py-2.5 text-sm font-semibold ${
              stateTab === key
                ? 'border-brand text-brand'
                : 'border-transparent text-slate-500 hover:text-slate-700'}`}>
            <Icon size={15} /> {label}
            {/* Nothing rather than a zero while the count is still loading —
                a flash of "0" beside Arrivals reads as "nobody is coming". */}
            {n !== undefined && (
              <span className={`rounded-full px-1.5 py-0.5 text-xs font-semibold ${
                stateTab === key ? 'bg-brand text-white'
                  : n > 0 ? 'bg-slate-75 text-slate-600'
                    : 'bg-transparent text-slate-300'}`}>
                {n}
              </span>
            )}
          </button>
        ))}
      </div>

      {stateTab === 'arrivals' && <Arrivals />}
      {stateTab === 'in-house' && <InHouse />}
      {stateTab === 'departures' && <Departures />}

      {stateTab === 'all' && (<>
      {/* Filters: one row, which has to hold on a laptop at 125% zoom.
          Status is a segmented track like the view switcher above -- one
          choice of five, a third narrower than five separate pills, with
          the chosen one filled green. The
          arrival window folds into one button that still names its range.
          Search takes whatever width is left instead of a fixed one, so the
          row gives before it breaks; below ~900px it wraps with search
          spanning its own line. The total is on the tab already, so the
          count only appears when a filter narrows it. */}
      <div className="flex flex-wrap items-center gap-2">
        <span className="inline-flex shrink-0 items-center gap-0.5 rounded-lg border border-slate-200 bg-white p-0.5">
          {STATUSES.map((sdef) => (
            <button key={sdef.key} onClick={() => setStatus(sdef.key)} aria-pressed={status === sdef.key}
              className={`whitespace-nowrap rounded-md px-3 py-1.5 text-sm ${status === sdef.key
                ? 'bg-brand font-semibold text-white'
                : 'font-medium text-slate-500 hover:bg-slate-50 hover:text-slate-700'}`}>
              {sdef.label}
            </button>
          ))}
        </span>
        <DateRangeFilter label="Arriving" from={from} to={to} onChange={setRange} />
        <div className="flex min-w-[240px] flex-1 items-center justify-end gap-2">
          {filtered && (
            <span className="whitespace-nowrap text-sm text-slate-500">
              {rows.length} {rows.length === 1 ? 'match' : 'matches'}
            </span>
          )}
          <div className="relative min-w-0 max-w-sm flex-1">
            <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search number or guest…" className="w-full rounded-lg border border-slate-200 bg-slate-50 py-2 pl-9 pr-3 text-sm outline-none focus:border-brand" />
          </div>
          <StayLayoutToggle layout={layout} onChange={chooseLayout} />
        </div>
      </div>

      {/* One mapping, two readings of it. The table and the cards are handed
          the same rows — including the status pill, the Assign Room link and
          the actions menu — so the two cannot start disagreeing about what a
          booking is. */}
      <StayView
        layout={layout}
        loading={isLoading}
        empty={
          <>
            {from && to && from === to
              ? `No reservations arrive on ${day(from)}.`
              : from || to
                ? `No reservations arrive between ${day(from)} and ${day(to)}.`
                : status
                  ? `No reservations are ${status}.`
                  : query
                    ? `Nothing matches “${query}”.`
                    : 'There are no reservations yet.'}
            {filtered && (
              <button
                onClick={() => { setStatus(''); setQuery(''); setRange('', '') }}
                className="ml-2 font-semibold text-brand hover:underline">
                Clear all filters
              </button>
            )}
          </>
        }
        rows={rows.map((r) => ({
          key: r.id,
          number: r.number,
          bookedAt: r.booked_at,
          guestName: r.guest_name,
          roomType: r.room_type,
          unitsLabel: r.units > 1 ? `×${r.units}` : undefined,
          roomCodes: r.room_codes ?? [],
          plan: r.plan,
          roomExtra: (r.unassigned_units ?? 0) > 0 ? (
            <button
              onClick={(e) => { e.stopPropagation(); setAssignOpen(r.arrival_date ?? '') }}
              className="font-semibold text-brand hover:underline">
              {(r.room_codes?.length ?? 0) > 0 ? ' · ' : ''}
              Assign Room
              {r.unassigned_units! > 1 && ` (${r.unassigned_units})`}
            </button>
          ) : undefined,
          arrival: r.arrival_date ?? '',
          departure: r.departure_date ?? '',
          nights: r.nights,
          hasFolio: r.has_folio,
          total: r.total_amount ?? r.total_charges ?? 0,
          paid: r.total_paid ?? 0,
          balance: r.balance_due ?? 0,
          status: (
            <span className={`rounded-full px-2.5 py-1 text-xs font-medium capitalize ${statusPill(r.status)}`}>
              {r.status}
            </span>
          ),
          // The word as well as the pill: the card colours its edge stripe by
          // state, and a rendered node cannot be read back.
          statusKey: r.status,
          adults: r.adults,
          children: r.children,
          arrivalTime: fmtClock(r.expected_arrival_time),
          departureTime: fmtClock(r.expected_departure_time),
          // The row and the "⋮" open the same panel. They used to open two
          // different ones -- a summary drawer from the row, the actions
          // panel from the menu -- which is why a click near the right-hand
          // edge produced a different thing from a click anywhere else.
          onClick: () => setActionsFor(r.id),
          selected: actionsFor === r.id,
          // Booking-level: this list has no single unit, so the actions that
          // act on one stay (check in, move room, no-show) are not offered.
          action: (
            <RowActions
              open={actionsFor === r.id}
              onOpenChange={(v) => setActionsFor(v ? r.id : '')}
              ctx={{
              propertyId,
              reservationId: r.id,
              number: r.number,
              hasFolio: r.has_folio,
              room: r.room_codes?.[0] ?? null,
              state: r.status,
              guestName: r.guest_name,
              roomType: r.room_type,
              departureDate: r.departure_date,
              nights: r.nights,
              adults: r.adults,
              children: r.children,
              total: Number(r.total_amount ?? r.total_charges ?? 0),
              paid: Number(r.total_paid ?? 0),
              statusLabel: r.status,
              balance: Number(r.balance_due ?? 0),
              arrivalDate: r.arrival_date,
              businessDate: todayIso(),
              onDone: () => void refetch(),
              onAssignRoom: () => setAssignOpen(r.arrival_date ?? ''),
              show: {
                checkIn: false, checkOut: false, moveRoom: false,
                noShow: false, housekeeping: false, roomHistory: false,
                // Booking-level: a card is signed per room, and this list
                // has no single room. Hidden rather than shown disabled,
                // which is what this tab does with every other unit action.
                registrationCard: false, complimentary: false,
                postCharge: false, collectPayment: false, adjustFolio: false,
                taxInvoice: false, printBill: false,
              },
            }} />
          ),
        }))}
      />

      {assignToast && (
        <p className="flex items-center gap-2 rounded-xl bg-emerald-50 px-4 py-3 text-sm text-emerald-700">
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
          }} />
      )}

      {/* The summary drawer that used to open from a row click is gone.
          It and the actions panel were two right-hand panels about the
          same booking, offering mostly the same actions, chosen by how
          near the right-hand edge you happened to click. RowActions now
          serves both, controlled by `actionsFor`. */}
      </>)}
    </div>
  )
}
