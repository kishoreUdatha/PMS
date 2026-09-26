import { useEffect, useState } from 'react'
import Select from '../components/Select'
import { CityInput, PostalCodeInput, StateField } from '../components/ListSelect'
import { gstCodeForState, postalCodeProblem, stateCodeProblem } from '../lib/options'
import { FILTER_SELECT } from '../lib/controls'
import { useQuery, useQueryClient, keepPreviousData } from '@tanstack/react-query'
import {
  Building2, Search, Plus, Loader2, X, AlertTriangle, CheckCircle2,
  Pencil, Info,
} from 'lucide-react'
import {
  listCommercialAccounts, createCommercialAccount, updateCommercialAccount,
  ACCOUNT_KINDS,
  type CommercialAccount, type AccountIn, type AccountKind,
} from '../api'
import { Crumbs } from '../components/Crumbs'
import { useOrgId } from '../hooks/useProperty'
import EntityCards, { type Tone } from '../components/EntityCards'
import { StayLayoutToggle, useListLayout } from '../lib/listLayout'
import { errorText } from '../lib/forms'

const money = (v: string | number | null) => v === null || v === undefined
  ? '—'
  : new Intl.NumberFormat('en-IN', {
    style: 'currency', currency: 'INR', maximumFractionDigits: 0,
  }).format(Number(v))

const KIND_TONE: Record<string, string> = {
  company: 'bg-blue-50 text-blue-700',
  travel_agent: 'bg-purple-50 text-purple-700',
  ota: 'bg-amber-50 text-amber-700',
  government: 'bg-slate-75 text-slate-600',
  other: 'bg-slate-75 text-slate-600',
}

/** Two letters for the badge. */
function initials(name: string): string {
  return name.split(/\s+/).filter(Boolean).slice(0, 2)
    .map((w) => w[0]!.toUpperCase()).join('') || '?'
}


/** Companies and agents as cards.
 *
 * An account is looked up, not compared: somebody is taking a booking for a
 * company and wants that company — is it active, what does it owe, is it over
 * its limit. The over-limit warning is the one thing that changes a decision
 * at the desk, so it gets the stripe and the only red.
 */
function AccountCards({ rows, loading, onEdit }: {
  rows: CommercialAccount[]
  loading: boolean
  onEdit: (a: CommercialAccount) => void
}) {
  return (
    <EntityCards
      columns={2}
      loading={loading}
      empty="No account matches. Open one to bill a booking to a company."
      cards={rows.map((a) => ({
        key: a.id,
        tone: (a.over_limit ? 'bad'
          : a.status !== 'active' ? 'muted' : 'ok') as Tone,
        badge: initials(a.name),
        title: a.name,
        subtitle: [a.code, a.legal_name !== a.name ? a.legal_name : null]
          .filter(Boolean).join(' · '),
        status: (
          <span className={`whitespace-nowrap rounded-full px-2.5 py-1 text-xs font-semibold ${KIND_TONE[a.kind]}`}>
            {a.kind_label}
          </span>
        ),
        actions: (
          <button onClick={(e) => { e.stopPropagation(); onEdit(a) }}
            title={`Edit ${a.name}`} aria-label={`Edit ${a.name}`}
            className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-50 hover:text-brand">
            <Pencil size={15} />
          </button>
        ),
        facts: [
          { label: 'GSTIN', value: a.gstin },
          {
            label: 'Contact',
            value: (a.contact_name || a.contact_email || a.contact_phone) ? (
              <span className="block">
                {a.contact_name && <span className="block">{a.contact_name}</span>}
                <span className="truncate text-xs text-slate-500">
                  {a.contact_email ?? a.contact_phone}
                </span>
              </span>
            ) : null,
          },
          { label: 'Terms', value: a.payment_terms },
        ],
        footer: (
          <div className="border-t border-slate-100 pt-3">
            <div className="flex items-end justify-between gap-3 text-sm">
              <div>
                <div className="text-xs text-slate-400">Bookings</div>
                <div className="font-medium tabular-nums text-slate-700">{a.bookings}</div>
              </div>
              <div className="text-right">
                <div className="text-xs text-slate-400">Outstanding</div>
                <div className={`font-semibold tabular-nums ${
                  a.over_limit ? 'text-red-600' : 'text-ink'}`}>
                  {money(a.outstanding)}
                </div>
                {a.open_folios > 0 && (
                  <div className="text-[11px] text-slate-400">
                    {a.open_folios} open folio{a.open_folios === 1 ? '' : 's'}
                  </div>
                )}
              </div>
            </div>
            {/* Warned, never enforced — refusing a booking is a property's
                policy call, not the schema's. */}
            {a.over_limit && (
              <p className="mt-2 flex items-center gap-1 text-xs font-semibold text-red-600">
                <AlertTriangle size={12} /> Over its credit limit
                {a.credit_limit && ` of ${money(a.credit_limit)}`}
              </p>
            )}
          </div>
        ),
      }))}
    />
  )
}

