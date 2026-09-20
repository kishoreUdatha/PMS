import { useState } from 'react'
import { fmtMonth, fmtWeekday } from '../lib/dates'
import RoomPicker from './RoomPicker'
import { useNavigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  X, Calendar, ChevronRight, ChevronDown, ChevronLeft, Loader2, BedDouble,
  AlertTriangle, CheckCircle2, LogIn, Info,
} from 'lucide-react'
import { getAssignBoard, assignRoomToUnit, type AssignUnit } from '../api'

import DateField from '../components/DateField'
const dayNum = (d: string) => new Date(`${d}T00:00:00`).getDate()
const monthShort = (d: string) => fmtMonth(d).toUpperCase()
const weekday = (d: string) => fmtWeekday(d)
const shift = (d: string, n: number) => {
  const x = new Date(`${d}T00:00:00`)
  x.setDate(x.getDate() + n)
  return x.toISOString().slice(0, 10)
}

/**
 * The front desk's "who still needs a room" queue.
 *
 * Bookings are grouped by room type because that is how the work is done —
 * clear the Suites, then the Twins. The rooms offered under each booking come
 * from the server, filtered against the same calendar the exclusion
 * constraint guards; if one is taken between opening this panel and clicking
 * Assign, the API refuses with a 409 and the message is shown here rather
 * than the panel pretending it worked.
 */
