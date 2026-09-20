import { useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import {
  ArrowLeft, ArrowRight, ChevronRight, Columns3, Download, FileText, Info,
  Loader2, Printer, Receipt, Search, Star, X,
} from 'lucide-react'
import {
  listBackOfficeReports, runBackOfficeReport,
  type BackOfficeReportResult, type ReportCell, type ReportMetric,
} from '../api'
import DateField from '../components/DateField'
import Select from '../components/Select'
import { useActivePropertyId } from '../hooks/useProperty'
import { downloadCsv, datedName } from '../lib/csv'
import {
  CONTROL, CONTROL_TYPE, FILTER_BOX, FILTER_BOX_ICON, FILTER_SELECT,
} from '../lib/controls'
import { fmtDate, fmtDateTime } from '../lib/dates'
import { readFavorites, reportHref, toggleFavorite } from '../lib/reports'

/**
 * One back-office report.
 *
 * Every report in the catalog renders through this screen: the finance
 * service sends the columns, the rows, the metric definitions and the
 * filters, and this draws them. Adding a report is a query on the server, not
 * a new page here.
 *
 * **Dates and parameters go to the server; column filters and search do not.**
 * The date window decides which rows exist, so changing it is a new question.
 * Narrowing by cashier or status only hides rows already fetched, and asking
 * the server again for that would be a round trip to learn nothing new. The
 * metric tiles and the totals are always computed from the rows on screen, so
 * they can never describe rows the reader has filtered away.
 */

const money = new Intl.NumberFormat('en-IN', {
  style: 'currency', currency: 'INR', minimumFractionDigits: 2,
})
const plain = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 2 })
const NUMERIC: ReadonlySet<string> = new Set(['money', 'number', 'percent'])

function show(v: ReportCell, kind: string): string {
  if (v === null || v === undefined || v === '') return '—'
  if (kind === 'money') return money.format(Number(v))
  if (kind === 'number') return plain.format(Number(v))
  if (kind === 'percent') return `${Number(v).toFixed(1)}%`
  if (kind === 'date') return fmtDate(String(v))
  if (kind === 'datetime') return fmtDateTime(String(v))
  return String(v)
}

function sum(rows: ReportCell[][], i: number): number {
  return rows.reduce((t, r) => t + (Number(r[i]) || 0), 0)
}

/** A metric tile's figure, over the rows currently shown. */
function metric(m: ReportMetric, rows: ReportCell[][]): string {
  const one = typeof m.col === 'number' ? m.col : 0
  const many = Array.isArray(m.col) ? m.col : []
  const rs = m.where === null
    ? rows
    : rows.filter((r) =>
      (String(r[m.where as number] ?? '') === m.value) !== m.negate)
  let v: number | null
  switch (m.op) {
    case 'count': v = rs.length; break
    case 'sum': v = sum(rs, one); break
    case 'sumcols': v = many.reduce((t, c) => t + sum(rs, c), 0); break
    case 'difference': v = sum(rs, many[0]) - sum(rs, many[1]); break
    case 'unique': v = new Set(rs.map((r) => r[one])).size; break
    case 'max':
      v = rs.length ? Math.max(...rs.map((r) => Number(r[one]) || 0)) : null
      break
    case 'nonzero': v = rs.filter((r) => Number(r[one]) !== 0).length; break
    case 'ratio': {
      const den = sum(rs, m.den ?? 0)
      v = den ? (sum(rs, one) / den) * (m.fmt === 'percent' ? 100 : 1) : null
      break
    }
    default: v = null
  }
  return v === null ? '—' : show(v, m.fmt)
}

/** Badge colour for a status word. Red is checked first so "Not verified"
 *  is not painted green for containing "verified". */