export default function CommercialAccounts() {
  const orgId = useOrgId()
  const qc = useQueryClient()
  const [search, setSearch] = useState('')
  const [q, setQ] = useState('')
  const [kind, setKind] = useState('')
  const [status, setStatus] = useState('active')
  const [editing, setEditing] = useState<CommercialAccount | 'new' | null>(null)
  const [toast, setToast] = useState('')

  useEffect(() => {
    const t = setTimeout(() => setQ(search), 300)
    return () => clearTimeout(t)
  }, [search])

  const { data, isLoading, isFetching } = useQuery({
    queryKey: ['commercial-accounts', orgId, q, kind, status],
    queryFn: () => listCommercialAccounts(orgId, {
      q: q || undefined, kind: kind || undefined, status: status || undefined,
    }),
    enabled: orgId !== '',
    placeholderData: keepPreviousData,
  })
  const rows = data?.rows ?? []
  const [layout, chooseLayout] = useListLayout('accounts')

  function done(m: string) {
    setToast(m)
    setTimeout(() => setToast(''), 3500)
    qc.invalidateQueries({ queryKey: ['commercial-accounts'] })
    setEditing(null)
  }

  const sel = `${FILTER_SELECT} bg-white outline-none focus:border-brand`

  return (
    <div className="space-y-4">
      <Crumbs title="Companies & Agents" trail={[{ label: 'Guests', to: '/guests' },
        { label: 'Companies & Agents' }]} />
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <h1 className="flex items-center gap-2 text-display text-ink">
          <Building2 size={26} className="text-brand" /> Companies &amp; Agents
        </h1>
        <div className="flex flex-wrap items-center gap-2">
          {data?.can_create !== false && (
            <button onClick={() => setEditing('new')}
              className="flex items-center gap-1.5 whitespace-nowrap rounded-lg bg-brand px-3 py-2 text-sm font-medium text-white hover:bg-brand-dark disabled:opacity-40">
              <Plus size={15} /> New Account
            </button>
          )}
        </div>
      </div>

      {toast && (
        <p className="flex items-start gap-2 rounded-xl bg-emerald-50 px-4 py-3 text-sm text-emerald-700">
          <CheckCircle2 size={16} className="mt-0.5 shrink-0" /> {toast}
        </p>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <Select blankIsChoice value={kind} onChange={(e) => setKind(e.target.value)}
          aria-label="Type" className={sel}>
          <option value="">All types</option>
          {(data?.kinds ?? []).map((k) => (
            <option key={k.value} value={k.value}>{k.label} ({k.count})</option>
          ))}
        </Select>
        <Select blankIsChoice value={status} onChange={(e) => setStatus(e.target.value)}
          aria-label="Status" className={sel}>
          <option value="active">Active</option>
          <option value="inactive">Inactive</option>
          <option value="">All statuses</option>
        </Select>
        <div className="flex min-w-[240px] flex-1 items-center justify-end gap-2">
          {isFetching && !isLoading && (
            <Loader2 size={15} className="animate-spin text-slate-300" />
          )}
          <div className="relative min-w-0 max-w-sm flex-1">
            <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <input value={search} onChange={(e) => setSearch(e.target.value)}
              placeholder="Search by name, code or GSTIN…"
              className="w-full rounded-lg border border-slate-200 bg-slate-50 py-2 pl-9 pr-3 text-sm outline-none focus:border-brand" />
          </div>
          <StayLayoutToggle layout={layout} onChange={chooseLayout} />
        </div>
      </div>

      {layout === 'cards' && (
        <AccountCards rows={rows} loading={isLoading} onEdit={setEditing} />
      )}

      <div className={`overflow-hidden rounded-2xl border border-slate-100 bg-white ${
        layout === 'cards' ? 'hidden' : ''}`}>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="border-b border-slate-100 bg-slate-50/60 text-left text-slate-600">
              <tr>
                <th className="px-5 py-3 font-semibold">Account</th>
                <th className="px-4 py-3 font-semibold">Type</th>
                <th className="px-4 py-3 font-semibold">GSTIN</th>
                <th className="px-4 py-3 font-semibold">Contact</th>
                <th className="px-4 py-3 text-right font-semibold">Bookings</th>
                <th className="px-4 py-3 text-right font-semibold">Outstanding</th>
                <th className="px-4 py-3 text-right font-semibold">Terms</th>
                <th className="w-12 px-4 py-3" />
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-50">
              {isLoading && (
                <tr><td colSpan={8} className="p-10 text-center text-slate-400">
                  <Loader2 size={18} className="mx-auto animate-spin" />
                </td></tr>
              )}
              {!isLoading && rows.length === 0 && (
                <tr><td colSpan={8} className="p-10 text-center text-sm text-slate-400">
                  No account matches. Open one to bill a booking to a company.
                </td></tr>
              )}
              {rows.map((a) => (
                <tr key={a.id} className="text-slate-700 hover:bg-slate-50/60">
                  <td className="px-5 py-3">
                    <span className="block font-semibold text-slate-800">{a.name}</span>
                    <span className="block text-xs text-slate-400">
                      {a.code}
                      {a.legal_name && a.legal_name !== a.name && ` · ${a.legal_name}`}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <span className={`rounded-full px-2.5 py-1 text-xs font-semibold ${KIND_TONE[a.kind]}`}>
                      {a.kind_label}
                    </span>
                    {a.status !== 'active' && (
                      <span className="ml-1 rounded-full bg-slate-75 px-2 py-0.5 text-[11px] text-slate-500">
                        inactive
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-xs text-slate-500">
                    {a.gstin ?? <span className="text-slate-300">—</span>}
                  </td>
                  <td className="px-4 py-3 text-xs text-slate-500">
                    {a.contact_name && <span className="block">{a.contact_name}</span>}
                    {a.contact_email ?? a.contact_phone ?? '—'}
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums">{a.bookings}</td>
                  <td className="px-4 py-3 text-right">
                    <span className={`font-medium ${
                      a.over_limit ? 'text-red-600' : 'text-slate-800'}`}>
                      {money(a.outstanding)}
                    </span>
                    {a.open_folios > 0 && (
                      <span className="block text-[11px] text-slate-400">
                        {a.open_folios} open folio{a.open_folios === 1 ? '' : 's'}
                      </span>
                    )}
                    {/* Warned, never enforced — refusing a booking is a
                        property's policy call, not the schema's. */}
                    {a.over_limit && (
                      <span className="flex items-center justify-end gap-1 text-[11px] font-semibold text-red-600">
                        <AlertTriangle size={11} /> over limit
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-right text-xs text-slate-500">
                    {a.credit_limit ? money(a.credit_limit) : '—'}
                    {a.credit_days != null && (
                      <span className="block text-[11px] text-slate-400">
                        {a.credit_days} days
                      </span>
                    )}
                    {a.commission_percent != null && (
                      <span className="block text-[11px] text-purple-600">
                        {Number(a.commission_percent)}% commission
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <button onClick={() => setEditing(a)} title={`Edit ${a.name}`}
                      className="rounded-lg p-2 text-slate-400 hover:bg-slate-50 hover:text-brand">
                      <Pencil size={15} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="border-t border-slate-100 px-5 py-3 text-sm text-slate-500">
          {data?.total ?? 0} account{(data?.total ?? 0) === 1 ? '' : 's'}
        </p>
      </div>

      <p className="flex items-start gap-2 text-xs text-slate-400">
        <Info size={13} className="mt-0.5 shrink-0" />
        Outstanding is the sum of this account's folio balances, read from the
        ledger rather than stored, so it cannot drift. A credit limit warns; it
        does not block a booking — refusing one is a policy decision that
        belongs to the property.
      </p>

      {editing && (
        <AccountModal orgId={orgId}
          account={editing === 'new' ? null : editing}
          onClose={() => setEditing(null)} onDone={done} />
      )}
    </div>
  )
}

const inputCls = 'w-full rounded-lg border border-slate-200 px-3 py-2.5 text-sm outline-none focus:border-brand'

function AccountModal({ orgId, account, onClose, onDone }: {
  orgId: string; account: CommercialAccount | null
  onClose: () => void; onDone: (m: string) => void
}) {
  const [f, setF] = useState({
    code: account?.code ?? '',
    name: account?.name ?? '',
    legal_name: account?.legal_name ?? '',
    kind: (account?.kind ?? 'company') as AccountKind,
    gstin: account?.gstin ?? '',
    address_line: account?.address_line ?? '',
    city: account?.city ?? '',
    state: account?.state ?? '',
    state_code: account?.state_code ?? '',
    postal_code: account?.postal_code ?? '',
    country: account?.country ?? 'India',
    contact_name: account?.contact_name ?? '',
    contact_phone: account?.contact_phone ?? '',
    contact_email: account?.contact_email ?? '',
    credit_limit: account?.credit_limit ?? '',
    credit_days: account?.credit_days?.toString() ?? '',
    commission_percent: account?.commission_percent ?? '',
    payment_terms: account?.payment_terms ?? '',
    status: account?.status ?? 'active',
    notes: account?.notes ?? '',
  })
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const set = (k: string, v: string) => setF((p) => ({ ...p, [k]: v }))
  const valid = f.code.trim() !== '' && f.name.trim() !== ''
    && postalCodeProblem(f.postal_code, f.country) === null
    && stateCodeProblem(f.state, f.state_code) === null

  async function save() {
    setErr(''); setBusy(true)
    const body: AccountIn = {
      organization_id: orgId,
      code: f.code.trim().toUpperCase(),
      name: f.name.trim(),
      legal_name: f.legal_name.trim() || null,
      kind: f.kind,
      gstin: f.gstin.trim().toUpperCase() || null,
      address_line: f.address_line.trim() || null,
      city: f.city.trim() || null,
      state: f.state.trim() || null,
      state_code: f.state_code.trim() || null,
      postal_code: f.postal_code.trim() || null,
      country: f.country.trim() || 'India',
      contact_name: f.contact_name.trim() || null,
      contact_phone: f.contact_phone.trim() || null,
      contact_email: f.contact_email.trim() || null,
      credit_limit: f.credit_limit === '' ? null : Number(f.credit_limit),
      credit_days: f.credit_days === '' ? null : Number(f.credit_days),
      commission_percent: f.commission_percent === ''
        ? null : Number(f.commission_percent),
      payment_terms: f.payment_terms.trim() || null,
      status: f.status,
      notes: f.notes.trim() || null,
    }
    try {
      if (account) {
        await updateCommercialAccount(account.id, body)
        onDone(`${body.name} updated.`)
      } else {
        await createCommercialAccount(body)
        onDone(`${body.name} added.`)
      }
    } catch (e) {
      setErr(errorText(e, 'Could not save.'))
    } finally { setBusy(false) }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4"
      onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()}
        className="max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-2xl bg-white p-6 shadow-xl">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold text-ink">
            {account ? `Edit ${account.name}` : 'New Account'}
          </h2>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600">
            <X size={18} />
          </button>
        </div>
        {err && (
          <p className="mt-3 flex items-start gap-2 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">
            <AlertTriangle size={15} className="mt-0.5 shrink-0" /> {err}
          </p>
        )}

        <Section title="Identity">
          <L label="Code" required hint="Short code the desk types. Unique.">
            <input value={f.code} onChange={(e) => set('code', e.target.value)}
              placeholder="BLUEWAVE" className={`${inputCls} uppercase`} autoFocus />
          </L>
          <L label="Type">
            <Select value={f.kind} className={inputCls}
              onChange={(e) => set('kind', e.target.value)}>
              {ACCOUNT_KINDS.map((k) => (
                <option key={k.value} value={k.value}>{k.label}</option>
              ))}
            </Select>
          </L>
          <L label="Name" required>
            <input value={f.name} onChange={(e) => set('name', e.target.value)}
              className={inputCls} />
          </L>
          <L label="Legal name" hint="What goes on the invoice, if different.">
            <input value={f.legal_name} className={inputCls}
              onChange={(e) => set('legal_name', e.target.value)} />
          </L>
          <L label="GSTIN">
            <input value={f.gstin} className={`${inputCls} uppercase`} maxLength={20}
              onChange={(e) => set('gstin', e.target.value)} />
          </L>
          <L label="Status">
            <Select value={f.status} className={inputCls}
              onChange={(e) => set('status', e.target.value)}>
              <option value="active">Active</option>
              <option value="inactive">Inactive</option>
            </Select>
          </L>
        </Section>

        <Section title="Billing address">
          <L label="Address" wide>
            <input value={f.address_line} className={inputCls}
              onChange={(e) => set('address_line', e.target.value)} />
          </L>
          <L label="City">
            <CityInput value={f.city} onChange={(v) => set('city', v)} state={f.state}
              className={inputCls} />
          </L>
          <L label="State">
            <StateField value={f.state} country={f.country} className={inputCls}
              onChange={(v) => {
                set('state', v)
                // The code follows the state; typed by hand it was the usual mistake.
                const code = gstCodeForState(v)
                if (code) set('state_code', code)
              }} />
          </L>
          <L label="GST state code"
            hint="Decides CGST+SGST versus IGST on their invoice.">
            <input value={f.state_code} maxLength={4} className={inputCls}
              onChange={(e) => set('state_code', e.target.value)} />
            {stateCodeProblem(f.state, f.state_code) && (
              <span className="mt-1 block text-xs text-red-600">{stateCodeProblem(f.state, f.state_code)}</span>
            )}
          </L>
          <L label="Postal code">
            <PostalCodeInput value={f.postal_code} className={inputCls} country={f.country}
              state={f.state}
              onChange={(v) => set('postal_code', v)} />
          </L>
        </Section>

        <Section title="Contact">
          <L label="Contact name">
            <input value={f.contact_name} className={inputCls}
              onChange={(e) => set('contact_name', e.target.value)} />
          </L>
          <L label="Phone">
            <input value={f.contact_phone} className={inputCls}
              onChange={(e) => set('contact_phone', e.target.value)} />
          </L>
          <L label="Email" wide>
            <input value={f.contact_email} className={inputCls}
              onChange={(e) => set('contact_email', e.target.value)} />
          </L>
        </Section>

        <Section title="Terms">
          <L label="Credit limit (₹)"
            hint="Warns when exceeded. Does not block a booking.">
            <input value={f.credit_limit} type="number" min={0} className={inputCls}
              onChange={(e) => set('credit_limit', e.target.value)} />
          </L>
          <L label="Credit days">
            <input value={f.credit_days} type="number" min={0} className={inputCls}
              onChange={(e) => set('credit_days', e.target.value)} />
          </L>
          <L label="Commission %" hint="For agents. 0–100.">
            <input value={f.commission_percent} type="number" min={0} max={100}
              step="0.01" className={inputCls}
              onChange={(e) => set('commission_percent', e.target.value)} />
          </L>
          <L label="Payment terms" wide>
            <textarea value={f.payment_terms} rows={2}
              className={`${inputCls} resize-none`}
              placeholder="e.g. Net 30 from invoice date"
              onChange={(e) => set('payment_terms', e.target.value)} />
          </L>
          <L label="Notes" wide>
            <textarea value={f.notes} rows={2} className={`${inputCls} resize-none`}
              onChange={(e) => set('notes', e.target.value)} />
          </L>
        </Section>

        <div className="mt-5 flex justify-end gap-2">
          <button onClick={onClose}
            className="rounded-lg border border-slate-200 px-4 py-2 text-sm text-slate-600 hover:bg-slate-50">
            Cancel
          </button>
          <button onClick={save} disabled={!valid || busy}
            className="flex items-center gap-2 rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white enabled:hover:bg-brand/90 disabled:opacity-50">
            {busy && <Loader2 size={15} className="animate-spin" />}
            {account ? 'Save Changes' : 'Add Account'}
          </button>
        </div>
      </div>
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <>
      <p className="mt-5 text-xs font-semibold uppercase tracking-wide text-slate-400">
        {title}
      </p>
      <div className="mt-2 grid gap-3 sm:grid-cols-2">{children}</div>
    </>
  )
}

function L({ label, required, hint, wide, children }: {
  label: string; required?: boolean; hint?: string; wide?: boolean
  children: React.ReactNode
}) {
  return (
    <label className={`block ${wide ? 'sm:col-span-2' : ''}`}>
      <span className="mb-1 block text-sm font-medium text-slate-600">
        {label} {required && <span className="text-red-500">*</span>}
      </span>
      {children}
      {hint && <span className="mt-1 block text-xs text-slate-400">{hint}</span>}
    </label>
  )
}
