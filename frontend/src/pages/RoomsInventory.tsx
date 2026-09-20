import { useState } from 'react'
import Select from '../components/Select'
import { FILTER_SELECT } from '../lib/controls'
import { StayLayoutToggle } from '../lib/listLayout'
import type React from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import {
  Bed, Users, DoorOpen, Wrench, Plus,
  Loader2, User as UserIcon, Wifi, Heart, Building2, X,
  Trash2, AlertTriangle, CheckSquare, Square,
} from 'lucide-react'
import { Broom } from '../components/icons'
import BlockDialog from '../components/BlockDialog'

/** Anything that renders like a lucide icon: our own SVGs qualify too. */
type IconType = React.ComponentType<{ className?: string }>
import {
  listRooms, getRoomFacets, 
  listAssignableUnits, assignRoomToUnit, endBlock, bulkDeleteRooms,
  type RoomRow, type RoomStats, type AssignableUnit,
  type BulkDeleteResult,
} from '../api'
import RoomTypeManagement from './RoomTypeManagement'
import AmenitiesManagement from './AmenitiesManagement'
import RoomBlocks from './RoomBlocks'
import RatePlans from './RatePlans'
import { useActivePropertyId } from '../hooks/useProperty'


const filterSelect = `${FILTER_SELECT} bg-white outline-none focus:border-brand`

const TABS = ['Room Inventory', 'Room Types', 'Amenities', 'Rate Plans', 'Blocks'] as const
type Tab = (typeof TABS)[number]

/** The badge on each card: derived operational state, not a stored column. */
function stateBadge(room: RoomRow): { label: string; cls: string } {
  // occupancy_state is derived server-side, with today's block taking priority.
  switch (room.occupancy_state) {
    case 'out_of_service': return { label: 'Out of Order', cls: 'bg-red-100 text-red-700' }
    case 'blocked':        return { label: 'Blocked', cls: 'bg-amber-100 text-amber-800' }
    case 'maintenance':    return { label: 'Maintenance', cls: 'bg-red-100 text-red-700' }
    case 'inactive':       return { label: 'Inactive', cls: 'bg-slate-100 text-slate-500' }
    case 'occupied':       return { label: 'Occupied', cls: 'bg-emerald-100 text-emerald-700' }
    case 'reserved':       return { label: 'Reserved', cls: 'bg-sky-100 text-sky-800' }
    default:               return { label: 'Available', cls: 'bg-emerald-100 text-emerald-700' }
  }
}

function housekeepingBanner(room: RoomRow) {
  if (room.block_group_id) {
    return {
      icon: Wrench, label: 'Blocked',
      detail: room.block_end ? `until ${room.block_end}` : 'Under repair',
      cls: 'bg-red-100 text-red-700',
    }
  }
  if (room.service_status !== 'in_service') {
    return { icon: Wrench, label: 'Maintenance', detail: 'Under repair', cls: 'bg-red-100 text-red-700' }
  }
  const map: Record<string, { label: string; cls: string }> = {
    dirty: { label: 'Dirty', cls: 'bg-amber-100 text-amber-800' },
    cleaning: { label: 'In Progress', cls: 'bg-amber-100 text-amber-800' },
    clean: { label: 'Clean', cls: 'bg-sky-100 text-sky-800' },
    inspected: { label: 'Inspected', cls: 'bg-sky-100 text-sky-800' },
  }
  const m = map[room.housekeeping_state] ?? map.clean
  return { icon: Broom, label: 'Housekeeping', detail: m.label, cls: m.cls }
}

/** Mockup writes floors as "1st Floor", not "Floor 1". */
function ordinalFloor(floor: string | null): string {
  if (!floor) return '—'
  const n = Number(floor)
  if (Number.isNaN(n)) return floor
  const suffix = n % 100 >= 11 && n % 100 <= 13
    ? 'th'
    : ['th', 'st', 'nd', 'rd'][n % 10] ?? 'th'
  return `${n}${suffix} Floor`
}

