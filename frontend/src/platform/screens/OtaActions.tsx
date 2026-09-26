/**
 * OTA obligations across every property.
 *
 * A no-show on an OTA booking has to be reported to that channel within 24
 * hours or the hotel pays commission on a room nobody slept in. Each property
 * can already see its own queue. This is the view that only exists here: one
 * hotel quietly missing its window every week costs more over a year than a
 * single forgotten booking ever does, and from inside that hotel it looks
 * like nothing at all.
 *
 * So the screen is ordered by who is worst, not alphabetically, and leads
 * with how late the worst obligation is rather than how many there are — ten
 * an hour late and one a fortnight late are different problems with the same
 * count.
 *
 * Properties owing nothing are listed with zeros rather than dropped. An
 * empty row is the evidence that a hotel is keeping up; omitting it would
 * make "no rows" ambiguous between "all clear" and "not reporting".
 */
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Busy, DataTable, Metrics, Note, Page, Td, errorText } from '../ui'
import { otaActions, type OtaPropertyRow } from '../api'

/** How late, in the words somebody would use out loud. */
function lateness(hours: number | null): string {
  if (hours === null) return '—'
  if (hours < 24) return `${Math.floor(hours)}h late`
  const days = Math.floor(hours / 24)
  return `${days}d late`
}

function dueIn(iso: string | null): string {
  if (!iso) return '—'
  const h = (new Date(iso).getTime() - Date.now()) / 3_600_000
  if (h < 0) return 'now'
  if (h < 1) return `${Math.max(Math.floor(h * 60), 1)} min`
  return `${Math.floor(h)}h`
}

export default function PlatformOtaActions() {
  const [rows, setRows] = useState<OtaPropertyRow[]>([])
  const [busy, setBusy] = useState(true)
  const [err, setErr] = useState('')

  useEffect(() => {
    let live = true
    const load = () => otaActions()
      .then((r) => { if (live) { setRows(r); setErr('') } })
      .catch((e) => { if (live) setErr(errorText(e, 'Could not load OTA actions.')) })
      .finally(() => { if (live) setBusy(false) })
    load()
    // The clock is the subject of this screen; a stale one is worse than none.
    // Not while the tab is hidden: nobody is reading it, and a console left
    // open overnight should not poll all night.
    const t = setInterval(() => { if (!document.hidden) load() }, 60_000)
    return () => { live = false; clearInterval(t) }
  }, [])

  const open = rows.reduce((n, r) => n + r.open_count, 0)
  const overdue = rows.reduce((n, r) => n + r.overdue_count, 0)
  const failing = rows.filter((r) => r.overdue_count > 0)
  const worst = rows.reduce<number | null>(
    (m, r) => (r.oldest_overdue_hours !== null
      && (m === null || r.oldest_overdue_hours > m))
      ? r.oldest_overdue_hours : m, null)

  return (
    <Page
      eyebrow="Operations"
      title="OTA Actions"
      subtitle={'No-shows on channel bookings must be reported to the channel '
        + 'within 24 hours, or the hotel pays commission on a room nobody '
        + 'slept in. This is every property at once.'}
    >
        <Metrics items={[
          { label: 'Open obligations', value: open,
            caption: 'Across every property' },
          { label: 'Past the deadline', value: overdue,
            caption: overdue > 0 ? 'Commission may be unrecoverable' : 'None',
            tone: overdue > 0 ? 'warn' : 'good' },
          { label: 'Properties behind', value: failing.length,
            caption: `of ${rows.length} active`,
            tone: failing.length > 0 ? 'warn' : 'good' },
          { label: 'Worst overdue', value: lateness(worst),
            caption: worst === null ? 'Nothing late' : 'Oldest unreported',
            tone: worst !== null && worst > 24 ? 'warn' : 'default' },
        ]} />

        {err && (
          <p className="mb-4 rounded-lg bg-red-50 px-4 py-3 text-pf-td text-red-700">
            {err}
          </p>
        )}

        {busy ? <Busy /> : (
        <DataTable
          title="By property"
          head={['Property', 'Organisation', 'Open', 'Overdue',
            'Worst overdue', 'Next due', 'Reported']}
          count={rows.length}
          empty="No active properties."
          footnote={
            <>
              Ordered by who is furthest behind, not by name. A property with
              nothing owed is still listed — that is how you tell “all clear”
              from “not reporting”.
            </>
          }
        >
          {rows.map((r) => (
            <tr key={r.property_id}
              className={r.overdue_count > 0 ? 'bg-red-50/40' : undefined}>
              <Td>
                <Link to={`/platform/properties/${r.property_id}`}
                  className="font-medium text-pf-navy hover:underline">
                  {r.name}
                </Link>
                <span className="block text-xs text-slate-400">{r.code}</span>
              </Td>
              <Td>{r.organization_name}</Td>
              <Td className="tabular-nums">{r.open_count}</Td>
              <Td className="tabular-nums">
                {r.overdue_count > 0 ? (
                  <span className="rounded bg-red-100 px-1.5 py-0.5 font-semibold text-red-700">
                    {r.overdue_count}
                  </span>
                ) : '0'}
              </Td>
              <Td className={r.oldest_overdue_hours !== null
                ? 'font-medium text-red-700' : 'text-slate-400'}>
                {lateness(r.oldest_overdue_hours)}
              </Td>
              <Td className="text-slate-500">{dueIn(r.next_due_at)}</Td>
              <Td className="tabular-nums text-slate-500">
                {r.reported_count}
              </Td>
            </tr>
          ))}
        </DataTable>
        )}

        <Note>
          Reporting happens in the property’s own console, under Distribution →
          OTA Actions, and in the channel’s extranet. Nothing is reported from
          here: whether a channel manager can report a no-show depends on the
          channel and the plan, and a console that appears to send and quietly
          does not is worse than one that does not pretend.
        </Note>
    </Page>
  )
}
