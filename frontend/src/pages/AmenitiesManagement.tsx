import { useState } from 'react'
import Select from '../components/Select'
import { FILTER_SELECT } from '../lib/controls'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Sparkles, Eye, Package, EyeOff, Plus, Loader2, X, Search, Link2, Ban,
  ChevronLeft, ChevronRight,
} from 'lucide-react'
import {
  listAmenities, getAmenityStats, createAmenity, updateAmenity, mergeAmenity,
  listManagedRoomTypes, AMENITY_CATEGORIES,
  type AmenityRow, type AmenityStats, type RoomTypeRow,
} from '../api'

/** Screen 062 — Amenities Management. */

type IconType = React.ComponentType<{ className?: string }>

const ICON_CHOICES = [
  'wifi', 'wind', 'tv', 'phone', 'lock', 'bath', 'shower', 'coffee', 'wine',
  'trees', 'waves', 'briefcase', 'armchair', 'accessibility',
]

function apiError(e: unknown): string {
  const d = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  if (typeof d === 'string') return d
  if (Array.isArray(d)) return 'Some fields are invalid. Check the highlighted values.'
  return 'Could not save. Please try again.'
}

function Kpi({
  icon: Icon, value, label, tint,
}: {
  icon: IconType; value: number; label: string; tint: string
}) {
  return (
    <div className="flex items-center gap-4 rounded-xl border border-slate-200 bg-white p-4">
      <span className={`grid h-12 w-12 shrink-0 place-items-center rounded-xl ${tint}`}>
        <Icon className="h-6 w-6" />
      </span>
      <div>
        <p className="text-base font-semibold text-slate-800">{label}</p>
        <p className="text-display leading-tight text-slate-800">{value}</p>
      </div>
    </div>
  )
}

function KpiRow({ s }: { s: AmenityStats }) {
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
      <Kpi icon={Sparkles} value={s.total} label="Total Amenities"
        tint="bg-sky-100 text-sky-600" />
      <Kpi icon={Eye} value={s.guest_visible} label="Guest Visible"
        tint="bg-emerald-100 text-emerald-600" />
      <Kpi icon={Package} value={s.active} label="Active"
        tint="bg-brand-light text-brand" />
      <Kpi icon={EyeOff} value={s.inactive} label="Inactive"
        tint="bg-slate-75 text-slate-500" />
    </div>
  )
}

interface FormState {
  code: string; name: string; category: string; icon: string
  guest_visible: boolean; is_chargeable: boolean
  description: string; status: string; room_type_ids: string[]
}

const EMPTY: FormState = {
  code: '', name: '', category: 'in_room', icon: '', guest_visible: true,
  is_chargeable: false, description: '', status: 'active', room_type_ids: [],
}

const input = 'w-full rounded-lg border border-slate-200 px-3 py-2 text-sm'
const filterSelect = `${FILTER_SELECT} bg-white outline-none focus:border-brand`

function Label({ children, required }: { children: React.ReactNode; required?: boolean }) {
  return (
    <span className="mb-1 block text-sm font-medium text-slate-700">
      {children} {required && <span className="text-red-500">*</span>}
    </span>
  )
}