function tone(s: string): string {
  const t = s.toLowerCase()
  if (/(fail|out of order|not verified|cancel|collect|short|over|rejected|urgent|owes)/.test(t)) {
    return 'bg-red-50 text-red-700'
  }
  if (/(open|dirty|pending|review|no show|blocked|unapplied|arrival|refunded|not declared|stop sell|partially|payable|no rate|high|on hold)/.test(t)) {
    return 'bg-amber-50 text-amber-700'
  }
  if (/(in-house|occupied|cleaning|in progress|reserved)/.test(t)) {
    return 'bg-sky-50 text-sky-700'
  }
  if (/(settled|succeeded|captured|closed|clean|inspected|checked out|verified|active|posted|balanced|vacant|ready|paid|approved|issued|within limit|completed)/.test(t)) {
    return 'bg-emerald-50 text-emerald-700'
  }
  return 'bg-slate-75 text-slate-600'
}

/** Remounted per report, so filters chosen on one never leak into the next. */
export default function BackOfficeReportRoute() {
  const { slug = '' } = useParams()
  return <BackOfficeReport key={slug} slug={slug} />
}

function BackOfficeReport({ slug }: { slug: string }) {
  const propertyId = useActivePropertyId()

  // What the panel is editing, and what the report was actually run with.
  const [draft, setDraft] = useState<Record<string, string>>({})
  const [applied, setApplied] = useState<Record<string, string>>({})
  const [draftCols, setDraftCols] = useState<Record<number, string>>({})
  const [appliedCols, setAppliedCols] = useState<Record<number, string>>({})
  const [needle, setNeedle] = useState('')
  const [hidden, setHidden] = useState<number[]>([])
  const [choosing, setChoosing] = useState(false)
  const [picked, setPicked] = useState<number | null>(null)
  const [favorites, setFavorites] = useState(readFavorites)
  const [error, setError] = useState('')

  const q = useQuery({
    queryKey: ['backoffice-report', slug, propertyId, applied],
    queryFn: () => runBackOfficeReport(slug, propertyId, applied),
    enabled: propertyId !== '',
    placeholderData: keepPreviousData,
  })
  const catalog = useQuery({
    queryKey: ['backoffice-reports', propertyId],
    queryFn: () => listBackOfficeReports(propertyId),
    enabled: propertyId !== '',
  })
  const d = q.data

  // Rows after column filters and search, each with its index in the
  // response so its link can still be found.
  const view = useMemo(() => {
    if (!d) return []
    const text = needle.trim().toLowerCase()
    return d.rows
      .map((row, i) => ({ row, i }))
      .filter(({ row }) =>
        Object.entries(appliedCols).every(([c, v]) =>
          !v || String(row[Number(c)] ?? '') === v)
        && (!text || row.some((v, c) =>
          show(v, d.columns[c]?.kind ?? 'text').toLowerCase().includes(text))))
  }, [d, appliedCols, needle])

  if (q.isLoading || propertyId === '') {
    return (
      <p className="flex items-center gap-2 py-16 text-sm text-slate-400">
        <Loader2 size={15} className="animate-spin" /> Running the report…
      </p>
    )
  }
  if (q.isError || !d) {
    return (
      <div className="space-y-3 py-16 text-center text-sm text-slate-500">
        <p>The report could not be run. {(q.error as Error)?.message}</p>
        <Link to="/reports" className="text-brand hover:underline">
          Back to all reports
        </Link>
      </div>
    )
  }

  const rows = view.map((v) => v.row)
  const visible = d.columns.map((_, i) => i).filter((i) => !hidden.includes(i))
  const favorite = favorites.includes(slug)
  const live = (catalog.data ?? []).filter((r) => r.available && !r.href)
  const at = live.findIndex((r) => r.slug === slug)
  const prev = at > 0 ? live[at - 1] : undefined
  const next = at >= 0 && at < live.length - 1 ? live[at + 1] : undefined
  const filtersActive = Object.values(appliedCols).some(Boolean)
    || Object.values(applied).some(Boolean)

  function run() {
    const from = draft.date_from ?? d!.date_from
    const to = draft.date_to ?? d!.date_to
    if (d!.dates === 'range' && from > to) {
      setError('The end date must be on or after the start date.')
      return
    }
    setError('')
    setPicked(null)
    setApplied({ ...draft })
    setAppliedCols({ ...draftCols })
  }
  function reset() {
    setDraft({})
    setApplied({})
    setDraftCols({})
    setAppliedCols({})
    setNeedle('')
    setError('')
    setPicked(null)
  }
  function exportCsv() {
    downloadCsv(
      datedName(slug, d!.date_to),
      visible.map((i) => d!.columns[i].label),
      rows.map((r) => visible.map((i) => {
        const c = d!.columns[i]
        // Figures stay numbers so a spreadsheet can add them up; dates are
        // written the way the screen shows them.
        return NUMERIC.has(c.kind) ? r[i] : show(r[i], c.kind)
      })),
    )
  }

  const pickedRow = picked === null ? undefined : view.find((v) => v.i === picked)

  return (
    <div className={pickedRow ? 'flex gap-4' : ''}>
    <div className="min-w-0 flex-1 space-y-4">
      {/* ---------------------------------------------------------- head --- */}
      <nav className="flex items-center gap-1.5 text-xs text-slate-400">
        <Link to="/reports" className="hover:text-slate-600">Reports</Link>
        <ChevronRight size={12} />
        <Link to={`/reports?category=${encodeURIComponent(d.category)}`}
          className="hover:text-slate-600">
          {d.category}
        </Link>
        <ChevronRight size={12} />
        <span className="font-medium text-slate-600">{d.title}</span>
      </nav>

      <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
        <div>
          <h1 className="text-display text-ink">{d.title}</h1>
          <p className="mt-1 text-sm text-slate-500">{d.description}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button onClick={() => setFavorites(toggleFavorite(slug))}
            aria-pressed={favorite}
            aria-label={favorite ? 'Remove from favorites' : 'Add to favorites'}
            title={favorite ? 'Remove from favorites' : 'Add to favorites'}
            className={`${FILTER_BOX} px-2.5 hover:bg-slate-50`}>
            <Star size={15} className={favorite
              ? 'fill-amber-400 text-amber-400' : 'text-slate-500'} />
          </button>
          <button onClick={exportCsv}
            className={`flex items-center gap-2 ${FILTER_BOX} ${CONTROL} hover:bg-slate-50`}>
            <Download size={15} /> Export CSV
          </button>
          <button onClick={() => window.print()}
            className={`flex items-center gap-2 ${FILTER_BOX} ${CONTROL} hover:bg-slate-50`}>
            <Printer size={15} /> Print
          </button>
        </div>
      </div>

      {/* -------------------------------------------------------- filters --- */}
      <section className="rounded-xl border border-slate-100 bg-white p-4">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
          {d.dates === 'range' && (
            <>
              <label className="text-xs text-slate-500">
                <span className="block">{d.basis} from</span>
                <DateField className="mt-1 w-full"
                  value={draft.date_from ?? d.date_from}
                  onChange={(v) => setDraft((x) => ({ ...x, date_from: v }))}
                  label={`${d.basis} from`} />
              </label>
              <label className="text-xs text-slate-500">
                <span className="block">To</span>
                <DateField className="mt-1 w-full"
                  value={draft.date_to ?? d.date_to}
                  onChange={(v) => setDraft((x) => ({ ...x, date_to: v }))}
                  label={`${d.basis} to`} />
              </label>
            </>
          )}
          {d.dates === 'single' && (
            <label className="text-xs text-slate-500">
              <span className="block">{d.basis}</span>
              <DateField className="mt-1 w-full"
                value={draft.date_to ?? d.date_to}
                onChange={(v) => setDraft((x) => ({ ...x, date_to: v }))}
                label={d.basis} />
            </label>
          )}
          {d.params.map((p) => (
            <label key={p.key} className="text-xs text-slate-500">
              <span className="block">{p.label}</span>
              <input type={p.kind === 'number' ? 'number' : 'text'} min={0}
                value={draft[p.key] ?? String(d.param_values[p.key] ?? '')}
                onChange={(e) => setDraft((x) => ({ ...x, [p.key]: e.target.value }))}
                onKeyDown={(e) => { if (e.key === 'Enter') run() }}
                className={`mt-1 w-full ${FILTER_BOX} text-sm text-slate-700 outline-none focus:border-brand`} />
            </label>
          ))}
          {d.filters.map((c) => {
            const values = [...new Set(d.rows
              .map((r) => r[c])
              .filter((v) => v !== null && v !== '')
              .map(String))].sort()
            return (
              <label key={c} className="text-xs text-slate-500">
                <span className="block">{d.columns[c].label}</span>
                <Select blankIsChoice className={`mt-1 w-full ${FILTER_SELECT}`}
                  value={draftCols[c] ?? ''}
                  onChange={(e) => setDraftCols((x) => ({ ...x, [c]: e.target.value }))}>
                  <option value="">All</option>
                  {values.map((v) => <option key={v} value={v}>{v}</option>)}
                </Select>
              </label>
            )
          })}
          <div className="flex items-end gap-2">
            {filtersActive && (
              <button onClick={reset}
                className={`${FILTER_BOX} ${CONTROL} hover:bg-slate-50`}>
                Reset
              </button>
            )}
            <button onClick={run} disabled={q.isFetching}
              className={`flex flex-1 items-center justify-center gap-2 rounded-lg bg-brand px-4 py-2 ${CONTROL_TYPE} text-white hover:bg-brand-dark disabled:opacity-50`}>
              {q.isFetching
                ? <Loader2 size={15} className="animate-spin" />
                : <Search size={15} />}
              Run Report
            </button>
          </div>
        </div>
        {error && <p className="mt-2 text-xs text-red-600">{error}</p>}
        <p className="mt-3 flex flex-wrap gap-x-4 gap-y-1 border-t border-slate-100 pt-3 text-xs text-slate-400">
          <span>{d.property_name}</span>
          <span>
            {d.dates === 'range'
              ? `${d.basis}: ${fmtDate(d.date_from)} – ${fmtDate(d.date_to)}`
              : d.dates === 'single'
                ? `${d.basis}: ${fmtDate(d.date_to)}`
                : d.basis}
          </span>
          <span>Open business date: {fmtDate(d.business_date)}</span>
          <span>Currency: {d.currency}</span>
        </p>
      </section>

      {/* -------------------------------------------------------- metrics --- */}
      {d.metrics.length > 0 && (
        <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {d.metrics.map((m) => (
            <div key={m.label} className="rounded-xl border border-slate-100 bg-white p-4">
              <p className="text-xs font-medium text-slate-500">{m.label}</p>
              <p className="mt-1.5 text-xl font-semibold text-slate-800">
                {metric(m, rows)}
              </p>
              <p className="mt-0.5 text-[11px] text-slate-400">
                {m.sub || 'For the records shown'}
              </p>
            </div>
          ))}
        </section>
      )}

      {/* ---------------------------------------------------------- table --- */}
      <section className="overflow-hidden rounded-xl border border-slate-100 bg-white">
        <header className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-4 py-3">
          <h2 className="text-section font-semibold text-slate-800">
            Report results{' '}
            <span className="text-sm font-normal text-slate-400">
              {rows.length === d.rows.length
                ? `${rows.length} records`
                : `${rows.length} of ${d.rows.length} records`}
            </span>
          </h2>
          <div className="relative flex items-center gap-2">
            <span className="relative">
              <Search size={14} className="absolute left-3 top-2.5 text-slate-400" />
              <input value={needle} onChange={(e) => setNeedle(e.target.value)}
                placeholder="Search within report" aria-label="Search within report"
                className={`w-56 ${FILTER_BOX_ICON} text-sm text-slate-700 outline-none focus:border-brand`} />
            </span>
            <button onClick={() => setChoosing((x) => !x)} aria-expanded={choosing}
              className={`flex items-center gap-2 ${FILTER_BOX} ${CONTROL} hover:bg-slate-50`}>
              <Columns3 size={15} /> Columns
            </button>
            {choosing && (
              <div className="absolute right-0 top-11 z-20 w-64 rounded-xl border border-slate-200 bg-white p-3 shadow-lg">
                <div className="mb-2 flex items-center justify-between">
                  <p className="text-xs font-semibold text-slate-600">
                    Columns on screen and in the export
                  </p>
                  <button onClick={() => setChoosing(false)} aria-label="Close"
                    className="rounded p-0.5 text-slate-400 hover:bg-slate-50">
                    <X size={14} />
                  </button>
                </div>
                <div className="max-h-72 space-y-1 overflow-y-auto">
                  {d.columns.map((c, i) => (
                    <label key={c.key}
                      className="flex items-center gap-2 rounded px-1 py-1 text-sm text-slate-700 hover:bg-slate-50">
                      <input type="checkbox" disabled={i === 0}
                        checked={!hidden.includes(i)}
                        onChange={() => setHidden((h) =>
                          h.includes(i) ? h.filter((x) => x !== i) : [...h, i])} />
                      {c.label}
                    </label>
                  ))}
                </div>
                <p className="mt-2 text-[11px] text-slate-400">
                  The first column always stays visible.
                </p>
              </div>
            )}
          </div>
        </header>

        {rows.length === 0 ? (
          <div className="px-4 py-14 text-center">
            <p className="text-sm font-medium text-slate-700">
              {d.rows.length === 0 ? 'Nothing to report' : 'No matching records'}
            </p>
            <p className="mx-auto mt-1 max-w-lg text-xs leading-relaxed text-slate-500">
              {d.rows.length === 0
                ? d.empty_text
                : 'Nothing matches the filters or search. Reset them to see every record.'}
            </p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-sm [&_td]:border [&_td]:border-slate-100 [&_th]:border [&_th]:border-slate-100 [&_th]:whitespace-nowrap">
              <thead className="bg-slate-50/80 text-left text-xs font-semibold text-slate-500">
                <tr>
                  {visible.map((i) => (
                    <th key={d.columns[i].key}
                      className={`px-3 py-2.5 font-semibold ${
                        NUMERIC.has(d.columns[i].kind) ? 'text-right' : ''}`}>
                      {d.columns[i].label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {view.map(({ row, i: at }) => (
                  <tr key={at} tabIndex={0}
                    onClick={() => setPicked(at)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault()
                        setPicked(at)
                      }
                    }}
                    className={`cursor-pointer outline-none focus-visible:bg-brand/5 ${
                      picked === at ? 'bg-brand/10' : 'hover:bg-slate-50/60'}`}>
                    {visible.map((c, ix) => {
                      const col = d.columns[c]
                      const txt = show(row[c], col.kind)
                      return (
                        <td key={col.key}
                          className={`px-3 py-2.5 ${
                            NUMERIC.has(col.kind) ? 'whitespace-nowrap text-right text-slate-700'
                              : col.kind === 'date' || col.kind === 'datetime'
                                ? 'whitespace-nowrap text-slate-600'
                                : ix === 0 ? 'font-medium text-slate-800' : 'text-slate-600'}`}>
                          {col.kind === 'status' && row[c]
                            ? (
                              <span className={`whitespace-nowrap rounded-md px-2 py-0.5 text-xs font-medium ${tone(txt)}`}>
                                {txt}
                              </span>
                            )
                            : txt}
                        </td>
                      )
                    })}
                  </tr>
                ))}
              </tbody>
              {d.totals.length > 0 && (
                <tfoot className="bg-brand/5 font-semibold text-brand-dark">
                  <tr>
                    {visible.map((c, ix) => (
                      <td key={d.columns[c].key}
                        className={`whitespace-nowrap px-3 py-2.5 ${
                          NUMERIC.has(d.columns[c].kind) ? 'text-right' : ''}`}>
                        {d.totals.includes(c)
                          ? show(sum(rows, c), d.columns[c].kind)
                          : ix === 0 ? 'Total' : ''}
                      </td>
                    ))}
                  </tr>
                </tfoot>
              )}
            </table>
          </div>
        )}
        {d.truncated && (
          <p className="border-t border-slate-100 px-4 py-2.5 text-xs text-caution">
            Showing the first {d.rows.length} records. Narrow the dates to see the rest.
          </p>
        )}
      </section>

      <p className="flex items-start gap-2 rounded-xl border border-slate-100 bg-white px-4 py-3 text-xs leading-relaxed text-slate-500">
        <Info size={14} className="mt-0.5 shrink-0 text-slate-400" />
        <span><strong className="text-slate-600">Report basis.</strong> {d.note}</span>
      </p>

      <div className="flex flex-wrap items-center justify-between gap-3 text-xs">
        {prev ? (
          <Link to={reportHref(prev)}
            className="flex items-center gap-1.5 text-slate-500 hover:text-brand">
            <ArrowLeft size={13} /> {prev.title}
          </Link>
        ) : <span />}
        <Link to="/reports" className="font-medium text-slate-500 hover:text-brand">
          All reports
        </Link>
        {next ? (
          <Link to={reportHref(next)}
            className="flex items-center gap-1.5 text-slate-500 hover:text-brand">
            {next.title} <ArrowRight size={13} />
          </Link>
        ) : <span />}
      </div>
    </div>

    {pickedRow && (
      <RecordDrawer d={d} row={pickedRow.row}
        reservationId={d.links[pickedRow.i] ?? null}
        onClose={() => setPicked(null)} />
    )}
    </div>
  )
}

/** Every column of one record, including the ones hidden from the table. */
function RecordDrawer({ d, row, reservationId, onClose }: {
  d: BackOfficeReportResult; row: ReportCell[]
  reservationId: string | null; onClose: () => void
}) {
  return (
    <aside className="w-[22rem] shrink-0 space-y-4 self-start rounded-xl border border-slate-100 bg-white p-4">
      <header className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-section font-semibold text-slate-800">Record details</h2>
          <p className="text-xs text-slate-500">{d.title}</p>
        </div>
        <button onClick={onClose} aria-label="Close"
          className="rounded p-1 text-slate-400 hover:bg-slate-50 hover:text-slate-600">
          <X size={16} />
        </button>
      </header>
      <dl className="space-y-1.5 border-t border-slate-100 pt-3 text-sm">
        {d.columns.map((c, i) => (
          <div key={c.key} className="flex items-baseline justify-between gap-3">
            <dt className="text-slate-500">{c.label}</dt>
            <dd className="min-w-0 break-words text-right font-medium text-slate-800">
              {show(row[i], c.kind)}
            </dd>
          </div>
        ))}
      </dl>
      {reservationId && (
        <div className="flex flex-wrap gap-2">
          <Link to={`/reservations/${reservationId}/folio`}
            className={`flex flex-1 items-center justify-center gap-2 ${FILTER_BOX} ${CONTROL} hover:bg-slate-50`}>
            <FileText size={15} /> View Folio
          </Link>
          <Link to={`/reservations/${reservationId}`}
            className={`flex flex-1 items-center justify-center gap-2 ${FILTER_BOX} ${CONTROL} hover:bg-slate-50`}>
            <Receipt size={15} /> Reservation
          </Link>
        </div>
      )}
    </aside>
  )
}
