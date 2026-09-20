import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Search } from 'lucide-react'
import {
  findUsers, setUserStatus, revokeSessions, type PlatformUser,
} from '../api'
import {
  Busy, Button, Card, DataTable, ErrorNote, Metrics, Note, Page, Pill, Td,
  askReason, errorText, inputClass, notify,
} from '../ui'

/** Find a person across every tenant.
 *
 *  A search rather than a directory, and the API enforces a minimum query
 *  length: this answers "the person on the phone cannot sign in", not "show me
 *  everyone on the platform".
 */
export default function Users() {
  const [params, setParams] = useSearchParams()
  const [query, setQuery] = useState(params.get('q') ?? '')
  const [rows, setRows] = useState<PlatformUser[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  async function run(q: string) {
    if (q.trim().length < 3) {
      setErr('Type at least three characters.')
      return
    }
    setErr(''); setBusy(true)
    try {
      setRows(await findUsers(q.trim()))
    } catch (e) {
      setErr(errorText(e, 'The search failed.'))
    } finally { setBusy(false) }
  }

  // Arriving with ?q= must actually search. This screen is where the header
  // box and the platform search send you to act on a person, and it used to
  // open an empty form and ignore the query it had just been handed -- which
  // read, correctly, as the search being broken.
  const incoming = params.get('q') ?? ''
  useEffect(() => {
    if (!incoming) return
    setQuery(incoming)
    run(incoming)
  }, [incoming])

  function search() {
    // Put the query in the URL so the result can be linked and reloaded; the
    // effect above is what actually runs it, so there is one path, not two.
    const q = query.trim()
    if (q.length < 3) { setErr('Type at least three characters.'); return }
    if (q === incoming) run(q)
    else setParams({ q })
  }

  async function toggle(u: PlatformUser) {
    const next = u.status === 'active' ? 'suspended' : 'active'
    const reason = await askReason(
      `${next === 'suspended' ? 'Deactivate' : 'Reactivate'} ${u.display_name}?`)
    if (!reason) return
    setErr('')
    try {
      const r = await setUserStatus(u.id, next, reason)
      if (r.sessions_revoked) {
        notify(`${r.sessions_revoked} session(s) revoked.`)
      }
      search()
    } catch (e) { setErr(errorText(e, 'The change was refused.')) }
  }

  async function kick(u: PlatformUser) {
    const reason = await askReason(`Sign ${u.display_name} out everywhere?`)
    if (!reason) return
    setErr('')
    try {
      const r = await revokeSessions(u.id, reason)
      notify(`${r.sessions_revoked} session(s) revoked.`, 'Signed out')
    } catch (e) { setErr(errorText(e)) }
  }

  const found = rows ?? []
  const platform = found.filter((u) => u.is_platform_admin).length
  const suspended = found.filter((u) => u.status !== 'active').length
  const orphans = found.filter(
    (u) => u.memberships.length === 0 && !u.is_platform_admin).length

  return (
    <Page
      eyebrow="Team & access"
      crumbs={[{ label: 'Overview', to: '/platform' },
        { label: 'User lookup' }]}
      title="User lookup"
      subtitle="Find a person by email, name or subject across every tenant.">
      <ErrorNote>{err}</ErrorNote>

      {rows !== null && (
        <Metrics items={[
          { label: 'Matches', value: found.length, caption: `for "${query}"` },
          { label: 'Platform staff', value: platform,
            caption: 'Belong to no tenant' },
          { label: 'Suspended', value: suspended,
            tone: suspended ? 'warn' : 'default', caption: 'Cannot sign in' },
          { label: 'No membership', value: orphans,
            tone: orphans ? 'warn' : 'default',
            caption: 'Not platform staff either' },
        ]} />
      )}

      <Card className="mb-4 p-4">
        <div className="flex flex-wrap items-end gap-3">
          <div className="min-w-[18rem] flex-1">
            <label htmlFor="u-q"
              className="mb-1.5 block text-pf-label text-pf-muted">
              Email, name or subject
            </label>
            <div className="relative">
              <Search size={15}
                className="absolute left-3 top-1/2 -translate-y-1/2 text-pf-placeholder" />
              <input id="u-q" className={`${inputClass} pl-9`} value={query}
                autoFocus placeholder="At least three characters"
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && search()} />
            </div>
          </div>
          <Button tone="primary" onClick={search} className="px-5 py-2.5">
            Search
          </Button>
        </div>
        <p className="mt-2.5 text-pf-help text-pf-muted">
          A lookup, not a directory: this answers "the person on the phone
          cannot sign in", so it needs a name to start from rather than
          listing every user on the platform.
        </p>
      </Card>

      {busy && <Busy />}

      {!busy && rows !== null && (
        <DataTable
          title="People"
          count={found.length}
          head={['Name', 'Email', 'Tenants', 'Account', 'MFA', 'Last sign-in', '']}
          footnote={`Showing ${found.length} of ${found.length} records`}
          empty="Nobody matches that.">
          {found.map((u) => (
            <tr key={u.id} className="group hover:bg-pf-bg">
              <Td className="text-pf-navy">
                {u.display_name}
                {u.is_platform_admin && (
                  <span className="ml-2 rounded bg-pf-soft px-1.5 py-0.5 text-[10px] text-pf-deep">
                    platform
                  </span>
                )}
              </Td>
              <Td className="text-pf-muted">{u.email || '—'}</Td>
              <Td>
                {u.memberships.length === 0 ? (
                  <span className="text-pf-help text-pf-muted">
                    {u.is_platform_admin ? 'none — as it should be' : 'none'}
                  </span>
                ) : (
                  <div className="flex flex-wrap gap-1">
                    {u.memberships.map((m) => (
                      <Link key={m.organization_id}
                        to={`/platform/tenants/${m.organization_id}`}
                        className="rounded border border-pf-divider px-1.5 py-0.5 text-[11px] text-pf-body hover:border-pf-teal hover:text-pf-deep">
                        {m.organization}
                      </Link>
                    ))}
                  </div>
                )}
              </Td>
              <Td><Pill value={u.status} /></Td>
              <Td className="text-pf-muted">{u.mfa_status || '—'}</Td>
              <Td className="text-pf-muted">
                {u.last_login_at
                  ? new Date(u.last_login_at).toLocaleDateString()
                  : 'never'}
              </Td>
              <Td className="text-right">
                <div className="flex items-center justify-end gap-1 opacity-0 transition group-hover:opacity-100">
                  <Button onClick={() => kick(u)}>Revoke sessions</Button>
                  <Button tone={u.status === 'active' ? 'danger' : 'default'}
                    onClick={() => toggle(u)}>
                    {u.status === 'active' ? 'Deactivate' : 'Reactivate'}
                  </Button>
                </div>
              </Td>
            </tr>
          ))}
        </DataTable>
      )}

      <Note>
        Deactivating a person here stops them signing in to every tenant they
        belong to, and revokes their live sessions. It does not remove them
        from a tenant — that is the tenant's own administrator to do.
      </Note>
    </Page>
  )
}
