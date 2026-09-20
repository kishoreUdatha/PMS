import { useEffect, useState } from 'react'
import { analytics, type AnalyticsPage } from '../opsApi'
import { money } from '../billingApi'
import {
  Busy, Card, DataTable, ErrorNote, Metrics, Note, Page, Panel, Td, errorText,
} from '../ui'

/** Screen 27 — revenue mix, cohorts, adoption.
 *
 *  Every figure comes from a table that already holds real rows, and where
 *  there is not enough history the screen says so instead of drawing a line.
 *  A churn rate computed over four tenants and three weeks is a number with no
 *  information in it, and a chart makes it look like one with plenty — so the
 *  churn card carries the reason it is blank rather than a confident 0.0%.
 *
 *  The bars are divs against a shared maximum, not a charting library: four
 *  plans and a handful of months do not need one, and the scale is easier to
 *  keep honest when it is one number in one place.
 */

function Bars({ items }: {
  items: { label: string; value: number; caption: string }[]
}) {
  const max = Math.max(1, ...items.map((i) => i.value))
  return (
    <div className="space-y-3">
      {items.map((i) => (
        <div key={i.label}>
          <div className="flex items-baseline justify-between gap-3">
            <span className="text-pf-td text-pf-navy">{i.label}</span>
            <span className="text-pf-help tabular-nums text-pf-muted">
              {i.caption}
            </span>
          </div>
          <div className="mt-1 h-2 w-full overflow-hidden rounded-full bg-pf-bg">
            <div className="h-full rounded-full bg-pf-teal"
              style={{ width: `${Math.round((100 * i.value) / max)}%` }} />
          </div>
        </div>
      ))}
    </div>
  )
}

