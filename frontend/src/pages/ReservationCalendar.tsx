import { useMemo, useState } from 'react'
import Select from '../components/Select'
import { CONTROL, FILTER_BOX, FILTER_CHIP, FILTER_SELECT } from '../lib/controls'
import { useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { fmtDate, fmtDayMonth, fmtWeekday } from '../lib/dates'
import {
  AlertTriangle, CalendarDays, CheckCircle2, ChevronDown, ChevronLeft,
  ChevronRight, Info, Loader2, X,
} from 'lucide-react'
import {
  getReservationCalendar, listManagedRoomTypes, 
  assignRoomToUnit, RACK_STATUSES,
  type RackBar, type RackView,
} from '../api'
import { useActivePropertyId } from '../hooks/useProperty'
import { errorText } from '../lib/forms'

/**
 * Screen 003 — Reservation Calendar (the room rack).
 *
 * Every bar comes from `booking.room_calendar_entries`, the same table the GiST
 * exclusion constraint guards, so what the rack shows and what the system will
 * allow are the same thing by construction. Reservations and maintenance blocks
 * are both entries; only their colour differs.
 *
 * The unassigned lane is not a leftover — it is the screen's real job. A
 * booking with no room yet sits under its room type until someone puts it
 * somewhere, and assigning it goes through `assign_room`, so a double-booking
 * is rejected by the database rather than prevented by this UI being careful.
 */


const asDate = (iso: string) => new Date(`${iso.slice(0, 10)}T00:00:00`)
const iso = (d: Date) => d.toISOString().slice(0, 10)
const day = (s?: string | null) => fmtDate(s)
const shortDay = (s: string) => fmtDayMonth(s)
const addDays = (isoStr: string, n: number) => {
  const d = asDate(isoStr)
  d.setDate(d.getDate() + n)
  return iso(d)
}
const daysBetween = (a: string, b: string) =>
  Math.round((asDate(b).getTime() - asDate(a).getTime()) / 86400000)

const BAR_STYLE: Record<string, string> = {
  confirmed: 'bg-emerald-100 text-emerald-900 ring-emerald-300',
  tentative: 'bg-amber-100 text-amber-900 ring-amber-300',
  checked_in: 'bg-sky-100 text-sky-900 ring-sky-300',
  // Muted on purpose: a finished stay is history on the grid, not something
  // competing for attention with tonight's arrivals.
  checked_out: 'bg-slate-75 text-slate-500 ring-slate-300',
  blocked: 'bg-rose-100 text-rose-900 ring-rose-300',
}

// The mockup gives each room-type band its own tint; cycled by position so any
// number of room types keeps the same rhythm.
/** Rates on the grid: no decimals, no symbol — the column is 40px wide and
 *  the currency is stated once in the footer. */
const inr = (v: string | number) =>
  new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 }).format(Number(v))

const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/** The mock's chip row. "All" first, then the states a desk asks about. */
const STATE_CHIPS = [
  { key: 'all_rooms', label: 'All' },
  { key: 'vacant', label: 'Vacant' },
  { key: 'occupied', label: 'Occupied' },
  { key: 'reserved', label: 'Reserved' },
  { key: 'blocked', label: 'Blocked' },
  { key: 'due_out', label: 'Due Out' },
  { key: 'not_ready', label: 'Not ready' },
] as const

// Room-type bands, all in the brand family. They used to cycle through sky,
// orange, violet and rose — five unrelated hues stacked down one screen, which
// read as five different meanings rather than five room types. These are
// shades of the same teal, so the bands separate the types without any of
// them claiming to be a status.
const GROUP_TINTS = [
  'bg-brand-light text-brand-dark',
  'bg-teal-50 text-teal-900',
  'bg-cyan-50 text-cyan-900',
  'bg-emerald-50 text-emerald-900',
  'bg-slate-75 text-slate-700',
]

const WINDOW_DAYS = 14