export default function AssignRoomsPanel({
  propertyId, startDate, onClose, onAssigned,
}: {
  propertyId: string
  /** Arrival date of the booking that was clicked; the panel opens there
      rather than on today, which would be the wrong day for a future
      arrival. */
  startDate?: string | null
  onClose: () => void
  onAssigned: (message: string) => void
}) {
  const qc = useQueryClient()
  const [day, setDay] = useState(
    () => startDate ?? new Date().toISOString().slice(0, 10))
  const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({})
  const [editing, setEditing] = useState<string | null>(null)
  const [err, setErr] = useState('')

  const { data, isLoading, isFetching } = useQuery({
    queryKey: ['assign-board', propertyId, day],
    queryFn: () => getAssignBoard(propertyId, day),
    enabled: propertyId !== '',
  })

  function refresh() {
    qc.invalidateQueries({ queryKey: ['assign-board'] })
    qc.invalidateQueries({ queryKey: ['reservations'] })
    qc.invalidateQueries({ queryKey: ['room-rack'] })
  }

  const groups = data?.groups ?? []
  // The first group starts open, so the panel is never a wall of closed rows.
  const isOpen = (key: string, i: number) => openGroups[key] ?? i === 0

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/30" onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()}
        className="flex h-full w-full max-w-md flex-col bg-white shadow-xl">
        <div className="flex items-center justify-between border-b border-slate-100 px-5 py-4">
          <h2 className="flex items-center gap-2 text-lg font-semibold text-ink">
            <BedDouble size={19} className="text-brand" /> Assign Rooms
          </h2>
          <div className="flex items-center gap-1">
            <label className="flex cursor-pointer items-center gap-1.5 rounded-lg border border-slate-200 px-2.5 py-1.5 text-xs text-slate-600 hover:bg-slate-50">
              <Calendar size={14} />
              <DateField value={day} onChange={(v) => setDay(v)} className="w-[105px] bg-transparent outline-none" />
            </label>
            <button onClick={onClose} className="p-1.5 text-slate-400 hover:text-slate-600">
              <X size={18} />
            </button>
          </div>
        </div>

        {/* Date strip: the count is how many arrivals that day still need a
            room, so a clerk can see where the work is before clicking. */}
        <div className="flex items-center gap-1 border-b border-slate-100 px-2 py-2">
          <button onClick={() => setDay(shift(day, -1))} title="Earlier"
            className="shrink-0 rounded-lg p-1.5 text-slate-400 hover:bg-slate-50">
            <ChevronLeft size={16} />
          </button>
          <div className="flex flex-1 gap-1 overflow-x-auto">
            {(data?.days ?? []).map((c) => {
              const active = c.day === day
              return (
                <button key={c.day} onClick={() => setDay(c.day)}
                  className={`relative flex min-w-[46px] shrink-0 flex-col items-center rounded-lg px-1.5 py-1.5 text-xs ${
                    active ? 'bg-brand text-white'
                      : c.is_weekend ? 'bg-amber-50 text-caution hover:bg-amber-100'
                        : 'text-slate-600 hover:bg-slate-50'}`}>
                  <span className="opacity-70">{weekday(c.day)}</span>
                  <span className="text-sm font-bold">{dayNum(c.day)}</span>
                  <span className="opacity-70">{monthShort(c.day)}</span>
                  {c.unassigned > 0 && (
                    <span className={`absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full px-1 text-[10px] font-bold ${
                      active ? 'bg-white text-brand' : 'bg-rose-500 text-white'}`}>
                      {c.unassigned}
                    </span>
                  )}
                </button>
              )
            })}
          </div>
          <button onClick={() => setDay(shift(day, 7))} title="Later"
            className="shrink-0 rounded-lg p-1.5 text-slate-400 hover:bg-slate-50">
            <ChevronRight size={16} />
          </button>
        </div>

        {err && (
          <p className="flex items-start gap-2 border-b border-red-100 bg-red-50 px-5 py-3 text-sm text-red-700">
            <AlertTriangle size={15} className="mt-0.5 shrink-0" /> {err}
          </p>
        )}

        <div className="flex-1 overflow-y-auto p-3">
          {isLoading && (
            <p className="py-16 text-center"><Loader2 className="mx-auto animate-spin text-slate-400" /></p>
          )}
          {!isLoading && groups.length === 0 && (
            <p className="flex flex-col items-center gap-2 py-16 text-center text-sm text-slate-400">
              <CheckCircle2 size={22} className="text-emerald-500" />
              Every arrival on this day has a room.
            </p>
          )}
          {groups.map((g, i) => {
            const key = g.room_type_id ?? 'none'
            const open = isOpen(key, i)
            return (
              <div key={key} className="mb-2 overflow-hidden rounded-xl border border-slate-200">
                <button
                  onClick={() => setOpenGroups((p) => ({ ...p, [key]: !open }))}
                  className="flex w-full items-center justify-between px-4 py-3 text-sm font-semibold text-slate-700 hover:bg-slate-50">
                  <span className="flex items-center gap-2">
                    {open ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
                    {g.name}
                  </span>
                  <span className="text-slate-400">({g.count})</span>
                </button>
                {open && (
                  <div className="space-y-2 border-t border-slate-100 bg-slate-50/50 p-2">
                    {g.units.map((u) => (
                      <UnitCard key={u.unit_id} unit={u}
                        editing={editing === u.unit_id}
                        canAssign={data?.can_assign !== false}
                        onEdit={() => { setEditing(editing === u.unit_id ? null : u.unit_id); setErr('') }}
                        onDone={(m) => { setEditing(null); setErr(''); refresh(); onAssigned(m) }}
                        onFail={setErr} />
                    ))}
                  </div>
                )}
              </div>
            )
          })}
        </div>

        <p className="flex items-start gap-2 border-t border-slate-100 px-5 py-3 text-xs text-slate-400">
          <Info size={13} className="mt-0.5 shrink-0" />
          Rooms offered are free for the whole stay. A room still being cleaned
          is offered and marked — housekeeping will reach it before the guest.
          {isFetching && !isLoading && <Loader2 size={12} className="animate-spin" />}
        </p>
      </div>
    </div>
  )
}

