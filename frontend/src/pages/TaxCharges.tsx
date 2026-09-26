import { useEffect, useState } from 'react'
import Select from '../components/Select'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import DateField from '../components/DateField'
import { fmtDate } from '../lib/dates'
import {
  AlertCircle, CalendarClock, ChevronDown, ChevronLeft, ChevronRight, Copy,
  FileText, Info, Layers, Loader2, Percent, Plus, Power, Receipt, Search, Wallet, X,
} from 'lucide-react'
import { FILTER_SELECT } from '../lib/controls'
import {
  listTaxCharges, listTaxRevisions, createTaxCharge, updateTaxCharge,
  scheduleTaxChange, cloneTaxCharge, setTaxChargeStatus, 
  TAX_CHARGE_TYPES, TAX_APPLICABILITY, TAX_AMOUNT_BASES,
  type TaxCharge, type TaxStats,
} from '../api'
import { Crumbs } from '../components/Crumbs'
import { useActivePropertyId } from '../hooks/useProperty'
import { errorText } from '../lib/forms'

/**
 * Screen 118 — Tax and Service Charge Setup.
 *
 * A rule is versioned by effective date: the table lists the revision in force,
 * and "Schedule Change" adds a later one beside it rather than overwriting. A
 * folio posted last month must still be able to say what rate it was billed at,
 * so nothing here rewrites a rate that has already applied.
 */


const PAGE_SIZE = 10
const input = 'w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700'
const select = `${FILTER_SELECT} bg-white outline-none focus:border-brand`

const day = (s: string | null) => fmtDate(s)
const today = () => new Date().toISOString().slice(0, 10)

