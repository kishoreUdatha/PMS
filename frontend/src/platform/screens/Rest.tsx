/** The remaining console screens: business dates, audit, permissions, team.
 *
 *  Grouped because each is one list over one endpoint. Tenants, properties,
 *  subscriptions and plans earn their own files by having dialogs and
 *  multi-step flows; these four do not, and four near-identical files would
 *  say less than one.
 */
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { AlertTriangle, Plus, ShieldCheck } from 'lucide-react'
import {
  businessDates, platformAudit, listPermissions, createPermission,
  listAdmins, grantAdmin, revokeAdmin, listRoles, setAdminRoles,
  type BusinessDateRow, type AuditRow, type Permission, type PlatformAdmin,
  type PlatformRole,
} from '../api'
import {
  Busy, Button, Card, DataTable, ErrorNote, Field, FilterBar, Metrics, Note,
  Page, Panel, Pill, Select, SplitLayout, Td, askReason, errorText, inputClass,
} from '../ui'

const CRUMB = { label: 'Overview', to: '/platform' }

// ------------------------------------------------------- business dates ---

export function BusinessDates() {
  const [rows, setRows] = useState<BusinessDateRow[]>([])
  const [busy, setBusy] = useState(true)
  const [err, setErr] = useState('')

  useEffect(() => {
    businessDates().then(setRows)
      .catch((e) => setErr(errorText(e)))
      .finally(() => setBusy(false))
  }, [])

  if (busy) return <Busy />

  const drifted = rows.filter((r) => (r.days_ahead ?? 0) > 0)
  const neverRun = rows.filter((r) => !r.last_run_status)
  const noHour = rows.filter((r) => r.audit_hour === null)

  return (
    <Page
      eyebrow="Tenants"
      crumbs={[CRUMB, { label: 'Business dates' }]}
      title="Business dates"
      subtitle="Where each property's business date has got to, and whether it has run ahead of its own calendar.">
      <ErrorNote>{err}</ErrorNote>

      <Metrics items={[
        { label: 'Properties', value: rows.length, caption: 'Active properties' },
        { label: 'Drifted', value: drifted.length,
          tone: drifted.length ? 'warn' : 'good',
          caption: 'Ahead of their own date' },
        { label: 'Never audited', value: neverRun.length,
          tone: neverRun.length ? 'warn' : 'default',
          caption: 'Night audit has not run' },
        { label: 'No audit hour', value: noHour.length,
          caption: 'Schedule not configured' },
      ]} />

      <DataTable
        title="Night audit status"
        count={rows.length}
        head={['Property', 'Tenant', 'Last audited', 'Drift', 'Last run',
          'Audit hour', 'Timezone']}
        footnote={'Showing ' + rows.length + ' of ' + rows.length + ' records'}>
        {rows.map((r) => {
          const ahead = r.days_ahead ?? 0
          return (
            <tr key={r.property_id} className="hover:bg-pf-bg">
              <Td className="text-pf-navy">
                <Link to={'/platform/properties/' + r.property_id}
                  className="hover:text-pf-deep hover:underline">
                  <span className="font-mono tracking-widest text-pf-muted">
                    {r.code}
                  </span>{' '}
                  {r.name}
                </Link>
              </Td>
              <Td className="text-pf-muted">{r.organization_name}</Td>
              <Td className="text-pf-body">{r.last_audited_date || '—'}</Td>
              <Td>
                {ahead > 0 ? (
                  <span className="inline-flex items-center gap-1 rounded-full bg-pf-warn-bg px-2.5 py-1 text-pf-badge text-pf-warn-text">
                    <AlertTriangle size={11} /> {ahead} day
                    {ahead === 1 ? '' : 's'} ahead
                  </span>
                ) : (
                  <span className="text-pf-help text-pf-muted">aligned</span>
                )}
              </Td>
              <Td>{r.last_run_status
                ? <Pill value={r.last_run_status} />
                : <span className="text-pf-help text-pf-muted">never run</span>}
              </Td>
              <Td className="text-pf-muted">
                {r.audit_hour === null ? 'not set' : r.audit_hour + ':00'}
              </Td>
              <Td className="text-pf-muted">{r.timezone}</Td>
            </tr>
          )
        })}
      </DataTable>

      <Note>
        A business date that runs ahead of the property's own calendar makes
        every report answer for the wrong day. This screen exists because that
        happened and nothing surfaced it.
      </Note>
    </Page>
  )
}