function UnitCard({ unit, editing, canAssign, onEdit, onDone, onFail }: {
  unit: AssignUnit; editing: boolean; canAssign: boolean
  onEdit: () => void; onDone: (m: string) => void; onFail: (m: string) => void
}) {
  const nav = useNavigate()
  const [roomId, setRoomId] = useState('')
  const [busy, setBusy] = useState(false)
  const none = unit.free_count === 0

  async function assign(thenCheckIn: boolean) {
    if (!roomId) return
    setBusy(true)
    try {
      await assignRoomToUnit(unit.unit_id, roomId)
      const code = unit.candidates.find((c) => c.room_id === roomId)?.code
      onDone(`${unit.guest_name ?? unit.number} assigned to room ${code}.`)
      if (thenCheckIn) nav(`/front-desk/check-in/${unit.unit_id}`)
    } catch (e) {
      const er = e as { response?: { data?: { detail?: string } }; message?: string }
      onFail(er.response?.data?.detail
        ?? er.message ?? 'That room could not be assigned.')
    } finally { setBusy(false) }
  }

  return (
    <div className={`rounded-xl border bg-white p-3 ${
      editing ? 'border-brand' : 'border-slate-200'}`}>
      <div className="flex gap-3">
        <div className="shrink-0 text-center">
          <DateChip iso={unit.arrival_date} />
          <span className="my-0.5 block text-[10px] text-slate-300">
            {unit.nights}n
          </span>
          <DateChip iso={unit.departure_date} muted />
        </div>
        <div className="min-w-0 flex-1">
          <button onClick={() => nav(`/reservations/${unit.reservation_id}`)}
            className="block truncate font-semibold text-brand hover:underline">
            {unit.guest_name ?? 'No guest on file'}
          </button>
          <span className="block text-xs text-slate-500">{unit.number}</span>
          <span className="block text-xs text-slate-400">
            {unit.adults} adult{unit.adults === 1 ? '' : 's'}
            {unit.children > 0 && `, ${unit.children} child${unit.children === 1 ? '' : 'ren'}`}
          </span>
          <div className="mt-2 grid grid-cols-2 gap-2">
            <span className="min-w-0">
              <span className="block text-[10px] uppercase tracking-wide text-slate-400">
                Room Type
              </span>
              <span className="block truncate text-xs text-slate-700">
                {unit.room_type ?? '—'}
              </span>
            </span>
            <span className="min-w-0">
              <span className="block text-[10px] uppercase tracking-wide text-slate-400">
                Room
              </span>
              {canAssign && !editing ? (
                <button onClick={onEdit} disabled={none}
                  className="mt-0.5 w-full rounded-lg border border-brand px-2 py-1 text-xs font-semibold text-brand enabled:hover:bg-brand-light disabled:border-slate-200 disabled:text-slate-300">
                  {none ? 'No room free' : 'Assign Room'}
                </button>
              ) : (
                <span className="block text-xs text-slate-400">&mdash;</span>
              )}
            </span>
          </div>
        </div>
      </div>

      {editing && (
        <div className="mt-3 border-t border-slate-100 pt-3">
          <label className="block">
            <span className="mb-1 block text-[10px] uppercase tracking-wide text-slate-400">
              Room Type
            </span>
            <input value={unit.room_type ?? '—'} readOnly tabIndex={-1}
              title="Changing the room type re-prices the stay — use Modify Stay"
              className="w-full cursor-not-allowed rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-500" />
          </label>
          <label className="mt-2 block">
            <span className="mb-1 block text-[10px] uppercase tracking-wide text-slate-400">
              Room ({unit.free_count} of {unit.candidates.length} free for these dates)
            </span>
            {/* Each row carries state the clerk is deciding on — free,
                cleaned, or what is in the way — so the list is drawn rather
                than left to the operating system, which paints every option
                the same shade of blue. */}
            <RoomPicker rooms={unit.candidates} value={roomId}
              onChange={setRoomId} />
          </label>
          <div className="mt-3 flex justify-end gap-2">
            <button onClick={onEdit}
              className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs text-slate-600 hover:bg-slate-50">
              Cancel
            </button>
            {/* Assigning and then checking in is one motion at the desk. */}
            <button onClick={() => assign(true)} disabled={!roomId || busy}
              className="flex items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-semibold text-slate-600 enabled:hover:bg-slate-50 disabled:opacity-40">
              <LogIn size={13} /> Check-in
            </button>
            <button onClick={() => assign(false)} disabled={!roomId || busy}
              className="flex items-center gap-1.5 rounded-lg bg-brand px-3 py-1.5 text-xs font-semibold text-white enabled:hover:bg-brand/90 disabled:opacity-40">
              {busy && <Loader2 size={13} className="animate-spin" />} Assign Room
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

function DateChip({ iso, muted }: { iso: string; muted?: boolean }) {
  return (
    <span className={`block w-11 ${muted ? 'text-slate-400' : 'text-brand'}`}>
      <span className="block text-sm font-bold leading-none">{dayNum(iso)}</span>
      <span className="block text-[9px] leading-tight">{monthShort(iso)}</span>
    </span>
  )
}