function apiError(e: unknown): string {
  return errorText(e, 'Could not save. Please try again.')
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

function Kpi({ icon: Icon, label, value, sub, tint }: {
  icon: React.ComponentType<{ className?: string }>
  label: string; value: number; sub: string; tint: string
}) {
  return (
    <div className="flex items-center gap-4 rounded-xl border border-slate-200 bg-white p-4">
      <span className={`grid h-11 w-11 shrink-0 place-items-center rounded-xl ${tint}`}>
        <Icon className="h-5 w-5" />
      </span>
      <div className="min-w-0">
        <p className="text-sm font-medium text-slate-600">{label}</p>
        <p className="text-2xl font-bold leading-tight text-slate-800">{value}</p>
        <p className="truncate text-xs text-slate-500">{sub}</p>
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------ drawer --- */
interface FormState {
  code: string; name: string; charge_type: string
  rate_type: string; rate_value: string; amount_basis: string
  apply_as: string; applicability: string[]
  income_account: string; remarks: string
  effective_from: string; effective_to: string; status: string
  is_default: boolean
  components: { code: string; rate: string }[]
}

const EMPTY: FormState = {
  code: '', name: '', charge_type: 'service_charge',
  rate_type: 'percent', rate_value: '', amount_basis: 'per_night',
  apply_as: 'exclusive', applicability: [], income_account: '', remarks: '',
  effective_from: today(), effective_to: '', status: 'active',
  is_default: false, components: [],
}

function fromCharge(c: TaxCharge): FormState {
  return {
    code: c.code, name: c.name, charge_type: c.charge_type,
    rate_type: c.rate_type, rate_value: String(Number(c.rate_value)),
    amount_basis: c.amount_basis ?? 'per_night',
    apply_as: c.apply_as, applicability: c.applicability,
    income_account: c.income_account ?? '', remarks: c.remarks ?? '',
    effective_from: c.effective_from, effective_to: c.effective_to ?? '',
    status: c.status, is_default: c.is_default,
    components: c.components.map((x) => ({ code: x.code, rate: String(Number(x.rate)) })),
  }
}

function ChargeDrawer({
  propertyId, charge, onClose,
}: { propertyId: string; charge: TaxCharge | null; onClose: () => void }) {
  const qc = useQueryClient()
  const [form, setForm] = useState<FormState>(charge ? fromCharge(charge) : EMPTY)
  const [error, setError] = useState<string | null>(null)
  const [scheduling, setScheduling] = useState(false)
  const [scheduleFrom, setScheduleFrom] = useState('')

  useEffect(() => {
    setForm(charge ? fromCharge(charge) : EMPTY)
    setError(null); setScheduling(false); setScheduleFrom('')
  }, [charge])

  const revisionsQ = useQuery({
    queryKey: ['taxRevisions', propertyId, charge?.code],
    queryFn: () => listTaxRevisions(propertyId, charge!.code),
    enabled: !!charge,
  })

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['taxCharges', propertyId] })
    qc.invalidateQueries({ queryKey: ['taxRevisions', propertyId] })
  }

  const payload = (effectiveFrom?: string) => ({
    code: form.code.trim(), name: form.name.trim(),
    charge_type: form.charge_type, rate_type: form.rate_type,
    rate_value: form.rate_value === '' ? 0 : Number(form.rate_value),
    // A percentage has no charging basis; the API rejects sending one.
    amount_basis: form.rate_type === 'amount' ? form.amount_basis : null,
    apply_as: form.apply_as, applicability: form.applicability,
    income_account: form.income_account.trim() || null,
    remarks: form.remarks.trim() || null,
    effective_from: effectiveFrom ?? form.effective_from,
    effective_to: form.effective_to || null,
    status: form.status,
    is_default: form.charge_type === 'tax_group' && form.is_default,
    components: form.charge_type === 'tax_group'
      ? form.components
          .filter((c) => c.code.trim() !== '')
          .map((c) => ({ code: c.code.trim().toUpperCase(), rate: Number(c.rate || 0) }))
      : [],
  })

  const save = useMutation({
    mutationFn: () => charge
      ? updateTaxCharge(propertyId, charge.id, { ...payload(), version: charge.version })
      : createTaxCharge(propertyId, payload()),
    onSuccess: () => { refresh(); onClose() },
    onError: (e) => setError(apiError(e)),
  })
  const schedule = useMutation({
    mutationFn: () => scheduleTaxChange(propertyId, charge!.id, payload(scheduleFrom)),
    onSuccess: () => { refresh(); onClose() },
    onError: (e) => setError(apiError(e)),
  })
  const clone = useMutation({
    mutationFn: () => cloneTaxCharge(propertyId, charge!.id),
    onSuccess: () => { refresh(); onClose() },
    onError: (e) => setError(apiError(e)),
  })
  const toggle = useMutation({
    mutationFn: () => setTaxChargeStatus(propertyId, charge!.id, {
      status: charge!.status === 'active' ? 'inactive' : 'active',
      version: charge!.version,
    }),
    onSuccess: () => { refresh(); onClose() },
    onError: (e) => setError(apiError(e)),
  })

  const busy = save.isPending || schedule.isPending || clone.isPending || toggle.isPending
  const set = <K extends keyof FormState>(k: K, v: FormState[K]) =>
    setForm((f) => ({ ...f, [k]: v }))
  const componentTotal = form.components.reduce((n, c) => n + Number(c.rate || 0), 0)

  return (
    <aside className="flex h-full min-h-0 flex-col rounded-xl border border-slate-200 bg-white">
      <div className="flex items-start justify-between gap-3 border-b border-slate-100 px-5 py-4">
        <div>
          <h2 className="text-xl font-bold text-ink">
            {charge ? `Edit ${charge.charge_type_label}` : 'Create Tax or Charge'}
          </h2>
          <p className="text-sm text-slate-500">
            {charge ? 'Update rate, applicability and effective dates'
              : 'Define a tax group, GST component, service charge or other tax'}
          </p>
        </div>
        <button onClick={onClose} aria-label="Close"
          className="rounded p-1 text-slate-400 hover:bg-slate-100">
          <X className="h-5 w-5" />
        </button>
      </div>

      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-5 py-4">
        <div className="grid grid-cols-2 gap-3">
          <Field label="Code" required>
            <input className={`${input} uppercase disabled:bg-slate-50`}
              value={form.code} maxLength={50} disabled={!!charge}
              title={charge ? 'A code cannot be changed once it is in use' : undefined}
              onChange={(e) => set('code', e.target.value.toUpperCase())} />
          </Field>
          <Field label="Name" required>
            <input className={input} value={form.name} maxLength={120}
              onChange={(e) => set('name', e.target.value)} />
          </Field>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <Field label="Type" required>
            <Select className={`${input} disabled:bg-slate-50`} value={form.charge_type}
              disabled={!!charge}
              title={charge ? 'Changing the kind of charge would change how it bills'
                : undefined}
              onChange={(e) => set('charge_type', e.target.value)}>
              {TAX_CHARGE_TYPES.map((t) => (
                <option key={t.code} value={t.code}>{t.label}</option>
              ))}
            </Select>
          </Field>
          <Field label="Status">
            <Select className={input} value={form.status}
              onChange={(e) => set('status', e.target.value)}>
              <option value="active">Active</option>
              <option value="inactive">Inactive</option>
            </Select>
          </Field>
        </div>

        <div>
          <span className="mb-1 block text-sm font-medium text-slate-700">
            Rate / Value <span className="text-red-500">*</span>
          </span>
          <div className="flex gap-2">
            <input type="number" min={0} step="0.01" className={input}
              value={form.rate_value}
              onChange={(e) => set('rate_value', e.target.value)} />
            <Select className={`${input} w-28 shrink-0`} value={form.rate_type}
              onChange={(e) => set('rate_type', e.target.value)}>
              <option value="percent">%</option>
              <option value="amount">₹</option>
            </Select>
            {form.rate_type === 'amount' && (
              <Select className={`${input} w-36 shrink-0`} value={form.amount_basis}
                onChange={(e) => set('amount_basis', e.target.value)}>
                {TAX_AMOUNT_BASES.map((b) => (
                  <option key={b.code} value={b.code}>{b.label}</option>
                ))}
              </Select>
            )}
          </div>
        </div>

        <div>
          <span className="mb-1 block text-sm font-medium text-slate-700">
            Apply As <span className="text-red-500">*</span>
          </span>
          {([['exclusive', 'Exclusive (added to bill)'],
             ['inclusive', 'Inclusive (included in rate)']] as const).map(([v, l]) => (
            <label key={v} className="flex items-center gap-2 py-0.5 text-sm text-slate-700">
              <input type="radio" name="apply-as" checked={form.apply_as === v}
                onChange={() => set('apply_as', v)}
                className="h-4 w-4 border-slate-300 text-brand" />
              {l}
            </label>
          ))}
        </div>

        <div>
          <span className="mb-1 block text-sm font-medium text-slate-700">
            Applicability <span className="text-red-500">*</span>
          </span>
          <div className="grid grid-cols-2 gap-x-4 gap-y-1">
            {TAX_APPLICABILITY.map((a) => (
              <label key={a.code} className="flex items-center gap-2 text-sm text-slate-700">
                <input type="checkbox" checked={form.applicability.includes(a.code)}
                  onChange={(e) => set('applicability', e.target.checked
                    ? [...form.applicability, a.code]
                    : form.applicability.filter((x) => x !== a.code))}
                  className="h-4 w-4 rounded border-slate-300 text-brand" />
                {a.label}
              </label>
            ))}
          </div>
        </div>

        {form.charge_type === 'tax_group' && (
          <div className="rounded-lg border border-slate-200 bg-slate-50/60 p-3">
            <label className="flex items-start gap-2 text-sm font-medium text-slate-700">
              <input type="checkbox" checked={form.is_default}
                onChange={(e) => set('is_default', e.target.checked)}
                className="mt-0.5 h-4 w-4 rounded border-slate-300 text-brand" />
              <span>
                Default rate for these areas
                <span className="mt-0.5 block text-xs font-normal text-slate-500">
                  A charge pays one GST rate, not the sum of every group that
                  covers it. The default is what applies unless a charge names a
                  different group — leave this off for a rate that only applies
                  to selected items, such as alcohol.
                </span>
              </span>
            </label>
          </div>
        )}

        {form.charge_type === 'tax_group' && (
          <div>
            <div className="mb-1 flex items-center justify-between">
              <span className="text-sm font-medium text-slate-700">Components</span>
              <button
                onClick={() => set('components', [...form.components, { code: '', rate: '' }])}
                className="flex items-center gap-1 text-sm font-semibold text-brand hover:underline">
                <Plus className="h-4 w-4" /> Add component
              </button>
            </div>
            <div className="space-y-2">
              {form.components.map((c, i) => (
                <div key={i} className="flex gap-2">
                  <input className={`${input} uppercase`} value={c.code} maxLength={20}
                    placeholder="CGST"
                    onChange={(e) => set('components', form.components.map(
                      (x, n) => n === i ? { ...x, code: e.target.value.toUpperCase() } : x))} />
                  <input type="number" min={0} step="0.01" className={`${input} w-28`}
                    value={c.rate} placeholder="2.5"
                    onChange={(e) => set('components', form.components.map(
                      (x, n) => n === i ? { ...x, rate: e.target.value } : x))} />
                  <button onClick={() => set('components',
                    form.components.filter((_, n) => n !== i))}
                    aria-label="Remove component"
                    className="grid h-[38px] w-10 shrink-0 place-items-center rounded-lg border border-slate-200 text-slate-400 hover:bg-slate-50">
                    <X className="h-4 w-4" />
                  </button>
                </div>
              ))}
            </div>
            {form.components.length > 0 && (
              <p className={`mt-1 text-xs ${
                componentTotal === Number(form.rate_value || 0)
                  ? 'text-slate-400' : 'text-amber-700'}`}>
                Components total {componentTotal}% — the group's rate is{' '}
                {Number(form.rate_value || 0)}%.
                {componentTotal !== Number(form.rate_value || 0) && ' They must match.'}
              </p>
            )}
          </div>
        )}

        <div className="grid grid-cols-2 gap-3">
          <Field label="Effective From" required>
            <DateField value={form.effective_from} onChange={(v) => set('effective_from', v)} className={input} />
          </Field>
          <Field label="Effective To">
            <DateField value={form.effective_to} onChange={(v) => set('effective_to', v)} className={input} />
          </Field>
        </div>

        <Field label="Accounting Mapping">
          <input className={input} value={form.income_account} maxLength={80}
            placeholder="e.g. Service Charge Income (4005)"
            onChange={(e) => set('income_account', e.target.value)} />
        </Field>

        <Field label="Remarks">
          <textarea className={`${input} h-20`} value={form.remarks} maxLength={400}
            onChange={(e) => set('remarks', e.target.value)} />
        </Field>

        {scheduling && (
          <div className="rounded-lg border border-brand/40 bg-brand-light/40 p-3">
            <p className="mb-2 text-sm font-semibold text-slate-800">
              Schedule this change from a future date
            </p>
            <p className="mb-2 text-xs text-slate-600">
              The rate above is saved as a new revision starting on this date. The
              current one keeps applying until then, so nothing already billed
              changes.
            </p>
            <DateField value={scheduleFrom} onChange={(v) => setScheduleFrom(v)} className={input} />
          </div>
        )}

        {charge && (revisionsQ.data ?? []).length > 1 && (
          <div>
            <p className="mb-1 text-sm font-medium text-slate-700">Rate history</p>
            <div className="divide-y divide-slate-100 rounded-lg border border-slate-200">
              {(revisionsQ.data ?? []).map((r) => (
                <div key={r.id}
                  className={`flex items-center justify-between px-3 py-2 text-sm ${
                    r.id === charge.id ? 'bg-brand-light/40' : ''}`}>
                  <span className="text-slate-600">
                    From {day(r.effective_from)}
                    {r.effective_from > today() && (
                      <span className="ml-2 rounded bg-sky-100 px-1.5 py-0.5 text-xs font-semibold text-sky-700">
                        Scheduled
                      </span>
                    )}
                  </span>
                  <span className="font-medium text-slate-800">{r.rate_label}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {charge?.charge_type === 'service_charge' && (
          <p className="flex items-start gap-2 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-900">
            <Info className="mt-0.5 h-4 w-4 shrink-0" />
            <span>
              <strong className="font-semibold">Approval rules.</strong> The design
              calls for service charge changes to be approved by a Finance Manager.
              Approval routing is not wired yet — screen 042 has a queue but no way
              to raise a request — so this saves directly and is recorded in the
              audit log.
            </span>
          </p>
        )}

        {charge?.updated_by_name && (
          <p className="text-xs text-slate-400">
            Last modified {day(charge.updated_at)} by {charge.updated_by_name}
            {' · '}revision {charge.revision}
          </p>
        )}

        {error && (
          <p className="flex items-start gap-2 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" /> {error}
          </p>
        )}
      </div>

      <div className="flex flex-wrap items-center justify-end gap-2 border-t border-slate-100 px-5 py-4">
        <button onClick={onClose}
          className="rounded-lg border border-slate-200 px-4 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50">
          Cancel
        </button>
        {charge && (
          <>
            <button onClick={() => setScheduling((v) => !v)} disabled={busy}
              className="flex items-center gap-2 rounded-lg border border-brand px-4 py-2 text-sm font-semibold text-brand hover:bg-brand-light disabled:opacity-50">
              <CalendarClock className="h-4 w-4" /> Schedule Change
            </button>
            <button onClick={() => clone.mutate()} disabled={busy}
              className="flex items-center gap-2 rounded-lg border border-slate-200 px-4 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-50">
              <Copy className="h-4 w-4" /> Clone
            </button>
            <button onClick={() => toggle.mutate()} disabled={busy}
              className="flex items-center gap-2 rounded-lg border border-red-200 px-4 py-2 text-sm font-semibold text-red-600 hover:bg-red-50 disabled:opacity-50">
              <Power className="h-4 w-4" />
              {charge.status === 'active' ? 'Deactivate' : 'Activate'}
            </button>
          </>
        )}
        <button
          onClick={() => {
            setError(null)
            if (scheduling) {
              if (!scheduleFrom) { setError('Choose the date the new rate starts.'); return }
              schedule.mutate()
            } else save.mutate()
          }}
          disabled={busy}
          className="flex items-center gap-2 rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white hover:bg-brand-dark disabled:opacity-50">
          {busy && <Loader2 className="h-4 w-4 animate-spin" />}
          {scheduling ? 'Save Scheduled Change' : charge ? 'Save Changes' : 'Create'}
        </button>
      </div>
    </aside>
  )
}

/* -------------------------------------------------------------------- page --- */
export default function TaxCharges() {
  const propertyId = useActivePropertyId()
  const [tab, setTab] = useState<string>('')
  const [search, setSearch] = useState('')
  const [chargeStatus, setChargeStatus] = useState('')
  const [applicability, setApplicability] = useState('')
  const [page, setPage] = useState(0)
  const [selected, setSelected] = useState<TaxCharge | null>(null)
  const [creating, setCreating] = useState(false)

  const filters = {
    search: search || undefined,
    charge_type: tab || undefined,
    status: chargeStatus || undefined,
    applicability: applicability || undefined,
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
  }
  const q = useQuery({
    queryKey: ['taxCharges', propertyId, filters],
    queryFn: () => listTaxCharges(propertyId, filters),
    enabled: propertyId !== '',
  })

  const items = q.data?.items ?? []
  const total = q.data?.total ?? 0
  const stats: TaxStats | undefined = q.data?.stats
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const drawerOpen = creating || selected !== null

  const TABS: { key: string; label: string; count?: number }[] = [
    { key: '', label: 'All', count: stats?.total },
    { key: 'tax_group', label: 'Tax Groups', count: stats?.tax_groups },
    { key: 'gst_component', label: 'GST Components', count: stats?.gst_components },
    { key: 'service_charge', label: 'Service Charges', count: stats?.service_charges },
    { key: 'other_tax', label: 'Other Taxes', count: stats?.other_taxes },
  ]

  return (
    <div className="space-y-4">
      <div className="space-y-1">
        <Crumbs title="Tax and Service Charge Setup" trail={[
          { label: 'Administration', to: '/admin' },
          { label: 'Taxes & Charges' }]} />
        <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
          <h1 className="flex items-center gap-2 text-display text-ink">
            <Wallet size={26} className="text-brand" /> Tax and Service Charge Setup
          </h1>
          <div className="flex flex-wrap items-center gap-2">
            <button onClick={() => { setSelected(null); setCreating(true) }}
              className="flex items-center gap-1.5 whitespace-nowrap rounded-lg bg-brand px-3 py-2 text-sm font-medium text-white hover:bg-brand-dark disabled:opacity-40">
              <Plus size={15} /> Create New
            </button>
          </div>
        </div>
      </div>

      {stats && (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <Kpi icon={Layers} label="Tax Groups" value={stats.tax_groups}
            sub={stats.tax_groups_inactive
              ? `${stats.tax_groups_inactive} inactive` : 'All active'}
            tint="bg-sky-50 text-sky-600" />
          <Kpi icon={Percent} label="GST Components" value={stats.gst_components}
            sub={stats.gst_component_codes.join(', ') || '—'}
            tint="bg-violet-50 text-violet-600" />
          <Kpi icon={Receipt} label="Service Charges" value={stats.service_charges}
            sub={stats.scheduled_changes
              ? `${stats.scheduled_changes} scheduled change${stats.scheduled_changes === 1 ? '' : 's'}`
              : 'No scheduled changes'}
            tint="bg-emerald-50 text-emerald-600" />
          <Kpi icon={FileText} label="Other Taxes" value={stats.other_taxes}
            sub={stats.other_tax_names.join(', ') || '—'}
            tint="bg-amber-50 text-amber-600" />
        </div>
      )}

      <div className="flex flex-wrap gap-1 border-b border-slate-200">
        {TABS.map((t) => (
          <button key={t.key} onClick={() => { setTab(t.key); setPage(0) }}
            className={`border-b-2 px-4 py-3 text-sm font-semibold transition ${
              tab === t.key ? 'border-brand text-brand'
                : 'border-transparent text-slate-500 hover:text-slate-700'
            }`}>
            {t.label}{t.count !== undefined && ` (${t.count})`}
          </button>
        ))}
      </div>

      <div className={`grid gap-5 ${drawerOpen ? 'xl:grid-cols-[minmax(0,1fr)_420px]' : ''}`}>
        <div className="min-w-0 space-y-4">
          {/* One row, no captions; search at the right end as on Reservations. */}
          <div className="flex flex-wrap items-center gap-2">
            <Select blankIsChoice aria-label="Status" className={select} value={chargeStatus}
              onChange={(e) => { setChargeStatus(e.target.value); setPage(0) }}>
              <option value="">All Statuses</option>
              <option value="active">Active</option>
              <option value="inactive">Inactive</option>
            </Select>
            <Select blankIsChoice aria-label="Applicability" className={select} value={applicability}
              onChange={(e) => { setApplicability(e.target.value); setPage(0) }}>
              <option value="">All Applicability</option>
              {TAX_APPLICABILITY.map((a) => (
                <option key={a.code} value={a.code}>{a.label}</option>
              ))}
            </Select>
            <button
              onClick={() => {
                setSearch(''); setChargeStatus(''); setApplicability(''); setPage(0)
              }}
              className="whitespace-nowrap px-1 py-2 text-sm font-semibold text-brand hover:underline">
              Clear Filters
            </button>
            <div className="flex min-w-[240px] flex-1 items-center justify-end gap-2">
              <div className="relative min-w-0 max-w-sm flex-1">
                <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                <input value={search} aria-label="Search charges" placeholder="Search by name or code..."
                  onChange={(e) => { setSearch(e.target.value); setPage(0) }}
                  className="w-full rounded-lg border border-slate-200 bg-slate-50 py-2 pl-9 pr-3 text-sm outline-none focus:border-brand" />
              </div>
            </div>
          </div>

          <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
            <table className="w-full min-w-[900px] text-left">
              <thead>
                <tr className="border-b border-slate-200 bg-slate-50/60">
                  {['Code', 'Name', 'Type', 'Rate / Value', 'Applicability',
                    'Effective From', 'Status', ''].map((h) => (
                    <th key={h} className="px-4 py-3 text-sm font-semibold text-slate-600">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {q.isLoading && (
                  <tr><td colSpan={8} className="px-4 py-12 text-center text-slate-400">
                    <Loader2 className="mx-auto h-5 w-5 animate-spin" />
                  </td></tr>
                )}
                {q.isError && (
                  <tr><td colSpan={8} className="px-4 py-12 text-center text-sm text-red-700">
                    {apiError(q.error)}
                  </td></tr>
                )}
                {!q.isLoading && items.length === 0 && (
                  <tr><td colSpan={8} className="px-4 py-12 text-center text-sm text-slate-500">
                    Nothing matches these filters.
                  </td></tr>
                )}
                {items.map((c) => (
                  <tr key={c.id}
                    onClick={() => { setCreating(false); setSelected(c) }}
                    className={`cursor-pointer border-b border-slate-100 last:border-0 hover:bg-slate-50 ${
                      selected?.id === c.id ? 'bg-brand-light/40' : ''}`}>
                    <td className="px-4 py-3 text-sm font-bold text-slate-700">{c.code}</td>
                    <td className="px-4 py-3 text-sm text-slate-700">{c.name}</td>
                    <td className="px-4 py-3 text-sm text-slate-600">
                      {c.charge_type_label}
                      {c.is_default && (
                        <span className="ml-2 rounded bg-brand-light px-1.5 py-0.5 text-xs font-semibold text-brand"
                          title="The rate these areas pay unless a charge names another group">
                          Default
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-sm text-slate-700">{c.rate_label}</td>
                    <td className="px-4 py-3 text-sm text-slate-600">
                      {c.applicability_label}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-sm text-slate-600">
                      {day(c.effective_from)}
                      {c.scheduled_count > 0 && (
                        <span className="ml-2 rounded bg-sky-100 px-1.5 py-0.5 text-xs font-semibold text-sky-700"
                          title={`Next change on ${day(c.scheduled_from)}`}>
                          +{c.scheduled_count}
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <span className={`rounded-full px-3 py-1 text-xs font-semibold ${
                        c.status === 'active'
                          ? 'bg-emerald-50 text-emerald-700' : 'bg-slate-75 text-slate-500'}`}>
                        {c.status === 'active' ? 'Active' : 'Inactive'}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-right">
                      <ChevronDown className="ml-auto h-4 w-4 -rotate-90 text-slate-300" />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-sm text-slate-500">
              {total === 0 ? 'No records'
                : `Showing ${page * PAGE_SIZE + 1} – `
                  + `${Math.min((page + 1) * PAGE_SIZE, total)} of ${total} records`}
            </p>
            <div className="flex items-center gap-1">
              <button onClick={() => setPage((p) => Math.max(0, p - 1))}
                disabled={page === 0} aria-label="Previous page"
                className="grid h-9 w-9 place-items-center rounded-lg border border-slate-200 text-slate-500 hover:bg-slate-50 disabled:opacity-40">
                <ChevronLeft className="h-4 w-4" />
              </button>
              {Array.from({ length: pages }, (_, i) => (
                <button key={i} onClick={() => setPage(i)}
                  className={`h-9 w-9 rounded-lg text-sm font-semibold ${
                    i === page ? 'bg-brand text-white'
                      : 'border border-slate-200 text-slate-600 hover:bg-slate-50'}`}>
                  {i + 1}
                </button>
              ))}
              <button onClick={() => setPage((p) => Math.min(pages - 1, p + 1))}
                disabled={page >= pages - 1} aria-label="Next page"
                className="grid h-9 w-9 place-items-center rounded-lg border border-slate-200 text-slate-500 hover:bg-slate-50 disabled:opacity-40">
                <ChevronRight className="h-4 w-4" />
              </button>
            </div>
          </div>
        </div>

        {drawerOpen && (
          <div className="xl:sticky xl:top-4 xl:max-h-[calc(100vh-6rem)]">
            <ChargeDrawer propertyId={propertyId} charge={selected}
              onClose={() => { setSelected(null); setCreating(false) }} />
          </div>
        )}
      </div>
    </div>
  )
}
