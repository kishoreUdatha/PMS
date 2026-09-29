import { useRef, useState } from 'react'
import Select from '../components/Select'
import ListSelect from '../components/ListSelect'
import { BED_SETUPS, ROOM_VIEWS } from '../lib/options'
import { FILTER_SELECT } from '../lib/controls'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Bed, DoorOpen, Users, BarChart3, Plus, Loader2, X, Search, Camera,
  Copy, Ban, ImageOff, Star, Trash2,
} from 'lucide-react'
import {
  listManagedRoomTypes, getRoomTypeStats, getRoomTypeViews, getManagedRoomType,
  createManagedRoomType, updateManagedRoomType, duplicateRoomType,
  setRoomTypeStatus, listAmenities, uploadRoomTypePhoto,
  setRoomTypePrimaryPhoto, deleteRoomTypePhoto,
  type RoomTypeRow, type RoomTypeStats, type AmenityRow,
} from '../api'
import { errorText } from '../lib/forms'
import { clickableRow } from '../lib/a11y'

/** Screen 060 — Room Type Management. */

type IconType = React.ComponentType<{ className?: string }>

const CHILD_POLICIES = [
  'No children', '1 Child (0-12 yrs)', '2 Children (0-12 yrs)', 'Children on request',
]
const RATE_PLANS = [
  'Best Available Rate', 'Advance Purchase', 'Corporate', 'Package Rate',
]
const SORTS = [
  { code: 'name_asc', label: 'Name (A - Z)' },
  { code: 'name_desc', label: 'Name (Z - A)' },
  { code: 'rate_desc', label: 'Base Rate (high - low)' },
  { code: 'rate_asc', label: 'Base Rate (low - high)' },
  { code: 'rooms_desc', label: 'Most Rooms' },
]

function money(v: string | null): string {
  if (v === null) return '—'
  return `₹${Number(v).toLocaleString('en-IN')}`
}

function apiError(e: unknown): string {
  return errorText(e, 'Could not save. Please try again.')
}

function Kpi({
  icon: Icon, value, label, sub, tint,
}: {
  icon: IconType; value: string | number; label: string; sub: string; tint: string
}) {
  return (
    <div className="flex items-center gap-4 rounded-xl border border-slate-200 bg-white p-4">
      <span className={`grid h-12 w-12 shrink-0 place-items-center rounded-xl ${tint}`}>
        <Icon className="h-6 w-6" />
      </span>
      <div className="min-w-0">
        <p className="text-2xl font-bold leading-tight text-slate-800">{value}</p>
        <p className="text-base font-semibold text-slate-800">{label}</p>
        <p className="text-sm text-slate-500">{sub}</p>
      </div>
    </div>
  )
}

function KpiRow({ s }: { s: RoomTypeStats }) {
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
      <Kpi icon={Bed} value={s.room_types} label="Room Types"
        sub={`${s.active_types} Active | ${s.inactive_types} Inactive`}
        tint="bg-sky-100 text-sky-600" />
      <Kpi icon={DoorOpen} value={s.total_rooms} label="Total Rooms (Keys)"
        sub={`${s.active_rooms} Active | ${s.inactive_rooms} Inactive`}
        tint="bg-sky-100 text-sky-600" />
      <Kpi icon={Users} value={s.max_guest_capacity} label="Max Guest Capacity"
        sub="Across all room types" tint="bg-sky-100 text-sky-600" />
      <Kpi icon={BarChart3} value={money(s.average_base_rate)} label="Average Base Rate"
        sub="Per room per night" tint="bg-sky-100 text-sky-600" />
    </div>
  )
}

interface FormState {
  code: string; name: string; description: string
  max_adults: number; max_children: number; max_occupancy: number
  base_rate: string; bed_setup: string; size_sqft: string
  child_policy: string; extra_bed_available: boolean; extra_bed_charge: string
  room_view: string; default_rate_plan: string; status: string
  amenity_ids: string[]
}

const EMPTY: FormState = {
  code: '', name: '', description: '', max_adults: 2, max_children: 0,
  max_occupancy: 2, base_rate: '', bed_setup: '', size_sqft: '',
  child_policy: '', extra_bed_available: false, extra_bed_charge: '',
  room_view: '', default_rate_plan: '', status: 'active', amenity_ids: [],
}