/** The 4th spec slot shows a headline feature, like the mockup's "Free Wi-Fi". */
const FEATURE_PRIORITY = ['pool', 'wifi', 'balcony', 'bathtub', 'minibar', 'ac']
function headlineFeature(room: RoomRow): string {
  for (const code of FEATURE_PRIORITY) {
    const hit = room.amenities.find((a) => a.code === code)
    if (hit) return hit.name
  }
  return room.amenities[0]?.name ?? '—'
}

function money(v: string | null): string {
  if (v === null) return '—'
  return `₹ ${Number(v).toLocaleString('en-IN')}`
}

function KpiCard({
  icon: Icon, label, value, sub, tint,
}: {
  icon: IconType; label: string; value: number | string; sub: string; tint: string
}) {
  return (
    <div className="flex items-center gap-4 rounded-xl border border-slate-200 bg-white p-4">
      <span className={`grid h-12 w-12 place-items-center rounded-full ${tint}`}>
        <Icon className="h-6 w-6" />
      </span>
      {/* min-w-0 + truncate, because a grid row is as tall as its tallest
          cell: "Housekeeping in progress" wrapped to two lines and added 20px
          to all five tiles, four of which had nothing to put there. The hint
          is a hint -- if it does not fit, the title attribute has it. */}
      <div className="min-w-0">
        <p className="truncate text-base font-semibold text-slate-800" title={label}>{label}</p>
        <p className="text-display leading-tight text-slate-800">{value}</p>
        <p className="truncate text-sm text-slate-500" title={sub}>{sub}</p>
      </div>
    </div>
  )
}

function KpiRow({ stats }: { stats: RoomStats }) {
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-5">
      <KpiCard icon={Bed} label="Total Units" value={stats.total_units}
        sub="Rooms & Villas" tint="bg-sky-100 text-sky-600" />
      <KpiCard icon={Users} label="Occupied" value={stats.occupied}
        sub={`${stats.occupancy_pct}% occupancy`} tint="bg-emerald-100 text-emerald-600" />
      <KpiCard icon={DoorOpen} label="Available" value={stats.available}
        sub={`${stats.available_pct}% available`} tint="bg-brand-light text-brand" />
      <KpiCard icon={Broom} label="Cleaning" value={stats.cleaning}
        sub="In progress" tint="bg-amber-100 text-amber-600" />
      <KpiCard icon={Wrench} label="Maintenance" value={stats.maintenance}
        sub="Out of service" tint="bg-red-100 text-red-500" />
    </div>
  )
}

/** One spec cell: icon above label, as in the mockup's 4-up strip. */
function Spec({ icon: Icon, label }: { icon: IconType; label: string }) {
  return (
    <div className="flex flex-col items-center gap-1">
      <Icon className="h-5 w-5 text-slate-500" />
      {/* 13px keeps "Free Wi-Fi" and "1 Queen Bed" on one line in a 4-up grid. */}
      <span className="text-center text-[13px] leading-tight text-slate-600">{label}</span>
    </div>
  )
}

