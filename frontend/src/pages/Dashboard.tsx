import {
  Bed,
  Users,
  LogOut,
  DoorOpen,
  Coins,
  Plus,
  ShoppingBag,
  TrendingUp,
  TrendingDown,
  Loader2,
  AlertTriangle,
} from 'lucide-react'
import { fmtDayMonth, fmtWeekday } from '../lib/dates'
import { Broom } from '../components/icons'
import type { LucideIcon } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { getDashboard, type DashboardData } from '../api'
import { useAuth } from '../auth/AuthContext'
import { useActivePropertyId } from '../hooks/useProperty'

/* ---------------- KPI cards ---------------- */
interface Kpi {
  label: string
  value: string
  delta?: string
  positive?: boolean
  icon: LucideIcon
  tile: string
  tint: string
}

/** A delta as the card prints it: "+6%", "-3", or nothing at all.
 *
 *  `null` (or an absent block, from a gateway that does not send one yet)
 *  means there is no comparable figure for yesterday, and the card then shows
 *  no indicator rather than an authoritative-looking "+0".
 */
function trend(
  value: number | null | undefined,
  unit: '%' | '',
): Pick<Kpi, 'delta' | 'positive'> {
  if (value == null || value === 0) return {}
  const sign = value > 0 ? '+' : ''
  return { delta: `${sign}${value}${unit}`, positive: value > 0 }
}

function buildKpis(d: DashboardData): Kpi[] {
  const money = new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency: 'INR',
    maximumFractionDigits: 0,
  })
  const t = d.deltas
  return [
    { label: 'Occupancy', value: `${d.kpis.occupancy_pct}%`, ...trend(t?.occupancy_pct, '%'), icon: Bed, tile: 'bg-teal-100 text-teal-700', tint: '#0f766e' },
    { label: 'Arrivals', value: String(d.kpis.arrivals), ...trend(t?.arrivals, ''), icon: Users, tile: 'bg-blue-100 text-blue-600', tint: '#2563eb' },
    { label: 'Departures', value: String(d.kpis.departures), ...trend(t?.departures, ''), icon: LogOut, tile: 'bg-red-100 text-red-500', tint: '#ef4444' },
    { label: 'Available Rooms', value: String(d.kpis.available_rooms), icon: DoorOpen, tile: 'bg-amber-100 text-amber-600', tint: '#d97706' },
    { label: "Today's Revenue", value: money.format(d.kpis.today_revenue), ...trend(t?.today_revenue, '%'), icon: Coins, tile: 'bg-teal-100 text-teal-700', tint: '#0f766e' },
  ]
}

