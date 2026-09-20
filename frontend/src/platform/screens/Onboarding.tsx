import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { AlertTriangle } from 'lucide-react'
import { Check, Minus } from 'lucide-react'
import { onboardingProgress, type OnboardingRow } from '../api'
import {
  Busy, Card, DataTable, ErrorNote, Metrics, Note, Page, Td, errorText,
} from '../ui'

/** Screen 07 — who is still setting up, and what is stopping them.
 *
 *  Ordered with the most neglected first, because the useful question is not
 *  "who is furthest along" but "who is about to churn before they ever went
 *  live". A property nobody has touched for a fortnight with a trial running
 *  out is the row worth acting on.
 *
 *  The wizard records that a step was *visited*, which is not the same as
 *  finished — somebody can open a page and leave it empty. This screen says
 *  "visited" rather than "complete" for exactly that reason.
 */
export default function Onboarding() {
  const [rows, setRows] = useState<OnboardingRow[]>([])
  const [busy, setBusy] = useState(true)
  const [err, setErr] = useState('')

  useEffect(() => {
    onboardingProgress().then(setRows)
      .catch((e) => setErr(errorText(e, 'Could not load onboarding.')))
      .finally(() => setBusy(false))
  }, [])

  if (busy) return <Busy />

  const live = rows.filter((r) => r.live).length
  const untouched = rows.filter((r) => r.visited_count === 0).length
  const stalled = rows.filter(
    (r) => !r.live && r.visited_count > 0 && r.required_outstanding.length > 0)
  // The gap this screen could not show. Onboarding reaches go-live with every
  // required step ticked while the property is still invisible to guests,
  // because nothing in the wizard, in tenant creation or in billing switches
  // the booking engine on. "Live" and "bookable" were silently different
  // things and only one of them was on the screen.
  const finishedNotSelling = rows.filter(
    (r) => r.live && r.booking_engine !== 'enabled')

  return (
    <Page
      eyebrow="Tenants"
      crumbs={[{ label: 'Overview', to: '/platform' },
        { label: 'Onboarding progress' }]}
      title="Onboarding progress"
      subtitle="Properties still being set up, least recently touched first.">
      <ErrorNote>{err}</ErrorNote>

      {/* Four, not five. A "Properties" count said the same thing as the
          "4 records" on the table immediately below it, and spending a fifth
          of the row on it made every card narrower than its caption wanted.
          Every label and caption here fits one line, so no single card can
          size the row for the rest. */}
      <Metrics items={[
        { label: 'Live', value: live, tone: 'good',
          caption: 'Finished onboarding' },
        { label: 'In progress', value: stalled.length,
          tone: stalled.length ? 'warn' : 'default',
          caption: 'Started, not finished' },
        { label: 'Never started', value: untouched,
          tone: untouched ? 'warn' : 'default',
          caption: 'No step visited yet' },
        { label: 'Not bookable', value: finishedNotSelling.length,
          tone: finishedNotSelling.length ? 'warn' : 'good',
          caption: finishedNotSelling.length
            ? 'Live, booking engine off'
            : 'Every live one on sale' },
      ]} />

      <div className="mb-5">
        <DataTable
          title="Onboarding queue"
          count={rows.length}
          head={['Property', 'Tenant', 'Step', 'Visited', 'Rooms', 'State',
            'Bookable', 'Last activity']}
          footnote={'Showing ' + rows.length + ' of ' + rows.length + ' records'}
          empty="Every property has finished setup.">
          {rows.map((r) => (
            <tr key={r.property_id} className="hover:bg-pf-bg">
              <Td className="text-pf-navy">
                <Link to={'/platform/properties/' + r.property_id}
                  className="hover:text-pf-deep hover:underline">
                  <span className="font-mono tracking-widest text-pf-muted">
                    {r.code}
                  </span>{' '}{r.name}
                </Link>
              </Td>
              <Td className="text-pf-muted">{r.organization_name}</Td>
              <Td className="text-pf-muted">{r.current_step || 'not started'}</Td>
              <Td className={r.visited_count === 0 ? 'text-pf-warn-text' : ''}>
                {r.visited_count} / {r.total_steps}
              </Td>
              <Td>{r.rooms}</Td>
              <Td>
                {r.live
                  ? <span className="text-pf-ok-text">live</span>
                  : <span className="text-pf-warn-text">setting up</span>}
              </Td>
              <Td>
                {r.booking_engine === 'enabled'
                  ? <span className="text-pf-ok-text">on sale</span>
                  : (
                    <span className={r.live
                      ? 'text-pf-warn-text' : 'text-pf-muted'}>
                      {r.booking_engine === 'disabled'
                        ? 'switched off' : 'not switched on'}
                    </span>
                  )}
              </Td>
              <Td className="text-pf-muted">
                {r.last_activity_at
                  ? new Date(r.last_activity_at).toLocaleDateString()
                  : 'never'}
              </Td>
            </tr>
          ))}
        </DataTable>
      </div>

      {finishedNotSelling.length > 0 && (
        <Card className="mb-5 flex items-start gap-3 border-pf-warn-text/40 p-5">
          <AlertTriangle size={18} className="mt-0.5 shrink-0 text-pf-warn-text" />
          <div>
            <div className="text-pf-desc font-semibold text-pf-navy">
              {finishedNotSelling.length === 1
                ? 'One property has finished setup and cannot be booked'
                : `${finishedNotSelling.length} properties have finished setup `
                  + 'and cannot be booked'}
            </div>
            <p className="mt-1 text-pf-help text-pf-muted">
              Finishing onboarding does not put a hotel on sale. The booking
              engine is switched on per property — by the hotel itself, from
              its own settings, or from the property screen here — and until
              somebody does, a guest opening the booking link is told the
              property does not exist. Nothing in the wizard or in billing
              does it automatically.
            </p>
            <ul className="mt-2 space-y-1">
              {finishedNotSelling.map((r) => (
                <li key={r.property_id} className="text-pf-help">
                  <Link to={'/platform/properties/' + r.property_id}
                    className="text-pf-deep hover:underline">
                    {r.code} {r.name}
                  </Link>
                  <span className="text-pf-muted">
                    {' — '}
                    {r.booking_engine === 'disabled'
                      ? 'switched off' : 'never switched on'}
                    {r.booking_entitled ? '' : ' · not granted by their plan'}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        </Card>
      )}

      <h2 className="mb-2 text-pf-card text-pf-navy">Step detail</h2>
      <div className="space-y-3">
        {rows.map((r) => (
          <Card key={r.property_id} className="p-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <Link to={`/platform/properties/${r.property_id}`}
                    className="font-medium text-pf-deep hover:underline">
                    {r.name}
                  </Link>
                  <span className="font-mono text-xs tracking-widest text-pf-muted">
                    {r.code}
                  </span>
                  {r.live ? (
                    <span className="rounded-full bg-pf-ok-bg px-2 py-0.5 text-xs font-medium text-pf-ok-text">
                      live
                    </span>
                  ) : (
                    <span className="rounded-full bg-pf-warn-bg px-2 py-0.5 text-xs font-medium text-pf-warn-text">
                      setting up
                    </span>
                  )}
                </div>
                <div className="mt-0.5 text-sm text-pf-muted">
                  <Link to={`/platform/tenants/${r.organization_id}`}
                    className="hover:text-pf-deep">{r.organization_name}</Link>
                  {' · '}{r.rooms} room{r.rooms === 1 ? '' : 's'}
                  {r.trial_ends_on && ` · trial ends ${r.trial_ends_on}`}
                </div>
              </div>
              <div className="text-right">
                <div className="text-pf-desc font-medium text-pf-navy">
                  {r.visited_count} of {r.total_steps}
                </div>
                <div className="text-pf-help text-pf-muted">steps visited</div>
              </div>
            </div>

            {/* The wizard's own steps, in the order it walks them. */}
            <div className="mt-3 flex flex-wrap gap-1.5">
              {r.steps.map((s) => (
                <span key={s.code}
                  title={s.required ? `${s.label} (required)` : s.label}
                  className={`inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs ${
                    s.visited
                      ? 'bg-pf-soft text-pf-deep'
                      : s.required
                        ? 'bg-pf-warn-bg text-pf-warn-text'
                        : 'bg-pf-bg text-pf-muted'
                  }`}>
                  {s.visited ? <Check size={11} /> : <Minus size={11} />}
                  {s.label}
                </span>
              ))}
            </div>

            {!r.live && r.required_outstanding.length > 0 && (
              <p className="mt-3 border-t border-pf-divider pt-2.5 text-pf-help text-pf-muted">
                Not visited yet:{' '}
                <strong className="text-pf-warn-text">
                  {r.required_outstanding.join(', ')}
                </strong>
                {r.last_activity_at && (
                  <> · last activity{' '}
                    {new Date(r.last_activity_at).toLocaleDateString()}</>
                )}
              </p>
            )}
          </Card>
        ))}
      </div>
      <Note>
        A step counts as visited when the wizard's page was opened, which is
        not the same as finished. Mark a property live only after the setup
        behind each step has actually been checked.
      </Note>
    </Page>
  )
}