export default function Analytics() {
  const [d, setD] = useState<AnalyticsPage | null>(null)
  const [busy, setBusy] = useState(true)
  const [err, setErr] = useState('')

  useEffect(() => {
    analytics().then(setD)
      .catch((e) => setErr(errorText(e, 'Could not load analytics.')))
      .finally(() => setBusy(false))
  }, [])

  if (busy) return <Busy />
  if (!d) {
    return (
      <Page title="Platform analytics" eyebrow="Analytics"
        crumbs={[{ label: 'Overview', to: '/platform' },
          { label: 'Analytics' }]}>
        <ErrorNote>{err}</ErrorNote>
      </Page>
    )
  }

  const t = d.totals
  const committed = Number(t.committed_mrr || 0)
  const trialing = Number(t.trialing_mrr || 0)
  const collected = d.invoices.reduce(
    (s, m) => s + Number(m.collected || 0), 0)
  const billed = d.invoices.reduce((s, m) => s + Number(m.billed || 0), 0)
  const uncollected = billed - collected

  return (
    <Page
      eyebrow="Analytics"
      crumbs={[{ label: 'Overview', to: '/platform' },
        { label: 'Platform analytics' }]}
      title="Platform analytics"
      subtitle="Revenue mix, signup cohorts and module adoption, from live tables.">
      <ErrorNote>{err}</ErrorNote>

      <Metrics items={[
        { label: 'Committed MRR', value: money(String(committed)),
          caption: 'Active, past due and in grace' },
        { label: 'In trial', value: money(String(trialing)),
          tone: trialing ? 'warn' : 'default',
          caption: trialing
            ? 'Not yet revenue' : 'Nobody is trialing' },
        { label: 'Tenants', value: t.tenants,
          caption: `${t.properties} active properties`
            + (t.suspended ? ` · ${t.suspended} suspended` : '') },
        { label: 'Uncollected', value: money(String(uncollected)),
          tone: uncollected > 0 ? 'warn' : 'good',
          caption: uncollected > 0
            ? 'Invoiced and not yet paid' : 'Everything invoiced is paid' },
      ]} />

      <div className="grid gap-4 lg:grid-cols-2">
        <Card className="p-5">
          <h2 className="text-pf-card text-pf-navy">Revenue by plan</h2>
          <p className="mt-0.5 text-pf-help text-pf-muted">
            Monthly recurring, per tenant.
          </p>
          <div className="mt-4">
            {d.mix.length === 0 ? (
              <p className="text-pf-help text-pf-muted">
                No tenant is on a plan.
              </p>
            ) : (
              <Bars items={d.mix.map((m) => ({
                label: m.plan_name,
                value: Number(m.mrr || 0),
                caption: `${money(m.mrr)} · ${m.tenants} tenant`
                  + `${m.tenants === 1 ? '' : 's'}`,
              }))} />
            )}
          </div>
        </Card>

        <Card className="p-5">
          <h2 className="text-pf-card text-pf-navy">Module adoption</h2>
          <p className="mt-0.5 text-pf-help text-pf-muted">
            Share of properties with each module switched on.
          </p>
          <div className="mt-4">
            {d.adoption.length === 0 ? (
              <p className="text-pf-help text-pf-muted">
                No module entitlements recorded.
              </p>
            ) : (
              <Bars items={d.adoption.slice(0, 8).map((a) => ({
                label: a.module_code.replace(/_/g, ' '),
                value: a.enabled_on,
                caption: `${a.enabled_on}/${a.available_on}`
                  + (a.percent !== null ? ` · ${a.percent}%` : ''),
              }))} />
            )}
          </div>
        </Card>
      </div>

      <div className="mt-5">
        <DataTable
          title="Signup cohorts"
          count={d.cohorts.length}
          head={['Cohort', 'Signed up', 'Still active', 'On a plan',
            'Conversion']}
          footnote="A cohort is the month a tenant's account was created."
          empty="No tenant has been created.">
          {d.cohorts.map((c) => (
            <tr key={c.cohort} className="hover:bg-pf-bg">
              <Td className="text-pf-navy">{c.cohort}</Td>
              <Td className="tabular-nums text-pf-muted">{c.signed_up}</Td>
              <Td className="tabular-nums text-pf-muted">{c.still_active}</Td>
              <Td className="tabular-nums text-pf-muted">{c.paying}</Td>
              <Td className="tabular-nums text-pf-muted">
                {c.signed_up
                  ? `${Math.round((100 * c.paying) / c.signed_up)}%` : '—'}
              </Td>
            </tr>
          ))}
        </DataTable>
      </div>

      <div className="mt-5">
        <DataTable
          title="Billed and collected"
          count={d.invoices.length}
          head={['Month', 'Invoices', 'Billed', 'Collected', 'Outstanding']}
          footnote="Subscription invoices raised against tenants, not guest folios."
          empty="No subscription invoice has been raised yet.">
          {d.invoices.map((m) => {
            const out = Number(m.billed || 0) - Number(m.collected || 0)
            return (
              <tr key={m.month} className="hover:bg-pf-bg">
                <Td className="text-pf-navy">{m.month}</Td>
                <Td className="tabular-nums text-pf-muted">{m.invoices}</Td>
                <Td className="tabular-nums text-pf-muted">
                  {money(m.billed || '0')}
                </Td>
                <Td className="tabular-nums text-pf-muted">
                  {money(m.collected || '0')}
                </Td>
                <Td className={`tabular-nums ${
                  out > 0 ? 'text-pf-warn-text' : 'text-pf-muted'}`}>
                  {money(String(out))}
                </Td>
              </tr>
            )
          })}
        </DataTable>
      </div>

      <div className="mt-5">
        <Panel
          title="Churn"
          rows={d.churn.measurable ? [
            { label: 'Subscriptions ended', value: d.churn.cancelled },
            { label: 'Months of history',
              value: d.churn.months_of_history },
          ] : [
            { label: 'Subscriptions ended', value: d.churn.cancelled },
            { label: 'Months of history', value: d.churn.months_of_history },
            { label: 'Rate', value: 'not yet measurable',
              tone: 'warn' as const },
          ]}
        />
      </div>

      <Note>
        {d.churn.note} Trial revenue is shown apart from committed revenue
        because counting the two together makes a pipeline look like a
        business — a trial that does not convert was never money.
      </Note>
    </Page>
  )
}
