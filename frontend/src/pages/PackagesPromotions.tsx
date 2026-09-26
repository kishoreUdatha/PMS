import { useRef, useState } from 'react'
import Select from '../components/Select'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import DateField from '../components/DateField'
import { fmtDate, fmtDateTime } from '../lib/dates'
import {
  AlertCircle, CalendarDays, Camera, Check, Ellipsis, ImageOff,
  Loader2, Pencil, Plus, Search, Star, Tag, Ticket, Trash2, X,
} from 'lucide-react'
import {
  listPackages, createPackage, updatePackage, setPackageStatus,
  uploadPackageImage, deletePackageImage,
  listPromoCodes, createPromoCode, updatePromoCode, redeemPromoCode,
  listRedemptions, listAddons, createAddon, updateAddon,
  listManagedRoomTypes, ADDON_CATEGORIES, PRICING_UNITS,
  type ResortPackage, type PromoCode, type Addon,
} from '../api'
import { errorText } from '../lib/forms'

/**
 * Screen 035 — Packages & Promotions.
 *
 * A package prices like a rate plan: it holds an adjustment to the room type's
 * base rate, so its headline "from" figure is computed and cannot drift from
 * the room it is built on.
 *
 * Bookings and revenue on each card are derived from reservations that name the
 * package. The booking flow does not offer packages yet, so they read zero —
 * which is the truth, not a placeholder to be filled in with something nicer.
 */

const TABS = ['Packages', 'Promo Codes', 'Add-ons', 'Redemption'] as const
type Tab = typeof TABS[number]

const input = 'w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700'
const day = (s: string | null) => fmtDate(s)
const stamp = (s: string) => fmtDateTime(s)
const money = (v: string | null) =>
  v === null ? '—' : `₹${Number(v).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`

function apiError(e: unknown): string {
  return errorText(e, 'Could not save. Please try again.')
}

const STATE_STYLE: Record<string, string> = {
  active: 'bg-emerald-50 text-emerald-700',
  inactive: 'bg-slate-100 text-slate-500',
  expired: 'bg-red-50 text-red-700',
  scheduled: 'bg-sky-50 text-sky-700',
  exhausted: 'bg-amber-50 text-amber-800',
}

function Field({ label, required, children }: {
  label: string; required?: boolean; children: React.ReactNode
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-sm font-medium text-slate-700">
        {label}{required && <span className="text-red-500"> *</span>}
      </span>
      {children}
    </label>
  )
}

function Dialog({ title, subtitle, onClose, children, footer }: {
  title: string; subtitle?: string; onClose: () => void
  children: React.ReactNode; footer: React.ReactNode
}) {
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-slate-900/40 p-4">
      <div className="flex max-h-[90vh] w-full max-w-2xl flex-col rounded-xl bg-white shadow-xl">
        <div className="flex items-start justify-between border-b border-slate-100 px-5 py-4">
          <div>
            <h3 className="text-lg font-bold text-ink">{title}</h3>
            {subtitle && <p className="text-sm text-slate-500">{subtitle}</p>}
          </div>
          <button onClick={onClose} aria-label="Close"
            className="rounded p-1 text-slate-400 hover:bg-slate-100">
            <X className="h-5 w-5" />
          </button>
        </div>
        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-5 py-4">{children}</div>
        <div className="flex justify-end gap-2 border-t border-slate-100 px-5 py-4">{footer}</div>
      </div>
    </div>
  )
}

