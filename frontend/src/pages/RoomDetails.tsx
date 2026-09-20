import { useMemo, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useParams } from 'react-router-dom'
import {
  ArrowLeft, ArrowRight, Ban, Bed, CalendarDays, CheckCircle2, ChevronLeft,
  ChevronRight, Ellipsis, Eye, FileText, IndianRupee, Loader2, Lock, Maximize2,
  Pencil, Tag, Users, Wrench, X, AlertCircle, ImageOff,
} from 'lucide-react'
import { Broom } from '../components/icons'
import BlockDialog from '../components/BlockDialog'
import { fmtDate, fmtDateLong, fmtTime } from '../lib/dates'
import {
  getRoomOverview, getRoomReservations, getRoomMaintenance,
  getRoomStatusHistory, endBlock, 
  type RoomOverview, type RoomPhoto, type StatusEvent,
} from '../api'
import { useActivePropertyId } from '../hooks/useProperty'

/**
 * Screen 011 — Room Details.
 *
 * The read-only view of a room, and the hub the other room screens hang off:
 * Edit Room is 059, Block Room and Mark Out of Order are 063, and the Activity
 * Log tab is the 064 timeline. Nothing is re-derived here — each tab reads the
 * records that already own that information, so this screen cannot disagree
 * with the screen the data came from.
 */


const TABS = ['Overview', 'Reservations', 'Housekeeping', 'Maintenance',
  'Charges', 'Activity Log'] as const
type Tab = typeof TABS[number]

/** Anything that renders like a lucide icon; our own SVGs qualify too. */
type IconType = React.ComponentType<{ className?: string }>

const HOUSEKEEPING_STATUSES = ['clean', 'dirty', 'cleaning', 'inspected']

const BLOCK_STATUS_LABELS: Record<string, string> = {
  active: 'Active', ended: 'Ended', cancelled: 'Cancelled',
}

const BADGE: Record<string, string> = {
  occupied: 'bg-sky-100 text-sky-700',
  reserved: 'bg-indigo-100 text-indigo-700',
  available: 'bg-emerald-100 text-emerald-700',
  clean: 'bg-emerald-100 text-emerald-700',
  inspected: 'bg-violet-100 text-violet-700',
  dirty: 'bg-amber-100 text-amber-800',
  cleaning: 'bg-blue-100 text-blue-700',
  out_of_service: 'bg-red-100 text-red-700',
  out_of_order: 'bg-red-100 text-red-700',
  blocked: 'bg-orange-100 text-orange-700',
  maintenance: 'bg-amber-100 text-amber-800',
  inactive: 'bg-slate-200 text-slate-600',
  reserved_unit: 'bg-indigo-100 text-indigo-700',
  checked_in: 'bg-sky-100 text-sky-700',
  checked_out: 'bg-slate-200 text-slate-600',
  cancelled: 'bg-slate-200 text-slate-500',
  no_show: 'bg-red-100 text-red-700',
  active: 'bg-emerald-100 text-emerald-700',
  ended: 'bg-slate-200 text-slate-600',
}

// The two chips must read the room's real state: a blocked room showing a
// green tick would be actively misleading.
const STATUS_CHIP: Record<string, { tint: string; icon: IconType }> = {
  available: { tint: 'border-emerald-100 bg-emerald-50/70 text-emerald-500', icon: CheckCircle2 },
  clean: { tint: 'border-emerald-100 bg-emerald-50/70 text-emerald-500', icon: CheckCircle2 },
  inspected: { tint: 'border-violet-100 bg-violet-50/70 text-violet-500', icon: CheckCircle2 },
  occupied: { tint: 'border-sky-100 bg-sky-50/70 text-sky-500', icon: Users },
  reserved: { tint: 'border-indigo-100 bg-indigo-50/70 text-indigo-500', icon: CalendarDays },
  cleaning: { tint: 'border-blue-100 bg-blue-50/70 text-blue-500', icon: Broom },
  dirty: { tint: 'border-amber-100 bg-amber-50/70 text-amber-500', icon: AlertCircle },
  maintenance: { tint: 'border-amber-100 bg-amber-50/70 text-amber-500', icon: Wrench },
  blocked: { tint: 'border-orange-100 bg-orange-50/70 text-orange-500', icon: Lock },
  out_of_service: { tint: 'border-red-100 bg-red-50/70 text-red-500', icon: Ban },
  inactive: { tint: 'border-slate-200 bg-slate-50 text-slate-400', icon: Ban },
}
const FALLBACK_CHIP = {
  tint: 'border-slate-200 bg-slate-50 text-slate-400', icon: CheckCircle2,
}


