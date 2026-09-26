import { useEffect, useRef, useState } from 'react'
import Select from '../components/Select'
import { StateField } from '../components/ListSelect'
import { gstCodeForState, stateCodeProblem } from '../lib/options'
import { Link, useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle, ArrowLeft, ChevronDown, Clock, CreditCard, Info, Landmark,
  Loader2, Plus,
} from 'lucide-react'
import {
  WizardFrame, Labelled, ContinueButton, obInput, obLabel,
} from './Onboarding'
import { gstinProblem } from '../lib/gstin'
import { usePaymentMethods } from '../lib/paymentMethods'
import {
  getOnboarding, getOnboardingRates, saveOnboardingRates, getInvoiceSettings,
  saveInvoiceSettings, listRatePlans,
  type RatesAndPolicies, type InvoiceSettings as Billing,
} from '../api'
import { errorText } from '../lib/forms'

/**
 * Onboarding steps 5 and 6: what the property charges, and how it bills.
 *
 * Step 6 is deliberately the same record the Invoice Settings screen edits.
 * Billing identity is the sort of thing that gets set once during onboarding
 * and corrected for years afterwards, and two forms writing two copies is how
 * a GSTIN ends up right on one screen and wrong on the invoice.
 */



/** The names a default cancellation policy may carry; the server agrees. */
const CANCELLATION_NAMES = ['Flexible', 'Moderate', 'Strict', 'Non-refundable']

/** Free-cancellation windows, in the words the mockup uses. The column holds
 *  whole days, so anything finer than a day has nowhere to live. */
const FREE_UNTIL = [
  { days: 0, label: 'Same day as arrival' },
  { days: 1, label: '24 hours before arrival' },
  { days: 2, label: '48 hours before arrival' },
  { days: 3, label: '3 days before arrival' },
  { days: 7, label: '7 days before arrival' },
  { days: 14, label: '14 days before arrival' },
]

/** The underline tabs across the top of step 5. They jump to a section rather
 *  than hide one: both are short, both have to be filled before Continue will
 *  move on, and a policy hidden behind a tab is a policy nobody sets. */
function SectionTabs({ active, onPick }: {
  active: string
  onPick: (k: 'rates' | 'policies') => void
}) {
  return (
    <div className="mb-4 flex gap-6 border-b border-slate-200">
      {([['rates', 'Base Rates'], ['policies', 'Stay Policies']] as const)
        .map(([k, label]) => (
          <button key={k} onClick={() => onPick(k)}
            className={`-mb-px border-b-2 px-1 pb-3 text-sm font-semibold ${
              active === k
                ? 'border-brand text-brand'
                : 'border-transparent text-slate-500 hover:text-slate-700'}`}>
            {label}
          </button>
        ))}
    </div>
  )
}