function Editor({
  propertyId, amenity, allAmenities, roomTypes, onClose,
}: {
  propertyId: string
  amenity: AmenityRow | null
  allAmenities: AmenityRow[]
  roomTypes: RoomTypeRow[]
  onClose: () => void
}) {
  const qc = useQueryClient()
  const isEdit = amenity !== null
  const [form, setForm] = useState<FormState>(
    amenity
      ? {
          code: amenity.code, name: amenity.name, category: amenity.category,
          icon: amenity.icon ?? '', guest_visible: amenity.guest_visible,
          is_chargeable: amenity.is_chargeable,
          description: amenity.description ?? '', status: amenity.status,
          room_type_ids: amenity.room_types.map((r) => r.id),
        }
      : EMPTY,
  )
  const [error, setError] = useState<string | null>(null)
  const [mergeTarget, setMergeTarget] = useState('')

  const body = {
    code: form.code,
    name: form.name,
    category: form.category,
    icon: form.icon || null,
    guest_visible: form.guest_visible,
    is_chargeable: form.is_chargeable,
    description: form.description || null,
    status: form.status,
    room_type_ids: form.room_type_ids,
  }

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['amenities', propertyId] })
    qc.invalidateQueries({ queryKey: ['amenityStats', propertyId] })
    qc.invalidateQueries({ queryKey: ['managedRoomTypes', propertyId] })
  }

  const save = useMutation({
    mutationFn: () =>
      isEdit
        ? updateAmenity(propertyId, amenity!.id, { ...body, version: amenity!.version })
        : createAmenity(propertyId, body),
    onSuccess: () => { invalidate(); onClose() },
    onError: (e) => setError(apiError(e)),
  })
  const deactivate = useMutation({
    mutationFn: () =>
      updateAmenity(propertyId, amenity!.id, {
        status: amenity!.status === 'active' ? 'inactive' : 'active',
        version: amenity!.version,
      }),
    onSuccess: () => { invalidate(); onClose() },
    onError: (e) => setError(apiError(e)),
  })
  const merge = useMutation({
    mutationFn: () =>
      mergeAmenity(propertyId, amenity!.id, {
        target_amenity_id: mergeTarget,
        reason: 'Merged duplicate from Amenities Management',
      }),
    onSuccess: () => { invalidate(); onClose() },
    onError: (e) => setError(apiError(e)),
  })

  const set = <K extends keyof FormState>(k: K, v: FormState[K]) =>
    setForm((f) => ({ ...f, [k]: v }))
  const toggleRoomType = (id: string) =>
    set('room_type_ids', form.room_type_ids.includes(id)
      ? form.room_type_ids.filter((x) => x !== id)
      : [...form.room_type_ids, id])

  return (
    <div className="space-y-4 rounded-xl border border-slate-200 bg-white p-5">
      <div className="flex items-start justify-between">
        <h2 className="text-lg font-semibold text-ink">
          {isEdit ? 'Edit Amenity' : 'Add Amenity'}
        </h2>
        <button onClick={onClose} className="text-slate-400 hover:text-slate-600">
          <X className="h-5 w-5" />
        </button>
      </div>

      <label className="block">
        <Label required>Amenity Name</Label>
        <input value={form.name} onChange={(e) => set('name', e.target.value)}
          className={input} />
      </label>

      <label className="block">
        <Label required>Amenity Code</Label>
        <input value={form.code} disabled={isEdit}
          onChange={(e) => set('code', e.target.value.toLowerCase().replace(/\s+/g, '_'))}
          className={`${input} disabled:bg-slate-50`} />
        <span className="mt-1 block text-xs text-slate-500">
          Use a unique code (e.g. rain_shower)
        </span>
      </label>

      <label className="block">
        <Label required>Category</Label>
        <Select value={form.category} onChange={(e) => set('category', e.target.value)}
          className={input}>
          {AMENITY_CATEGORIES.map((c) => (
            <option key={c.code} value={c.code}>{c.label}</option>
          ))}
        </Select>
      </label>

      <label className="block">
        <Label>Icon</Label>
        <Select value={form.icon} onChange={(e) => set('icon', e.target.value)}
          className={input}>
          <option value="">—</option>
          {ICON_CHOICES.map((i) => <option key={i} value={i}>{i}</option>)}
        </Select>
      </label>

      <label className="flex items-start gap-2">
        <input type="checkbox" checked={form.guest_visible}
          onChange={(e) => set('guest_visible', e.target.checked)}
          className="mt-0.5 h-4 w-4 rounded border-slate-300 text-brand" />
        <span>
          <span className="text-sm font-medium text-slate-700">Visible to Guests</span>
          <span className="block text-xs text-slate-500">
            Show this amenity on website, booking engine and guest communications.
          </span>
        </span>
      </label>

      <div>
        <Label required>Assign to Room Types</Label>
        <div className="flex flex-wrap gap-2 rounded-lg border border-slate-200 p-2">
          {roomTypes.length === 0 && (
            <span className="px-1 text-sm text-slate-400">No room types yet</span>
          )}
          {roomTypes.map((rt) => {
            const on = form.room_type_ids.includes(rt.id)
            return (
              <button key={rt.id} type="button" onClick={() => toggleRoomType(rt.id)}
                className={`inline-flex items-center gap-1 rounded-md px-2.5 py-1 text-xs font-medium ${
                  on ? 'bg-brand text-white' : 'bg-slate-75 text-slate-500'}`}>
                {rt.name}
                {on && <X className="h-3 w-3" />}
              </button>
            )
          })}
        </div>
      </div>

      <div>
        <Label>Status</Label>
        <div className="flex gap-6">
          {['active', 'inactive'].map((v) => (
            <label key={v} className="flex items-center gap-2 text-sm capitalize text-slate-700">
              <input type="radio" name="amenity-status" checked={form.status === v}
                onChange={() => set('status', v)}
                className="h-4 w-4 border-slate-300 text-brand" />
              {v}
            </label>
          ))}
        </div>
        <p className="mt-1 text-xs text-slate-500">
          Inactive amenities will not be available for assignment.
        </p>
      </div>

      <label className="flex items-center gap-2 text-sm text-slate-700">
        <input type="checkbox" checked={form.is_chargeable}
          onChange={(e) => set('is_chargeable', e.target.checked)}
          className="h-4 w-4 rounded border-slate-300 text-brand" />
        Chargeable
      </label>

      <label className="block">
        <Label>Description (Optional)</Label>
        <textarea value={form.description} rows={2} maxLength={300}
          onChange={(e) => set('description', e.target.value)} className={input} />
        <span className="mt-1 block text-right text-xs text-slate-400">
          {form.description.length}/300
        </span>
      </label>

      {isEdit && (
        <div className="flex gap-2 rounded-lg bg-sky-50 px-3 py-2 text-xs text-sky-900">
          <span>
            <span className="font-semibold">Audit Information</span>
            <span className="block">
              Created by: {amenity!.created_by_name ?? '—'}
              {amenity!.created_at ? ` on ${amenity!.created_at.slice(0, 16).replace('T', ' ')}` : ''}
            </span>
            <span className="block">
              Last modified: {amenity!.updated_by_name ?? '—'}
            </span>
          </span>
        </div>
      )}

      {error && (
        <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>
      )}

      <div className="flex gap-3">
        <button onClick={onClose}
          className="flex-1 rounded-lg border border-brand px-4 py-2 text-sm font-semibold text-brand hover:bg-brand-light">
          Cancel
        </button>
        <button onClick={() => { setError(null); save.mutate() }}
          disabled={save.isPending || !form.name || !form.code}
          className="inline-flex flex-1 items-center justify-center gap-2 rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white hover:bg-brand/90 disabled:opacity-50">
          {save.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
          Save Amenity
        </button>
      </div>

      {isEdit && (
        <div className="space-y-3 border-t border-slate-100 pt-4">
          <p className="text-center text-xs font-medium text-slate-400">Other Actions</p>
          <div className="flex gap-2">
            <Select value={mergeTarget} onChange={(e) => setMergeTarget(e.target.value)}
              className={input}>
              <option value="">Merge into…</option>
              {allAmenities.filter((a) => a.id !== amenity!.id).map((a) => (
                <option key={a.id} value={a.id}>{a.name}</option>
              ))}
            </Select>
            <button onClick={() => { setError(null); merge.mutate() }}
              disabled={!mergeTarget || merge.isPending}
              className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-50">
              <Link2 className="h-4 w-4" /> Merge Duplicate
            </button>
          </div>
          <button onClick={() => { setError(null); deactivate.mutate() }}
            disabled={deactivate.isPending}
            className="inline-flex w-full items-center justify-center gap-1.5 rounded-lg border border-red-300 px-4 py-2 text-sm font-semibold text-red-600 hover:bg-red-50 disabled:opacity-50">
            <Ban className="h-4 w-4" />
            {amenity!.status === 'active' ? 'Deactivate' : 'Activate'}
          </button>
        </div>
      )}
    </div>
  )
}