/* ------------------------------------------------------------------ page --- */
export default function ReservationCalendar() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const propertyId = useActivePropertyId()

  const [start, setStart] = useState(() => iso(new Date()))
  const [roomType, setRoomType] = useState('')
  // Which chip is lit. It highlights the count being read; the
  // grid filter itself still lives in the Status dropdown.
  const [stateChip, setStateChip] = useState<string>('')
  const [picked, setPicked] = useState<RackBar | null>(null)
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({})
  const [toast, setToast] = useState('')
  const [error, setError] = useState('')

  const end = addDays(start, WINDOW_DAYS)

  const q = useQuery({
    queryKey: ['rack', propertyId, start, end, roomType],
    queryFn: () => getReservationCalendar(propertyId, { start, end },
      { room_type_id: roomType }),
    enabled: propertyId !== '',
  })
  const typesQ = useQuery({
    queryKey: ['managedRoomTypes', propertyId],
    queryFn: () => listManagedRoomTypes(propertyId), enabled: propertyId !== '',
  })

  const view: RackView | undefined = q.data
  const days = view?.days ?? []
  const barsByRoom = useMemo(() => {
    const m: Record<string, RackBar[]> = {}
    for (const b of view?.bars ?? []) {
      if (b.room_id) (m[b.room_id] ??= []).push(b)
    }
    return m
  }, [view])

  const assign = useMutation({
    mutationFn: ({ unitId, roomId }: { unitId: string; roomId: string }) =>
      assignRoomToUnit(unitId, roomId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['rack'] })
      setPicked(null)
      setError('')
      setToast('Room assigned.')
      setTimeout(() => setToast(''), 3000)
    },
    onError: (e) => {
      setToast('')
      setError(errorText(e, 'That room could not be assigned. It may already be taken.'))
    },
  })

  // Rooms the picked booking could actually go in: right type, and free for
  // every night of the stay according to the bars already drawn.
  const candidateRooms = useMemo(() => {
    if (!picked || !view || picked.room_id) return []
    const group = view.groups.find((g) => g.room_type_id === picked.room_type_id)
    if (!group) return []
    return group.rooms.filter((room) => !(barsByRoom[room.id] ?? []).some(
      (b) => b.start_date < picked.end_date && b.end_date > picked.start_date))
  }, [picked, view, barsByRoom])

  return (
    <div className="space-y-4">
      {/* -------------------------------------------------------- header --- */}
      {/* Every other screen names itself. This one did not, which left the
          page with no heading for a screen reader to announce and nothing
          above the grid saying what you are looking at. */}
      <div>
        <h1 className="flex items-center gap-2 text-display text-ink">
          <CalendarDays size={26} className="text-brand" /> Stayview
        </h1>
        <p className="text-slate-500">
          Room-by-room availability and stays across the dates you choose.
        </p>
      </div>
      {toast && (
        <p className="flex items-center gap-2 rounded-xl bg-emerald-50 px-4 py-3 text-sm text-emerald-700">
          <CheckCircle2 size={16} /> {toast}
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

      {/* ------------------------------------------------------- filters --- */}
      <div className="flex items-center gap-2">
        <span className="flex shrink-0 items-center overflow-hidden rounded-lg border border-slate-200">
          <button onClick={() => setStart(addDays(start, -WINDOW_DAYS))}
            aria-label="Previous window"
            className="px-2.5 py-2 text-slate-500 hover:bg-slate-50">
            <ChevronLeft size={16} />
          </button>
          <span className={`border-x border-slate-200 px-4 py-2 ${CONTROL}`}>
            {shortDay(start)} – {shortDay(addDays(end, -1))} {asDate(start).getFullYear()}
          </span>
          <button onClick={() => setStart(addDays(start, WINDOW_DAYS))}
            aria-label="Next window"
            className="px-2.5 py-2 text-slate-500 hover:bg-slate-50">
            <ChevronRight size={16} />
          </button>
        </span>
        <button onClick={() => setStart(iso(new Date()))}
          className={`shrink-0 ${FILTER_BOX} ${CONTROL} hover:bg-slate-50`}>
          Today
        </button>
        <label className="flex shrink-0 items-center">
          <Select className={FILTER_SELECT} blankIsChoice value={roomType}
            onWheel={(e) => (e.target as HTMLSelectElement).blur()}
            onChange={(e) => setRoomType(e.target.value)}>
            <option value="">All Room Types</option>
            {(typesQ.data ?? []).map((t) => (
              <option key={t.id} value={t.id}>{t.name}</option>
            ))}
          </Select>
        </label>
        {/* The chips share the filter line rather than sitting on their own
            row: they answer the same question the select does — which rooms am
            I looking at — and a second row of controls above the grid pushed
            the rooms themselves further down the page.
            They also replaced an "All Statuses" dropdown that sat here. Two
            status filters on one row is one too many: that one narrowed by
            *reservation* status while these narrow by *room* state, and the
            two overlap without agreeing — Blocked appeared in both, Reserved
            and Occupied were the same rooms as Confirmed and Checked In. The
            chips win because they carry their counts, so you can see there is
            nothing to look at before you filter to it, and because the legend
            below the grid already spells out what each reservation colour
            means. */}
        {view && (
          <span className="scroll-slim ml-auto flex min-w-0 items-center gap-1.5 overflow-x-auto">
            {STATE_CHIPS.map((c) => {
              const n = view.room_states?.[c.key] ?? 0
              const on = stateChip === c.key
              return (
                <button key={c.key}
                  onClick={() => setStateChip(on ? '' : c.key)}
                  className={`${FILTER_CHIP} ${
                    on ? 'bg-brand text-white'
                      : 'border border-slate-200 bg-white text-slate-600 hover:border-slate-300 hover:text-ink'}`}>
                  {c.label}
                  <span className={`rounded-full px-1.5 text-xs ${
                    on ? 'bg-white/25' : 'bg-white'}`}>{n}</span>
                </button>
              )
            })}
          </span>
        )}
      </div>

      {view && view.unassigned_count > 0 && (
        <p className="flex items-start gap-2 rounded-lg bg-amber-50 px-4 py-3 text-sm text-amber-900">
          <Info size={16} className="mt-0.5 shrink-0" />
          <span>
            <strong className="font-semibold">
              {view.unassigned_count} booking{view.unassigned_count === 1 ? '' : 's'}
            </strong>
            {' '}in this window {view.unassigned_count === 1 ? 'has' : 'have'} no
            room yet — they are listed under their room type. Pick one to put it
            in a room.
          </span>
        </p>
      )}

      {/* The rack takes the full width until a bar is picked; the booking
          panel is the answer to a question you asked, not furniture. */}
      <div className={`grid gap-4 ${
        picked ? 'xl:grid-cols-[minmax(0,1fr)_320px]' : ''}`}>
        {/* ------------------------------------------------------- rack --- */}
        <div className="min-w-0 overflow-x-auto rounded-xl border border-slate-200 bg-white">
          {q.isLoading && (
            <p className="p-10 text-center text-slate-400">
              <Loader2 size={18} className="mx-auto animate-spin" />
            </p>
          )}
          {view && view.groups.length === 0 && (
            <p className="p-10 text-center text-sm text-slate-500">
              No rooms match this filter.
            </p>
          )}
          {view && view.groups.length > 0 && (
            <div className="min-w-[900px]">
              <Row header days={days} label="Room / Date" />
              {/* Only the rooms scroll. The date header above and the two
                  totals below stay put, because those are the lines you are
                  reading the grid against — scrolling to the bottom to see
                  how much is left, then back up to find the room, is the
                  whole job done twice. */}
              <div className="scroll-slim max-h-[60vh] overflow-y-auto">
              {view.groups.map((g, gi) => (
                <div key={g.room_type_id}>
                  {/* The band and its figures are one row, as on the mock.
                      A separate strip underneath doubled the height of every
                      room type and pushed the rooms themselves off screen. */}
                  <div style={gridStyle(days.length)}
                    className={`border-y border-slate-200 ${
                      GROUP_TINTS[gi % GROUP_TINTS.length]}`}>
                    <button
                      onClick={() => setCollapsed((c) =>
                        ({ ...c, [g.room_type_id]: !c[g.room_type_id] }))}
                      className="flex items-center gap-2 px-3 py-1.5 text-left text-sm font-semibold">
                      <ChevronDown size={15}
                        className={`transition-transform ${
                          collapsed[g.room_type_id] ? '-rotate-90' : ''}`} />
                      {g.name} ({g.rooms.length})
                      {g.rooms.length === 0 && (
                        <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-medium text-caution">
                          no rooms
                        </span>
                      )}
                    </button>
                    {days.map((d, i) => {
                      const free = g.availability?.[i] ?? 0
                      const closed = g.stop_sell?.[i]
                      return (
                        <div key={d}
                          className="border-l border-white/40 px-1 py-1 text-center leading-tight">
                          {/* Rooms left, in the brand teal when there are any
                              and red once the type is full — the two answers
                              a rack is scanned for. A type closed for sale is
                              struck through rather than shown as sellable. */}
                          <div className={`text-xs font-bold tabular-nums ${
                            closed ? 'text-slate-400 line-through'
                              : free === 0 ? 'text-red-600' : 'text-brand'}`}>
                            {g.availability?.[i] ?? '—'}
                          </div>
                          {/* "from" when the rooms of this type are priced
                              differently: the figure is then the cheapest of
                              them, not the price of the type. */}
                          <div className="text-[10px] font-medium tabular-nums text-brand-dark/70"
                            title={g.rate_varies?.[i]
                              ? 'Rooms of this type are priced differently — this is the lowest'
                              : undefined}>
                            {g.rates?.[i] == null ? '—' : (
                              <>
                                {g.rate_varies?.[i] && (
                                  <span className="mr-0.5 font-normal opacity-60">from</span>
                                )}
                                ₹{inr(g.rates[i]!)}
                              </>
                            )}
                          </div>
                        </div>
                      )
                    })}
                  </div>
                  {!collapsed[g.room_type_id] && <>

                  {g.unassigned.length > 0 && (() => {
                    const lanes = packLanes(g.unassigned)
                    return (
                      // Bounded so a long queue cannot push the rooms off the
                      // screen; every booking is still reachable by scrolling.
                      <div className={lanes.length > 5
                        ? 'max-h-[220px] overflow-y-auto bg-amber-50/40' : 'bg-amber-50/40'}>
                        {lanes.map((lane, i) => (
                          <Lane key={i} days={days}
                            label={i === 0
                              ? `Unassigned (${g.unassigned.length})` : ''}
                            muted bars={lane} picked={picked} onPick={setPicked} />
                        ))}
                      </div>
                    )
                  })()}
                  {g.rooms.map((room) => (
                    <Lane key={room.id} days={days}
                      label={
                        <>
                          {room.code}
                          {/* Only rooms priced away from their type show a
                              figure. Repeating the band's rate on every line
                              would bury the one room that differs. */}
                          {room.base_rate != null && (
                            <span className="block text-[10px] font-medium tabular-nums text-brand"
                              title="This room is priced apart from its room type">
                              ₹{inr(room.base_rate)}
                            </span>
                          )}
                        </>
                      }
                      bars={barsByRoom[room.id] ?? []}
                      picked={picked} onPick={setPicked} />
                  ))}
                  </>}
                </div>
              ))}
              </div>

              {/* The two questions a stayview is opened to answer, on one
                  line each: what is left, and how full the house is. */}
              <FigureRow days={days} label="Available Inventory"
                values={view.inventory ?? []}
                tone="border-t-2 border-slate-200 bg-slate-50" />
              <FigureRow days={days} label="Occupancy (%)"
                values={(view.occupancy_pct ?? []).map((v) => `${v}%`)}
                bar={(view.occupancy_pct ?? []).map((v) => Number(v))}
                tone="bg-slate-50" />
            </div>
          )}
          {view && (
            <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-200 px-3 py-2.5">
              <span className="flex flex-wrap items-center gap-3 text-xs text-slate-500">
                {RACK_STATUSES.map((st) => (
                  <span key={st.code} className="flex items-center gap-1.5">
                    <span className={`h-2.5 w-2.5 rounded-full ${st.dot}`} />
                    {st.label}
                  </span>
                ))}
              </span>
              <span className="text-xs text-slate-500">
                Showing {day(view.start_date)} – {day(addDays(view.end_date, -1))}
                {'  |  '}{view.stats.rooms_booked} of {view.stats.total_rooms} rooms
                booked
              </span>
            </div>
          )}
        </div>

        {/* --------------------------------------------- selected booking --- */}
        {picked && (
          <aside className="xl:sticky xl:top-4 xl:self-start">
            <div className="rounded-2xl border border-slate-100 bg-white p-4 shadow-sm">
              <div className="mb-3 flex items-center justify-between">
                <h2 className="font-semibold text-ink">Selected Booking</h2>
                <button onClick={() => setPicked(null)} aria-label="Close"
                  className="rounded-lg p-1 text-slate-400 hover:bg-slate-100">
                  <X size={15} />
                </button>
              </div>
              <Selected bar={picked} rooms={candidateRooms} busy={assign.isPending}
                onAssign={(roomId) => assign.mutate({
                  unitId: picked.reservation_unit_id as string, roomId })}
                // The booking's own screen. This pointed at
                // /reservations/list?id=<uuid>, which worked while that list
                // had a summary drawer keyed on ?id=. The drawer was removed
                // -- RowActions replaced it -- and nothing reads `id` any
                // more, so the button quietly landed on the unfiltered list.
                onOpen={() => picked.reservation_id
                  && navigate(`/reservations/${picked.reservation_id}`)} />
            </div>
          </aside>
        )}
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------ bits --- */

/**
 * Split bars into as few non-overlapping rows as will hold them.
 *
 * An assigned room never needs this — the GiST constraint means one room
 * cannot hold two stays at once — but the unassigned lane is a queue, and 35
 * bookings over the same nights drawn in one row would sit on top of each
 * other and show only the last.
 */
function packLanes(bars: RackBar[]): RackBar[][] {
  const lanes: RackBar[][] = []
  for (const b of [...bars].sort((x, y) => x.start_date.localeCompare(y.start_date))) {
    const lane = lanes.find((l) => l.every(
      (o) => o.end_date <= b.start_date || o.start_date >= b.end_date))
    if (lane) lane.push(b)
    else lanes.push([b])
  }
  return lanes
}

function gridStyle(dayCount: number) {
  return {
    display: 'grid',
    gridTemplateColumns: `160px repeat(${dayCount}, minmax(52px, 1fr))`,
  } as const
}

function Row({ days, label, header }: {
  days: string[]; label: string; header?: boolean
}) {
  return (
    <div style={gridStyle(days.length)}
      className={header ? 'border-b border-slate-200 bg-slate-50/70' : ''}>
      <div className="px-3 py-2 text-sm font-semibold text-slate-600">{label}</div>
      {days.map((d) => {
        const dt = asDate(d)
        const weekend = dt.getDay() === 0 || dt.getDay() === 6
        return (
          <div key={d}
            className={`border-l border-slate-100 px-1 py-2 text-center text-xs ${
              weekend ? 'bg-slate-75/70 text-rose-500' : 'text-slate-500'}`}>
            {/* Weekday above the date: a stayview is read across a week,
                and it is the day of the week that tells you where you are. */}
            <div className="text-[10px] uppercase tracking-wide">{fmtWeekday(dt)}</div>
            <div className="font-semibold text-slate-700">
              {String(dt.getDate()).padStart(2, '0')} {MON[dt.getMonth()]}
            </div>
          </div>
        )
      })}
    </div>
  )
}

/** A row of one figure per day, under a label.
 *
 * Used for the room-type strip (what is left to sell and at what price) and
 * for the two footer rows. A stayview that only draws bars says who is in
 * which room; these are what make it say what can still be sold, which is the
 * other half of the job.
 */
function FigureRow({ days, label, values, sub, tone, bar }: {
  days: string[]
  label: string
  values: (string | number)[]
  /** Second line in each cell — the night's rate, where there is one. */
  sub?: (string | null)[]
  tone?: string
  /** Draw each value as a proportion bar as well as a number. */
  bar?: number[]
}) {
  return (
    <div style={gridStyle(days.length)}
      className={`border-t border-slate-100 ${tone ?? ''}`}>
      <div className="px-3 py-1.5 text-xs font-semibold text-slate-500">{label}</div>
      {days.map((d, i) => (
        <div key={d}
          className="border-l border-slate-100 px-1 py-1.5 text-center leading-tight">
          <div className="text-xs font-semibold tabular-nums text-slate-700">
            {values[i] ?? '—'}
          </div>
          {sub && (
            <div className="text-[10px] tabular-nums text-slate-400">
              {sub[i] ?? '—'}
            </div>
          )}
          {bar && (
            <div className="mx-auto mt-1 h-1 w-8 overflow-hidden rounded-full bg-slate-200">
              <div className="h-full rounded-full bg-brand"
                style={{ width: `${Math.min(100, Math.max(0, bar[i] ?? 0))}%` }} />
            </div>
          )}
        </div>
      ))}
    </div>
  )
}

function Lane({ days, label, bars, muted, picked, onPick }: {
  days: string[]
  label: React.ReactNode
  bars: RackBar[]
  muted?: boolean
  picked: RackBar | null
  onPick: (b: RackBar) => void
}) {
  const first = days[0]
  const last = days[days.length - 1]
  return (
    <div style={gridStyle(days.length)}
      className="border-b border-slate-100 last:border-0">
      <div className={`px-3 py-2 text-sm ${
        muted ? 'font-medium italic text-amber-700' : 'text-slate-700'}`}
        style={{ gridColumn: 1, gridRow: 1 }}>
        {label}
      </div>
      {days.map((d, i) => {
        const dt = asDate(d)
        const weekend = dt.getDay() === 0 || dt.getDay() === 6
        return (
          <div key={d} style={{ gridColumn: i + 2, gridRow: 1 }}
            className={`min-h-[38px] border-l border-slate-100 ${
              weekend ? 'bg-slate-50/70' : ''}`} />
        )
      })}
      {bars.map((b) => {
        // Clip to the window: a stay that started last week still shows, it
        // just begins at the left edge.
        const from = Math.max(0, daysBetween(first, b.start_date))
        const to = Math.min(days.length, daysBetween(first, b.end_date))
        const span = Math.max(1, to - from)
        if (to <= 0 || from >= days.length) return null
        const isPicked = picked?.id === b.id
        return (
          <button key={b.id} onClick={() => onPick(b)}
            title={`${b.label} · ${day(b.start_date)} → ${day(b.end_date)}`}
            style={{ gridColumn: `${from + 2} / span ${span}`, gridRow: 1 }}
            className={`z-10 m-1 truncate rounded-md px-2 py-1 text-xs font-medium ring-1 ${
              BAR_STYLE[b.status] ?? 'bg-slate-75 text-slate-700 ring-slate-300'} ${
              isPicked ? 'ring-2 ring-offset-1 ring-brand' : ''} ${
              b.starts_before ? 'rounded-l-none' : ''} ${
              b.ends_after ? 'rounded-r-none' : ''}`}>
            {b.starts_before && '‹ '}{b.label}{b.ends_after && ' ›'}
          </button>
        )
      })}
      {/* Keep the row from collapsing when the window shows nothing. */}
      {bars.length === 0 && days.length === 0 && <div className="min-h-[38px]" />}
      <span className="sr-only">{last}</span>
    </div>
  )
}

function Selected({ bar, rooms, busy, onAssign, onOpen }: {
  bar: RackBar
  rooms: { id: string; code: string }[]
  busy: boolean
  onAssign: (roomId: string) => void
  onOpen: () => void
}) {
  const [room, setRoom] = useState('')
  const unassigned = bar.room_id === null && bar.reservation_unit_id !== null

  if (bar.status === 'blocked') {
    return (
      <div className="space-y-2 text-sm">
        <p className="text-lg font-semibold text-slate-800">{bar.label}</p>
        <span className="inline-block rounded-full bg-rose-100 px-3 py-1 text-xs font-semibold text-rose-700">
          Blocked
        </span>
        <Field label="From" value={day(bar.start_date)} />
        <Field label="To" value={day(bar.end_date)} />
        <Field label="Nights" value={String(bar.nights)} />
        <p className="pt-1 text-xs text-slate-500">
          This room is out of service for these dates, so nothing can be booked
          into it.
        </p>
      </div>
    )
  }

  return (
    <div className="space-y-2 text-sm">
      <p className="text-lg font-semibold text-slate-800">
        {bar.guest_name ?? <span className="italic text-slate-400">No guest on file</span>}
      </p>
      <span className={`inline-block rounded-full px-3 py-1 text-xs font-semibold ${
        BAR_STYLE[bar.status]}`}>
        {bar.status_label}
      </span>
      <Field label="Reservation" value={bar.number ?? '—'} />
      <Field label="Check In" value={day(bar.start_date)} />
      <Field label="Check Out" value={day(bar.end_date)} />
      <Field label="Nights" value={String(bar.nights)} />
      <Field label="Guests" value={`${bar.adults ?? 0} adults${
        bar.children ? `, ${bar.children} children` : ''}`} />

      {/* What the stay owes. The rack is where an arrival with nothing paid
          gets noticed; without this the only way to find out was to leave the
          screen and look the booking up somewhere else. */}
      <div className="mt-1 rounded-lg border border-slate-100 bg-slate-50 p-2.5">
        {bar.has_folio ? (
          <>
            <Field label="Charged" value={`₹${inr(bar.total)}`} />
            <Field label="Paid" value={`₹${inr(bar.paid)}`} />
            <div className="mt-1 flex items-center justify-between border-t border-slate-200 pt-1.5">
              <span className="text-xs font-semibold text-slate-500">Balance</span>
              <span className={`text-sm font-bold tabular-nums ${
                Number(bar.balance) > 0 ? 'text-red-600' : 'text-emerald-600'}`}>
                ₹{inr(bar.balance)}
              </span>
            </div>
          </>
        ) : (
          // "Nothing posted" and "a folio that comes to zero" are different
          // facts, and a stay with no folio is the one worth chasing.
          <p className="text-xs text-slate-500">
            No folio yet — nothing has been charged to this stay.
          </p>
        )}
      </div>

      {unassigned && (
        <div className="rounded-lg bg-amber-50 p-3">
          <p className="mb-2 text-xs font-semibold text-amber-900">
            No room assigned yet
          </p>
          {rooms.length === 0 ? (
            <p className="text-xs text-amber-900">
              No room of this type is free for the whole stay. Free one up, or
              change the booking's room type.
            </p>
          ) : (
            <>
              <Select value={room} onChange={(e) => setRoom(e.target.value)}
                onWheel={(e) => (e.target as HTMLSelectElement).blur()}
                className="mb-2 w-full rounded-lg border border-amber-200 bg-white px-2.5 py-2 text-sm">
                <option value="">Choose a room…</option>
                {rooms.map((r) => (
                  <option key={r.id} value={r.id}>Room {r.code}</option>
                ))}
              </Select>
              <button disabled={!room || busy} onClick={() => onAssign(room)}
                className="flex w-full items-center justify-center gap-2 rounded-lg bg-brand px-3 py-2 text-sm font-semibold text-white hover:bg-brand-dark disabled:opacity-50">
                {busy && <Loader2 size={14} className="animate-spin" />} Assign Room
              </button>
              <p className="mt-1.5 text-[11px] text-caution">
                {rooms.length} room{rooms.length === 1 ? '' : 's'} free for these
                dates.
              </p>
            </>
          )}
        </div>
      )}

      <button onClick={onOpen}
        className="mt-2 w-full rounded-lg border border-brand px-3 py-2 text-sm font-semibold text-brand hover:bg-brand-light">
        View Reservation
      </button>
    </div>
  )
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <p className="flex justify-between gap-3 border-b border-slate-50 py-1.5">
      <span className="text-slate-500">{label}</span>
      <span className="text-right font-medium text-slate-700">{value}</span>
    </p>
  )
}