/* ---------------------------------------------------- 05 rates & policies --- */
export function OnboardingRates() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const propertyId = localStorage.getItem('property_id') ?? ''

  const { data } = useQuery({
    queryKey: ['ob-rates', propertyId],
    queryFn: () => getOnboardingRates(propertyId),
    enabled: propertyId !== '',
  })
  const [form, setForm] = useState<RatesAndPolicies | null>(null)
  useEffect(() => { if (data && !form) setForm(data) }, [data, form])

  // What board basis the property actually sells on. Read from the rate plans
  // rather than the meal-plan list, because a meal plan is only a label -- the
  // rate plan is what pairs it with a price.
  const { data: ratePlans } = useQuery({
    queryKey: ['rate-plans', propertyId],
    queryFn: () => listRatePlans(propertyId, { status: 'active' }),
    enabled: propertyId !== '',
  })
  const boardBasis = Object.entries(
    (ratePlans?.items ?? []).reduce<Record<string, number>>((acc, p) => {
      const name = p.meal_plan_name ?? 'No meal plan'
      acc[name] = (acc[name] ?? 0) + 1
      return acc
    }, {}),
  ).sort((a, b) => b[1] - a[1])
  const [tab, setTab] = useState<'rates' | 'policies'>('rates')
  const ratesRef = useRef<HTMLDivElement>(null)
  const policyRef = useRef<HTMLDivElement>(null)
  const jump = (k: 'rates' | 'policies') => {
    setTab(k)
    const el = (k === 'rates' ? ratesRef : policyRef).current
    el?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  // Saving is unconditional; advancing is not. The step is incomplete because
  // the rates are missing, so refusing to save would leave no way to finish it.
  const [notice, setNotice] = useState<string | null>(null)
  const save = useMutation({
    mutationFn: () => saveOnboardingRates(propertyId, form!),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ['onboarding', propertyId] })
      const fresh = await getOnboarding(propertyId)
      const me = fresh.steps.find((x) => x.key === 'rates')
      if (me?.complete) navigate('/onboarding/billing')
      else setNotice(me?.blocker ?? 'Some room types still need a rate.')
    },
  })
  const err = save.error as { response?: { data?: { detail?: string } } } | null

  if (!form) {
    return (
      <WizardFrame step="rates" eyebrow="05 — Rates & Policies" stepNo={5}
        title="Set your rates and stay policies" blurb="Loading…">
        <Loader2 className="animate-spin text-slate-400" />
      </WizardFrame>
    )
  }

  const setRate = (id: string, field: keyof RatesAndPolicies['rates'][0], v: string) =>
    setForm((f) => f && ({
      ...f,
      rates: f.rates.map((r) => r.room_type_id === id
        ? { ...r, [field]: v === '' ? null : v } : r),
    }))
  const setPolicy = <K extends keyof RatesAndPolicies['policy']>(
    k: K, v: RatesAndPolicies['policy'][K],
  ) => setForm((f) => f && ({ ...f, policy: { ...f.policy, [k]: v } }))

  return (
    <WizardFrame step="rates" eyebrow="05 — Rates & Policies" stepNo={5}
      title="Set your rates and stay policies"
      blurb="Define how much you charge and set the policies for your property."
      footer={<>
        <button onClick={() => navigate('/onboarding/rooms')}
          className="flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-medium text-slate-600 hover:bg-slate-50">
          <ArrowLeft size={15} /> Back
        </button>
        <ContinueButton step="rates" mode="save" busy={save.isPending}
          hint={notice} onClick={() => save.mutate()} />
      </>}>

      <SectionTabs active={tab} onPick={jump} />

      <div ref={ratesRef} className="rounded-2xl border border-slate-100 bg-white p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold text-ink">Room rates</h2>
            <p className="text-sm text-slate-500">
              Per night, and what each extra occupant adds.
            </p>
          </div>
          {/* The mockup puts one meal plan across every rate. That cannot be
              told the truth here: a meal plan carries no price -- it is a label
              -- and the board basis belongs to the rate plan, which is also
              where the money difference between Room Only and Breakfast lives.
              This property sells on several plans across more than one basis,
              so a single selector would either be a lie or would flatten the
              distinction between them. It reports what is actually configured
              and links to the screen that owns it. */}
          <div className="text-right">
            <span className="mb-1 block text-xs text-slate-500">
              Meal plan <span className="text-slate-400">(set per rate plan)</span>
            </span>
            <span className="flex flex-wrap justify-end gap-1.5">
              {boardBasis.length === 0 ? (
                <span className="rounded-lg bg-slate-50 px-2.5 py-1 text-sm text-slate-500">
                  No rate plans yet
                </span>
              ) : boardBasis.map(([name, n]) => (
                <span key={name}
                  className="rounded-lg bg-slate-50 px-2.5 py-1 text-sm text-slate-600">
                  {name}
                  <span className="ml-1.5 text-xs text-slate-400">
                    {n} plan{n === 1 ? '' : 's'}
                  </span>
                </span>
              ))}
            </span>
            <Link to="/rates/plans"
              className="mt-1 block text-xs font-medium text-brand hover:underline">
              Manage rate plans
            </Link>
          </div>
        </div>

        <div className="mt-4 overflow-x-auto">
          <table className="w-full min-w-[720px] text-left text-sm">
            <thead>
              <tr className="border-b border-slate-200 bg-slate-50/60 text-slate-600">
                <th className="px-4 py-2.5 font-semibold">Room Type</th>
                <th className="px-4 py-2.5 font-semibold">Rooms</th>
                <th className="px-4 py-2.5 font-semibold">Per Night (₹)</th>
                <th className="px-4 py-2.5 font-semibold">Extra Adult (₹)</th>
                <th className="px-4 py-2.5 font-semibold">Extra Child (₹)</th>
              </tr>
            </thead>
            <tbody>
              {form.rates.map((r) => (
                <tr key={r.room_type_id} className="border-b border-slate-100 last:border-0">
                  <td className="px-4 py-2.5 font-medium text-slate-700">{r.name}</td>
                  {/* What the price is actually for. A type with no rooms
                      cannot be sold, so its rate is academic -- worth seeing
                      before typing a number into it. */}
                  <td className="px-4 py-2.5">
                    {r.rooms > 0 ? (
                      <span className="tabular-nums text-slate-600">
                        {r.rooms} available
                      </span>
                    ) : (
                      <span className="rounded-md bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-700">
                        No rooms yet
                      </span>
                    )}
                  </td>
                  {(['base_rate', 'extra_adult_rate', 'extra_child_rate'] as const)
                    .map((field) => (
                      <td key={field} className="px-4 py-2">
                        <input className={`${obInput} w-32 tabular-nums`}
                          inputMode="decimal" value={r[field] ?? ''}
                          onChange={(e) => setRate(r.room_type_id, field, e.target.value)} />
                      </td>
                    ))}
                </tr>
              ))}
              {form.rates.length === 0 && (
                <tr><td colSpan={5} className="px-4 py-8 text-center text-sm text-slate-400">
                  No room types yet — add them in step 4 first.
                </td></tr>
              )}
            </tbody>
          </table>
        </div>

        {/* Seasonal pricing is a date range on the rate calendar, not a row
            here. The link goes to the screen that owns it rather than making a
            second place to set the same thing. */}
        <Link to="/rates/calendar"
          className="mt-3 inline-flex items-center gap-1.5 text-sm font-semibold text-brand hover:underline">
          <Plus size={14} /> Add seasonal rate
        </Link>
        <p className="mt-2 text-xs text-slate-400">
          These are the standing room-only rates a night falls back to, before
          any rate plan adjusts them. Seasonal pricing is set per date under
          Rates &amp; Inventory.
        </p>
      </div>

      <div ref={policyRef}
        className="mt-4 rounded-2xl border border-slate-100 bg-white p-5">
        <h2 className="text-lg font-semibold text-ink">Stay policies</h2>
        <div className="mt-4 grid gap-4 lg:grid-cols-3">
          <Labelled label="Check-in time">
            <span className="relative block">
              <Clock size={15}
                className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
              {/* A time input, not a list of six. The list ran 11:00 to
                  16:00 and offered the same six for both ends, so a property
                  with a 9am check-out or a 6pm check-in could not say so —
                  and plenty have exactly that. */}
              <input type="time" className={`${obInput} pl-9`}
                value={form.policy.checkin_time ?? ''}
                onChange={(e) => setPolicy('checkin_time', e.target.value || null)} />
            </span>
          </Labelled>
          <Labelled label="Check-out time">
            <span className="relative block">
              <Clock size={15}
                className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
              <input type="time" className={`${obInput} pl-9`}
                value={form.policy.checkout_time ?? ''}
                onChange={(e) => setPolicy('checkout_time', e.target.value || null)} />
            </span>
          </Labelled>
          <Labelled label="Cancellation policy">
            {/* The name and the rule below live on one row, so what a guest is
                quoted and what is actually enforced cannot drift apart. */}
            <Select className={obInput} value={form.policy.cancellation_name ?? ''}
              onChange={(e) => setPolicy('cancellation_name', e.target.value || null)}>
              <option value="">Not set</option>
              {/* A policy saved before this list existed -- "Standard", say --
                  stays selectable, or the screen would quietly show "Not set"
                  over a policy that is very much set. */}
              {[...new Set([...CANCELLATION_NAMES,
                ...(form.policy.cancellation_name ? [form.policy.cancellation_name] : [])])]
                .map((n) => <option key={n} value={n}>{n}</option>)}
            </Select>
          </Labelled>
        </div>

        <div className="mt-5 grid gap-4 lg:grid-cols-3">
          <div className="lg:col-span-2">
            <p className={obLabel}>Advance payment required</p>
            <div className="mt-2 flex flex-wrap items-center gap-3">
              <span className="flex overflow-hidden rounded-lg border border-slate-200">
                <input className="w-20 px-3 py-2.5 text-sm tabular-nums outline-none"
                  inputMode="decimal" aria-label="Advance payment amount"
                  value={form.policy.advance_value ?? ''}
                  onChange={(e) => setPolicy('advance_value',
                    e.target.value === '' ? null : e.target.value)} />
                <span className="grid w-10 place-items-center border-l border-slate-200 bg-slate-50 text-sm text-slate-500">
                  {form.policy.advance_kind === 'fixed' ? '₹' : '%'}
                </span>
              </span>
              {(['percent', 'fixed'] as const).map((kind) => (
                <label key={kind} className="flex items-center gap-2 text-sm text-slate-600">
                  <input type="radio" name="advance_kind" className="accent-brand"
                    checked={form.policy.advance_kind === kind}
                    onChange={() => setPolicy('advance_kind', kind)} />
                  {kind === 'percent' ? 'Percentage of total' : 'Fixed amount'}
                </label>
              ))}
              {form.policy.advance_kind && (
                <button onClick={() => {
                  setPolicy('advance_kind', null); setPolicy('advance_value', null)
                }} className="text-xs font-medium text-slate-500 hover:underline">
                  No advance required
                </button>
              )}
            </div>
          </div>
          <Labelled label="Free cancellation until">
            <Select className={obInput}
              value={form.policy.free_until_days ?? ''}
              onChange={(e) => setPolicy('free_until_days',
                e.target.value === '' ? null : Number(e.target.value))}>
              <option value="">Not set</option>
              {FREE_UNTIL.map((f) => (
                <option key={f.days} value={f.days}>{f.label}</option>
              ))}
            </Select>
          </Labelled>
        </div>

        <div className="mt-5">
          <Labelled label="Policy notes (optional)">
            <textarea className={`${obInput} resize-none`} rows={3} maxLength={500}
              placeholder="E.g. Additional charges, pet policy, group bookings, etc."
              value={form.policy.policy_text ?? ''}
              onChange={(e) => setPolicy('policy_text', e.target.value || null)} />
          </Labelled>
          <p className="mt-1 text-right text-xs text-slate-400">
            {(form.policy.policy_text ?? '').length}/500
          </p>
        </div>

        {err && (
          <p className="mt-3 flex items-start gap-2 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700">
            <AlertTriangle size={16} className="mt-0.5 shrink-0" />
            {errorText(err, 'That could not be saved.')}
          </p>
        )}
      </div>
    </WizardFrame>
  )
}

