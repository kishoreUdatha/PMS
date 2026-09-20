import { useEffect, useState } from 'react'
import { Check, Minus, ShieldCheck } from 'lucide-react'
import {
  listRoles, listCapabilities, setRoleCapabilities, whoami,
  type PlatformRole, type Capability, type Whoami,
} from '../api'
import {
  Busy, Card, DataTable, ErrorNote, Metrics, Note, Page, Panel,
  Td, askReason, errorText,
} from '../ui'

/** The platform permission matrix — specification screen 15.
 *
 *  A grid rather than a per-role form, because the question people actually
 *  arrive with is comparative: "who can suspend a tenant?" reads down a
 *  column, and "what can a support agent do?" reads across a row. A stack of
 *  five checkbox forms answers neither without clicking through all of them.
 */
export default function Roles() {
  const [roles, setRoles] = useState<PlatformRole[]>([])
  const [caps, setCaps] = useState<Capability[]>([])
  const [me, setMe] = useState<Whoami | null>(null)
  const [busy, setBusy] = useState(true)
  const [err, setErr] = useState('')
  const [saving, setSaving] = useState('')

  function load() {
    Promise.all([listRoles(), listCapabilities(), whoami()])
      .then(([r, c, w]) => { setRoles(r); setCaps(c); setMe(w) })
      .catch((e) => setErr(errorText(e, 'Could not load roles.')))
      .finally(() => setBusy(false))
  }
  useEffect(load, [])

  const mayEdit = me?.capabilities.includes('staff.manage') ?? false

  async function toggle(role: PlatformRole, code: string) {
    if (!mayEdit || saving) return
    const has = role.capabilities.includes(code)
    const next = has
      ? role.capabilities.filter((c) => c !== code)
      : [...role.capabilities, code]
    const reason = await askReason(
      `${has ? 'Remove' : 'Add'} ${code} ${has ? 'from' : 'to'} ${role.name}?`)
    if (!reason) return
    setErr(''); setSaving(role.id)
    try {
      await setRoleCapabilities(role.id, next, reason)
      load()
    } catch (e) {
      setErr(errorText(e, 'The change was refused.'))
    } finally { setSaving('') }
  }

  if (busy) return <Busy />

  return (
    <Page eyebrow="Team & access"
      crumbs={[{ label: 'Overview', to: '/platform' }, { label: 'Roles & permissions' }]}
      title="Roles &amp; permissions"
      subtitle="What each platform role may do. Every route checks its own capability server-side.">
      <ErrorNote>{err}</ErrorNote>

      {!mayEdit && (
        <Card className="mb-4 p-3">
          <p className="text-pf-desc text-pf-muted">
            You hold {me?.roles.join(', ') || 'no roles'}, which does not
            include <code>staff.manage</code>, so this matrix is read-only for
            you.
          </p>
        </Card>
      )}

      <Metrics items={[
        { label: 'Capabilities', value: caps.length,
          caption: 'What the console checks' },
        { label: 'Roles', value: roles.length, caption: 'Composable sets' },
        { label: 'Your roles', value: (me?.roles || []).join(', ') || 'none',
          caption: 'What you hold' },
        { label: 'Your capabilities', value: (me?.capabilities || []).length,
          caption: 'Across all your roles' },
      ]} />

      <div className="mb-5">
        <DataTable title="Permission matrix" count={caps.length}
          head={['Capability', ...roles.map((r) => r.name)]}
          footnote={'Showing ' + caps.length + ' of ' + caps.length + ' capabilities'}>
          {caps.map((c) => (
            <tr key={c.code} className="hover:bg-pf-bg">
              <Td>
                <div className="font-medium text-pf-navy">{c.code}</div>
                <div className="text-pf-help text-pf-muted">{c.description}</div>
              </Td>
              {roles.map((r) => {
                const has = r.capabilities.includes(c.code)
                return (
                  <Td key={r.id} className="text-center">
                    <button
                      type="button"
                      disabled={!mayEdit || saving === r.id}
                      onClick={() => toggle(r, c.code)}
                      aria-label={`${has ? 'Remove' : 'Add'} ${c.code} for ${r.name}`}
                      className={`inline-flex h-6 w-6 items-center justify-center rounded ${
                        has
                          ? 'bg-pf-soft text-pf-deep'
                          : 'bg-pf-bg text-pf-placeholder'
                      } ${mayEdit ? 'cursor-pointer hover:ring-2 hover:ring-brand/30' : 'cursor-default'}`}>
                      {has ? <Check size={13} /> : <Minus size={12} />}
                    </button>
                  </Td>
                )
              })}
            </tr>
          ))}
        </DataTable>
      </div>

      <h2 className="mb-2 text-sm font-medium text-pf-body">The roles</h2>
      <div className="grid gap-3 md:grid-cols-2">
        {roles.map((r) => (
          <Card key={r.id} className="p-4">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <ShieldCheck size={14} className="shrink-0 text-pf-deep" />
                  <span className="font-medium text-pf-navy">{r.name}</span>
                </div>
                <code className="text-pf-help text-pf-muted">{r.code}</code>
              </div>
              <span className="shrink-0 text-pf-help text-pf-muted">
                {r.held_by} {r.held_by === 1 ? 'person' : 'people'}
              </span>
            </div>
            <p className="mt-2 text-sm text-pf-muted">{r.description}</p>
            <p className="mt-2 text-pf-help text-pf-muted">
              {r.capabilities.length} of {caps.length} capabilities
            </p>
          </Card>
        ))}
      </div>

      <div className="mt-5 max-w-md">
        <Panel
          title="Permission meaning"
          rows={[
            { label: 'Manage', value: 'Create, change and remove' },
            { label: 'View', value: 'Read only' },
            { label: 'Approve', value: 'Second signature, never your own' },
          ]}
        />
      </div>

      <Note>
        Roles are composable capabilities, not one unrestricted flag. No role
        grants access to a tenant's guest records — the console has no route
        that reads them.
      </Note>
    </Page>
  )
}