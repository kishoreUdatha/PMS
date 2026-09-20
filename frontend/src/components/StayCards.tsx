import type { ReactNode } from 'react'
import { Baby, Building2, Loader2, User } from 'lucide-react'
import type { StayRow } from './StayTable'
import { fmtDate } from '../lib/dates'

/**
 * The same bookings as StayTable, as cards.
 *
 * Deliberately built on `StayRow` rather than on the API row. The list and
 * the cards are two readings of one thing, and giving each its own mapping is
 * how they start disagreeing — one showing a room the other calls unassigned,
 * one counting a folio the other does not. Everything here comes from the row
 * the table would have rendered, including the status pill, the "Assign Room"
 * link and the actions menu, which are already React nodes.
 *
 * The layout follows the shape a front desk expects: a status stripe down the
 * edge, the guest at the top, then the stay as a band — arrival, how many
 * nights, departure — and the money last. The band is the point of the card.
 * A date range written as a sentence has to be read; written as three blocks
 * with the night count filled in the middle, it is taken in at a glance,
 * which is what someone scanning twenty bookings is doing.
 *
 * Times are shown only where a screen has them. The booking list holds dates
 * and not times, and printing the property's standard check-in time there
 * would state as fact something nobody has agreed with this guest.
 */

const money = new Intl.NumberFormat('en-IN', {
  minimumFractionDigits: 2, maximumFractionDigits: 2,
})

/** The edge stripe. Status is the first thing worth knowing about a booking
 *  and the last thing that should need reading, so it is a colour. */
function stripe(status?: string): string {
  const map: Record<string, string> = {
    confirmed: 'bg-emerald-500',
    held: 'bg-amber-400',
    cancelled: 'bg-red-400',
    completed: 'bg-slate-300',
    draft: 'bg-slate-300',
    checked_in: 'bg-brand',
    in_house: 'bg-brand',
  }
  return map[(status ?? '').toLowerCase()] ?? 'bg-slate-300'
}

