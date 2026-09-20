import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { channelHealth, type ChannelLink } from '../api'
import {
  Busy, DataTable, ErrorNote, FilterBar, Metrics, Note, Page, Panel, Pill,
  Select, SplitLayout, Td, errorText,
} from '../ui'

/** Screen 17 — channel connectivity across every tenant.
 *
 *  Connection *state*, never connection secrets. What was last pushed, what is
 *  mapped and what arrived are operational facts a platform operator needs;
 *  the credential that authenticates the link is a different privilege and
 *  has no route behind this screen.
 *
 *  Provisioning and pushing are shown separately because they fail
 *  separately. A link can provision and never push, or push happily against a
 *  half-built mapping — collapsing both into one "healthy" hides the case
 *  worth finding.
 */
export default function Channels() {
  const [rows, setRows] = useState<ChannelLink[]>([])
  const [provider, setProvider] = useState('')
  const [busy, setBusy] = useState(true)
  const [applying, setApplying] = useState(false)
  const [err, setErr] = useState('')

  function load(p = provider) {
    setApplying(true)
    channelHealth(p || undefined)
      .then(setRows)
      .catch((e) => setErr(errorText(e, 'Could not load channel health.')))
      .finally(() => { setBusy(false); setApplying(false) })
  }
  useEffect(() => { load('') }, [])

  if (busy) return <Busy />

  const healthy = rows.filter((r) => r.health === 'ok').length
  const unmapped = rows.filter((r) => r.health === 'unmapped').length
  const failing = rows.length - healthy
  const bookings = rows.reduce((t, r) => t + r.bookings_received, 0)
  const providers = [...new Set(rows.map((r) => r.provider))]

  return (
    <Page
      eyebrow="Integrations"
      crumbs={[{ label: 'Overview', to: '/platform' },
        { label: 'Channel health' }]}
      title="Channel health"
      subtitle="Monitor connectivity and failed synchronisation across properties.">
      <ErrorNote>{err}</ErrorNote>

      <Metrics items={[
        { label: 'Connected properties', value: rows.length,
          caption: providers.join(', ') || 'No provider configured' },
        { label: 'Healthy', value: healthy, tone: 'good',
          caption: 'Provisioned, pushing, mapped' },
        { label: 'Needs attention', value: failing,
          tone: failing ? 'warn' : 'default',
          caption: unmapped ? `${unmapped} with nothing mapped` : 'Provisioning or push' },
        { label: 'Bookings delivered', value: bookings,
          caption: 'Received from the channel' },
      ]} />

      <FilterBar onApply={() => load()} applying={applying}>
        <Select id="ch-provider" label="Provider" value={provider}
          onChange={setProvider} options={[
            { value: '', label: 'All providers' },
            ...providers.map((p) => ({ value: p, label: p })),
          ]} />
      </FilterBar>

      <SplitLayout
        main={
          <DataTable
            title="Property connections"
            count={rows.length}
            head={['Property', 'Tenant', 'Provider', 'Health', 'Rooms',
              'Rates', 'Bookings', 'Last push']}
            footnote={'Showing ' + rows.length + ' of ' + rows.length + ' records'}
            empty="No property is connected to a channel manager.">
            {rows.map((r) => (
              <tr key={r.id} className="hover:bg-pf-bg">
                <Td>
                  <Link to={'/platform/properties/' + r.property_id}
                    className="text-pf-navy hover:text-pf-deep hover:underline">
                    <span className="font-mono tracking-widest text-pf-muted">
                      {r.property_code}
                    </span>{' '}
                    {r.property_name}
                  </Link>
                </Td>
                <Td>
                  <Link to={'/platform/tenants/' + r.organization_id}
                    className="text-pf-muted hover:text-pf-deep hover:underline">
                    {r.tenant_name}
                  </Link>
                </Td>
                <Td className="text-pf-muted">{r.provider}</Td>
                <Td><Pill value={r.health} /></Td>
                <Td className={r.room_mappings === 0
                  ? 'text-pf-warn-text' : ''}>{r.room_mappings}</Td>
                <Td>{r.rate_mappings}</Td>
                <Td>{r.bookings_received}</Td>
                <Td className="text-pf-muted">
                  {r.last_pushed_at
                    ? new Date(r.last_pushed_at).toLocaleDateString('en-IN',
                      { day: '2-digit', month: 'short' })
                    : 'never'}
                </Td>
              </tr>
            ))}
          </DataTable>
        }
        side={
          <>
            <Panel
              title="What is failing"
              empty="Every connection is provisioned, pushing and mapped."
              rows={rows.filter((r) => r.health !== 'ok').map((r) => ({
                label: r.property_name,
                value: r.health === 'provisioning'
                  ? (r.last_provision_detail || r.last_provision_status || 'provisioning')
                  : r.health === 'push failed'
                    ? (r.last_push_detail || 'push failed')
                    : 'nothing mapped',
                tone: 'warn' as const,
              }))}
            />
            <Panel
              title="Scope"
              rows={[
                { label: 'Shown', value: 'Connection state' },
                { label: 'Not shown', value: 'Provider credentials' },
                { label: 'Authorisation', value: 'Per property' },
                { label: 'Retries', value: 'From the tenant PMS' },
              ]}
            />
          </>
        }
      />

      <Note>
        A platform connection to a channel manager does not authorise every
        tenant's OTA account — each property authorises its own and maps its
        own rooms and rates. Nothing here reads the credentials that
        authenticate those connections.
      </Note>
    </Page>
  )
}