/** "05 Sep 2026", or "Thu, 05 Sep 2026" with the weekday. */
function day(iso: string | null, weekday = false): string {
  if (!iso) return '—'
  return weekday ? fmtDateLong(iso) : fmtDate(iso)
}

function stamp(iso: string | null): string {
  if (!iso) return '—'
  return `${day(iso)} ${fmtTime(iso)}`
}

function money(v: string | null): string {
  if (v === null) return '—'
  return `₹${Number(v).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`
}

function apiError(e: unknown): string {
  const d = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  return typeof d === 'string' ? d : 'Something went wrong. Please try again.'
}

function Badge({ tone, children }: { tone: string; children: React.ReactNode }) {
  return (
    <span className={`inline-block whitespace-nowrap rounded-full px-3 py-1 text-xs font-semibold ${
      BADGE[tone] ?? 'bg-slate-75 text-slate-600'}`}>
      {children}
    </span>
  )
}

function Card({ title, children, action }: {
  title?: React.ReactNode; children: React.ReactNode; action?: React.ReactNode
}) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white">
      {title && (
        <div className="flex items-center justify-between gap-3 border-b border-slate-100 px-5 py-3">
          <h3 className="font-bold text-ink">{title}</h3>
          {action}
        </div>
      )}
      <div className="px-5 py-4">{children}</div>
    </div>
  )
}

function Spec({ icon: Icon, label, value }: {
  icon: React.ComponentType<{ className?: string }>; label: string; value: React.ReactNode
}) {
  return (
    <div className="flex items-start gap-3">
      <Icon className="mt-0.5 h-5 w-5 shrink-0 text-slate-400" />
      <div className="min-w-0">
        <p className="text-sm text-slate-500">{label}</p>
        <p className="font-medium text-slate-800">{value}</p>
      </div>
    </div>
  )
}

function StatusChip({ label, state, value }: {
  label: string; state: string; value: string
}) {
  const { tint, icon: Icon } = STATUS_CHIP[state] ?? FALLBACK_CHIP
  return (
    <div className={`rounded-xl border px-4 py-3 ${tint}`}>
      <div className="flex items-center gap-2">
        <Icon className="h-5 w-5 shrink-0" />
        <p className="text-sm text-slate-500">{label}</p>
      </div>
      <p className="mt-1 font-bold text-slate-800">{value}</p>
    </div>
  )
}

function Empty({ children }: { children: React.ReactNode }) {
  return <p className="px-1 py-8 text-center text-sm text-slate-500">{children}</p>
}

