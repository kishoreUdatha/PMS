import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { ArrowUpRight } from 'lucide-react'
import {
  health as fetchHealth, listOrganizations, businessDates, onboardingProgress,
  type Health, type Organization, type BusinessDateRow, type OnboardingRow,
} from '../api'
import { listInvoices, money, type Invoice } from '../billingApi'
import {
  Busy, Button, DataTable, ErrorNote, Metrics, Note, Page, Panel, Pill,
  SplitLayout, Td, errorText,
} from '../ui'

/** Screen 03 — the platform overview.
 *
 *  Laid out to the artboard: four KPIs, then the tenant portfolio beside an
 *  Action queue and a Service snapshot.
 *
 *  The action queue is the part worth getting right. In the pack it lists
 *  three sample exceptions; here it is built from what is actually wrong —
 *  unpaid invoices, properties stuck in setup, and business dates that have
 *  drifted ahead of their own calendar. An empty queue means nothing needs
 *  attention, and says so, rather than inventing rows to fill the panel.
 */
export default function Overview() {
  const navigate = useNavigate()
  const [h, setH] = useState<Health | null>(null)
  const [orgs, setOrgs] = useState<Organization[]>([])
  const [drift, setDrift] = useState<BusinessDateRow[]>([])
  const [onboarding, setOnboarding] = useState<OnboardingRow[]>([])
  const [invoices, setInvoices] = useState<Invoice[]>([])
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(true)

  useEffect(() => {
    Promise.all([
      fetchHealth(), listOrganizations(), businessDates(),
      onboardingProgress(), listInvoices({ status: 'issued' }),
    ])
      .then(([a, b, c, d, e]) => {
        setH(a); setOrgs(b); setDrift(c); setOnboarding(d); setInvoices(e)
      })
      .catch((e) => setErr(errorText(e, 'Could not load the overview.')))
      .finally(() => setBusy(false))
  }, [])

  if (busy) return <Busy />

  const mrr = orgs.reduce((t, o) => t + Number(o.mrr ?? 0), 0)
  const rooms = orgs.reduce((t, o) => t + o.rooms, 0)
  const byState = (s: string) => orgs.filter((o) => o.lifecycle === s).length
  const drifted = drift.filter((d) => (d.days_ahead ?? 0) > 0)
  const stuck = onboarding.filter(
    (r) => !r.live && r.required_outstanding.length > 0)
  const overdue = invoices.filter((i) => Number(i.outstanding) > 0)
  const attention = overdue.length + stuck.length + drifted.length

  const heads = Object.entries(h?.migration_heads ?? {})
  const schemasMissing = heads.filter(([, v]) => !v).length

  return (
    <Page
      eyebrow="Overview"
      crumbs={[{ label: 'Overview', to: '/platform' }]}
      title="Platform overview"
      subtitle="Monitor tenant activity, recurring revenue and platform exceptions."
      actions={
        <Button tone="primary" className="px-5 py-2.5"
          onClick={() => navigate('/platform/tenants')}>
          View tenants
        </Button>
      }>
      <ErrorNote>{err}</ErrorNote>

      <Metrics items={[
        {
          label: 'Tenants',
          value: orgs.length,
          caption: `${byState('active')} active · ${byState('trialing')} trial`
        },
        {
          label: 'Properties',
          value: h?.totals.properties ?? 0,
          caption: `${rooms} configured room${rooms === 1 ? '' : 's'}`,
        },
        {
          label: 'Contracted MRR',
          value: money(mrr),
          caption: overdue.length
            ? 'Includes past-due subscriptions'
            : 'All subscriptions current',
        },
        {
          label: 'Needs attention',
          value: attention,
          tone: attention ? 'warn' : 'good',
          caption: 'Billing, setup and dates',
        },
      ]} />

      <SplitLayout
        main={
          <DataTable
            title="Tenant portfolio"
            count={orgs.length}
            head={['Tenant', 'Properties', 'Plan', 'Status', 'Monthly recurring', '']}
            footnote={`Showing ${orgs.length} of ${orgs.length} records`}
            action={
              <Link to="/platform/tenants" className="text-pf-deep hover:underline">
                View record details
              </Link>
            }>
            {orgs.map((o) => (
              <tr key={o.id} className="hover:bg-pf-bg">
                <Td className="text-pf-navy">{o.name}</Td>
                <Td>{o.properties}</Td>
                <Td>{o.plan_name || (
                  <span className="text-pf-warn-text">no plan</span>)}</Td>
                <Td><Pill value={o.lifecycle} /></Td>
                <Td className="tabular-nums">{money(o.mrr)}</Td>
                <Td className="text-right">
                  <button onClick={() => navigate(`/platform/tenants/${o.id}`)}
                    title={`Open ${o.name}`}
                    className="rounded p-1 text-pf-placeholder hover:bg-pf-soft hover:text-pf-deep">
                    <ArrowUpRight size={15} />
                  </button>
                </Td>
              </tr>
            ))}
          </DataTable>
        }
        side={<>
          <Panel
            title="Action queue"
            empty="Nothing needs attention."
            rows={[
              ...(overdue.length ? [{
                label: 'Billing',
                value: `${overdue[0].organization_name} · ${money(overdue[0].outstanding)} overdue`
                  + (overdue.length > 1 ? ` (+${overdue.length - 1} more)` : ''),
                tone: 'warn' as const,
              }] : []),
              ...(stuck.length ? [{
                label: 'Onboarding',
                value: `${stuck[0].name} · ${stuck[0].visited_count} of ${stuck[0].total_steps} steps`
                  + (stuck.length > 1 ? ` (+${stuck.length - 1} more)` : ''),
                tone: 'warn' as const,
              }] : []),
              ...(drifted.length ? [{
                label: 'Business date',
                value: `${drifted[0].name} · ${drifted[0].days_ahead} day(s) ahead`
                  + (drifted.length > 1 ? ` (+${drifted.length - 1} more)` : ''),
                tone: 'warn' as const,
              }] : []),
            ]}
          />
          <Panel
            title="Service snapshot"
            rows={[
              {
                label: 'Database schemas',
                value: schemasMissing
                  ? `${schemasMissing} not migrated`
                  : `${heads.length} migrated`,
                tone: schemasMissing ? 'warn' : 'good',
              },
              {
                label: 'Live sessions',
                value: h?.totals.live_sessions ?? 0,
              },
              {
                label: 'Platform operators',
                value: h?.totals.platform_admins ?? 0,
              },
              {
                label: 'Last refreshed',
                value: new Date().toLocaleString('en-IN', {
                  day: '2-digit', month: 'short', year: 'numeric',
                  hour: '2-digit', minute: '2-digit',
                }),
              },
            ]}
          />
        </>}
      />

      <Note>
        Recurring revenue measures subscriptions to your software. Hotel room
        revenue belongs in each tenant's own PMS and never appears here.
      </Note>
    </Page>
  )
}
