import { Fragment, useEffect, useRef, useState } from 'react'
import Select from '../components/Select'
import ListSelect from '../components/ListSelect'
import { BED_SETUPS } from '../lib/options'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle, ArrowLeft, Ban, BedDouble, Building2, Check, CheckCircle2,
  ChevronDown, ChevronRight, Copy, Home, Image as ImageIcon, Info, Layers,
  LayoutGrid, Loader2, Pencil, Plus, PlusCircle, Trash2, Upload, X,
} from 'lucide-react'
import { WizardFrame, ContinueButton, obInput } from './Onboarding'
import ActionsMenu from '../components/ActionsMenu'
import {
  listBuildings, createBuilding, updateBuilding, deleteBuilding,
  createFloor, deleteFloor, listManagedRoomTypes, createManagedRoomType,
  updateManagedRoomType, setRoomTypeStatus, duplicateRoomType, listAmenities,
  getManagedRoomType, deleteManagedRoomType,
  uploadRoomTypePhoto, deleteRoomTypePhoto, bulkCreateRooms, listRooms,
  updateRoom, deleteRoom, type BuildingRow, type RoomTypeRow, type RoomRow,
} from '../api'

/**
 * Onboarding steps 3 and 4: the shape of the property, then what is in it.
 *
 * Both are thin over endpoints that already exist — buildings, floors and room
 * types are ordinary parts of the product, and the wizard should not become a
 * second way to create them that can drift from the first. The one genuinely
 * new thing is bulk room creation, because typing fifty rooms one form at a
 * time is where a new property gives up.
 */

/* ---------------------------------------------------------- 03 structure --- */

/* obInput carries w-full, and Tailwind settles a width clash by stylesheet
   order rather than by which class was written last -- so a narrow field has
   to be built from a class list that never mentions w-full at all. */
const narrowInput =
  'rounded-lg border border-slate-200 px-3 py-1.5 text-sm text-slate-700 outline-none focus:border-brand'

/** Codes are an internal handle; nobody onboarding should be asked for one. */
const toCode = (name: string) =>
  name.trim().toUpperCase().replace(/[^A-Z0-9]+/g, '-').slice(0, 20) || 'X'

