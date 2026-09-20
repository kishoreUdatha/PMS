import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowUpRight, X } from 'lucide-react'
import {
  listOrganizations, createTenant, renameOrganization, suspendOrganization,
  reactivateOrganization,
  type Organization, type TenantCreated,
} from '../api'
import { listPlans, money, type Plan } from '../billingApi'
import {
  Busy, Button, Card, DataTable, ErrorNote, Field, FilterBar, Metrics, Note,
  Page, Pill, Select, Td, askReason, askText, errorText, inputClass, notify,
} from '../ui'

/** Screen 04 — the tenant directory.
 *
 *  Laid out to the artboard: a KPI strip, a filter card that applies on a
 *  button, and the table inside a titled card carrying a record count.
 *
 *  One departure worth stating. The pack's "Status" is a single lifecycle
 *  word, and this system has two states that could claim it — the account's
 *  (active/suspended) and the subscription's (trialing/past_due/…). They
 *  answer different questions, and a directory that conflated them would show
 *  a suspended account as "active" because its plan still said so. The column
 *  shows the subscription's state where there is one and the account's where
 *  there is not; a tenant on no plan says so rather than showing a blank.
 */

function NewTenant({ onClose, onDone }: {
  onClose: () => void; onDone: (t: TenantCreated) => void
}) {
  const [f, setF] = useState({
    organization_name: '', property_name: '', owner_name: '',
    owner_email: '', owner_phone: '',
  })
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setF({ ...f, [k]: e.target.value })

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setErr(''); setBusy(true)
    try { onDone(await createTenant(f)) }
    catch (e2) { setErr(errorText(e2, 'The tenant could not be created.')) }
    finally { setBusy(false) }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center overflow-y-auto bg-black/50 p-4">
      <Card className="w-full max-w-lg p-6">
        <div className="mb-1 flex items-start justify-between">
          <h2 className="text-[17px] font-bold text-pf-navy">Add tenant</h2>
          <button onClick={onClose} className="text-pf-muted hover:text-pf-body">
            <X size={18} />
          </button>
        </div>
        <p className="mb-5 text-sm text-pf-muted">
          Create the company account and prepare its owner invitation.
        </p>
        <ErrorNote>{err}</ErrorNote>
        <form onSubmit={submit} className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Company name">
              <input className={inputClass} value={f.organization_name}
                onChange={set('organization_name')} autoFocus required />
            </Field>
            <Field label="First property">
              <input className={inputClass} value={f.property_name}
                onChange={set('property_name')}
                placeholder="Defaults to the company name" />
            </Field>
            <Field label="Owner name">
              <input className={inputClass} value={f.owner_name}
                onChange={set('owner_name')} required />
            </Field>
            <Field label="Owner work email">
              <input className={inputClass} type="email" value={f.owner_email}
                onChange={set('owner_email')} required />
            </Field>
          </div>
          <p className="text-xs leading-relaxed text-pf-muted">
            The tenant code is assigned automatically. The owner receives a
            link to set their own password — no password is ever emailed or
            shown to platform staff.
          </p>
          <div className="flex justify-end gap-2 pt-1">
            <Button onClick={onClose}>Cancel</Button>
            <Button type="submit" tone="primary" disabled={busy}>
              {busy ? 'Creating…' : 'Create tenant'}
            </Button>
          </div>
        </form>
      </Card>
    </div>
  )
}

const STATUSES = [
  { value: '', label: 'All statuses' },
  { value: 'active', label: 'Active' },
  { value: 'trialing', label: 'Trial' },
  { value: 'past_due', label: 'Past due' },
  { value: 'suspended', label: 'Suspended' },
]

