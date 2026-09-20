import { useState } from 'react'
import Select from '../components/Select'
import { FILTER_SELECT } from '../lib/controls'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import {
  AlertTriangle, BarChart3, CalendarCheck, CalendarDays, Ellipsis, HelpCircle, Loader2, Plus, Search,
  Tag,
} from 'lucide-react'
import SimulateRateDialog from '../components/SimulateRateDialog'
import { fmtDate } from '../lib/dates'
import {
  listRateRules, listManagedRoomTypes, listRatePlans,
  RULE_STATUSES,
  type RateRuleStats,
} from '../api'
import { Crumbs } from '../components/Crumbs'
import PublishRatesDialog from '../components/PublishRatesDialog'
import { useActivePropertyId } from '../hooks/useProperty'

/**
 * Screen 119 — the rate rule list.
 *
 * Creating and editing happen on their own pages (/rates/rules/new and
 * /rates/rules/:ruleId): seven tabs of configuration needs the room, and a rule
 * being written is a task in its own right rather than a preview of a row.
 *
 * Rules layer rather than replace — several can touch one night and priority
 * decides the order, lowest first — so a shared priority over the same dates is
 * flagged here and explained on the rule's own Preview & Conflicts tab.
 */


const select = `${FILTER_SELECT} bg-white outline-none focus:border-brand`

const day = (s: string | null) => {
  if (!s) return '—'
  const d = new Date(`${s.slice(0, 10)}T00:00:00`)
  return fmtDate(d)
}
const STATUS_STYLE: Record<string, string> = {
  published: 'bg-emerald-100 text-emerald-700',
  scheduled: 'bg-sky-100 text-sky-700',
  draft: 'bg-slate-200 text-slate-600',
  paused: 'bg-amber-100 text-amber-800',
  inactive: 'bg-slate-100 text-slate-500',
}

