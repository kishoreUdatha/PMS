import type { ReactNode } from 'react'
import { Loader2 } from 'lucide-react'
import { fmtDate, whenLabel } from '../lib/dates'

/** The one stay list, used by Reservations, Arrivals, In-house and Departures.
 *
 * These four tabs answer four questions about the same thing — a booked stay —
 * and they had drifted into four different tables. Arrivals called occupancy
 * "Guests" next to a "Guest" column and split the room over "Room Type" and
 * "Room"; Departures and In-house stacked it into "Room Details"; Reservations
 * put occupancy under the guest's name and split the stay across three
 * columns. A desk that moves between tabs all day had to relearn the grid each
 * time.
 *
 * So the columns live here, once. A tab varies by which rows it supplies and
 * what it puts in the action cell — never by what the columns mean.
 */
export interface StayRow {
  key: string
  number: string
  /** When the booking was taken; shown under the number. */
  bookedAt?: string | null
  guestName?: string | null
  roomType: string
  /** "×2" and similar, for a booking holding more than one unit. */
  unitsLabel?: string
  roomCodes?: string[]
  /** Board or rate basis. */
  plan?: string | null
  /** Anything the tab wants beside the room codes — an Assign Room link. */
  roomExtra?: ReactNode
  arrival: string
  departure: string
  nights: number
  /** False where no folio has been opened. "Nothing posted yet" and "a folio
   *  that comes to zero" are different facts; showing both as 0.00 reads as a
   *  stay worth nothing. */
  hasFolio?: boolean
  total: number | string
  paid: number | string
  balance: number | string
  status: ReactNode
  /** The raw status, alongside the rendered pill. The pill is a node and
   *  cannot be read back, so anything that needs to *colour* by state — the
   *  card's edge stripe — needs the word itself. */
  statusKey?: string
  adults?: number
  children?: number
  /** Where a screen knows them. Absent on the booking list, which holds dates
   *  and not times; the card leaves the line out rather than showing a
   *  property's standard check-in time as if it were this guest's. */
  arrivalTime?: string | null
  departureTime?: string | null
  action?: ReactNode
  onClick?: () => void
  selected?: boolean
}

const money = new Intl.NumberFormat('en-IN', {
  minimumFractionDigits: 2, maximumFractionDigits: 2,
})

const COLS = ['Reservation No.', 'Guest', 'Room Details', 'Stay']
const MONEY_COLS = ['Total (₹)', 'Paid (₹)', 'Balance (₹)']

export default function StayTable({
  rows, loading, empty, showAction = true,
}: {
  rows: StayRow[]
  loading?: boolean
  empty: ReactNode
  /** Tabs with nothing to do per row (Reservations) drop the last cell. */
  showAction?: boolean
}) {
  const span = COLS.length + MONEY_COLS.length + 1 + (showAction ? 1 : 0)

  return (
    <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
      <table className="w-full min-w-[1080px] text-left text-sm">
        <thead>
          <tr className="border-b border-slate-200 bg-slate-50/60 text-sm text-slate-600">
            {COLS.map((h) => (
              <th key={h} className="whitespace-nowrap px-4 py-3 font-semibold">{h}</th>
            ))}
            {MONEY_COLS.map((h) => (
              <th key={h} className="whitespace-nowrap px-4 py-3 text-right font-semibold">{h}</th>
            ))}
            <th className="whitespace-nowrap px-4 py-3 font-semibold">Status</th>
            {showAction && <th className="px-4 py-3" />}
          </tr>
        </thead>
        <tbody>
          {loading && (
            <tr><td colSpan={span} className="px-4 py-12 text-center text-slate-400">
              <Loader2 size={18} className="mx-auto animate-spin" />
            </td></tr>
          )}
          {!loading && rows.length === 0 && (
            <tr><td colSpan={span}
              className="px-4 py-12 text-center text-sm text-slate-500">
              {empty}
            </td></tr>
          )}
          {!loading && rows.map((r) => (
            <tr key={r.key} onClick={r.onClick}
              className={`border-b border-slate-100 last:border-0 ${
                r.onClick ? 'cursor-pointer' : ''} ${
                r.selected ? 'bg-brand-light' : 'hover:bg-slate-50'}`}>
              <td className="px-4 py-3 font-medium text-slate-800">
                {r.number}
                {r.bookedAt && (
                  <span className="block text-xs font-normal text-slate-400">
                    Booked {fmtDate(r.bookedAt)}
                  </span>
                )}
              </td>
              <td className="px-4 py-3">
                {r.guestName
                  ? <span className="text-slate-700">{r.guestName}</span>
                  : <span className="italic text-slate-400">No guest on file</span>}
              </td>
              {/* Type, the rooms actually assigned, then the plan — the room
                  described the way a desk describes it, not split over two
                  columns one of which is called "Room". */}
              <td className="px-4 py-3">
                <span className="block text-slate-700">
                  {r.roomType}
                  {r.unitsLabel && <span className="text-slate-400"> {r.unitsLabel}</span>}
                </span>
                <span className="block text-xs">
                  {(r.roomCodes?.length ?? 0) > 0
                    ? <span className="text-slate-500">Room {r.roomCodes!.join(', ')}</span>
                    : !r.roomExtra && <span className="text-amber-600">Not assigned</span>}
                  {r.roomExtra}
                </span>
                {r.plan && <span className="block text-xs text-slate-400">{r.plan}</span>}
              </td>
              <td className="whitespace-nowrap px-4 py-3 text-slate-600">
                {fmtDate(r.arrival)} → {fmtDate(r.departure)}
                <span className="ml-1.5 text-slate-400">({r.nights}N)</span>
                {(() => {
                  // How far off the arrival is — an arrival still sitting in
                  // the past is the row worth chasing.
                  const w = whenLabel(r.arrival)
                  return w ? (
                    <span className={`ml-2 rounded-full px-2 py-0.5 text-xs font-medium ${w.tone}`}>
                      {w.text}
                    </span>
                  ) : null
                })()}
              </td>
              <td className="px-4 py-3 text-right tabular-nums text-slate-600">
                {r.hasFolio === false
                  ? <span className="text-xs text-slate-300">—</span>
                  : money.format(Number(r.total))}
              </td>
              <td className="px-4 py-3 text-right tabular-nums text-slate-600">
                {r.hasFolio === false
                  ? <span className="text-xs text-slate-300">—</span>
                  : money.format(Number(r.paid))}
              </td>
              {/* Only the outstanding figure is coloured — it is the one that
                  decides whether this guest can walk out. */}
              <td className={`px-4 py-3 text-right font-semibold tabular-nums ${
                r.hasFolio === false ? ''
                  : Number(r.balance) > 0 ? 'text-red-600' : 'text-emerald-600'}`}>
                {r.hasFolio === false
                  ? <span className="text-xs font-normal text-slate-300">No folio yet</span>
                  : money.format(Number(r.balance))}
              </td>
              <td className="px-4 py-3">{r.status}</td>
              {showAction && (
                <td className="px-4 py-3 text-right">{r.action}</td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
