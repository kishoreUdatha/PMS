import { useMemo, useState } from 'react'
import { fmtMonthYear } from '../lib/dates'
import Select from '../components/Select'
import { FILTER_SELECT } from '../lib/controls'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import DateField from '../components/DateField'
import {
  Bed, Wrench, CalendarCheck, BarChart3, Plus, Loader2, Search, X,
  CalendarDays, List, Ban, Hourglass, AlertTriangle, Info,
} from 'lucide-react'
import {
  listBlocks, getBlockStats, getBlockableRooms, createBlock, endBlock,
  cancelBlock, listManagedRoomTypes, BLOCK_REASONS,
  type BlockGroup, type BlockStats, type BlockConflict,
} from '../api'
import { errorText } from '../lib/forms'

/** Screen 063 — Room Block and Out of Order. */

type IconType = React.ComponentType<{ className?: string }>

const input = 'w-full rounded-lg border border-slate-200 px-3 py-2 text-sm'
const filterSelect = `${FILTER_SELECT} bg-white outline-none focus:border-brand`

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

const today = () => new Date().toISOString().slice(0, 10)

/** Bar colour follows the mockup legend. */
function barClass(g: BlockGroup): string {
  if (g.block_type === 'out_of_order') return 'bg-red-200 text-red-900'
  if (g.reason_category.startsWith('maintenance')) return 'bg-amber-200 text-amber-900'
  return 'bg-sky-200 text-sky-900'
}

function Kpi({
  icon: Icon, value, label, tint,
}: { icon: IconType; value: number; label: string; tint: string }) {
  return (
    <div className={`flex items-center gap-4 rounded-xl border border-slate-200 p-4 ${tint}`}>
      <Icon className="h-7 w-7 shrink-0" />
      <div>
        <p className="text-2xl font-bold leading-tight text-slate-800">{value}</p>
        <p className="text-base font-semibold text-slate-700">{label}</p>
      </div>
    </div>
  )
}

function KpiRow({ s }: { s: BlockStats }) {
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
      <Kpi icon={Bed} value={s.currently_blocked} label="Currently Blocked"
        tint="bg-sky-50 text-sky-600" />
      <Kpi icon={Wrench} value={s.out_of_order} label="Out of Order"
        tint="bg-red-50 text-red-500" />
      <Kpi icon={CalendarCheck} value={s.due_to_end_today} label="Due to End Today"
        tint="bg-emerald-50 text-emerald-600" />
      <Kpi icon={BarChart3} value={s.total_unavailable} label="Total Rooms Unavailable"
        tint="bg-amber-50 text-amber-600" />
    </div>
  )
}

