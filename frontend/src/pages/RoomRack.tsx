import { useMemo, useState } from 'react'
import { fmtDateLong } from '../lib/dates'
import { Link, useNavigate } from 'react-router-dom'
import { useQuery, keepPreviousData } from '@tanstack/react-query'
import {
  ChevronLeft, ChevronRight, Calendar, Search, Loader2,
  CalendarPlus, LogIn, LogOut, ArrowLeftRight, Wrench, Info,
} from 'lucide-react'
import { getRoomRack, type RackBar, type RackRoom } from '../api'
import { useActivePropertyId } from '../hooks/useProperty'

/** The visible window. Outside it, a stay is drawn clipped to the edge. */
const DAY_START = 6 * 60
const DAY_END = 22 * 60
const SPAN = DAY_END - DAY_START
const HOURS = Array.from({ length: (DAY_END - DAY_START) / 60 + 1 },
  (_, i) => DAY_START + i * 60)

const hhmm = (m: number) =>
  `${String(Math.floor(m / 60) % 24).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`
// The hour ruler across the top of the rack. 24-hour like every other time in
// the product -- and it removes the reading the old labels invited, where a
// rack running 6 AM to 11 PM showed "12 PM" in the middle and left you working
// out whether that was noon or midnight.
const hourLabel = (m: number) =>
  `${String(Math.floor(m / 60) % 24).padStart(2, '0')}:00`

const TONE: Record<string, { bar: string; text: string; dot: string; label: string }> = {
  checked_in:  { bar: 'bg-blue-100 border-blue-300',      text: 'text-blue-900',    dot: 'bg-blue-500',    label: 'Checked In' },
  due_out:     { bar: 'bg-emerald-100 border-emerald-300', text: 'text-emerald-900', dot: 'bg-emerald-500', label: 'Due Out' },
  arriving:    { bar: 'bg-amber-100 border-amber-300',    text: 'text-amber-900',   dot: 'bg-amber-500',   label: 'Arriving' },
  reserved:    { bar: 'bg-amber-50 border-amber-200',     text: 'text-amber-800',   dot: 'bg-amber-400',   label: 'Reserved' },
  departed:    { bar: 'bg-slate-75 border-slate-300',    text: 'text-slate-600',   dot: 'bg-slate-400',   label: 'Checked Out' },
  cleaning:    { bar: 'bg-rose-100 border-rose-300',      text: 'text-rose-900',    dot: 'bg-rose-500',    label: 'Cleaning' },
  maintenance: { bar: 'bg-slate-200 border-slate-400',    text: 'text-slate-700',   dot: 'bg-slate-500',   label: 'Maintenance' },
}
const LEGEND = ['checked_in', 'arriving', 'due_out', 'cleaning', 'maintenance'] as const

const CHIPS = [
  { key: '', label: 'All Rooms', dot: '' },
  { key: 'available', label: 'Available', dot: 'bg-emerald-500' },
  { key: 'occupied', label: 'Occupied', dot: 'bg-blue-500' },
  { key: 'reserved', label: 'Reserved', dot: 'bg-amber-500' },
  // Key stays 'dirty' -- that is the lane the room-rack endpoint emits, and
  // it already counts 'cleaning' as well as 'dirty'. Only the label was
  // wrong: a room being cleaned right now is not dirty, it is not ready.
  { key: 'dirty', label: 'Not ready', dot: 'bg-rose-500' },
  { key: 'maintenance', label: 'Maintenance', dot: 'bg-slate-400' },
]

const iso = (d: Date) => d.toISOString().slice(0, 10)
const shift = (d: string, days: number) => {
  const x = new Date(`${d}T00:00:00`)
  x.setDate(x.getDate() + days)
  return iso(x)
}
const pretty = (d: string) => fmtDateLong(d)

