import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { ArrowUpRight, SlidersHorizontal, X } from 'lucide-react'
import {
  listProperties, setPropertyStatus, listModules, setModule,
  listOrganizations, type PlatformProperty, type Module, type Organization,
} from '../api'
import {
  Busy, Button, Card, DataTable, ErrorNote, FilterBar,
  Metrics, Note, Page, Pill, Select, Td, askReason, errorText,
} from '../ui'

/** Screen 08 — the cross-tenant property directory.
 *
 *  "State" is readiness rather than row status: a property can be suspended,
 *  live, or still in setup, and those are three answers that two columns in
 *  the database give between them. Resolved server-side so every caller gets
 *  the same word.
 */

function Modules({ property, onClose }: {
  property: PlatformProperty; onClose: () => void
}) {
  const [rows, setRows] = useState<Module[]>([])
  const [busy, setBusy] = useState(true)
  const [err, setErr] = useState('')

  function load() {
    listModules(property.id).then(setRows)
      .catch((e) => setErr(errorText(e)))
      .finally(() => setBusy(false))
  }
  useEffect(load, [property.id])

  async function toggle(m: Module) {
    setErr('')
    try { await setModule(property.id, m.module_code, !m.enabled); load() }
    catch (e) { setErr(errorText(e)) }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center overflow-y-auto bg-black/50 p-4">
      <Card className="w-full max-w-md p-6">
        <div className="mb-1 flex items-start justify-between">
          <h2 className="text-pf-card text-pf-navy">
            Modules — {property.name}
          </h2>
          <button onClick={onClose} className="text-pf-muted hover:text-pf-body">
            <X size={18} />
          </button>
        </div>
        <p className="mb-4 text-pf-help text-pf-muted">
          What this property is entitled to use.
        </p>
        <ErrorNote>{err}</ErrorNote>
        {busy ? <Busy /> : rows.length === 0 ? (
          <p className="py-8 text-center text-pf-td text-pf-muted">
            No modules recorded for this property yet.
          </p>
        ) : (
          <ul className="space-y-1">
            {rows.map((m) => (
              <li key={m.module_code}
                className="flex items-center justify-between rounded-md px-2 py-2 hover:bg-pf-bg">
                <span className="text-pf-td text-pf-body">{m.module_code}</span>
                <Button tone={m.enabled ? 'default' : 'primary'}
                  onClick={() => toggle(m)}>
                  {m.enabled ? 'Disable' : 'Enable'}
                </Button>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  )
}

export default function Properties() {
  const navigate = useNavigate()
  const [rows, setRows] = useState<PlatformProperty[]>([])
  const [orgs, setOrgs] = useState<Organization[]>([])
  const [org, setOrg] = useState('')
  const [state, setState] = useState('')
  const [busy, setBusy] = useState(true)
  const [applying, setApplying] = useState(false)
  const [err, setErr] = useState('')
  const [modules, setModules] = useState<PlatformProperty | null>(null)

  function load(o = org, st = state) {
    setApplying(true)
    listProperties({ org_id: o || undefined, state: st || undefined })
      .then(setRows)
      .catch((e) => setErr(errorText(e, 'Could not load properties.')))
      .finally(() => { setBusy(false); setApplying(false) })
  }

  useEffect(() => {
    listOrganizations().then(setOrgs).catch(() => setOrgs([]))
    load('', '')
  }, [])

  async function toggle(p: PlatformProperty) {
    setErr('')
    const next = p.status === 'active' ? 'suspended' : 'active'
    const reason = await askReason(
      (next === 'suspended' ? 'Suspend ' : 'Reactivate ') + p.name + '?')
    if (!reason) return
    try { await setPropertyStatus(p.id, next, reason); load() }
    catch (e) { setErr(errorText(e, 'The change was refused.')) }
  }

  if (busy) return <Busy />

  const rooms = rows.reduce((t, r) => t + r.rooms, 0)
  const live = rows.filter((r) => r.readiness === 'live').length
  const notLive = rows.length - live

  return (
    <Page
      eyebrow="Properties"
      crumbs={[{ label: 'Overview', to: '/platform' },
        { label: 'Properties' }]}
      title="Properties"
      subtitle="Review property ownership, capacity and readiness across tenants.">
      <ErrorNote>{err}</ErrorNote>

      <Metrics items={[
        { label: 'Properties', value: rows.length, caption: 'Across all tenants' },
        { label: 'Configured rooms', value: rooms, caption: 'Sellable inventory' },
        { label: 'Live', value: live, tone: 'good', caption: 'Setup complete' },
        { label: 'Setup / suspended', value: notLive,
          tone: notLive ? 'warn' : 'default', caption: 'Not selling yet' },
      ]} />

      <FilterBar onApply={() => load()} applying={applying}>
        <Select id="p-tenant" label="Tenant" value={org} onChange={setOrg}
          options={[{ value: '', label: 'All tenants' },
            ...orgs.map((o) => ({ value: o.id, label: o.name }))]} />
        <Select id="p-state" label="Property state" value={state}
          onChange={setState} options={[
            { value: '', label: 'All states' },
            { value: 'live', label: 'Live' },
            { value: 'setup', label: 'In setup' },
            { value: 'suspended', label: 'Suspended' },
          ]} />
      </FilterBar>

      <DataTable
        title="Property directory"
        count={rows.length}
        head={['Code', 'Property', 'Tenant', 'City', 'Rooms', 'State', '']}
        footnote={'Showing ' + rows.length + ' of ' + rows.length + ' records'}
        empty="No properties match those filters.">
        {rows.map((p) => (
          <tr key={p.id} className="group hover:bg-pf-bg">
            <Td className="font-mono tracking-widest text-pf-navy">{p.code}</Td>
            <Td>
              <Link to={'/platform/properties/' + p.id}
                className="text-pf-navy hover:text-pf-deep hover:underline">
                {p.name}
              </Link>
            </Td>
            <Td>
              <Link to={'/platform/tenants/' + p.organization_id}
                className="text-pf-muted hover:text-pf-deep hover:underline">
                {p.organization_name}
              </Link>
            </Td>
            <Td className="text-pf-muted">{p.city || '—'}</Td>
            <Td>{p.rooms}</Td>
            <Td><Pill value={p.readiness} /></Td>
            <Td className="text-right">
              <div className="flex items-center justify-end gap-1">
                <Button onClick={() => setModules(p)}
                  className="opacity-0 transition group-hover:opacity-100">
                  <span className="flex items-center gap-1.5">
                    <SlidersHorizontal size={12} /> Modules
                  </span>
                </Button>
                <Button tone={p.status === 'active' ? 'danger' : 'default'}
                  onClick={() => toggle(p)}
                  className="opacity-0 transition group-hover:opacity-100">
                  {p.status === 'active' ? 'Suspend' : 'Reactivate'}
                </Button>
                <button onClick={() => navigate('/platform/properties/' + p.id)}
                  title={'Open ' + p.name}
                  className="rounded p-1 text-pf-placeholder hover:bg-pf-soft hover:text-pf-deep">
                  <ArrowUpRight size={15} />
                </button>
              </div>
            </Td>
          </tr>
        ))}
      </DataTable>

      <Note>
        Property codes are unique identifiers. Every property belongs to
        exactly one tenant, and nothing on this screen reads that tenant's
        guest or booking records.
      </Note>

      {modules && (
        <Modules property={modules} onClose={() => setModules(null)} />
      )}
    </Page>
  )
}