/* --------------------------------------------------------------- calendar --- */
function CalendarView({ groups, month }: { groups: BlockGroup[]; month: Date }) {
  const first = new Date(month.getFullYear(), month.getMonth(), 1)
  const start = new Date(first)
  start.setDate(first.getDate() - first.getDay())          // back to Sunday
  const days = Array.from({ length: 42 }, (_, i) => {
    const d = new Date(start)
    d.setDate(start.getDate() + i)
    return d
  })
  const iso = (d: Date) => d.toISOString().slice(0, 10)

  return (
    <div className="overflow-hidden rounded-lg border border-slate-200">
      <div className="grid grid-cols-7 border-b border-slate-200 bg-slate-50/60">
        {['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].map((d) => (
          <div key={d} className="px-2 py-2 text-center text-sm font-semibold text-slate-600">
            {d}
          </div>
        ))}
      </div>
      <div className="grid grid-cols-7">
        {days.map((d) => {
          const key = iso(d)
          const inMonth = d.getMonth() === month.getMonth()
          const onThisDay = groups.filter(
            (g) => g.start_date <= key && key <= g.end_date,
          )
          return (
            <div key={key}
              className={`min-h-[86px] border-b border-r border-slate-100 p-1.5 ${
                inMonth ? '' : 'bg-slate-50/50 text-slate-300'
              }`}>
              <div className="mb-1 text-right text-xs text-slate-500">{d.getDate()}</div>
              <div className="space-y-1">
                {onThisDay.slice(0, 3).map((g) => (
                  <div key={g.group_id}
                    title={`${g.rooms_label} | ${g.reason_category_label}`}
                    className={`truncate rounded px-1.5 py-0.5 text-[11px] font-medium ${barClass(g)}`}>
                    {g.rooms_label} | {g.reason_category_label}
                  </div>
                ))}
                {onThisDay.length > 3 && (
                  <div className="px-1.5 text-[11px] text-slate-500">
                    +{onThisDay.length - 3} more
                  </div>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------ page --- */
export default function RoomBlocks({ propertyId }: { propertyId: string }) {
  const qc = useQueryClient()
  const [view, setView] = useState<'calendar' | 'list'>('calendar')
  const [month, setMonth] = useState(() => new Date())
  const [statusFilter, setStatusFilter] = useState('active')
  const [roomTypeId, setRoomTypeId] = useState('')
  const [search, setSearch] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [conflicts, setConflicts] = useState<BlockConflict[]>([])
  const [notice, setNotice] = useState<string | null>(null)

  const [form, setForm] = useState({
    block_type: 'room_block',
    room_ids: [] as string[],
    reason_category: 'maintenance_scheduled',
    reason: '',
    severity: 'medium',
    start_date: today(),
    end_date: today(),
    linked_reference: '',
  })

  const filters = {
    status: statusFilter || undefined,
    room_type_id: roomTypeId || undefined,
    search: search || undefined,
  }
  const blocksQ = useQuery({
    queryKey: ['roomBlocks', propertyId, filters],
    queryFn: () => listBlocks(propertyId, filters),
    enabled: propertyId !== '',
  })
  const statsQ = useQuery({
    queryKey: ['blockStats', propertyId],
    queryFn: () => getBlockStats(propertyId),
    enabled: propertyId !== '',
  })
  const roomsQ = useQuery({
    queryKey: ['blockableRooms', propertyId],
    queryFn: () => getBlockableRooms(propertyId),
    enabled: propertyId !== '',
  })
  const typesQ = useQuery({
    queryKey: ['managedRoomTypes', propertyId, {}],
    queryFn: () => listManagedRoomTypes(propertyId),
    enabled: propertyId !== '',
  })

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['roomBlocks', propertyId] })
    qc.invalidateQueries({ queryKey: ['blockStats', propertyId] })
    qc.invalidateQueries({ queryKey: ['rooms', propertyId] })
  }

  const create = useMutation({
    mutationFn: () =>
      createBlock(propertyId, {
        ...form,
        reason: form.reason || null,
        linked_reference: form.linked_reference || null,
      }),
    onSuccess: (res) => {
      setError(null)
      setConflicts(res.conflicts)
      setNotice(
        res.created.length
          ? `Blocked ${res.created.length} room(s).`
          : 'No rooms could be blocked.',
      )
      if (res.created.length) setForm((f) => ({ ...f, room_ids: [] }))
      invalidate()
    },
    onError: (e) => { setConflicts([]); setError(apiError(e)) },
  })
  const end = useMutation({
    mutationFn: (g: BlockGroup) =>
      endBlock(propertyId, g.group_id, { end_date: today(), reason: 'Ended early' }),
    onSuccess: () => { setNotice('Block ended; the rooms are back on sale.'); invalidate() },
    onError: (e) => setError(apiError(e)),
  })
  const cancel = useMutation({
    mutationFn: (g: BlockGroup) => cancelBlock(propertyId, g.group_id, { reason: 'Cancelled' }),
    onSuccess: () => { setNotice('Block cancelled.'); invalidate() },
    onError: (e) => setError(apiError(e)),
  })

  const groups = blocksQ.data ?? []
  const rooms = roomsQ.data ?? []
  const selected = useMemo(
    () => rooms.filter((r) => form.room_ids.includes(r.id)),
    [rooms, form.room_ids],
  )
  const toggleRoom = (id: string) =>
    setForm((f) => ({
      ...f,
      room_ids: f.room_ids.includes(id)
        ? f.room_ids.filter((x) => x !== id)
        : [...f.room_ids, id],
    }))

  const monthLabel = fmtMonthYear(month)
  const shiftMonth = (d: number) =>
    setMonth((m) => new Date(m.getFullYear(), m.getMonth() + d, 1))

  if (blocksQ.isError) {
    return (
      <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-6 text-center text-sm text-red-700">
        Could not load blocks. You may not have permission to view this module.
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {statsQ.data && <KpiRow s={statsQ.data} />}

      <div className="grid gap-4 xl:grid-cols-[1fr_420px]">
        {/* ---------------- calendar / list ---------------- */}
        <div className="space-y-4 rounded-xl border border-slate-200 bg-white p-4">
          <div className="flex gap-6 border-b border-slate-200">
            {([['calendar', 'Calendar View', CalendarDays], ['list', 'List View', List]] as const)
              .map(([key, label, Icon]) => (
                <button key={key} onClick={() => setView(key)}
                  className={`-mb-px flex items-center gap-2 border-b-2 px-1 pb-2.5 text-sm ${
                    view === key
                      ? 'border-brand font-semibold text-brand'
                      : 'border-transparent font-normal text-slate-500 hover:text-slate-700'
                  }`}>
                  <Icon className="h-4 w-4" /> {label}
                </button>
              ))}
          </div>

          {/* One row, no captions: each select's first option says what it
              filters. */}
          <div className="flex flex-wrap items-center gap-2">
            <Select blankIsChoice value={roomTypeId} onChange={(e) => setRoomTypeId(e.target.value)}
              aria-label="Room type" className={filterSelect}>
              <option value="">All Room Types</option>
              {(typesQ.data ?? []).map((t) => (
                <option key={t.id} value={t.id}>{t.name}</option>
              ))}
            </Select>
            <Select blankIsChoice value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}
              aria-label="Status" className={filterSelect}>
              <option value="">All Statuses</option>
              <option value="active">Active</option>
              <option value="ended">Ended</option>
              <option value="cancelled">Cancelled</option>
            </Select>
            <div className="flex min-w-[240px] flex-1 items-center justify-end gap-2">
              <div className="relative min-w-0 max-w-sm flex-1">
                <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                <input value={search} onChange={(e) => setSearch(e.target.value)}
                  placeholder="Search by room, reason or reference..."
                  className="w-full rounded-lg border border-slate-200 bg-slate-50 py-2 pl-9 pr-3 text-sm outline-none focus:border-brand" />
              </div>
            </div>
          </div>

          {view === 'calendar' ? (
            <>
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <button onClick={() => shiftMonth(-1)}
                    className="rounded-md border border-slate-200 px-2 py-1 text-slate-600 hover:bg-slate-50">‹</button>
                  <button onClick={() => shiftMonth(1)}
                    className="rounded-md border border-slate-200 px-2 py-1 text-slate-600 hover:bg-slate-50">›</button>
                  <span className="ml-2 text-lg font-semibold text-slate-800">{monthLabel}</span>
                </div>
                <div className="flex items-center gap-4 text-xs text-slate-600">
                  <span className="flex items-center gap-1.5">
                    <span className="h-3 w-3 rounded-sm bg-sky-200" /> Blocked
                  </span>
                  <span className="flex items-center gap-1.5">
                    <span className="h-3 w-3 rounded-sm bg-red-200" /> Out of Order
                  </span>
                  <span className="flex items-center gap-1.5">
                    <span className="h-3 w-3 rounded-sm bg-amber-200" /> Maintenance
                  </span>
                </div>
              </div>
              {blocksQ.isLoading ? (
                <div className="flex justify-center py-20 text-slate-400">
                  <Loader2 className="h-6 w-6 animate-spin" />
                </div>
              ) : (
                <CalendarView groups={groups} month={month} />
              )}
            </>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="border-b border-slate-100 bg-slate-50/60 text-left text-sm text-slate-600">
                  <tr>
                    {['Room(s)', 'Room Type', 'Block Type', 'Reason', 'Start', 'End',
                      'Status', 'Linked To', 'Created By', ''].map((h) => (
                      <th key={h} className="px-3 py-3 font-semibold">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {groups.map((g) => (
                    <tr key={g.group_id} className="hover:bg-slate-50">
                      <td className="px-3 py-3 font-semibold text-slate-800">{g.rooms_label}</td>
                      <td className="px-3 py-3 text-slate-600">{g.room_type_name}</td>
                      <td className="px-3 py-3 text-slate-600">{g.block_type_label}</td>
                      <td className="px-3 py-3 text-slate-600">{g.reason_category_label}</td>
                      <td className="px-3 py-3 text-slate-600">{g.start_date}</td>
                      <td className="px-3 py-3 text-slate-600">{g.end_date}</td>
                      <td className="px-3 py-3">
                        <span className={`rounded-full px-2.5 py-1 text-xs font-semibold ${
                          g.status === 'active'
                            ? 'bg-emerald-100 text-emerald-700'
                            : g.status === 'ended'
                              ? 'bg-slate-75 text-slate-500'
                              : 'bg-red-100 text-red-700'}`}>
                          {g.status}
                        </span>
                      </td>
                      <td className="px-3 py-3 text-slate-500">{g.linked_reference ?? '—'}</td>
                      <td className="px-3 py-3 text-slate-600">{g.created_by_name ?? '—'}</td>
                      <td className="px-3 py-3 text-right">
                        {g.status === 'active' && (
                          <span className="flex justify-end gap-2">
                            <button onClick={() => { setError(null); end.mutate(g) }}
                              className="inline-flex items-center gap-1 rounded-lg border border-slate-200 px-2.5 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-50">
                              <Hourglass className="h-3 w-3" /> End Early
                            </button>
                            <button onClick={() => { setError(null); cancel.mutate(g) }}
                              className="inline-flex items-center gap-1 rounded-lg border border-red-300 px-2.5 py-1.5 text-xs font-semibold text-red-600 hover:bg-red-50">
                              <Ban className="h-3 w-3" /> Cancel
                            </button>
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                  {groups.length === 0 && (
                    <tr>
                      <td colSpan={10} className="px-3 py-16 text-center text-slate-500">
                        No blocks match these filters.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* ---------------- create block ---------------- */}
        <div className="space-y-4 rounded-xl border border-slate-200 bg-white p-5">
          <h2 className="flex items-center gap-2 text-lg font-semibold text-ink">
            <Plus className="h-5 w-5 text-brand" /> Create Block
          </h2>

          <div>
            <Label required>Block Type</Label>
            <div className="flex flex-col gap-2">
              {([['room_block', 'Room Block (Hold Inventory)'],
                 ['out_of_order', 'Out of Order (Not Sellable)']] as const).map(([v, l]) => (
                <label key={v} className="flex items-center gap-2 text-sm text-slate-700">
                  <input type="radio" name="block-type" checked={form.block_type === v}
                    onChange={() => setForm({ ...form, block_type: v })}
                    className="h-4 w-4 border-slate-300 text-brand" />
                  {l}
                </label>
              ))}
            </div>
          </div>

          <div>
            <Label required>Select Rooms</Label>
            <div className="max-h-32 overflow-y-auto rounded-lg border border-slate-200 p-2">
              <div className="flex flex-wrap gap-1.5">
                {rooms.map((r) => {
                  const on = form.room_ids.includes(r.id)
                  return (
                    <button key={r.id} type="button" onClick={() => toggleRoom(r.id)}
                      className={`rounded-md px-2 py-1 text-xs font-medium ${
                        on ? 'bg-brand text-white' : 'bg-slate-75 text-slate-500'
                      }`}>
                      {r.code}{on && ' ×'}
                    </button>
                  )
                })}
              </div>
            </div>
            <span className="mt-1 block text-xs text-slate-500">
              {selected.length} room(s) selected
            </span>
          </div>

          <label className="block">
            <Label required>Reason Category</Label>
            <Select value={form.reason_category}
              onChange={(e) => setForm({ ...form, reason_category: e.target.value })}
              className={input}>
              {BLOCK_REASONS.map((r) => (
                <option key={r.code} value={r.code}>{r.label}</option>
              ))}
            </Select>
          </label>

          <label className="block">
            <Label>Reason / Remarks</Label>
            <textarea value={form.reason} rows={2} maxLength={500}
              onChange={(e) => setForm({ ...form, reason: e.target.value })}
              className={input} />
            <span className="mt-1 block text-right text-xs text-slate-400">
              {form.reason.length}/500
            </span>
          </label>

          <div className="grid grid-cols-2 gap-3">
            <label>
              <Label required>Start Date</Label>
              <DateField value={form.start_date} onChange={(v) => setForm({ ...form, start_date: v })} className={input} />
            </label>
            <label>
              <Label required>End Date</Label>
              <DateField value={form.end_date} onChange={(v) => setForm({ ...form, end_date: v })} className={input} />
            </label>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <label>
              <Label required>Severity</Label>
              <Select value={form.severity}
                onChange={(e) => setForm({ ...form, severity: e.target.value })}
                className={input}>
                <option value="low">Low</option>
                <option value="medium">Medium</option>
                <option value="high">High</option>
              </Select>
            </label>
            <label>
              <Label>Link to Maintenance</Label>
              <input value={form.linked_reference} placeholder="#MT-2026-0912"
                onChange={(e) => setForm({ ...form, linked_reference: e.target.value })}
                className={input} />
            </label>
          </div>

          <div className="flex items-start gap-2 rounded-lg bg-sky-50 px-3 py-2.5">
            <Info className="mt-0.5 h-4 w-4 shrink-0 text-sky-500" />
            <div className="text-xs text-sky-900">
              <p className="font-semibold">
                {selected.length} room{selected.length === 1 ? '' : 's'} will be unavailable for sale
              </p>
              <p>These rooms will be removed from available inventory for the selected dates.</p>
            </div>
          </div>

          {notice && !conflicts.length && (
            <p className="rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-800">{notice}</p>
          )}
          {conflicts.length > 0 && (
            <div className="rounded-lg bg-amber-50 px-3 py-2.5">
              <p className="flex items-center gap-2 text-sm font-semibold text-amber-900">
                <AlertTriangle className="h-4 w-4" />
                {conflicts.length} room{conflicts.length === 1 ? '' : 's'} could not be blocked
              </p>
              <ul className="mt-1 space-y-0.5 text-xs text-caution">
                {conflicts.map((c) => <li key={c.room_id}>{c.detail}</li>)}
              </ul>
            </div>
          )}
          {error && (
            <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>
          )}

          <div className="flex gap-3">
            <button
              onClick={() => {
                setForm((f) => ({ ...f, room_ids: [], reason: '', linked_reference: '' }))
                setConflicts([]); setError(null); setNotice(null)
              }}
              className="rounded-lg border border-slate-200 px-4 py-2 text-sm font-semibold text-slate-600 hover:bg-slate-50">
              <X className="mr-1 inline h-4 w-4" /> Clear
            </button>
            <button
              onClick={() => { setError(null); setNotice(null); create.mutate() }}
              disabled={create.isPending || form.room_ids.length === 0}
              className="inline-flex flex-1 items-center justify-center gap-2 rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white hover:bg-brand/90 disabled:opacity-50">
              {create.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
              Create Block
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