export default function RoomRack() {
  const nav = useNavigate()
  const propertyId = useActivePropertyId()
  const [day, setDay] = useState(() => iso(new Date()))
  const [lane, setLane] = useState('')
  const [q, setQ] = useState('')

  const { data, isLoading, isFetching } = useQuery({
    queryKey: ['room-rack', propertyId, day],
    queryFn: () => getRoomRack(propertyId, day),
    enabled: propertyId !== '',
    placeholderData: keepPreviousData,
  })

  const rooms = useMemo(() => {
    let list: RackRoom[] = data?.rooms ?? []
    if (lane) list = list.filter((r) => r.lane === lane)
    const needle = q.trim().toLowerCase()
    if (needle) {
      list = list.filter((r) =>
        r.code.toLowerCase().includes(needle)
        || (r.room_type ?? '').toLowerCase().includes(needle)
        || r.bars.some((b) => (b.guest_name ?? '').toLowerCase().includes(needle)
          || (b.reservation_number ?? '').toLowerCase().includes(needle)))
    }
    return list
  }, [data, lane, q])

  const ACTIONS = [
    { label: 'New Reservation', icon: CalendarPlus, to: '/reservations/new', cls: 'bg-brand text-white hover:bg-brand/90' },
    { label: 'Check-in Guest', icon: LogIn, to: '/reservations/list?tab=arrivals', cls: 'bg-blue-50 text-blue-700 hover:bg-blue-100' },
    { label: 'Check-out Guest', icon: LogOut, to: '/reservations/list?tab=departures', cls: 'bg-amber-50 text-amber-700 hover:bg-amber-100' },
    { label: 'Room Change', icon: ArrowLeftRight, to: '/reservations/list?tab=arrivals', cls: 'bg-purple-50 text-purple-700 hover:bg-purple-100' },
  ]

  const nowPct = data?.now_minute != null
    && data.now_minute >= DAY_START && data.now_minute <= DAY_END
    ? ((data.now_minute - DAY_START) / SPAN) * 100
    : null

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          {/* Named for what it is, and for what the sidebar calls it. It
              used to head itself "Front Desk", from when that was its own
              section; the section is gone and the heading disagreeing with
              the nav was the confusion the rename set out to remove. */}
          <h1 className="text-display text-ink">Today&rsquo;s Rack</h1>
          <p className="text-slate-500">Every room, hour by hour, for one day.</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex items-center gap-1 rounded-xl border border-slate-200 bg-white p-1">
            <button onClick={() => setDay(shift(day, -1))} title="Previous day"
              className="rounded-lg p-1.5 text-slate-500 hover:bg-slate-50">
              <ChevronLeft size={17} />
            </button>
            <span className="flex items-center gap-2 px-2 text-sm font-semibold text-slate-700">
              <Calendar size={15} className="text-slate-400" />
              {day === iso(new Date()) ? 'Today, ' : ''}{pretty(day)}
            </span>
            <button onClick={() => setDay(shift(day, 1))} title="Next day"
              className="rounded-lg p-1.5 text-slate-500 hover:bg-slate-50">
              <ChevronRight size={17} />
            </button>
          </div>
          <div className="flex rounded-xl border border-slate-200 bg-white p-1 text-sm font-semibold">
            <span className="rounded-lg bg-brand px-4 py-1.5 text-white">Day View</span>
            <button onClick={() => nav('/reservations')}
              title="Opens the Reservation Calendar"
              className="rounded-lg px-4 py-1.5 text-slate-500 hover:text-slate-700">
              Week View
            </button>
          </div>
          <Link to="/reservations/new"
            className="flex items-center gap-2 rounded-xl bg-brand px-5 py-2.5 text-sm font-medium text-white hover:bg-brand/90">
            <CalendarPlus size={17} /> Walk-in Booking
          </Link>
        </div>
      </div>

      <div className="flex flex-col gap-5 xl:flex-row">
        <div className="min-w-0 flex-1 space-y-4 rounded-2xl border border-slate-100 bg-white p-4">
          <div className="flex flex-wrap items-center gap-2">
            {CHIPS.map((c) => {
              const n = c.key ? data?.lane_counts?.[c.key] : data?.total_rooms
              return (
                <button key={c.label} onClick={() => setLane(c.key)}
                  className={`flex items-center gap-2 rounded-xl border px-4 py-2 text-sm font-medium ${
                    lane === c.key
                      ? 'border-brand bg-brand-light text-brand'
                      : 'border-slate-200 text-slate-600 hover:bg-slate-50'}`}>
                  {c.dot && <span className={`h-2 w-2 rounded-full ${c.dot}`} />}
                  {c.label}
                  {n !== undefined && (
                    <span className="text-xs text-slate-400">{n}</span>
                  )}
                </button>
              )
            })}
            <div className="relative ml-auto min-w-[200px] flex-1">
              <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
              <input value={q} onChange={(e) => setQ(e.target.value)}
                placeholder="Search room or guest…"
                className="w-full rounded-xl border border-slate-200 py-2 pl-9 pr-3 text-sm outline-none focus:border-brand" />
            </div>
          </div>

          <div className="overflow-x-auto">
            <div className="min-w-[860px]">
              <div className="flex border-b border-slate-100 pb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
                <span className="w-32 shrink-0 px-2">Room</span>
                <span className="w-28 shrink-0 px-2">Type</span>
                <span className="relative flex flex-1">
                  {HOURS.slice(0, -1).map((h) => (
                    <span key={h} className="flex-1 text-center">{hourLabel(h)}</span>
                  ))}
                </span>
              </div>

              {isLoading && (
                <p className="py-16 text-center text-slate-400">
                  <Loader2 size={18} className="mx-auto animate-spin" />
                </p>
              )}
              {!isLoading && rooms.length === 0 && (
                <p className="py-16 text-center text-sm text-slate-400">
                  No room matches this filter.
                </p>
              )}

              <div className="divide-y divide-slate-50">
                {rooms.map((r) => (
                  <div key={r.room_id} className="flex items-stretch">
                    <span className="flex w-32 shrink-0 items-center gap-2 px-2 py-2.5">
                      <span className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-lg text-xs font-bold ${
                        r.lane === 'occupied' ? 'bg-blue-50 text-blue-700'
                          : r.lane === 'reserved' ? 'bg-amber-50 text-amber-700'
                            : r.lane === 'dirty' ? 'bg-rose-50 text-rose-700'
                              : r.lane === 'maintenance' ? 'bg-slate-75 text-slate-600'
                                : 'bg-emerald-50 text-emerald-700'}`}>
                        {r.photo_url
                          ? <img src={r.photo_url} alt="" className="h-10 w-10 rounded-lg object-cover" />
                          : r.code}
                      </span>
                      <span className="min-w-0">
                        <Link to={`/rooms/${r.room_id}`}
                          className="block truncate text-sm font-semibold text-slate-800 hover:text-brand">
                          {r.code}
                        </Link>
                        {r.floor && (
                          <span className="block text-[11px] text-slate-400">
                            Floor {r.floor}
                          </span>
                        )}
                      </span>
                    </span>
                    <span className="flex w-28 shrink-0 items-center gap-1 px-2 py-2.5 text-xs text-slate-500">
                      <span className="truncate">{r.room_type ?? '—'}</span>
                      {r.out_of_service && (
                        <Wrench size={12} className="shrink-0 text-slate-400"
                          aria-label="Flagged out of service" />
                      )}
                    </span>

                    <span className="relative flex-1 py-2.5">
                      <span className="absolute inset-y-0 left-0 right-0 flex">
                        {HOURS.slice(0, -1).map((h) => (
                          <span key={h} className="flex-1 border-l border-slate-50" />
                        ))}
                      </span>
                      {nowPct !== null && (
                        <span className="absolute inset-y-0 z-10 w-px bg-red-400/70"
                          style={{ left: `${nowPct}%` }} />
                      )}
                      {r.bars.map((b) => <Bar key={b.id} bar={b} />)}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-4 border-t border-slate-100 pt-3 text-xs text-slate-500">
            {LEGEND.map((s) => (
              <span key={s} className="flex items-center gap-1.5">
                <span className={`h-2.5 w-2.5 rounded-full ${TONE[s].dot}`} />
                {TONE[s].label}
              </span>
            ))}
            {isFetching && !isLoading && (
              <Loader2 size={13} className="animate-spin text-slate-300" />
            )}
          </div>
        </div>

        <div className="w-full shrink-0 space-y-4 xl:w-80">
          <div className="rounded-2xl border border-slate-100 bg-white p-5">
            <h2 className="text-lg font-semibold text-ink">Quick Actions</h2>
            <div className="mt-3 space-y-2">
              {ACTIONS.map((a) => (
                <Link key={a.label} to={a.to}
                  className={`flex items-center gap-3 rounded-xl px-4 py-3 text-sm font-medium ${a.cls}`}>
                  <a.icon size={17} /> {a.label}
                </Link>
              ))}
            </div>
          </div>

          {/* Said out loud rather than filled with a plausible number. */}
          <p className="flex items-start gap-2 text-xs text-slate-400">
            <Info size={13} className="mt-0.5 shrink-0" />
            Bars are drawn from the stay ledger, so they cannot disagree with what
            the system will allow. A time shown without a dot is the property's
            published {data?.checkin_time ?? '14:00'} / {data?.checkout_time ?? '11:00'}
            {' '}rather than one that has happened. There is no Walk-ins count
            because nothing records how a booking arrived.
          </p>
        </div>
      </div>
    </div>
  )
}

function Bar({ bar }: { bar: RackBar }) {
  const from = Math.max(bar.start_minute, DAY_START)
  const to = Math.min(bar.end_minute, DAY_END)
  if (to <= DAY_START || from >= DAY_END) return null

  const left = ((from - DAY_START) / SPAN) * 100
  const width = Math.max(((to - from) / SPAN) * 100, 3)
  const t = TONE[bar.state] ?? TONE.reserved

  const body = (
    <>
      {/* A dot marks a time that actually happened; its absence means expected. */}
      {bar.time_is_actual && (
        <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${t.dot}`} />
      )}
      <span className="min-w-0 leading-tight">
        <span className="block truncate text-xs font-semibold">{bar.label}</span>
        <span className="block truncate text-[11px] opacity-80">{bar.sublabel}</span>
      </span>
    </>
  )
  const cls = `absolute inset-y-1.5 z-[5] flex items-center gap-1.5 overflow-hidden border px-2 ${t.bar} ${t.text} ${
    bar.starts_before ? 'rounded-l-none border-l-0' : 'rounded-l-lg'} ${
    bar.ends_after ? 'rounded-r-none border-r-0' : 'rounded-r-lg'}`
  const title = `${bar.label} · ${bar.sublabel} · ${hhmm(bar.start_minute)}–${hhmm(bar.end_minute)}`
    + (bar.time_is_actual ? ' (recorded)' : ' (expected)')
    + (bar.reservation_number ? ` · ${bar.reservation_number}` : '')

  // Where a bar leads follows the guest's state. Sending an in-house guest
  // to the check-in form gave them a screen whose only message was that it
  // had already happened; check-out is what somebody clicking them wants.
  const href = ['checked_in', 'due_out', 'departed'].includes(bar.state)
    ? `/front-desk/check-out/${bar.reservation_unit_id}`
    : `/front-desk/check-in/${bar.reservation_unit_id}`

  return bar.reservation_unit_id ? (
    <Link to={href} title={title}
      style={{ left: `${left}%`, width: `${width}%` }}
      className={`${cls} hover:brightness-95`}>
      {body}
    </Link>
  ) : (
    <span title={title} style={{ left: `${left}%`, width: `${width}%` }} className={cls}>
      {body}
    </span>
  )
}