function Sparkline({ color, points }: { color: string; points: number[] }) {
  const max = Math.max(1, ...points)
  const pts = points
    .map((v, i) => `${(i / (points.length - 1)) * 100},${28 - (v / max) * 24}`)
    .join(' ')
  return (
    <svg viewBox="0 0 100 32" className="h-8 w-24" preserveAspectRatio="none">
      <polyline points={pts} fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

function KpiCard({ k, spark }: { k: Kpi; spark: number[] }) {
  return (
    <div className="flex h-full flex-col rounded-2xl border border-slate-100 bg-white p-4 shadow-sm">
      <div className="flex items-center gap-2.5">
        <span className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-xl ${k.tile}`}>
          <k.icon size={20} />
        </span>
        <span className="text-base font-semibold text-slate-800">{k.label}</span>
      </div>
      <div className="mt-3 flex items-end justify-between gap-2">
        <div className="text-display leading-none text-slate-800">{k.value}</div>
        <Sparkline color={k.tint} points={spark} />
      </div>
      <div className="mt-2 h-4">
        {k.delta && (
          <div className={`flex items-center gap-1 text-xs font-semibold ${k.positive ? 'text-emerald-600' : 'text-red-500'}`}>
            {k.positive ? <TrendingUp size={13} /> : <TrendingDown size={13} />}
            {k.delta}
          </div>
        )}
      </div>
    </div>
  )
}

/* ---------------- Occupancy & Revenue chart ---------------- */
function OccupancyChart({ chart }: { chart: DashboardData['chart'] }) {
  const W = 620, H = 240, padL = 34, padR = 44, padT = 12, padB = 34
  const plotW = W - padL - padR
  const plotH = H - padT - padB
  const n = Math.max(chart.length, 1)
  const slot = plotW / n
  const barW = 22
  // Round the revenue axis up to a readable step so its labels are round
  // numbers and the line does not run along the top of the plot.
  const peak = Math.max(1, ...chart.map((c) => c.revenue))
  const mag = Math.pow(10, Math.floor(Math.log10(peak / 5)))
  const step = (([1, 2, 2.5, 5, 10].find((m) => m * mag >= peak / 5) ?? 10) * mag)
  const maxRev = step * 5
  const rupees = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 })
  const yOcc = (v: number) => padT + plotH * (1 - v / 100)
  const yRev = (v: number) => padT + plotH * (1 - v / maxRev)
  const label = (iso: string) => ({ d: fmtDayMonth(iso), w: fmtWeekday(iso) })
  const linePts = chart.map((c, i) => `${padL + slot * i + slot / 2},${yRev(c.revenue)}`).join(' ')

  return (
    <div className="rounded-2xl border border-slate-100 bg-white p-5 shadow-sm">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <h2 className="flex items-center gap-2 text-lg font-semibold text-ink">
          <ShoppingBag size={18} className="text-brand" />
          Occupancy &amp; Revenue <span className="font-normal text-slate-400">(Last 7 Days)</span>
        </h2>
        <div className="flex items-center gap-4 text-xs text-slate-500">
          <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-full bg-teal-600" /> Occupancy %</span>
          <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-full bg-amber-400" /> Revenue (₹)</span>
        </div>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="h-56 w-full">
        {[0, 20, 40, 60, 80, 100].map((t, i) => (
          <g key={t}>
            <line x1={padL} x2={W - padR} y1={yOcc(t)} y2={yOcc(t)} stroke="#e7edf4" strokeWidth="1" />
            <text x={padL - 6} y={yOcc(t) + 3} textAnchor="end"
              className="fill-slate-600 font-semibold" fontSize="11">{t}%</text>
            {/* Revenue scale, sharing the occupancy gridlines. */}
            <text x={W - padR + 6} y={yOcc(t) + 3} textAnchor="start"
              className="fill-slate-600 font-semibold" fontSize="11">
              {rupees.format(step * i)}
            </text>
          </g>
        ))}
        {chart.map((c, i) => {
          const x = padL + slot * i + slot / 2 - barW / 2
          const y = yOcc(c.occupancy)
          return <rect key={c.date} x={x} y={y} width={barW} height={padT + plotH - y} rx="3" fill="#2dd4bf" fillOpacity="0.85" />
        })}
        {chart.length > 1 && (
          <polyline points={linePts} fill="none" stroke="#f5b942" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
        )}
        {chart.map((c, i) => (
          <circle key={c.date} cx={padL + slot * i + slot / 2} cy={yRev(c.revenue)} r="3" fill="#f5b942" />
        ))}
        {chart.map((c, i) => {
          const l = label(c.date)
          const cx = padL + slot * i + slot / 2
          return (
            <text key={c.date} x={cx} y={H - padB + 14} textAnchor="middle"
              className="fill-slate-700 font-semibold" fontSize="11">
              <tspan x={cx} dy="0">{l.d}</tspan>
              <tspan x={cx} dy="11">{l.w}</tspan>
            </text>
          )
        })}
      </svg>
    </div>
  )
}

/* ---------------- Room Status donut ---------------- */
const STATUS_META: { key: string; label: string; color: string }[] = [
  { key: 'occupied', label: 'Occupied', color: '#0f766e' },
  { key: 'available', label: 'Available', color: '#5eead4' },
  { key: 'cleaning', label: 'Cleaning', color: '#fcd9a8' },
  { key: 'maintenance', label: 'Maintenance', color: '#fca5a5' },
]

function Donut({ roomStatus, total }: { roomStatus: Record<string, number>; total: number }) {
  const segs = STATUS_META.map((m) => ({ ...m, value: roomStatus[m.key] ?? 0 }))
  const sum = Math.max(segs.reduce((s, r) => s + r.value, 0), 1)
  const radius = 54
  const circ = 2 * Math.PI * radius
  let offset = 0
  return (
    <div className="rounded-2xl border border-slate-100 bg-white p-5 shadow-sm">
      <h2 className="mb-4 flex items-center gap-2 text-lg font-semibold text-ink">
        <Bed size={18} className="text-brand" /> Room Status
      </h2>
      <div className="flex items-center gap-4">
        <div className="relative h-36 w-36 shrink-0">
          <svg viewBox="0 0 140 140" className="h-full w-full -rotate-90">
            {segs.map((r) => {
              const len = (r.value / sum) * circ
              const seg = (
                <circle key={r.key} cx="70" cy="70" r={radius} fill="none" stroke={r.color} strokeWidth="16"
                  strokeDasharray={`${len} ${circ - len}`} strokeDashoffset={-offset} />
              )
              offset += len
              return seg
            })}
          </svg>
          <div className="absolute inset-0 flex flex-col items-center justify-center">
            <div className="text-2xl font-bold text-slate-800">{total}</div>
            <div className="text-[11px] text-slate-400">Total Rooms</div>
          </div>
        </div>
        <ul className="flex-1 space-y-2 text-sm">
          {segs.map((r) => (
            <li key={r.key} className="flex items-center justify-between">
              <span className="flex items-center gap-2 text-slate-600">
                <span className="h-2.5 w-2.5 rounded-full" style={{ background: r.color }} />
                {r.label}
              </span>
              <span className="font-semibold text-slate-800">{r.value}</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  )
}

/* ---------------- Today's Arrivals ---------------- */
function ArrivalsTable({ arrivals }: { arrivals: DashboardData['todays_arrivals'] }) {
  return (
    <div className="rounded-2xl border border-slate-100 bg-white p-5 shadow-sm">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="flex items-center gap-2 text-lg font-semibold text-ink">
          <Users size={18} className="text-brand" /> Today's Arrivals
        </h2>
        <button className="text-sm font-medium text-brand">View All</button>
      </div>
      {arrivals.length === 0 ? (
        <div className="py-8 text-center text-sm text-slate-400">No arrivals for today.</div>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-sm text-slate-600">
              <th className="pb-2 font-semibold">Guest Name</th>
              <th className="pb-2 font-semibold">Room No.</th>
              <th className="pb-2 font-semibold">Arrival Time</th>
              <th className="pb-2 font-semibold">Reservation No.</th>
              <th className="pb-2 font-semibold">Status</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-50">
            {arrivals.map((a) => (
              <tr key={a.reservation_no} className="text-slate-700">
                <td className="py-2.5">
                  {a.guest_name
                    ?? <span className="italic text-slate-400">No guest on file</span>}
                </td>
                <td className="py-2.5">{a.room_no}</td>
                <td className="py-2.5">{a.arrival_time}</td>
                <td className="py-2.5">{a.reservation_no}</td>
                <td className="py-2.5">
                  <span className={`rounded-full px-2.5 py-1 text-xs font-medium ${
                    a.status === 'Checked In' ? 'bg-emerald-50 text-emerald-600' : 'bg-blue-50 text-blue-600'
                  }`}>
                    {a.status}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

/* ---------------- Housekeeping Status ---------------- */
function Housekeeping({ hk, total }: { hk: Record<string, number>; total: number }) {
  const t = Math.max(total, 1)
  const rows = [
    { label: 'Rooms Cleaned', n: hk.rooms_cleaned ?? 0, color: 'bg-emerald-400', dot: 'bg-emerald-400' },
    { label: 'In Progress', n: hk.in_progress ?? 0, color: 'bg-amber-300', dot: 'bg-amber-300' },
    { label: 'Pending', n: hk.pending ?? 0, color: 'bg-orange-300', dot: 'bg-orange-400' },
    { label: 'Maintenance', n: hk.maintenance ?? 0, color: 'bg-red-300', dot: 'bg-red-500' },
  ]
  return (
    <div className="rounded-2xl border border-slate-100 bg-white p-5 shadow-sm">
      <div className="mb-4 flex items-center justify-between">
        <h2 className="flex items-center gap-2 text-lg font-semibold text-ink">
          <Broom size={18} className="text-brand" /> Housekeeping Status
        </h2>
        <button className="text-sm font-medium text-brand">View All</button>
      </div>
      <ul className="space-y-3.5">
        {rows.map((h) => {
          const pct = Math.round((h.n / t) * 100)
          return (
            <li key={h.label} className="flex items-center gap-3 text-sm">
              <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${h.dot}`} />
              <span className="w-28 shrink-0 text-slate-600">{h.label}</span>
              <span className="w-14 shrink-0 text-slate-500">{h.n} / {total}</span>
              <div className="h-2 flex-1 overflow-hidden rounded-full bg-slate-100">
                <div className={`h-full rounded-full ${h.color}`} style={{ width: `${pct}%` }} />
              </div>
              <span className="w-10 shrink-0 text-right text-slate-500">{pct}%</span>
            </li>
          )
        })}
      </ul>
    </div>
  )
}