export function OnboardingStructure() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const propertyId = localStorage.getItem('property_id') ?? ''

  const [tab, setTab] = useState<'buildings' | 'standalone'>('buildings')
  const [open, setOpen] = useState<Record<string, boolean>>({})
  const [adding, setAdding] = useState(false)
  const [newBuilding, setNewBuilding] = useState('')
  const [newFloor, setNewFloor] = useState<Record<string, string>>({})
  const [editing, setEditing] = useState<string | null>(null)
  const [editName, setEditName] = useState('')
  const [err, setErr] = useState('')

  const { data: buildings, isLoading } = useQuery({
    queryKey: ['ob-buildings', propertyId],
    queryFn: () => listBuildings(propertyId),
    enabled: propertyId !== '',
  })

  const refresh = () => {
    setErr('')
    qc.invalidateQueries({ queryKey: ['ob-buildings', propertyId] })
    qc.invalidateQueries({ queryKey: ['onboarding', propertyId] })
  }
  const fail = (e: unknown) => setErr(
    (e as { response?: { data?: { detail?: string } } })
      .response?.data?.detail ?? 'That could not be saved.')

  const addBuilding = useMutation({
    mutationFn: () => createBuilding(propertyId, {
      name: newBuilding.trim(), code: toCode(newBuilding),
    }),
    onSuccess: (b) => {
      setNewBuilding(''); setAdding(false); refresh()
      setOpen((o) => ({ ...o, [b.id]: true }))
    },
    onError: fail,
  })
  const renameBuilding = useMutation({
    mutationFn: (v: { id: string; name: string; version: number }) =>
      updateBuilding(propertyId, v.id, { name: v.name.trim(), version: v.version }),
    onSuccess: () => { setEditing(null); refresh() },
    onError: fail,
  })
  const removeBuilding = useMutation({
    mutationFn: (id: string) => deleteBuilding(propertyId, id),
    onSuccess: refresh, onError: fail,
  })
  const addFloor = useMutation({
    mutationFn: (v: { buildingId: string; name: string }) =>
      createFloor(propertyId, {
        building_id: v.buildingId, name: v.name.trim(), code: toCode(v.name),
      }),
    onSuccess: (_d, v) => {
      setNewFloor((f) => ({ ...f, [v.buildingId]: '' })); refresh()
    },
    onError: fail,
  })
  const removeFloor = useMutation({
    mutationFn: (id: string) => deleteFloor(propertyId, id),
    onSuccess: refresh, onError: fail,
  })

  const rows = buildings ?? []
  const totalFloors = rows.reduce((n, b) => n + (b.floors?.length ?? 0), 0)

  /** "Ground + 2 floors", the way the mock summarises a building. */
  const floorSummary = (b: BuildingRow) => {
    const names = (b.floors ?? []).map((f) => f.name)
    if (names.length === 0) return 'No floors yet'
    const ground = names.some((n) => /ground/i.test(n))
    const rest = names.length - (ground ? 1 : 0)
    if (ground) {
      return rest === 0 ? 'Ground only'
        : `Ground + ${rest} floor${rest === 1 ? '' : 's'}`
    }
    return `${names.length} floor${names.length === 1 ? '' : 's'}`
  }

  const floorPanel = (b: BuildingRow) => (
    <div className="rounded-xl bg-slate-50 p-3">
      <p className="text-xs font-semibold text-slate-500">
        Floors ({b.floors?.length ?? 0})
      </p>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        {(b.floors ?? []).map((f) => (
          <span key={f.id}
            className="flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs text-slate-600">
            {f.name}
            {f.room_count > 0 && (
              <span className="text-slate-400">{f.room_count} rooms</span>
            )}
            <button onClick={() => removeFloor.mutate(f.id)}
              aria-label={`Remove ${f.name}`}
              className="text-slate-300 hover:text-red-600">
              <X size={12} />
            </button>
          </span>
        ))}
        <input className={`${narrowInput} w-32 text-xs`}
          placeholder="Floor name"
          value={newFloor[b.id] ?? ''}
          onChange={(e) => setNewFloor((f) => ({ ...f, [b.id]: e.target.value }))}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (newFloor[b.id] ?? '').trim()) {
              addFloor.mutate({ buildingId: b.id, name: newFloor[b.id] })
            }
          }} />
        <button disabled={!(newFloor[b.id] ?? '').trim() || addFloor.isPending}
          onClick={() => addFloor.mutate({ buildingId: b.id, name: newFloor[b.id] ?? '' })}
          className="flex items-center gap-1 rounded-lg border border-dashed border-brand px-2.5 py-1.5 text-xs font-semibold text-brand hover:bg-brand/5 disabled:opacity-40">
          <Plus size={13} /> Add Floor
        </button>
      </div>
    </div>
  )

  return (
    <WizardFrame step="structure" stepNo={3}
      title="Set up your property layout"
      blurb="Organize buildings, floors, cottages and villas."
      footer={<>
        <button onClick={() => navigate('/onboarding/property')}
          className="flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-medium text-slate-600 hover:bg-slate-50">
          <ArrowLeft size={15} /> Back
        </button>
        <ContinueButton step="structure"
          onClick={() => navigate('/onboarding/rooms')} />
      </>}>

      {/* The mock puts these counts level with the title; the frame's own
          header already owns that corner, so they sit directly beneath it. */}
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="inline-flex overflow-hidden rounded-xl border border-slate-200">
          {([['buildings', 'Buildings & Floors', Building2],
             ['standalone', 'Standalone Units', Home]] as const).map(([k, label, Icon]) => (
            <button key={k} onClick={() => setTab(k)}
              className={`flex items-center gap-2 px-5 py-2.5 text-sm font-semibold ${
                tab === k ? 'bg-brand text-white' : 'bg-white text-slate-600 hover:bg-slate-50'}`}>
              <Icon size={15} /> {label}
            </button>
          ))}
        </div>
        <div className="flex overflow-hidden rounded-xl border border-slate-200 bg-white">
          {[{ n: rows.length, label: 'Buildings' },
            { n: totalFloors, label: 'Floors' }].map((c, i) => (
            <span key={c.label}
              className={`px-7 py-2.5 text-center ${i > 0 ? 'border-l border-slate-200' : ''}`}>
              <span className="block text-2xl font-bold tabular-nums text-slate-800">{c.n}</span>
              <span className="text-xs text-slate-500">{c.label}</span>
            </span>
          ))}
        </div>
      </div>

      {tab === 'standalone' ? (
        <div className="rounded-2xl border border-slate-100 bg-white p-8 text-center">
          <Home size={22} className="mx-auto text-slate-300" />
          <p className="mt-3 font-semibold text-slate-800">
            Standalone units use the same structure
          </p>
          <p className="mx-auto mt-1 max-w-xl text-sm text-slate-500">
            Nothing in the data tells a villa apart from a room whose building
            holds one room, so a separate list here would be a second name for
            what Buildings &amp; Floors already does. Add a building for the
            cluster &mdash; &ldquo;Beach Cottages&rdquo;, say &mdash; then one
            floor to hold them.
          </p>
          <button onClick={() => setTab('buildings')}
            className="mt-4 inline-flex items-center gap-2 rounded-xl bg-brand px-5 py-2.5 text-sm font-semibold text-white hover:bg-brand-dark">
            Go to Buildings &amp; Floors <ChevronRight size={15} />
          </button>
        </div>
      ) : (
        <div className="grid gap-4 xl:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
          <div className="overflow-hidden rounded-2xl border border-slate-100 bg-white">
            <div className="flex items-center justify-between gap-3 px-5 py-4">
              <h2 className="text-lg font-semibold text-ink">Buildings</h2>
              <button onClick={() => setAdding((v) => !v)}
                className="flex items-center gap-1.5 rounded-xl bg-brand px-3 py-2 text-sm font-semibold text-white hover:bg-brand-dark">
                <Plus size={15} /> Add Building
              </button>
            </div>

            {adding && (
              <div className="flex gap-2 border-t border-slate-100 bg-slate-50/60 px-5 py-3">
                <input className={`${obInput} flex-1`} autoFocus value={newBuilding}
                  placeholder="e.g. Main Building"
                  onChange={(e) => setNewBuilding(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && newBuilding.trim()) addBuilding.mutate()
                    if (e.key === 'Escape') { setAdding(false); setNewBuilding('') }
                  }} />
                <button disabled={!newBuilding.trim() || addBuilding.isPending}
                  onClick={() => addBuilding.mutate()}
                  className="rounded-lg bg-brand px-4 text-sm font-semibold text-white disabled:opacity-50">
                  Add
                </button>
                <button onClick={() => { setAdding(false); setNewBuilding('') }}
                  className="rounded-lg border border-slate-200 px-3 text-slate-500">
                  <X size={15} />
                </button>
              </div>
            )}

            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-y border-slate-100 bg-slate-50/60 text-slate-600">
                  <th className="w-9 px-3 py-2.5" />
                  <th className="px-3 py-2.5 font-semibold">Building</th>
                  <th className="px-3 py-2.5 font-semibold">Floors</th>
                  <th className="px-3 py-2.5 text-right font-semibold">Actions</th>
                </tr>
              </thead>
              <tbody>
                {isLoading && (
                  <tr><td colSpan={4} className="px-3 py-10 text-center">
                    <Loader2 className="mx-auto animate-spin text-slate-400" />
                  </td></tr>
                )}
                {!isLoading && rows.length === 0 && (
                  <tr><td colSpan={4} className="px-4 py-10 text-center text-sm text-slate-400">
                    No buildings yet. Even a single-block property needs one
                    &mdash; it is what floors and rooms hang from.
                  </td></tr>
                )}
                {rows.map((b) => (
                  <Fragment key={b.id}>
                    <tr className="border-b border-slate-100">
                      <td className="px-3 py-3">
                        <button onClick={() => setOpen((o) => ({ ...o, [b.id]: !o[b.id] }))}
                          aria-label={open[b.id] ? `Collapse ${b.name}` : `Expand ${b.name}`}
                          className="text-slate-400 hover:text-slate-600">
                          {open[b.id] ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
                        </button>
                      </td>
                      <td className="px-3 py-3">
                        {editing === b.id ? (
                          <span className="flex items-center gap-2">
                            <input className={`${narrowInput} w-44`} autoFocus
                              value={editName}
                              onChange={(e) => setEditName(e.target.value)}
                              onKeyDown={(e) => {
                                if (e.key === 'Enter' && editName.trim()) {
                                  renameBuilding.mutate({
                                    id: b.id, name: editName, version: b.version })
                                }
                                if (e.key === 'Escape') setEditing(null)
                              }} />
                            <button aria-label="Save name"
                              onClick={() => renameBuilding.mutate({
                                id: b.id, name: editName, version: b.version })}
                              className="text-brand"><Check size={16} /></button>
                            <button aria-label="Cancel" onClick={() => setEditing(null)}
                              className="text-slate-400"><X size={16} /></button>
                          </span>
                        ) : (
                          <span className="font-semibold text-slate-800">{b.name}</span>
                        )}
                      </td>
                      <td className="px-3 py-3 text-slate-500">{floorSummary(b)}</td>
                      <td className="px-3 py-3">
                        <span className="flex justify-end gap-1">
                          <button aria-label={`Rename ${b.name}`}
                            onClick={() => { setEditing(b.id); setEditName(b.name) }}
                            className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-600">
                            <Pencil size={15} />
                          </button>
                          <button aria-label={`Delete ${b.name}`}
                            disabled={removeBuilding.isPending}
                            onClick={() => removeBuilding.mutate(b.id)}
                            className="rounded-lg p-1.5 text-slate-400 hover:bg-red-50 hover:text-red-600">
                            <Trash2 size={15} />
                          </button>
                        </span>
                      </td>
                    </tr>
                    {open[b.id] && (
                      <tr className="border-b border-slate-100">
                        <td />
                        <td colSpan={3} className="px-3 pb-4">{floorPanel(b)}</td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>

            {err && (
              <p className="m-4 flex items-start gap-2 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700">
                <AlertTriangle size={16} className="mt-0.5 shrink-0" /> {err}
              </p>
            )}
          </div>

          <div className="rounded-2xl border border-slate-100 bg-white p-5">
            <h2 className="flex items-center gap-2 text-lg font-semibold text-ink">
              <Layers size={17} className="text-brand" /> Property Layout Preview
            </h2>
            <div className="mt-4 space-y-4">
              {rows.map((b) => (
                <div key={b.id}>
                  <p className="flex items-center gap-2 text-sm font-semibold text-slate-700">
                    <Building2 size={14} className="text-brand" /> {b.name}
                    <span className="font-normal text-slate-400">
                      {b.floors?.length ?? 0} floors &middot; {b.room_count} rooms
                    </span>
                  </p>
                  <div className="mt-2 space-y-1.5">
                    {(b.floors ?? []).map((f) => (
                      <p key={f.id}
                        className="flex items-center justify-between gap-2 rounded-lg bg-slate-50 px-3 py-2 text-sm text-slate-600">
                        <span className="flex items-center gap-2">
                          <Layers size={13} className="text-slate-400" /> {f.name}
                        </span>
                        <span className="text-xs text-slate-400">
                          {f.room_count} rooms
                        </span>
                      </p>
                    ))}
                    {(b.floors?.length ?? 0) === 0 && (
                      <p className="px-3 text-xs text-slate-400">No floors yet.</p>
                    )}
                  </div>
                </div>
              ))}
              {rows.length === 0 && (
                <p className="text-sm text-slate-400">Nothing to preview yet.</p>
              )}
            </div>
          </div>
        </div>
      )}

      {tab === 'buildings' && (
        <p className="mt-4 flex items-start gap-2 rounded-xl bg-sky-50 px-4 py-3 text-sm text-sky-800">
          <Info size={16} className="mt-0.5 shrink-0" />
          Cottages and villas are set up the same way: add a building for the
          cluster, then a floor to hold them.
        </p>
      )}
    </WizardFrame>
  )
}

/* -------------------------------------------------------------- 04 rooms --- */

/**
 * Expand "101-110, 201" the way the server does, so the button can say how
 * many rooms it is about to make and the preview can list them.
 *
 * Kept deliberately in step with `parse_room_spec` in rooms_routes.py: ranges
 * only open up when both ends are plain numbers of the same width, and codes
 * keep their leading zeros, because 007 and 7 are different doors.
 */
const MAX_BULK_ROOMS = 200

function parseRoomSpec(spec: string): { codes: string[]; error: string | null } {
  const codes: string[] = []
  const seen = new Set<string>()
  for (const raw of spec.replace(/;/g, ',').split(',')) {
    const part = raw.trim()
    if (!part) continue
    const cut = part.indexOf('-')
    const lo = cut < 0 ? part : part.slice(0, cut).trim()
    const hi = cut < 0 ? '' : part.slice(cut + 1).trim()
    const numeric = /^\d+$/
    if (cut >= 0 && numeric.test(lo) && numeric.test(hi) && lo.length === hi.length) {
      const start = Number(lo)
      const end = Number(hi)
      if (end < start) {
        return { codes: [], error: `'${part}' counts backwards — the range must ascend.` }
      }
      if (end - start + 1 > MAX_BULK_ROOMS) {
        return { codes: [], error: `'${part}' is more than ${MAX_BULK_ROOMS} rooms.` }
      }
      for (let n = start; n <= end; n += 1) {
        const code = String(n).padStart(lo.length, '0')
        if (!seen.has(code)) { seen.add(code); codes.push(code) }
      }
    } else if (!seen.has(part)) {
      seen.add(part)
      codes.push(part)
    }
  }
  if (codes.length > MAX_BULK_ROOMS) {
    return { codes: [], error: `That is more than ${MAX_BULK_ROOMS} rooms in one go.` }
  }
  return { codes, error: null }
}


type TypeForm = {
  name: string
  max_adults: string
  max_children: string
  bed_setup: string
  size_sqft: string
  amenity_ids: string[]
}

const formOf = (t: RoomTypeRow): TypeForm => ({
  name: t.name,
  max_adults: String(t.max_adults),
  max_children: String(t.max_children),
  bed_setup: t.bed_setup ?? '',
  size_sqft: t.size_sqft === null ? '' : String(t.size_sqft),
  amenity_ids: [...t.amenity_ids],
})

export function OnboardingRooms() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const propertyId = localStorage.getItem('property_id') ?? ''

  const [tab, setTab] = useState<'types' | 'inventory'>('types')

  const { data: types } = useQuery({
    queryKey: ['ob-room-types', propertyId],
    queryFn: () => listManagedRoomTypes(propertyId),
    enabled: propertyId !== '',
  })
  const { data: buildings } = useQuery({
    queryKey: ['ob-buildings', propertyId],
    queryFn: () => listBuildings(propertyId),
    enabled: propertyId !== '',
  })
  const { data: roomList } = useQuery({
    queryKey: ['ob-rooms', propertyId],
    queryFn: () => listRooms(propertyId, { limit: 500 }),
    enabled: propertyId !== '',
  })
  const { data: amenities } = useQuery({
    queryKey: ['ob-amenities', propertyId],
    queryFn: () => listAmenities(propertyId, { status: 'active', limit: 60 }),
    enabled: propertyId !== '',
  })

  // Never falls back to "the first one". It used to, and adding a room type
  // left `picked` empty — so a run of rooms typed straight afterwards was
  // created against whichever type happened to sort first, while the new one
  // still read 0 rooms. It looked exactly like nothing had saved.
  const [picked, setPicked] = useState<string>('')
  const active = (types ?? []).find((t) => t.id === picked)

  // The list endpoint leaves `photos` empty -- only the detail endpoint fills
  // it in -- so the images have to be read from the selected type on its own.
  // Reading them off the list row meant every upload landed on the server and
  // then showed nothing, which looked exactly like a broken upload.
  const { data: detail } = useQuery({
    queryKey: ['ob-room-type', propertyId, picked],
    queryFn: () => getManagedRoomType(propertyId, picked),
    enabled: propertyId !== '' && picked !== '',
  })
  const photos = detail?.id === picked ? detail.photos : []

  const [form, setForm] = useState<TypeForm | null>(null)
  const [spec, setSpec] = useState('')
  // Ids, not names. The room carries both a floor_id and a display string,
  // and sending the name alone left the id null -- so rooms created here did
  // not show up in the floor counts on step 3 at all.
  const [building, setBuilding] = useState('')
  const [floor, setFloor] = useState('')
  const [err, setErr] = useState('')
  const [typeFilter, setTypeFilter] = useState('')
  const photoInput = useRef<HTMLInputElement>(null)

  // Select the first type once, so a property with types already set up does
  // not start on nothing.
  useEffect(() => {
    if (!picked && (types?.length ?? 0) > 0) setPicked(types![0].id)
  }, [types, picked])

  // The form follows the selection, but must not stamp on edits in progress —
  // so it reloads when the identity changes, not on every refetch.
  useEffect(() => {
    if (active) setForm(formOf(active))
    else setForm(null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active?.id])

  const refresh = () => {
    setErr('')
    qc.invalidateQueries({ queryKey: ['ob-room-types', propertyId] })
    qc.invalidateQueries({ queryKey: ['ob-room-type', propertyId] })
    qc.invalidateQueries({ queryKey: ['ob-rooms', propertyId] })
    qc.invalidateQueries({ queryKey: ['onboarding', propertyId] })
  }
  const fail = (e: unknown) => setErr(
    (e as { response?: { data?: { detail?: string } } })
      .response?.data?.detail ?? 'That could not be saved.')

  const addType = useMutation({
    mutationFn: () => createManagedRoomType(propertyId, {
      name: 'New room type',
      code: `TYPE-${Date.now().toString(36).toUpperCase().slice(-6)}`,
      max_occupancy: 2,
    }),
    onSuccess: (created) => { setPicked(created.id); refresh() },
    onError: fail,
  })

  const saveType = useMutation({
    mutationFn: () => updateManagedRoomType(propertyId, active!.id, {
      name: form!.name.trim(),
      max_adults: Number(form!.max_adults) || 1,
      max_children: Number(form!.max_children) || 0,
      max_occupancy:
        (Number(form!.max_adults) || 1) + (Number(form!.max_children) || 0),
      // Left out rather than sent as null: the endpoint reads null as "leave
      // this alone", so a null would look like a clear and quietly do nothing.
      ...(form!.bed_setup ? { bed_setup: form!.bed_setup } : {}),
      ...(form!.size_sqft.trim() ? { size_sqft: Number(form!.size_sqft) } : {}),
      amenity_ids: form!.amenity_ids,
      version: active!.version,
    }),
    onSuccess: refresh, onError: fail,
  })

  const copyType = useMutation({
    mutationFn: (t: RoomTypeRow) => duplicateRoomType(propertyId, t.id),
    onSuccess: (made) => { setPicked(made.id); refresh() },
    onError: fail,
  })

  // There is no hard delete for a room type — rates, bookings and history hang
  // off it. Deactivating is what the product actually offers.
  const flipStatus = useMutation({
    mutationFn: (t: RoomTypeRow) => setRoomTypeStatus(propertyId, t.id, {
      status: t.status === 'active' ? 'inactive' : 'active',
      version: t.version,
    }),
    onSuccess: refresh, onError: fail,
  })

  // Deleting is for a type created by mistake. The server refuses the moment
  // anything real points at it and says what, so the menu item stays available
  // rather than being greyed out with a guess at the reason.
  const dropType = useMutation({
    mutationFn: (t: RoomTypeRow) => deleteManagedRoomType(propertyId, t.id),
    onSuccess: (_d, t) => {
      if (t.id === picked) setPicked('')
      refresh()
    },
    onError: fail,
  })

  const addPhoto = useMutation({
    mutationFn: (file: File) =>
      uploadRoomTypePhoto(propertyId, active!.id, file, photos.length === 0),
    onSuccess: refresh, onError: fail,
  })
  const dropPhoto = useMutation({
    mutationFn: (photoId: string) =>
      deleteRoomTypePhoto(propertyId, active!.id, photoId),
    onSuccess: refresh, onError: fail,
  })

  const parsed = parseRoomSpec(spec)

  const bulk = useMutation({
    mutationFn: () => bulkCreateRooms(propertyId, {
      room_type_id: active!.id,
      spec,
      floor_id: floor || null,
    }),
    onSuccess: () => { setSpec(''); refresh() },
    onError: fail,
  })

  const dropRoom = useMutation({
    mutationFn: (id: string) => deleteRoom(propertyId, id),
    onSuccess: refresh, onError: fail,
  })

  // Moving a room between types is the usual fix during setup -- a run
  // generated under the wrong type. The endpoint resyncs physical_capacity for
  // both the type it left and the one it joins, so neither is left oversold.
  const moveRoom = useMutation({
    mutationFn: (v: { room: RoomRow; roomTypeId?: string; floorId?: string }) =>
      updateRoom(propertyId, v.room.id, {
        ...(v.roomTypeId ? { room_type_id: v.roomTypeId } : {}),
        ...(v.floorId ? { floor_id: v.floorId } : {}),
        version: v.room.version,
        reason: 'Reassigned during onboarding',
      }),
    onSuccess: refresh, onError: fail,
  })

  const rows = types ?? []
  const allRooms = roomList?.items ?? []
  const rooms = typeFilter
    ? allRooms.filter((r) => r.room_type_id === typeFilter)
    : allRooms
  const floors = (buildings ?? []).find((b) => b.id === building)?.floors ?? []
  const dirty = active !== undefined && form !== null
    && JSON.stringify(form) !== JSON.stringify(formOf(active))

  const label = 'mb-1 block text-sm font-medium text-slate-600'
  const req = <span className="text-red-500"> *</span>

  return (
    <WizardFrame step="rooms" stepNo={4}
      title="Add room types and rooms"
      blurb="Define room categories and create physical rooms."
      footer={<>
        <button onClick={() => navigate('/onboarding/structure')}
          className="flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-medium text-slate-600 hover:bg-slate-50">
          <ArrowLeft size={15} /> Back
        </button>
        <ContinueButton step="rooms"
          onClick={() => navigate('/onboarding/rates')} />
      </>}>

      <div className="mb-4 inline-flex overflow-hidden rounded-xl border border-slate-200">
        {([['types', 'Room Types', BedDouble],
           ['inventory', 'Room Inventory', LayoutGrid]] as const).map(([k, text, Icon]) => (
          <button key={k} onClick={() => setTab(k)}
            className={`flex items-center gap-2 px-5 py-2.5 text-sm font-semibold ${
              tab === k ? 'bg-brand text-white' : 'bg-white text-slate-600 hover:bg-slate-50'}`}>
            <Icon size={15} /> {text}
          </button>
        ))}
      </div>

      {err && (
        <p className="mb-4 flex items-start gap-2 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700">
          <AlertTriangle size={16} className="mt-0.5 shrink-0" /> {err}
        </p>
      )}

      {tab === 'inventory' ? (
        <div className="overflow-hidden rounded-2xl border border-slate-100 bg-white">
          <div className="flex flex-wrap items-start justify-between gap-3 px-5 py-4">
            <div>
              <h2 className="text-lg font-semibold text-ink">Room Inventory</h2>
              <p className="text-sm text-slate-500">
                {rooms.length} room{rooms.length === 1 ? '' : 's'}
                {typeFilter ? ` of ${allRooms.length}` : ' so far'}. Change a
                room's type in place; one typed in by mistake can go, and one
                that has ever been booked is refused with the reason.
              </p>
            </div>
            <Select blankIsChoice className={`${narrowInput} w-48`} value={typeFilter}
              aria-label="Filter by room type"
              onChange={(e) => setTypeFilter(e.target.value)}>
              <option value="">All room types</option>
              {rows.map((t) => (
                <option key={t.id} value={t.id}>{t.name}</option>
              ))}
            </Select>
          </div>
          <div className="scroll-slim max-h-[26rem] overflow-y-auto">
            <table className="w-full text-left text-sm">
              <thead className="sticky top-0 bg-slate-50">
                <tr className="border-y border-slate-100 text-slate-600">
                  <th className="px-4 py-2.5 font-semibold">Room</th>
                  <th className="px-3 py-2.5 font-semibold">Type</th>
                  <th className="px-3 py-2.5 font-semibold">Building</th>
                  <th className="px-3 py-2.5 font-semibold">Floor</th>
                  <th className="px-3 py-2.5 text-right font-semibold">Actions</th>
                </tr>
              </thead>
              <tbody>
                {rooms.length === 0 && (
                  <tr><td colSpan={5} className="px-4 py-10 text-center text-sm text-slate-400">
                    No rooms yet. Create a run of them from the Room Types tab.
                  </td></tr>
                )}
                {rooms.map((r) => (
                  <tr key={r.id} className="border-b border-slate-100 last:border-0">
                    <td className="px-4 py-2.5 font-semibold text-slate-800">{r.code}</td>
                    <td className="px-3 py-2">
                      <Select className={`${narrowInput} w-44`} value={r.room_type_id}
                        aria-label={`Room type for ${r.code}`}
                        disabled={moveRoom.isPending}
                        onChange={(e) => moveRoom.mutate({
                          room: r, roomTypeId: e.target.value })}>
                        {/* The room's own type stays listed even when it has
                            been deactivated, or the select would show blank. */}
                        {rows.map((t) => (
                          <option key={t.id} value={t.id}>{t.name}</option>
                        ))}
                      </Select>
                    </td>
                    <td className="px-3 py-2">
                      <Select className={`${narrowInput} w-40`}
                        aria-label={`Building for ${r.code}`}
                        disabled={moveRoom.isPending}
                        value={r.building_id ?? ''}
                        onChange={(e) => {
                          // A room is placed by floor, not by building, so
                          // picking a building moves it to that building's
                          // first floor -- there is no "on a building but no
                          // floor" state to land in.
                          const b = (buildings ?? []).find((x) => x.id === e.target.value)
                          const first = b?.floors?.[0]
                          if (first) moveRoom.mutate({ room: r, floorId: first.id })
                          else setErr(`${b?.name ?? 'That building'} has no floors yet.`)
                        }}>
                        {r.building_id === null && (
                          <option value="">Not placed</option>
                        )}
                        {(buildings ?? []).map((b) => (
                          <option key={b.id} value={b.id}>{b.name}</option>
                        ))}
                      </Select>
                    </td>
                    <td className="px-3 py-2">
                      <Select className={`${narrowInput} w-36`}
                        aria-label={`Floor for ${r.code}`}
                        disabled={moveRoom.isPending || r.building_id === null}
                        value={r.floor_id ?? ''}
                        onChange={(e) => moveRoom.mutate({
                          room: r, floorId: e.target.value })}>
                        {r.floor_id === null && (
                          <option value="">Not placed</option>
                        )}
                        {((buildings ?? []).find((b) => b.id === r.building_id)
                          ?.floors ?? []).map((f) => (
                          <option key={f.id} value={f.id}>{f.name}</option>
                        ))}
                      </Select>
                    </td>
                    <td className="px-3 py-2.5">
                      <span className="flex justify-end">
                        <button aria-label={`Delete room ${r.code}`}
                          disabled={dropRoom.isPending}
                          onClick={() => dropRoom.mutate(r.id)}
                          className="rounded-lg p-1.5 text-slate-400 hover:bg-red-50 hover:text-red-600">
                          <Trash2 size={15} />
                        </button>
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : (<>
        {/* The types themselves, one card each, then the card that makes one. */}
        <div className="flex flex-wrap gap-3">
          {rows.map((t: RoomTypeRow) => {
            const on = t.id === picked
            return (
              <div key={t.id} onClick={() => setPicked(t.id)}
                className={`flex min-w-[15rem] cursor-pointer items-center gap-3 rounded-xl border-2 px-4 py-3 ${
                  on ? 'border-brand bg-brand/5' : 'border-slate-200 bg-white hover:bg-slate-50'}`}>
                <BedDouble size={18} className={on ? 'text-brand' : 'text-slate-400'} />
                <span className="flex-1">
                  <span className="block text-sm font-semibold text-slate-800">
                    {t.name}
                    {t.status !== 'active' && (
                      <span className="ml-2 rounded-md bg-slate-75 px-1.5 py-0.5 text-xs font-medium text-slate-500">
                        Inactive
                      </span>
                    )}
                  </span>
                  <span className="block text-xs text-slate-500">
                    {t.active_room_count ?? 0} rooms
                  </span>
                </span>
                <span onClick={(e) => e.stopPropagation()}>
                  <ActionsMenu label={`Actions for ${t.name}`} items={[
                    { label: 'Edit details', icon: Pencil, tone: 'primary',
                      onSelect: () => setPicked(t.id) },
                    { label: 'Duplicate', icon: Copy,
                      onSelect: () => copyType.mutate(t) },
                    { label: t.status === 'active' ? 'Deactivate' : 'Reactivate',
                      icon: Ban, group: 'end',
                      hint: 'Comes off sale; rates and history are kept',
                      onSelect: () => flipStatus.mutate(t) },
                    { label: 'Delete', icon: Trash2, tone: 'danger', group: 'end',
                      disabled: (t.active_room_count ?? 0) > 0,
                      hint: (t.active_room_count ?? 0) > 0
                        ? `${t.active_room_count} room(s) still use this type`
                        : 'Only if nothing uses it yet',
                      onSelect: () => dropType.mutate(t) },
                  ]} />
                </span>
              </div>
            )
          })}
          <button onClick={() => addType.mutate()} disabled={addType.isPending}
            className="flex min-w-[15rem] items-center gap-3 rounded-xl border-2 border-dashed border-brand/40 bg-white px-4 py-3 text-left hover:bg-brand/5 disabled:opacity-50">
            <PlusCircle size={20} className="text-brand" />
            <span className="text-sm font-semibold text-brand">Add Room Type</span>
          </button>
        </div>

        <div className="mt-4 grid gap-4 xl:grid-cols-2">
          {/* ---------------------------------------- room type details --- */}
          <div className="rounded-2xl border border-slate-100 bg-white p-5">
            <h2 className="text-lg font-semibold text-ink">Room Type Details</h2>
            {!form || !active ? (
              <p className="mt-4 text-sm text-slate-400">
                Add a room type to begin.
              </p>
            ) : (<>
              <div className="mt-4">
                <label className={label}>Room type name{req}</label>
                <input className={obInput} value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })} />
              </div>

              <div className="mt-4 grid gap-4 sm:grid-cols-2">
                <div>
                  <label className={label}>Max adults{req}</label>
                  <Select className={obInput} value={form.max_adults}
                    onChange={(e) => setForm({ ...form, max_adults: e.target.value })}>
                    {[1, 2, 3, 4, 5, 6].map((n) => (
                      <option key={n} value={n}>{n}</option>
                    ))}
                  </Select>
                </div>
                <div>
                  <label className={label}>Max children{req}</label>
                  <Select className={obInput} value={form.max_children}
                    onChange={(e) => setForm({ ...form, max_children: e.target.value })}>
                    {[0, 1, 2, 3, 4].map((n) => (
                      <option key={n} value={n}>{n}</option>
                    ))}
                  </Select>
                </div>
                <div>
                  <label className={label}>Bed type</label>
                  <ListSelect className={obInput} value={form.bed_setup} options={BED_SETUPS}
                    placeholder="Not set"
                    onChange={(v) => setForm({ ...form, bed_setup: v })} />
                </div>
                <div>
                  <label className={label}>Room size (sq ft)</label>
                  <input className={obInput} type="number" min={1} value={form.size_sqft}
                    placeholder="320"
                    onChange={(e) => setForm({ ...form, size_sqft: e.target.value })} />
                </div>
              </div>

              <div className="mt-4">
                <label className={label}>Amenities</label>
                {(amenities?.items.length ?? 0) === 0 ? (
                  <p className="text-sm text-slate-400">
                    No amenities set up for this property yet.
                  </p>
                ) : (
                  <div className="scroll-slim flex max-h-32 flex-wrap gap-x-5 gap-y-2 overflow-y-auto">
                    {(amenities?.items ?? []).map((a) => (
                      <label key={a.id}
                        className="flex cursor-pointer items-center gap-2 text-sm text-slate-600">
                        <input type="checkbox" className="accent-brand"
                          checked={form.amenity_ids.includes(a.id)}
                          onChange={(e) => setForm({
                            ...form,
                            amenity_ids: e.target.checked
                              ? [...form.amenity_ids, a.id]
                              : form.amenity_ids.filter((x) => x !== a.id),
                          })} />
                        {a.name}
                      </label>
                    ))}
                  </div>
                )}
              </div>

              <div className="mt-4">
                <label className={label}>Room type images</label>
                {/* A type with a dozen photos would otherwise push the save
                    button off the bottom of a long panel. */}
                <div className="scroll-slim flex max-h-60 flex-wrap gap-3 overflow-y-auto">
                  {photos.map((p) => (
                    <span key={p.id} className="group relative">
                      <img src={p.url} alt={p.caption ?? active.name}
                        className="h-24 w-32 rounded-xl object-cover" />
                      <button aria-label="Remove image"
                        onClick={() => dropPhoto.mutate(p.id)}
                        className="absolute right-1 top-1 rounded-lg bg-white/90 p-1 text-slate-500 opacity-0 group-hover:opacity-100 hover:text-red-600">
                        <X size={13} />
                      </button>
                    </span>
                  ))}
                  <button onClick={() => photoInput.current?.click()}
                    aria-label={`Upload an image for ${active.name}`}
                    disabled={addPhoto.isPending}
                    className="flex h-24 w-44 flex-col items-center justify-center gap-1 rounded-xl border-2 border-dashed border-slate-200 text-slate-400 hover:border-brand hover:text-brand disabled:opacity-50">
                    {addPhoto.isPending
                      ? <Loader2 size={18} className="animate-spin" />
                      : <ImageIcon size={18} />}
                    <span className="text-xs font-medium">Click to upload images</span>
                    <span className="text-[11px]">JPG, PNG (Max 5MB)</span>
                  </button>
                  <input ref={photoInput} type="file" hidden
                    accept="image/jpeg,image/png,image/webp"
                    onChange={(e) => {
                      const f = e.target.files?.[0]
                      // Checked here as well as on the server, so a phone photo
                      // is refused before it is uploaded and refused anyway.
                      if (f && f.size > 5 * 1024 * 1024) {
                        setErr('Image is larger than 5 MB.')
                      } else if (f) {
                        addPhoto.mutate(f)
                      }
                      e.target.value = ''
                    }} />
                </div>
              </div>

              <button disabled={!dirty || saveType.isPending}
                onClick={() => saveType.mutate()}
                className="mt-5 flex items-center gap-2 rounded-xl bg-brand px-5 py-2.5 text-sm font-semibold text-white hover:bg-brand-dark disabled:opacity-40">
                {saveType.isPending
                  ? <Loader2 size={15} className="animate-spin" />
                  : <Check size={15} />}
                {dirty ? 'Save changes' : 'Saved'}
              </button>
            </>)}
          </div>

          {/* ------------------------------------------ bulk create --- */}
          <div className="space-y-4">
            <div className="rounded-2xl border border-slate-100 bg-white p-5">
              <h2 className="text-lg font-semibold text-ink">Bulk Create Rooms</h2>
              <p className="mt-0.5 text-sm text-slate-500">
                {active
                  ? <>These rooms will be created as{' '}
                      <span className="font-semibold text-brand">{active.name}</span>.</>
                  : 'Choose a room type first.'}
              </p>
              <div className="mt-4 grid gap-4 sm:grid-cols-2">
                <div>
                  <label className={label}>Building{req}</label>
                  <Select className={obInput} value={building}
                    onChange={(e) => { setBuilding(e.target.value); setFloor('') }}>
                    <option value="">Select building</option>
                    {(buildings ?? []).map((b) => (
                      <option key={b.id} value={b.id}>{b.name}</option>
                    ))}
                  </Select>
                </div>
                <div>
                  <label className={label}>Floor{req}</label>
                  <Select className={obInput} value={floor} disabled={!building}
                    onChange={(e) => setFloor(e.target.value)}>
                    <option value="">Select floor</option>
                    {floors.map((f) => (
                      <option key={f.id} value={f.id}>{f.name}</option>
                    ))}
                  </Select>
                </div>
              </div>

              <div className="mt-4">
                <label className={label}>Room numbers{req}</label>
                <input className={obInput} value={spec} placeholder="101 – 110"
                  onChange={(e) => setSpec(e.target.value)} />
                <p className="mt-1 text-xs text-slate-400">
                  Enter a range (e.g. 101–110) or comma separated (e.g. 101,
                  102, 103). Leading zeros are kept, so 007 stays 007.
                </p>
                {parsed.error && (
                  <p className="mt-1 text-xs text-amber-700">{parsed.error}</p>
                )}
              </div>

              <button
                disabled={parsed.codes.length === 0 || !active || !building
                  || !floor || bulk.isPending}
                title={active ? undefined : 'Choose a room type first'}
                onClick={() => bulk.mutate()}
                className="mt-4 flex w-full items-center justify-center gap-2 rounded-xl bg-brand px-4 py-3 text-sm font-semibold text-white hover:bg-brand-dark disabled:opacity-50">
                {bulk.isPending && <Loader2 size={15} className="animate-spin" />}
                {parsed.codes.length > 0
                  ? `Generate ${parsed.codes.length} Room${parsed.codes.length === 1 ? '' : 's'}`
                  : 'Generate Rooms'}
              </button>
            </div>

            {parsed.codes.length > 0 && !bulk.data && (
              <div className="rounded-2xl border border-slate-100 bg-white p-5">
                <div className="flex items-baseline justify-between gap-3">
                  <h2 className="text-lg font-semibold text-ink">Preview Rooms</h2>
                  <span className="text-sm text-slate-500">
                    {parsed.codes.length} room{parsed.codes.length === 1 ? '' : 's'} ready to add
                  </span>
                </div>
                <div className="mt-3 flex flex-wrap gap-2">
                  {parsed.codes.map((c) => (
                    <span key={c}
                      className="rounded-lg bg-slate-50 px-3 py-1.5 text-sm text-slate-600">
                      {c}
                    </span>
                  ))}
                </div>
                {/* The mockup offers "Import rooms from Excel". There is no
                    import endpoint, and a file picker that discards the file is
                    worse than none — so it says so rather than pretending. */}
                <p className="mt-4 flex items-center gap-2 text-xs text-slate-400">
                  <Upload size={13} /> Importing rooms from a spreadsheet is not
                  available yet.
                </p>
              </div>
            )}

            {bulk.data && (() => {
              const made = bulk.data.created
              const skipped = bulk.data.results.filter((r) => !r.created)
              return (
                // Green only when something actually happened. "0 created"
                // under a tick reads as success, and the operator is left
                // wondering what the tick was for.
                <div className={`rounded-2xl p-5 ${
                  made > 0 ? 'bg-emerald-50' : 'bg-amber-50'}`}>
                  <p className={`flex items-center gap-2 text-sm font-semibold ${
                    made > 0 ? 'text-emerald-800' : 'text-amber-900'}`}>
                    {made > 0 ? <CheckCircle2 size={15} /> : <AlertTriangle size={15} />}
                    {made > 0
                      ? `${made} room${made === 1 ? '' : 's'} created`
                      : 'No rooms created'}
                    {skipped.length > 0 && `, ${skipped.length} skipped`}
                  </p>

                  {made > 0 && (
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {bulk.data.results.filter((r) => r.created).map((r) => (
                        <span key={r.code}
                          className="rounded-lg bg-white px-2.5 py-1 text-xs text-slate-600">
                          {r.code}
                        </span>
                      ))}
                    </div>
                  )}

                  {/* The reason, in words, next to the rooms it applies to. It
                      used to live in a title attribute, so a run that created
                      nothing gave no clue why. */}
                  {skipped.length > 0 && (
                    <ul className="mt-2 space-y-1">
                      {skipped.map((r) => (
                        <li key={r.code}
                          className="flex items-center gap-2 text-xs text-amber-900">
                          <span className="rounded-lg bg-amber-100 px-2 py-0.5 font-medium">
                            {r.code}
                          </span>
                          {r.reason}
                        </li>
                      ))}
                    </ul>
                  )}

                  {made === 0 && skipped.length > 0 && (
                    <p className="mt-2 text-xs text-caution">
                      Room numbers are unique across the property, so a number
                      already in use cannot be given to another room type. Pick
                      different numbers, or move the existing rooms.
                    </p>
                  )}
                </div>
              )
            })()}
          </div>
        </div>
      </>)}
    </WizardFrame>
  )
}
