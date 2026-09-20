/**
 * The three tabs the reference names that had no screen behind them.
 *
 * Each is built from data that already exists rather than invented, and each
 * says plainly when it has nothing — an empty panel with no explanation is
 * how a person decides a screen is broken.
 */
import { useQuery } from '@tanstack/react-query'
import {
  AlertTriangle, BedDouble, Brush, Info, Loader2, ShieldCheck, Wrench,
} from 'lucide-react'
import { fmtDate } from '../lib/dates'
import { getReservationTasks, type ReservationFull } from '../api'

/** A stored time as "12:00 PM". */
function hhmm(t?: string | null): string | null {
  if (!t) return null
  const [h, m] = t.split(':').map(Number)
  if (Number.isNaN(h)) return null
  return `${h % 12 === 0 ? 12 : h % 12}:${String(m ?? 0).padStart(2, '0')} ${h < 12 ? 'AM' : 'PM'}`
}

function Card({ title, icon: Icon, children }: {
  title: string
  icon: typeof Info
  children: React.ReactNode
}) {
  return (
    <div className="rounded-2xl border border-slate-100 bg-white p-5">
      <h2 className="flex items-center gap-2 text-sm font-semibold text-ink">
        <Icon size={16} className="text-brand" /> {title}
      </h2>
      <div className="mt-4">{children}</div>
    </div>
  )
}

/* ------------------------------------------------------ stay information -- */

/**
 * What was actually sold: each room, its terms, and the policy behind it.
 *
 * Booking Details answers "how did this booking come to us" — source, segment,
 * who it is for. This answers "what did we agree to provide", which is a
 * different question and the one a duty manager asks.
 */
