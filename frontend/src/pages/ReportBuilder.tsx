import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  BarChart3, ChevronRight, Download, Loader2, Play, Plus, Save, Trash2, X,
} from 'lucide-react'
import {
  builderDatasets, builderValues, deleteSavedReport, listSavedReports,
  runBuilder, saveReport,
  type BuilderDataset, type BuilderDefinition, type BuilderFilter, type BuilderResult,
} from '../api'
import DateField from '../components/DateField'
import { useActivePropertyId, usePropertyToday } from '../hooks/useProperty'
import { datedName, downloadCsv } from '../lib/csv'
import { errorText, inputCls } from '../lib/forms'

/**
 * Report builder — the questions nobody wrote a report for.
 *
 * Choose what to report on, which columns, a date range and filters, and
 * optionally group the rows ("payments by method", "bookings by source").
 * Run it, export it to CSV, or save it to run again later.
 *
 * The server builds the query only from a fixed list of datasets and
 * columns, so nothing typed here can reach the database as SQL
 * (report_builder_routes.py).
 */

const OP_LABEL: Record<string, string> = {
  eq: 'is', neq: 'is not', contains: 'contains', gte: 'from', lte: 'up to',
}

function fmt(kind: string, v: unknown): string {
  if (v === null || v === undefined || v === '') return '—'
  if (kind === 'money') {
    return new Intl.NumberFormat('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(Number(v))
  }
  if (kind === 'date') {
    const d = new Date(`${String(v).slice(0, 10)}T00:00:00`)
    return Number.isNaN(d.getTime()) ? String(v) : d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' })
  }
  return String(v)
}

function monthAgo(today: string): string {
  const d = new Date(`${today}T00:00:00`)
  d.setMonth(d.getMonth() - 1)
  return d.toISOString().slice(0, 10)
}

export default function ReportBuilder() {
  const propertyId = useActivePropertyId()
  const today = usePropertyToday()
  const qc = useQueryClient()
  const dsQ = useQuery({ queryKey: ['builder-datasets'], queryFn: builderDatasets })
  const savedQ = useQuery({
    queryKey: ['builder-saved', propertyId], queryFn: () => listSavedReports(propertyId),
    enabled: propertyId !== '',
  })

  const [def, setDef] = useState<BuilderDefinition>({
    dataset: '', columns: [], filters: [], group_by: null, sort: null, descending: false,
  })
  const [result, setResult] = useState<BuilderResult | null>(null)
  const [err, setErr] = useState('')
  const [name, setName] = useState('')
  const [note, setNote] = useState('')

  const ds: BuilderDataset | undefined = dsQ.data?.find((d) => d.key === def.dataset)

  // Start on the first dataset, with its suggested columns and the last month.
  useEffect(() => {
    if (!def.dataset && dsQ.data?.length && today) {
      const first = dsQ.data[0]
      setDef((d) => ({ ...d, dataset: first.key, columns: first.default_columns,
        date_from: monthAgo(today), date_to: today }))
    }
  }, [dsQ.data, today, def.dataset])

  function pickDataset(key: string) {
    const next = dsQ.data?.find((d) => d.key === key)
    if (!next) return
    setDef((d) => ({ ...d, dataset: key, columns: next.default_columns, filters: [],
      group_by: null, sort: null }))
    setResult(null)
  }

  const run = useMutation({
    mutationFn: () => runBuilder(propertyId, def),
    onSuccess: (r) => { setErr(''); setResult(r) },
    onError: (e) => setErr(errorText(e, 'The report could not run.')),
  })
  const save = useMutation({
    mutationFn: () => saveReport(propertyId, name.trim(), def),
    onSuccess: () => {
      setNote(`Saved as "${name.trim()}".`)
      qc.invalidateQueries({ queryKey: ['builder-saved', propertyId] })
    },
    onError: (e) => setErr(errorText(e, 'Could not save the report.')),
  })
  const remove = useMutation({
    mutationFn: (id: string) => deleteSavedReport(propertyId, id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['builder-saved', propertyId] }),
  })

  function exportCsv() {
    if (!result) return
    downloadCsv(datedName(`report-${def.dataset}`), result.columns.map((c) => c.label),
      result.rows.map((r) => result.columns.map((c) => (r[c.key] ?? '') as string | number)))
  }

  const groupable = useMemo(() => ds?.columns.filter((c) => c.kind === 'text' || c.kind === 'date') ?? [], [ds])

  if (dsQ.isLoading) return <Loader2 className="animate-spin text-slate-300" />

  return (
    <div className="space-y-4">
      <nav className="flex items-center gap-1.5 text-xs text-slate-400">
        <Link to="/reports" className="hover:text-brand">Reports</Link>
        <ChevronRight size={12} />
        <span className="font-medium text-slate-600">Report builder</span>
      </nav>
      <h1 className="flex items-center gap-2 text-display text-ink">
        <BarChart3 size={26} className="text-brand" /> Report builder
      </h1>

      <div className="grid gap-4 xl:grid-cols-[260px_1fr]">
        <aside className="space-y-2 rounded-2xl border border-slate-100 bg-white p-4">
          <p className="text-sm font-semibold text-ink">Saved reports</p>
          {(savedQ.data ?? []).length === 0 && <p className="text-xs text-slate-400">None yet. Build one and save it.</p>}
          {(savedQ.data ?? []).map((s) => (
            <div key={s.id} className="group flex items-center justify-between gap-2 rounded-lg px-2 py-1.5 hover:bg-slate-50">
              <button onClick={() => { setDef(s.definition); setName(s.name); setResult(null); setNote('') }}
                className="min-w-0 truncate text-left text-sm text-slate-700 hover:text-brand">{s.name}</button>
              <button onClick={() => remove.mutate(s.id)} aria-label={`Delete ${s.name}`}
                className="text-slate-300 opacity-0 hover:text-red-600 group-hover:opacity-100"><Trash2 size={14} /></button>
            </div>
          ))}
        </aside>

        <div className="space-y-4">
          <div className="space-y-4 rounded-2xl border border-slate-100 bg-white p-5">
            <div className="flex flex-wrap gap-2">
              {dsQ.data?.map((d) => (
                <button key={d.key} onClick={() => pickDataset(d.key)} title={d.description}
                  className={`rounded-full px-4 py-2 text-sm font-semibold ${d.key === def.dataset ? 'bg-ink text-white' : 'border border-slate-200 text-slate-600 hover:border-brand'}`}>
                  {d.label}
                </button>
              ))}
            </div>
            {ds && <p className="text-xs text-slate-500">{ds.description}</p>}

            {ds && (
              <>
                <div>
                  <p className="mb-2 text-sm font-medium text-slate-600">Columns</p>
                  <div className="flex flex-wrap gap-2">
                    {ds.columns.map((c) => {
                      const on = def.columns.includes(c.key)
                      return (
                        <button key={c.key}
                          onClick={() => setDef((d) => ({ ...d, columns: on
                            ? d.columns.filter((k) => k !== c.key) : [...d.columns, c.key] }))}
                          className={`rounded-lg border px-3 py-1.5 text-xs font-medium ${on ? 'border-brand bg-brand-light/40 text-brand-deep' : 'border-slate-200 text-slate-500'}`}>
                          {c.label}
                        </button>
                      )
                    })}
                  </div>
                </div>

                <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                  <label className="text-sm font-medium text-slate-600">
                    From ({ds.columns.find((c) => c.key === ds.date_column)?.label.toLowerCase()})
                    <DateField value={def.date_from ?? ''} onChange={(v) => setDef((d) => ({ ...d, date_from: v || null }))} className="mt-1 w-full" />
                  </label>
                  <label className="text-sm font-medium text-slate-600">
                    To
                    <DateField value={def.date_to ?? ''} onChange={(v) => setDef((d) => ({ ...d, date_to: v || null }))} className="mt-1 w-full" />
                  </label>
                  <label className="text-sm font-medium text-slate-600">
                    Group by
                    <select value={def.group_by ?? ''} onChange={(e) => setDef((d) => ({ ...d, group_by: e.target.value || null, sort: null }))}
                      className={`${inputCls} mt-1`}>
                      <option value="">No grouping</option>
                      {groupable.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}
                    </select>
                  </label>
                  <label className="text-sm font-medium text-slate-600">
                    Sort by
                    <select value={def.sort ?? ''} onChange={(e) => setDef((d) => ({ ...d, sort: e.target.value || null }))}
                      className={`${inputCls} mt-1`}>
                      <option value="">Default</option>
                      {(def.group_by
                        ? [def.group_by, 'count', ...def.columns.filter((k) => ['money', 'number'].includes(ds.columns.find((c) => c.key === k)?.kind ?? ''))]
                        : def.columns).map((k) => (
                        <option key={k} value={k}>{k === 'count' ? 'Count' : ds.columns.find((c) => c.key === k)?.label}</option>
                      ))}
                    </select>
                    <label className="mt-1 flex items-center gap-1.5 text-xs text-slate-500">
                      <input type="checkbox" checked={!!def.descending} onChange={(e) => setDef((d) => ({ ...d, descending: e.target.checked }))} />
                      Largest first
                    </label>
                  </label>
                </div>

                <Filters ds={ds} propertyId={propertyId} filters={def.filters}
                  onChange={(filters) => setDef((d) => ({ ...d, filters }))} />

                <div className="flex flex-wrap items-center gap-2 border-t border-slate-100 pt-4">
                  <button onClick={() => run.mutate()} disabled={run.isPending || def.columns.length === 0}
                    className="flex items-center gap-1.5 rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white hover:bg-brand-dark disabled:opacity-50">
                    {run.isPending ? <Loader2 size={15} className="animate-spin" /> : <Play size={15} />} Run report
                  </button>
                  <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Name to save as"
                    className={`${inputCls} max-w-[220px]`} />
                  <button onClick={() => save.mutate()} disabled={!name.trim() || save.isPending}
                    className="flex items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-40">
                    <Save size={15} /> Save
                  </button>
                  {note && <span className="text-xs text-emerald-700">{note}</span>}
                </div>
              </>
            )}
          </div>

          {err && <p className="rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700">{err}</p>}

          {result && (
            <div className="rounded-2xl border border-slate-100 bg-white">
              <div className="flex flex-wrap items-center justify-between gap-2 px-5 py-3">
                <p className="text-sm text-slate-500">
                  {result.rows.length} row{result.rows.length === 1 ? '' : 's'} · {fmt('date', result.date_from)} to {fmt('date', result.date_to)}
                  {result.truncated && <span className="ml-2 text-amber-700">Showing the first {result.row_limit}; narrow the dates or filters.</span>}
                </p>
                <button onClick={exportCsv} className="flex items-center gap-1.5 text-sm font-semibold text-brand hover:underline">
                  <Download size={15} /> Export CSV
                </button>
              </div>
              <div className="max-h-[560px] overflow-auto">
                <table className="w-full text-sm">
                  <thead className="sticky top-0 bg-slate-50 text-left text-xs font-semibold text-slate-500">
                    <tr>{result.columns.map((c) => (
                      <th key={c.key} className={`whitespace-nowrap px-4 py-2.5 ${['money', 'number'].includes(c.kind) ? 'text-right' : ''}`}>{c.label}</th>
                    ))}</tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {result.rows.map((r, i) => (
                      <tr key={i} className="hover:bg-slate-50">
                        {result.columns.map((c) => (
                          <td key={c.key} className={`whitespace-nowrap px-4 py-2 text-slate-700 ${['money', 'number'].includes(c.kind) ? 'text-right tabular-nums' : ''}`}>
                            {fmt(c.kind, r[c.key])}
                          </td>
                        ))}
                      </tr>
                    ))}
                    {result.rows.length === 0 && (
                      <tr><td colSpan={result.columns.length} className="px-4 py-10 text-center text-slate-400">Nothing matches.</td></tr>
                    )}
                  </tbody>
                  {Object.keys(result.totals).length > 0 && result.rows.length > 0 && (
                    <tfoot className="border-t-2 border-slate-200 bg-slate-50 font-semibold text-ink">
                      <tr>{result.columns.map((c, i) => (
                        <td key={c.key} className={`whitespace-nowrap px-4 py-2.5 ${['money', 'number'].includes(c.kind) ? 'text-right tabular-nums' : ''}`}>
                          {c.key in result.totals ? fmt(c.kind, result.totals[c.key]) : i === 0 ? 'Total' : ''}
                        </td>
                      ))}</tr>
                    </tfoot>
                  )}
                </table>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function Filters({ ds, propertyId, filters, onChange }: {
  ds: BuilderDataset; propertyId: string; filters: BuilderFilter[]
  onChange: (f: BuilderFilter[]) => void
}) {
  const set = (i: number, patch: Partial<BuilderFilter>) =>
    onChange(filters.map((f, n) => (n === i ? { ...f, ...patch } : f)))
  return (
    <div>
      <p className="mb-2 text-sm font-medium text-slate-600">Filters</p>
      <div className="space-y-2">
        {filters.map((f, i) => {
          const col = ds.columns.find((c) => c.key === f.column) ?? ds.columns[0]
          return (
            <div key={i} className="flex flex-wrap items-center gap-2">
              <select value={f.column} className={`${inputCls} w-auto`}
                onChange={(e) => {
                  const c = ds.columns.find((x) => x.key === e.target.value)!
                  set(i, { column: c.key, op: c.ops[0], value: '' })
                }}>
                {ds.columns.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}
              </select>
              <select value={f.op} onChange={(e) => set(i, { op: e.target.value })} className={`${inputCls} w-auto`}>
                {col.ops.map((o) => <option key={o} value={o}>{OP_LABEL[o] ?? o}</option>)}
              </select>
              <FilterValue ds={ds} propertyId={propertyId} column={col} op={f.op}
                value={f.value} onChange={(value) => set(i, { value })} />
              <button onClick={() => onChange(filters.filter((_, n) => n !== i))} aria-label="Remove filter"
                className="rounded p-1 text-slate-300 hover:text-red-600"><X size={16} /></button>
            </div>
          )
        })}
        <button onClick={() => {
          const c = ds.columns[0]
          onChange([...filters, { column: c.key, op: c.ops[0], value: '' }])
        }} className="flex items-center gap-1 text-sm font-semibold text-brand hover:underline">
          <Plus size={14} /> Add a filter
        </button>
      </div>
    </div>
  )
}

function FilterValue({ ds, propertyId, column, op, value, onChange }: {
  ds: BuilderDataset; propertyId: string
  column: BuilderDataset['columns'][number]; op: string; value: string
  onChange: (v: string) => void
}) {
  const choices = useQuery({
    queryKey: ['builder-values', ds.key, column.key, propertyId],
    queryFn: () => builderValues(propertyId, ds.key, column.key),
    enabled: column.choices && (op === 'eq' || op === 'neq') && propertyId !== '',
  })
  if (column.choices && (op === 'eq' || op === 'neq')) {
    return (
      <select value={value} onChange={(e) => onChange(e.target.value)} className={`${inputCls} w-auto min-w-[160px]`}>
        <option value="">Choose…</option>
        {(choices.data ?? []).map((v) => <option key={v} value={v}>{v}</option>)}
      </select>
    )
  }
  if (column.kind === 'date') {
    return <DateField value={value} onChange={onChange} className="w-44" />
  }
  return (
    <input value={value} onChange={(e) => onChange(e.target.value)}
      type={column.kind === 'text' ? 'text' : 'number'}
      className={`${inputCls} w-auto min-w-[160px]`} />
  )
}