/* ---------------------------------------------------------- 06 billing --- */
export function OnboardingBilling() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const propertyId = localStorage.getItem('property_id') ?? ''
  const { methods } = usePaymentMethods(propertyId)

  const { data } = useQuery({
    queryKey: ['invoice-settings', propertyId],
    queryFn: () => getInvoiceSettings(propertyId),
    enabled: propertyId !== '',
  })
  const [form, setForm] = useState<Billing | null>(null)
  useEffect(() => { if (data && !form) setForm(data) }, [data, form])
  const set = <K extends keyof Billing>(k: K, v: Billing[K]) =>
    setForm((f) => (f ? { ...f, [k]: v } : f))

  const [notice, setNotice] = useState<string | null>(null)
  const [bankOpen, setBankOpen] = useState(false)
  const save = useMutation({
    mutationFn: () => saveInvoiceSettings(propertyId, form!),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ['onboarding', propertyId] })
      const fresh = await getOnboarding(propertyId)
      const me = fresh.steps.find((x) => x.key === 'billing')
      // Step 7, not step 9. This predates Team and Import being built and
      // jumped straight past both of them.
      if (me?.complete) navigate('/onboarding/team')
      else setNotice(me?.blocker ?? 'Billing details are not complete yet.')
    },
  })
  const err = save.error as { response?: { data?: { detail?: string } } } | null

  if (!form) {
    return (
      <WizardFrame step="billing" eyebrow="06 — Billing & Payments" stepNo={6}
        title="Configure billing and payments" blurb="Loading…">
        <Loader2 className="animate-spin text-slate-400" />
      </WizardFrame>
    )
  }

  // The stored answer, not a guess from whether a number happens to be there.
  const gst = form.gst_registered
  // The same rule the server applies, so the field answers while you type
  // instead of on save. Held back until the number is long enough to judge --
  // shouting "15 characters" at the third keystroke helps nobody.
  const gstinSaysWhat = (form.gstin ?? '').trim().length >= 15
    ? gstinProblem(form.gstin, form.state_code)
    : null
  const toggleMethod = (key: string) => set('payment_methods',
    form.payment_methods.includes(key)
      ? form.payment_methods.filter((m) => m !== key)
      : [...form.payment_methods, key])

  return (
    <WizardFrame step="billing" eyebrow="06 — Billing & Payments" stepNo={6}
      title="Configure billing and payments"
      blurb="Set up your billing details, taxes and payment methods."
      footer={<>
        <button onClick={() => navigate('/onboarding/rates')}
          className="flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-medium text-slate-600 hover:bg-slate-50">
          <ArrowLeft size={15} /> Back
        </button>
        <ContinueButton step="billing" mode="save" busy={save.isPending}
          hint={notice} onClick={() => save.mutate()} />
      </>}>

      <div className="rounded-2xl border border-slate-100 bg-white p-5">
        <h2 className="text-lg font-semibold text-ink">Billing details</h2>
        <p className="text-sm text-slate-500">
          This is the identity that appears on every invoice you issue.
        </p>

        <div className="mt-4 grid gap-4 lg:grid-cols-2">
          <Labelled label="Billing entity name" required>
            <input className={obInput} value={form.legal_name ?? ''}
              placeholder="Enter registered business name"
              onChange={(e) => set('legal_name', e.target.value || null)} />
          </Labelled>
          <Labelled label="Registered billing address" required>
            <textarea className={`${obInput} resize-none`} rows={3}
              placeholder="Enter complete registered address"
              value={form.address_line ?? ''}
              onChange={(e) => set('address_line', e.target.value || null)} />
          </Labelled>
        </div>

        <div className="mt-4 grid items-end gap-4 lg:grid-cols-[auto_1fr_1fr]">
          <div>
            <span className="mb-1 block text-sm font-medium text-slate-600">
              GST registered?
            </span>
            <span className="flex overflow-hidden rounded-lg border border-slate-200">
              {([true, false] as const).map((yes) => (
                <button key={String(yes)}
                  onClick={() => {
                    set('gst_registered', yes)
                    // Answering "No" clears the number rather than leaving a
                    // stale GSTIN behind a No -- the invoice heading is decided
                    // by whether one is stored, so the two must agree.
                    if (!yes) set('gstin', null)
                  }}
                  className={`w-16 py-2.5 text-sm font-semibold ${
                    gst === yes ? 'bg-brand text-white'
                                : 'bg-white text-slate-600 hover:bg-slate-50'}`}>
                  {yes ? 'Yes' : 'No'}
                </button>
              ))}
            </span>
          </div>
          <Labelled label="GSTIN" required={gst === true}>
            <input
              className={`${obInput} ${gstinSaysWhat ? 'border-red-300' : ''}`}
              value={form.gstin ?? ''} maxLength={15}
              disabled={gst !== true}
              placeholder={gst === null ? 'Answer above first'
                : gst ? '37ABCDE1234F1ZZ' : 'Not registered'}
              onChange={(e) => set('gstin',
                e.target.value.replace(/\s+/g, '').toUpperCase() || null)} />
          </Labelled>
          <Labelled label="Place of supply (state)" required>
            {/* Not in the mockup, and not droppable: this is what decides
                CGST+SGST against IGST on every invoice the property issues. */}
            <span className="flex gap-2">
              <StateField className={obInput} value={form.state}
                onChange={(v) => {
                  set('state', v || null)
                  const code = gstCodeForState(v)
                  if (code) set('state_code', code)
                }} />
              <input className={`${obInput} w-16`} value={form.state_code ?? ''}
                maxLength={2} placeholder="Code"
                onChange={(e) => set('state_code', e.target.value || null)} />
            </span>
            {stateCodeProblem(form.state, form.state_code) && (
              <span className="mt-1 block text-xs text-red-600">{stateCodeProblem(form.state, form.state_code)}</span>
            )}
          </Labelled>
        </div>

        {gstinSaysWhat && (
          <p className="mt-3 flex items-start gap-2 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700">
            <AlertTriangle size={16} className="mt-0.5 shrink-0" /> {gstinSaysWhat}
          </p>
        )}

        {gst === false && (
          <p className="mt-3 flex items-start gap-2 rounded-xl bg-amber-50 px-4 py-3 text-sm text-caution">
            <Info size={16} className="mt-0.5 shrink-0" />
            Not registered, so documents are headed <strong>INVOICE</strong>
            rather than TAX INVOICE — claiming the latter without a
            registration would be a false tax record.
          </p>
        )}

        <div className="mt-5 flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className={obLabel}>Tax rules</p>
            <Link to="/admin/taxes"
              className="mt-1 inline-flex items-center gap-1.5 text-sm font-semibold text-brand hover:underline">
              <Plus size={14} /> Add applicable tax
            </Link>
          </div>
          <div className="flex flex-wrap gap-4">
            {[[true, 'Inclusive of tax'], [false, 'Exclusive of tax']].map(([v, label]) => (
              <label key={String(v)} className="flex items-center gap-2 text-sm text-slate-600">
                <input type="radio" name="tax_inclusive" className="accent-brand"
                  checked={form.tax_inclusive === v}
                  onChange={() => set('tax_inclusive', v as boolean)} />
                {label as string}
              </label>
            ))}
          </div>
        </div>
        <p className="mt-1 text-xs text-slate-400">
          Tax treatment decides whether a quoted rate already contains the tax
          or has it added, so it changes what every folio line means. The rates
          and slabs themselves are set under Taxes &amp; Charges.
        </p>

        <div className="mt-5 grid gap-4 sm:grid-cols-2">
          <Labelled label="Invoice prefix">
            <input className={obInput} value={form.fiscal_series} maxLength={20}
              onChange={(e) => set('fiscal_series', e.target.value)} />
          </Labelled>
          <Labelled label="Next invoice number">
            <input className={obInput} type="number" min={1} value={form.next_number}
              onChange={(e) => set('next_number', Number(e.target.value))} />
            <span className="mt-1 block text-xs text-slate-400">
              Next number to be used for new invoices.
            </span>
          </Labelled>
        </div>
        <p className="mt-1 text-xs text-slate-400">
          Set this to continue a sequence from a previous system. It cannot be
          moved back over numbers already issued.
        </p>
      </div>

      <div className="mt-4 rounded-2xl border border-slate-100 bg-white p-5">
        <h2 className="text-lg font-semibold text-ink">Payment methods</h2>
        <p className="text-sm text-slate-500">
          What the desk may take. At least one is required.
        </p>
        <div className="mt-4 flex flex-wrap gap-x-8 gap-y-3">
          {/* Served, not listed here. The list this file kept was missing
              "wallet" entirely, so a property could never switch it on during
              onboarding even though every collection screen offers it. */}
          {methods.map((m) => (
            <label key={m.value}
              className="flex cursor-pointer items-center gap-2 text-sm text-slate-700">
              <input type="checkbox" className="accent-brand"
                checked={form.payment_methods.includes(m.value)}
                onChange={() => toggleMethod(m.value)} />
              {m.label}
            </label>
          ))}
        </div>

        {/* The gateway half of the mockup is real now and lives on its own
            screen, so this row sends people there rather than duplicating a
            form that handles secrets. Settlement bank details still have
            nowhere to be held, so that row below stays inert and says why. */}
        <div className="mt-4 flex flex-wrap items-center justify-between gap-4 rounded-xl bg-slate-50 px-4 py-3">
          <p className="flex items-start gap-3">
            <CreditCard size={20} className="mt-0.5 shrink-0 text-slate-400" />
            <span>
              {/* No status badge. This screen does not read the gateway, and
                  a hardcoded "Not connected" would go on claiming that after
                  somebody connected one. The screen that knows says so. */}
              <span className="block text-sm font-semibold text-slate-700">
                Online payments
              </span>
              <span className="block text-xs text-slate-500">
                Let guests pay online through a payment gateway.
              </span>
            </span>
          </p>
          <span className="text-right">
            <Link to="/admin/payment-gateway"
              className="inline-block rounded-xl bg-brand px-4 py-2.5 text-sm font-semibold text-white hover:bg-brand/90">
              Connect gateway
            </Link>
            <span className="mt-1 block text-xs text-slate-400">
              Enter your own Razorpay keys so guest payments reach your account.
              You can do this later.
            </span>
          </span>
        </div>

        <div className="mt-3 rounded-xl bg-slate-50 px-4 py-3">
          <button onClick={() => setBankOpen((v) => !v)}
            className="flex w-full items-center justify-between gap-3 text-left">
            <span className="flex items-start gap-3">
              <Landmark size={20} className="mt-0.5 shrink-0 text-slate-400" />
              <span>
                <span className="block text-sm font-semibold text-slate-700">
                  Settlement bank details
                </span>
                <span className="block text-xs text-slate-500">
                  Add your bank account to receive online payments.
                </span>
              </span>
            </span>
            <ChevronDown size={18}
              className={`shrink-0 text-slate-400 ${bankOpen ? 'rotate-180' : ''}`} />
          </button>
          {bankOpen && (
            <p className="mt-3 border-t border-slate-200 pt-3 text-xs text-slate-500">
              There is nowhere to store bank details yet — they only matter once
              a gateway settles money to an account, and no gateway is
              integrated. Until then payments are taken at the desk and recorded
              against the folio.
            </p>
          )}
        </div>

        {err && (
          <p className="mt-3 flex items-start gap-2 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700">
            <AlertTriangle size={16} className="mt-0.5 shrink-0" />
            {errorText(err, 'Billing could not be saved.')}
          </p>
        )}
      </div>
    </WizardFrame>
  )
}
