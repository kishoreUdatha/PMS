import { useEffect, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { ArrowUpRight, Search as SearchIcon } from 'lucide-react'
import {
  listOrganizations, listProperties, findUsers,
  type Organization, type PlatformProperty, type PlatformUser,
} from '../api'
import {
  Busy, Button, DataTable, ErrorNote, Note, Page, Pill, Td, errorText,
  inputClass,
} from '../ui'

/** What the header search box actually does.
 *
 *  It promised "tenants, properties or people" and delivered none of them: it
 *  navigated to the user lookup, which never read the query it was handed, so
 *  every search landed on an empty form. Searching for a tenant by name was
 *  doubly wrong -- even had the lookup run, it only matches people.
 *
 *  All three are separate reads against separate tables with separate
 *  capabilities behind them, so they are asked for together and reported
 *  separately. One of them being refused is a fact about that section, not a
 *  failed search: an operator without user lookup should still find the
 *  tenant they were looking for.
 */

/** findUsers is the strictest of the three -- the API requires three
 *  characters, because a person lookup over every tenant is not a directory
 *  listing. Tenants and properties are happy with two. */
const MIN_ALL = 2
const MIN_PEOPLE = 3

type Result<T> = { rows: T[]; error: string } | null

export default function Search() {
  const [params, setParams] = useSearchParams()
  const navigate = useNavigate()
  const q = (params.get('q') ?? '').trim()
  const [draft, setDraft] = useState(q)
  const [busy, setBusy] = useState(false)
  const [tenants, setTenants] = useState<Result<Organization>>(null)
  const [properties, setProperties] = useState<Result<PlatformProperty>>(null)
  const [people, setPeople] = useState<Result<PlatformUser>>(null)

  useEffect(() => { setDraft(q) }, [q])

  useEffect(() => {
    if (q.length < MIN_ALL) {
      setTenants(null); setProperties(null); setPeople(null)
      return
    }
    let live = true
    setBusy(true)
    // allSettled, not all: a refused section must not take the others with it.
    Promise.allSettled([
      listOrganizations({ query: q }),
      listProperties({ query: q }),
      q.length >= MIN_PEOPLE
        ? findUsers(q)
        : Promise.resolve([] as PlatformUser[]),
    ]).then(([o, p, u]) => {
      if (!live) return
      setTenants(o.status === 'fulfilled'
        ? { rows: o.value, error: '' }
        : { rows: [], error: errorText(o.reason, 'Tenants could not be searched.') })
      setProperties(p.status === 'fulfilled'
        ? { rows: p.value, error: '' }
        : { rows: [], error: errorText(p.reason, 'Properties could not be searched.') })
      setPeople(u.status === 'fulfilled'
        ? { rows: u.value, error: '' }
        : { rows: [], error: errorText(u.reason, 'People could not be searched.') })
      setBusy(false)
    })
    return () => { live = false }
  }, [q])

  function submit(e: React.FormEvent) {
    e.preventDefault()
    const next = draft.trim()
    if (next.length < MIN_ALL) return
    setParams({ q: next })
  }

  const counts = [tenants, properties, people]
    .map((r) => r?.rows.length ?? 0)
  const total = counts.reduce((a, b) => a + b, 0)

  const box = (
    <form onSubmit={submit} className="mb-5 flex max-w-xl items-center gap-2">
      <div className="relative flex-1">
        <SearchIcon size={15}
          className="absolute left-3 top-1/2 -translate-y-1/2 text-pf-placeholder" />
        <input
          id="search-q"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Tenant, property or person"
          className={`${inputClass} pl-9`}
        />
      </div>
      <Button tone="primary" type="submit" className="px-5 py-2.5">Search</Button>
    </form>
  )

  return (
    <Page
      eyebrow="Search"
      crumbs={[{ label: 'Overview', to: '/platform' }, { label: 'Search' }]}
      title={q ? `Results for “${q}”` : 'Search'}
      subtitle="Tenants, properties and people, across the whole platform.">
      {box}

      {q.length < MIN_ALL && (
        <Note>
          Type at least {MIN_ALL} characters. A tenant or property matches on
          name or code; a person matches on email, name or subject and needs
          {' '}{MIN_PEOPLE} characters, because a lookup across every tenant is
          not a directory listing.
        </Note>
      )}

      {q.length >= MIN_ALL && busy && <Busy />}

      {q.length >= MIN_ALL && !busy && (
        <>
          {total === 0 && (
            <Note>
              Nothing matches “{q}”. Tenants and properties match on name or
              exact code; people match on email, name or subject.
            </Note>
          )}

          {tenants?.error && <ErrorNote>{tenants.error}</ErrorNote>}
          {(tenants?.rows.length ?? 0) > 0 && (
            <DataTable
              title="Tenants"
              count={tenants!.rows.length}
              head={['Tenant', 'Code', 'Properties', 'Plan', 'Status', '']}
              empty="">
              {tenants!.rows.map((o) => (
                <tr key={o.id} className="hover:bg-pf-bg">
                  <Td className="text-pf-navy">{o.name}</Td>
                  <Td className="font-mono text-[12px] text-pf-muted">{o.code}</Td>
                  <Td className="text-pf-muted">{o.properties}</Td>
                  <Td className="text-pf-muted">{o.plan_name ?? '—'}</Td>
                  <Td><Pill value={o.lifecycle} /></Td>
                  <Td className="text-right">
                    <Link to={`/platform/tenants/${o.id}`}
                      title={`Open ${o.name}`}
                      className="inline-flex text-pf-deep hover:text-pf-hover">
                      <ArrowUpRight size={15} />
                    </Link>
                  </Td>
                </tr>
              ))}
            </DataTable>
          )}

          {properties?.error && <ErrorNote>{properties.error}</ErrorNote>}
          {(properties?.rows.length ?? 0) > 0 && (
            <div className="mt-5">
              <DataTable
                title="Properties"
                count={properties!.rows.length}
                head={['Property', 'Code', 'Tenant', 'Rooms', 'Readiness', '']}
                empty="">
                {properties!.rows.map((p) => (
                  <tr key={p.id} className="hover:bg-pf-bg">
                    <Td className="text-pf-navy">{p.name}</Td>
                    <Td className="font-mono text-[12px] text-pf-muted">{p.code}</Td>
                    <Td className="text-pf-muted">{p.organization_name}</Td>
                    <Td className="text-pf-muted">{p.rooms}</Td>
                    <Td><Pill value={p.readiness} /></Td>
                    <Td className="text-right">
                      <Link to={`/platform/properties/${p.id}`}
                        title={`Open ${p.name}`}
                        className="inline-flex text-pf-deep hover:text-pf-hover">
                        <ArrowUpRight size={15} />
                      </Link>
                    </Td>
                  </tr>
                ))}
              </DataTable>
            </div>
          )}

          {people?.error && <ErrorNote>{people.error}</ErrorNote>}
          {(people?.rows.length ?? 0) > 0 && (
            <div className="mt-5">
              <DataTable
                title="People"
                count={people!.rows.length}
                head={['Name', 'Email', 'Tenants', 'Status', '']}
                footnote="Opens the user lookup, where a session can be revoked."
                empty="">
                {people!.rows.map((u) => (
                  <tr key={u.id} className="hover:bg-pf-bg">
                    <Td className="text-pf-navy">{u.display_name}</Td>
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
                    <Td className="text-right">
                      {/* The lookup is where a session can be revoked; it
                          takes the same query, so hand it one that matches
                          exactly this person. */}
                      <Button onClick={() => navigate('/platform/users?q='
                        + encodeURIComponent(u.email || u.display_name))}>
                        Open
                      </Button>
                    </Td>
                  </tr>
                ))}
              </DataTable>
            </div>
          )}

          {q.length >= MIN_ALL && q.length < MIN_PEOPLE && (
            <Note>
              People were not searched: that needs {MIN_PEOPLE} characters.
            </Note>
          )}
        </>
      )}
    </Page>
  )
}
