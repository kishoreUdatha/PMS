import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { BarChart3, ChevronRight, FileText, Loader2, Search, Star } from 'lucide-react'
import { listBackOfficeReports, type BackOfficeReportInfo } from '../api'
import { useActivePropertyId } from '../hooks/useProperty'
import { FILTER_CHIP } from '../lib/controls'
import { CATEGORY_ICON, readFavorites, reportHref } from '../lib/reports'

/**
 * Back office reports — the catalog.
 *
 * Every report in the back-office design is listed, including the ones that
 * are not built yet. Those are shown greyed with the reason rather than left
 * out: which reports exist is part of what a manager needs to know, and a
 * catalog that quietly omits the city ledger reads as though there is none to
 * ask for.
 *
 * The list comes from the finance service, not a constant here, so a report
 * becomes clickable the day its query ships without a frontend change.
 */

const ALL = 'All reports'

export default function ReportsCatalog() {
  const propertyId = useActivePropertyId()
  const [params, setParams] = useSearchParams()
  const category = params.get('category') ?? ALL
  const [needle, setNeedle] = useState('')
  const [favOnly, setFavOnly] = useState(false)
  const favorites = readFavorites()

  const q = useQuery({
    queryKey: ['backoffice-reports', propertyId],
    queryFn: () => listBackOfficeReports(propertyId),
    enabled: propertyId !== '',
  })

  if (q.isLoading || propertyId === '') {
    return (
      <p className="flex items-center gap-2 py-16 text-sm text-slate-400">
        <Loader2 size={15} className="animate-spin" /> Loading reports…
      </p>
    )
  }
  if (q.isError || !q.data) {
    return (
      <p className="py-16 text-center text-sm text-slate-500">
        The report list could not be loaded. {(q.error as Error)?.message}
      </p>
    )
  }

  const reports = q.data
  const categories = [...new Set(reports.map((r) => r.category))]
  const text = needle.trim().toLowerCase()
  const shown = reports.filter((r) =>
    (favOnly || category === ALL || r.category === category)
    && (!favOnly || favorites.includes(r.slug))
    && `${r.title} ${r.description}`.toLowerCase().includes(text))
  const live = reports.filter((r) => r.available).length

  function pick(c: string) {
    setFavOnly(false)
    setParams(c === ALL ? {} : { category: c }, { replace: true })
  }

  return (
    <div className="space-y-4">
      <nav className="flex items-center gap-1.5 text-xs text-slate-400">
        <span>Reports</span>
        <ChevronRight size={12} />
        <span className="font-medium text-slate-600">Back office</span>
      </nav>

      {/* Title on one line; how many reports are live rides beside it. */}
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <div className="flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-1">
          <h1 className="flex items-center gap-2 text-display text-ink">
            <BarChart3 size={26} className="text-brand" /> Back office reports
          </h1>
          <span className="text-sm text-slate-500">
            {live} of {reports.length} reports run on this property's data today
          </span>
        </div>
      </div>

      {/* One row: category chips (seven categories plus Favourites -- too
          many for a segmented track), then the count and search at the right. */}
      <div className="flex flex-wrap items-center gap-2">
        {[ALL, ...categories].map((c) => {
          const n = c === ALL
            ? reports.length
            : reports.filter((r) => r.category === c).length
          const on = !favOnly && category === c
          return (
            <button key={c} onClick={() => pick(c)}
              className={`${FILTER_CHIP} ${on
                ? 'bg-brand text-white'
                : 'border border-slate-200 bg-white text-slate-600 hover:bg-slate-50'}`}>
              {c}
              <span className={on ? 'text-white/75' : 'text-slate-400'}>{n}</span>
            </button>
          )
        })}
        <button onClick={() => setFavOnly((x) => !x)}
          className={`${FILTER_CHIP} ${favOnly
            ? 'bg-brand text-white'
            : 'border border-slate-200 bg-white text-slate-600 hover:bg-slate-50'}`}>
          <Star size={14} className={favOnly ? 'fill-current' : ''} /> Favorites
          <span className={favOnly ? 'text-white/75' : 'text-slate-400'}>
            {favorites.length}
          </span>
        </button>
        <div className="flex min-w-[240px] flex-1 items-center justify-end gap-2">
          {(favOnly || category !== ALL || text !== '') && (
            <span className="whitespace-nowrap text-sm text-slate-500">
              {shown.length} {shown.length === 1 ? 'report' : 'reports'}
              {favOnly ? ' in favorites' : ''}
            </span>
          )}
          <div className="relative min-w-0 max-w-sm flex-1">
            <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <input value={needle} onChange={(e) => setNeedle(e.target.value)}
              aria-label="Search reports"
              placeholder={`Search ${reports.length} reports by name or purpose`}
              className="w-full rounded-lg border border-slate-200 bg-slate-50 py-2 pl-9 pr-3 text-sm outline-none focus:border-brand" />
          </div>
        </div>
      </div>

      {shown.length === 0 ? (
        <div className="rounded-xl border border-slate-100 bg-white px-4 py-14 text-center">
          <p className="text-sm font-medium text-slate-700">No matching reports</p>
          <p className="mt-1 text-xs text-slate-500">
            {favOnly
              ? 'Star a report from its page to keep it here.'
              : 'Try a different search or category.'}
          </p>
        </div>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {shown.map((r) => (
            <Tile key={r.slug} r={r} favorite={favorites.includes(r.slug)} />
          ))}
        </div>
      )}
    </div>
  )
}

function Tile({ r, favorite }: { r: BackOfficeReportInfo; favorite: boolean }) {
  const Icon = CATEGORY_ICON[r.category] ?? FileText
  const body = (
    <>
      <div className="flex items-start justify-between gap-2">
        <span className={`grid h-9 w-9 place-items-center rounded-lg ${
          r.available ? 'bg-brand/10 text-brand' : 'bg-slate-75 text-slate-400'}`}>
          <Icon size={17} />
        </span>
        <span className="flex items-center gap-1.5 text-[11px] font-semibold text-slate-400">
          {favorite && <Star size={12} className="fill-amber-400 text-amber-400" />}
          {r.number > 0 && String(r.number).padStart(2, '0')}
        </span>
      </div>
      <h2 className={`mt-3 text-sm font-semibold ${
        r.available ? 'text-slate-800 group-hover:text-brand' : 'text-slate-400'}`}>
        {r.title}
      </h2>
      <p className={`mt-1 text-xs leading-relaxed ${
        r.available ? 'text-slate-500' : 'text-slate-400'}`}>
        {r.description}
      </p>
      {!r.available && r.planned && (
        <span className="mt-2.5 inline-block rounded bg-slate-75 px-1.5 py-0.5 text-[11px] font-semibold text-slate-500">
          {r.planned}
        </span>
      )}
    </>
  )
  return r.available ? (
    <Link to={reportHref(r)}
      className="group block rounded-xl border border-slate-100 bg-white p-4 transition hover:border-brand/40 hover:shadow-sm">
      {body}
    </Link>
  ) : (
    // Not a link: there is nothing behind it yet. The reason is on hover.
    <div title={r.unavailable_reason ?? undefined}
      className="block cursor-not-allowed rounded-xl border border-dashed border-slate-200 bg-slate-50/60 p-4">
      {body}
    </div>
  )
}