/* ------------------------------------------------------------ package card --- */
function PackageCard({
  pkg, onEdit, onToggle, busy,
}: { pkg: ResortPackage; onEdit: () => void; onToggle: () => void; busy: boolean }) {
  const [menu, setMenu] = useState(false)
  const active = pkg.status === 'active'
  return (
    <div className="flex flex-col overflow-hidden rounded-xl border border-slate-200 bg-white">
      <div className="relative">
        {pkg.image_url ? (
          <img src={pkg.image_url} alt={pkg.name} className="h-40 w-full object-cover" />
        ) : (
          <div className="grid h-40 w-full place-items-center bg-slate-75 text-slate-300">
            <ImageOff className="h-7 w-7" />
          </div>
        )}
        {pkg.badge && (
          <span className="absolute left-3 top-3 rounded-md bg-white/90 px-2.5 py-1 text-xs font-semibold text-slate-700 shadow-sm">
            {pkg.badge}
          </span>
        )}
        {/* The heading used to carry this: a package was featured because it
            sat under "Featured Packages". With one list the flag has to be on
            the card, or the tick in the editor would have no visible effect. */}
        {pkg.is_featured && (
          <span title="Featured package"
            className="absolute right-3 top-3 flex items-center gap-1 rounded-md bg-amber-400/95 px-2 py-1 text-xs font-semibold text-amber-950 shadow-sm">
            <Star className="h-3 w-3 fill-current" /> Featured
          </span>
        )}
      </div>

      <div className="flex flex-1 flex-col gap-3 p-4">
        <div>
          <p className="text-lg font-bold text-slate-800">{pkg.name}</p>
          {pkg.tagline && <p className="text-sm text-slate-500">{pkg.tagline}</p>}
        </div>

        <div className="flex items-start justify-between gap-3">
          <p className="flex items-center gap-2 text-sm text-slate-600">
            <CalendarDays className="h-4 w-4 shrink-0 text-slate-400" />
            {day(pkg.valid_from)} – {day(pkg.valid_to)}
          </p>
          <div className="shrink-0 text-right">
            <p className="text-xs text-slate-500">From</p>
            <p className="text-lg font-bold text-slate-800">{money(pkg.from_rate)}</p>
            <p className="text-xs text-slate-500">per night</p>
          </div>
        </div>

        <ul className="space-y-1">
          {pkg.inclusions.slice(0, 4).map((i) => (
            <li key={i.id} className="flex items-start gap-2 text-sm text-slate-600">
              <Check className="mt-0.5 h-4 w-4 shrink-0 text-emerald-500" />
              {i.label}
            </li>
          ))}
          {pkg.inclusions.length > 4 && (
            <li className="pl-6 text-sm text-slate-400">
              +{pkg.inclusions.length - 4} more
            </li>
          )}
          {pkg.inclusions.length === 0 && (
            <li className="text-sm text-slate-400">No inclusions listed yet.</li>
          )}
        </ul>

        <div className="mt-auto flex items-end justify-between gap-3 border-t border-slate-100 pt-3">
          <div className="flex gap-5">
            <div>
              <p className="text-xs text-slate-500">Bookings</p>
              <p className="font-bold text-slate-800">{pkg.bookings}</p>
            </div>
            <div>
              <p className="text-xs text-slate-500">Revenue</p>
              <p className="font-bold text-slate-800">{money(pkg.revenue)}</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <span className={`rounded-full px-3 py-1 text-xs font-semibold ${
              active ? 'bg-emerald-50 text-emerald-700' : 'bg-slate-75 text-slate-500'}`}>
              {active ? 'Active' : 'Inactive'}
            </span>
            <div className="relative">
              <button onClick={() => setMenu((v) => !v)} disabled={busy}
                aria-label={`Actions for ${pkg.name}`}
                className="grid h-8 w-8 place-items-center rounded-lg text-slate-400 hover:bg-slate-100 disabled:opacity-50">
                {busy ? <Loader2 className="h-4 w-4 animate-spin" />
                  : <Ellipsis className="h-5 w-5" />}
              </button>
              {menu && (
                <div className="absolute right-0 z-20 mt-1 w-40 overflow-hidden rounded-lg border border-slate-200 bg-white shadow-lg">
                  <button onClick={() => { setMenu(false); onEdit() }}
                    className="block w-full px-4 py-2 text-left text-sm text-slate-700 hover:bg-slate-50">
                    Edit
                  </button>
                  <button onClick={() => { setMenu(false); onToggle() }}
                    className="block w-full px-4 py-2 text-left text-sm text-slate-700 hover:bg-slate-50">
                    {active ? 'Deactivate' : 'Activate'}
                  </button>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

/* ---------------------------------------------------------- package dialog --- */
interface PkgForm {
  code: string; name: string; tagline: string; description: string; badge: string
  valid_from: string; valid_to: string
  pricing_mode: 'adjustment' | 'fixed'
  adjustment_direction: string; adjustment_type: string; adjustment_value: string
  flat_rate: string
  min_nights: number; max_nights: string; is_featured: boolean; status: string
  room_type_ids: string[]; inclusions: { label: string; addon_id: string | null }[]
}

const today = () => new Date().toISOString().slice(0, 10)
const EMPTY_PKG: PkgForm = {
  code: '', name: '', tagline: '', description: '', badge: '',
  valid_from: today(), valid_to: today(),
  pricing_mode: 'adjustment',
  adjustment_direction: 'increase', adjustment_type: 'percent', adjustment_value: '0',
  flat_rate: '',
  min_nights: 1, max_nights: '', is_featured: true, status: 'active',
  room_type_ids: [], inclusions: [],
}

function PackageDialog({
  propertyId, pkg, addons, roomTypes, onClose,
}: {
  propertyId: string
  pkg: ResortPackage | null
  addons: Addon[]
  roomTypes: { id: string; name: string }[]
  onClose: () => void
}) {
  const qc = useQueryClient()
  const fileRef = useRef<HTMLInputElement>(null)
  const [form, setForm] = useState<PkgForm>(pkg ? {
    code: pkg.code, name: pkg.name, tagline: pkg.tagline ?? '',
    description: pkg.description ?? '', badge: pkg.badge ?? '',
    valid_from: pkg.valid_from, valid_to: pkg.valid_to,
    pricing_mode: pkg.flat_rate !== null ? 'fixed' : 'adjustment',
    adjustment_direction: pkg.adjustment_direction,
    adjustment_type: pkg.adjustment_type,
    adjustment_value: String(Number(pkg.adjustment_value)),
    flat_rate: pkg.flat_rate !== null ? String(Number(pkg.flat_rate)) : '',
    min_nights: pkg.min_nights, max_nights: pkg.max_nights ? String(pkg.max_nights) : '',
    is_featured: pkg.is_featured, status: pkg.status,
    room_type_ids: pkg.room_type_ids,
    inclusions: pkg.inclusions.map((i) => ({ label: i.label, addon_id: i.addon_id })),
  } : EMPTY_PKG)
  const [error, setError] = useState<string | null>(null)

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['packages', propertyId] })
    qc.invalidateQueries({ queryKey: ['promotionStats', propertyId] })
  }
  const payload = () => ({
    code: form.code.trim(), name: form.name.trim(),
    tagline: form.tagline.trim() || null,
    description: form.description.trim() || null,
    badge: form.badge.trim() || null,
    valid_from: form.valid_from, valid_to: form.valid_to,
    adjustment_direction: form.adjustment_direction,
    adjustment_type: form.adjustment_type,
    adjustment_value: form.adjustment_value === '' ? 0 : Number(form.adjustment_value),
    // A fixed price and an adjustment are alternatives, so only one is sent.
    flat_rate: form.pricing_mode === 'fixed' && form.flat_rate !== ''
      ? Number(form.flat_rate) : null,
    min_nights: form.min_nights,
    max_nights: form.max_nights === '' ? null : Number(form.max_nights),
    is_featured: form.is_featured, status: form.status,
    room_type_ids: form.room_type_ids,
    inclusions: form.inclusions.filter((i) => i.label.trim() !== ''),
  })

  const save = useMutation({
    mutationFn: () => pkg
      ? updatePackage(propertyId, pkg.id, { ...payload(), version: pkg.version })
      : createPackage(propertyId, payload()),
    onSuccess: () => { refresh(); onClose() },
    onError: (e) => setError(apiError(e)),
  })
  const image = useMutation({
    mutationFn: (file: File | null) => file
      ? uploadPackageImage(propertyId, pkg!.id, file)
      : deletePackageImage(propertyId, pkg!.id),
    onSuccess: refresh,
    onError: (e) => setError(apiError(e)),
  })

  const set = <K extends keyof PkgForm>(k: K, v: PkgForm[K]) =>
    setForm((f) => ({ ...f, [k]: v }))
  const setInclusion = (i: number, patch: Partial<PkgForm['inclusions'][number]>) =>
    setForm((f) => ({
      ...f,
      inclusions: f.inclusions.map((x, n) => (n === i ? { ...x, ...patch } : x)),
    }))

  return (
    <Dialog
      title={pkg ? 'Edit Package' : 'New Package'}
      subtitle={pkg ? 'Update the offer, its window and what it includes'
        : 'Bundle a room with the extras that make the stay'}
      onClose={onClose}
      footer={
        <>
          <button onClick={onClose}
            className="rounded-lg border border-slate-200 px-4 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50">
            Cancel
          </button>
          <button onClick={() => { setError(null); save.mutate() }} disabled={save.isPending}
            className="flex items-center gap-2 rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white hover:bg-brand-dark disabled:opacity-50">
            {save.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
            {pkg ? 'Save Changes' : 'Create Package'}
          </button>
        </>
      }>
      <div className="grid grid-cols-[1fr_140px] gap-3">
        <Field label="Package Name" required>
          <input className={input} value={form.name} maxLength={120}
            onChange={(e) => set('name', e.target.value)} />
        </Field>
        <Field label="Code" required>
          <input className={`${input} uppercase`} value={form.code} maxLength={20}
            onChange={(e) => set('code', e.target.value.toUpperCase())} />
        </Field>
      </div>
      <Field label="Tagline">
        <input className={input} value={form.tagline} maxLength={200}
          placeholder="Let the rains bring you closer to nature."
          onChange={(e) => set('tagline', e.target.value)} />
      </Field>
      <Field label="Badge">
        <input className={input} value={form.badge} maxLength={40}
          placeholder="e.g. Seasonal Favourite — shown on the card image"
          onChange={(e) => set('badge', e.target.value)} />
      </Field>

      <div className="grid grid-cols-2 gap-3">
        <Field label="Valid from" required>
          <DateField value={form.valid_from} onChange={(v) => set('valid_from', v)} className={input} />
        </Field>
        <Field label="Valid to" required>
          <DateField value={form.valid_to} onChange={(v) => set('valid_to', v)} className={input} />
        </Field>
      </div>

      <div>
        <span className="mb-1 block text-sm font-medium text-slate-700">Pricing</span>
        <div className="mb-2 flex overflow-hidden rounded-lg border border-slate-200">
          {([['adjustment', 'Adjust room type rate'],
             ['fixed', 'Set a fixed price']] as const).map(([v, l]) => (
            <button key={v} onClick={() => set('pricing_mode', v)}
              className={`flex-1 px-3 py-2 text-sm font-semibold transition ${
                form.pricing_mode === v
                  ? 'bg-brand text-white' : 'bg-white text-slate-500 hover:bg-slate-50'
              }`}>
              {l}
            </button>
          ))}
        </div>

        {form.pricing_mode === 'fixed' ? (
          <>
            <div className="relative">
              <span className="absolute left-3 top-1/2 -translate-y-1/2 text-sm text-slate-400">
                ₹
              </span>
              <input type="number" min={0} step="0.01" className={`${input} pl-7`}
                value={form.flat_rate} placeholder="e.g. 7500"
                onChange={(e) => set('flat_rate', e.target.value)} />
            </div>
            <p className="mt-1 text-xs text-slate-400">
              Charged per night for every room type this covers. It stays at this
              price when a room type's base rate changes.
            </p>
          </>
        ) : (
          <>
            <div className="flex gap-2">
              <Select className={`${input} w-40`} value={form.adjustment_direction}
                onChange={(e) => set('adjustment_direction', e.target.value)}>
                <option value="increase">Increase by</option>
                <option value="decrease">Decrease by</option>
              </Select>
              <input type="number" min={0} step="0.01" className={input}
                value={form.adjustment_value}
                onChange={(e) => set('adjustment_value', e.target.value)} />
              <div className="flex shrink-0 overflow-hidden rounded-lg border border-slate-200">
                {(['percent', 'amount'] as const).map((t) => (
                  <button key={t} onClick={() => set('adjustment_type', t)}
                    className={`px-4 py-2 text-sm font-semibold transition ${
                      form.adjustment_type === t
                        ? 'bg-brand text-white' : 'bg-white text-slate-500 hover:bg-slate-50'
                    }`}>
                    {t === 'percent' ? '%' : 'INR'}
                  </button>
                ))}
              </div>
            </div>
            <p className="mt-1 text-xs text-slate-400">
              Applied to each room type's base rate, so it follows when that rate changes.
            </p>
          </>
        )}
      </div>

      <div className="grid grid-cols-3 gap-3">
        <Field label="Min nights">
          <input type="number" min={1} className={input} value={form.min_nights}
            onChange={(e) => set('min_nights', Number(e.target.value))} />
        </Field>
        <Field label="Max nights">
          <input type="number" min={1} className={input} value={form.max_nights}
            placeholder="No limit"
            onChange={(e) => set('max_nights', e.target.value)} />
        </Field>
        <Field label="Status">
          <Select className={input} value={form.status}
            onChange={(e) => set('status', e.target.value)}>
            <option value="active">Active</option>
            <option value="inactive">Inactive</option>
          </Select>
        </Field>
      </div>

      <label className="flex items-center gap-2 text-sm font-medium text-slate-700">
        <input type="checkbox" checked={form.is_featured}
          onChange={(e) => set('is_featured', e.target.checked)}
          className="h-4 w-4 rounded border-slate-300 text-brand" />
        Show in Featured Packages
      </label>

      <div>
        <div className="mb-1 flex items-center justify-between">
          <span className="text-sm font-medium text-slate-700">Inclusions</span>
          <button
            onClick={() => set('inclusions', [...form.inclusions, { label: '', addon_id: null }])}
            className="flex items-center gap-1 text-sm font-semibold text-brand hover:underline">
            <Plus className="h-4 w-4" /> Add inclusion
          </button>
        </div>
        <p className="mb-2 text-xs text-slate-400">
          Link an add-on where the property actually sells one; leave it as free
          text for things it does not stock, such as a late check-out.
        </p>
        <div className="space-y-2">
          {form.inclusions.map((inc, i) => (
            <div key={i} className="flex gap-2">
              <input className={input} value={inc.label} maxLength={200}
                placeholder="Complimentary breakfast"
                onChange={(e) => setInclusion(i, { label: e.target.value })} />
              <Select className={`${input} w-52`} value={inc.addon_id ?? ''}
                onChange={(e) => {
                  const id = e.target.value || null
                  const addon = addons.find((a) => a.id === id)
                  setInclusion(i, {
                    addon_id: id,
                    // Naming an add-on fills an empty label with its name.
                    ...(addon && inc.label.trim() === '' ? { label: addon.name } : {}),
                  })
                }}>
                <option value="">Free text</option>
                {addons.filter((a) => a.status === 'active').map((a) => (
                  <option key={a.id} value={a.id}>{a.name}</option>
                ))}
              </Select>
              <button
                onClick={() => set('inclusions', form.inclusions.filter((_, n) => n !== i))}
                aria-label="Remove inclusion"
                className="grid h-[38px] w-10 shrink-0 place-items-center rounded-lg border border-slate-200 text-slate-400 hover:bg-slate-50">
                <Trash2 className="h-4 w-4" />
              </button>
            </div>
          ))}
          {form.inclusions.length === 0 && (
            <p className="rounded-lg bg-slate-50 px-3 py-3 text-sm text-slate-500">
              No inclusions yet.
            </p>
          )}
        </div>
      </div>

      <div>
        <span className="mb-1 block text-sm font-medium text-slate-700">Room Types</span>
        <p className="mb-2 text-xs text-slate-400">
          Select none to offer this package on every room type.
        </p>
        <div className="space-y-1 rounded-lg border border-slate-200 p-3">
          {roomTypes.map((rt) => (
            <label key={rt.id} className="flex items-center gap-2 text-sm text-slate-700">
              <input type="checkbox" checked={form.room_type_ids.includes(rt.id)}
                onChange={(e) => set('room_type_ids', e.target.checked
                  ? [...form.room_type_ids, rt.id]
                  : form.room_type_ids.filter((i) => i !== rt.id))}
                className="h-4 w-4 rounded border-slate-300 text-brand" />
              {rt.name}
            </label>
          ))}
        </div>
      </div>

      {pkg && (
        <div>
          <span className="mb-1 block text-sm font-medium text-slate-700">Card image</span>
          <div className="flex items-center gap-3">
            {pkg.image_url ? (
              <img src={pkg.image_url} alt={pkg.name}
                className="h-16 w-24 rounded-lg object-cover" />
            ) : (
              <div className="grid h-16 w-24 place-items-center rounded-lg bg-slate-75 text-slate-300">
                <ImageOff className="h-5 w-5" />
              </div>
            )}
            <input ref={fileRef} type="file" accept="image/jpeg,image/png,image/webp"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0]
                if (f) image.mutate(f)
                e.target.value = ''
              }} />
            <button onClick={() => fileRef.current?.click()} disabled={image.isPending}
              className="flex items-center gap-2 rounded-lg border border-slate-200 px-3 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-50">
              {image.isPending ? <Loader2 className="h-4 w-4 animate-spin" />
                : <Camera className="h-4 w-4" />}
              {pkg.image_url ? 'Replace' : 'Upload'}
            </button>
            {pkg.image_url && (
              <button onClick={() => image.mutate(null)} disabled={image.isPending}
                className="flex items-center gap-2 rounded-lg border border-slate-200 px-3 py-2 text-sm font-semibold text-slate-600 hover:bg-slate-50 disabled:opacity-50">
                <Trash2 className="h-4 w-4" /> Remove
              </button>
            )}
          </div>
        </div>
      )}

      {error && (
        <p className="flex items-start gap-2 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" /> {error}
        </p>
      )}
    </Dialog>
  )
}

