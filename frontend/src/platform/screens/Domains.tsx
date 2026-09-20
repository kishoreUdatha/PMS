import { useEffect, useState } from 'react'
import { ArrowUpRight, Copy, Globe } from 'lucide-react'
import {
  addDomain, domains, setDomainState, type DomainsPage,
} from '../opsApi'
import {
  Busy, Button, Card, DataTable, ErrorNote, Field, Metrics, Note, Page, Pill,
  Select, Td, errorText, inputClass,
} from '../ui'

/** Screen 18 — custom domains for the guest booking engine.
 *
 *  Every property already has a working booking URL on the shared host. A
 *  custom domain is an upgrade, which is why the second table lists properties
 *  rather than only domains: the useful question is usually "who has not got
 *  one", not "what have we set up".
 *
 *  Nothing is served from a hostname until somebody proves they control it. A
 *  name pointed at this platform by a party who does not own it is a phishing
 *  page with a real booking engine and a real payment form behind it, so the
 *  verify step is the whole feature and the rest is bookkeeping.
 */

function tlsTone(d: { tls_status: string; tls_days_left: number | null }) {
  if (d.tls_status === 'failed') return 'bad'
  if (d.tls_days_left !== null && d.tls_days_left <= 14) return 'bad'
  if (d.tls_status === 'issued') return 'good'
  return 'muted'
}

