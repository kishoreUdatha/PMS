import { useState, type ReactNode } from 'react'
import Select from '../components/Select'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Building2, Layers, Plus, ArrowUpDown, ChevronDown, ChevronRight, GripVertical,
  Loader2, Info, Ban, ArrowLeftRight, Save, X, MoreHorizontal,
  AlertTriangle,
} from 'lucide-react'
import {
  listBuildings, createBuilding, updateBuilding, getFloor, getFloorDependents,
  createFloor, updateFloor, moveFloorRooms, reorderBuildings, reorderFloors,
  listUnassignedRooms, assignRoomsToFloor,
  type BuildingRow, type FloorRow,
} from '../api'
import { errorText } from '../lib/forms'

/** Screen 061 — Buildings & Floors. */

const input = 'w-full rounded-lg border border-slate-200 px-3 py-2 text-sm'

function Label({ children, required }: { children: React.ReactNode; required?: boolean }) {
  return (
    <span className="mb-1 block text-sm font-medium text-slate-700">
      {children} {required && <span className="text-red-500">*</span>}
    </span>
  )
}

function apiError(e: unknown): string {
  return errorText(e, 'Could not save. Please try again.')
}

function StatusPill({ status }: { status: string }) {
  return (
    <span
      className={`rounded-full px-2.5 py-1 text-xs font-semibold ${
        status === 'active'
          ? 'bg-emerald-100 text-emerald-700'
          : 'bg-slate-75 text-slate-500'
      }`}
    >
      {status === 'active' ? 'Active' : 'Inactive'}
    </span>
  )
}