function fromRow(r: RoomTypeRow): FormState {
  return {
    code: r.code, name: r.name, description: r.description ?? '',
    max_adults: r.max_adults, max_children: r.max_children,
    max_occupancy: r.max_occupancy, base_rate: r.base_rate ?? '',
    bed_setup: r.bed_setup ?? '', size_sqft: r.size_sqft?.toString() ?? '',
    child_policy: r.child_policy ?? '',
    extra_bed_available: r.extra_bed_available,
    extra_bed_charge: r.extra_bed_charge ?? '',
    room_view: r.room_view ?? '', default_rate_plan: r.default_rate_plan ?? '',
    status: r.status, amenity_ids: r.amenity_ids,
  }
}

function toBody(f: FormState) {
  return {
    code: f.code,
    name: f.name,
    description: f.description || null,
    max_adults: Number(f.max_adults),
    max_children: Number(f.max_children),
    max_occupancy: Number(f.max_occupancy),
    base_rate: f.base_rate === '' ? null : f.base_rate,
    bed_setup: f.bed_setup || null,
    size_sqft: f.size_sqft === '' ? null : Number(f.size_sqft),
    child_policy: f.child_policy || null,
    extra_bed_available: f.extra_bed_available,
    extra_bed_charge: f.extra_bed_charge === '' ? null : f.extra_bed_charge,
    room_view: f.room_view || null,
    default_rate_plan: f.default_rate_plan || null,
    status: f.status,
    amenity_ids: f.amenity_ids,
  }
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

/** The right-hand editor panel from the mockup. */
function Editor({
  propertyId, roomTypeId, amenities, onClose,
}: {
  propertyId: string
  roomTypeId: string | null
  amenities: AmenityRow[]
  onClose: () => void
}) {
  const qc = useQueryClient()
  const isEdit = roomTypeId !== null
  const [form, setForm] = useState<FormState | null>(isEdit ? null : EMPTY)
  const [error, setError] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  // Which control opened the picker: replacing the hero, or adding one.
  const replaceHero = useRef(false)

  const detailQ = useQuery({
    queryKey: ['managedRoomType', propertyId, roomTypeId],
    queryFn: () => getManagedRoomType(propertyId, roomTypeId!),
    enabled: isEdit,
  })
  const f = form ?? (detailQ.data ? fromRow(detailQ.data) : null)
  const version = detailQ.data?.version ?? 0

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['managedRoomTypes', propertyId] })
    qc.invalidateQueries({ queryKey: ['roomTypeStats', propertyId] })
    qc.invalidateQueries({ queryKey: ['roomFacets', propertyId] })
    qc.invalidateQueries({ queryKey: ['amenities', propertyId] })
  }

  const save = useMutation({
    mutationFn: () =>
      isEdit
        ? updateManagedRoomType(propertyId, roomTypeId!, { ...toBody(f!), version })
        : createManagedRoomType(propertyId, toBody(f!)),
    onSuccess: () => { invalidate(); onClose() },
    onError: (e) => setError(apiError(e)),
  })
  const dup = useMutation({
    mutationFn: () => duplicateRoomType(propertyId, roomTypeId!, {}),
    onSuccess: () => { invalidate(); onClose() },
    onError: (e) => setError(apiError(e)),
  })
  const refreshPhotos = () => {
    qc.invalidateQueries({ queryKey: ['managedRoomType', propertyId, roomTypeId] })
    qc.invalidateQueries({ queryKey: ['managedRoomTypes', propertyId] })
  }
  const upload = useMutation({
    mutationFn: (file: File) =>
      uploadRoomTypePhoto(propertyId, roomTypeId!, file, replaceHero.current),
    onSuccess: () => { setError(null); refreshPhotos() },
    onError: (e) => setError(apiError(e)),
  })
  const makePrimary = useMutation({
    mutationFn: (photoId: string) =>
      setRoomTypePrimaryPhoto(propertyId, roomTypeId!, photoId),
    onSuccess: refreshPhotos,
    onError: (e) => setError(apiError(e)),
  })
  const removePhoto = useMutation({
    mutationFn: (photoId: string) =>
      deleteRoomTypePhoto(propertyId, roomTypeId!, photoId),
    onSuccess: refreshPhotos,
    onError: (e) => setError(apiError(e)),
  })
  const deactivate = useMutation({
    mutationFn: () =>
      setRoomTypeStatus(propertyId, roomTypeId!, {
        status: f!.status === 'active' ? 'inactive' : 'active',
        version,
        reason: 'Changed from Room Type Management',
      }),
    onSuccess: () => { invalidate(); onClose() },
    // Deactivating a type still used by active rooms comes back as 409.
    onError: (e) => setError(apiError(e)),
  })

  if (!f) {
    return (
      <div className="flex justify-center rounded-xl border border-slate-200 bg-white py-24 text-slate-400">
        <Loader2 className="h-6 w-6 animate-spin" />
      </div>
    )
  }

  const set = <K extends keyof FormState>(k: K, v: FormState[K]) => setForm({ ...f, [k]: v })
  const toggleAmenity = (id: string) =>
    set('amenity_ids', f.amenity_ids.includes(id)
      ? f.amenity_ids.filter((x) => x !== id)
      : [...f.amenity_ids, id])
  const photos = detailQ.data?.photos ?? []

  return (
    <div className="rounded-xl border border-slate-200 bg-white">
      {/* Photo gallery */}
      <div className="relative border-b border-slate-100 p-4">
        <button onClick={onClose}
          className="absolute right-4 top-4 z-10 text-slate-400 hover:text-slate-600">
          <X className="h-5 w-5" />
        </button>
        <input ref={fileRef} type="file" accept="image/jpeg,image/png,image/webp"
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0]
            if (file) upload.mutate(file)
            e.target.value = ''   // let the same file be picked again
          }} />

        <div className="flex gap-3 pr-8">
          <div className="group relative grid h-36 flex-1 place-items-center overflow-hidden rounded-lg bg-gradient-to-br from-sky-100 to-brand-light">
            {photos[0] ? (
              <img src={photos[0].url} alt="" className="h-full w-full object-cover" />
            ) : (
              <div className="flex flex-col items-center gap-1 text-sky-400">
                <ImageOff className="h-7 w-7" />
                <span className="text-xs">No photos yet</span>
              </div>
            )}
            <button
              onClick={() => {
                if (!isEdit) return setError('Save the room type before adding photos.')
                replaceHero.current = true
                fileRef.current?.click()
              }}
              disabled={upload.isPending}
              className="absolute bottom-2 right-2 inline-flex items-center gap-1 rounded-md bg-slate-800/70 px-2 py-1 text-xs text-white hover:bg-slate-800 disabled:opacity-60">
              {upload.isPending
                ? <Loader2 className="h-3 w-3 animate-spin" />
                : <Camera className="h-3 w-3" />}
              {photos.length ? 'Change Photo' : 'Add Photo'}
            </button>
          </div>

          <div className="grid w-40 grid-cols-2 gap-2">
            {photos.slice(1, 5).map((p) => (
              <div key={p.id} className="group relative h-16 overflow-hidden rounded-md">
                <img src={p.url} alt="" className="h-full w-full object-cover" />
                <div className="absolute inset-0 hidden items-center justify-center gap-1 bg-slate-900/60 group-hover:flex">
                  <button title="Make primary"
                    onClick={() => makePrimary.mutate(p.id)}
                    className="rounded bg-white/90 p-1 text-slate-700 hover:bg-white">
                    <Star className="h-3 w-3" />
                  </button>
                  <button title="Remove"
                    onClick={() => removePhoto.mutate(p.id)}
                    className="rounded bg-white/90 p-1 text-red-600 hover:bg-white">
                    <Trash2 className="h-3 w-3" />
                  </button>
                </div>
              </div>
            ))}
            <button
              onClick={() => {
                if (!isEdit) return setError('Save the room type before adding photos.')
                replaceHero.current = false
                fileRef.current?.click()
              }}
              disabled={upload.isPending}
              className="grid h-16 place-items-center rounded-md border border-dashed border-slate-300 text-slate-400 hover:border-brand hover:text-brand disabled:opacity-50">
              {upload.isPending
                ? <Loader2 className="h-4 w-4 animate-spin" />
                : <span className="flex flex-col items-center"><Plus className="h-4 w-4" /><span className="text-[10px]">Add More</span></span>}
            </button>
          </div>
        </div>
        {photos.length > 0 && (
          <p className="mt-2 text-xs text-slate-400">
            {photos.length} photo{photos.length === 1 ? '' : 's'} · first is primary ·
            JPG, PNG or WebP up to 5 MB
          </p>
        )}
      </div>

      <div className="space-y-4 p-5">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold text-ink">
            {isEdit ? 'Edit Room Type' : 'New Room Type'}
          </h2>
          <label className="flex items-center gap-2 text-sm font-medium text-slate-700">
            {f.status === 'active' ? 'Active' : 'Inactive'}
            <input type="checkbox" checked={f.status === 'active'}
              onChange={(e) => set('status', e.target.checked ? 'active' : 'inactive')}
              className="h-5 w-9 rounded-full border-slate-300 text-brand" />
          </label>
        </div>

        <div className="grid grid-cols-3 gap-4">
          <label className="col-span-2">
            <Label required>Room Type Name</Label>
            <input value={f.name} onChange={(e) => set('name', e.target.value)}
              className={input} />
          </label>
          <label>
            <Label required>Code</Label>
            <input value={f.code} disabled={isEdit}
              onChange={(e) => set('code', e.target.value.toUpperCase())}
              className={`${input} disabled:bg-slate-50`} />
          </label>
        </div>

        <label className="block">
          <Label>Description</Label>
          <textarea value={f.description} rows={2} maxLength={500}
            onChange={(e) => set('description', e.target.value)} className={input} />
          <span className="mt-1 block text-right text-xs text-slate-400">
            {f.description.length}/500
          </span>
        </label>

        <div className="grid grid-cols-3 gap-4">
          <label>
            <Label required>Max Occupancy</Label>
            <Select value={f.max_occupancy}
              onChange={(e) => set('max_occupancy', Number(e.target.value))}
              className={input}>
              {[1, 2, 3, 4, 5, 6].map((n) => (
                <option key={n} value={n}>{n} Adults</option>
              ))}
            </Select>
          </label>
          <label>
            <Label>Child Policy</Label>
            <Select value={f.child_policy}
              onChange={(e) => set('child_policy', e.target.value)} className={input}>
              <option value="">—</option>
              {CHILD_POLICIES.map((c) => <option key={c} value={c}>{c}</option>)}
            </Select>
          </label>
          <label>
            <Label>Room Size (sq. ft.)</Label>
            <input type="number" min={1} value={f.size_sqft}
              onChange={(e) => set('size_sqft', e.target.value)} className={input} />
          </label>
        </div>

        <div className="grid grid-cols-2 gap-4">
          <label>
            <Label>Bed Configuration</Label>
            <ListSelect value={f.bed_setup} onChange={(v) => set('bed_setup', v)}
              options={BED_SETUPS} placeholder="—" className={input} />
          </label>
          <div>
            <Label>Extra Bed Option</Label>
            <div className="flex gap-2">
              <Select value={f.extra_bed_available ? 'yes' : 'no'}
                onChange={(e) => set('extra_bed_available', e.target.value === 'yes')}
                className={input}>
                <option value="no">Not available</option>
                <option value="yes">Available</option>
              </Select>
              {f.extra_bed_available && (
                <input type="number" min={0} placeholder="₹" value={f.extra_bed_charge}
                  onChange={(e) => set('extra_bed_charge', e.target.value)}
                  className={`${input} w-28`} />
              )}
            </div>
          </div>
        </div>

        <div className="grid grid-cols-3 gap-4">
          <label>
            <Label required>Base Rate (INR)</Label>
            <input type="number" min={0} value={f.base_rate}
              onChange={(e) => set('base_rate', e.target.value)} className={input} />
          </label>
          <label>
            <Label>Room View</Label>
            <ListSelect value={f.room_view} onChange={(v) => set('room_view', v)}
              options={ROOM_VIEWS} placeholder="—" className={input} />
          </label>
          <label>
            <Label>Default Rate Plan</Label>
            <Select value={f.default_rate_plan}
              onChange={(e) => set('default_rate_plan', e.target.value)} className={input}>
              <option value="">—</option>
              {RATE_PLANS.map((r) => <option key={r} value={r}>{r}</option>)}
            </Select>
          </label>
        </div>

        <div>
          <Label>
            Amenities <span className="font-normal text-slate-500">(Select all that apply)</span>
          </Label>
          <div className="grid grid-cols-3 gap-x-4 gap-y-2">
            {amenities.filter((a) => a.status === 'active').map((a) => (
              <label key={a.id} className="flex items-center gap-2 text-sm text-slate-600">
                <input type="checkbox" checked={f.amenity_ids.includes(a.id)}
                  onChange={() => toggleAmenity(a.id)}
                  className="h-4 w-4 rounded border-slate-300 text-brand" />
                {a.name}
              </label>
            ))}
          </div>
        </div>

        {error && (
          <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>
        )}

        <div className="flex flex-wrap items-end justify-between gap-4 border-t border-slate-100 pt-4">
          <div className="text-xs text-slate-500">
            <p className="font-semibold text-slate-600">Audit Information</p>
            <p>
              Created by {detailQ.data?.created_by_name ?? '—'}
              {detailQ.data?.created_at ? ` | ${detailQ.data.created_at.slice(0, 16).replace('T', ' ')}` : ''}
            </p>
            <p>
              Last modified by {detailQ.data?.updated_by_name ?? '—'}
              {detailQ.data?.updated_at ? ` | ${detailQ.data.updated_at.slice(0, 16).replace('T', ' ')}` : ''}
            </p>
          </div>
          <div className="flex gap-2">
            {isEdit && (
              <>
                <button onClick={() => { setError(null); dup.mutate() }}
                  disabled={dup.isPending}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-brand px-4 py-2 text-sm font-semibold text-brand hover:bg-brand-light disabled:opacity-50">
                  <Copy className="h-4 w-4" /> Duplicate
                </button>
                <button onClick={() => { setError(null); deactivate.mutate() }}
                  disabled={deactivate.isPending}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-red-300 px-4 py-2 text-sm font-semibold text-red-600 hover:bg-red-50 disabled:opacity-50">
                  <Ban className="h-4 w-4" />
                  {f.status === 'active' ? 'Deactivate' : 'Activate'}
                </button>
              </>
            )}
            <button onClick={() => { setError(null); save.mutate() }}
              disabled={save.isPending || !f.name || !f.code}
              className="inline-flex items-center gap-2 rounded-lg bg-brand px-5 py-2 text-sm font-semibold text-white hover:bg-brand/90 disabled:opacity-50">
              {save.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
              {isEdit ? 'Save Changes' : 'Create Room Type'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

export default function RoomTypeManagement({ propertyId }: { propertyId: string }) {
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState('')
  const [roomView, setRoomView] = useState('')
  const [sort, setSort] = useState('name_asc')
  const [editing, setEditing] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)

  const filters = {
    search: search || undefined,
    status: status || undefined,
    room_view: roomView || undefined,
    sort,
  }
  const listQ = useQuery({
    queryKey: ['managedRoomTypes', propertyId, filters],
    queryFn: () => listManagedRoomTypes(propertyId, filters),
    enabled: propertyId !== '',
  })
  const statsQ = useQuery({
    queryKey: ['roomTypeStats', propertyId],
    queryFn: () => getRoomTypeStats(propertyId),
    enabled: propertyId !== '',
  })
  const viewsQ = useQuery({
    queryKey: ['roomTypeViews', propertyId],
    queryFn: () => getRoomTypeViews(propertyId),
    enabled: propertyId !== '',
  })
  const amenitiesQ = useQuery({
    queryKey: ['amenities', propertyId, 'all'],
    queryFn: () => listAmenities(propertyId, { limit: 200 }),
    enabled: propertyId !== '',
  })

  const rows = listQ.data ?? []
  const panelOpen = creating || editing !== null

  if (listQ.isError) {
    return (
      <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-6 text-center text-sm text-red-700">
        Could not load room types. You may not have permission to view this module.
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {statsQ.data && <KpiRow s={statsQ.data} />}

      <div className={panelOpen ? 'grid gap-4 xl:grid-cols-2' : ''}>
        <div className="space-y-4 rounded-xl border border-slate-200 bg-white p-4">
          {/* One row, no captions: each select's first option says what it
              filters. Wraps when the editor halves the card. */}
          <div className="flex flex-wrap items-center gap-2">
            <Select blankIsChoice value={status} onChange={(e) => setStatus(e.target.value)}
              aria-label="Status" className={filterSelect}>
              <option value="">All Statuses</option>
              <option value="active">Active</option>
              <option value="inactive">Inactive</option>
            </Select>
            <Select blankIsChoice value={roomView} onChange={(e) => setRoomView(e.target.value)}
              aria-label="Room view" className={filterSelect}>
              <option value="">All Room Views</option>
              {(viewsQ.data ?? []).map((v) => <option key={v} value={v}>{v}</option>)}
            </Select>
            <Select value={sort} onChange={(e) => setSort(e.target.value)}
              aria-label="Sort by" className={filterSelect}>
              {SORTS.map((s) => <option key={s.code} value={s.code}>{s.label}</option>)}
            </Select>
            <div className="flex min-w-[240px] flex-1 items-center justify-end gap-2">
              <div className="relative min-w-0 max-w-sm flex-1">
                <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                <input value={search} onChange={(e) => setSearch(e.target.value)}
                  placeholder="Search by name or code..."
                  className="w-full rounded-lg border border-slate-200 bg-slate-50 py-2 pl-9 pr-3 text-sm outline-none focus:border-brand" />
              </div>
            </div>
          </div>

          {listQ.isLoading ? (
            <div className="flex justify-center py-16 text-slate-400">
              <Loader2 className="h-6 w-6 animate-spin" />
            </div>
          ) : rows.length === 0 ? (
            <p className="py-16 text-center text-sm text-slate-500">
              No room types match these filters.
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="border-b border-slate-100 bg-slate-50/60 text-left text-sm text-slate-600">
                  <tr>
                    {['Room Type', 'Code', 'Total Rooms', 'Active Rooms', 'Base Rate (INR)', 'Status', ''].map((h) => (
                      <th key={h} className="px-3 py-3 font-semibold">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {rows.map((rt) => (
                    <tr key={rt.id}
                      {...clickableRow(() => { setCreating(false); setEditing(rt.id) })}
                      className={`cursor-pointer ${editing === rt.id ? 'bg-brand-light' : 'hover:bg-slate-50'}`}>
                      <td className="px-3 py-3">
                        <div className="flex items-center gap-3">
                          <span className="grid h-10 w-12 shrink-0 place-items-center overflow-hidden rounded-md bg-gradient-to-br from-sky-100 to-brand-light">
                            {rt.primary_photo_url
                              ? <img src={rt.primary_photo_url} alt="" className="h-full w-full object-cover" />
                              : <Bed className="h-4 w-4 text-sky-400" />}
                          </span>
                          <div className="min-w-0">
                            <p className="font-semibold text-slate-800">{rt.name}</p>
                            {rt.description && (
                              <p className="truncate text-xs text-slate-500">{rt.description}</p>
                            )}
                          </div>
                        </div>
                      </td>
                      <td className="px-3 py-3 text-slate-600">{rt.code}</td>
                      <td className="px-3 py-3 text-slate-600">{rt.room_count}</td>
                      <td className="px-3 py-3 text-slate-600">{rt.active_room_count}</td>
                      <td className="px-3 py-3 text-slate-600">{money(rt.base_rate)}</td>
                      <td className="px-3 py-3">
                        <span className={`rounded-full px-2.5 py-1 text-xs font-semibold ${
                          rt.status === 'active'
                            ? 'bg-emerald-100 text-emerald-700'
                            : 'bg-slate-75 text-slate-500'}`}>
                          {rt.status === 'active' ? 'Active' : 'Inactive'}
                        </span>
                      </td>
                      <td className="px-3 py-3 text-right">
                        <span className="text-brand hover:underline">Edit</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <div className="flex items-center justify-between">
            <p className="text-sm text-slate-500">
              Showing {rows.length} of {statsQ.data?.room_types ?? rows.length} room types
            </p>
            <button onClick={() => { setEditing(null); setCreating(true) }}
              className="flex items-center gap-1.5 whitespace-nowrap rounded-lg bg-brand px-3 py-2 text-sm font-medium text-white hover:bg-brand-dark disabled:opacity-40">
              <Plus size={15} /> Create Room Type
            </button>
          </div>
        </div>

        {panelOpen && (
          <Editor propertyId={propertyId} roomTypeId={editing}
            amenities={amenitiesQ.data?.items ?? []}
            onClose={() => { setCreating(false); setEditing(null) }} />
        )}
      </div>
    </div>
  )
}
