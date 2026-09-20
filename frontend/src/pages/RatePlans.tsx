import { useEffect, useMemo, useRef, useState } from 'react'
import Select from '../components/Select'
import { FILTER_SELECT } from '../lib/controls'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  AlertCircle, Ban, Building2, Camera, CheckCircle2, Copy, Ellipsis, ImageOff,
  Loader2, Plus, Power, Search, Tag, Trash2, Users, Utensils, X, XCircle,
} from 'lucide-react'
import {
  listRatePlans, createRatePlan, updateRatePlan, setRatePlanStatus,
  duplicateRatePlan, uploadRatePlanImage, deleteRatePlanImage,
  listMealPlans, createMealPlan, updateMealPlan, listManagedRoomTypes,
  listCommercialAccounts, CANCELLATION_POLICIES,
  type RatePlan, type MealPlan, type RatePlanFilters,
} from '../api'
import { useOrgId } from '../hooks/useProperty'

/**
 * Screen 033 — Rate Plans.
 *
 * A rate plan holds an adjustment to the room type's base rate, never a price
 * of its own, so the "from" figure on each card is computed server-side and a
 * change to a room type's rate moves every plan built on it.
 *
 * Corporate Rates is the same list filtered to corporate plans rather than a
 * second entity — the mockup shows "Corporate BlueWave" among the ordinary
 * plans, so treating it as a separate record would mean storing it twice.
 */

const TABS = ['Rate Plans', 'Meal Plans', 'Corporate Rates', 'Taxes'] as const
type Tab = typeof TABS[number]

const input = 'w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700'
const select = `${FILTER_SELECT} bg-white outline-none focus:border-brand`

function money(v: string | null): string {
  if (v === null) return '—'
  return `₹${Number(v).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`
}