// ---------------------------------------------------------------- audit ---

/** The pack's "Category" column: the first segment of the action code, which
 *  is how these are named throughout the system. */
function category(action: string): string {
  const head = action.split('.')[0]
  return head.charAt(0).toUpperCase() + head.slice(1)
}

export function Audit() {
  const [rows, setRows] = useState<AuditRow[]>([])
  const [cat, setCat] = useState('')
  const [result, setResult] = useState('')
  const [busy, setBusy] = useState(true)
  const [err, setErr] = useState('')

  function load(c = cat, r = result) {
    setBusy(true)
    platformAudit({
      action: c || undefined,
      limit: 200,
    })
      .then((all) => setRows(
        r === 'denied' ? all.filter((x) => x.action.endsWith('.denied'))
          : r === 'success' ? all.filter((x) => !x.action.endsWith('.denied'))
            : all))
      .catch((e) => setErr(errorText(e)))
      .finally(() => setBusy(false))
  }
  useEffect(() => { load('', '') }, [])

  const denied = rows.filter((r) => r.action.endsWith('.denied')).length

  return (
    <Page
      eyebrow="Audit &amp; security"
      crumbs={[CRUMB, { label: 'Audit logs' }]}
      title="Audit logs"
      subtitle="Trace platform changes, approvals and staff access."
      actions={
        <Link to="/platform/security">
          <Button className="px-5 py-2.5">Security &amp; sessions</Button>
        </Link>
      }>
      <ErrorNote>{err}</ErrorNote>

      <Metrics items={[
        { label: 'Events shown', value: rows.length, caption: 'Most recent first' },
        { label: 'Refused actions', value: denied,
          tone: denied ? 'warn' : 'good',
          caption: 'Reached for what they lack' },
        { label: 'Tenants touched',
          value: new Set(rows.map((r) => r.organization_id).filter(Boolean)).size,
          caption: 'Distinct organisations' },
        { label: 'Actors',
          value: new Set(rows.map((r) => r.actor_subject).filter(Boolean)).size,
          caption: 'Distinct people and jobs' },
      ]} />

      <FilterBar onApply={() => load()} applying={busy}>
        <Select id="a-cat" label="Event category" value={cat} onChange={setCat}
          options={[
            { value: '', label: 'All categories' },
            { value: 'platform.', label: 'Platform' },
            { value: 'billing.', label: 'Billing' },
            { value: 'mfa.', label: 'Security' },
            { value: 'account.', label: 'Account' },
          ]} />
        <Select id="a-result" label="Result" value={result} onChange={setResult}
          options={[
            { value: '', label: 'All results' },
            { value: 'success', label: 'Succeeded' },
            { value: 'denied', label: 'Refused' },
          ]} />
      </FilterBar>

      {busy ? <Busy /> : (
        <DataTable
          title="Event trail"
          count={rows.length}
          head={['Time (IST)', 'Actor', 'Category', 'Tenant / scope', 'Action',
            'Result']}
          footnote={'Showing ' + rows.length + ' of ' + rows.length + ' records'}
          empty="Nothing recorded for that filter.">
          {rows.map((r) => {
            const refused = r.action.endsWith('.denied')
            return (
              <tr key={r.id} className="hover:bg-pf-bg">
                <Td className="whitespace-nowrap text-pf-muted">
                  {new Date(r.occurred_at).toLocaleString('en-IN', {
                    day: '2-digit', month: 'short', hour: '2-digit',
                    minute: '2-digit',
                  })}
                </Td>
                <Td className="text-pf-body">{r.actor_subject || '—'}</Td>
                <Td className="text-pf-muted">{category(r.action)}</Td>
                <Td>
                  {r.organization_id ? (
                    <Link to={'/platform/tenants/' + r.organization_id}
                      className="text-pf-deep hover:underline">
                      {r.organization_name}
                    </Link>
                  ) : (
                    <span className="text-pf-help text-pf-muted">platform</span>
                  )}
                </Td>
                <Td className="text-pf-navy">{r.action}</Td>
                <Td>
                  <Pill value={refused ? 'refused' : 'ok'} />
                </Td>
              </tr>
            )
          })}
        </DataTable>
      )}

      <Note>
        Entries are append-only and carry a correlation ID. Before/after
        snapshots are deliberately withheld here — a tenant's own change can
        contain their data, and this screen is read from outside that tenant.
      </Note>
    </Page>
  )
}