export default function Tenants() {
  const navigate = useNavigate()
  const [rows, setRows] = useState<Organization[]>([])
  const [plans, setPlans] = useState<Plan[]>([])
  const [status, setStatus] = useState('')
  const [plan, setPlan] = useState('')
  const [busy, setBusy] = useState(true)
  const [applying, setApplying] = useState(false)
  const [err, setErr] = useState('')
  const [creating, setCreating] = useState(false)
  const [created, setCreated] = useState<TenantCreated | null>(null)

  function load(st = status, pl = plan) {
    setApplying(true)
    listOrganizations({ status: st || undefined, plan: pl || undefined })
      .then(setRows)
      .catch((e) => setErr(errorText(e, 'Could not load tenants.')))
      .finally(() => { setBusy(false); setApplying(false) })
  }

  useEffect(() => {
    listPlans().then(setPlans).catch(() => setPlans([]))
    load('', '')
  }, [])

  async function rename(o: Organization) {
    const name = await askText({
      title: `Rename ${o.name}`,
      label: 'Company name',
      initial: o.name,
      minLength: 2,
      hint: 'At least 2 characters.',
      confirmText: 'Rename',
    })
    if (!name || name === o.name) return
    setErr('')
    try {
      await renameOrganization(o.id, name)
      notify(`${o.name} is now ${name}.`, 'Tenant renamed')
      load()
    } catch (e) { setErr(errorText(e, 'The tenant was not renamed.')) }
  }

  async function toggle(o: Organization) {
    setErr('')
    try {
      if (o.status === 'active') {
        const reason = await askReason(`Suspend ${o.name}?`)
        if (!reason) return
        const r = await suspendOrganization(o.id, reason)
        notify(`${r.sessions_revoked} session(s) revoked.`,
          `${o.name} is suspended`)
      } else {
        await reactivateOrganization(o.id)
      }
      load()
    } catch (e) { setErr(errorText(e, 'The change was refused.')) }
  }

  if (busy) return <Busy />

  const total = rows.length
  const active = rows.filter((r) => r.lifecycle === 'active').length
  const trial = rows.filter((r) => r.lifecycle === 'trialing').length
  const trouble = rows.filter(
    (r) => r.lifecycle === 'past_due' || r.lifecycle === 'suspended').length

  return (
    <Page
      eyebrow="Tenants"
      crumbs={[{ label: 'Overview', to: '/platform' }, { label: 'Tenants' }]}
      title="Tenants"
      subtitle="Manage the companies using your hotel and resort PMS."
      actions={
        <Button tone="primary" onClick={() => setCreating(true)}
          className="px-5 py-2.5">
          Add tenant
        </Button>
      }>
      <ErrorNote>{err}</ErrorNote>

      {created && (
        <Card className="mb-4 border-l-2 border-l-pf-ok-text p-4">
          <div className="text-pf-desc font-semibold text-pf-ok-text">Tenant created</div>
          <div className="mt-1 space-y-0.5 text-sm text-pf-body">
            <div>
              Property code{' '}
              <strong className="tracking-widest">{created.property_code}</strong>
            </div>
            <div>Owner {created.owner_email}</div>
            <div className={created.welcome_emailed
              ? 'text-pf-muted' : 'text-pf-warn-text'}>
              {created.welcome_emailed
                ? 'A welcome email has been sent.'
                : 'Mail is not configured — the welcome email was NOT sent, so the owner has no way in yet.'}
            </div>
          </div>
          <button onClick={() => setCreated(null)}
            className="mt-2 text-pf-help text-pf-muted hover:text-pf-body">
            Dismiss
          </button>
        </Card>
      )}

      <Metrics items={[
        { label: 'Total tenants', value: total, caption: 'All accounts' },
        { label: 'Active', value: active, caption: 'Paying or live' },
        { label: 'Trial', value: trial, caption: 'Inside a trial period' },
        { label: 'Past due / suspended', value: trouble,
          tone: trouble ? 'warn' : 'default', caption: 'Need attention' },
      ]} />

      <FilterBar onApply={() => load()} applying={applying}>
        <Select id="f-status" label="Lifecycle status" value={status}
          onChange={setStatus} options={STATUSES} />
        <Select id="f-plan" label="Plan" value={plan} onChange={setPlan}
          options={[{ value: '', label: 'All plans' },
            ...plans.map((p) => ({ value: p.code, label: p.name }))]} />
      </FilterBar>

      <DataTable
        title="Tenant directory"
        count={rows.length}
        head={['Tenant ID', 'Company', 'Properties', 'Rooms', 'Plan', 'Status',
          'MRR', '']}
        footnote={`Showing ${rows.length} of ${rows.length} records`}
        empty="No tenants match those filters.">
        {rows.map((o) => (
          <tr key={o.id} className="group hover:bg-pf-bg">
            <Td className="font-semibold text-pf-navy">{o.code || '—'}</Td>
            <Td>{o.name}</Td>
            <Td>{o.properties}</Td>
            <Td>{o.rooms}</Td>
            <Td>{o.plan_name || <span className="text-pf-warn-text">no plan</span>}</Td>
            <Td><Pill value={o.lifecycle} /></Td>
            <Td className="tabular-nums">{money(o.mrr)}</Td>
            <Td className="text-right">
              <div className="flex items-center justify-end gap-1">
                {/* Rename existed only on the detail screen, which is why the
                    list looked like it had no edit at all. */}
                <Button onClick={() => rename(o)}
                  className="opacity-0 transition group-hover:opacity-100">
                  Rename
                </Button>
                <Button tone={o.status === 'active' ? 'danger' : 'default'}
                  onClick={() => toggle(o)}
                  className="opacity-0 transition group-hover:opacity-100">
                  {o.status === 'active' ? 'Suspend' : 'Reactivate'}
                </Button>
                <button onClick={() => navigate(`/platform/tenants/${o.id}`)}
                  title="Open tenant"
                  className="rounded p-1.5 text-pf-placeholder hover:bg-pf-soft hover:text-pf-deep">
                  <ArrowUpRight size={15} />
                </button>
              </div>
            </Td>
          </tr>
        ))}
      </DataTable>

      <Note>
        A tenant can own multiple properties. Its users, permissions and
        operational data remain tenant-scoped — nothing on this console reads
        a tenant's guest records.
      </Note>

      {creating && (
        <NewTenant onClose={() => setCreating(false)}
          onDone={(t) => { setCreating(false); setCreated(t); load() }} />
      )}
    </Page>
  )
}