/* ------------------------------------------------------------ promo dialog --- */
function PromoDialog({
  propertyId, promo, packages, onClose,
}: {
  propertyId: string; promo: PromoCode | null
  packages: ResortPackage[]; onClose: () => void
}) {
  const qc = useQueryClient()
  const [form, setForm] = useState({
    code: promo?.code ?? '', description: promo?.description ?? '',
    discount_type: promo?.discount_type ?? 'percent',
    discount_value: promo ? String(Number(promo.discount_value)) : '10',
    usage_limit: promo?.usage_limit === null || promo === null
      ? '' : String(promo.usage_limit),
    valid_from: promo?.valid_from ?? today(),
    valid_to: promo?.valid_to ?? today(),
    min_nights: promo?.min_nights ? String(promo.min_nights) : '',
    package_id: promo?.package_id ?? '',
    status: promo?.status ?? 'active',
  })
  const [error, setError] = useState<string | null>(null)

  const save = useMutation({
    mutationFn: () => {
      const body = {
        code: form.code.trim(), description: form.description.trim() || null,
        discount_type: form.discount_type,
        discount_value: form.discount_value === '' ? 0 : Number(form.discount_value),
        usage_limit: form.usage_limit === '' ? null : Number(form.usage_limit),
        valid_from: form.valid_from, valid_to: form.valid_to,
        min_nights: form.min_nights === '' ? null : Number(form.min_nights),
        package_id: form.package_id || null,
        status: form.status,
      }
      return promo
        ? updatePromoCode(propertyId, promo.id, { ...body, version: promo.version })
        : createPromoCode(propertyId, body)
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['promoCodes', propertyId] })
      qc.invalidateQueries({ queryKey: ['promotionStats', propertyId] })
      onClose()
    },
    onError: (e) => setError(apiError(e)),
  })

  const set = (k: keyof typeof form, v: string) => setForm((f) => ({ ...f, [k]: v }))

  return (
    <Dialog
      title={promo ? 'Edit Promo Code' : 'Create Promo Code'}
      subtitle="Discount codes for direct bookings and campaigns"
      onClose={onClose}
      footer={
        <>
          <button onClick={onClose}
            className="rounded-lg border border-slate-200 px-4 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50">
            Cancel
          </button>
          <button onClick={() => { setError(null); save.mutate() }} disabled={save.isPending}
            className="flex items-center gap-2 rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white hover:bg-brand-dark disabled:opacity-50">
            {save.isPending && <Loader2 className="h-4 w-4 animate-spin" />} Save
          </button>
        </>
      }>
      <div className="grid grid-cols-[180px_1fr] gap-3">
        <Field label="Code" required>
          <input className={`${input} uppercase`} value={form.code} maxLength={30}
            onChange={(e) => set('code', e.target.value.toUpperCase())} />
        </Field>
        <Field label="Description">
          <input className={input} value={form.description} maxLength={300}
            onChange={(e) => set('description', e.target.value)} />
        </Field>
      </div>
      <div className="grid grid-cols-3 gap-3">
        <Field label="Discount type">
          <Select className={input} value={form.discount_type}
            onChange={(e) => set('discount_type', e.target.value)}>
            <option value="percent">Percentage</option>
            <option value="amount">Fixed amount (₹)</option>
          </Select>
        </Field>
        <Field label="Value" required>
          <input type="number" min={0} step="0.01" className={input}
            value={form.discount_value}
            onChange={(e) => set('discount_value', e.target.value)} />
        </Field>
        <Field label="Usage limit">
          <input type="number" min={0} className={input} value={form.usage_limit}
            placeholder="Unlimited"
            onChange={(e) => set('usage_limit', e.target.value)} />
        </Field>
      </div>
      <div className="grid grid-cols-3 gap-3">
        <Field label="Valid from" required>
          <DateField value={form.valid_from} onChange={(v) => set('valid_from', v)} className={input} />
        </Field>
        <Field label="Expires" required>
          <DateField value={form.valid_to} onChange={(v) => set('valid_to', v)} className={input} />
        </Field>
        <Field label="Min nights">
          <input type="number" min={1} className={input} value={form.min_nights}
            placeholder="Any" onChange={(e) => set('min_nights', e.target.value)} />
        </Field>
      </div>
      <Field label="Restrict to package">
        <Select className={input} value={form.package_id}
          onChange={(e) => set('package_id', e.target.value)}>
          <option value="">Any booking</option>
          {packages.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </Select>
      </Field>
      <Field label="Status">
        <Select className={input} value={form.status}
          onChange={(e) => set('status', e.target.value)}>
          <option value="active">Active</option>
          <option value="inactive">Inactive</option>
        </Select>
      </Field>
      {promo && (
        <p className="rounded-lg bg-slate-50 px-3 py-2 text-sm text-slate-600">
          Redeemed {promo.usage_count} time{promo.usage_count === 1 ? '' : 's'}.
          The usage limit cannot be set below that.
        </p>
      )}
      {error && (
        <p className="flex items-start gap-2 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" /> {error}
        </p>
      )}
    </Dialog>
  )
}