/* ---------------------------------------------------------------- tree --- */
function Tree({
  buildings, selectedFloorId, reordering, onSelectFloor, onAddFloor,
  onToggleBuilding, onMoveBuilding, onMoveFloor,
}: {
  buildings: BuildingRow[]
  selectedFloorId: string | null
  reordering: boolean
  onSelectFloor: (f: FloorRow) => void
  onAddFloor: (b: BuildingRow) => void
  onToggleBuilding: (b: BuildingRow) => void
  onMoveBuilding: (index: number, dir: -1 | 1) => void
  onMoveFloor: (b: BuildingRow, index: number, dir: -1 | 1) => void
}) {
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({})

  return (
    <div className="divide-y divide-slate-100 overflow-hidden rounded-xl border border-slate-200 bg-white">
      {buildings.map((b, bi) => {
        const isOpen = !collapsed[b.id]
        return (
          <div key={b.id}>
            <div className="flex items-center gap-3 px-4 py-3">
              <button
                onClick={() => setCollapsed((c) => ({ ...c, [b.id]: isOpen }))}
                className="text-slate-400 hover:text-slate-600"
                aria-label={isOpen ? 'Collapse building' : 'Expand building'}
              >
                {isOpen ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
              </button>
              <Building2 className="h-5 w-5 shrink-0 text-brand" />
              <div className="min-w-0 flex-1">
                <p className="font-semibold text-slate-800">{b.name}</p>
                <p className="text-sm text-slate-500">
                  {b.floor_count} {b.floor_count === 1 ? 'Floor' : 'Floors'} &nbsp;|&nbsp;{' '}
                  {b.room_count} Rooms
                </p>
              </div>
              <StatusPill status={b.status} />
              {reordering ? (
                <span className="flex flex-col">
                  <button onClick={() => onMoveBuilding(bi, -1)} disabled={bi === 0}
                    className="text-slate-400 hover:text-brand disabled:opacity-30"
                    aria-label="Move building up">▲</button>
                  <button onClick={() => onMoveBuilding(bi, 1)}
                    disabled={bi === buildings.length - 1}
                    className="text-slate-400 hover:text-brand disabled:opacity-30"
                    aria-label="Move building down">▼</button>
                </span>
              ) : (
                <button onClick={() => onToggleBuilding(b)}
                  title={b.status === 'active' ? 'Deactivate building' : 'Activate building'}
                  className="text-slate-400 hover:text-slate-600">
                  <MoreHorizontal className="h-4 w-4" />
                </button>
              )}
            </div>

            {isOpen && (
              <div className="bg-slate-50/40">
                {b.floors.map((f, fi) => {
                  const selected = f.id === selectedFloorId
                  return (
                    <button
                      key={f.id}
                      onClick={() => onSelectFloor(f)}
                      className={`flex w-full items-center gap-3 px-4 py-2.5 text-left ${
                        selected ? 'bg-brand-light' : 'hover:bg-slate-50'
                      }`}
                    >
                      {reordering ? (
                        <span className="flex flex-col text-xs">
                          <span role="button" onClick={(e) => { e.stopPropagation(); onMoveFloor(b, fi, -1) }}
                            className={`${fi === 0 ? 'opacity-30' : 'hover:text-brand'} text-slate-400`}>▲</span>
                          <span role="button" onClick={(e) => { e.stopPropagation(); onMoveFloor(b, fi, 1) }}
                            className={`${fi === b.floors.length - 1 ? 'opacity-30' : 'hover:text-brand'} text-slate-400`}>▼</span>
                        </span>
                      ) : (
                        <GripVertical className="h-4 w-4 shrink-0 text-slate-300" />
                      )}
                      <Layers className="h-4 w-4 shrink-0 text-slate-400" />
                      <span className="min-w-0 flex-1">
                        <span className="block font-semibold text-slate-800">
                          {f.name} ({f.code})
                        </span>
                        <span className="block text-sm text-slate-500">
                          {f.from_room_no && f.to_room_no
                            ? `Rooms ${f.from_room_no} - ${f.to_room_no}`
                            : 'No room range set'}{' '}
                          &nbsp;|&nbsp; {f.room_count} Rooms
                        </span>
                      </span>
                      {f.status !== 'active' && <StatusPill status={f.status} />}
                    </button>
                  )
                })}
                <button onClick={() => onAddFloor(b)}
                  className="flex w-full items-center gap-2 px-4 py-2.5 text-sm font-semibold text-brand hover:bg-brand-light">
                  <Plus className="h-4 w-4" /> Add floor to {b.name}
                </button>
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

/* -------------------------------------------------------------- editor --- */
interface FloorForm {
  building_id: string; code: string; name: string
  from_room_no: string; to_room_no: string
  display_order: number; status: string
}

function FloorEditor({
  propertyId, floorId, draftBuilding, buildings, onClose,
}: {
  propertyId: string
  floorId: string | null
  draftBuilding: BuildingRow | null
  buildings: BuildingRow[]
  onClose: () => void
}) {
  const qc = useQueryClient()
  const isEdit = floorId !== null
  const [form, setForm] = useState<FloorForm | null>(
    isEdit
      ? null
      : {
          building_id: draftBuilding?.id ?? '',
          code: '', name: '', from_room_no: '', to_room_no: '',
          display_order: (draftBuilding?.floor_count ?? 0) + 1,
          status: 'active',
        },
  )
  const [error, setError] = useState<string | null>(null)
  const [showDeps, setShowDeps] = useState(false)
  const [moveTarget, setMoveTarget] = useState('')

  const detailQ = useQuery({
    queryKey: ['floor', propertyId, floorId],
    queryFn: () => getFloor(propertyId, floorId!),
    enabled: isEdit,
  })
  const depsQ = useQuery({
    queryKey: ['floorDeps', propertyId, floorId],
    queryFn: () => getFloorDependents(propertyId, floorId!),
    enabled: isEdit && showDeps,
  })

  const d = detailQ.data
  const f =
    form ??
    (d
      ? {
          building_id: d.building_id, code: d.code, name: d.name,
          from_room_no: d.from_room_no ?? '', to_room_no: d.to_room_no ?? '',
          display_order: d.display_order, status: d.status,
        }
      : null)

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['buildings', propertyId] })
    qc.invalidateQueries({ queryKey: ['floor', propertyId, floorId] })
    qc.invalidateQueries({ queryKey: ['rooms', propertyId] })
    qc.invalidateQueries({ queryKey: ['roomFacets', propertyId] })
  }

  const body = f && {
    building_id: f.building_id,
    code: f.code,
    name: f.name,
    from_room_no: f.from_room_no || null,
    to_room_no: f.to_room_no || null,
    display_order: Number(f.display_order),
    status: f.status,
  }

  const save = useMutation({
    mutationFn: () =>
      isEdit
        ? updateFloor(propertyId, floorId!, { ...body!, version: d!.version })
        : createFloor(propertyId, body!),
    onSuccess: () => { invalidate(); onClose() },
    onError: (e) => setError(apiError(e)),
  })
  const deactivate = useMutation({
    mutationFn: () =>
      updateFloor(propertyId, floorId!, {
        status: d!.status === 'active' ? 'inactive' : 'active',
        version: d!.version,
        reason: 'Changed from Buildings & Floors',
      }),
    onSuccess: () => { invalidate(); onClose() },
    // Blocked by live reservations comes back as 409 with the count.
    onError: (e) => setError(apiError(e)),
  })
  const move = useMutation({
    mutationFn: () =>
      moveFloorRooms(propertyId, floorId!, {
        target_floor_id: moveTarget,
        reason: 'Moved from Buildings & Floors',
      }),
    onSuccess: () => { setError(null); invalidate() },
    onError: (e) => setError(apiError(e)),
  })

  if (!f) {
    return (
      <div className="flex justify-center rounded-xl border border-slate-200 bg-white py-24 text-slate-400">
        <Loader2 className="h-6 w-6 animate-spin" />
      </div>
    )
  }

  const set = <K extends keyof FloorForm>(k: K, v: FloorForm[K]) => setForm({ ...f, [k]: v })
  const otherFloors = buildings.flatMap((b) => b.floors).filter((x) => x.id !== floorId)

  // Total Rooms follows the typed range (111-120 = 10), live. Room numbers are
  // free text ("A201"), so classify the range rather than silently showing a
  // dash: a backwards range is a real error, a non-numeric one simply cannot
  // be counted.
  const rawFrom = f.from_room_no.trim()
  const rawTo = f.to_room_no.trim()
  const from = Number(rawFrom)
  const to = Number(rawTo)
  const bothNumeric =
    rawFrom !== '' && rawTo !== '' && Number.isInteger(from) && Number.isInteger(to)

  const rangeState: 'ok' | 'incomplete' | 'non-numeric' | 'backwards' =
    rawFrom === '' || rawTo === ''
      ? 'incomplete'
      : !bothNumeric
        ? 'non-numeric'
        : to < from
          ? 'backwards'
          : 'ok'

  const rangeCount = rangeState === 'ok' ? to - from + 1 : null
  const linked = d?.room_count ?? 0
  const mismatch = isEdit && rangeCount !== null && rangeCount !== linked

  return (
    <div className="space-y-5 rounded-xl border border-slate-200 bg-white p-6">
      <div className="flex items-start justify-between">
        <div>
          <h2 className="text-lg font-semibold text-ink">
            {isEdit ? 'Edit Floor' : 'Add Floor'}
          </h2>
          <p className="text-sm text-slate-500">
            Update floor details, room range and display order.
          </p>
        </div>
        <div className="flex items-center gap-3">
          {isEdit && d && <StatusPill status={d.status} />}
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600">
            <X className="h-5 w-5" />
          </button>
        </div>
      </div>

      <div className="grid grid-cols-3 gap-4">
        <label>
          <Label required>Building</Label>
          <Select value={f.building_id} disabled={isEdit}
            onChange={(e) => set('building_id', e.target.value)}
            className={`${input} disabled:bg-slate-50`}>
            {buildings.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
          </Select>
        </label>
        <label>
          <Label required>Floor Name</Label>
          <input value={f.name} onChange={(e) => set('name', e.target.value)}
            className={input} />
        </label>
        <label>
          <Label required>Floor Code</Label>
          <input value={f.code} onChange={(e) => set('code', e.target.value)}
            className={input} />
          <span className="mt-1 block text-xs text-slate-500">
            Shown in room numbers (e.g. 1 for 111–120)
          </span>
        </label>
      </div>

      <div className="grid grid-cols-3 gap-4">
        <label>
          <Label required>From Room No.</Label>
          <input value={f.from_room_no}
            onChange={(e) => set('from_room_no', e.target.value)} className={input} />
        </label>
        <label>
          <Label required>To Room No.</Label>
          <input value={f.to_room_no}
            onChange={(e) => set('to_room_no', e.target.value)} className={input} />
        </label>
        <label>
          <Label>Total Rooms</Label>
          <input
            value={rangeCount ?? '—'}
            readOnly
            className={`${input} bg-slate-50 ${
              rangeState === 'backwards'
                ? 'border-red-300 text-red-700'
                : mismatch
                  ? 'text-amber-700'
                  : 'text-slate-500'
            }`}
          />
          <span
            className={`mt-1 block text-xs ${
              rangeState === 'backwards'
                ? 'text-red-600'
                : mismatch
                  ? 'text-amber-700'
                  : 'text-slate-500'
            }`}
          >
            {rangeState === 'backwards'
              ? `To Room No. (${rawTo}) is before From Room No. (${rawFrom}).`
              : rangeState === 'non-numeric'
                ? 'Room numbers are not numeric, so the total cannot be calculated.'
                : rangeState === 'incomplete'
                  ? 'Enter From and To to calculate.'
                  : mismatch
                    ? `Range implies ${rangeCount}, but ${linked} room${
                        linked === 1 ? ' is' : 's are'
                      } linked to this floor.`
                    : isEdit
                      ? `${linked} room${linked === 1 ? '' : 's'} linked to this floor.`
                      : 'Calculated from the room range.'}
          </span>
        </label>
      </div>

      <div className="grid grid-cols-3 gap-4">
        <label>
          <Label>Display Order</Label>
          <input type="number" min={0} value={f.display_order}
            onChange={(e) => set('display_order', Number(e.target.value))}
            className={input} />
          <span className="mt-1 block text-xs text-slate-500">
            Lower numbers appear first in the list.
          </span>
        </label>
        <label>
          <Label>Status</Label>
          <Select value={f.status} onChange={(e) => set('status', e.target.value)}
            className={input}>
            <option value="active">Active</option>
            <option value="inactive">Inactive</option>
          </Select>
        </label>
      </div>

      {isEdit && d && (
        <div className="border-t border-slate-100 pt-4">
          <p className="mb-2 text-sm font-semibold text-slate-700">Audit Information</p>
          <div className="grid grid-cols-2 gap-x-8 gap-y-1 text-sm">
            <p className="text-slate-500">
              Created By <span className="ml-3 text-slate-700">{d.created_by_name ?? '—'}</span>
            </p>
            <p className="text-slate-500">
              Last Modified By <span className="ml-3 text-slate-700">{d.updated_by_name ?? '—'}</span>
            </p>
            <p className="text-slate-500">
              Created On <span className="ml-3 text-slate-700">
                {d.created_at?.slice(0, 16).replace('T', ' ') ?? '—'}</span>
            </p>
            <p className="text-slate-500">
              Last Modified On <span className="ml-3 text-slate-700">
                {d.updated_at?.slice(0, 16).replace('T', ' ') ?? '—'}</span>
            </p>
          </div>
        </div>
      )}

      {isEdit && d && !d.can_deactivate && (
        <div className="flex items-start gap-3 rounded-lg bg-sky-50 px-4 py-3">
          <Info className="mt-0.5 h-5 w-5 shrink-0 text-sky-500" />
          <div className="flex-1">
            <p className="font-semibold text-sky-900">Cannot deactivate this floor</p>
            <p className="text-sm text-sky-800">{d.blocker_message}</p>
          </div>
          <button onClick={() => setShowDeps((v) => !v)}
            className="shrink-0 rounded-lg border border-sky-300 bg-white px-3 py-2 text-sm font-semibold text-sky-800 hover:bg-sky-100">
            {showDeps ? 'Hide' : 'View'} Dependent Rooms
          </button>
        </div>
      )}

      {showDeps && (
        <div className="overflow-hidden rounded-lg border border-slate-200">
          <table className="w-full text-sm">
            <thead className="border-b border-slate-100 bg-slate-50/60 text-left text-sm text-slate-600">
              <tr>
                {['Room', 'Type', 'Status', 'Service', 'Active Reservations'].map((h) => (
                  <th key={h} className="px-3 py-2 font-semibold">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {(depsQ.data ?? []).map((r) => (
                <tr key={r.id}>
                  <td className="px-3 py-2 font-semibold text-slate-800">{r.code}</td>
                  <td className="px-3 py-2 text-slate-600">{r.room_type_name}</td>
                  <td className="px-3 py-2 text-slate-600">{r.status}</td>
                  <td className="px-3 py-2 text-slate-600">{r.service_status}</td>
                  <td className="px-3 py-2 text-slate-600">{r.active_reservations}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {error && (
        <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>
      )}

      <div className="flex flex-wrap items-center gap-3 border-t border-slate-100 pt-4">
        {isEdit && (
          <>
            <div className="flex items-center gap-2">
              <Select value={moveTarget} onChange={(e) => setMoveTarget(e.target.value)}
                className={`${input} w-48`}>
                <option value="">Move rooms to…</option>
                {otherFloors.map((x) => (
                  <option key={x.id} value={x.id}>{x.name} ({x.code})</option>
                ))}
              </Select>
              <button onClick={() => { setError(null); move.mutate() }}
                disabled={!moveTarget || move.isPending}
                className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-50">
                <ArrowLeftRight className="h-4 w-4" /> Move Rooms
              </button>
            </div>
            <button onClick={() => { setError(null); deactivate.mutate() }}
              disabled={deactivate.isPending || (d?.status === 'active' && !d?.can_deactivate)}
              title={d?.status === 'active' && !d?.can_deactivate ? d?.blocker_message ?? '' : ''}
              className="inline-flex items-center gap-1.5 rounded-lg border border-red-300 px-4 py-2 text-sm font-semibold text-red-600 hover:bg-red-50 disabled:opacity-40">
              <Ban className="h-4 w-4" />
              {d?.status === 'active' ? 'Deactivate' : 'Activate'}
            </button>
          </>
        )}
        <button onClick={() => { setError(null); save.mutate() }}
          disabled={save.isPending || !f.name || !f.code || rangeState === 'backwards'}
          className="ml-auto inline-flex items-center gap-2 rounded-lg bg-brand px-5 py-2 text-sm font-semibold text-white hover:bg-brand/90 disabled:opacity-50">
          {save.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
          {isEdit ? 'Save Changes' : 'Create Floor'}
        </button>
      </div>
    </div>
  )
}

/* ------------------------------------------------------------ building --- */
function BuildingDialog({
  propertyId, onClose,
}: { propertyId: string; onClose: () => void }) {
  const qc = useQueryClient()
  const [form, setForm] = useState({ code: '', name: '', display_order: 0 })
  const [error, setError] = useState<string | null>(null)

  const save = useMutation({
    mutationFn: () => createBuilding(propertyId, { ...form, status: 'active' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['buildings', propertyId] })
      onClose()
    },
    onError: (e) => setError(apiError(e)),
  })

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-slate-900/30 p-4">
      <div className="w-full max-w-md rounded-xl bg-white shadow-lg">
        <div className="flex items-center justify-between border-b border-slate-200 px-6 py-4">
          <h2 className="text-lg font-semibold text-ink">Add Building</h2>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600">
            <X className="h-5 w-5" />
          </button>
        </div>
        <div className="space-y-4 px-6 py-5">
          <label className="block">
            <Label required>Building Name</Label>
            <input value={form.name} placeholder="Seaside Block"
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              className={input} />
          </label>
          <label className="block">
            <Label required>Building Code</Label>
            <input value={form.code} placeholder="SEA"
              onChange={(e) => setForm({ ...form, code: e.target.value.toUpperCase() })}
              className={input} />
          </label>
          <label className="block">
            <Label>Display Order</Label>
            <input type="number" min={0} value={form.display_order}
              onChange={(e) => setForm({ ...form, display_order: Number(e.target.value) })}
              className={input} />
          </label>
          {error && (
            <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>
          )}
        </div>
        <div className="flex justify-end gap-3 border-t border-slate-200 px-6 py-4">
          <button onClick={onClose}
            className="rounded-lg border border-slate-200 px-4 py-2 text-sm font-semibold text-slate-600">
            Cancel
          </button>
          <button onClick={() => { setError(null); save.mutate() }}
            disabled={save.isPending || !form.name || !form.code}
            className="inline-flex items-center gap-2 rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white hover:bg-brand/90 disabled:opacity-50">
            {save.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
            Create Building
          </button>
        </div>
      </div>
    </div>
  )
}

/* ----------------------------------------------------------------- page --- */
/**
 * Rooms that belong to no floor.
 *
 * A floor can only list what points at it, so a room with no floor appears
 * nowhere on this tree — real, bookable rooms that the estate screen refuses
 * to admit exist. Worse, "Move Rooms" moves rooms *off* a floor, so it cannot
 * reach them either. This is the only way back.
 */
function UnassignedRooms({ propertyId, buildings }: {
  propertyId: string
  buildings: BuildingRow[]
}) {
  const qc = useQueryClient()
  const [target, setTarget] = useState('')
  const [err, setErr] = useState('')

  const { data: rooms } = useQuery({
    queryKey: ['unassigned-rooms', propertyId],
    queryFn: () => listUnassignedRooms(propertyId),
    enabled: propertyId !== '',
  })

  const assign = useMutation({
    mutationFn: (ids: string[]) =>
      assignRoomsToFloor(propertyId, target, {
        room_ids: ids, reason: 'assigned from unallocated rooms',
      }),
    onSuccess: () => {
      setErr('')
      qc.invalidateQueries({ queryKey: ['unassigned-rooms', propertyId] })
      qc.invalidateQueries({ queryKey: ['buildings', propertyId] })
    },
    onError: (e) => setErr(apiError(e)),
  })

  if (!rooms || rooms.length === 0) return null

  const floors = buildings.flatMap((b) =>
    b.floors.map((f) => ({ id: f.id, label: `${b.name} — ${f.name}` })))

  return (
    <div className="rounded-xl border border-amber-200 bg-amber-50/60 p-4">
      <div className="flex items-start gap-2">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-semibold text-caution">
            {rooms.length} room{rooms.length === 1 ? '' : 's'} on no floor
          </h3>
          <p className="mt-0.5 text-xs text-amber-700">
            These rooms exist and can be booked, but no floor claims them, so
            they are missing from every count above.
          </p>

          <div className="mt-3 flex flex-wrap gap-1.5">
            {rooms.map((r) => (
              <span key={r.id}
                title={`${r.room_type ?? 'no room type'}${
                  r.floor_hint ? ` · labelled floor ${r.floor_hint}` : ''}${
                  r.building_hint ? ` · labelled ${r.building_hint}` : ''}`}
                className="rounded-md border border-amber-300 bg-white px-2 py-0.5 font-mono text-xs text-slate-700">
                {r.code}
              </span>
            ))}
          </div>

          {floors.length === 0 ? (
            <p className="mt-3 text-xs text-amber-700">
              Add a floor first — there is nowhere to put them.
            </p>
          ) : (
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <Select value={target} onChange={(e) => setTarget(e.target.value)}
                className={`${input} w-auto min-w-[16rem] bg-white`}>
                <option value="">Choose a floor…</option>
                {floors.map((f) => (
                  <option key={f.id} value={f.id}>{f.label}</option>
                ))}
              </Select>
              <button
                disabled={!target || assign.isPending}
                onClick={() => assign.mutate(rooms.map((r) => r.id))}
                className="inline-flex items-center gap-2 rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white hover:bg-brand/90 disabled:opacity-50">
                {assign.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
                Assign all {rooms.length}
              </button>
            </div>
          )}
          {err && <p className="mt-2 text-xs text-red-600">{err}</p>}
        </div>
      </div>
    </div>
  )
}

export default function BuildingsFloors({ propertyId, header }: {
  propertyId: string
  /** Draws the page title with this screen's actions beside it. Without it
   *  the actions sit on their own row, right-aligned. */
  header?: (actions: ReactNode) => ReactNode
}) {
  const qc = useQueryClient()
  const [selected, setSelected] = useState<string | null>(null)
  const [draftBuilding, setDraftBuilding] = useState<BuildingRow | null>(null)
  const [addingBuilding, setAddingBuilding] = useState(false)
  const [reordering, setReordering] = useState(false)

  const buildingsQ = useQuery({
    queryKey: ['buildings', propertyId],
    queryFn: () => listBuildings(propertyId),
    enabled: propertyId !== '',
  })
  const buildings = buildingsQ.data ?? []

  const toggleBuilding = useMutation({
    mutationFn: (b: BuildingRow) =>
      updateBuilding(propertyId, b.id, {
        status: b.status === 'active' ? 'inactive' : 'active',
        version: b.version,
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['buildings', propertyId] }),
  })
  const reorderB = useMutation({
    mutationFn: (ids: string[]) => reorderBuildings(propertyId, ids),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['buildings', propertyId] }),
  })
  const reorderF = useMutation({
    mutationFn: (ids: string[]) => reorderFloors(propertyId, ids),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['buildings', propertyId] }),
  })

  const moveBuilding = (index: number, dir: -1 | 1) => {
    const ids = buildings.map((b) => b.id)
    const to = index + dir
    if (to < 0 || to >= ids.length) return
    ;[ids[index], ids[to]] = [ids[to], ids[index]]
    reorderB.mutate(ids)
  }
  const moveFloor = (b: BuildingRow, index: number, dir: -1 | 1) => {
    const ids = b.floors.map((f) => f.id)
    const to = index + dir
    if (to < 0 || to >= ids.length) return
    ;[ids[index], ids[to]] = [ids[to], ids[index]]
    reorderF.mutate(ids)
  }

  const panelOpen = selected !== null || draftBuilding !== null

  if (buildingsQ.isError) {
    return (
      <div className="space-y-4">
        {header?.(null)}
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-6 text-center text-sm text-red-700">
          Could not load buildings. You may not have permission to view this module.
        </div>
      </div>
    )
  }

  // The state these act on lives here, so the page title is drawn through
  // `header` rather than the actions being lifted out to it.
  const actions = (
      <div className="flex flex-wrap items-center gap-2">
        <button onClick={() => setReordering((v) => !v)} aria-pressed={reordering}
          className={`flex items-center gap-1.5 whitespace-nowrap rounded-lg border px-3 py-2 text-sm font-medium ${
            reordering
              ? 'border-brand bg-brand text-white hover:bg-brand-dark'
              : 'border-slate-200 bg-white text-slate-600 hover:bg-slate-50'
          }`}>
          <ArrowUpDown size={15} /> {reordering ? 'Done' : 'Reorder'}
        </button>
        <button onClick={() => setAddingBuilding(true)}
          className="flex items-center gap-1.5 whitespace-nowrap rounded-lg bg-brand px-3 py-2 text-sm font-medium text-white hover:bg-brand-dark disabled:opacity-40">
          <Plus size={15} /> Add Building
        </button>
      </div>
  )

  return (
    <div className="space-y-4">
      {header ? header(actions) : <div className="flex justify-end">{actions}</div>}
      <UnassignedRooms propertyId={propertyId} buildings={buildings} />

      {buildingsQ.isLoading ? (
        <div className="flex justify-center py-24 text-slate-400">
          <Loader2 className="h-6 w-6 animate-spin" />
        </div>
      ) : buildings.length === 0 ? (
        <div className="rounded-xl border border-slate-200 bg-white px-4 py-16 text-center">
          <p className="font-medium text-slate-700">No buildings yet.</p>
          <p className="mt-1 text-sm text-slate-500">
            Add a building to start organising floors and room ranges.
          </p>
        </div>
      ) : (
        <div className={panelOpen ? 'grid gap-4 xl:grid-cols-[minmax(0,480px)_1fr]' : ''}>
          <Tree
            buildings={buildings}
            selectedFloorId={selected}
            reordering={reordering}
            onSelectFloor={(f) => { setDraftBuilding(null); setSelected(f.id) }}
            onAddFloor={(b) => { setSelected(null); setDraftBuilding(b) }}
            onToggleBuilding={(b) => toggleBuilding.mutate(b)}
            onMoveBuilding={moveBuilding}
            onMoveFloor={moveFloor}
          />
          {panelOpen && (
            <FloorEditor
              propertyId={propertyId}
              floorId={selected}
              draftBuilding={draftBuilding}
              buildings={buildings}
              onClose={() => { setSelected(null); setDraftBuilding(null) }}
            />
          )}
        </div>
      )}

      {addingBuilding && (
        <BuildingDialog propertyId={propertyId} onClose={() => setAddingBuilding(false)} />
      )}
    </div>
  )
}