/* ------------------------------------------------------------------ gallery --- */
function Gallery({ photos, code }: { photos: RoomPhoto[]; code: string }) {
  const [index, setIndex] = useState(0)
  const [lightbox, setLightbox] = useState(false)

  if (photos.length === 0) {
    return (
      <div className="grid h-72 place-items-center rounded-xl border border-slate-200 bg-slate-50 text-slate-400">
        <div className="text-center">
          <ImageOff className="mx-auto h-8 w-8" />
          <p className="mt-2 text-sm">No photos for this room yet.</p>
        </div>
      </div>
    )
  }

  const main = photos[index] ?? photos[0]
  // The mockup shows four thumbnails beside the hero; anything beyond that is
  // rolled into a "+N more" badge on the last one.
  const thumbs = photos.filter((_, i) => i !== index).slice(0, 4)
  const overflow = photos.length - 1 - thumbs.length
  const step = (d: number) => setIndex((i) => (i + d + photos.length) % photos.length)

  return (
    <>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-[minmax(0,1.7fr)_minmax(0,1fr)]">
        <div className="group relative overflow-hidden rounded-xl bg-slate-75">
          <img src={main.url} alt={main.caption ?? `Room ${code}`}
            className="h-72 w-full cursor-zoom-in object-cover"
            onClick={() => setLightbox(true)} />
          <span className="absolute bottom-3 left-3 rounded-md bg-slate-900/60 px-2 py-1 text-xs font-medium text-white">
            {index + 1} / {photos.length}
          </span>
          <button onClick={() => setLightbox(true)} aria-label="Expand photo"
            className="absolute right-3 top-3 grid h-8 w-8 place-items-center rounded-full bg-white/85 text-slate-600 opacity-0 transition group-hover:opacity-100">
            <Maximize2 className="h-4 w-4" />
          </button>
          {photos.length > 1 && (
            <>
              <button onClick={() => step(-1)} aria-label="Previous photo"
                className="absolute left-2 top-1/2 grid h-8 w-8 -translate-y-1/2 place-items-center rounded-full bg-white/85 text-slate-600 opacity-0 transition group-hover:opacity-100">
                <ChevronLeft className="h-5 w-5" />
              </button>
              <button onClick={() => step(1)} aria-label="Next photo"
                className="absolute right-2 top-1/2 grid h-8 w-8 -translate-y-1/2 place-items-center rounded-full bg-white/85 text-slate-600 opacity-0 transition group-hover:opacity-100">
                <ChevronRight className="h-5 w-5" />
              </button>
            </>
          )}
        </div>
        <div className="grid grid-cols-2 gap-3">
          {thumbs.map((p, i) => {
            const last = i === thumbs.length - 1 && overflow > 0
            return (
              <button key={p.id} onClick={() => setIndex(photos.indexOf(p))}
                className="relative h-[8.25rem] overflow-hidden rounded-xl bg-slate-75">
                <img src={p.url} alt={p.caption ?? `Room ${code}`}
                  className="h-full w-full object-cover" />
                {last && (
                  <span className="absolute inset-0 grid place-items-center bg-slate-900/50 text-sm font-semibold text-white">
                    +{overflow} more
                  </span>
                )}
              </button>
            )
          })}
        </div>
      </div>

      {lightbox && (
        <div className="fixed inset-0 z-50 grid place-items-center bg-slate-900/80 p-6"
          onClick={() => setLightbox(false)}>
          <img src={main.url} alt={main.caption ?? `Room ${code}`}
            className="max-h-full max-w-full rounded-lg object-contain" />
          <button onClick={() => setLightbox(false)} aria-label="Close"
            className="absolute right-5 top-5 grid h-9 w-9 place-items-center rounded-full bg-white/90 text-slate-700">
            <X className="h-5 w-5" />
          </button>
        </div>
      )}
    </>
  )
}