export function StayInformationTab({ r }: { r: ReservationFull }) {
  const p = r.policy
  return (
    <div className="space-y-4">
      <Card title="Rooms and terms" icon={BedDouble}>
        <div className="-mx-5 overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="px-5 py-2.5 font-medium">Room</th>
                <th className="px-3 py-2.5 font-medium">Stay</th>
                <th className="px-3 py-2.5 font-medium">Expected</th>
                <th className="px-3 py-2.5 font-medium">Occupancy</th>
                <th className="px-3 py-2.5 font-medium">Rate plan</th>
                <th className="px-3 py-2.5 font-medium">Board</th>
                <th className="px-5 py-2.5 font-medium">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {r.units.map((u) => (
                <tr key={u.id}>
                  <td className="px-5 py-3">
                    <span className="font-medium text-ink">
                      {u.room_code ?? 'Not assigned'}
                    </span>
                    <span className="block text-xs text-slate-500">
                      {u.room_type ?? '—'}
                    </span>
                  </td>
                  <td className="whitespace-nowrap px-3 py-3 text-slate-600">
                    {fmtDate(u.arrival_date)} → {fmtDate(u.departure_date)}
                    <span className="block text-xs text-slate-400">
                      {u.nights} night{u.nights === 1 ? '' : 's'}
                    </span>
                  </td>
                  <td className="whitespace-nowrap px-3 py-3 text-slate-600">
                    {hhmm(u.expected_arrival_time) ?? '—'}
                    <span className="block text-xs text-slate-400">
                      out {hhmm(u.expected_departure_time) ?? '—'}
                    </span>
                  </td>
                  <td className="px-3 py-3 text-slate-600">
                    {u.adults}A{u.children > 0 ? ` ${u.children}C` : ''}
                  </td>
                  <td className="px-3 py-3 text-slate-600">
                    {u.rate_plan ?? 'Room’s own rate'}
                  </td>
                  <td className="px-3 py-3 text-slate-600">
                    {u.meal_plan ?? 'Room only'}
                    {u.package && (
                      <span className="block text-xs text-slate-400">
                        {u.package}
                      </span>
                    )}
                  </td>
                  <td className="px-5 py-3">
                    <span className="rounded-full bg-slate-75 px-2 py-0.5 text-xs font-medium capitalize text-slate-600">
                      {u.status.replace(/_/g, ' ')}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <Card title="Cancellation policy" icon={ShieldCheck}>
        {!p.name && !p.text ? (
          <p className="text-sm text-slate-500">
            No cancellation policy is attached to this booking, so the
            property’s default applies at the time of cancellation.
          </p>
        ) : (
          <>
            <p className="font-medium text-ink">{p.name ?? 'Policy'}</p>
            <dl className="mt-3 grid gap-3 sm:grid-cols-3">
              <Fact label="Free until"
                value={p.free_until_date
                  ? fmtDate(p.free_until_date)
                  : p.free_until_days !== null
                    ? `${p.free_until_days} days before arrival`
                    : '—'} />
              <Fact label="Penalty"
                value={p.penalty_nights !== null
                  ? `${p.penalty_nights} night${p.penalty_nights === 1 ? '' : 's'}`
                  : '—'} />
              <Fact label="No-show refund"
                value={p.no_show_refund === null ? '—'
                  : p.no_show_refund ? 'Refundable' : 'Not refundable'} />
            </dl>
            {p.text && (
              <p className="mt-3 rounded-lg bg-slate-50 px-3 py-2 text-sm text-slate-600">
                {p.text}
              </p>
            )}
          </>
        )}
      </Card>

      {r.booking.special_requests && (
        <Card title="Special requests" icon={Info}>
          <p className="text-sm text-slate-700">{r.booking.special_requests}</p>
        </Card>
      )}
    </div>
  )
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-slate-50 px-3 py-2">
      <dt className="text-xs text-slate-500">{label}</dt>
      <dd className="mt-0.5 font-medium text-ink">{value}</dd>
    </div>
  )
}

/* ------------------------------------------------------------ credit card -- */

/* ------------------------------------------------------------------ tasks -- */

/**
 * Maintenance and housekeeping on the rooms this booking occupies.
 *
 * Neither belongs to a reservation — both belong to a room, which is right: a
 * dripping tap is the room's problem whoever is sleeping in it. The desk's
 * question is about the guest in front of them, so the server answers it by
 * asking about the rooms this booking holds, over the nights it holds them.
 */
export function TasksTab({ r }: { r: ReservationFull }) {
  const q = useQuery({
    queryKey: ['reservation-tasks', r.id],
    queryFn: () => getReservationTasks(r.id),
  })
  const rows = q.data ?? []
  const unassigned = r.units.every((u) => !u.room_code)

  return (
    <Card title="Work on these rooms" icon={Wrench}>
      {q.isLoading ? (
        <p className="py-6 text-center text-sm text-slate-400">
          <Loader2 className="mx-auto mb-2 h-5 w-5 animate-spin" /> Loading…
        </p>
      ) : unassigned ? (
        <p className="flex items-start gap-2 py-4 text-sm text-slate-500">
          <AlertTriangle size={15} className="mt-0.5 shrink-0 text-caution" />
          No room is assigned to this booking yet, so there is nothing to show —
          maintenance and housekeeping belong to a room, not to a reservation.
        </p>
      ) : rows.length === 0 ? (
        <p className="py-4 text-center text-sm text-slate-500">
          Nothing open or recorded on{' '}
          {r.units.map((u) => u.room_code).filter(Boolean).join(', ')} for these
          dates.
        </p>
      ) : (
        <ul className="divide-y divide-slate-100">
          {rows.map((t) => (
            <li key={t.id} className="flex flex-wrap items-center gap-3 py-3">
              <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-xl ${
                t.kind === 'work_order'
                  ? 'bg-amber-50 text-caution' : 'bg-brand-light text-brand'}`}>
                {t.kind === 'work_order' ? <Wrench size={16} /> : <Brush size={16} />}
              </span>
              <span className="min-w-[12rem] flex-1">
                <span className="block font-medium text-ink">{t.title}</span>
                <span className="block text-xs text-slate-500">
                  {t.kind === 'work_order' ? 'Maintenance' : 'Housekeeping'}
                  {t.room && ` · room ${t.room}`}
                  {t.on_date && ` · ${fmtDate(t.on_date)}`}
                </span>
                {t.detail && (
                  <span className="mt-0.5 block text-xs text-slate-500">
                    {t.detail}
                  </span>
                )}
              </span>
              {t.assigned_to && (
                <span className="text-xs text-slate-500">{t.assigned_to}</span>
              )}
              <span className={`rounded-full px-2 py-0.5 text-xs font-medium capitalize ${
                ['done', 'completed', 'clean'].includes(t.status)
                  ? 'bg-emerald-50 text-emerald-700'
                  : t.status === 'in_progress'
                    ? 'bg-brand-light text-brand'
                    : 'bg-slate-75 text-slate-600'}`}>
                {t.status.replace(/_/g, ' ')}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}