/* ---------------- Page ---------------- */
export default function Dashboard() {
  const hour = new Date().getHours()
  const greeting = hour < 12 ? 'Good morning' : hour < 17 ? 'Good afternoon' : 'Good evening'
  // The signed-in user, not a name baked in at build time -- this greeted
  // every user of every tenant as "Kishore". First name only: it is a
  // greeting, and "Good evening, Ghana Syam" reads like a summons.
  const { session } = useAuth()
  const userName = session?.display_name?.trim().split(/\s+/)[0] ?? ''

  const navigate = useNavigate()
  const propertyId = useActivePropertyId()
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['dashboard', propertyId],
    queryFn: () => getDashboard(propertyId),
    enabled: propertyId !== '',
    refetchInterval: 30000,
  })

  const occSpark = data?.chart.map((c) => c.occupancy) ?? []
  const revSpark = data?.chart.map((c) => c.revenue) ?? []

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="relative flex items-center justify-between overflow-hidden rounded-lg py-2">
        <div aria-hidden className="pointer-events-none absolute inset-y-0 right-0 w-2/5 bg-[length:auto_130%] bg-right bg-no-repeat opacity-70" style={{ backgroundImage: 'url(/header-palms.png)' }} />
        <div aria-hidden className="pointer-events-none absolute inset-0" style={{ background: // Must track `canvas` in tailwind.config.js: this fades the header
          // image into the page, so a stale value shows as a band of the wrong
          // grey rather than as nothing at all.
          'linear-gradient(90deg, #fcfdfe 0%, #fcfdfe 34%, rgba(252,253,254,0.35) 55%, rgba(252,253,254,0.75) 82%, #fcfdfe 100%)' }} />
        <div className="relative z-10">
          {/* The same heading every other screen uses. This was Playfair at
              text-4xl/5xl in slate-900 while forty-three other pages are Inter
              at text-3xl in slate-800 — three differences at once, which read
              as a different application rather than as emphasis. A greeting is
              still the most prominent thing on the page at this size; it no
              longer has to change typeface to say so. */}
          <h1 className="text-display text-ink md:text-4xl">{userName ? `${greeting}, ${userName}` : greeting}</h1>
          <p className="mt-2 text-base text-slate-500 md:text-lg">Here's what's happening at your resort today.</p>
        </div>
        <div className="relative z-10 flex items-center gap-6">
          <div className="hidden select-none text-right font-script text-3xl leading-none text-brand lg:block">
            Good Stays<br />Brighter Days
            <div className="ml-auto mt-1 h-[2px] w-24 rounded-full bg-brand/40" />
          </div>
          <button onClick={() => navigate('/reservations/new')} className="flex shrink-0 items-center gap-2 rounded-xl bg-brand px-5 py-3 text-sm font-medium text-white shadow-sm hover:bg-brand/90">
            <Plus size={18} /> New Reservation
          </button>
        </div>
      </div>

      {/* States */}
      {propertyId === '' && !isLoading && (
        <Banner icon={<AlertTriangle size={16} />} tone="warn">
          No property available yet. Create a property in IAM (or seed demo data)
          to load live dashboard metrics.
        </Banner>
      )}
      {isLoading && propertyId !== '' && (
        <Banner icon={<Loader2 size={16} className="animate-spin" />} tone="info">
          Loading live dashboard data…
        </Banner>
      )}
      {isError && (
        <Banner icon={<AlertTriangle size={16} />} tone="error">
          Failed to load dashboard: {(error as Error)?.message}
        </Banner>
      )}

      {data && (
        <>
          <div className="grid grid-cols-2 gap-4 md:grid-cols-3 xl:grid-cols-5">
            {buildKpis(data).map((k, i) => (
              <KpiCard key={k.label} k={k} spark={i === 4 ? revSpark : occSpark} />
            ))}
          </div>

          <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
            <OccupancyChart chart={data.chart} />
            <Donut roomStatus={data.room_status} total={data.total_rooms} />
            <ArrivalsTable arrivals={data.todays_arrivals} />
            <Housekeeping hk={data.housekeeping} total={data.total_rooms} />
          </div>
        </>
      )}
    </div>
  )
}

function Banner({ children, icon, tone }: { children: React.ReactNode; icon: React.ReactNode; tone: 'info' | 'warn' | 'error' }) {
  const cls = {
    info: 'bg-blue-50 text-blue-700',
    warn: 'bg-amber-50 text-amber-700',
    error: 'bg-red-50 text-red-700',
  }[tone]
  return <div className={`flex items-center gap-2 rounded-xl px-4 py-3 text-sm ${cls}`}>{icon}{children}</div>
}
