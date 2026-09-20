import { useRef, useState } from 'react'
import Select from '../components/Select'
import ListSelect from '../components/ListSelect'
import { BED_SETUPS, ROOM_VIEWS } from '../lib/options'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useParams } from 'react-router-dom'
import {
  Building2, Settings, CheckCircle2, Clock, Info, ArrowLeft, Loader2, AlertCircle,
  ImagePlus, ImageOff, Star, Trash2, Camera, Ban, FileEdit, History,
} from 'lucide-react'
import {
  getRoom, createRoom, updateRoom, deleteRoom, getRoomFacets, listAmenities,
  listBuildings, uploadRoomPhoto, setRoomPrimaryPhoto, deleteRoomPhoto,
  type RoomDetail,
} from '../api'
import { Crumbs } from '../components/Crumbs'
import { useActivePropertyId } from '../hooks/useProperty'

/** Screen 059 — Add or Edit Room. */


const ACCESSIBILITY = [
  { value: 'none', label: 'Not Accessible' },
  { value: 'wheelchair', label: 'Wheelchair Accessible' },
  { value: 'hearing', label: 'Hearing Accessible' },
  { value: 'visual', label: 'Visually Accessible' },
]

interface FormState {
  code: string
  room_type_id: string
  building: string
  floor: string
  /** The Buildings & Floors link; ``building``/``floor`` are its names. */
  building_id: string
  floor_id: string
  bed_setup: string
  view_type: string
  max_adults: number
  max_children: number
  base_rate: string
  housekeeping_zone: string
  accessibility: string
  near_elevator: boolean
  status: string
  service_status: string
  notes: string
  amenity_ids: string[]
}

const EMPTY: FormState = {
  code: '', room_type_id: '', building: '', floor: '', building_id: '', floor_id: '',
  bed_setup: '', view_type: '',
  max_adults: 2, max_children: 0, base_rate: '', housekeeping_zone: '',
  accessibility: 'none', near_elevator: false, status: 'active',
  service_status: 'in_service', notes: '', amenity_ids: [],
}

function fromRoom(r: RoomDetail): FormState {
  return {
    code: r.code, room_type_id: r.room_type_id, building: r.building ?? '',
    floor: r.floor ?? '', building_id: r.building_id ?? '', floor_id: r.floor_id ?? '',
    bed_setup: r.bed_setup ?? '', view_type: r.view_type ?? '',
    max_adults: r.max_adults, max_children: r.max_children,
    base_rate: r.base_rate ?? '', housekeeping_zone: r.housekeeping_zone ?? '',
    accessibility: r.accessibility, near_elevator: r.near_elevator,
    status: r.status, service_status: r.service_status, notes: r.notes ?? '',
    amenity_ids: r.amenities.map((a) => a.id),
  }
}

function apiError(e: unknown): string {
  const detail = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) return 'Some fields are invalid. Check the highlighted values.'
  return 'Could not save the room. Please try again.'
}

function Field({ label, required, children }: {
  label: string; required?: boolean; children: React.ReactNode
}) {
  return (
    <label className="text-sm">
      <span className="mb-1 block text-slate-500">
        {label} {required && <span className="text-red-500">*</span>}
      </span>
      {children}
    </label>
  )
}

const inputCls = 'w-full rounded-lg border border-slate-200 px-3 py-2'