/* ------------------------------------------------------------ add-on dialog --- */
function AddonDialog({
  propertyId, addon, onClose,
}: { propertyId: string; addon: Addon | null; onClose: () => void }) {
  const qc = useQueryClient()
  const [form, setForm] = useState({
    code: addon?.code ?? '', name: addon?.name ?? '',
    description: addon?.description ?? '', category: addon?.category ?? 'other',
    price: addon ? String(Number(addon.price)) : '0',
    pricing_unit: addon?.pricing_unit ?? 'per_stay',
    status: addon?.status ?? 'active',
  })
  const [error, setError] = useState<string | null>(null)

  const save = useMutation({
    mutationFn: () => {
      const body = { ...form, price: form.price === '' ? 0 : Number(form.price) }
      return addon
        ? updateAddon(propertyId, addon.id, { ...body, version: addon.version })
        : createAddon(propertyId, body)
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['addons', propertyId] })
      onClose()
    },
    onError: (e) => setError(apiError(e)),
  })
  const set = (k: keyof typeof form, v: string) => setForm((f) => ({ ...f, [k]: v }))

  return (
    <Dialog
      title={addon ? 'Edit Add-on' : 'New Add-on'}
      subtitle="Extras the property sells, on their own or inside a package"
      onClose={onClose}
      footer={
        <>
          <button onClick={onClose}
            className="rounded-lg border border-slate-200 px-4 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50">
            Cancel
          </button>
          <button onClick={() => { setError(null); save.mutate() }} disabled={save.isPending}
            className="flex items-center gap-2 rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white hover:bg-brand-dark disabled:opacity-50">
            {save.isPending && <Loader2 className="h-4 w-4 animate-spin" />} Save
          </button>
        </>
      }>
      <div className="grid grid-cols-[150px_1fr] gap-3">
        <Field label="Code" required>
          <input className={`${input} uppercase`} value={form.code} maxLength={20}
            onChange={(e) => set('code', e.target.value.toUpperCase())} />
        </Field>
        <Field label="Name" required>
          <input className={input} value={form.name} maxLength={120}
            onChange={(e) => set('name', e.target.value)} />
        </Field>
      </div>
      <Field label="Description">
        <textarea className={`${input} h-20`} value={form.description} maxLength={400}
          onChange={(e) => set('description', e.target.value)} />
      </Field>
      <div className="grid grid-cols-3 gap-3">
        <Field label="Category">
          <Select className={input} value={form.category}
            onChange={(e) => set('category', e.target.value)}>
            {ADDON_CATEGORIES.map((c) => (
              <option key={c.code} value={c.code}>{c.label}</option>
            ))}
          </Select>
        </Field>
        <Field label="Price (₹)">
          <input type="number" min={0} step="0.01" className={input} value={form.price}
            onChange={(e) => set('price', e.target.value)} />
        </Field>
        <Field label="Charged">
          <Select className={input} value={form.pricing_unit}
            onChange={(e) => set('pricing_unit', e.target.value)}>
            {PRICING_UNITS.map((u) => (
              <option key={u.code} value={u.code}>{u.label}</option>
            ))}
          </Select>
        </Field>
      </div>
      <Field label="Status">
        <Select className={input} value={form.status}
          onChange={(e) => set('status', e.target.value)}>
          <option value="active">Active</option>
          <option value="inactive">Inactive</option>
        </Select>
      </Field>
      {error && (
        <p className="flex items-start gap-2 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" /> {error}
        </p>
      )}
    </Dialog>
  )
}

