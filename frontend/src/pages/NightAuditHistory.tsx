import { useState } from 'react'
import { useInfiniteQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import {
  AlertTriangle, ArrowLeft, ArrowRight, CheckCircle2, History, Loader2, Wallet,
} from 'lucide-react'
import { listNightAuditRuns, type NightAuditRun } from '../api'
import { usePropertyName } from '../hooks/useProperty'
import DateRangeFilter from '../components/DateRangeFilter'
import { fmtDateTime } from '../lib/dates'
import {
  money, shortDate, bucketOf, TABS, ReportModal, type RunBucket,
} from '../components/nightAudit'
import { useActivePropertyId } from '../hooks/useProperty'

/**
 * Every night audit this property has run.
 *
 * Split out of the audit screen, which is a screen about *tonight*: the table
 * sat below the fold there, so checking last Tuesday meant scrolling past a
 * decision you were not making. History is a different question asked at a
 * different time, and it deserves the whole page — room for the filters and
 * for more than a handful of rows.
 */


//: How many runs to fetch at a time. Roughly two months of daily audits —
//: enough that most visits never need a second page.
const PAGE = 60

export default function NightAuditHistory() {
  const propertyId = useActivePropertyId()
  const propertyName = usePropertyName()
  const [tab, setTab] = useState<RunBucket | 'all'>('all')
  const [report, setReport] = useState<NightAuditRun | null>(null)
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')

  /*
   * Real pages, fetched by offset and appended.
   *
   * The first version grew a single `limit` instead — 60, 120, 180 — which
   * re-fetched every row already on screen and, worse, walked straight into
   * the endpoint's 500 cap: the ninth press asked for 540 and got a 422. A
   * property with two years of audits would have hit a dead end with no way
   * back except narrowing the dates.
   *
   * Each page is a fixed `limit` at a growing `offset`, so nothing is fetched
   * twice and there is no ceiling to walk into.
   */
  const history = useInfiniteQuery({
    queryKey: ['night-audit-history', propertyId, from, to],
    queryFn: ({ pageParam }) =>
      listNightAuditRuns(propertyId, { from, to, limit: PAGE, offset: pageParam }),
    initialPageParam: 0,
    getNextPageParam: (last, pages) => {
      const loaded = pages.reduce((n, pg) => n + pg.rows.length, 0)
      return loaded < last.total ? loaded : undefined
    },
    enabled: propertyId !== '',
  })

  const runs = history.data?.pages.flatMap((pg) => pg.rows) ?? []
  const total = history.data?.pages[0]?.total ?? 0
  const filtered = from !== '' || to !== ''
  const counts = runs.reduce<Record<string, number>>((acc, r) => {
    const b = bucketOf(r)
    acc[b] = (acc[b] ?? 0) + 1
    acc.all += 1
    return acc
  }, { all: 0 })
  const shown = tab === 'all' ? runs : runs.filter((r) => bucketOf(r) === tab)

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <div className="flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-1">
          <h1 className="flex items-center gap-2 text-display text-ink">
            <History size={26} className="text-brand" /> Audit History
          </h1>
          {propertyName && (
            <span className="text-sm text-slate-500">{propertyName}</span>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Link to="/night-audit"
            className="flex items-center gap-1.5 whitespace-nowrap rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-600 hover:bg-slate-50">
            <ArrowLeft size={15} /> Back to Night Audit
          </Link>
        </div>
      </div>

      {/* One row: outcome as a segmented track, then the date range. The
          range is applied on the server, because filtering the newest N in
          the browser leaves everything older invisible however you filter
          it; the outcome only sorts the runs already loaded. */}
      <div className="flex flex-wrap items-center gap-2">
        <span className="inline-flex shrink-0 items-center gap-0.5 rounded-lg border border-slate-200 bg-white p-0.5">
          {TABS.map((t) => {
            const n = counts[t.key] ?? 0
            return (
              <button key={t.key} type="button" onClick={() => setTab(t.key)}
                aria-pressed={tab === t.key}
                // An empty group stays visible, greyed: "no failed runs" is
                // worth being able to see, and a strip whose tabs come and go
                // is one you cannot learn.
                disabled={n === 0 && t.key !== 'all'}
                className={`whitespace-nowrap rounded-md px-3 py-1.5 text-sm ${
                  tab === t.key ? 'bg-brand font-semibold text-white'
                    : n === 0 && t.key !== 'all'
                      ? 'cursor-not-allowed font-medium text-slate-300'
                      : 'font-medium text-slate-500 hover:bg-slate-50 hover:text-slate-700'}`}>
                {t.label}
                <span className={tab === t.key ? 'ml-1.5 text-white/70'
                                               : 'ml-1.5 text-slate-400'}>
                  {n}
                </span>
              </button>
            )
          })}
        </span>
        <DateRangeFilter label="Business date" direction="past" from={from} to={to}
          onChange={(a, b) => { setFrom(a); setTo(b) }} />
      </div>

      <div className="overflow-hidden rounded-xl border border-slate-200 bg-white">
        {history.isLoading ? (
          <div className="flex items-center gap-2 px-5 py-8 text-sm text-slate-400">
            <Loader2 size={14} className="animate-spin" /> Loading…
          </div>
        ) : runs.length === 0 ? (
          <p className="px-5 py-12 text-center text-sm text-slate-400">
            No day has been closed yet.
          </p>
        ) : shown.length === 0 ? (
          <p className="px-5 py-12 text-center text-sm text-slate-400">
            No runs in this group.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[760px] text-sm">
              <thead className="border-b border-slate-100 bg-slate-50/60 text-left text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="px-5 py-3 font-medium">Business date</th>
                  <th className="px-5 py-3 font-medium">Completed at</th>
                  <th className="px-5 py-3 font-medium">Charges posted</th>
                  <th className="px-5 py-3 font-medium">Amount</th>
                  <th className="px-5 py-3 font-medium">Closed by</th>
                  <th className="px-5 py-3 font-medium">Status</th>
                  <th className="px-5 py-3 font-medium">Report</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((r) => {
                  const c = r.steps.find((s) => s.step_code === 'post_room_charges')
                  const bucket = bucketOf(r)
                  const failed = bucket === 'failed'
                  return (
                    <tr key={r.run_id}
                      className="border-b border-slate-100 last:border-0 hover:bg-slate-50/60">
                      <td className="px-5 py-3.5 font-medium text-slate-800">
                        {shortDate(r.business_date)}
                        {r.run_number > 1 && (
                          <span className="ml-1.5 text-xs font-normal text-slate-400">
                            attempt {r.run_number}
                          </span>
                        )}
                      </td>
                      <td className="px-5 py-3.5 text-slate-600">
                        {fmtDateTime(r.completed_at)}
                      </td>
                      <td className="px-5 py-3.5 text-slate-600">
                        {String(c?.detail?.posted ?? 0)}
                      </td>
                      <td className="px-5 py-3.5 text-slate-600">
                        {money(String(c?.detail?.amount ?? 0))}
                      </td>
                      <td className="px-5 py-3.5 text-slate-600">
                        {r.run_by_name ?? (
                          <span className="text-slate-400">Automatic</span>
                        )}
                      </td>
                      <td className="px-5 py-3.5">
                        <span className={`inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium ${
                          failed ? 'bg-red-50 text-red-700'
                            : bucket === 'exception' ? 'bg-amber-50 text-amber-700'
                            : 'bg-emerald-50 text-emerald-700'}`}>
                          {failed ? <AlertTriangle size={12} />
                            : bucket === 'exception' ? <Wallet size={12} />
                            : <CheckCircle2 size={12} />}
                          {failed ? 'Failed'
                            : bucket === 'exception' ? 'Closed over open till'
                            : 'Completed'}
                        </span>
                      </td>
                      <td className="px-5 py-3.5">
                        <button type="button" onClick={() => setReport(r)}
                          className="flex items-center gap-1 text-sm font-medium text-brand hover:underline">
                          View report <ArrowRight size={13} />
                        </button>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}

        {/* Never let the page look complete when it is not: an audit trail
            that silently stops at the newest N hides the night somebody came
            here to find. */}
        {runs.length > 0 && (
          <div className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-100 px-5 py-3 text-xs text-slate-500">
            <span>
              Showing {shown.length}
              {tab !== 'all' && ` of ${runs.length} loaded`}
              {' '}of {total} run{total === 1 ? '' : 's'}
              {filtered && ' in this range'}
            </span>
            {history.hasNextPage && (
              <button type="button" onClick={() => history.fetchNextPage()}
                disabled={history.isFetchingNextPage}
                className="flex items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-1.5 font-medium text-brand hover:bg-slate-50 disabled:opacity-50">
                {history.isFetchingNextPage
                  && <Loader2 size={12} className="animate-spin" />}
                Load {Math.min(PAGE, total - runs.length)} more
              </button>
            )}
          </div>
        )}
      </div>

      {report && (
        <ReportModal run={report} propertyName={propertyName}
          propertyId={propertyId}
          onClose={() => setReport(null)} />
      )}
    </div>
  )
}