function apiError(e: unknown): string {
  const d = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  if (typeof d === 'string') return d
  if (Array.isArray(d)) return 'Some fields are invalid. Check the highlighted values.'
  return 'Could not save. Please try again.'
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

interface FormState {
  code: string
  name: string
  description: string
  plan_type: string
  corporate_account: string
  commercial_account_id: string
  meal_plan_id: string
  pricing_mode: 'adjustment' | 'fixed'
  adjustment_direction: string
  adjustment_type: string
  adjustment_value: string
  flat_rate: string
  extra_adult_charge: string
  child_charge: string
  min_stay: number
  min_guests: number
  max_guests: number
  refundable: boolean
  free_cancellation_hours: string
  cancellation_policy: string
  status: string
  room_type_ids: string[]
}

const EMPTY: FormState = {
  code: '', name: '', description: '', plan_type: 'standard', corporate_account: '',
  commercial_account_id: '',
  meal_plan_id: '', pricing_mode: 'adjustment',
  adjustment_direction: 'increase', adjustment_type: 'percent',
  adjustment_value: '0', flat_rate: '',
  extra_adult_charge: '', child_charge: '', min_stay: 1,
  min_guests: 1, max_guests: 2, refundable: true, free_cancellation_hours: '24',
  cancellation_policy: CANCELLATION_POLICIES[0], status: 'active', room_type_ids: [],
}

function fromPlan(p: RatePlan): FormState {
  return {
    code: p.code, name: p.name, description: p.description ?? '',
    plan_type: p.plan_type, corporate_account: p.corporate_account ?? '',
    commercial_account_id: p.commercial_account_id ?? '',
    meal_plan_id: p.meal_plan_id ?? '',
    pricing_mode: p.flat_rate !== null ? 'fixed' : 'adjustment',
    adjustment_direction: p.adjustment_direction,
    adjustment_type: p.adjustment_type,
    adjustment_value: String(Number(p.adjustment_value)),
    flat_rate: p.flat_rate !== null ? String(Number(p.flat_rate)) : '',
    extra_adult_charge: p.extra_adult_charge ? String(Number(p.extra_adult_charge)) : '',
    child_charge: p.child_charge ? String(Number(p.child_charge)) : '',
    min_stay: p.min_stay, min_guests: p.min_guests, max_guests: p.max_guests,
    refundable: p.refundable,
    free_cancellation_hours: p.free_cancellation_hours === null
      ? '' : String(p.free_cancellation_hours),
    cancellation_policy: p.cancellation_policy ?? '',
    status: p.status, room_type_ids: p.room_type_ids,
  }
}

/* ------------------------------------------------------------------- card --- */
function PlanCard({
  plan, selected, onSelect, onDuplicate, onToggleStatus, busy,
}: {
  plan: RatePlan
  selected: boolean
  onSelect: () => void
  onDuplicate: () => void
  onToggleStatus: () => void
  busy: boolean
}) {
  const [menu, setMenu] = useState(false)
  const active = plan.status === 'active'
  return (
    <div onClick={onSelect}
      className={`flex cursor-pointer gap-4 rounded-xl border bg-white p-3 transition ${
        selected ? 'border-brand ring-1 ring-brand/30' : 'border-slate-200 hover:border-slate-300'
      }`}>
      {plan.image_url ? (
        <img src={plan.image_url} alt={plan.name}
          className="h-20 w-24 shrink-0 rounded-lg object-cover" />
      ) : (
        <div className="grid h-20 w-24 shrink-0 place-items-center rounded-lg bg-slate-75 text-slate-300">
          <ImageOff className="h-6 w-6" />
        </div>
      )}

      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-lg font-bold text-slate-800">{plan.name}</p>
          <span className="rounded bg-sky-50 px-2 py-0.5 text-xs font-bold text-sky-600">
            {plan.code}
          </span>
          {plan.plan_type === 'corporate' && plan.corporate_account && (
            <span className="rounded bg-violet-50 px-2 py-0.5 text-xs font-semibold text-violet-600">
              {plan.corporate_account}
            </span>
          )}
        </div>
        {plan.description && (
          <p className="mt-0.5 truncate text-sm text-slate-500">{plan.description}</p>
        )}
        <div className="mt-2 flex flex-wrap items-center gap-x-5 gap-y-1 text-sm">
          <span className={`flex items-center gap-1.5 ${
            plan.refundable ? 'text-slate-600' : 'text-red-600'}`}>
            {plan.refundable
              ? <CheckCircle2 className="h-4 w-4 text-emerald-500" />
              : <XCircle className="h-4 w-4" />}
            {plan.cancellation_label}
          </span>
          <span className="flex items-center gap-1.5 text-slate-600">
            <Utensils className="h-4 w-4 text-slate-400" />
            {plan.meal_plan_name ?? 'No meal plan'}
          </span>
          <span className="flex items-center gap-1.5 text-slate-600">
            <Users className="h-4 w-4 text-slate-400" />
            {plan.min_guests}–{plan.max_guests} Guests
          </span>
        </div>
      </div>

      <div className="shrink-0 text-right">
        <p className="text-sm text-slate-500">From</p>
        <p className="text-xl font-bold text-slate-800">{money(plan.from_rate)}</p>
        <p className="text-sm text-slate-500">per night</p>
      </div>

      <div className="flex shrink-0 items-start gap-2">
        <span className={`flex items-center gap-1 rounded-full px-3 py-1 text-xs font-semibold ${
          active ? 'bg-emerald-50 text-emerald-700' : 'bg-slate-75 text-slate-500'}`}>
          {active ? <CheckCircle2 className="h-3.5 w-3.5" /> : <Ban className="h-3.5 w-3.5" />}
          {active ? 'Active' : 'Inactive'}
        </span>
        <div className="relative" onClick={(e) => e.stopPropagation()}>
          <button onClick={() => setMenu((v) => !v)} aria-label={`Actions for ${plan.name}`}
            disabled={busy}
            className="grid h-8 w-8 place-items-center rounded-lg text-slate-400 hover:bg-slate-100 disabled:opacity-50">
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Ellipsis className="h-5 w-5" />}
          </button>
          {menu && (
            <div className="absolute right-0 z-20 mt-1 w-44 overflow-hidden rounded-lg border border-slate-200 bg-white shadow-lg">
              <button onClick={() => { setMenu(false); onSelect() }}
                className="block w-full px-4 py-2 text-left text-sm text-slate-700 hover:bg-slate-50">
                Edit
              </button>
              <button onClick={() => { setMenu(false); onDuplicate() }}
                className="block w-full px-4 py-2 text-left text-sm text-slate-700 hover:bg-slate-50">
                Duplicate
              </button>
              <button onClick={() => { setMenu(false); onToggleStatus() }}
                className="block w-full px-4 py-2 text-left text-sm text-slate-700 hover:bg-slate-50">
                {active ? 'Deactivate' : 'Activate'}
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

/* ----------------------------------------------------------------- drawer --- */
function PlanDrawer({
  propertyId, plan, mealPlans, roomTypes, onClose,
}: {
  propertyId: string
  plan: RatePlan | null            // null = create
  mealPlans: MealPlan[]
  roomTypes: { id: string; name: string }[]
  onClose: () => void
}) {
  const qc = useQueryClient()
  const orgId = useOrgId()
  const [form, setForm] = useState<FormState>(plan ? fromPlan(plan) : EMPTY)
  const [error, setError] = useState<string | null>(null)
  // Only fetched when the drawer needs it, which is when the plan is
  // corporate. A standard plan has no company to pick.
  const accounts = useQuery({
    queryKey: ['commercialAccounts', orgId],
    queryFn: () => listCommercialAccounts(orgId, { status: 'active' }),
    enabled: orgId !== '' && form.plan_type === 'corporate',
  })
  const fileRef = useRef<HTMLInputElement>(null)

  useEffect(() => { setForm(plan ? fromPlan(plan) : EMPTY); setError(null) }, [plan])

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['ratePlans', propertyId] })
    qc.invalidateQueries({ queryKey: ['mealPlans', propertyId] })
  }

  const payload = () => ({
    code: form.code.trim(),
    name: form.name.trim(),
    description: form.description.trim() || null,
    plan_type: form.plan_type,
    corporate_account: form.corporate_account.trim() || null,
    commercial_account_id: form.commercial_account_id || null,
    meal_plan_id: form.meal_plan_id || null,
    adjustment_direction: form.adjustment_direction,
    adjustment_type: form.adjustment_type,
    adjustment_value: form.adjustment_value === '' ? 0 : Number(form.adjustment_value),
    // A fixed price and an adjustment are alternatives, so only one is sent.
    flat_rate: form.pricing_mode === 'fixed' && form.flat_rate !== ''
      ? Number(form.flat_rate) : null,
    extra_adult_charge: form.extra_adult_charge === '' ? null : Number(form.extra_adult_charge),
    child_charge: form.child_charge === '' ? null : Number(form.child_charge),
    min_stay: form.min_stay,
    min_guests: form.min_guests,
    max_guests: form.max_guests,
    refundable: form.refundable,
    // Free-cancellation hours are meaningless on a non-refundable plan, and the
    // API rejects the combination, so drop them rather than send a conflict.
    free_cancellation_hours: !form.refundable || form.free_cancellation_hours === ''
      ? null : Number(form.free_cancellation_hours),
    cancellation_policy: form.cancellation_policy.trim() || null,
    status: form.status,
    room_type_ids: form.room_type_ids,
  })

  const save = useMutation({
    mutationFn: () => plan
      ? updateRatePlan(propertyId, plan.id, { ...payload(), version: plan.version })
      : createRatePlan(propertyId, payload()),
    onSuccess: () => { refresh(); onClose() },
    onError: (e) => setError(apiError(e)),
  })

  const duplicate = useMutation({
    mutationFn: () => duplicateRatePlan(propertyId, plan!.id),
    onSuccess: () => { refresh(); onClose() },
    onError: (e) => setError(apiError(e)),
  })

  const toggle = useMutation({
    mutationFn: () => setRatePlanStatus(propertyId, plan!.id, {
      status: plan!.status === 'active' ? 'inactive' : 'active',
      version: plan!.version,
    }),
    onSuccess: () => { refresh(); onClose() },
    onError: (e) => setError(apiError(e)),
  })

  const image = useMutation({
    mutationFn: (file: File | null) => file
      ? uploadRatePlanImage(propertyId, plan!.id, file)
      : deleteRatePlanImage(propertyId, plan!.id),
    onSuccess: refresh,
    onError: (e) => setError(apiError(e)),
  })

  const busy = save.isPending || duplicate.isPending || toggle.isPending
  const set = <K extends keyof FormState>(k: K, v: FormState[K]) =>
    setForm((f) => ({ ...f, [k]: v }))

  return (
    <aside className="flex h-full min-h-0 flex-col rounded-xl border border-slate-200 bg-white">
      <div className="flex items-start justify-between gap-3 border-b border-slate-100 px-5 py-4">
        <div>
          <h2 className="text-xl font-bold text-ink">
            {plan ? 'Edit Rate Plan' : 'Create Rate Plan'}
          </h2>
          <p className="text-sm text-slate-500">
            {plan ? 'Update rate plan details and policies' : 'Define a new way to sell your rooms'}
          </p>
        </div>
        <button onClick={onClose} aria-label="Close"
          className="rounded p-1 text-slate-400 hover:bg-slate-100">
          <X className="h-5 w-5" />
        </button>
      </div>

      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-5 py-4">
        <div className="grid grid-cols-[1fr_120px] gap-3">
          <Field label="Rate Plan Name" required>
            <input className={input} value={form.name} maxLength={120}
              onChange={(e) => set('name', e.target.value)} />
          </Field>
          <Field label="Short Code" required>
            <input className={`${input} uppercase`} value={form.code} maxLength={12}
              onChange={(e) => set('code', e.target.value.toUpperCase())} />
          </Field>
        </div>

        <Field label="Description">
          <textarea className={`${input} h-20`} value={form.description} maxLength={500}
            onChange={(e) => set('description', e.target.value)} />
        </Field>

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

        <Field label="Meal Plan">
          <Select className={input} value={form.meal_plan_id}
            onChange={(e) => set('meal_plan_id', e.target.value)}>
            <option value="">No meal plan</option>
            {mealPlans.filter((m) => m.status === 'active').map((m) => (
              <option key={m.id} value={m.id}>{m.name}</option>
            ))}
          </Select>
        </Field>

        <div className="grid grid-cols-2 gap-3">
          <Field label="Extra Adult Charge (per night)">
            <input type="number" min={0} step="0.01" className={input}
              value={form.extra_adult_charge}
              onChange={(e) => set('extra_adult_charge', e.target.value)} />
          </Field>
          <Field label="Child Charge (per night)">
            <input type="number" min={0} step="0.01" className={input}
              value={form.child_charge}
              onChange={(e) => set('child_charge', e.target.value)} />
          </Field>
        </div>

        <div className="grid grid-cols-3 gap-3">
          <Field label="Minimum Stay">
            <Select className={input} value={form.min_stay}
              onChange={(e) => set('min_stay', Number(e.target.value))}>
              {[1, 2, 3, 4, 5, 7, 14].map((n) => (
                <option key={n} value={n}>{n} night{n === 1 ? '' : 's'}</option>
              ))}
            </Select>
          </Field>
          <Field label="Min Guests">
            <input type="number" min={1} className={input} value={form.min_guests}
              onChange={(e) => set('min_guests', Number(e.target.value))} />
          </Field>
          <Field label="Max Guests">
            <input type="number" min={1} className={input} value={form.max_guests}
              onChange={(e) => set('max_guests', Number(e.target.value))} />
          </Field>
        </div>

        <Field label="Cancellation Policy">
          <Select className={input} value={form.cancellation_policy}
            onChange={(e) => set('cancellation_policy', e.target.value)}>
            <option value="">No policy stated</option>
            {CANCELLATION_POLICIES.map((p) => <option key={p} value={p}>{p}</option>)}
          </Select>
        </Field>

        <label className="flex items-center gap-2 text-sm font-medium text-slate-700">
          <input type="checkbox" checked={form.refundable}
            onChange={(e) => set('refundable', e.target.checked)}
            className="h-4 w-4 rounded border-slate-300 text-brand" />
          Refundable
        </label>

        {form.refundable && (
          <Field label="Free cancellation window (hours before arrival)">
            <input type="number" min={0} className={input}
              value={form.free_cancellation_hours}
              placeholder="Leave blank for no stated window"
              onChange={(e) => set('free_cancellation_hours', e.target.value)} />
          </Field>
        )}

        <Field label="Plan Type">
          <Select className={input} value={form.plan_type}
            onChange={(e) => set('plan_type', e.target.value)}>
            <option value="standard">Standard</option>
            <option value="corporate">Corporate</option>
            <option value="package">Package</option>
          </Select>
        </Field>

        {form.plan_type === 'corporate' && (
          <Field label="Corporate Account" required>
            {/* Chosen, not typed. A name typed here could never be matched
                against Companies & Agents, so taking a booking for that
                company had no way to find its negotiated rate. */}
            <Select className={input} value={form.commercial_account_id}
              onChange={(e) => set('commercial_account_id', e.target.value)}>
              <option value="">Choose the company…</option>
              {(accounts.data?.rows ?? []).map((a) => (
                <option key={a.id} value={a.id}>{a.name} ({a.code})</option>
              ))}
            </Select>
            {form.corporate_account && !form.commercial_account_id && (
              // A plan created before this was an account. Say what it used
              // to name, so whoever opens it can pick the right company.
              <p className="mt-1 text-xs text-caution">
                Previously typed as “{form.corporate_account}” — choose the
                matching account so bookings can find this rate.
              </p>
            )}
          </Field>
        )}

        <div>
          <span className="mb-1 block text-sm font-medium text-slate-700">Room Types</span>
          <p className="mb-2 text-xs text-slate-400">
            Select none to offer this plan on every room type.
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
            {roomTypes.length === 0 && (
              <p className="text-sm text-slate-500">No room types configured.</p>
            )}
          </div>
        </div>

        {plan && (
          <div>
            <span className="mb-1 block text-sm font-medium text-slate-700">Image</span>
            <div className="flex items-center gap-3">
              {plan.image_url ? (
                <img src={plan.image_url} alt={plan.name}
                  className="h-16 w-20 rounded-lg object-cover" />
              ) : (
                <div className="grid h-16 w-20 place-items-center rounded-lg bg-slate-75 text-slate-300">
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
                {image.isPending
                  ? <Loader2 className="h-4 w-4 animate-spin" />
                  : <Camera className="h-4 w-4" />}
                {plan.image_url ? 'Replace' : 'Upload'}
              </button>
              {plan.image_url && (
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
      </div>

      <div className="flex flex-wrap items-center justify-end gap-2 border-t border-slate-100 px-5 py-4">
        {plan && (
          <>
            <button onClick={() => duplicate.mutate()} disabled={busy}
              className="flex items-center gap-2 rounded-lg border border-slate-200 px-4 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-50">
              <Copy className="h-4 w-4" /> Duplicate
            </button>
            <button onClick={() => toggle.mutate()} disabled={busy}
              className="flex items-center gap-2 rounded-lg border border-red-200 px-4 py-2 text-sm font-semibold text-red-600 hover:bg-red-50 disabled:opacity-50">
              <Power className="h-4 w-4" />
              {plan.status === 'active' ? 'Deactivate' : 'Activate'}
            </button>
          </>
        )}
        <button onClick={() => { setError(null); save.mutate() }} disabled={busy}
          className="flex items-center gap-2 rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white hover:bg-brand-dark disabled:opacity-50">
          {save.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
          {plan ? 'Save Changes' : 'Create Rate Plan'}
        </button>
      </div>
    </aside>
  )
}

/* ------------------------------------------------------------- meal plans --- */
function MealPlansTab({ propertyId }: { propertyId: string }) {
  const qc = useQueryClient()
  const [editing, setEditing] = useState<MealPlan | null>(null)
  const [creating, setCreating] = useState(false)
  const [form, setForm] = useState({ code: '', name: '', description: '', display_order: 0 })
  const [error, setError] = useState<string | null>(null)

  const { data = [], isLoading } = useQuery({
    queryKey: ['mealPlans', propertyId], queryFn: () => listMealPlans(propertyId),
    enabled: propertyId !== '',
  })

  const done = () => {
    qc.invalidateQueries({ queryKey: ['mealPlans', propertyId] })
    qc.invalidateQueries({ queryKey: ['ratePlans', propertyId] })
    setEditing(null); setCreating(false); setError(null)
  }
  const save = useMutation({
    mutationFn: () => editing
      ? updateMealPlan(propertyId, editing.id, {
          ...form, status: editing.status, version: editing.version })
      : createMealPlan(propertyId, { ...form, status: 'active' }),
    onSuccess: done,
    onError: (e) => setError(apiError(e)),
  })
  const toggle = useMutation({
    mutationFn: (m: MealPlan) => updateMealPlan(propertyId, m.id, {
      code: m.code, name: m.name, description: m.description,
      display_order: m.display_order,
      status: m.status === 'active' ? 'inactive' : 'active', version: m.version,
    }),
    onSuccess: done,
    onError: (e) => setError(apiError(e)),
  })

  const open = (m: MealPlan | null) => {
    setEditing(m); setCreating(m === null); setError(null)
    setForm(m
      ? { code: m.code, name: m.name, description: m.description ?? '',
          display_order: m.display_order }
      : { code: '', name: '', description: '', display_order: data.length + 1 })
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <p className="text-sm text-slate-500">
          Board bases a rate plan can include. A meal plan in use by an active
          rate plan cannot be deactivated.
        </p>
        <button onClick={() => open(null)}
          className="flex shrink-0 items-center gap-2 rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white hover:bg-brand-dark">
          <Plus className="h-4 w-4" /> Add Meal Plan
        </button>
      </div>

      {error && (
        <p className="flex items-start gap-2 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" /> {error}
        </p>
      )}

      <div className="overflow-hidden rounded-xl border border-slate-200 bg-white">
        <table className="w-full text-left">
          <thead>
            <tr className="border-b border-slate-200 bg-slate-50/60">
              {['Code', 'Name', 'Description', 'Rate Plans', 'Status', ''].map((h) => (
                <th key={h} className="px-5 py-3 text-sm font-semibold text-slate-600">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {isLoading && (
              <tr><td colSpan={6} className="px-5 py-10 text-center text-slate-400">
                <Loader2 className="mx-auto h-5 w-5 animate-spin" />
              </td></tr>
            )}
            {data.map((m) => (
              <tr key={m.id} className="border-b border-slate-100 last:border-0">
                <td className="px-5 py-3 text-sm font-bold text-slate-700">{m.code}</td>
                <td className="px-5 py-3 text-sm text-slate-700">{m.name}</td>
                <td className="px-5 py-3 text-sm text-slate-500">{m.description ?? '—'}</td>
                <td className="px-5 py-3 text-sm text-slate-700">{m.rate_plan_count}</td>
                <td className="px-5 py-3">
                  <span className={`rounded-full px-3 py-1 text-xs font-semibold ${
                    m.status === 'active'
                      ? 'bg-emerald-100 text-emerald-700' : 'bg-slate-200 text-slate-600'}`}>
                    {m.status === 'active' ? 'Active' : 'Inactive'}
                  </span>
                </td>
                <td className="px-5 py-3 text-right">
                  <button onClick={() => open(m)}
                    className="mr-3 text-sm font-semibold text-brand hover:underline">
                    Edit
                  </button>
                  <button onClick={() => toggle.mutate(m)} disabled={toggle.isPending}
                    className="text-sm font-semibold text-slate-500 hover:underline disabled:opacity-50">
                    {m.status === 'active' ? 'Deactivate' : 'Activate'}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {(editing || creating) && (
        <div className="fixed inset-0 z-50 grid place-items-center bg-slate-900/40 p-4">
          <div className="w-full max-w-md rounded-xl bg-white shadow-xl">
            <div className="flex items-center justify-between border-b border-slate-100 px-5 py-4">
              <h3 className="text-lg font-bold text-ink">
                {editing ? 'Edit Meal Plan' : 'Add Meal Plan'}
              </h3>
              <button onClick={done} aria-label="Close"
                className="rounded p-1 text-slate-400 hover:bg-slate-100">
                <X className="h-5 w-5" />
              </button>
            </div>
            <div className="space-y-4 px-5 py-4">
              <div className="grid grid-cols-[110px_1fr] gap-3">
                <Field label="Code" required>
                  <input className={`${input} uppercase`} value={form.code} maxLength={10}
                    onChange={(e) => setForm({ ...form, code: e.target.value.toUpperCase() })} />
                </Field>
                <Field label="Name" required>
                  <input className={input} value={form.name} maxLength={80}
                    onChange={(e) => setForm({ ...form, name: e.target.value })} />
                </Field>
              </div>
              <Field label="Description">
                <textarea className={`${input} h-20`} value={form.description} maxLength={300}
                  onChange={(e) => setForm({ ...form, description: e.target.value })} />
              </Field>
              <Field label="Display Order">
                <input type="number" className={input} value={form.display_order}
                  onChange={(e) => setForm({ ...form, display_order: Number(e.target.value) })} />
              </Field>
              {error && (
                <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>
              )}
            </div>
            <div className="flex justify-end gap-2 border-t border-slate-100 px-5 py-4">
              <button onClick={done}
                className="rounded-lg border border-slate-200 px-4 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50">
                Cancel
              </button>
              <button onClick={() => { setError(null); save.mutate() }} disabled={save.isPending}
                className="flex items-center gap-2 rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white hover:bg-brand-dark disabled:opacity-50">
                {save.isPending && <Loader2 className="h-4 w-4 animate-spin" />} Save
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

/* --------------------------------------------------------------------- page --- */
export default function RatePlans({ propertyId, showHeading = true }: {
  propertyId: string
  /** Screen 008 embeds this as a tab and already has a page heading. */
  showHeading?: boolean
}) {
  const qc = useQueryClient()
  const [tab, setTab] = useState<Tab>('Rate Plans')
  const [filters, setFilters] = useState<RatePlanFilters>({})
  const [selected, setSelected] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)

  const corporateOnly = tab === 'Corporate Rates'
  const query = useMemo(
    () => (corporateOnly ? { ...filters, plan_type: 'corporate' } : filters),
    [filters, corporateOnly],
  )

  const plansQ = useQuery({
    queryKey: ['ratePlans', propertyId, query],
    queryFn: () => listRatePlans(propertyId, query),
    enabled: propertyId !== '' && tab !== 'Meal Plans' && tab !== 'Taxes',
  })
  const mealQ = useQuery({
    queryKey: ['mealPlans', propertyId], queryFn: () => listMealPlans(propertyId),
    enabled: propertyId !== '',
  })
  const typesQ = useQuery({
    queryKey: ['managedRoomTypes', propertyId],
    queryFn: () => listManagedRoomTypes(propertyId), enabled: propertyId !== '',
  })

  const plans = plansQ.data?.items ?? []
  const current = plans.find((p) => p.id === selected) ?? null
  const roomTypes = (typesQ.data ?? []).map((t) => ({ id: t.id, name: t.name }))

  const refresh = () => qc.invalidateQueries({ queryKey: ['ratePlans', propertyId] })
  const duplicate = useMutation({
    mutationFn: (id: string) => duplicateRatePlan(propertyId, id),
    onMutate: (id) => setBusyId(id),
    onSuccess: refresh,
    onError: (e) => setError(apiError(e)),
    onSettled: () => setBusyId(null),
  })
  const toggle = useMutation({
    mutationFn: (p: RatePlan) => setRatePlanStatus(propertyId, p.id, {
      status: p.status === 'active' ? 'inactive' : 'active', version: p.version,
    }),
    onMutate: (p) => setBusyId(p.id),
    onSuccess: refresh,
    onError: (e) => setError(apiError(e)),
    onSettled: () => setBusyId(null),
  })

  const drawerOpen = creating || current !== null

  return (
    <div className="space-y-4">
      {/* Embedded as a tab on screen 008, which has its own heading, only the
          action is drawn -- still at the right. */}
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        {showHeading ? (
          <h1 className="flex items-center gap-2 text-display text-ink">
            <Tag size={26} className="text-brand" /> Rate Plans
          </h1>
        ) : <div />}
        <div className="flex flex-wrap items-center gap-2">
          <button onClick={() => { setSelected(null); setCreating(true) }}
            className="flex items-center gap-1.5 whitespace-nowrap rounded-lg bg-brand px-3 py-2 text-sm font-medium text-white hover:bg-brand-dark disabled:opacity-40">
            <Plus size={15} /> Create Rate Plan
          </button>
        </div>
      </div>

      <div className="flex gap-1 border-b border-slate-200">
        {TABS.map((t) => (
          <button key={t} onClick={() => { setTab(t); setSelected(null); setCreating(false) }}
            className={`border-b-2 px-4 py-3 text-sm font-semibold transition ${
              tab === t
                ? 'border-brand text-brand'
                : 'border-transparent text-slate-500 hover:text-slate-700'
            }`}>
            {t}
          </button>
        ))}
      </div>

      {error && (
        <p className="flex items-start gap-2 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" /> {error}
        </p>
      )}

      {tab === 'Meal Plans' && <MealPlansTab propertyId={propertyId} />}

      {tab === 'Taxes' && (
        <div className="rounded-xl border border-slate-200 bg-white px-4 py-16 text-center">
          <Building2 className="mx-auto h-7 w-7 text-slate-300" />
          <p className="mt-2 font-medium text-slate-700">
            Tax and service charge setup lives on its own screen.
          </p>
          <p className="mx-auto mt-1 max-w-lg text-sm text-slate-500">
            Taxes are configured under Administration &rsaquo; Taxes &amp; Charges.
            Rate plans read those rules rather than holding their own copy, so
            nothing is configured here.
          </p>
          <a href="/admin/taxes"
            className="mt-4 inline-flex items-center gap-2 rounded-lg border border-brand px-4 py-2 text-sm font-semibold text-brand hover:bg-brand-light">
            Open Taxes &amp; Charges
          </a>
        </div>
      )}

      {(tab === 'Rate Plans' || corporateOnly) && (
        <div className={`grid gap-5 ${drawerOpen ? 'xl:grid-cols-[minmax(0,1fr)_420px]' : ''}`}>
          <div className="min-w-0 space-y-4">
            {/* One row, no captions: each select's "All …" option names it.
                Search sits at the right end, as on Reservations. */}
            <div className="flex flex-wrap items-center gap-2">
              <Select blankIsChoice aria-label="Status" className={select}
                value={filters.status ?? ''}
                onChange={(e) => setFilters((f) => ({
                  ...f, status: e.target.value || undefined }))}>
                <option value="">All Statuses</option>
                <option value="active">Active</option>
                <option value="inactive">Inactive</option>
              </Select>
              <Select blankIsChoice aria-label="Meal plan" className={select}
                value={filters.meal_plan_id ?? ''}
                onChange={(e) => setFilters((f) => ({
                  ...f, meal_plan_id: e.target.value || undefined }))}>
                <option value="">All Meal Plans</option>
                {(mealQ.data ?? []).map((m) => (
                  <option key={m.id} value={m.id}>{m.name}</option>
                ))}
              </Select>
              <Select blankIsChoice aria-label="Room type" className={select}
                value={filters.room_type_id ?? ''}
                onChange={(e) => setFilters((f) => ({
                  ...f, room_type_id: e.target.value || undefined }))}>
                <option value="">All Room Types</option>
                {roomTypes.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
              </Select>
              <div className="flex min-w-[240px] flex-1 items-center justify-end gap-2">
                <div className="relative min-w-0 max-w-sm flex-1">
                  <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                  <input aria-label="Search rate plans" placeholder="Search rate plans..."
                    className="w-full rounded-lg border border-slate-200 bg-slate-50 py-2 pl-9 pr-3 text-sm outline-none focus:border-brand"
                    value={filters.search ?? ''}
                    onChange={(e) => setFilters((f) => ({
                      ...f, search: e.target.value || undefined }))} />
                </div>
              </div>
            </div>

            {plansQ.isLoading && (
              <div className="grid h-40 place-items-center text-slate-400">
                <Loader2 className="h-6 w-6 animate-spin" />
              </div>
            )}
            {!plansQ.isLoading && plans.length === 0 && (
              <div className="rounded-xl border border-slate-200 bg-white px-4 py-16 text-center">
                <p className="font-medium text-slate-700">No rate plans match these filters.</p>
                <p className="mt-1 text-sm text-slate-500">
                  {corporateOnly
                    ? 'Corporate rates are rate plans with a corporate account. Create one and set its plan type to Corporate.'
                    : 'Clear the filters, or create a rate plan to get started.'}
                </p>
              </div>
            )}
            <div className="space-y-3">
              {plans.map((p) => (
                <PlanCard key={p.id} plan={p} selected={p.id === selected}
                  busy={busyId === p.id}
                  onSelect={() => { setCreating(false); setSelected(p.id) }}
                  onDuplicate={() => duplicate.mutate(p.id)}
                  onToggleStatus={() => toggle.mutate(p)} />
              ))}
            </div>
          </div>

          {drawerOpen && (
            <div className="xl:sticky xl:top-4 xl:max-h-[calc(100vh-6rem)]">
              <PlanDrawer propertyId={propertyId} plan={current}
                mealPlans={mealQ.data ?? []} roomTypes={roomTypes}
                onClose={() => { setSelected(null); setCreating(false) }} />
            </div>
          )}
        </div>
      )}
    </div>
  )
}