export default function Domains() {
  const [d, setD] = useState<DomainsPage | null>(null)
  const [busy, setBusy] = useState(true)
  const [err, setErr] = useState('')
  const [adding, setAdding] = useState(false)
  const [propertyId, setPropertyId] = useState('')
  const [hostname, setHostname] = useState('')
  const [instruction, setInstruction] = useState('')

  function load() {
    domains().then(setD)
      .catch((e) => setErr(errorText(e, 'Could not load domains.')))
      .finally(() => setBusy(false))
  }
  useEffect(load, [])

  async function add() {
    if (!propertyId || !hostname.trim()) return
    try {
      const r = await addDomain({
        property_id: propertyId, hostname: hostname.trim() })
      setInstruction(r.instruction)
      setHostname(''); setAdding(false); load()
    } catch (e) { setErr(errorText(e, 'That hostname was not accepted.')) }
  }

  async function move(id: string, action: 'verify' | 'activate' | 'retire') {
    setErr('')
    try { await setDomainState(id, action); load() }
    catch (e) { setErr(errorText(e)) }
  }

  if (busy) return <Busy />
  if (!d) {
    return (
      <Page title="Booking engine domains" eyebrow="Booking engine"
        crumbs={[{ label: 'Overview', to: '/platform' },
          { label: 'Booking engine' }]}>
        <ErrorNote>{err}</ErrorNote>
      </Page>
    )
  }

  const live = d.domains.filter((x) => x.status === 'live')
  const pending = d.domains.filter((x) => x.status === 'pending')
  const expiring = d.domains.filter(
    (x) => x.tls_days_left !== null && x.tls_days_left <= 30
      && x.status === 'live')
  const withoutDomain = d.properties.filter((p) => p.domains === 0)
  // The distinction this screen used to get wrong. Every active property has
  // a /book/{code} URL; only an entitled one answers with rooms. The rest
  // serve the page and then 404 the availability call, which looks to a guest
  // exactly like a property that does not exist.
  const sellable = d.properties.filter((p) => p.booking_engine === 'enabled')
  const notSelling = d.properties.length - sellable.length

  return (
    <Page
      eyebrow="Booking engine"
      crumbs={[{ label: 'Overview', to: '/platform' },
        { label: 'Booking engine domains' }]}
      title="Booking engine domains"
      subtitle="Custom hostnames for the guest booking engine, and the proof behind each one."
      actions={
        <Button tone="primary" className="px-5 py-2.5"
          onClick={() => setAdding((v) => !v)}>
          {adding ? 'Cancel' : 'Add a domain'}
        </Button>
      }>
      <ErrorNote>{err}</ErrorNote>

      <Metrics items={[
        { label: 'Live domains', value: live.length,
          tone: live.length ? 'good' : 'default',
          caption: `${d.domains.length} registered in total` },
        { label: 'Awaiting proof', value: pending.length,
          tone: pending.length ? 'warn' : 'good',
          caption: pending.length
            ? 'DNS record not confirmed yet' : 'Nothing waiting' },
        { label: 'Certificates due', value: expiring.length,
          tone: expiring.length ? 'warn' : 'good',
          caption: expiring.length
            ? 'Expiring within 30 days' : 'None expiring soon' },
        { label: 'Taking bookings', value: sellable.length,
          tone: notSelling ? 'warn' : 'good',
          caption: notSelling
            ? `${notSelling} of ${d.properties.length} not on sale`
            : `All ${d.properties.length} on sale at ${d.hosted_pattern}` },
      ]} />

      {adding && (
        <Card className="mb-5 max-w-2xl p-5">
          <h2 className="text-pf-card text-pf-navy">Add a custom domain</h2>
          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            <Select
              id="domain-property" label="Property" value={propertyId}
              onChange={setPropertyId}
              options={[{ value: '', label: 'Choose a property' },
                ...d.properties.map((p) => ({
                  value: p.id,
                  label: `${p.name} · ${p.code} (${p.tenant_name})` }))]}
            />
            <Field label="Hostname">
              <input id="domain-host" className={inputClass} value={hostname}
                placeholder="book.example.com"
                onChange={(e) => setHostname(e.target.value)} />
            </Field>
          </div>
          <div className="mt-4">
            <Button tone="primary" onClick={add} className="px-5 py-2.5">
              Register and issue a token
            </Button>
          </div>
        </Card>
      )}

      {instruction && (
        <Card className="mb-5 flex items-start gap-3 border-pf-teal p-5">
          <Globe size={17} className="mt-0.5 shrink-0 text-pf-deep" />
          <div className="min-w-0">
            <div className="text-pf-desc font-semibold text-pf-navy">
              Ask the tenant to publish this DNS record
            </div>
            <code className="mt-1.5 block break-all rounded bg-pf-bg px-3 py-2 font-mono text-[11px] text-pf-body">
              {instruction}
            </code>
            <button type="button"
              className="mt-2 inline-flex items-center gap-1.5 text-pf-help text-pf-deep hover:underline"
              onClick={() => navigator.clipboard?.writeText(instruction)}>
              <Copy size={12} /> Copy
            </button>
          </div>
        </Card>
      )}

      <DataTable
        title="Registered domains"
        count={d.domains.length}
        head={['Hostname', 'Property', 'Tenant', 'Status', 'Certificate', '']}
        footnote="A domain serves guest traffic only once its DNS record has been confirmed."
        empty="No custom domain has been registered. Every property is served from the shared host.">
        {d.domains.map((x) => {
          const tone = tlsTone(x)
          return (
            <tr key={x.id} className="hover:bg-pf-bg">
              <Td className="font-mono text-[12px] text-pf-navy">
                {x.hostname}
              </Td>
              <Td className="text-pf-muted">
                {x.property_name} · {x.property_code}
              </Td>
              <Td className="text-pf-muted">{x.tenant_name}</Td>
              <Td><Pill value={x.status} /></Td>
              <Td className={tone === 'bad' ? 'text-pf-warn-text'
                : tone === 'good' ? '' : 'text-pf-muted'}>
                {x.tls_status === 'none' ? '—'
                  : x.tls_days_left !== null
                    ? `${x.tls_status} · ${x.tls_days_left} day(s) left`
                    : x.tls_status}
              </Td>
              <Td className="text-right">
                <div className="flex justify-end gap-1.5">
                  {(x.status === 'pending' || x.status === 'failed') && (
                    <Button onClick={() => move(x.id, 'verify')}>
                      Mark verified
                    </Button>
                  )}
                  {x.status === 'verified' && (
                    <Button tone="primary" onClick={() => move(x.id, 'activate')}>
                      Go live
                    </Button>
                  )}
                  {x.status !== 'retired' && (
                    <Button onClick={() => move(x.id, 'retire')}>Retire</Button>
                  )}
                </div>
              </Td>
            </tr>
          )
        })}
      </DataTable>

      <div className="mt-5">
        <DataTable
          title="Properties on the shared host"
          count={withoutDomain.length}
          head={['Property', 'Code', 'Tenant', 'Booking URL', 'Booking engine',
            'Room types']}
          footnote="A custom domain is an upgrade, not a prerequisite — but a property only takes bookings once its booking engine module is on."
          empty="Every active property has a custom domain.">
          {withoutDomain.map((p) => {
            const on = p.booking_engine === 'enabled'
            return (
              <tr key={p.id} className="hover:bg-pf-bg">
                <Td className="text-pf-navy">{p.name}</Td>
                <Td className="font-mono tracking-widest text-pf-navy">
                  {p.code}
                </Td>
                <Td className="text-pf-muted">{p.tenant_name}</Td>
                <Td className="font-mono text-[11px]">
                  {/* A real link, because the first thing an operator wants
                      to do with a booking URL is open it. Relative on
                      purpose: the gateway serves the console and /book from
                      one origin in production, and the dev server now
                      proxies /book to it, so the same href is right in
                      both. */}
                  {on ? (
                    <a href={`/book/${p.code}`} target="_blank"
                      rel="noreferrer"
                      className="inline-flex items-center gap-1 text-pf-deep hover:underline">
                      /book/{p.code}
                      <ArrowUpRight size={12} />
                    </a>
                  ) : (
                    <span className="text-pf-placeholder line-through">
                      /book/{p.code}
                    </span>
                  )}
                </Td>
                <Td>
                  {on ? <Pill value="on sale" />
                    : <span className="text-pf-warn-text">
                      {p.booking_engine === 'disabled'
                        ? 'switched off' : 'never configured'}
                    </span>}
                </Td>
                <Td className={p.room_types ? 'text-pf-muted'
                  : 'text-pf-warn-text'}>
                  {p.room_types}
                </Td>
              </tr>
            )
          })}
        </DataTable>
      </div>

      <Note>
        A struck-through URL serves the page and then refuses: booking-core
        folds the entitlement into the property lookup, so an unentitled
        property answers a guest exactly as a property that does not exist
        does. That is deliberate — a public endpoint should not be a directory
        of which hotels exist — and it does mean the link is not worth handing
        out until the module is on, which is done from the property's own
        screen.{' '}
        “Mark verified” records that an operator confirmed the DNS record. This
        service does not resolve DNS, so it cannot check for itself — and
        showing a tick for a lookup nobody performed would be worse than asking
        somebody to do it. Certificate dates are recorded here; issuing and
        renewal happen at the edge.
      </Note>
    </Page>
  )
}