/* --------------------------------------------------------------------- tabs --- */
function Table({ head, children }: { head: string[]; children: React.ReactNode }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[720px] text-left">
        <thead>
          <tr className="border-y border-slate-200 bg-slate-50/60">
            {head.map((h) => (
              <th key={h} className="px-5 py-3 text-sm font-semibold text-slate-600">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  )
}

function TimelineTable({ events }: { events: StatusEvent[] }) {
  if (events.length === 0) return <Empty>Nothing recorded for this room yet.</Empty>
  return (
    <Table head={['Date & Time', 'Status', 'Changed By', 'Source', 'Remarks']}>
      {events.map((e) => (
        <tr key={e.id} className="border-b border-slate-100 last:border-0">
          <td className="whitespace-nowrap px-5 py-4 text-sm text-slate-700">
            {stamp(e.occurred_at)}
          </td>
          <td className="px-5 py-4"><Badge tone={e.status}>{e.status_label}</Badge></td>
          <td className="px-5 py-4">
            <p className="text-sm font-medium text-slate-700">{e.changed_by_name ?? 'System'}</p>
            {e.changed_by_role && <p className="text-sm text-slate-500">{e.changed_by_role}</p>}
          </td>
          <td className="px-5 py-4">
            <p className="text-sm text-slate-700">{e.source_label}</p>
            {e.source_reference && <p className="text-sm text-slate-500">#{e.source_reference}</p>}
          </td>
          <td className="px-5 py-4 text-sm text-slate-600">{e.remarks ?? '—'}</td>
        </tr>
      ))}
    </Table>
  )
}

/* --------------------------------------------------------------------- page --- */
export default function RoomDetails() {
  const { roomId = '' } = useParams()
  const propertyId = useActivePropertyId()
  const navigate = useNavigate()
  const qc = useQueryClient()

  const [tab, setTab] = useState<Tab>('Overview')
  const [blocking, setBlocking] = useState<'out_of_order' | 'room_block' | null>(null)
  const [moreOpen, setMoreOpen] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const enabled = propertyId !== '' && roomId !== ''
  const overviewQ = useQuery({
    queryKey: ['roomOverview', propertyId, roomId],
    queryFn: () => getRoomOverview(propertyId, roomId), enabled,
  })
  const reservationsQ = useQuery({
    queryKey: ['roomReservations', propertyId, roomId],
    queryFn: () => getRoomReservations(propertyId, roomId),
    enabled: enabled && (tab === 'Reservations'),
  })
  const maintenanceQ = useQuery({
    queryKey: ['roomMaintenance', propertyId, roomId],
    queryFn: () => getRoomMaintenance(propertyId, roomId),
    enabled: enabled && tab === 'Maintenance',
  })
  const historyQ = useQuery({
    queryKey: ['statusHistory', propertyId, roomId, {}],
    queryFn: () => getRoomStatusHistory(propertyId, roomId),
    enabled: enabled && (tab === 'Activity Log' || tab === 'Housekeeping'),
  })

  const r: RoomOverview | undefined = overviewQ.data

  const unblock = useMutation({
    mutationFn: () =>
      endBlock(propertyId, r!.active_block_group_id!, {
        reason: 'Released from Room Details',
      }),
    onSuccess: () => {
      for (const key of ['roomOverview', 'roomMaintenance', 'statusHistory', 'rooms',
        'roomBlocks', 'blockStats']) {
        qc.invalidateQueries({ queryKey: [key, propertyId] })
      }
      qc.invalidateQueries({ queryKey: ['roomOverview', propertyId, roomId] })
    },
    onError: (e) => setError(apiError(e)),
  })

  // The Housekeeping tab is the same timeline, narrowed to the cleaning states.
  const housekeeping = useMemo(
    () => (historyQ.data ?? []).filter((e) => HOUSEKEEPING_STATUSES.includes(e.status)),
    [historyQ.data],
  )

  if (overviewQ.isLoading) {
    return (
      <div className="grid h-64 place-items-center text-slate-400">
        <Loader2 className="h-6 w-6 animate-spin" />
      </div>
    )
  }
  if (overviewQ.isError || !r) {
    return (
      <p className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">
        This room could not be loaded. It may have been removed, or it belongs to
        a different property.
      </p>
    )
  }

  const capacity = `${r.max_adults} adult${r.max_adults === 1 ? '' : 's'}`
    + (r.max_children > 0 ? ` + ${r.max_children} child${r.max_children === 1 ? '' : 'ren'}` : '')
  const blocked = r.active_block_group_id !== null

  return (
    <div className="space-y-4">
      <button onClick={() => navigate('/rooms')}
        className="inline-flex items-center gap-2 text-sm font-semibold text-brand hover:underline">
        <ArrowLeft className="h-4 w-4" /> Back to Rooms
      </button>

      {error && (
        <p className="flex items-center gap-2 rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">
          <AlertCircle className="h-4 w-4 shrink-0" /> {error}
        </p>
      )}

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,1fr)_340px]">
        {/* ----------------------------------------------------- left column */}
        <div className="min-w-0 space-y-5">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <h1 className="text-display text-ink">
                Room {r.code} — {r.room_type_name}
              </h1>
              <p className="mt-1 text-sm text-slate-500">
                {[r.building_name, r.floor_name, `Room ${r.code}`]
                  .filter(Boolean)
                  .map((part, i, all) => (
                    <span key={`${part}`}>
                      {part}
                      {i < all.length - 1 && <span className="px-2 text-slate-300">›</span>}
                    </span>
                  ))}
              </p>
            </div>
            <div className="relative shrink-0">
              <button onClick={() => setMoreOpen((v) => !v)} aria-label="More actions"
                className="grid h-9 w-9 place-items-center rounded-lg border border-slate-200 text-slate-500 hover:bg-slate-50">
                <Ellipsis className="h-5 w-5" />
              </button>
              {moreOpen && (
                <div className="absolute right-0 z-20 mt-1 w-52 overflow-hidden rounded-lg border border-slate-200 bg-white shadow-lg">
                  <button onClick={() => { setMoreOpen(false); navigate(`/rooms/${roomId}/edit`) }}
                    className="block w-full px-4 py-2 text-left text-sm text-slate-700 hover:bg-slate-50">
                    Edit room
                  </button>
                  <button onClick={() => { setMoreOpen(false); navigate(`/rooms/${roomId}/history`) }}
                    className="block w-full px-4 py-2 text-left text-sm text-slate-700 hover:bg-slate-50">
                    Status history
                  </button>
                  <button onClick={() => { setMoreOpen(false); navigate('/rooms/blocks') }}
                    className="block w-full px-4 py-2 text-left text-sm text-slate-700 hover:bg-slate-50">
                    Manage room blocks
                  </button>
                </div>
              )}
            </div>
          </div>

          <Gallery photos={r.photos} code={r.code} />

          <div className="rounded-xl border border-slate-200 bg-white">
            <div className="flex gap-1 overflow-x-auto border-b border-slate-200 px-3">
              {TABS.map((t) => (
                <button key={t} onClick={() => setTab(t)}
                  className={`whitespace-nowrap border-b-2 px-4 py-3 text-sm font-semibold transition ${
                    tab === t
                      ? 'border-brand text-brand'
                      : 'border-transparent text-slate-500 hover:text-slate-700'
                  }`}>
                  {t}
                </button>
              ))}
            </div>

            <div className="p-5">
              {tab === 'Overview' && (
                <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
                  <Card title="Room Details">
                    <div className="grid grid-cols-1 gap-5 sm:grid-cols-2">
                      <Spec icon={Bed} label="Room Type" value={r.room_type_name} />
                      <Spec icon={Maximize2} label="Room Size"
                        value={r.size_sqft ? `${r.size_sqft} sq ft` : '—'} />
                      <Spec icon={Users} label="Capacity" value={capacity} />
                      <Spec icon={Eye} label="View" value={r.view_type ?? '—'} />
                      <Spec icon={Bed} label="Bed Type" value={r.bed_setup ?? '—'} />
                      <Spec icon={IndianRupee} label="Rate (Base)"
                        value={r.base_rate ? `${money(r.base_rate)} / night` : '—'} />
                    </div>
                  </Card>
                  <Card title="Amenities">
                    {r.amenities.length === 0 ? (
                      <Empty>No amenities recorded for this room.</Empty>
                    ) : (
                      <div className="grid grid-cols-1 gap-x-6 gap-y-3 sm:grid-cols-2">
                        {r.amenities.map((a) => (
                          <div key={a.id} className="flex items-center gap-3">
                            <CheckCircle2 className="h-4 w-4 shrink-0 text-brand" />
                            <span className="text-sm text-slate-700">{a.name}</span>
                          </div>
                        ))}
                      </div>
                    )}
                  </Card>
                </div>
              )}

              {tab === 'Reservations' && (
                reservationsQ.isLoading ? <Empty>Loading…</Empty>
                : (reservationsQ.data ?? []).length === 0
                  ? <Empty>No reservation has been assigned to this room.</Empty>
                  : (
                    <Table head={['Reservation', 'Guest', 'Arrival', 'Departure',
                      'Nights', 'Guests', 'Status']}>
                      {(reservationsQ.data ?? []).map((v) => (
                        <tr key={v.reservation_unit_id}
                          className="border-b border-slate-100 last:border-0">
                          <td className="px-5 py-4">
                            <button onClick={() => navigate('/reservations')}
                              className="text-sm font-semibold text-brand hover:underline">
                              {v.reservation_number}
                            </button>
                          </td>
                          <td className="px-5 py-4 text-sm text-slate-700">
                            {v.guest_name ?? '—'}
                          </td>
                          <td className="whitespace-nowrap px-5 py-4 text-sm text-slate-700">
                            {day(v.arrival_date)}
                          </td>
                          <td className="whitespace-nowrap px-5 py-4 text-sm text-slate-700">
                            {day(v.departure_date)}
                          </td>
                          <td className="px-5 py-4 text-sm text-slate-700">{v.nights}</td>
                          <td className="px-5 py-4 text-sm text-slate-700">
                            {v.adults + v.children}
                          </td>
                          <td className="px-5 py-4"><Badge tone={v.status}>{v.status_label}</Badge></td>
                        </tr>
                      ))}
                    </Table>
                  )
              )}

              {tab === 'Housekeeping' && (
                historyQ.isLoading ? <Empty>Loading…</Empty>
                  : housekeeping.length === 0
                    ? <Empty>No housekeeping activity recorded for this room.</Empty>
                    : <TimelineTable events={housekeeping} />
              )}

              {tab === 'Maintenance' && (
                maintenanceQ.isLoading ? <Empty>Loading…</Empty>
                : (maintenanceQ.data ?? []).length === 0
                  ? <Empty>This room has never been blocked or taken out of order.</Empty>
                  : (
                    <Table head={['Period', 'Type', 'Reason', 'Reference',
                      'Raised By', 'Status']}>
                      {(maintenanceQ.data ?? []).map((m) => (
                        <tr key={`${m.group_id}-${m.start_date}`}
                          className="border-b border-slate-100 last:border-0">
                          <td className="whitespace-nowrap px-5 py-4 text-sm text-slate-700">
                            {day(m.start_date)} – {day(m.end_date)}
                          </td>
                          <td className="px-5 py-4 text-sm text-slate-700">{m.block_type_label}</td>
                          <td className="px-5 py-4">
                            <p className="text-sm text-slate-700">{m.reason_category_label}</p>
                            {m.reason && <p className="text-sm text-slate-500">{m.reason}</p>}
                          </td>
                          <td className="px-5 py-4 text-sm text-slate-500">
                            {m.linked_reference ?? '—'}
                          </td>
                          <td className="px-5 py-4 text-sm text-slate-700">
                            {m.created_by_name ?? '—'}
                          </td>
                          <td className="px-5 py-4">
                            <Badge tone={m.status}>
                              {BLOCK_STATUS_LABELS[m.status] ?? m.status}
                            </Badge>
                          </td>
                        </tr>
                      ))}
                    </Table>
                  )
              )}

              {tab === 'Charges' && (
                <div className="px-1 py-8 text-center">
                  <IndianRupee className="mx-auto h-7 w-7 text-slate-300" />
                  <p className="mt-2 text-sm font-medium text-slate-600">
                    Room charges are not available yet.
                  </p>
                  <p className="mx-auto mt-1 max-w-md text-sm text-slate-500">
                    Charges are posted by the folio and billing screens, which are
                    not built yet. This tab will read from them rather than keep a
                    second copy of the figures.
                  </p>
                </div>
              )}

              {tab === 'Activity Log' && (
                historyQ.isLoading ? <Empty>Loading…</Empty>
                  : <TimelineTable events={historyQ.data ?? []} />
              )}
            </div>
          </div>
        </div>

        {/* ---------------------------------------------------- right column */}
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <StatusChip label="Room Status" state={r.occupancy_state}
              value={r.occupancy_label} />
            <StatusChip label="Housekeeping" state={r.housekeeping_state}
              value={r.housekeeping_label} />
          </div>

          <Card
            title="Upcoming Booking"
            action={r.upcoming_booking && (
              <button onClick={() => navigate('/reservations')}
                className="flex items-center gap-1 text-sm font-semibold text-brand hover:underline">
                View Reservation <ArrowRight className="h-4 w-4" />
              </button>
            )}>
            {!r.upcoming_booking ? (
              <p className="text-sm text-slate-500">
                No upcoming booking for this room.
              </p>
            ) : (
              <>
                <p className="text-lg font-bold text-slate-800">
                  {r.upcoming_booking.guest_name ?? r.upcoming_booking.reservation_number}
                </p>
                <p className="mt-2 flex flex-wrap items-center gap-2 text-sm text-slate-600">
                  <CalendarDays className="h-4 w-4 text-slate-400" />
                  {day(r.upcoming_booking.arrival_date, true)}
                  <ArrowRight className="h-3.5 w-3.5 text-slate-400" />
                  {day(r.upcoming_booking.departure_date, true)}
                </p>
                <p className="mt-2 flex flex-wrap items-center gap-3 text-sm text-slate-600">
                  <span>{r.upcoming_booking.nights} night
                    {r.upcoming_booking.nights === 1 ? '' : 's'}</span>
                  <span className="text-slate-300">|</span>
                  <span className="flex items-center gap-1">
                    <Users className="h-4 w-4 text-slate-400" />
                    {r.upcoming_booking.adults} adult
                    {r.upcoming_booking.adults === 1 ? '' : 's'}
                    {r.upcoming_booking.children > 0
                      && `, ${r.upcoming_booking.children} child`}
                  </span>
                </p>
                <p className="mt-3">
                  <Badge tone={r.upcoming_booking.status}>
                    {r.upcoming_booking.status_label}
                  </Badge>
                </p>
              </>
            )}
          </Card>

          <div className="space-y-3">
            {blocked ? (
              <button onClick={() => { setError(null); unblock.mutate() }}
                disabled={unblock.isPending}
                className="flex w-full items-center justify-center gap-2 rounded-lg bg-brand px-4 py-3 font-semibold text-white hover:bg-brand-dark disabled:opacity-60">
                {unblock.isPending
                  ? <Loader2 className="h-4 w-4 animate-spin" />
                  : <Lock className="h-4 w-4" />}
                Release Block
              </button>
            ) : (
              <button onClick={() => setBlocking('room_block')}
                className="flex w-full items-center justify-center gap-2 rounded-lg bg-brand px-4 py-3 font-semibold text-white hover:bg-brand-dark">
                <Lock className="h-4 w-4" /> Block Room
              </button>
            )}
            <button onClick={() => setBlocking('out_of_order')} disabled={blocked}
              title={blocked ? 'This room is already blocked' : undefined}
              className="flex w-full items-center justify-center gap-2 rounded-lg border border-brand px-4 py-3 font-semibold text-brand hover:bg-brand-light disabled:cursor-not-allowed disabled:border-slate-200 disabled:text-slate-400 disabled:hover:bg-transparent">
              <Ban className="h-4 w-4" /> Mark Out of Order
            </button>
            <button onClick={() => navigate(`/rooms/${roomId}/edit`)}
              className="flex w-full items-center justify-center gap-2 rounded-lg border border-brand px-4 py-3 font-semibold text-brand hover:bg-brand-light">
              <Pencil className="h-4 w-4" /> Edit Room
            </button>
          </div>

          <Card title={
            <span className="flex items-center gap-2">
              <FileText className="h-4 w-4 text-slate-400" /> Notes
            </span>
          }>
            {r.notes
              ? <p className="whitespace-pre-wrap text-sm text-slate-600">{r.notes}</p>
              : <p className="text-sm text-slate-500">No notes recorded for this room.</p>}
          </Card>

          <Card title={
            <span className="flex items-center gap-2">
              <Tag className="h-4 w-4 text-slate-400" /> Room Tags
            </span>
          }>
            {r.tags.length === 0 ? (
              <p className="text-sm text-slate-500">No tags.</p>
            ) : (
              <div className="flex flex-wrap gap-2">
                {r.tags.map((t) => (
                  <span key={t}
                    className="rounded-full bg-amber-50 px-3 py-1 text-xs font-semibold text-caution">
                    {t}
                  </span>
                ))}
              </div>
            )}
            <p className="mt-3 text-xs text-slate-400">
              Tags follow the room's view, type and capacity — they are not
              edited separately.
            </p>
          </Card>

          {r.housekeeping_zone && (
            <Card title={
              <span className="flex items-center gap-2">
                <Wrench className="h-4 w-4 text-slate-400" /> Housekeeping Zone
              </span>
            }>
              <p className="text-sm text-slate-700">{r.housekeeping_zone}</p>
            </Card>
          )}
        </div>
      </div>

      {blocking && (
        <BlockDialog propertyId={propertyId} roomId={roomId} roomCode={r.code}
          defaultBlockType={blocking} onClose={() => setBlocking(null)} />
      )}
    </div>
  )
}