// ---------------------------------------------------------- permissions ---

export function Permissions() {
  const [rows, setRows] = useState<Permission[]>([])
  const [busy, setBusy] = useState(true)
  const [err, setErr] = useState('')
  const [adding, setAdding] = useState(false)
  const [f, setF] = useState({ resource_code: '', action_code: '', description: '' })

  function load() {
    setBusy(true)
    listPermissions().then(setRows)
      .catch((e) => setErr(errorText(e)))
      .finally(() => setBusy(false))
  }
  useEffect(load, [])

  async function add(e: React.FormEvent) {
    e.preventDefault()
    setErr('')
    try {
      await createPermission(f)
      setAdding(false)
      setF({ resource_code: '', action_code: '', description: '' })
      load()
    } catch (e2) { setErr(errorText(e2)) }
  }

  if (busy) return <Busy />

  const ungranted = rows.filter((r) => r.granted_to_roles === 0)
  const resources = new Set(rows.map((r) => r.resource_code)).size

  return (
    <Page
      eyebrow="Team &amp; access"
      crumbs={[CRUMB, { label: 'Tenant catalogue' }]}
      title="Tenant permission catalogue"
      subtitle="The global catalogue every tenant's roles are built from. Adding one grants nothing by itself."
      actions={
        <Button tone="primary" className="px-5 py-2.5"
          onClick={() => setAdding(!adding)}>
          <span className="flex items-center gap-1.5"><Plus size={14} /> Add permission</span>
        </Button>
      }>
      <ErrorNote>{err}</ErrorNote>

      <Metrics items={[
        { label: 'Permissions', value: rows.length, caption: 'In the catalogue' },
        { label: 'Resources', value: resources, caption: 'Distinct areas' },
        { label: 'Granted to no role', value: ungranted.length,
          tone: ungranted.length ? 'warn' : 'good',
          caption: 'Routes refusing everyone' },
        { label: 'Scope', value: 'Tenant', caption: 'Not platform staff' },
      ]} />

      {adding && (
        <Card className="mb-4 p-5">
          <form onSubmit={add} className="flex flex-wrap items-end gap-4">
            <div className="w-48">
              <Field label="Resource">
                <input className={inputClass} value={f.resource_code} required
                  placeholder="reservations"
                  onChange={(e) => setF({ ...f, resource_code: e.target.value })} />
              </Field>
            </div>
            <div className="w-48">
              <Field label="Action">
                <input className={inputClass} value={f.action_code} required
                  placeholder="export"
                  onChange={(e) => setF({ ...f, action_code: e.target.value })} />
              </Field>
            </div>
            <div className="min-w-[16rem] flex-1">
              <Field label="Description">
                <input className={inputClass} value={f.description}
                  onChange={(e) => setF({ ...f, description: e.target.value })} />
              </Field>
            </div>
            <Button type="submit" tone="primary">Create</Button>
          </form>
        </Card>
      )}

      <SplitLayout
        main={
          <DataTable
            title="Permission catalogue"
            count={rows.length}
            head={['Resource', 'Action', 'Description', 'Granted to roles']}
            footnote={'Showing ' + rows.length + ' of ' + rows.length + ' records'}>
            {rows.map((r) => (
              <tr key={r.id} className="hover:bg-pf-bg">
                <Td className="text-pf-navy">{r.resource_code}</Td>
                <Td className="text-pf-body">{r.action_code}</Td>
                <Td className="text-pf-muted">{r.description || '—'}</Td>
                <Td className={r.granted_to_roles === 0
                  ? 'text-pf-warn-text' : 'text-pf-muted'}>
                  {r.granted_to_roles}
                </Td>
              </tr>
            ))}
          </DataTable>
        }
        side={
          <Panel
            title="Why this matters"
            rows={[
              { label: 'Granted to no role', value: ungranted.length,
                tone: ungranted.length ? 'warn' : 'good' },
              { label: 'Effect', value: 'Route refuses everyone' },
              { label: 'Seen before', value: 'property.create' },
              { label: 'Fix', value: 'Grant it, or drop the gate' },
            ]}
          />
        }
      />

      <Note>
        A permission the catalogue lacks is one every route gated on it
        refuses — which is how a button shipped that returned 403 to every
        tenant. Creating one here does not grant it to anybody.
      </Note>
    </Page>
  )
}

