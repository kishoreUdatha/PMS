import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import ListSelect, { CityInput, PostalCodeInput, StateField } from '../components/ListSelect'
import { COUNTRIES, gstCodeForState, postalCodeProblem, stateCodeProblem } from '../lib/options'
import {
  Settings2, Loader2, Save, AlertTriangle, CheckCircle2, Info,
} from 'lucide-react'
import {
  getInvoiceSettings, saveInvoiceSettings, type InvoiceSettings as S,
} from '../api'
import { Crumbs } from '../components/Crumbs'
import { useActivePropertyId } from '../hooks/useProperty'
import { errorText } from '../lib/forms'

const EMPTY: S = {
  legal_name: null, tagline: null, address_line: null, city: null,
  state: null,
  state_code: null, postal_code: null, country: 'India', phone: null,
  email: null, gst_registered: null, gstin: null,
  fiscal_series: 'INV', next_number: 1,
  footer_note: null,
  tax_inclusive: true,
  payment_methods: ['cash', 'upi', 'card', 'bank_transfer'],
}

// The fields an invoice needs before it can call itself a GST tax invoice.
// The GSTIN is on that list only for a property that says it is registered --
// the same rule the onboarding step applies, so the two screens cannot tell
// the operator different things about the same record.
const REQUIRED: (keyof S)[] = [
  'legal_name', 'address_line', 'state', 'state_code',
]

