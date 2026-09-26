import { useMemo, useState } from 'react'
import Select from '../components/Select'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useParams } from 'react-router-dom'
import {
  ArrowLeft, ChevronDown, ChevronLeft, Download, Info, Loader2, ExternalLink,
  Bed, Building2, Wrench, AlertCircle, X,
} from 'lucide-react'
import { Broom } from '../components/icons'
import DateRangeFilter from '../components/DateRangeFilter'
import { FILTER_SELECT } from '../lib/controls'
import { fmtDate, fmtDateTime } from '../lib/dates'
import {
  getRoomStatusHistory, getRoomStatusSummary, setHousekeepingStatus,
  
  type StatusEvent, type RoomStatusSummary, type StatusHistoryFilters,
} from '../api'
import { useActivePropertyId } from '../hooks/useProperty'
import { errorText } from '../lib/forms'

/**
 * Screen 064 — Room Status History.
 *
 * The timeline is read-only by design: it is what happened, not a field to be
 * edited. A correction is therefore a *new* event appended to the end, which is
 * what the correction dialog does — nothing on this screen rewrites the past.
 */


const STATUS_OPTIONS = [
  ['occupied', 'Occupied'], ['available', 'Available'], ['clean', 'Clean'],
  ['dirty', 'Dirty'], ['cleaning', 'Cleaning'], ['inspected', 'Inspected'],
  ['out_of_order', 'Out of Order'], ['blocked', 'Blocked'],
  ['inactive', 'Inactive'], ['maintenance', 'Maintenance'],
] as const

const SOURCE_OPTIONS = [
  ['reservation_checkin', 'Reservation Check-in'],
  ['reservation_checkout', 'Reservation Check-out'],
  ['housekeeping', 'Housekeeping'], ['maintenance', 'Maintenance'],
  ['block', 'Room Block'], ['user_update', 'User Update'], ['system', 'System'],
] as const

// Housekeeping is the only status a person sets directly; occupancy comes from
// the reservation and out-of-order from the block, so those are corrected on
// their own records rather than here.
const CORRECTABLE = [
  ['clean', 'Clean'], ['dirty', 'Dirty'],
  ['cleaning', 'Cleaning'], ['inspected', 'Inspected'],
] as const

const BADGE: Record<string, string> = {
  occupied: 'bg-sky-100 text-sky-700',
  available: 'bg-emerald-100 text-emerald-700',
  clean: 'bg-emerald-100 text-emerald-700',
  inspected: 'bg-violet-100 text-violet-700',
  dirty: 'bg-amber-100 text-amber-800',
  cleaning: 'bg-blue-100 text-blue-700',
  out_of_order: 'bg-red-100 text-red-700',
  blocked: 'bg-orange-100 text-orange-700',
  maintenance: 'bg-amber-100 text-amber-800',
  inactive: 'bg-slate-200 text-slate-600',
}

const input = 'w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700'
const filterSelect = `${FILTER_SELECT} bg-white outline-none focus:border-brand`


/** "25 Sep 2026, 11:15" — one spelling, shared with the whole app. */
function stamp(iso: string | null, withTime = true): string {
  if (!iso) return '—'
  return withTime ? fmtDateTime(iso) : fmtDate(iso)
}

