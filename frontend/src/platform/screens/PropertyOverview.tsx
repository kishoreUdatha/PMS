import { useEffect, useState } from 'react'
import { Link, useParams, useNavigate } from 'react-router-dom'
import { ArrowUpRight } from 'lucide-react'
import { propertyOverview, type PropertyOverview as P } from '../api'
import {
  Busy, Button, DataTable, ErrorNote, Metrics, Note, Page, Panel, Pill, Td,
  errorText,
} from '../ui'

/** Screen 09 — one property's configuration and service health.
 *
 *  Configuration and readiness only. What this property's guests are called,
 *  what they were charged and what they owe are not here and have no endpoint
 *  behind this page: day-to-day operations stay in the property's own portal.
 *
 *  The readiness checks are derived from real state, never asserted. A check
 *  that cannot be answered says so rather than showing a reassuring tick —
 *  a green row nobody computed is worse than an absent one.
 */
export default function PropertyOverview() {
  const { propertyId = '' } = useParams()
  const navigate = useNavigate()
  const [p, setP] = useState<P | null>(null)
  const [busy, setBusy] = useState(true)
  const [err, setErr] = useState('')

  useEffect(() => {
    setBusy(true)
    propertyOverview(propertyId)
      .then(setP)
      .catch((e) => setErr(errorText(e, 'Could not load this property.')))
      .finally(() => setBusy(false))
  }, [propertyId])

  if (busy) return <Busy />
  if (!p) {
    return (
      <Page title="Property" eyebrow="Properties"
        crumbs={[{ label: 'Overview', to: '/platform' },
          { label: 'Properties', to: '/platform/properties' }]}>
        <ErrorNote>{err}</ErrorNote>
      </Page>
    )
  }

  const sv = p.services
  const live = !!p.onboarding?.activated_at
  const state = p.status !== 'active' ? p.status : live ? 'Live' : 'Setup'
  const drift = p.business_date?.days_ahead ?? 0
  const modules = p.modules ?? []
  const bookable = modules.some(
    (m) => m.module_code === 'booking_engine' && m.enabled)
  const entitled = (p.entitlements ?? []).some(
    (e) => e.code === 'booking_engine')

  // Each check is computed from something real, and each carries the scope it
  // was computed at — a mapping is a property fact, billing is a tenant one.
  const checks = [
    {
      check: 'Room and rate mapping',
      result: !sv ? 'Not connected'
        : sv.room_mappings > 0 && sv.rate_mappings > 0 ? 'Complete'
          : 'Incomplete',
      scope: p.code,
      to: '/platform/channels',
    },
    {
      check: 'Channel provisioning',
      result: !sv ? 'Not connected'
        : sv.last_provision_status === 'ok' ? 'Healthy'
          : (sv.last_provision_status || 'Unknown'),
      scope: p.code,
      to: '/platform/channels',
    },
    {
      check: 'Booking delivery',
      result: !sv ? 'Not connected'
        : sv.bookings_received > 0 ? 'Healthy' : 'Nothing received',
      scope: p.code,
      to: '/platform/channels',
    },
    {
      check: 'Business date',
      result: !p.business_date ? 'Never audited'
        : drift > 0 ? `${drift} day(s) ahead` : 'Aligned',
      scope: p.code,
      to: '/platform/business-dates',
    },
    {
      // The check that would have answered "why is this hotel not bookable".
      // Nothing grants it automatically: not tenant creation, not the
      // onboarding wizard, not the plan. A property that has finished setup
      // is still invisible to guests until somebody switches this on.
      check: 'Booking engine',
      result: bookable ? 'On sale'
        : modules.some((m) => m.module_code === 'booking_engine')
          ? 'Switched off' : 'Not switched on',
      scope: p.code,
      to: '/platform/booking-engine',
    },
    {
      // Deliberately separate from the line above. One is permission, the
      // other is readiness, and they are different systems -- billing writes
      // entitlements, the property switches modules, and nothing bridges
      // them. Showing only one would imply the other.
      check: 'Booking engine in plan',
      // Three readings, not two. "On sale without one" is the case worth
      // naming: nothing takes a hotel off sale when its entitlement lapses,
      // by design -- a downgrade that quietly closed the shop with guests
      // mid-booking would be far worse than a line on this screen. So the
      // screen carries it and a person decides.
      result: entitled ? 'Granted by plan'
        : bookable ? 'On sale without one' : 'Not in plan',
      scope: p.organization_name,
      to: '/platform/subscriptions',
    },
    {
      check: 'Tenant billing access',
      result: p.billing ? `${p.billing.plan_name} · ${p.billing.status}`
        : 'No subscription',
      scope: p.organization_name,
      to: '/platform/subscriptions',
    },
  ]

  const good = (r: string) =>
    ['Complete', 'Healthy', 'Aligned', 'On sale', 'Granted by plan']
      .includes(r) || r.includes('active')
  const bad = (r: string) =>
    ['Incomplete', 'No subscription', 'Never audited', 'Nothing received',
      'Switched off', 'Not switched on', 'On sale without one']
      .includes(r) || r.includes('ahead') || r === 'partial'

  return (
    <Page
      eyebrow="Properties"
      crumbs={[{ label: 'Overview', to: '/platform' },
        { label: 'Properties', to: '/platform/properties' },
        { label: p.name }]}
      title="Property overview"
      subtitle={`${p.name} · ${p.code}`}
      actions={
        <Button tone="primary" className="px-5 py-2.5"
          onClick={() => navigate('/platform/channels')}>
          View channel health
        </Button>
      }>
      <ErrorNote>{err}</ErrorNote>

      <Metrics items={[
        { label: 'Tenant', value: p.organization_code || '—',
          caption: p.organization_name },
        { label: 'Rooms', value: p.capacity.rooms,
          caption: `${p.capacity.room_types} room type(s)` },
        { label: 'Channels',
          value: sv ? `${sv.ota_connections} connected` : 'None',
          caption: sv ? 'Per-property authorisation' : 'No channel manager link' },
        { label: 'Property state', value: state,
          tone: state === 'Live' ? 'good' : 'warn',
          caption: live
            ? `Live since ${new Date(p.onboarding!.activated_at!).toLocaleDateString()}`
            : `Setup step: ${p.onboarding?.current_step || 'not started'}` },
      ]} />

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel
          title="Property configuration"
          rows={[
            { label: 'Property', value: p.name },
            { label: 'Tenant', value: p.organization_name },
            { label: 'Location',
              value: [p.city, p.state, p.country].filter(Boolean).join(', ')
                || 'Not set' },
            { label: 'Timezone', value: p.timezone },
            { label: 'Currency', value: p.currency },
            { label: 'Check-in / out',
              value: p.checkin_time
                ? `${p.checkin_time} / ${p.checkout_time}` : 'Not set' },
          ]}
        />
        <Panel
          title="Connected services"
          rows={[
            { label: 'Channel provider',
              value: sv?.channel_provider || 'Not connected' },
            { label: 'OTA connections',
              value: sv ? sv.ota_connections : 0 },
            { label: 'Room / rate mappings',
              value: sv ? `${sv.room_mappings} / ${sv.rate_mappings}` : '—' },
            { label: 'Booking engine',
              value: bookable
                ? `On sale · /book/${p.code}`
                : 'Switched off — the link answers as if the property does '
                  + 'not exist',
              tone: bookable ? ('good' as const) : ('warn' as const) },
            { label: 'Custom domain', value: 'Not configured' },
            { label: 'Modules enabled',
              value: `${p.modules.filter((m) => m.enabled).length} of ${p.modules.length}` },
          ]}
        />
      </div>

      <div className="mt-5">
        <DataTable
          title="Readiness checks"
          count={checks.length}
          head={['Check', 'Result', 'Scope', '']}
          footnote={`Showing ${checks.length} of ${checks.length} records`}>
          {checks.map((c) => (
            <tr key={c.check} className="hover:bg-pf-bg">
              <Td className="text-pf-navy">{c.check}</Td>
              <Td>
                {good(c.result) ? <Pill value={c.result} />
                  : bad(c.result)
                    ? <span className="text-pf-warn-text">{c.result}</span>
                    : <span className="text-pf-muted">{c.result}</span>}
              </Td>
              <Td className="text-pf-muted">{c.scope}</Td>
              <Td className="text-right">
                <Link to={c.to} title={`Open ${c.check}`}
                  className="inline-flex rounded p-1 text-pf-placeholder hover:bg-pf-soft hover:text-pf-deep">
                  <ArrowUpRight size={15} />
                </Link>
              </Td>
            </tr>
          ))}
        </DataTable>
      </div>

      <div className="mt-5">
        <DataTable
          title="Recent activity"
          count={p.recent_activity.length}
          head={['When', 'Actor', 'Action', 'Reason']}
          empty="Nothing recorded against this property yet.">
          {p.recent_activity.map((a, i) => (
            <tr key={`${a.occurred_at}-${i}`} className="hover:bg-pf-bg">
              <Td className="whitespace-nowrap text-pf-muted">
                {new Date(a.occurred_at).toLocaleString('en-IN',
                  { day: '2-digit', month: 'short', hour: '2-digit',
                    minute: '2-digit' })}
              </Td>
              <Td className="text-pf-muted">{a.actor_subject || '—'}</Td>
              <Td className="text-pf-navy">{a.action}</Td>
              <Td className="text-pf-muted">{a.reason || '—'}</Td>
            </tr>
          ))}
        </DataTable>
      </div>

      <Note>
        The platform view shows configuration and service health. Day-to-day
        room operations — arrivals, folios, housekeeping — stay in the
        property's own portal and have no route behind this page.
      </Note>
    </Page>
  )
}