/* -------------------------------------------------------------------- page --- */
export default function PackagesPromotions({ propertyId }: { propertyId: string }) {
  const qc = useQueryClient()
  const [tab, setTab] = useState<Tab>('Packages')
  const [search, setSearch] = useState('')
  const [editing, setEditing] = useState<ResortPackage | null>(null)
  const [creatingPkg, setCreatingPkg] = useState(false)
  const [editingPromo, setEditingPromo] = useState<PromoCode | null>(null)
  const [creatingPromo, setCreatingPromo] = useState(false)
  const [editingAddon, setEditingAddon] = useState<Addon | null>(null)
  const [creatingAddon, setCreatingAddon] = useState(false)
  const [redeeming, setRedeeming] = useState<PromoCode | null>(null)
  const [guestName, setGuestName] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)

  const on = propertyId !== ''
  const pkgQ = useQuery({
    queryKey: ['packages', propertyId, search],
    queryFn: () => listPackages(propertyId, search ? { search } : {}), enabled: on,
  })
  const promoQ = useQuery({
    queryKey: ['promoCodes', propertyId], queryFn: () => listPromoCodes(propertyId),
    enabled: on,
  })
  const addonQ = useQuery({
    queryKey: ['addons', propertyId], queryFn: () => listAddons(propertyId), enabled: on,
  })
  const redQ = useQuery({
    queryKey: ['redemptions', propertyId], queryFn: () => listRedemptions(propertyId),
    enabled: on && tab === 'Redemption',
  })
  const typesQ = useQuery({
    queryKey: ['managedRoomTypes', propertyId],
    queryFn: () => listManagedRoomTypes(propertyId), enabled: on,
  })

  const packages = pkgQ.data ?? []
  const promos = promoQ.data ?? []
  const addons = addonQ.data ?? []
  const roomTypes = (typesQ.data ?? []).map((t) => ({ id: t.id, name: t.name }))

  const togglePkg = useMutation({
    mutationFn: (p: ResortPackage) => setPackageStatus(propertyId, p.id, {
      status: p.status === 'active' ? 'inactive' : 'active', version: p.version,
    }),
    onMutate: (p) => setBusyId(p.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['packages', propertyId] }),
    onError: (e) => setError(apiError(e)),
    onSettled: () => setBusyId(null),
  })
  const redeem = useMutation({
    mutationFn: () => redeemPromoCode(propertyId, redeeming!.id, {
      guest_name: guestName.trim() || undefined,
    }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['promoCodes', propertyId] })
      qc.invalidateQueries({ queryKey: ['redemptions', propertyId] })
      qc.invalidateQueries({ queryKey: ['promotionStats', propertyId] })
      setRedeeming(null); setGuestName('')
    },
    onError: (e) => setError(apiError(e)),
  })

  /** Every package, featured ones first.
   *
   * These were two lists in two bordered cards under two headings, which read
   * as two kinds of thing. They are one kind of thing with a flag on it: the
   * same editor creates both, and clearing the Featured tick moved a package
   * from one card to the other as though it had changed species.
   *
   * `sort` on a copy -- `packages` comes from the query cache, and sorting it
   * in place would mutate what every other reader of that cache sees.
   */
  const ordered = [...packages].sort(
    (a, b) => Number(b.is_featured) - Number(a.is_featured))

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <h1 className="flex items-center gap-2 text-display text-ink">
          <Tag size={26} className="text-brand" /> Packages &amp; Promotions
        </h1>
        <div className="flex flex-wrap items-center gap-2">
          <button onClick={() => { setEditingPromo(null); setCreatingPromo(true) }}
            className="flex items-center gap-1.5 whitespace-nowrap rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-600 hover:bg-slate-50">
            <Plus size={15} /> Create Promo Code
          </button>
          <button onClick={() => { setEditing(null); setCreatingPkg(true) }}
            className="flex items-center gap-1.5 whitespace-nowrap rounded-lg bg-brand px-3 py-2 text-sm font-medium text-white hover:bg-brand-dark disabled:opacity-40">
            <Plus size={15} /> New Package
          </button>
        </div>
      </div>

      <div className="flex gap-1 border-b border-slate-200">
        {TABS.map((t) => (
          <button key={t} onClick={() => setTab(t)}
            className={`border-b-2 px-4 py-3 text-sm font-semibold transition ${
              tab === t ? 'border-brand text-brand'
                : 'border-transparent text-slate-500 hover:text-slate-700'
            }`}>
            {t}
          </button>
        ))}
      </div>

      {error && (
        <p className="flex items-start justify-between gap-3 rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">
          <span className="flex items-start gap-2">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" /> {error}
          </span>
          <button onClick={() => setError(null)} aria-label="Dismiss">
            <X className="h-4 w-4" />
          </button>
        </p>
      )}

      {/* ------------------------------------------------------------ packages */}
      {tab === 'Packages' && (
        <div className="space-y-4">
          {/* Search is the only filter, so the row is just that, at the right
              end where Reservations keeps it. The card no longer repeats the
              tab's name as its own heading. */}
          <div className="flex flex-wrap items-center gap-2">
            <div className="flex min-w-[240px] flex-1 items-center justify-end gap-2">
              <div className="relative min-w-0 max-w-sm flex-1">
                <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                <input aria-label="Search packages" placeholder="Search packages..."
                  className="w-full rounded-lg border border-slate-200 bg-slate-50 py-2 pl-9 pr-3 text-sm outline-none focus:border-brand"
                  value={search} onChange={(e) => setSearch(e.target.value)} />
              </div>
            </div>
          </div>
          <div className="rounded-xl border border-slate-200 bg-white p-5">
            {pkgQ.isLoading && (
              <div className="grid h-40 place-items-center text-slate-400">
                <Loader2 className="h-6 w-6 animate-spin" />
              </div>
            )}
            {!pkgQ.isLoading && ordered.length === 0 && (
              <p className="py-12 text-center text-sm text-slate-500">
                {search
                  ? 'No package matches that search.'
                  : 'No packages yet. Create one to show it here.'}
              </p>
            )}
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
              {ordered.map((p) => (
                <PackageCard key={p.id} pkg={p} busy={busyId === p.id}
                  onEdit={() => { setCreatingPkg(false); setEditing(p) }}
                  onToggle={() => togglePkg.mutate(p)} />
              ))}
            </div>
          </div>

        </div>
      )}

      {/* --------------------------------------------------------- promo codes */}
      {/* Only here. The Packages tab used to carry the first five promo codes
          as well, with a "View All" link across to this tab -- the same rows
          in two places, editable from both, and a tab strip already sitting
          above offering the real list. Five of something is not a summary
          either: it answers no question the full list does not. */}
      {tab === 'Promo Codes' && (
        <div className="rounded-xl border border-slate-200 bg-white p-5">
          <PromoTable promos={promos} loading={promoQ.isLoading}
            onEdit={(p) => { setCreatingPromo(false); setEditingPromo(p) }}
            onRedeem={setRedeeming} />
        </div>
      )}

      {/* -------------------------------------------------------------- add-ons */}
      {tab === 'Add-ons' && (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-sm text-slate-500">
              Extras the property sells. A package inclusion can point at one of
              these, so its price and name live in a single place.
            </p>
            <button onClick={() => { setEditingAddon(null); setCreatingAddon(true) }}
              className="flex shrink-0 items-center gap-2 rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white hover:bg-brand-dark">
              <Plus className="h-4 w-4" /> New Add-on
            </button>
          </div>
          <div className="overflow-hidden rounded-xl border border-slate-200 bg-white">
            <table className="w-full text-left">
              <thead>
                <tr className="border-b border-slate-200 bg-slate-50/60">
                  {['Code', 'Name', 'Category', 'Price', 'Charged', 'In Packages',
                    'Status', ''].map((h) => (
                    <th key={h} className="px-5 py-3 text-sm font-semibold text-slate-600">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {addonQ.isLoading && (
                  <tr><td colSpan={8} className="px-5 py-10 text-center text-slate-400">
                    <Loader2 className="mx-auto h-5 w-5 animate-spin" />
                  </td></tr>
                )}
                {!addonQ.isLoading && addons.length === 0 && (
                  <tr><td colSpan={8} className="px-5 py-10 text-center text-sm text-slate-500">
                    No add-ons yet.
                  </td></tr>
                )}
                {addons.map((a) => (
                  <tr key={a.id} className="border-b border-slate-100 last:border-0">
                    <td className="px-5 py-3 text-sm font-bold text-slate-700">{a.code}</td>
                    <td className="px-5 py-3 text-sm text-slate-700">{a.name}</td>
                    <td className="px-5 py-3 text-sm text-slate-600">{a.category_label}</td>
                    <td className="px-5 py-3 text-sm tabular-nums text-slate-700">
                      {money(a.price)}
                    </td>
                    <td className="px-5 py-3 text-sm text-slate-600">{a.pricing_unit_label}</td>
                    <td className="px-5 py-3 text-sm text-slate-700">{a.used_in_packages}</td>
                    <td className="px-5 py-3">
                      <span className={`rounded-full px-3 py-1 text-xs font-semibold ${
                        a.status === 'active'
                          ? 'bg-emerald-50 text-emerald-700' : 'bg-slate-75 text-slate-500'}`}>
                        {a.status === 'active' ? 'Active' : 'Inactive'}
                      </span>
                    </td>
                    <td className="px-5 py-3 text-right">
                      <button onClick={() => { setCreatingAddon(false); setEditingAddon(a) }}
                        className="text-sm font-semibold text-brand hover:underline">
                        Edit
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ----------------------------------------------------------- redemption */}
      {tab === 'Redemption' && (
        <div className="space-y-4">
          <p className="flex items-start gap-2 rounded-lg bg-sky-50 px-4 py-3 text-sm text-sky-800">
            <Ticket className="mt-0.5 h-4 w-4 shrink-0" />
            Every redemption is logged here as it happens. The booking flow does
            not apply promo codes yet, so entries appear only when a code is
            redeemed from the Promo Codes tab.
          </p>
          <div className="overflow-hidden rounded-xl border border-slate-200 bg-white">
            <table className="w-full text-left">
              <thead>
                <tr className="border-b border-slate-200 bg-slate-50/60">
                  {['Code', 'Guest', 'Reservation', 'Discount', 'Redeemed By',
                    'When'].map((h) => (
                    <th key={h} className="px-5 py-3 text-sm font-semibold text-slate-600">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {redQ.isLoading && (
                  <tr><td colSpan={6} className="px-5 py-10 text-center text-slate-400">
                    <Loader2 className="mx-auto h-5 w-5 animate-spin" />
                  </td></tr>
                )}
                {!redQ.isLoading && (redQ.data ?? []).length === 0 && (
                  <tr><td colSpan={6} className="px-5 py-10 text-center text-sm text-slate-500">
                    No promo code has been redeemed yet.
                  </td></tr>
                )}
                {(redQ.data ?? []).map((r) => (
                  <tr key={r.id} className="border-b border-slate-100 last:border-0">
                    <td className="px-5 py-3 text-sm font-bold text-slate-700">{r.code}</td>
                    <td className="px-5 py-3 text-sm text-slate-700">{r.guest_name ?? '—'}</td>
                    <td className="px-5 py-3 text-sm text-slate-600">
                      {r.reservation_number ?? '—'}
                    </td>
                    <td className="px-5 py-3 text-sm tabular-nums text-slate-700">
                      {r.discount_amount ? money(r.discount_amount) : '—'}
                    </td>
                    <td className="px-5 py-3 text-sm text-slate-600">
                      {r.redeemed_by_name ?? '—'}
                    </td>
                    <td className="whitespace-nowrap px-5 py-3 text-sm text-slate-600">
                      {stamp(r.redeemed_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {(creatingPkg || editing) && (
        <PackageDialog propertyId={propertyId} pkg={editing} addons={addons}
          roomTypes={roomTypes}
          onClose={() => { setCreatingPkg(false); setEditing(null) }} />
      )}
      {(creatingPromo || editingPromo) && (
        <PromoDialog propertyId={propertyId} promo={editingPromo} packages={packages}
          onClose={() => { setCreatingPromo(false); setEditingPromo(null) }} />
      )}
      {(creatingAddon || editingAddon) && (
        <AddonDialog propertyId={propertyId} addon={editingAddon}
          onClose={() => { setCreatingAddon(false); setEditingAddon(null) }} />
      )}

      {redeeming && (
        <Dialog title={`Redeem ${redeeming.code}`}
          subtitle="Records one use against this code"
          onClose={() => { setRedeeming(null); setGuestName('') }}
          footer={
            <>
              <button onClick={() => { setRedeeming(null); setGuestName('') }}
                className="rounded-lg border border-slate-200 px-4 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50">
                Cancel
              </button>
              <button onClick={() => { setError(null); redeem.mutate() }}
                disabled={redeem.isPending}
                className="flex items-center gap-2 rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white hover:bg-brand-dark disabled:opacity-50">
                {redeem.isPending && <Loader2 className="h-4 w-4 animate-spin" />} Redeem
              </button>
            </>
          }>
          <Field label="Guest name">
            <input className={input} value={guestName} maxLength={200}
              onChange={(e) => setGuestName(e.target.value)} />
          </Field>
          <p className="text-sm text-slate-500">
            Used {redeeming.usage_count}
            {redeeming.usage_limit !== null && ` of ${redeeming.usage_limit}`}.
          </p>
        </Dialog>
      )}
    </div>
  )
}

/* --------------------------------------------------------------- promo table --- */
function PromoTable({
  promos, loading, onEdit, onRedeem,
}: {
  promos: PromoCode[]; loading: boolean
  onEdit: (p: PromoCode) => void; onRedeem: (p: PromoCode) => void
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[860px] text-left">
        <thead>
          <tr className="border-b border-slate-200">
            {['Code', 'Description', 'Usage', 'Usage Limit', 'Value', 'Expiry Date',
              'Status', 'Actions'].map((h) => (
              <th key={h} className="px-4 py-3 text-sm font-semibold text-slate-600">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {loading && (
            <tr><td colSpan={8} className="px-4 py-10 text-center text-slate-400">
              <Loader2 className="mx-auto h-5 w-5 animate-spin" />
            </td></tr>
          )}
          {!loading && promos.length === 0 && (
            <tr><td colSpan={8} className="px-4 py-10 text-center text-sm text-slate-500">
              No promo codes yet.
            </td></tr>
          )}
          {promos.map((p) => (
            <tr key={p.id} className="border-b border-slate-100 last:border-0">
              <td className="px-4 py-3 text-sm font-bold text-slate-700">{p.code}</td>
              <td className="px-4 py-3 text-sm text-slate-600">{p.description ?? '—'}</td>
              <td className="px-4 py-3 text-sm tabular-nums text-slate-700">
                {p.usage_count}
              </td>
              <td className="px-4 py-3 text-sm tabular-nums text-slate-700">
                {p.usage_limit ?? 'Unlimited'}
              </td>
              <td className="px-4 py-3 text-sm font-semibold text-slate-700">
                {p.value_label}
              </td>
              <td className="whitespace-nowrap px-4 py-3 text-sm text-slate-600">
                {day(p.valid_to)}
              </td>
              <td className="px-4 py-3">
                <span className={`rounded-full px-3 py-1 text-xs font-semibold ${
                  STATE_STYLE[p.state] ?? 'bg-slate-75 text-slate-600'}`}>
                  {p.state_label}
                </span>
              </td>
              <td className="px-4 py-3">
                <div className="flex items-center gap-3">
                  <button onClick={() => onEdit(p)} aria-label={`Edit ${p.code}`}
                    className="text-slate-400 hover:text-brand">
                    <Pencil className="h-4 w-4" />
                  </button>
                  <button onClick={() => onRedeem(p)} disabled={p.state !== 'active'}
                    title={p.state === 'active'
                      ? 'Record a redemption' : `Cannot redeem: ${p.state_label}`}
                    className="text-sm font-semibold text-brand hover:underline disabled:cursor-not-allowed disabled:text-slate-300 disabled:no-underline">
                    Redeem
                  </button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