export default function InvoiceSettings() {
  const propertyId = useActivePropertyId()
  const [form, setForm] = useState<S>(EMPTY)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [ok, setOk] = useState('')

  const { data, isLoading } = useQuery({
    queryKey: ['invoice-settings', propertyId],
    queryFn: () => getInvoiceSettings(propertyId),
    enabled: propertyId !== '',
  })
  useEffect(() => { if (data) setForm(data) }, [data])

  const missing = [
    ...REQUIRED.filter((k) => !String(form[k] ?? '').trim()),
    ...(form.gst_registered && !String(form.gstin ?? '').trim() ? ['gstin'] : []),
  ]

  function set<K extends keyof S>(k: K, v: S[K]) {
    setForm((f) => ({ ...f, [k]: v }))
  }

  async function save() {
    setErr(''); setOk(''); setBusy(true)
    try {
      await saveInvoiceSettings(propertyId, form)
      setOk('Saved. New invoices will carry these details.')
    } catch (e) {
      setErr(errorText(e, 'Could not save.'))
    } finally { setBusy(false) }
  }

  if (isLoading) {
    return <p className="py-20 text-center text-slate-400">
      <Loader2 className="mx-auto animate-spin" />
    </p>
  }

  const cls = 'w-full rounded-lg border border-slate-200 px-3 py-2.5 text-sm outline-none focus:border-brand'

  return (
    <div className="space-y-5">
      <div>
        <Crumbs title="Invoice Settings" trail={[
          { label: 'Invoices & Credit Notes', to: '/finance/invoices' },
          { label: 'Settings' }]} />
        <h1 className="mt-1 flex items-center gap-2 text-display text-ink">
          <Settings2 size={26} className="text-brand" /> Invoice Settings
        </h1>
        <p className="text-slate-500">
          The legal identity an invoice is issued under, and its numbering.
        </p>
      </div>

      {err && (
        <p className="flex items-start gap-2 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700">
          <AlertTriangle size={16} className="mt-0.5 shrink-0" /> {err}
        </p>
      )}
      {ok && (
        <p className="flex items-start gap-2 rounded-xl bg-emerald-50 px-4 py-3 text-sm text-emerald-700">
          <CheckCircle2 size={16} className="mt-0.5 shrink-0" /> {ok}
        </p>
      )}

      <p className={`flex items-start gap-2 rounded-xl px-4 py-3 text-sm ${
        missing.length ? 'bg-amber-50 text-caution' : 'bg-emerald-50 text-emerald-700'}`}>
        <Info size={16} className="mt-0.5 shrink-0" />
        {missing.length
          ? `Invoices will be issued as plain invoices, not GST tax invoices, `
            + `until these are set: ${missing.join(', ')}. Nothing is filled in `
            + `for you — a tax registration number has to be the real one.`
          : 'Complete. Invoices will be issued as GST tax invoices.'}
      </p>

      <Section title="Legal identity">
        <Field label="Registered legal name" required>
          <input value={form.legal_name ?? ''} className={cls}
            onChange={(e) => set('legal_name', e.target.value || null)} />
        </Field>
        <Field label="Tagline"
          hint="Set in gold under the property name on the printed folio. Leave it empty and the header simply closes up.">
          <input value={form.tagline ?? ''} className={cls} maxLength={120}
            placeholder="e.g. SEA · STAY · SERENITY"
            onChange={(e) => set('tagline', e.target.value || null)} />
        </Field>
        <Field label="GSTIN" required>
          <input value={form.gstin ?? ''} className={cls} maxLength={20}
            placeholder="15 characters"
            onChange={(e) => set('gstin', e.target.value.toUpperCase() || null)} />
        </Field>
      </Section>

      <Section title="Registered address">
        <Field label="Address" required wide>
          <input value={form.address_line ?? ''} className={cls}
            onChange={(e) => set('address_line', e.target.value || null)} />
        </Field>
        <Field label="City">
          <CityInput value={form.city} className={cls} state={form.state}
            onChange={(v) => set('city', v || null)} />
        </Field>
        <Field label="State" required>
          <StateField value={form.state} className={cls} country={form.country}
            onChange={(v) => {
              set('state', v || null)
              const code = gstCodeForState(v)
              if (code) set('state_code', code)
            }} />
        </Field>
        <Field label="GST state code" required
          hint="Filled in from the state; decides CGST+SGST versus IGST.">
          <input value={form.state_code ?? ''} className={cls} maxLength={4}
            placeholder="Filled from the state"
            onChange={(e) => set('state_code', e.target.value || null)} />
          {stateCodeProblem(form.state, form.state_code) && (
            <span className="mt-1 block text-xs text-red-600">{stateCodeProblem(form.state, form.state_code)}</span>
          )}
        </Field>
        <Field label="Postal code">
          <PostalCodeInput value={form.postal_code} className={cls} country={form.country}
            state={form.state}
            onChange={(v) => set('postal_code', v || null)} />
        </Field>
        <Field label="Country">
          <ListSelect value={form.country} className={cls} options={COUNTRIES}
            placeholder="Select country"
            onChange={(v) => set('country', v)} />
        </Field>
        <Field label="Phone">
          <input value={form.phone ?? ''} className={cls}
            onChange={(e) => set('phone', e.target.value || null)} />
        </Field>
        <Field label="Email">
          <input value={form.email ?? ''} className={cls}
            onChange={(e) => set('email', e.target.value || null)} />
        </Field>
      </Section>

      <Section title="Numbering">
        <Field label="Series">
          <input value={form.fiscal_series} className={cls} maxLength={20}
            onChange={(e) => set('fiscal_series', e.target.value)} />
        </Field>
        <Field label="Next number"
          hint="Set this to continue a sequence from a previous system. It cannot be moved back over numbers already issued.">
          <input value={form.next_number} type="number" min={1} className={cls}
            onChange={(e) => set('next_number', Number(e.target.value))} />
        </Field>
        <Field label="Footer note" wide>
          <textarea value={form.footer_note ?? ''} rows={2}
            className={`${cls} resize-none`}
            placeholder="Printed at the foot of every invoice"
            onChange={(e) => set('footer_note', e.target.value || null)} />
        </Field>
      </Section>

      <div className="flex justify-end">
        <button onClick={save}
          disabled={busy || postalCodeProblem(form.postal_code, form.country) !== null
            || stateCodeProblem(form.state, form.state_code) !== null}
          className="flex items-center gap-2 rounded-xl bg-brand px-5 py-2.5 text-sm font-medium text-white enabled:hover:bg-brand/90 disabled:opacity-50">
          {busy ? <Loader2 size={16} className="animate-spin" /> : <Save size={16} />}
          Save Settings
        </button>
      </div>
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-2xl border border-slate-100 bg-white p-5">
      <h2 className="mb-4 text-lg font-semibold text-ink">{title}</h2>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">{children}</div>
    </div>
  )
}

function Field({ label, required, hint, wide, children }: {
  label: string; required?: boolean; hint?: string; wide?: boolean
  children: React.ReactNode
}) {
  return (
    <label className={`block ${wide ? 'sm:col-span-2 lg:col-span-3' : ''}`}>
      <span className="mb-1 block text-sm font-medium text-slate-600">
        {label} {required && <span className="text-red-500">*</span>}
      </span>
      {children}
      {hint && <span className="mt-1 block text-xs text-slate-400">{hint}</span>}
    </label>
  )
}