const PAGE_SIZE = 10

export default function AmenitiesManagement({ propertyId }: { propertyId: string }) {
  const qc = useQueryClient()
  const [tab, setTab] = useState('')          // '' = All
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState('')
  const [visible, setVisible] = useState('')
  const [page, setPage] = useState(0)
  const [editing, setEditing] = useState<AmenityRow | null>(null)
  const [creating, setCreating] = useState(false)

  const filters = {
    search: search || undefined,
    category: tab || undefined,
    status: status || undefined,
    guest_visible: visible === '' ? undefined : visible === 'yes',
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
  }
  const listQ = useQuery({
    queryKey: ['amenities', propertyId, filters],
    queryFn: () => listAmenities(propertyId, filters),
    enabled: propertyId !== '',
  })
  const statsQ = useQuery({
    queryKey: ['amenityStats', propertyId],
    queryFn: () => getAmenityStats(propertyId),
    enabled: propertyId !== '',
  })
  const allQ = useQuery({
    queryKey: ['amenities', propertyId, 'all'],
    queryFn: () => listAmenities(propertyId, { limit: 200 }),
    enabled: propertyId !== '',
  })
  const typesQ = useQuery({
    queryKey: ['managedRoomTypes', propertyId, {}],
    queryFn: () => listManagedRoomTypes(propertyId),
    enabled: propertyId !== '',
  })

  // The Guest Visible column toggles inline, like the mockup.
  const toggleVisible = useMutation({
    mutationFn: (a: AmenityRow) =>
      updateAmenity(propertyId, a.id, {
        guest_visible: !a.guest_visible, version: a.version,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['amenities', propertyId] })
      qc.invalidateQueries({ queryKey: ['amenityStats', propertyId] })
    },
  })

  const reset = () => { setSearch(''); setStatus(''); setVisible(''); setTab(''); setPage(0) }
  const rows = listQ.data?.items ?? []
  const total = listQ.data?.total ?? 0
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const panelOpen = creating || editing !== null

  if (listQ.isError) {
    return (
      <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-6 text-center text-sm text-red-700">
        Could not load amenities. You may not have permission to view this module.
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {statsQ.data && <KpiRow s={statsQ.data} />}

      <div className={panelOpen ? 'grid gap-4 xl:grid-cols-[1fr_380px]' : ''}>
        <div className="space-y-4 rounded-xl border border-slate-200 bg-white p-4">
          {/* Category tabs with live counts */}
          <div className="flex flex-wrap gap-5 border-b border-slate-200">
            {[{ code: '', label: 'All' }, ...AMENITY_CATEGORIES].map((c) => {
              const n = c.code
                ? statsQ.data?.by_category[c.code] ?? 0
                : statsQ.data?.total ?? 0
              return (
                <button key={c.code || 'all'}
                  onClick={() => { setTab(c.code); setPage(0) }}
                  className={`-mb-px border-b-2 px-1 pb-2.5 text-sm ${
                    tab === c.code
                      ? 'border-brand font-semibold text-brand'
                      : 'border-transparent font-normal text-slate-500 hover:text-slate-700'
                  }`}>
                  {c.label}{c.code ? ` (${n})` : ''}
                </button>
              )
            })}
          </div>

          {/* One row, no captions: each select's first option says what it
              filters. */}
          <div className="flex flex-wrap items-center gap-2">
            <Select blankIsChoice value={status}
              onChange={(e) => { setStatus(e.target.value); setPage(0) }}
              aria-label="Status" className={filterSelect}>
              <option value="">All Statuses</option>
              <option value="active">Active</option>
              <option value="inactive">Inactive</option>
            </Select>
            <Select blankIsChoice value={visible}
              onChange={(e) => { setVisible(e.target.value); setPage(0) }}
              aria-label="Guest visible" className={filterSelect}>
              <option value="">Visible &amp; Hidden</option>
              <option value="yes">Visible</option>
              <option value="no">Hidden</option>
            </Select>
            <button onClick={reset}
              className="px-1 text-sm font-semibold text-brand hover:underline">
              Reset
            </button>
            <div className="flex min-w-[240px] flex-1 items-center justify-end gap-2">
              <div className="relative min-w-0 max-w-sm flex-1">
                <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                <input value={search}
                  onChange={(e) => { setSearch(e.target.value); setPage(0) }}
                  placeholder="Search amenities by name or code..."
                  className="w-full rounded-lg border border-slate-200 bg-slate-50 py-2 pl-9 pr-3 text-sm outline-none focus:border-brand" />
              </div>
              <button onClick={() => { setEditing(null); setCreating(true) }}
                className="flex items-center gap-1.5 whitespace-nowrap rounded-lg bg-brand px-3 py-2 text-sm font-medium text-white hover:bg-brand-dark disabled:opacity-40">
                <Plus size={15} /> Add Amenity
              </button>
            </div>
          </div>

          {listQ.isLoading ? (
            <div className="flex justify-center py-16 text-slate-400">
              <Loader2 className="h-6 w-6 animate-spin" />
            </div>
          ) : rows.length === 0 ? (
            <p className="py-16 text-center text-sm text-slate-500">
              No amenities match these filters.
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="border-b border-slate-100 bg-slate-50/60 text-left text-sm text-slate-600">
                  <tr>
                    {['Amenity Name', 'Code', 'Category', 'Guest Visible', 'Assigned Room Types', 'Status', ''].map((h) => (
                      <th key={h} className="px-3 py-3 font-semibold">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {rows.map((a) => (
                    <tr key={a.id}
                      className={editing?.id === a.id ? 'bg-brand-light' : 'hover:bg-slate-50'}>
                      <td className="px-3 py-3 font-semibold text-slate-800">{a.name}</td>
                      <td className="px-3 py-3 text-slate-500">{a.code}</td>
                      <td className="px-3 py-3 text-slate-600">{a.category_label}</td>
                      <td className="px-3 py-3">
                        <button onClick={() => toggleVisible.mutate(a)}
                          aria-label={a.guest_visible ? 'Hide from guests' : 'Show to guests'}
                          className={`relative h-5 w-9 rounded-full transition-colors ${
                            a.guest_visible ? 'bg-brand' : 'bg-slate-300'}`}>
                          <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white transition-all ${
                            a.guest_visible ? 'left-[18px]' : 'left-0.5'}`} />
                        </button>
                      </td>
                      <td className="px-3 py-3 text-slate-600">
                        {a.room_types.length === 0
                          ? <span className="text-slate-400">—</span>
                          : a.room_types.length === (typesQ.data?.length ?? -1)
                            ? `All Room Types (${a.room_types.length})`
                            : `${a.room_types.map((r) => r.name).join(', ')} (${a.room_types.length})`}
                      </td>
                      <td className="px-3 py-3">
                        <span className={`rounded-full px-2.5 py-1 text-xs font-semibold ${
                          a.status === 'active'
                            ? 'bg-emerald-100 text-emerald-700'
                            : 'bg-slate-75 text-slate-500'}`}>
                          {a.status === 'active' ? 'Active' : 'Inactive'}
                        </span>
                      </td>
                      <td className="px-3 py-3 text-right">
                        <button onClick={() => { setCreating(false); setEditing(a) }}
                          className="font-semibold text-brand hover:underline">Edit</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <div className="flex items-center justify-between">
            <p className="text-sm text-slate-500">
              Showing {total === 0 ? 0 : page * PAGE_SIZE + 1} – {Math.min((page + 1) * PAGE_SIZE, total)} of {total} amenities
            </p>
            <div className="flex items-center gap-1">
              <button onClick={() => setPage((p) => Math.max(0, p - 1))} disabled={page === 0}
                className="rounded-md border border-slate-200 p-1.5 disabled:opacity-40">
                <ChevronLeft className="h-4 w-4" />
              </button>
              {Array.from({ length: pages }).map((_, i) => (
                <button key={i} onClick={() => setPage(i)}
                  className={`h-8 w-8 rounded-md text-sm font-medium ${
                    i === page ? 'bg-brand text-white' : 'border border-slate-200 text-slate-600'}`}>
                  {i + 1}
                </button>
              ))}
              <button onClick={() => setPage((p) => Math.min(pages - 1, p + 1))}
                disabled={page >= pages - 1}
                className="rounded-md border border-slate-200 p-1.5 disabled:opacity-40">
                <ChevronRight className="h-4 w-4" />
              </button>
            </div>
          </div>
        </div>

        {panelOpen && (
          <Editor propertyId={propertyId} amenity={editing}
            allAmenities={allQ.data?.items ?? []}
            roomTypes={typesQ.data ?? []}
            onClose={() => { setCreating(false); setEditing(null) }} />
        )}
      </div>
    </div>
  )
}
