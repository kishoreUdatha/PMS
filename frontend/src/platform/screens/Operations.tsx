import { useEffect, useState } from 'react'
import { systemOperations, type Operations as Ops } from '../api'
import {
  Busy, DataTable, ErrorNote, Metrics, Note, Page, Panel, Pill, SplitLayout,
  Td, errorText,
} from '../ui'

/** Screen 24 — queues, scheduled jobs and webhook reconciliation.
 *
 *  Everything here fails quietly. An outbox nobody drains just accumulates; a
 *  night audit that fails leaves a property's day unclosed in a step row
 *  nobody reads; a payment webhook that matches nothing is money the provider
 *  believes it took and this system has not recorded.
 *
 *  So the numbers are paired with *how long* — the useful question is not how
 *  many events are pending but since when, because that is the difference
 *  between a slow minute and a consumer that died on Tuesday.
 */

function ago(iso: string | null): string {
  if (!iso) return 'never'
  const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86400000)
  if (days > 0) return `${days} day${days === 1 ? '' : 's'} ago`
  const hours = Math.floor((Date.now() - new Date(iso).getTime()) / 3600000)
  if (hours > 0) return `${hours} hour${hours === 1 ? '' : 's'} ago`
  return 'just now'
}

export default function Operations() {
  const [d, setD] = useState<Ops | null>(null)
  const [busy, setBusy] = useState(true)
  const [err, setErr] = useState('')

  useEffect(() => {
    systemOperations().then(setD)
      .catch((e) => setErr(errorText(e, 'Could not load system operations.')))
      .finally(() => setBusy(false))
  }, [])

  if (busy) return <Busy />
  if (!d) {
    return (
      <Page title="System operations" eyebrow="System operations"
        crumbs={[{ label: 'Overview', to: '/platform' },
          { label: 'System operations' }]}>
        <ErrorNote>{err}</ErrorNote>
      </Page>
    )
  }

  // Two different problems, deliberately counted apart. `unmatched` is money
  // the gateway captured against an order we have no record of — somebody has
  // to look. `malformed` is a payload we could not read, which is a
  // configuration fault and not money at risk.
  const unmatched = d.webhooks
    .filter((w) => w.outcome === 'unmatched')
    .reduce((t, w) => t + w.events, 0)
  const malformed = d.webhooks
    .filter((w) => w.outcome === 'malformed')
    .reduce((t, w) => t + w.events, 0)
  // Counting failed *runs* was wrong and alarming: a run that was
  // deliberately reversed and re-run leaves the day perfectly closed. But
  // counting only *attempted* days was worse, and quietly so — a day the
  // scheduler never swept has no run row at all, so three properties sat a
  // day behind while this card read zero. It now counts days that are open,
  // however they got that way.
  const unclosed = d.unclosed_days.length
  // A day nobody has touched means the scheduler is not running. A day that
  // failed three times means the audit itself is stuck. Same card, very
  // different morning.
  const untouched = d.unclosed_days.filter((u) => u.attempts === 0).length
  const stale = d.unclosed_days.reduce(
    (worst, u) => Math.max(worst, u.days_open), 0)
  const stuck = d.outbox.pending > 0 && !!d.outbox.oldest_pending

  return (
    <Page
      eyebrow="System operations"
      crumbs={[{ label: 'Overview', to: '/platform' },
        { label: 'System health & jobs' }]}
      title="System health &amp; jobs"
      subtitle="Monitor queues, scheduled jobs and events needing intervention.">
      <ErrorNote>{err}</ErrorNote>

      <Metrics items={[
        { label: 'Outbox pending', value: d.outbox.pending,
          tone: stuck ? 'warn' : 'good',
          caption: stuck
            ? `oldest ${ago(d.outbox.oldest_pending)}`
            : 'Queue is draining' },
        { label: 'Unclosed days', value: unclosed,
          tone: unclosed ? 'warn' : 'good',
          caption: !unclosed ? 'Every finished day is closed'
            : untouched === unclosed
              ? `Never attempted — oldest ${stale} day(s) open`
              : `${untouched} never attempted, oldest ${stale} day(s) open` },
        { label: 'Unmatched payments', value: unmatched,
          tone: unmatched ? 'warn' : 'good',
          caption: unmatched
            ? 'Captured against an order we have no record of'
            : 'Every capture reconciled' },
        { label: 'Malformed events', value: malformed,
          caption: malformed
            ? 'A sender is misconfigured'
            : 'All payloads readable' },
      ]} />

      <SplitLayout
        main={
          <>
            <DataTable
              title="Outbox backlog"
              count={d.outbox_by_type.length}
              head={['Event type', 'Pending', 'Oldest']}
              footnote="Events waiting for a publisher to drain them."
              empty="Nothing is waiting in the outbox.">
              {d.outbox_by_type.map((t) => (
                <tr key={t.event_type} className="hover:bg-pf-bg">
                  <Td className="text-pf-navy">{t.event_type}</Td>
                  <Td className="text-pf-warn-text">{t.pending}</Td>
                  <Td className="text-pf-muted">{ago(t.oldest)}</Td>
                </tr>
              ))}
            </DataTable>

            <div className="mt-5">
              <DataTable
                title="Webhook reconciliation"
                count={d.webhooks.length}
                head={['Provider', 'Event', 'Outcome', 'Events', 'Last received']}
                footnote="A payment event that matches nothing is money this system has not recorded."
                empty="No provider events received.">
                {d.webhooks.map((w, i) => (
                  <tr key={`${w.provider}-${w.event_type}-${w.outcome}-${i}`}
                    className="hover:bg-pf-bg">
                    <Td className="text-pf-navy">{w.provider}</Td>
                    <Td className="text-pf-muted">{w.event_type}</Td>
                    <Td><Pill value={w.outcome} /></Td>
                    <Td className={w.outcome === 'unmatched'
                      ? 'text-pf-warn-text' : ''}>{w.events}</Td>
                    <Td className="text-pf-muted">{ago(w.last_received)}</Td>
                  </tr>
                ))}
              </DataTable>
            </div>
          </>
        }
        side={
          <>
            <Panel
              title="Unclosed days"
              empty="Every finished business day is closed."
              rows={d.unclosed_days.map((u) => ({
                label: `${u.property_name} · ${u.business_date}`,
                // The distinction that names the fault: nobody tried, versus
                // somebody tried and it would not close.
                value: u.attempts === 0
                  ? `never attempted · ${u.days_open} day(s) open`
                  : `${u.attempts} attempt${u.attempts === 1 ? '' : 's'}, `
                    + `none completed · ${u.days_open} day(s) open`,
                tone: 'warn' as const,
              }))}
            />
            <Panel
              title="Night audit runs"
              rows={d.night_audit.map((a) => ({
                label: a.status,
                value: `${a.runs} run${a.runs === 1 ? '' : 's'}`,
                tone: a.status === 'completed' ? ('good' as const) : undefined,
              }))}
              empty="The night audit has never run."
            />
            <Panel
              title="Reversed on purpose"
              empty="No run has been reversed."
              rows={d.reversed_runs.map((r) => ({
                label: `${r.property_name} · ${r.business_date}`,
                value: r.reason ? r.reason.slice(0, 80) : r.step_code,
              }))}
            />
            <Panel
              title="Not on this screen"
              rows={[
                { label: 'Backups', value: 'No inventory yet' },
                { label: 'Restore', value: 'No workflow yet' },
                { label: 'Manual replay', value: 'Not exposed' },
              ]}
            />
          </>
        }
      />

      <Note>
        A day counted here is open in <code>business_days</code>, which is the
        record that decides whether a night audit still owes work — not the
        list of runs that were attempted. A day the scheduler never swept has
        no run to show and used to be invisible on this screen.{' '}
        Retries and replay are deliberately not offered here. Re-publishing an
        outbox event or re-applying a webhook has to be idempotent and scoped
        before a button can exist for it, and neither has been proven.
      </Note>
    </Page>
  )
}