// -------------------------------------------------------- platform team ---

export function Admins() {
  const [rows, setRows] = useState<PlatformAdmin[]>([])
  const [status, setStatus] = useState('')
  const [busy, setBusy] = useState(true)
  const [err, setErr] = useState('')
  const [email, setEmail] = useState('')
  const [note, setNote] = useState('')
  const [roles, setRoles] = useState<PlatformRole[]>([])
  const [editing, setEditing] = useState<PlatformAdmin | null>(null)
  const [picked, setPicked] = useState<string[]>([])
  const [saving, setSaving] = useState(false)

  function load() {
    setBusy(true)
    listAdmins().then(setRows)
      .catch((e) => setErr(errorText(e)))
      .finally(() => setBusy(false))
  }
  useEffect(load, [])
  // The roles a staff member can be given. Read once: the list changes when
  // somebody edits it on the Roles screen, not while this table is open.
  useEffect(() => { listRoles().then(setRoles).catch(() => {}) }, [])

  function editRoles(a: PlatformAdmin) {
    setEditing(a)
    setPicked([...(a.roles || [])])
    setErr('')
  }

  async function saveRoles() {
    if (!editing) return
    // Every capability this person holds comes from their roles, so an empty
    // selection is not a no-op -- it is a revocation in everything but name.
    // Saying so is the difference between an accident and a decision.
    const emptying = picked.length === 0
    const reason = await askReason(
      emptying
        ? `Remove every role from ${editing.display_name}? They keep platform `
          + 'access but will be able to do nothing.'
        : `Set roles for ${editing.display_name} to ${picked.join(', ')}?`)
    if (!reason) return
    setSaving(true)
    try {
      await setAdminRoles(editing.user_id, picked, reason)
      setEditing(null)
      load()
    } catch (e) { setErr(errorText(e, 'The roles were not changed.')) }
    finally { setSaving(false) }
  }

  async function grant(e: React.FormEvent) {
    e.preventDefault()
    setErr('')
    try {
      await grantAdmin(email.trim(), note.trim() || undefined)
      setEmail(''); setNote('')
      load()
    } catch (e2) { setErr(errorText(e2)) }
  }

  async function revoke(a: PlatformAdmin) {
    const reason = await askReason(
      'Revoke platform access for ' + a.display_name + '?')
    if (!reason) return
    setErr('')
    try { await revokeAdmin(a.user_id, reason); load() }
    catch (e) { setErr(errorText(e)) }
  }

  if (busy) return <Busy />

  const shown = status ? rows.filter((r) => r.status === status) : rows
  const active = rows.filter((r) => r.status === 'active')
  const withMfa = active.filter((r) => r.mfa === 'active').length

  return (
    <Page
      eyebrow="Team &amp; access"
      crumbs={[CRUMB, { label: 'Platform team' }]}
      title="Platform team"
      subtitle="Manage internal platform staff and their access responsibilities."
      actions={
        <Link to="/platform/roles">
          <Button className="px-5 py-2.5">Roles &amp; permissions</Button>
        </Link>
      }>
      <ErrorNote>{err}</ErrorNote>

      <Metrics items={[
        { label: 'Staff accounts', value: rows.length, caption: 'All time' },
        { label: 'Active', value: active.length, caption: 'Can sign in today' },
        { label: 'With a second factor', value: withMfa + ' / ' + active.length,
          tone: withMfa < active.length ? 'warn' : 'good',
          caption: 'MFA enrolled and confirmed' },
        { label: 'Roles defined', value: 5, caption: 'Composable capabilities' },
      ]} />

      <Card className="mb-4 p-5">
        <form onSubmit={grant} className="flex flex-wrap items-end gap-4">
          <div className="min-w-[16rem] flex-1">
            <Field label="Grant platform access to">
              <input className={inputClass} type="email" value={email} required
                placeholder="ops@example.com"
                onChange={(e) => setEmail(e.target.value)} />
            </Field>
          </div>
          <div className="min-w-[14rem] flex-1">
            <Field label="Note">
              <input className={inputClass} value={note}
                onChange={(e) => setNote(e.target.value)} />
            </Field>
          </div>
          <Button type="submit" tone="primary">
            <span className="flex items-center gap-1.5">
              <ShieldCheck size={14} /> Grant
            </span>
          </Button>
        </form>
        <p className="mt-2.5 text-pf-help text-pf-muted">
          The account must already exist and must belong to no tenant. A tenant
          user cannot hold platform access — they would see every organisation
          here and their own through the application, and no audit row could
          say which they were acting as.
        </p>
      </Card>

      <FilterBar onApply={() => undefined}>
        <Select id="t-status" label="Staff status" value={status}
          onChange={setStatus} options={[
            { value: '', label: 'All statuses' },
            { value: 'active', label: 'Active' },
            { value: 'revoked', label: 'Revoked' },
          ]} />
      </FilterBar>

      <DataTable
        title="Platform staff"
        count={shown.length}
        head={['Staff member', 'Work email', 'Role', 'MFA', 'Last active',
          'Status', '']}
        footnote={'Showing ' + shown.length + ' of ' + rows.length + ' records'}
        empty="No staff match that filter.">
        {shown.map((a) => (
          <tr key={a.id} className="group hover:bg-pf-bg">
            <Td className="text-pf-navy">{a.display_name}</Td>
            <Td className="text-pf-muted">{a.email || '—'}</Td>
            <Td>
              {(a.roles || []).length === 0 ? (
                <span className="text-pf-help text-pf-warn-text">
                  no roles — cannot act
                </span>
              ) : (
                <div className="flex flex-wrap gap-1">
                  {(a.roles || []).map((r) => (
                    <span key={r}
                      className="rounded bg-pf-soft px-1.5 py-0.5 text-[10px] text-pf-deep">
                      {r}
                    </span>
                  ))}
                </div>
              )}
            </Td>
            <Td>
              {a.mfa === 'active'
                ? <Pill value="on" />
                : <span className="text-pf-help text-pf-warn-text">
                    {a.mfa === 'pending' ? 'not confirmed' : 'off'}
                  </span>}
            </Td>
            <Td className="text-pf-muted">
              {a.last_active
                ? new Date(a.last_active).toLocaleDateString()
                : 'never'}
            </Td>
            <Td><Pill value={a.status} /></Td>
            <Td className="text-right">
              {a.status === 'active' && (
                <div className="flex justify-end gap-1.5">
                  <Button onClick={() => editRoles(a)}>Edit roles</Button>
                  <Button tone="danger" onClick={() => revoke(a)}
                    className="opacity-0 transition group-hover:opacity-100">
                    Revoke
                  </Button>
                </div>
              )}
            </Td>
          </tr>
        ))}
      </DataTable>

      {editing && (
        <Card className="mt-5 max-w-2xl p-5">
          <h2 className="text-pf-card text-pf-navy">
            Roles for {editing.display_name}
          </h2>
          <p className="mt-1 text-pf-help text-pf-muted">
            Every capability this person holds comes from the roles ticked
            here. Granting platform access on its own lets somebody sign in
            and do nothing.
          </p>
          <div className="mt-4 space-y-1.5">
            {roles.map((r) => (
              <label key={r.code}
                className="flex items-start gap-2.5 rounded-md px-2 py-1.5 hover:bg-pf-bg">
                <input type="checkbox" id={`role-${r.code}`}
                  className="mt-1"
                  checked={picked.includes(r.code)}
                  onChange={(e) => setPicked(e.target.checked
                    ? [...picked, r.code]
                    : picked.filter((x) => x !== r.code))} />
                <span className="min-w-0">
                  <span className="block text-pf-td text-pf-navy">{r.name}</span>
                  <span className="block text-pf-help text-pf-muted">
                    {r.description || r.code}
                  </span>
                </span>
              </label>
            ))}
          </div>
          {picked.length === 0 && (
            <p className="mt-3 text-pf-help text-pf-warn-text">
              With no roles they keep platform access and can do nothing. If
              that is what you mean, revoke their access instead — it says so
              plainly in the audit log.
            </p>
          )}
          <div className="mt-4 flex items-center gap-2">
            <Button tone="primary" onClick={saveRoles} disabled={saving}
              className="px-5 py-2.5">
              {saving ? 'Saving…' : 'Save roles'}
            </Button>
            <Button onClick={() => setEditing(null)}>Cancel</Button>
          </div>
        </Card>
      )}

      <Note>
        Platform staff roles are separate from tenant owner, property manager
        and front-desk roles. Nothing granted here confers any authority inside
        a customer's account.
      </Note>
    </Page>
  )
}