export default function StayCards({
  rows, loading, empty, showAction = true,
}: {
  rows: StayRow[]
  loading?: boolean
  empty: ReactNode
  showAction?: boolean
}) {
  if (loading) {
    return (
      <div className="grid place-items-center rounded-2xl border border-slate-100 bg-white py-16">
        <Loader2 className="animate-spin text-slate-400" />
      </div>
    )
  }
  if (rows.length === 0) {
    return (
      <div className="rounded-2xl border border-slate-100 bg-white px-4 py-16 text-center text-sm text-slate-500">
        {empty}
      </div>
    )
  }

  return (
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
      {rows.map((r) => {
        const owed = Number(r.balance)
        const noFolio = r.hasFolio === false
        return (
          <article key={r.key} onClick={r.onClick}
            className={`relative flex flex-col overflow-hidden rounded-2xl border bg-white shadow-sm transition-colors ${
              r.onClick ? 'cursor-pointer' : ''} ${
              r.selected
                ? 'border-brand ring-1 ring-brand'
                : 'border-slate-100 hover:border-slate-200'}`}>

            {/* Status, as a colour down the edge. */}
            <span className={`absolute left-0 top-0 h-14 w-1.5 rounded-r ${stripe(r.statusKey)}`}
              aria-hidden="true" />

            {/* ------------------------------------------------- guest --- */}
            <div className="flex items-start gap-3 px-4 pt-4">
              <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-brand-light text-brand">
                <Building2 size={17} />
              </span>
              <div className="min-w-0 flex-1">
                <div className="truncate font-semibold text-ink"
                  title={r.guestName ?? ''}>
                  {r.guestName || <span className="italic font-normal text-slate-400">No guest on file</span>}
                </div>
                <div className="truncate font-mono text-xs text-slate-400"
                  title={r.number}>{r.number}</div>
              </div>
              <div className="flex shrink-0 items-center gap-1">
                {r.status}
                {showAction && r.action}
              </div>
            </div>

            {/* -------------------------------------------------- stay --- */}
            <div className="mx-4 mt-3 flex items-stretch overflow-hidden rounded-xl">
              <Edge date={r.arrival} time={r.arrivalTime} />
              <div className="grid shrink-0 place-items-center bg-brand px-3 py-2 text-center text-white">
                <div>
                  <div className="text-lg font-bold leading-none tabular-nums">{r.nights}</div>
                  <div className="text-[10px] font-medium uppercase tracking-wide opacity-90">
                    {r.nights === 1 ? 'Night' : 'Nights'}
                  </div>
                </div>
              </div>
              <Edge date={r.departure} time={r.departureTime} />
            </div>

            {/* ------------------------------------------------ detail --- */}
            <dl className="mt-3 space-y-2.5 px-4 text-sm">
              <div className="flex items-start justify-between gap-3">
                {/* Only where the screen carries it. The in-house list knows
                    who is in a room, not when the booking was taken, and a
                    labelled dash is worse than no label: it reads as data
                    somebody failed to fill in. */}
                <div className="min-w-0">
                  {r.bookedAt ? (
                    <>
                      <dt className="font-semibold text-slate-700">Booking Date</dt>
                      <dd className="text-slate-500">{fmtDate(r.bookedAt)}</dd>
                    </>
                  ) : (
                    <>
                      <dt className="font-semibold text-slate-700">Stay</dt>
                      <dd className="text-slate-500">
                        {r.nights} night{r.nights === 1 ? '' : 's'}
                      </dd>
                    </>
                  )}
                </div>
                {(r.adults != null || r.children != null) && (
                  <dd className="flex shrink-0 items-center gap-2.5 text-slate-500">
                    {r.adults != null && (
                      <span className="flex items-center gap-1" title={`${r.adults} adult(s)`}>
                        <User size={15} className="text-slate-400" />
                        <span className="tabular-nums">{r.adults}</span>
                      </span>
                    )}
                    {!!r.children && (
                      <span className="flex items-center gap-1" title={`${r.children} child(ren)`}>
                        <Baby size={15} className="text-slate-400" />
                        <span className="tabular-nums">{r.children}</span>
                      </span>
                    )}
                  </dd>
                )}
              </div>
              <div>
                <dt className="font-semibold text-slate-700">Room</dt>
                <dd className="text-slate-500">
                  {(r.roomCodes?.length ?? 0) > 0
                    ? r.roomCodes!.join(', ')
                    : <span className="text-slate-400">Not assigned</span>}
                  {r.plan && <span> / {r.plan}</span>}
                  <span className="text-slate-400"> · {r.roomType}</span>
                  {r.unitsLabel && <span className="text-slate-400"> {r.unitsLabel}</span>}
                  {r.roomExtra}
                </dd>
              </div>
            </dl>

            {/* ------------------------------------------------- money --- */}
            {/* mt-auto so every card in a row ends on the same line, whatever
                a long guest name did to the height above it. */}
            <dl className="mt-auto space-y-1 px-4 pb-4 pt-4 text-sm">
              <Line label="Total" value={noFolio ? null : r.total} />
              <Line label="Paid" value={noFolio ? null : r.paid} />
              <Line label="Balance" value={noFolio ? null : r.balance}
                due={!noFolio && owed > 0} />
              {noFolio && (
                // Not "0.00". Nothing posted yet and a folio that comes to
                // zero are different facts.
                <p className="pt-0.5 text-right text-xs text-slate-400">
                  No folio yet
                </p>
              )}
            </dl>
          </article>
        )
      })}
    </div>
  )
}

/** One end of the stay band. */
function Edge({ date, time }: { date: string; time?: string | null }) {
  return (
    <div className="flex-1 bg-slate-75 px-3 py-2 text-center">
      <div className="text-sm font-semibold tabular-nums text-ink">
        {fmtDate(date)}
      </div>
      {time && (
        <div className="text-xs tabular-nums text-slate-500">{time}</div>
      )}
    </div>
  )
}

function Line({ label, value, due }: {
  label: string; value: number | string | null; due?: boolean
}) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className={due ? 'font-medium text-red-600' : 'text-slate-500'}>
        {label}
      </dt>
      <dd className={`tabular-nums ${
        value === null ? 'text-slate-300'
          : due ? 'font-semibold text-red-600'
            : 'font-medium text-slate-700'}`}>
        {value === null ? '—' : `₹ ${money.format(Number(value))}`}
      </dd>
    </div>
  )
}