/* -------------------------------------------------------------------- page --- */
export default function RateRules() {
  const propertyId = useActivePropertyId()
  const navigate = useNavigate()
  const [search, setSearch] = useState('')
  const [ruleStatus, setRuleStatus] = useState('')
  const [ratePlanId, setRatePlanId] = useState('')
  const [roomTypeId, setRoomTypeId] = useState('')
  const [month, setMonth] = useState('')
  const [simulating, setSimulating] = useState(false)
  const [publishing, setPublishing] = useState(false)
  const [menuFor, setMenuFor] = useState<string | null>(null)

  const filters = {
    search: search || undefined, status: ruleStatus || undefined,
    rate_plan_id: ratePlanId || undefined, room_type_id: roomTypeId || undefined,
    month: month || undefined,
  }
  const q = useQuery({
    queryKey: ['rateRules', propertyId, filters],
    queryFn: () => listRateRules(propertyId, filters), enabled: propertyId !== '',
  })
  const typesQ = useQuery({
    queryKey: ['managedRoomTypes', propertyId],
    queryFn: () => listManagedRoomTypes(propertyId), enabled: propertyId !== '',
  })
  const plansQ = useQuery({
    queryKey: ['ratePlans', propertyId, {}],
    queryFn: () => listRatePlans(propertyId), enabled: propertyId !== '',
  })

  const rules = q.data?.items ?? []
  const stats: RateRuleStats | undefined = q.data?.stats
  const roomTypes = (typesQ.data ?? []).map((t) => ({ id: t.id, name: t.name }))
  const ratePlans = (plansQ.data?.items ?? []).map(
    (p) => ({ id: p.id, code: p.code, name: p.name }))

  return (
    <div className="space-y-4">
      <div className="space-y-1">
        <Crumbs title="Rate Rules" trail={[{ label: 'Revenue', to: '/rates' },
          { label: 'Rate Rules' }]} />
        <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
          <h1 className="flex items-center gap-2 text-display text-ink">
            <Tag size={26} className="text-brand" /> Rate Rules
          </h1>
          <div className="flex flex-wrap items-center gap-2">
            {/* The step that turns rules into prices. Beside Simulate because
                the two answer the same question at different scales: what would
                one night cost, and what would every night cost. */}
            <button onClick={() => setPublishing(true)}
              className="flex items-center gap-1.5 whitespace-nowrap rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-600 hover:bg-slate-50">
              <CalendarCheck size={15} /> Publish to Calendar
            </button>
            <button onClick={() => setSimulating(true)} disabled={roomTypes.length === 0}
              className="flex items-center gap-1.5 whitespace-nowrap rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-600 hover:bg-slate-50 disabled:opacity-40">
              <BarChart3 size={15} /> Simulate Rate
            </button>
            <button onClick={() => navigate('/rates/rules/new')}
              className="flex items-center gap-1.5 whitespace-nowrap rounded-lg bg-brand px-3 py-2 text-sm font-medium text-white hover:bg-brand-dark disabled:opacity-40">
              <Plus size={15} /> Create Rate Rule
            </button>
          </div>
        </div>
      </div>

      {stats && stats.conflicts > 0 && (
        <p className="flex items-start gap-2 rounded-lg bg-amber-50 px-4 py-3 text-sm text-amber-900">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>
            {stats.conflicts} conflict{stats.conflicts === 1 ? '' : 's'}: published
            rules sharing a priority over the same dates. Open a rule's
            <strong className="font-semibold"> Preview &amp; Conflicts</strong> tab
            to see which, and give one a different priority.
          </span>
        </p>
      )}

      <div className="space-y-3">
          {/* One row: selects and the month on the left, search at the right
              end as on Reservations. The month is a single month, not a
              from/to pair, so it stays a native month field. */}
          <div className="flex flex-wrap items-center gap-2">
            <Select blankIsChoice aria-label="Status" className={select} value={ruleStatus}
              onChange={(e) => setRuleStatus(e.target.value)}>
              <option value="">All Statuses</option>
              {RULE_STATUSES.map((s) => (
                <option key={s.code} value={s.code}>{s.label}</option>
              ))}
            </Select>
            <Select blankIsChoice aria-label="Rate plan" className={select} value={ratePlanId}
              onChange={(e) => setRatePlanId(e.target.value)}>
              <option value="">All Rate Plans</option>
              {ratePlans.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </Select>
            <Select blankIsChoice aria-label="Room type" className={select} value={roomTypeId}
              onChange={(e) => setRoomTypeId(e.target.value)}>
              <option value="">All Room Types</option>
              {roomTypes.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </Select>
            <span className="flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2">
              <CalendarDays size={14} className="text-slate-400" />
              <input type="month" value={month} aria-label="Month"
                onChange={(e) => setMonth(e.target.value)}
                className="border-0 bg-transparent text-sm text-slate-700 outline-none" />
            </span>
            <div className="flex min-w-[240px] flex-1 items-center justify-end gap-2">
              <div className="relative min-w-0 max-w-sm flex-1">
                <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                <input value={search} aria-label="Search rules" placeholder="Search by rule name..."
                  onChange={(e) => setSearch(e.target.value)}
                  className="w-full rounded-lg border border-slate-200 bg-slate-50 py-2 pl-9 pr-3 text-sm outline-none focus:border-brand" />
              </div>
            </div>
          </div>

          <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
            <table className="w-full min-w-[780px] text-left">
              <thead>
                <tr className="border-b border-slate-200 bg-slate-50/60">
                  {['Rule Name', 'Applies To', 'Validity', 'Priority', 'Status',
                    'Updated On', ''].map((h) => (
                    <th key={h} className="px-4 py-3 text-sm font-semibold text-slate-600">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {q.isLoading && (
                  <tr><td colSpan={7} className="px-4 py-12 text-center text-slate-400">
                    <Loader2 className="mx-auto h-5 w-5 animate-spin" />
                  </td></tr>
                )}
                {!q.isLoading && rules.length === 0 && (
                  <tr><td colSpan={7} className="px-4 py-12 text-center text-sm text-slate-500">
                    No rules match these filters.
                  </td></tr>
                )}
                {rules.map((r) => (
                  <tr key={r.id}
                    onClick={() => navigate(`/rates/rules/${r.id}`)}
                    className="cursor-pointer border-b border-slate-100 align-top last:border-0 hover:bg-slate-50">
                    <td className="px-4 py-3">
                      <p className="font-semibold text-slate-800">
                        {r.name}
                        {r.conflict_count > 0 && (
                          <AlertTriangle className="ml-1.5 inline h-4 w-4 text-amber-500"
                            aria-label="Has a conflict" />
                        )}
                      </p>
                      {r.description && (
                        <p className="text-sm text-slate-500">{r.description}</p>
                      )}
                    </td>
                    <td className="px-4 py-3 text-sm">
                      <p className="text-slate-700">{r.applies_to_label}</p>
                      <p className="text-slate-500">{r.rate_plan_label}</p>
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-sm">
                      <p className="text-slate-700">
                        {day(r.date_from)} – {day(r.date_to)}
                      </p>
                      <p className="text-slate-500">{r.weekdays_label}</p>
                    </td>
                    <td className="px-4 py-3 text-sm tabular-nums text-slate-700">
                      {r.priority}
                    </td>
                    <td className="px-4 py-3">
                      <span className={`rounded-full px-3 py-1 text-xs font-semibold ${
                        STATUS_STYLE[r.status] ?? 'bg-slate-75 text-slate-600'}`}>
                        {r.status_label}
                      </span>
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-sm">
                      <p className="text-slate-700">{day(r.updated_at)}</p>
                      {r.updated_by_name && (
                        <p className="text-slate-500">by {r.updated_by_name}</p>
                      )}
                    </td>
                    <td className="px-4 py-3 text-right"
                      onClick={(e) => e.stopPropagation()}>
                      <div className="relative">
                        <button onClick={() => setMenuFor(menuFor === r.id ? null : r.id)}
                          aria-label={`Actions for ${r.name}`}
                          className="grid h-8 w-8 place-items-center rounded-lg text-slate-400 hover:bg-slate-100">
                          <Ellipsis className="h-5 w-5" />
                        </button>
                        {menuFor === r.id && (
                          <div className="absolute right-0 z-20 mt-1 w-40 overflow-hidden rounded-lg border border-slate-200 bg-white shadow-lg">
                            <button
                              onClick={() => { setMenuFor(null); navigate(`/rates/rules/${r.id}`) }}
                              className="block w-full px-4 py-2 text-left text-sm text-slate-700 hover:bg-slate-50">
                              Edit
                            </button>
                            <button
                              onClick={() => { setMenuFor(null); setSimulating(true) }}
                              className="block w-full px-4 py-2 text-left text-sm text-slate-700 hover:bg-slate-50">
                              Simulate
                            </button>
                          </div>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-sm text-slate-500">
              Showing {rules.length} of {stats?.total ?? 0} rules
            </p>
            <p className="flex items-center gap-1 text-xs text-slate-400">
              <HelpCircle className="h-3.5 w-3.5" />
              Rules layer in priority order — lower number applies first.
            </p>
          </div>
      </div>

      {simulating && (
        <SimulateRateDialog propertyId={propertyId} roomTypes={roomTypes}
          ratePlans={ratePlans} onClose={() => setSimulating(false)} />
      )}

      {publishing && (
        <PublishRatesDialog propertyId={propertyId}
          onClose={() => setPublishing(false)} />
      )}
    </div>
  )
}