export default function RoomEditor() {
  const { roomId } = useParams<{ roomId: string }>()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const propertyId = useActivePropertyId()
  const isEdit = Boolean(roomId)

  const [form, setForm] = useState<FormState | null>(null)
  const [error, setError] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  // Which control opened the picker: replacing the primary, or adding one.
  const replacePrimary = useRef(false)

  const roomQ = useQuery({
    queryKey: ['room', propertyId, roomId],
    queryFn: () => getRoom(propertyId, roomId!),
    enabled: isEdit && propertyId !== '',
  })
  const facetsQ = useQuery({
    queryKey: ['roomFacets', propertyId],
    queryFn: () => getRoomFacets(propertyId),
    enabled: propertyId !== '',
  })
  const buildingsQ = useQuery({
    queryKey: ['ob-buildings', propertyId],
    queryFn: () => listBuildings(propertyId),
    enabled: propertyId !== '',
  })
  const amenitiesQ = useQuery({
    queryKey: ['amenities', propertyId],
    queryFn: () => listAmenities(propertyId, { limit: 200 }),
    enabled: propertyId !== '',
  })

  // Seed the form once the room (edit) or the room-type list (add) has loaded.
  const seeded = form ?? (
    isEdit
      ? roomQ.data ? fromRoom(roomQ.data) : null
      : facetsQ.data
        ? { ...EMPTY, room_type_id: facetsQ.data.room_types[0]?.id ?? '' }
        : null
  )

  const save = useMutation({
    mutationFn: () => {
      const f = seeded!
      const body = {
        room_type_id: f.room_type_id,
        code: f.code,
        // A chosen floor sets building, floor and both ids on the server.
        ...(f.floor_id ? { floor_id: f.floor_id } : {}),
        building: f.building || null,
        floor: f.floor || null,
        bed_setup: f.bed_setup || null,
        view_type: f.view_type || null,
        max_adults: Number(f.max_adults),
        max_children: Number(f.max_children),
        base_rate: f.base_rate === '' ? null : f.base_rate,
        housekeeping_zone: f.housekeeping_zone || null,
        accessibility: f.accessibility,
        near_elevator: f.near_elevator,
        status: f.status,
        service_status: f.service_status,
        notes: f.notes || null,
        amenity_ids: f.amenity_ids,
      }
      return isEdit
        ? updateRoom(propertyId, roomId!, {
            ...body, version: roomQ.data!.version,
            reason: 'Edited from Add/Edit Room screen',
          })
        : createRoom(propertyId, body)
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['rooms', propertyId] })
      qc.invalidateQueries({ queryKey: ['roomFacets', propertyId] })
      navigate('/rooms')
    },
    onError: (e) => setError(apiError(e)),
  })

  const refreshRoom = () => {
    qc.invalidateQueries({ queryKey: ['room', propertyId, roomId] })
    qc.invalidateQueries({ queryKey: ['rooms', propertyId] })
  }
  const upload = useMutation({
    mutationFn: (file: File) =>
      uploadRoomPhoto(propertyId, roomId!, file, replacePrimary.current),
    onSuccess: () => { setError(null); refreshRoom() },
    onError: (e) => setError(apiError(e)),
  })
  const makePrimary = useMutation({
    mutationFn: (photoId: string) => setRoomPrimaryPhoto(propertyId, roomId!, photoId),
    onSuccess: refreshRoom,
    onError: (e) => setError(apiError(e)),
  })
  const removePhoto = useMutation({
    mutationFn: (photoId: string) => deleteRoomPhoto(propertyId, roomId!, photoId),
    onSuccess: refreshRoom,
    onError: (e) => setError(apiError(e)),
  })
  // Two clicks, not a browser confirm: deleting a room cannot be undone, and
  // the second click is where the consequence is spelled out.
  const [confirmDelete, setConfirmDelete] = useState(false)
  const remove = useMutation({
    mutationFn: () => deleteRoom(propertyId, roomId!),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['rooms', propertyId] })
      navigate('/rooms')
    },
    // The API refuses a room with history and says why; that sentence is more
    // use than anything this screen could invent.
    onError: (e) => { setConfirmDelete(false); setError(apiError(e)) },
  })

  // Save Draft / Deactivate write `status` directly rather than through the
  // form, so the button says what it does regardless of the toggle's state.
  const saveWithStatus = useMutation({
    mutationFn: (status: string) =>
      updateRoom(propertyId, roomId!, {
        status,
        version: roomQ.data!.version,
        reason: status === 'draft' ? 'Saved as draft' : `Status set to ${status}`,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['rooms', propertyId] })
      navigate('/rooms')
    },
    onError: (e) => setError(apiError(e)),
  })

  if (!seeded) {
    return (
      <div className="flex justify-center py-24 text-slate-400">
        <Loader2 className="h-6 w-6 animate-spin" />
      </div>
    )
  }
  if (isEdit && roomQ.isError) {
    return (
      <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-6 text-center text-sm text-red-700">
        Could not load this room. It may not exist, or you may not have permission.
      </div>
    )
  }

  const f = seeded
  const photos = roomQ.data?.photos ?? []
  // Building and floor come from Buildings & Floors once any exist; a property
  // that has not set that up yet keeps the typed fields.
  const buildings = buildingsQ.data ?? []
  const useMaster = buildings.length > 0
  const floorsOf = buildings.find((b) => b.id === f.building_id)?.floors ?? []
  const set =<K extends keyof FormState>(k: K, v: FormState[K]) =>
    setForm({ ...f, [k]: v })

  const toggleAmenity = (id: string) =>
    set('amenity_ids', f.amenity_ids.includes(id)
      ? f.amenity_ids.filter((x) => x !== id)
      : [...f.amenity_ids, id])

  // Live validation mirrors the mockup's Validation panel.
  const checks = [
    { ok: f.code.trim() !== '', label: 'Room number is provided.' },
    { ok: f.room_type_id !== '', label: 'Room type is selected.' },
    { ok: f.floor.trim() !== '', label: 'Floor is set.' },
    { ok: f.amenity_ids.length > 0, label: 'At least one amenity selected.' },
    { ok: f.housekeeping_zone.trim() !== '', label: 'Housekeeping zone assigned.' },
  ]
  const canSave = checks.slice(0, 2).every((c) => c.ok)

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between">
        <div>
          <Crumbs trail={[{ label: 'Property', to: '/property' },
            { label: 'Rooms', to: '/rooms' }]} />
          <h1 className="text-display text-ink">
            {isEdit ? `Edit Room ${roomQ.data?.code ?? ''}` : 'Add Room'}
          </h1>
          <p className="text-sm text-slate-500">
            Create a new room or update room details. Keep information accurate for smooth operations.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {roomId && (
            <button onClick={() => navigate(`/rooms/${roomId}/history`)}
              className="inline-flex items-center gap-2 rounded-lg border border-slate-200 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50">
              <History className="h-4 w-4" /> Status History
            </button>
          )}
          <button onClick={() => navigate('/rooms')}
            className="inline-flex items-center gap-2 rounded-lg border border-brand px-4 py-2 text-sm font-medium text-brand hover:bg-brand-light">
            <ArrowLeft className="h-4 w-4" /> Back to Room List
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-3">
        {/* ---------------- Left: the form ---------------- */}
        <div className="space-y-6 xl:col-span-2">
          <section className="rounded-xl border border-slate-200 bg-white p-6">
            <h2 className="mb-4 flex items-center gap-2 text-lg font-semibold text-ink">
              <Building2 className="h-5 w-5 text-brand" /> Basic Information
            </h2>
            <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
              <Field label="Room Number" required>
                <input value={f.code} onChange={(e) => set('code', e.target.value)}
                  placeholder="201" className={inputCls} />
              </Field>
              <Field label="Building">
                {useMaster ? (
                  <Select value={f.building_id} className={inputCls}
                    onChange={(e) => {
                      const b = buildings.find((x) => x.id === e.target.value)
                      setForm({ ...f, building_id: b?.id ?? '', building: b?.name ?? '',
                        floor_id: '', floor: '' })
                    }}>
                    <option value="">
                      {f.building && !f.building_id ? `${f.building} (not linked)` : 'Select building'}
                    </option>
                    {buildings.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
                  </Select>
                ) : (
                  <>
                    <input list="buildings" value={f.building}
                      onChange={(e) => set('building', e.target.value)} className={inputCls} />
                    <datalist id="buildings">
                      {facetsQ.data?.buildings.map((b) => <option key={b} value={b} />)}
                    </datalist>
                  </>
                )}
              </Field>
              <Field label="Floor" required>
                {useMaster ? (
                  <Select value={f.floor_id} className={inputCls} disabled={!f.building_id}
                    onChange={(e) => {
                      const fl = floorsOf.find((x) => x.id === e.target.value)
                      setForm({ ...f, floor_id: fl?.id ?? '', floor: fl?.name ?? '' })
                    }}>
                    <option value="">
                      {f.floor && !f.floor_id ? `${f.floor} (not linked)`
                        : f.building_id ? 'Select floor' : 'Choose a building first'}
                    </option>
                    {floorsOf.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
                  </Select>
                ) : (
                  <>
                    <input list="floors" value={f.floor}
                      onChange={(e) => set('floor', e.target.value)} className={inputCls} />
                    <datalist id="floors">
                      {facetsQ.data?.floors.map((x) => <option key={x} value={x} />)}
                    </datalist>
                  </>
                )}
              </Field>
              <Field label="Room Type" required>
                <Select value={f.room_type_id} onChange={(e) => set('room_type_id', e.target.value)}
                  className={inputCls}>
                  {facetsQ.data?.room_types.map((t) => (
                    <option key={t.id} value={t.id}>{t.name}</option>
                  ))}
                </Select>
              </Field>

              <Field label="Occupancy (Adults)" required>
                <input type="number" min={1} value={f.max_adults}
                  onChange={(e) => set('max_adults', Number(e.target.value))} className={inputCls} />
              </Field>
              <Field label="Occupancy (Children)">
                <input type="number" min={0} value={f.max_children}
                  onChange={(e) => set('max_children', Number(e.target.value))} className={inputCls} />
              </Field>
              <Field label="Bed Setup">
                <ListSelect value={f.bed_setup} onChange={(v) => set('bed_setup', v)}
                  options={BED_SETUPS} placeholder="—" className={inputCls} />
              </Field>
              <Field label="View">
                <ListSelect value={f.view_type} onChange={(v) => set('view_type', v)}
                  options={ROOM_VIEWS} placeholder="—" className={inputCls} />
              </Field>

              <Field label="Base Rate (₹ per night)">
                <input type="number" min={0} value={f.base_rate}
                  onChange={(e) => set('base_rate', e.target.value)} className={inputCls} />
              </Field>
            </div>
          </section>

          <section className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            <div className="rounded-xl border border-slate-200 bg-white p-6">
              <h2 className="mb-4 text-lg font-semibold text-ink">Amenities &amp; Features</h2>
              <div className="grid grid-cols-2 gap-x-4 gap-y-3">
                {(amenitiesQ.data?.items ?? [])
                  .filter((a) => a.status === 'active')
                  .map((a) => (
                    <label key={a.id} className="flex items-center gap-2 text-sm text-slate-600">
                      <input type="checkbox" checked={f.amenity_ids.includes(a.id)}
                        onChange={() => toggleAmenity(a.id)}
                        className="h-4 w-4 rounded border-slate-300 text-brand" />
                      {a.name}
                    </label>
                  ))}
              </div>
            </div>

            <div className="rounded-xl border border-slate-200 bg-white p-6">
              <h2 className="mb-4 flex items-center gap-2 text-lg font-semibold text-ink">
                <Settings className="h-5 w-5 text-brand" /> Housekeeping &amp; Accessibility
              </h2>
              <div className="space-y-4">
                <Field label="Housekeeping Zone">
                  <input list="zones" value={f.housekeeping_zone}
                    onChange={(e) => set('housekeeping_zone', e.target.value)} className={inputCls} />
                  <datalist id="zones">
                    {facetsQ.data?.housekeeping_zones.map((z) => <option key={z} value={z} />)}
                  </datalist>
                </Field>
                <Field label="Accessibility">
                  <Select value={f.accessibility} onChange={(e) => set('accessibility', e.target.value)}
                    className={inputCls}>
                    {ACCESSIBILITY.map((a) => (
                      <option key={a.value} value={a.value}>{a.label}</option>
                    ))}
                  </Select>
                </Field>
                <label className="flex items-center gap-2 text-sm text-slate-600">
                  <input type="checkbox" checked={f.near_elevator}
                    onChange={(e) => set('near_elevator', e.target.checked)}
                    className="h-4 w-4 rounded border-slate-300 text-brand" />
                  Near Elevator
                  <span className="text-xs text-slate-400">
                    (useful for elderly or special assistance guests)
                  </span>
                </label>
                <Field label="Notes">
                  <textarea value={f.notes} rows={3}
                    onChange={(e) => set('notes', e.target.value)} className={inputCls} />
                </Field>
              </div>
            </div>
          </section>

          {/* Photos (mockup 059). Photos attach to a saved room, so on a new
              room the section explains that rather than failing on upload. */}
          <section className="rounded-xl border border-slate-200 bg-white p-6">
            <h2 className="mb-1 flex items-center gap-2 text-lg font-semibold text-ink">
              <Camera className="h-5 w-5 text-brand" /> Photos
            </h2>
            <p className="mb-4 text-sm text-slate-500">
              Upload clear room photos (JPG, PNG or WebP, max 5 MB each).
            </p>

            <input ref={fileRef} type="file" accept="image/jpeg,image/png,image/webp"
              className="hidden"
              onChange={(e) => {
                const file = e.target.files?.[0]
                if (file) upload.mutate(file)
                e.target.value = ''
              }} />

            {!isEdit ? (
              <div className="grid place-items-center rounded-lg border border-dashed border-slate-300 py-10 text-center">
                <ImageOff className="h-7 w-7 text-slate-300" />
                <p className="mt-2 text-sm text-slate-500">
                  Save the room first, then add photos.
                </p>
              </div>
            ) : (
              <div className="flex flex-wrap gap-3">
                {photos.map((p, i) => (
                  <div key={p.id} className="group relative h-28 w-40 overflow-hidden rounded-lg">
                    <img src={p.url} alt={p.caption ?? ''}
                      className="h-full w-full object-cover" />
                    {i === 0 && (
                      <span className="absolute left-2 top-2 rounded bg-brand px-2 py-0.5 text-[10px] font-semibold text-white">
                        Primary
                      </span>
                    )}
                    <div className="absolute inset-0 hidden items-center justify-center gap-2 bg-slate-900/60 group-hover:flex">
                      <button title="Make primary" onClick={() => makePrimary.mutate(p.id)}
                        className="rounded bg-white/90 p-1.5 text-slate-700 hover:bg-white">
                        <Star className="h-4 w-4" />
                      </button>
                      <button title="Remove" onClick={() => removePhoto.mutate(p.id)}
                        className="rounded bg-white/90 p-1.5 text-red-600 hover:bg-white">
                        <Trash2 className="h-4 w-4" />
                      </button>
                    </div>
                  </div>
                ))}
                <button
                  onClick={() => { replacePrimary.current = photos.length === 0; fileRef.current?.click() }}
                  disabled={upload.isPending}
                  className="grid h-28 w-40 place-items-center rounded-lg border border-dashed border-slate-300 text-slate-400 hover:border-brand hover:text-brand disabled:opacity-50">
                  {upload.isPending ? (
                    <Loader2 className="h-5 w-5 animate-spin" />
                  ) : (
                    <span className="flex flex-col items-center gap-1">
                      <ImagePlus className="h-5 w-5" />
                      <span className="text-xs">Upload Photo</span>
                    </span>
                  )}
                </button>
              </div>
            )}
          </section>
        </div>

        {/* ---------------- Right: status, validation, audit ---------------- */}
        <div className="space-y-6">
          <section className="rounded-xl border border-slate-200 bg-white p-6">
            <h2 className="mb-3 text-lg font-semibold text-ink">Room Status</h2>
            <label className="flex items-center gap-3">
              <input type="checkbox" checked={f.status === 'active'}
                onChange={(e) => set('status', e.target.checked ? 'active' : 'inactive')}
                className="h-5 w-9 rounded-full border-slate-300 text-brand" />
              <span className="font-medium text-slate-700">
                {f.status === 'active' ? 'Active' : 'Inactive'}
              </span>
            </label>
            <p className="mt-1 text-xs text-slate-400">
              Inactive rooms will not be available for reservations.
            </p>

            <Field label="Service Status">
              <Select value={f.service_status} onChange={(e) => set('service_status', e.target.value)}
                className={`${inputCls} mt-3`}>
                <option value="in_service">In Service</option>
                <option value="out_of_service">Out of Service</option>
                <option value="maintenance">Maintenance</option>
              </Select>
            </Field>
          </section>

          <section className="rounded-xl border border-emerald-200 bg-emerald-50/50 p-6">
            <h2 className="mb-3 flex items-center gap-2 text-lg font-semibold text-emerald-800">
              <CheckCircle2 className="h-5 w-5" /> Validation
            </h2>
            <ul className="space-y-2 text-sm">
              {checks.map((c) => (
                <li key={c.label} className="flex items-center gap-2">
                  {c.ok
                    ? <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-600" />
                    : <AlertCircle className="h-4 w-4 shrink-0 text-slate-300" />}
                  <span className={c.ok ? 'text-emerald-800' : 'text-slate-400'}>{c.label}</span>
                </li>
              ))}
            </ul>
          </section>

          {isEdit && roomQ.data && (
            <section className="rounded-xl border border-slate-200 bg-white p-6">
              <h2 className="mb-3 flex items-center gap-2 text-lg font-semibold text-ink">
                <Clock className="h-5 w-5 text-slate-400" /> Audit Information
              </h2>
              <dl className="space-y-2 text-sm">
                <div className="flex justify-between">
                  <dt className="text-slate-500">Created On</dt>
                  <dd className="text-slate-700">
                    {roomQ.data.created_at?.slice(0, 16).replace('T', ' ') ?? '—'}
                  </dd>
                </div>
                <div className="flex justify-between">
                  <dt className="text-slate-500">Last Updated</dt>
                  <dd className="text-slate-700">
                    {roomQ.data.updated_at?.slice(0, 16).replace('T', ' ') ?? '—'}
                  </dd>
                </div>
                <div className="flex justify-between">
                  <dt className="text-slate-500">Version</dt>
                  <dd className="text-slate-700">{roomQ.data.version}</dd>
                </div>
              </dl>
            </section>
          )}

          <section className="flex gap-3 rounded-xl border border-sky-200 bg-sky-50/60 p-4 text-sm text-sky-900">
            <Info className="h-5 w-5 shrink-0 text-sky-500" />
            <p>
              Keep room details updated to help with better inventory management and
              guest satisfaction.
            </p>
          </section>
        </div>
      </div>

      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      <div className="flex flex-wrap justify-end gap-3 border-t border-slate-200 pt-4">
        <button onClick={() => navigate('/rooms')}
          className="rounded-lg border border-slate-200 px-5 py-2.5 text-sm font-semibold text-slate-600 hover:bg-slate-50">
          Cancel
        </button>
        {isEdit && (
          <button onClick={() => { setError(null); saveWithStatus.mutate('draft') }}
            disabled={saveWithStatus.isPending}
            title="Keep the room out of reservations until it is ready"
            className="inline-flex items-center gap-2 rounded-lg border border-slate-200 px-5 py-2.5 text-sm font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-50">
            <FileEdit className="h-4 w-4" /> Save Draft
          </button>
        )}
        <button onClick={() => { setError(null); save.mutate() }}
          disabled={save.isPending || !canSave}
          className="inline-flex items-center gap-2 rounded-lg bg-brand px-5 py-2.5 text-sm font-semibold text-white hover:bg-brand/90 disabled:opacity-50">
          {save.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
          {isEdit ? 'Save Room' : 'Create Room'}
        </button>
        {isEdit && (
          <button
            onClick={() => {
              setError(null)
              saveWithStatus.mutate(f.status === 'active' ? 'inactive' : 'active')
            }}
            disabled={saveWithStatus.isPending}
            className="inline-flex items-center gap-2 rounded-lg border border-red-300 px-5 py-2.5 text-sm font-semibold text-red-600 hover:bg-red-50 disabled:opacity-50">
            <Ban className="h-4 w-4" />
            {f.status === 'active' ? 'Deactivate' : 'Activate'}
          </button>
        )}
        {isEdit && !confirmDelete && (
          <button onClick={() => { setError(null); setConfirmDelete(true) }}
            title="Permanently remove this room"
            className="inline-flex items-center gap-2 rounded-lg border border-slate-200 px-5 py-2.5 text-sm font-semibold text-slate-500 hover:border-red-300 hover:bg-red-50 hover:text-red-600">
            <Trash2 className="h-4 w-4" /> Delete
          </button>
        )}
        {isEdit && confirmDelete && (
          <span className="inline-flex items-center gap-2 rounded-lg border border-red-300 bg-red-50 px-4 py-2 text-sm">
            <AlertCircle className="h-4 w-4 shrink-0 text-red-600" />
            <span className="text-red-700">
              Delete room {f.code} for good? This cannot be undone.
            </span>
            <button onClick={() => remove.mutate()} disabled={remove.isPending}
              className="inline-flex items-center gap-1.5 rounded-md bg-red-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-red-700 disabled:opacity-50">
              {remove.isPending && <Loader2 className="h-3 w-3 animate-spin" />}
              Delete
            </button>
            <button onClick={() => setConfirmDelete(false)}
              className="rounded-md border border-slate-200 bg-white px-3 py-1.5 text-xs font-semibold text-slate-600 hover:bg-slate-50">
              Cancel
            </button>
          </span>
        )}
      </div>
    </div>
  )
}