function RoomCard({
  room, onView, onAssign, onBlock, onHistory, blocking, selected, onSelect,
}: {
  room: RoomRow
  onView: () => void
  onAssign: () => void
  onBlock: () => void
  onHistory: () => void
  blocking: boolean
  selected: boolean
  onSelect: (checked: boolean) => void
}) {
  const badge = stateBadge(room)
  const hk = housekeepingBanner(room)
  const HkIcon = hk.icon
  const blocked = room.block_group_id !== null
  // A blocked or inactive room cannot take a guest, so Assign is not offered.
  const assignable =
    !blocked && room.status === 'active' && room.service_status === 'in_service'
  const assignBlockedReason = blocked
    ? `Room ${room.code} is blocked${room.block_end ? ` until ${room.block_end}` : ''}. Release the block first.`
    : room.status !== 'active'
      ? `Room ${room.code} is not active.`
      : `Room ${room.code} is out of service.`
  const [favourite, setFavourite] = useState(false)

  return (
    <div className="flex flex-col overflow-hidden rounded-xl border border-slate-200 bg-white">
      <div className={`relative grid h-40 place-items-center bg-gradient-to-br from-sky-200 via-brand-light to-emerald-100 ${
        selected ? 'ring-2 ring-inset ring-brand' : ''}`}>
        {/* Selecting for a bulk action. Sits opposite the favourite so the
            two never fight for the same corner. */}
        <label className="absolute left-3 top-3 z-10 grid h-8 w-8 cursor-pointer place-items-center rounded-full bg-white/90 shadow-sm"
          title={`Select room ${room.code}`}>
          <input type="checkbox" checked={selected}
            onChange={(e) => onSelect(e.target.checked)}
            className="h-4 w-4 cursor-pointer accent-teal-700" />
        </label>
        {room.primary_photo_url ? (
          <img src={room.primary_photo_url} alt={room.code}
            className="h-full w-full object-cover" />
        ) : (
          <Bed className="h-10 w-10 text-sky-300" />
        )}
        <button
          onClick={() => setFavourite((v) => !v)}
          aria-label={favourite ? 'Remove from favourites' : 'Add to favourites'}
          className="absolute right-3 top-3 grid h-8 w-8 place-items-center rounded-full bg-white/90 shadow-sm">
          <Heart className={favourite ? 'h-4 w-4 fill-red-500 text-red-500' : 'h-4 w-4 text-slate-400'} />
        </button>
      </div>

      <div className="flex flex-1 flex-col gap-3 p-4">
        <div className="flex items-start justify-between">
          <div>
            <p className="text-xl font-bold text-slate-800">{room.code}</p>
            <p className="text-base text-slate-600">{room.room_type_name}</p>
          </div>
          <span className={`rounded-full px-3 py-1 text-xs font-semibold ${badge.cls}`}>
            {badge.label}
          </span>
        </div>

        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 text-sm">
            <UserIcon className="h-4 w-4 text-slate-400" />
            <div>
              <p className="font-semibold text-slate-800">
                {room.guest_name ?? (room.arrival_date ? 'Reserved' : 'No guest assigned')}
              </p>
              <p className="text-sm text-slate-500">
                {blocked
                  ? 'Out of service'
                  : room.occupancy_state === 'occupied' && room.departure_date
                    ? `Check-out: ${room.departure_date}`
                    : room.occupancy_state === 'reserved'
                      ? `Due out ${room.departure_date}`
                      : room.arrival_date
                        // Assigned, but the stay has not started yet.
                        ? `Arrives ${room.arrival_date}`
                        : 'Ready for check-in'}
              </p>
            </div>
          </div>
          <div className="text-right">
            <p className="text-lg font-bold text-slate-800">{money(room.base_rate)}</p>
            <p className="text-sm text-slate-500">per night</p>
          </div>
        </div>

        <div className="grid grid-cols-4 gap-1 border-y border-slate-100 py-3">
          <Spec icon={Building2} label={ordinalFloor(room.floor)} />
          <Spec icon={Bed} label={room.bed_setup ?? '—'} />
          <Spec icon={Users} label={`${room.max_adults + room.max_children} Guests`} />
          <Spec icon={Wifi} label={headlineFeature(room)} />
        </div>

        <div className={`flex items-center gap-3 rounded-lg px-3 py-2 text-sm ${hk.cls}`}>
          <HkIcon className="h-5 w-5 shrink-0" />
          <div className="leading-tight">
            <p className="font-semibold">{hk.label}</p>
            <p>{hk.detail}</p>
          </div>
        </div>

        <div className="mt-auto grid grid-cols-4 gap-2">
          <button onClick={onView}
            className="rounded-lg border border-slate-200 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50">
            View
          </button>
          <button onClick={onAssign} disabled={!assignable}
            title={assignable ? undefined : assignBlockedReason}
            className="rounded-lg border border-brand py-2 text-sm font-semibold text-brand hover:bg-brand-light disabled:cursor-not-allowed disabled:border-slate-200 disabled:text-slate-400 disabled:hover:bg-transparent">
            Assign
          </button>
          <button onClick={onBlock} disabled={blocking}
            className="rounded-lg border border-slate-200 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-50">
            {blocking ? '…' : blocked ? 'Unblock' : 'Block'}
          </button>
          <button onClick={onHistory} title="Room status history"
            className="rounded-lg border border-slate-200 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50">
            History
          </button>
        </div>
      </div>
    </div>
  )
}