/** "1 day 2 hours" — how long the current status has held. */
function elapsed(iso: string | null): string {
  if (!iso) return ''
  const mins = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 60000))
  const days = Math.floor(mins / 1440)
  const hours = Math.floor((mins % 1440) / 60)
  const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? '' : 's'}`
  if (days > 0) return `${plural(days, 'day')} ${plural(hours, 'hour')}`
  if (hours > 0) return `${plural(hours, 'hour')} ${plural(mins % 60, 'minute')}`
  return plural(mins, 'minute')
}

function apiError(e: unknown): string {
  return errorText(e, 'Could not record the correction. Please try again.')
}

function Badge({ status, label }: { status: string; label: string }) {
  return (
    <span className={`inline-block rounded-full px-3 py-1 text-xs font-semibold ${
      BADGE[status] ?? 'bg-slate-75 text-slate-600'}`}>
      {label}
    </span>
  )
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-1">
      <span className="text-sm text-slate-500">{label}</span>
      <span className="text-right text-sm font-medium text-slate-700">{value}</span>
    </div>
  )
}

function Panel({ title, children }: { title: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white">
      <div className="border-b border-slate-100 px-4 py-3">{title}</div>
      <div className="px-4 py-3">{children}</div>
    </div>
  )
}

/* ------------------------------------------------------- correction dialog --- */
function CorrectionDialog({
  propertyId, roomId, roomCode, onClose,
}: {
  propertyId: string; roomId: string; roomCode: string; onClose: () => void
}) {
  const qc = useQueryClient()
  const [status, setStatus] = useState('clean')
  const [remarks, setRemarks] = useState('')
  const [error, setError] = useState<string | null>(null)

  const save = useMutation({
    mutationFn: () => setHousekeepingStatus(propertyId, roomId, { status, remarks }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['statusHistory', propertyId, roomId] })
      qc.invalidateQueries({ queryKey: ['statusSummary', propertyId, roomId] })
      qc.invalidateQueries({ queryKey: ['rooms', propertyId] })
      onClose()
    },
    onError: (e) => setError(apiError(e)),
  })

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-slate-900/40 p-4">
      <div className="w-full max-w-lg rounded-xl bg-white shadow-xl">
        <div className="flex items-start justify-between border-b border-slate-100 px-5 py-4">
          <div>
            <h2 className="text-lg font-bold text-ink">
              Record a Status Correction
            </h2>
            <p className="text-sm text-slate-500">Room {roomCode}</p>
          </div>
          <button onClick={onClose} aria-label="Close"
            className="rounded p-1 text-slate-400 hover:bg-slate-100">
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="space-y-4 px-5 py-4">
          <p className="rounded-lg bg-slate-50 px-3 py-2 text-sm text-slate-600">
            History is never rewritten. The correction is appended as a new
            entry, attributed to you, so the original record stays visible.
          </p>
          <label className="block">
            <span className="mb-1 block text-sm font-medium text-slate-700">
              Corrected status <span className="text-red-500">*</span>
            </span>
            <Select className={input} value={status}
              onChange={(e) => setStatus(e.target.value)}>
              {CORRECTABLE.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </Select>
          </label>
          <label className="block">
            <span className="mb-1 block text-sm font-medium text-slate-700">Reason</span>
            <textarea className={`${input} h-24`} value={remarks} maxLength={500}
              placeholder="Why the recorded status was wrong."
              onChange={(e) => setRemarks(e.target.value)} />
          </label>
          <p className="text-sm text-slate-500">
            Occupancy and out-of-order statuses come from the reservation and
            the room block. Correct those on the record itself so the room
            calendar stays in step.
          </p>
          {error && (
            <p className="flex items-center gap-2 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">
              <AlertCircle className="h-4 w-4 shrink-0" /> {error}
            </p>
          )}
        </div>

        <div className="flex justify-end gap-2 border-t border-slate-100 px-5 py-4">
          <button onClick={onClose}
            className="rounded-lg border border-slate-200 px-4 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50">
            Cancel
          </button>
          <button onClick={() => save.mutate()} disabled={save.isPending}
            className="flex items-center gap-2 rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white hover:bg-brand-dark disabled:opacity-60">
            {save.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
            Record Correction
          </button>
        </div>
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------ page --- */
export default function RoomStatusHistory() {
  const { roomId = '' } = useParams()
  const propertyId = useActivePropertyId()
  const navigate = useNavigate()

  const [draft, setDraft] = useState<StatusHistoryFilters>({})
  const [applied, setApplied] = useState<StatusHistoryFilters>({})
  const [correcting, setCorrecting] = useState(false)
  const [moreOpen, setMoreOpen] = useState(false)

  const summaryQ = useQuery({
    queryKey: ['statusSummary', propertyId, roomId],
    queryFn: () => getRoomStatusSummary(propertyId, roomId),
    enabled: propertyId !== '' && roomId !== '',
  })
  const historyQ = useQuery({
    queryKey: ['statusHistory', propertyId, roomId, applied],
    queryFn: () => getRoomStatusHistory(propertyId, roomId, applied),
    enabled: propertyId !== '' && roomId !== '',
  })

  const s: RoomStatusSummary | undefined = summaryQ.data
  const events: StatusEvent[] = historyQ.data ?? []

  // The User filter offers only people who actually appear on this timeline,
  // so it can never select someone with nothing to show.
  const actors = useMemo(() => {
    const seen = new Map<string, string>()
    for (const e of events) {
      if (e.changed_by_name) seen.set(e.changed_by_name, e.changed_by_name)
    }
    return [...seen.keys()].sort()
  }, [events])

  const visible = useMemo(
    () => (applied.user ? events.filter((e) => e.changed_by_name === applied.user) : events),
    [events, applied.user],
  )

  const csv = useMemo(() => {
    const head = ['#', 'Date & Time', 'Status', 'Changed By', 'Role', 'Source',
      'Reference', 'Remarks']
    const cell = (v: string) => `"${v.replace(/"/g, '""')}"`
    const lines = visible.map((e, i) => [
      String(i + 1), stamp(e.occurred_at), e.status_label, e.changed_by_name ?? '',
      e.changed_by_role ?? '', e.source_label, e.source_reference ?? '',
      e.remarks ?? '',
    ].map(cell).join(','))
    return [head.map(cell).join(','), ...lines].join('\n')
  }, [visible])

  function exportCsv() {
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }))
    const a = document.createElement('a')
    a.href = url
    a.download = `room-${s?.room_code ?? roomId}-status-history.csv`
    a.click()
    URL.revokeObjectURL(url)
  }

  const set = (k: keyof StatusHistoryFilters) =>
    (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
      setDraft((d) => ({ ...d, [k]: e.target.value || undefined }))

  if (summaryQ.isLoading) {
    return (
      <div className="grid h-64 place-items-center text-slate-400">
        <Loader2 className="h-6 w-6 animate-spin" />
      </div>
    )
  }
  if (summaryQ.isError || !s) {
    return (
      <p className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">
        This room could not be loaded. It may have been removed, or it belongs
        to a different property.
      </p>
    )
  }

  return (
    <div className="space-y-4">
      <p className="text-sm text-slate-400">
        <button onClick={() => navigate('/rooms')}
          className="text-brand hover:underline">Rooms</button>
        <span className="px-2">›</span>
        <button onClick={() => navigate('/rooms')}
          className="text-brand hover:underline">Room List</button>
        <span className="px-2">›</span>
        <button onClick={() => navigate(`/rooms/${roomId}`)}
          className="text-brand hover:underline">Room {s.room_code}</button>
        <span className="px-2">›</span>
        <span className="font-semibold text-slate-700">Status History</span>
      </p>

      {/* Title, current status and the room's particulars on one line, the
          page actions at its right. */}
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
          <h1 className="flex items-center gap-2 text-display text-ink">
            <Building2 size={26} className="text-brand" /> Room {s.room_code}
          </h1>
          <Badge status={s.current_status} label={s.current_status_label} />
          <span className="text-sm text-slate-500">
            {[
              s.room_type_name,
              `${s.max_occupancy} Guests`,
              s.floor ? `Floor ${s.floor}` : null,
              s.reservation_number ? `Res #${s.reservation_number}` : null,
              s.current_guest,
            ].filter(Boolean).join(' · ')}
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative">
            <button onClick={() => setMoreOpen((v) => !v)}
              className="flex items-center gap-1.5 whitespace-nowrap rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-600 hover:bg-slate-50">
              More Actions <ChevronDown size={15} />
            </button>
            {moreOpen && (
              <div className="absolute right-0 z-20 mt-1 w-56 overflow-hidden rounded-lg border border-slate-200 bg-white shadow-lg">
                <button onClick={() => { setMoreOpen(false); exportCsv() }}
                  className="block w-full px-4 py-2 text-left text-sm text-slate-700 hover:bg-slate-50">
                  Export timeline (CSV)
                </button>
                <button onClick={() => { setMoreOpen(false); setCorrecting(true) }}
                  className="block w-full px-4 py-2 text-left text-sm text-slate-700 hover:bg-slate-50">
                  Record a status correction
                </button>
                <button onClick={() => { setMoreOpen(false); navigate('/rooms/blocks') }}
                  className="block w-full px-4 py-2 text-left text-sm text-slate-700 hover:bg-slate-50">
                  Manage room blocks
                </button>
              </div>
            )}
          </div>
          <button onClick={() => navigate(`/rooms/${roomId}`)}
            className="flex items-center gap-1.5 whitespace-nowrap rounded-lg bg-brand px-3 py-2 text-sm font-medium text-white hover:bg-brand-dark disabled:opacity-40">
            <ArrowLeft size={15} /> Back to Room
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_340px]">
        {/* ------------------------------------------------------ left column */}
        <div className="min-w-0 space-y-4">
          {/* Filters: one row, no captions. They are drafted here and only
              take effect on Apply. */}
          <div className="flex flex-wrap items-center gap-2">
            <DateRangeFilter label="Date" direction="past" from={draft.from ?? ''} to={draft.to ?? ''}
              onChange={(from, to) => setDraft((d) => ({
                ...d, from: from || undefined, to: to || undefined,
              }))} />
            <Select blankIsChoice aria-label="Status" className={filterSelect}
              value={draft.status ?? ''} onChange={set('status')}>
              <option value="">All Statuses</option>
              {STATUS_OPTIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </Select>
            <Select blankIsChoice aria-label="User" className={filterSelect}
              value={draft.user ?? ''} onChange={set('user')}>
              <option value="">All Users</option>
              {actors.map((name) => <option key={name} value={name}>{name}</option>)}
            </Select>
            <Select blankIsChoice aria-label="Source" className={filterSelect}
              value={draft.source ?? ''} onChange={set('source')}>
              <option value="">All Sources</option>
              {SOURCE_OPTIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </Select>
            <span className="ml-auto flex items-center gap-2">
              <button onClick={() => { setDraft({}); setApplied({}) }}
                className="whitespace-nowrap px-1 text-sm font-semibold text-brand hover:underline">
                Clear Filters
              </button>
              <button onClick={() => setApplied(draft)}
                className="flex items-center gap-1.5 whitespace-nowrap rounded-lg bg-brand px-3 py-2 text-sm font-medium text-white hover:bg-brand-dark disabled:opacity-40">
                Apply Filters
              </button>
            </span>
          </div>

          {/* timeline */}
          <div className="rounded-xl border border-slate-200 bg-white">
            <div className="flex flex-wrap items-center justify-between gap-3 px-5 py-4">
              <h2 className="text-xl font-bold text-ink">
                Status History Timeline (Room {s.room_code})
              </h2>
              <div className="flex items-center gap-3">
                <span className="rounded-md bg-slate-75 px-3 py-1 text-sm font-medium text-slate-600">
                  {visible.length} record{visible.length === 1 ? '' : 's'}
                </span>
                <button onClick={exportCsv} disabled={visible.length === 0}
                  className="flex items-center gap-2 rounded-lg border border-brand px-4 py-2 text-sm font-semibold text-brand hover:bg-brand-light disabled:cursor-not-allowed disabled:border-slate-200 disabled:text-slate-400 disabled:hover:bg-transparent">
                  <Download className="h-4 w-4" /> Export
                </button>
              </div>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full min-w-[760px] text-left">
                <thead>
                  <tr className="border-y border-slate-200 bg-slate-50/60">
                    {['#', 'Date & Time', 'Status', 'Changed By', 'Source', 'Remarks'].map((h) => (
                      <th key={h} className="px-5 py-3 text-sm font-semibold text-slate-600">
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {historyQ.isLoading && (
                    <tr><td colSpan={6} className="px-5 py-10 text-center text-slate-400">
                      <Loader2 className="mx-auto h-5 w-5 animate-spin" />
                    </td></tr>
                  )}
                  {!historyQ.isLoading && visible.length === 0 && (
                    <tr><td colSpan={6} className="px-5 py-10 text-center text-sm text-slate-500">
                      No status changes recorded for this room in the selected period.
                    </td></tr>
                  )}
                  {visible.map((e, i) => (
                    <tr key={e.id} className="border-b border-slate-100 last:border-0">
                      <td className="px-5 py-4 text-sm text-slate-500">{i + 1}</td>
                      <td className="whitespace-nowrap px-5 py-4 text-sm text-slate-700">
                        {stamp(e.occurred_at)}
                      </td>
                      <td className="px-5 py-4">
                        <Badge status={e.status} label={e.status_label} />
                      </td>
                      <td className="px-5 py-4">
                        <p className="text-sm font-medium text-slate-700">
                          {e.changed_by_name ?? 'System'}
                        </p>
                        {e.changed_by_role && (
                          <p className="text-sm text-slate-500">{e.changed_by_role}</p>
                        )}
                      </td>
                      <td className="px-5 py-4">
                        <p className="text-sm text-slate-700">{e.source_label}</p>
                        {e.source_reference && (
                          <p className="text-sm text-slate-500">#{e.source_reference}</p>
                        )}
                      </td>
                      <td className="px-5 py-4 text-sm text-slate-600">{e.remarks ?? '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>

        {/* ----------------------------------------------------- right column */}
        <div className="space-y-4">
          <div className="rounded-xl border border-slate-200 bg-white p-4">
            <div className="mb-3 flex items-center gap-2">
              <button onClick={() => navigate('/rooms')} aria-label="Back to room list"
                className="rounded p-0.5 text-slate-400 hover:bg-slate-100">
                <ChevronLeft className="h-5 w-5" />
              </button>
              <p className="text-lg font-bold text-slate-800">Room {s.room_code}</p>
            </div>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-1">
              {s.primary_photo_url ? (
                <img src={s.primary_photo_url} alt={`Room ${s.room_code}`}
                  className="h-32 w-full rounded-lg object-cover" />
              ) : (
                <div className="grid h-32 w-full place-items-center rounded-lg bg-slate-75 text-slate-400">
                  <Bed className="h-7 w-7" />
                </div>
              )}
              <div>
                <p className="mb-1 font-semibold text-slate-800">{s.room_type_name}</p>
                <Row label="Floor" value={s.floor ?? '—'} />
                <Row label="Max Occupancy" value={s.max_occupancy} />
                <Row label="Room Type" value={s.room_type_name} />
                <Row label="Room Status"
                  value={<Badge status={s.current_status} label={s.current_status_label} />} />
              </div>
            </div>
          </div>

          <Panel title={
            <div className="flex items-center justify-between gap-2">
              <h3 className="font-bold text-ink">Current Status</h3>
              <Badge status={s.current_status} label={s.current_status_label} />
            </div>
          }>
            <Row label="Since" value={
              s.since ? `${stamp(s.since)} (${elapsed(s.since)})` : 'Not recorded'
            } />
            <Row label="Current Guest" value={s.current_guest ?? '—'} />
            <Row label="Reservation" value={
              s.reservation_number
                ? <button onClick={() => navigate('/reservations')}
                    className="text-brand hover:underline">
                    {s.reservation_number} ({s.reservation_state})
                  </button>
                : '—'
            } />
          </Panel>

          <Panel title={<h3 className="font-bold text-ink">Linked Records</h3>}>
            {s.linked_records.length === 0 && (
              <p className="text-sm text-slate-500">No linked records.</p>
            )}
            <div className="divide-y divide-slate-100">
              {s.linked_records.map((r) => {
                const Icon = r.kind === 'reservation' ? Bed
                  : r.kind === 'housekeeping' ? Broom : Wrench
                const target = r.kind === 'reservation' ? '/reservations'
                  : r.kind === 'housekeeping' ? '/housekeeping' : '/rooms/blocks'
                return (
                  <div key={r.kind} className="flex items-start gap-3 py-3 first:pt-0 last:pb-0">
                    <Icon className="mt-0.5 h-5 w-5 shrink-0 text-slate-400" />
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-semibold capitalize text-slate-800">{r.kind}</p>
                      <p className="truncate text-sm text-brand">{r.reference}</p>
                      {r.detail && <p className="text-sm text-slate-500">{r.detail}</p>}
                    </div>
                    <button onClick={() => navigate(target)}
                      className="flex shrink-0 items-center gap-1 text-sm font-medium text-brand hover:underline">
                      View <ExternalLink className="h-3.5 w-3.5" />
                    </button>
                  </div>
                )
              })}
            </div>
          </Panel>

          <Panel title={
            <h3 className="flex items-center gap-2 font-bold text-ink">
              <Info className="h-4 w-4 text-slate-400" /> Audit Information
            </h3>
          }>
            <Row label="Created" value={stamp(s.created_at)} />
            <Row label="Last Updated" value={stamp(s.last_updated_at)} />
            <Row label="Total Changes" value={s.total_changes} />
            <Row label="Data Source" value="PMS (System & User Updates)" />
            <Row label="Record Type" value="Room Status History (Read-only)" />
          </Panel>

          <div className="rounded-xl border border-amber-200 bg-amber-50 p-4">
            <p className="flex items-center gap-2 font-semibold text-amber-900">
              <Info className="h-4 w-4 shrink-0" /> Need to correct a status?
            </p>
            <p className="mt-1 text-sm text-caution">
              Room status history is read-only. A correction is appended as a
              new entry rather than replacing what was recorded, so the original
              stays visible.
            </p>
            <button onClick={() => setCorrecting(true)}
              className="mt-3 rounded-lg border border-amber-400 bg-white px-4 py-2 text-sm font-semibold text-amber-900 hover:bg-amber-100">
              Record a Correction
            </button>
          </div>
        </div>
      </div>

      {correcting && (
        <CorrectionDialog propertyId={propertyId} roomId={roomId}
          roomCode={s.room_code} onClose={() => setCorrecting(false)} />
      )}
    </div>
  )
}
