import { useEffect, useState } from 'react'
import Select from '../components/Select'
import { useQuery } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import {
  ShieldCheck, Download, Loader2, ShieldAlert, X, User, Fingerprint, Copy, Search,
} from 'lucide-react'
import { FILTER_SELECT } from '../lib/controls'
import {
  getAuditEvents, getAuditEvent, getAuditActions,
  type AuditEvent, type AuditEventDetail,
} from '../api'

function riskBadge(risk: string) {
  const map: Record<string, string> = {
    'Permission Changed': 'bg-purple-50 text-purple-600',
    'Sensitive Data Viewed': 'bg-amber-50 text-amber-600',
    Export: 'bg-blue-50 text-blue-600',
    Refund: 'bg-rose-50 text-rose-600',
    Elevated: 'bg-orange-50 text-orange-600',
    Normal: 'bg-emerald-50 text-emerald-600',
  }
  return map[risk] ?? 'bg-slate-75 text-slate-500'
}

export default function AuditLog() {
  const [action, setAction] = useState('')
  const [entity, setEntity] = useState('')
  const [query, setQuery] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(null)

  // Another screen can hand this one a scope: ?entity_id=<id>[,<id>...] with an
  // optional ?scope_label= saying, in words, what those ids are. One thing in
  // the system is often several rows — a reservation's trail is the reservation
  // plus its instalments — so the filter takes a set, not a single id.
  const [params, setParams] = useSearchParams()
  const entityIds = params.get('entity_id') ?? ''
  const scopeLabel = params.get('scope_label') ?? ''

  const actionsQ = useQuery({ queryKey: ['auditActions'], queryFn: getAuditActions, retry: false })
  const eventsQ = useQuery({
    queryKey: ['auditEvents', action, entity, query, entityIds],
    queryFn: () => getAuditEvents({ action: action || undefined, entity_type: entity || undefined, query: query || undefined, entity_id: entityIds || undefined }),
    retry: false,
  })
  const detailQ = useQuery({
    queryKey: ['auditEvent', selectedId],
    queryFn: () => getAuditEvent(selectedId as string),
    enabled: !!selectedId,
  })

  const denied = eventsQ.isError && [401, 403].includes((eventsQ.error as { response?: { status?: number } })?.response?.status ?? 0)
  const events = eventsQ.data ?? []
  const entityTypes = Array.from(new Set(events.map((e) => e.entity_type)))

  useEffect(() => {
    if (!events.length) { setSelectedId(null); return }
    if (!selectedId || !events.some((e) => e.id === selectedId)) setSelectedId(events[0].id)
  }, [events, selectedId])

  if (denied) return <div className="flex items-center gap-2 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700"><ShieldAlert size={16} /> You do not have permission to view the audit log.</div>

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <h1 className="flex items-center gap-2 text-display text-ink"><ShieldCheck size={26} className="text-brand" /> Activity &amp; Audit Log</h1>
        <div className="flex flex-wrap items-center gap-2">
          <button disabled title="Export — coming soon" className="flex items-center gap-1.5 whitespace-nowrap rounded-lg bg-brand px-3 py-2 text-sm font-medium text-white hover:bg-brand-dark disabled:opacity-40"><Download size={15} /> Export Authorized Audit Report</button>
        </div>
      </div>

      {entityIds && (
        <div className="flex flex-wrap items-center gap-2 rounded-xl bg-brand-light px-4 py-3 text-sm text-slate-700">
          <Fingerprint size={16} className="text-brand" />
          <span>
            Showing activity for{' '}
            <strong className="font-semibold">{scopeLabel || 'the selected record'}</strong>
            {' '}only.
          </span>
          <button
            onClick={() => setParams({})}
            className="ml-auto font-semibold text-brand hover:underline">
            Show all activity
          </button>
        </div>
      )}

      {/* Filters: one row, no captions or card; search at the right. The
          event count already heads the table below. */}
      <div className="flex flex-wrap items-center gap-2">
        <Filter label="Action" value={action} onChange={setAction} options={actionsQ.data ?? []} all="All Actions" />
        <Filter label="Module" value={entity} onChange={setEntity} options={entityTypes} all="All Modules" />
        <div className="flex min-w-[240px] flex-1 items-center justify-end gap-2">
          <div className="relative min-w-0 max-w-sm flex-1">
            <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search actor, action, entity…" aria-label="Search audit events" className="w-full rounded-lg border border-slate-200 bg-slate-50 py-2 pl-9 pr-3 text-sm outline-none focus:border-brand" />
          </div>
        </div>
      </div>

      <div className="grid gap-5 xl:grid-cols-[1fr_400px]">
        {/* Events table */}
        <div className="overflow-hidden rounded-2xl border border-slate-100 bg-white shadow-sm">
          <div className="border-b border-slate-100 px-4 py-3 text-sm text-slate-500">Showing {events.length} events</div>
          {eventsQ.isLoading && <div className="p-8 text-center text-sm text-slate-400"><Loader2 size={16} className="mx-auto animate-spin" /></div>}
          {events.length === 0 && eventsQ.data && <div className="p-8 text-center text-sm text-slate-400">No audit events match your filters.</div>}
          {events.length > 0 && (
            <table className="w-full text-sm">
              <thead><tr className="border-b border-slate-100 bg-slate-50/60 text-left text-sm text-slate-600">
                <th className="px-4 py-2 font-semibold">Timestamp</th><th className="px-4 py-2 font-semibold">Actor</th><th className="px-4 py-2 font-semibold">Action</th><th className="px-4 py-2 font-semibold">Entity</th><th className="px-4 py-2 font-semibold">Summary</th><th className="px-4 py-2 font-semibold">Risk</th><th className="px-4 py-2 font-semibold">Result</th>
              </tr></thead>
              <tbody className="divide-y divide-slate-50">
                {events.map((e: AuditEvent) => (
                  <tr key={e.id} onClick={() => setSelectedId(e.id)} className={`cursor-pointer ${selectedId === e.id ? 'bg-brand-light' : 'hover:bg-slate-50'}`}>
                    <td className="px-4 py-2.5 text-xs text-slate-500">{e.occurred_at}</td>
                    <td className="px-4 py-2.5 text-slate-700">{e.actor_subject ?? '—'}</td>
                    <td className="px-4 py-2.5 text-slate-600">{e.action}</td>
                    <td className="px-4 py-2.5 text-slate-500">{e.entity_type}{e.entity_id ? ` #${e.entity_id.slice(0, 8)}` : ''}</td>
                    <td className="px-4 py-2.5 text-xs text-slate-500">{e.summary}</td>
                    <td className="px-4 py-2.5"><span className={`rounded-full px-2 py-0.5 text-xs font-medium ${riskBadge(e.risk)}`}>{e.risk}</span></td>
                    <td className="px-4 py-2.5"><span className="rounded-full bg-emerald-50 px-2 py-0.5 text-xs font-medium text-emerald-600">{e.result}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {/* Event Details */}
        <div className="rounded-2xl border border-slate-100 bg-white p-5 shadow-sm">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-semibold text-ink">Event Details</h2>
            {selectedId && <button onClick={() => setSelectedId(null)} className="text-slate-400 hover:text-slate-600"><X size={16} /></button>}
          </div>
          {!selectedId ? <div className="py-10 text-center text-sm text-slate-400">Select an event to view details.</div> :
            detailQ.isLoading || !detailQ.data ? <div className="py-10 text-center text-sm text-slate-400"><Loader2 size={16} className="mx-auto animate-spin" /></div> :
            <Detail d={detailQ.data} />}
        </div>
      </div>
    </div>
  )
}

function Detail({ d }: { d: AuditEventDetail }) {
  return (
    <div className="mt-3 space-y-3 text-sm">
      <div className="flex items-center justify-between">
        <span className={`rounded-full px-2.5 py-1 text-xs font-medium ${riskBadge(d.risk)}`}>{d.risk}</span>
        <span className="text-xs text-slate-400">{d.occurred_at}</span>
      </div>
      <DRow icon={<User size={14} />} label="Actor" value={d.actor_subject ?? '—'} />
      <DRow label="Action" value={d.action} />
      <DRow label="Module" value={d.entity_type} />
      <DRow label="Entity" value={d.entity_id ?? '—'} />
      <DRow label="Result" value={d.result} />
      {d.reason && <DRow label="Reason" value={d.reason} />}
      <DRow icon={<Fingerprint size={14} />} label="Correlation ID" value={d.correlation_id ?? '—'} mono />

      {(d.before || d.after) && (
        <div>
          <div className="mb-1 text-xs font-medium text-slate-500">Changes (Redacted)</div>
          <div className="grid grid-cols-2 gap-2">
            <JsonBlock title="Before" data={d.before} />
            <JsonBlock title="After" data={d.after} />
          </div>
        </div>
      )}
    </div>
  )
}

function DRow({ icon, label, value, mono }: { icon?: React.ReactNode; label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <span className="flex items-center gap-1.5 text-xs text-slate-400">{icon}{label}</span>
      <span className={`text-right text-slate-700 ${mono ? 'font-mono text-xs' : ''} break-all`}>{value}</span>
    </div>
  )
}

function JsonBlock({ title, data }: { title: string; data?: Record<string, unknown> | null }) {
  return (
    <div className="rounded-lg bg-slate-50 p-2">
      <div className="mb-1 flex items-center justify-between text-xs text-slate-400">{title}<Copy size={11} /></div>
      <pre className="max-h-48 overflow-auto text-[10px] leading-relaxed text-slate-600">{data ? JSON.stringify(data, null, 2) : '—'}</pre>
    </div>
  )
}

function Filter({ label, value, onChange, options, all }: { label: string; value: string; onChange: (v: string) => void; options: string[]; all: string }) {
  return (
    <Select blankIsChoice value={value} onChange={(e) => onChange(e.target.value)} aria-label={label}
      className={`${FILTER_SELECT} bg-white outline-none focus:border-brand`}>
      <option value="">{all}</option>
      {options.map((o) => <option key={o} value={o}>{o}</option>)}
    </Select>
  )
}
