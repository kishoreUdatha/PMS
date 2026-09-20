/**
 * Who is in the building right now.
 *
 * Arrivals answers "who is coming", Departures "who is going". Neither
 * answers the question a desk is actually asked all day — by housekeeping, by
 * a caller asking for a guest, by a manager walking past. This is every unit
 * in `checked_in`, whatever its dates, so an overstay and a guest who arrived
 * three days ago both appear where somebody would look for them.
 *
 * The action offered is check-out, because that is the only thing left to do
 * to a guest already in a room. Offering check-in here is what produced
 * "this guest is already checked in" on a screen that could do nothing about
 * it.
 */
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { StayLayoutToggle, StayView, useStayLayout } from '../lib/listLayout'
import RowActions from '../components/RowActions'
import { Search } from 'lucide-react'
import { listInHouse, type DepartureRow } from '../api'
import { useActivePropertyId } from '../hooks/useProperty'


/** Amber once the guest is due out today, red once they have overstayed. */
function dueTone(label: string): string {
  if (/Overdue/i.test(label)) return 'bg-red-50 text-red-600'
  if (/Today/i.test(label)) return 'bg-amber-50 text-amber-700'
  return 'bg-slate-75 text-slate-500'
}

export default function InHouse() {
  const [layout, chooseLayout] = useStayLayout()
  // The row, the card and the "⋮" all open the same panel. Without this the
  // three front-desk tabs answered a click on the row with nothing, while the
  // Reservations tab opened the actions -- the same list, reached by a
  // different tab, behaving differently.
  const [actionsFor, setActionsFor] = useState('')
  const propertyId = useActivePropertyId()
  const [search, setSearch] = useState('')

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['in-house', propertyId, search],
    queryFn: () => listInHouse(propertyId, { search: search.trim() || undefined }),
    enabled: propertyId !== '',
  })
  const rows: DepartureRow[] = data ?? []

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <p className="text-sm text-slate-500">
          Guests currently in a room, whatever their dates.
        </p>
        <div className="relative ml-auto min-w-[16rem] flex-1 sm:flex-none">
          <Search size={15}
            className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
          <input value={search} onChange={(e) => setSearch(e.target.value)}
            placeholder="Search by room, number or guest…"
            className="w-full rounded-lg border border-slate-200 py-2 pl-9 pr-3 text-sm outline-none focus:border-brand" />
        </div>
        <span className="text-sm text-slate-500">
          {rows.length} in house
        </span>
        <StayLayoutToggle layout={layout} onChange={chooseLayout} />
      </div>

      {isError && (
        <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-6 text-center text-sm text-red-700">
          Could not load who is in house. You may not have permission to view
          the front desk.
        </div>
      )}

      <StayView
        layout={layout}
        loading={isLoading}
        empty={search
          ? `Nobody in house matches “${search}”.`
          : 'Nobody is checked in right now.'}
        rows={rows.map((r) => ({
          key: r.reservation_unit_id,
          onClick: () => setActionsFor(r.reservation_unit_id),
          selected: actionsFor === r.reservation_unit_id,
          number: r.number,
          guestName: r.guest_name,
          roomType: r.room_type,
          roomCodes: r.room ? [r.room] : [],
          plan: r.plan,
          arrival: r.arrival_date,
          departure: r.departure_date,
          nights: r.nights,
          hasFolio: r.has_folio,
          total: r.total, paid: r.paid, balance: r.balance,
          statusKey: r.unit_status,
          adults: r.adults,
          children: r.children,
          status: (
            <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${dueTone(r.due_label)}`}>
              {r.due_label}
            </span>
          ),
          action: (
            <RowActions
              open={actionsFor === r.reservation_unit_id}
              onOpenChange={(v) => setActionsFor(v ? r.reservation_unit_id : '')}
              ctx={{
              propertyId,
              organizationId: r.organization_id,
              reservationId: r.reservation_id,
              reservationUnitId: r.reservation_unit_id,
              number: r.number,
              folioId: r.folio_id,
              hasFolio: r.has_folio,
              roomId: r.room_id,
              room: r.room,
              state: r.unit_status,
              guestName: r.guest_name,
              roomType: r.room_type,
              departureDate: r.departure_date,
              nights: r.nights,
              adults: r.adults,
              children: r.children,
              total: Number(r.total),
              paid: Number(r.paid),
              statusLabel: r.due_label,
              balance: Number(r.balance),
              arrivalDate: r.arrival_date,
              businessDate: r.departure_date,
              onDone: () => void refetch(),
              show: { checkIn: false, assignRoom: false, noShow: false, deposits: false, adjustFolio: false },
            }} />
          ),
        }))}
      />

    </div>
  )
}