/** Assign this room to an unassigned reservation unit of the same room type. */
function AssignDialog({
  propertyId, room, onClose,
}: {
  propertyId: string; room: RoomRow; onClose: () => void
}) {
  const qc = useQueryClient()
  const [error, setError] = useState<string | null>(null)

  const { data, isLoading } = useQuery({
    queryKey: ['assignable', propertyId, room.id],
    queryFn: () => listAssignableUnits(propertyId, room.id),
  })

  const assign = useMutation({
    mutationFn: (u: AssignableUnit) => assignRoomToUnit(u.reservation_unit_id, room.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['rooms', propertyId] })
      onClose()
    },
    // A room-calendar overlap comes back as 409 from the GiST exclusion
    // constraint. The service names the room by id; show the room number.
    onError: (e: unknown) => {
      const d = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setError(
        d ? d.split(room.id).join(room.code) : 'Could not assign this room.',
      )
    },
  })

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-slate-900/30 p-4">
      <div className="w-full max-w-xl rounded-xl bg-white shadow-lg">
        <div className="flex items-start justify-between border-b border-slate-200 px-6 py-4">
          <div>
            <h2 className="text-lg font-semibold text-ink">Assign Room {room.code}</h2>
            <p className="text-sm text-slate-500">
              Unassigned {room.room_type_name} reservations
            </p>
          </div>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600">
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="max-h-80 overflow-y-auto px-6 py-2">
          {isLoading ? (
            <div className="flex justify-center py-10 text-slate-400">
              <Loader2 className="h-5 w-5 animate-spin" />
            </div>
          ) : (data ?? []).length === 0 ? (
            <p className="py-10 text-center text-sm text-slate-500">
              No unassigned reservations for this room type.
            </p>
          ) : (
            <ul className="divide-y divide-slate-100">
              {data!.map((u) => (
                <li key={u.reservation_unit_id} className="flex items-center gap-4 py-3">
                  <div className="flex-1">
                    <p className="text-sm font-medium text-slate-800">
                      {u.guest_name ?? u.reservation_number ?? 'Reservation'}
                    </p>
                    <p className="text-xs text-slate-500">
                      {u.reservation_number} · {u.arrival_date} → {u.departure_date} ·{' '}
                      {u.adults}A/{u.children}C
                    </p>
                  </div>
                  <button onClick={() => { setError(null); assign.mutate(u) }}
                    disabled={assign.isPending}
                    className="rounded-lg bg-brand px-3 py-1.5 text-sm font-medium text-white hover:bg-brand/90 disabled:opacity-50">
                    Assign
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        {error && (
          <p className="mx-6 mb-4 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>
        )}
      </div>
    </div>
  )
}


/** Blocking a room needs dates and a reason, so the card opens this rather
 *  than silently flipping a status flag that never stopped a booking. */
function RoomInventoryTab({ propertyId }: { propertyId: string }) {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [floor, setFloor] = useState('')
  const [roomTypeId, setRoomTypeId] = useState('')
  const [state, setState] = useState('')
  const [view, setView] = useState<'grid' | 'list'>('grid')
  const [error, setError] = useState<string | null>(null)
  const [assigning, setAssigning] = useState<RoomRow | null>(null)

  const filters = {
    floor: floor || undefined,
    room_type_id: roomTypeId || undefined,
    state: state || undefined,
  }
  const roomsQ = useQuery({
    queryKey: ['rooms', propertyId, filters],
    queryFn: () => listRooms(propertyId, filters),
    enabled: propertyId !== '',
  })
  const facetsQ = useQuery({
    queryKey: ['roomFacets', propertyId],
    queryFn: () => getRoomFacets(propertyId),
    enabled: propertyId !== '',
  })

  const [blocking, setBlocking] = useState<RoomRow | null>(null)

  // Bulk delete. `report` holds the per-room outcome of the last run, because
  // a partial result is the normal one and needs saying rather than a count.
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [confirmBulk, setConfirmBulk] = useState(false)
  const [report, setReport] = useState<BulkDeleteResult | null>(null)
  const toggle = (id: string, on: boolean) => setSelected((prev) => {
    const next = new Set(prev)
    if (on) next.add(id); else next.delete(id)
    return next
  })
  const bulkDelete = useMutation({
    mutationFn: () =>
      bulkDeleteRooms(propertyId, [...selected], 'Deleted from Rooms & Villas'),
    onSuccess: (res) => {
      setError(null)
      setConfirmBulk(false)
      setReport(res)
      // Anything refused stays selected, so the next step is obvious.
      setSelected(new Set(res.results.filter((r) => !r.deleted).map((r) => r.id)))
      qc.invalidateQueries({ queryKey: ['rooms', propertyId] })
      qc.invalidateQueries({ queryKey: ['roomFacets', propertyId] })
    },
    onError: (e: unknown) => {
      setConfirmBulk(false)
      const detail = (e as { response?: { data?: { detail?: string } } })
        ?.response?.data?.detail
      setError(detail ?? 'Could not delete the selected rooms.')
    },
  })

  // Unblocking is unambiguous — end today's block now. Blocking needs dates
  // and a reason, so it opens the dialog rather than guessing.
  const unblockM = useMutation({
    mutationFn: (room: RoomRow) =>
      endBlock(propertyId, room.block_group_id!, {
        end_date: new Date().toISOString().slice(0, 10),
        reason: 'Released from Rooms & Villas',
      }),
    onSuccess: () => {
      setError(null)
      qc.invalidateQueries({ queryKey: ['rooms', propertyId] })
      qc.invalidateQueries({ queryKey: ['roomBlocks', propertyId] })
      qc.invalidateQueries({ queryKey: ['blockStats', propertyId] })
    },
    onError: (e: unknown) => {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setError(detail ?? 'Could not release the room.')
    },
  })

  const clearFilters = () => { setFloor(''); setRoomTypeId(''); setState('') }
  const rooms = roomsQ.data?.items ?? []
  const filtered = floor !== '' || roomTypeId !== '' || state !== ''

  return (
    <div className="space-y-4">
      {roomsQ.data && <KpiRow stats={roomsQ.data.stats} />}

      {/* One row, no captions: each select's first option says what it
          filters. The layout toggle sits at the right end, as on
          Reservations. */}
      <div className="flex flex-wrap items-center gap-2">
        <Select blankIsChoice value={floor} onChange={(e) => setFloor(e.target.value)}
          aria-label="Floor" className={filterSelect}>
          <option value="">All Floors</option>
          {facetsQ.data?.floors.map((f) => <option key={f} value={f}>Floor {f}</option>)}
        </Select>
        <Select blankIsChoice value={roomTypeId} onChange={(e) => setRoomTypeId(e.target.value)}
          aria-label="Room type" className={filterSelect}>
          <option value="">All Room Types</option>
          {facetsQ.data?.room_types.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
        </Select>
        <Select blankIsChoice value={state} onChange={(e) => setState(e.target.value)}
          aria-label="Status" className={filterSelect}>
          <option value="">All Statuses</option>
          <option value="available">Available</option>
          <option value="occupied">Occupied</option>
          <option value="cleaning">Cleaning</option>
          <option value="maintenance">Maintenance</option>
          <option value="out_of_service">Blocked</option>
        </Select>
        <button onClick={clearFilters}
          className="flex items-center gap-1.5 whitespace-nowrap rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-600 hover:bg-slate-50">
          Clear Filters
        </button>
        <span className="ml-auto flex items-center gap-2">
          {filtered && roomsQ.data && (
            <span className="whitespace-nowrap text-sm text-slate-500">
              {rooms.length} {rooms.length === 1 ? 'room' : 'rooms'}
            </span>
          )}
          <StayLayoutToggle layout={view === 'grid' ? 'cards' : 'list'}
            onChange={(v) => setView(v === 'cards' ? 'grid' : 'list')} />
        </span>
      </div>

      {/* Selection bar. Present only once something is selected, so the
          screen is unchanged for everyone not deleting rooms. */}
      {selected.size > 0 && (
        <div className="flex flex-wrap items-center gap-3 rounded-lg border border-brand/30 bg-brand-light/40 px-4 py-3">
          <span className="text-sm font-semibold text-slate-700">
            {selected.size} room{selected.size === 1 ? '' : 's'} selected
          </span>
          <button onClick={() => setSelected(new Set(rooms.map((r) => r.id)))}
            className="inline-flex items-center gap-1.5 text-sm text-slate-600 hover:text-brand">
            <CheckSquare className="h-4 w-4" /> Select all {rooms.length} shown
          </button>
          <button onClick={() => setSelected(new Set())}
            className="inline-flex items-center gap-1.5 text-sm text-slate-600 hover:text-brand">
            <Square className="h-4 w-4" /> Clear
          </button>
          <div className="ml-auto flex items-center gap-2">
            {!confirmBulk ? (
              <button onClick={() => { setError(null); setReport(null); setConfirmBulk(true) }}
                className="inline-flex items-center gap-2 rounded-lg border border-red-300 px-4 py-2 text-sm font-semibold text-red-600 hover:bg-red-50">
                <Trash2 className="h-4 w-4" /> Delete selected
              </button>
            ) : (
              <span className="inline-flex items-center gap-2 rounded-lg border border-red-300 bg-red-50 px-3 py-2 text-sm">
                <AlertTriangle className="h-4 w-4 shrink-0 text-red-600" />
                <span className="text-red-700">
                  Delete {selected.size} room{selected.size === 1 ? '' : 's'} for
                  good? Rooms with bookings or tasks will be kept.
                </span>
                <button onClick={() => bulkDelete.mutate()}
                  disabled={bulkDelete.isPending}
                  className="inline-flex items-center gap-1.5 rounded-md bg-red-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-red-700 disabled:opacity-50">
                  {bulkDelete.isPending && <Loader2 className="h-3 w-3 animate-spin" />}
                  Delete
                </button>
                <button onClick={() => setConfirmBulk(false)}
                  className="rounded-md border border-slate-200 bg-white px-3 py-1.5 text-xs font-semibold text-slate-600 hover:bg-slate-50">
                  Cancel
                </button>
              </span>
            )}
          </div>
        </div>
      )}

      {/* What actually happened. A bulk delete that kept some rooms has to
          say which and why, or the count alone reads as a failure. */}
      {report && (
        <div className={`rounded-lg border px-4 py-3 text-sm ${
          report.refused ? 'border-amber-200 bg-amber-50' : 'border-emerald-200 bg-emerald-50'}`}>
          <div className="flex items-start gap-2">
            <span className={report.refused ? 'text-amber-700' : 'text-emerald-700'}>
              {report.deleted} room{report.deleted === 1 ? '' : 's'} deleted
              {report.refused > 0 && `, ${report.refused} kept`}.
            </span>
            <button onClick={() => setReport(null)}
              className="ml-auto text-slate-400 hover:text-slate-600">
              <X className="h-4 w-4" />
            </button>
          </div>
          {report.results.filter((r) => !r.deleted).length > 0 && (
            <ul className="mt-2 space-y-1 text-xs text-caution">
              {report.results.filter((r) => !r.deleted).map((r) => (
                <li key={r.id}><b>{r.code}</b> — {r.reason}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-2 text-sm text-red-700">
          {error}
        </div>
      )}

      {roomsQ.isLoading ? (
        <div className="flex justify-center py-16 text-slate-400">
          <Loader2 className="h-6 w-6 animate-spin" />
        </div>
      ) : roomsQ.isError ? (
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-6 text-center text-sm text-red-700">
          Could not load rooms. You may not have permission to view this module.
        </div>
      ) : rooms.length === 0 ? (
        <div className="rounded-lg border border-slate-200 bg-white px-4 py-16 text-center text-slate-500">
          No rooms match these filters.
        </div>
      ) : view === 'grid' ? (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
          {rooms.map((room) => (
            <RoomCard key={room.id} room={room}
              onView={() => navigate(`/rooms/${room.id}`)}
              onHistory={() => navigate(`/rooms/${room.id}/history`)}
              onAssign={() => setAssigning(room)}
              onBlock={() =>
                room.block_group_id ? unblockM.mutate(room) : setBlocking(room)}
              blocking={unblockM.isPending && unblockM.variables?.id === room.id}
              selected={selected.has(room.id)}
              onSelect={(on) => toggle(room.id, on)} />
          ))}
        </div>
      ) : (
        <div className="overflow-hidden rounded-xl border border-slate-200 bg-white">
          <table className="w-full text-sm">
            <thead className="border-b border-slate-100 bg-slate-50/60 text-left text-sm text-slate-600">
              <tr>
                <th className="w-10 px-4 py-3">
                  <input type="checkbox"
                    checked={rooms.length > 0 && selected.size === rooms.length}
                    onChange={(e) => setSelected(
                      e.target.checked ? new Set(rooms.map((r) => r.id)) : new Set())}
                    className="h-4 w-4 cursor-pointer accent-teal-700"
                    title="Select every room shown" />
                </th>
                {['Room', 'Type', 'Floor', 'Bed', 'Occupancy', 'Rate', 'State', 'Housekeeping', ''].map((h) => (
                  <th key={h} className="px-4 py-3 font-semibold">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {rooms.map((room) => {
                const badge = stateBadge(room)
                return (
                  <tr key={room.id} className={selected.has(room.id)
                    ? 'bg-brand-light/30' : 'hover:bg-slate-50'}>
                    <td className="px-4 py-3">
                      <input type="checkbox" checked={selected.has(room.id)}
                        onChange={(e) => toggle(room.id, e.target.checked)}
                        className="h-4 w-4 cursor-pointer accent-teal-700" />
                    </td>
                    <td className="px-4 py-3 font-medium text-slate-800">{room.code}</td>
                    <td className="px-4 py-3 text-slate-600">{room.room_type_name}</td>
                    <td className="px-4 py-3 text-slate-600">{room.floor ?? '—'}</td>
                    <td className="px-4 py-3 text-slate-600">{room.bed_setup ?? '—'}</td>
                    <td className="px-4 py-3 text-slate-600">
                      {room.max_adults}A / {room.max_children}C
                    </td>
                    <td className="px-4 py-3 text-slate-600">{money(room.base_rate)}</td>
                    <td className="px-4 py-3">
                      <span className={`rounded-full px-3 py-1 text-xs font-semibold ${badge.cls}`}>
                        {badge.label}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-slate-600">{room.housekeeping_state}</td>
                    <td className="px-4 py-3 text-right">
                      <button onClick={() => navigate(`/rooms/${room.id}/edit`)}
                        className="text-brand hover:underline">Edit</button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {assigning && (
        <AssignDialog propertyId={propertyId} room={assigning}
          onClose={() => setAssigning(null)} />
      )}

      {blocking && (
        <BlockDialog propertyId={propertyId} roomId={blocking.id}
          roomCode={blocking.code}
          onClose={() => setBlocking(null)} />
      )}
    </div>
  )
}

export default function RoomsInventory() {
  const navigate = useNavigate()
  const propertyId = useActivePropertyId()
  const [tab, setTab] = useState<Tab>('Room Inventory')

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <h1 className="flex items-center gap-2 text-display text-ink">
          <Building2 size={26} className="text-brand" /> Rooms &amp; Villas
        </h1>
        <div className="flex flex-wrap items-center gap-2">
          <button onClick={() => navigate('/rooms/new')}
            className="flex items-center gap-1.5 whitespace-nowrap rounded-lg bg-brand px-3 py-2 text-sm font-medium text-white hover:bg-brand-dark disabled:opacity-40">
            <Plus size={15} /> Add Room
          </button>
        </div>
      </div>

      <div className="flex gap-6 border-b border-slate-200">
        {TABS.map((t) => (
          <button key={t} onClick={() => setTab(t)}
            className={`-mb-px border-b-2 px-1 pb-3 text-sm ${
              tab === t
                ? 'border-brand font-semibold text-brand'
                : 'border-transparent font-normal text-slate-500 hover:text-slate-700'
            }`}>
            {t}
          </button>
        ))}
      </div>

      {tab === 'Room Inventory' && <RoomInventoryTab propertyId={propertyId} />}
      {tab === 'Room Types' && <RoomTypeManagement propertyId={propertyId} />}
      {tab === 'Amenities' && <AmenitiesManagement propertyId={propertyId} />}
      {tab === 'Rate Plans' && <RatePlans propertyId={propertyId} showHeading={false} />}
      {tab === 'Blocks' && <RoomBlocks propertyId={propertyId} />}
    </div>
  )
}
