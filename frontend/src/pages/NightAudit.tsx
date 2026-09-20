import { useState, type ReactNode } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import {
  AlertTriangle, ArrowRight, BedDouble, CalendarCheck, Check, CheckCircle2,
  ChevronDown, Clock, History, Info, Loader2, Moon, Receipt, Users, UserX,
  Wallet,
} from 'lucide-react'
import Select from '../components/Select'
import {
  getNightAuditSettings, listProperties, previewNightAudit,
  listNightAuditRuns, runNightAudit, saveNightAuditSettings,
  type NightAuditChargeLine, type NightAuditOpenShift,
  type NightAuditPending, type NightAuditRun,
} from '../api'
import { usePropertyName } from '../hooks/useProperty'
import { fmtTime } from '../lib/dates'
import {
  money, longDate, shortDate, ReportModal,
} from '../components/nightAudit'
import { useActivePropertyId } from '../hooks/useProperty'

/**
 * Night audit — review today's activity and close the business day.
 *
 * A hotel never stops trading, so nothing ever makes the books sit still. The
 * audit creates that moment: it posts the room charge for everyone who slept
 * here tonight, says what the morning has to deal with, seals the day against
 * further posting, and moves the business date on.
 *
 * **The screen exists because closing a day cannot be undone.** The audit runs
 * unattended at 3am; the reason to have a screen is to see what it is about to
 * do before it does it, to close a day by hand when the schedule missed one,
 * and to read what a past run actually did. So the page is a review first and
 * a button second: the checks on the left, the consequences on the right, and
 * the close at the end of them.
 *
 * It deliberately does not offer to fix no-shows or overstays. Both cost the
 * guest money — a no-show penalty is a policy decision — and both already have
 * a screen where a person makes that call with the policy in front of them.
 * Reporting them here and linking across is the honest division: the audit
 * says what it found, a human decides what it means.
 */


/* ------------------------------------------------------------------ cards -- */

/**
 * One figure about tonight, as a column in the summary strip.
 *
 * These were four bordered cards, each stacking icon, label, value and
 * footnote on separate lines. Four of those plus a date band pushed the part
 * of the screen you actually act on below the fold, to say six small numbers.
 * They share one strip now, divided rather than boxed.
 */
function Stat({ icon, tint, label, value, foot }: {
  icon: React.ReactNode; tint: string; label: string
  value: string; foot: string
}) {
  return (
    <div className="flex min-w-0 items-center gap-2.5 px-4 py-2.5">
      <span className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg ${tint}`}>
        {icon}
      </span>
      <div className="min-w-0">
        <div className="truncate text-[11px] leading-tight text-slate-500">
          {label}
        </div>
        <div className="flex items-baseline gap-1.5">
          <span className="text-lg font-semibold leading-tight text-slate-800">
            {value}
          </span>
          <span className="truncate text-[11px] text-slate-400">{foot}</span>
        </div>
      </div>
    </div>
  )
}


/**
 * One line of the pre-close review: settled, or needing a person.
 *
 * A row can carry its own evidence. "Room charges reviewed" ticked itself
 * green next to a total and offered nothing to look at, which is not a review
 * — it is a box that agrees with you. Where there is something to inspect, the
 * row opens to show it.
 */
function CheckRow({ ok, title, detail, action, children }: {
  ok: boolean; title: string; detail: string
  action?: { label: string; to: string }
  children?: ReactNode
}) {
  const [open, setOpen] = useState(false)
  return (
    <li className="border-b border-slate-100 py-3 last:border-0">
      <div className="flex items-start gap-3">
        <span className={`mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full ${
          ok ? 'bg-brand text-white' : 'bg-amber-100 text-amber-700'}`}>
          {ok ? <Check size={13} strokeWidth={3} /> : <AlertTriangle size={12} />}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-sm font-medium text-slate-800">{title}</span>
          <span className="block text-xs text-slate-500">{detail}</span>
        </span>
        {children && (
          <button type="button" onClick={() => setOpen((v) => !v)}
            className="flex shrink-0 items-center gap-1 text-xs font-medium text-brand hover:underline">
            {open ? 'Hide' : 'Review'}
            <ChevronDown size={13} className={open ? 'rotate-180' : ''} />
          </button>
        )}
        {action && (
          <Link to={action.to}
            className="shrink-0 text-xs font-medium text-brand hover:underline">
            {action.label}
          </Link>
        )}
      </div>
      {open && children && <div className="mt-2.5 pl-9">{children}</div>}
    </li>
  )
}

/**
 * Bookings the audit found and a person has to settle.
 *
 * Each row links to the booking rather than to the screen in general: the
 * question in front of the auditor is "this one", and arriving at a list they
 * then have to search makes them do the matching twice.
 */
function PendingLines({ rows, href }: {
  rows: NightAuditPending[]; href: (r: NightAuditPending) => string
}) {
  return (
    <ul className="overflow-hidden rounded-lg border border-slate-200">
      {rows.map((r) => (
        <li key={r.unit_id}
          className="flex items-center gap-3 border-b border-slate-100 px-3 py-2 text-xs last:border-0">
          <span className="w-16 shrink-0 font-medium text-slate-800">
            {r.room ?? 'No room'}
          </span>
          <span className="min-w-0 flex-1 truncate text-slate-600">
            {r.guest ?? <span className="text-slate-400">No name</span>}
          </span>
          <span className="shrink-0 text-slate-400">
            {shortDate(r.arrival_date)} → {shortDate(r.departure_date)}
          </span>
          <Link to={href(r)}
            className="shrink-0 font-medium text-brand hover:underline">
            {r.reservation_number}
          </Link>
        </li>
      ))}
    </ul>
  )
}

/** Tills still open as the day closes. */
function OpenShiftLines({ rows }: { rows: NightAuditOpenShift[] }) {
  return (
    <ul className="overflow-hidden rounded-lg border border-slate-200">
      {rows.map((sh) => (
        <li key={sh.shift_id}
          className="flex items-center gap-3 border-b border-slate-100 px-3 py-2 text-xs last:border-0">
          <span className="min-w-0 flex-1 truncate font-medium text-slate-800">
            {sh.cashier ?? 'Unknown cashier'}
          </span>
          <span className="shrink-0 text-slate-500">
            Float {money(sh.opening_float)}
          </span>
          <span className="shrink-0 text-slate-400">
            {sh.opened_at
              ? `opened ${fmtTime(sh.opened_at)}`
              : 'open'}
          </span>
        </li>
      ))}
    </ul>
  )
}

/** The room charges the audit is about to post, line by line. */
function ChargeLines({ lines, total }: {
  lines: NightAuditChargeLine[]; total: string
}) {
  return (
    <div className="overflow-hidden rounded-lg border border-slate-200">
      <table className="w-full text-xs">
        <thead className="bg-slate-50 text-left text-slate-500">
          <tr>
            <th className="px-3 py-2 font-medium">Room</th>
            <th className="px-3 py-2 font-medium">Guest</th>
            <th className="px-3 py-2 font-medium">Booking</th>
            <th className="px-3 py-2 text-right font-medium">Tonight</th>
          </tr>
        </thead>
        <tbody>
          {lines.map((l) => (
            <tr key={l.unit_id} className="border-t border-slate-100">
              <td className="px-3 py-2">
                <span className="font-medium text-slate-800">
                  {l.room ?? 'Unassigned'}
                </span>
                {l.room_type && (
                  <span className="block text-[11px] text-slate-400">
                    {l.room_type}
                  </span>
                )}
              </td>
              <td className="px-3 py-2 text-slate-600">
                {l.guest ?? <span className="text-slate-400">No name</span>}
              </td>
              <td className="px-3 py-2 text-slate-500">{l.reservation_number}</td>
              <td className="px-3 py-2 text-right font-medium text-slate-800">
                {money(l.amount)}
              </td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr className="border-t border-slate-200 bg-slate-50">
            <td colSpan={3} className="px-3 py-2 font-medium text-slate-600">
              {lines.length} room night(s)
            </td>
            <td className="px-3 py-2 text-right font-semibold text-slate-800">
              {money(total)}
            </td>
          </tr>
        </tfoot>
      </table>
    </div>
  )
}

/* ------------------------------------------------------------------ report -- */


/**
 * When this property closes its books.
 *
 * It lives on this screen rather than in Property Settings because it is a
 * financial control, not a property detail: the hour the books seal is set by
 * whoever closes them, behind the same permission. On the settings screen it
 * would have been reachable by anyone who can correct the hotel's address.
 */
function ScheduleControl({ propertyId }: { propertyId: string }) {
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)

  const { data } = useQuery({
    queryKey: ['night-audit-settings', propertyId],
    queryFn: () => getNightAuditSettings(propertyId),
    enabled: propertyId !== '',
  })
  const save = useMutation({
    mutationFn: (body: { audit_hour?: number | null; no_show_penalty?: string }) =>
      saveNightAuditSettings(propertyId, body),
    onSuccess: () => {
      setOpen(false)
      qc.invalidateQueries({ queryKey: ['night-audit-settings'] })
      qc.invalidateQueries({ queryKey: ['night-audit-preview'] })
    },
  })

  if (!data) return null
  const effective = data.audit_hour ?? data.default_hour
  const at = `${String(effective).padStart(2, '0')}:`
    + String(data.scheduled_minute).padStart(2, '0')

  return (
    <div className="relative">
      <button type="button" onClick={() => setOpen((v) => !v)}
        title={data.enabled
          ? `Runs unattended at ${at} local. A day the schedule missed is `
            + 'closed on the next sweep, oldest first.'
          : 'Nothing will close a day on its own.'}
        className={`flex items-center gap-1.5 rounded-lg px-3 py-2 text-xs font-medium ${
          data.enabled ? 'bg-emerald-50 text-emerald-700 hover:bg-emerald-100'
                       : 'bg-amber-50 text-amber-700 hover:bg-amber-100'}`}>
        <Clock size={13} />
        {data.enabled ? `Auto audit · ${at}` : 'Auto audit off'}
        <ChevronDown size={12} className={open ? 'rotate-180' : ''} />
      </button>

      {open && (
        <div className="absolute right-0 z-30 mt-1 w-72 rounded-xl border border-slate-200 bg-white p-4 shadow-xl">
          <div className="text-sm font-medium text-slate-800">
            When this property closes
          </div>
          <p className="mt-1 text-xs text-slate-500">
            The hour is yours; the minute is fixed per property so tenants do
            not all close at the same instant.
          </p>

          <label className="mt-3 block text-xs text-slate-500">Hour (local)</label>
          <Select
            value={data.audit_hour === null ? '' : String(data.audit_hour)}
            onChange={(e) => save.mutate({
              audit_hour: e.target.value === '' ? null : Number(e.target.value),
              no_show_penalty: data.no_show_penalty,
            })}
            className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm">
            <option value="">
              Use the default ({String(data.default_hour).padStart(2, '0')}:00)
            </option>
            {Array.from({ length: 24 }, (_, h) => (
              <option key={h} value={h}>
                {String(h).padStart(2, '0')}:
                {String(data.scheduled_minute).padStart(2, '0')}
              </option>
            ))}
          </Select>

          {/* This could only be changed in the database before. An unattended
              charge is a decision, and a decision nobody can see or alter is
              not one the property has made. */}
          <label className="mt-4 block text-xs text-slate-500">
            No-show penalty, charged automatically
          </label>
          <Select
            value={data.no_show_penalty}
            onChange={(e) => save.mutate({
              audit_hour: data.audit_hour,
              no_show_penalty: e.target.value,
            })}
            className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm">
            {data.no_show_options.map((o) => (
              <option key={o.code} value={o.code}>{o.label}</option>
            ))}
          </Select>
          <p className="mt-1 text-xs text-slate-400">
            {data.no_show_penalty === 'none'
              ? 'Missed arrivals are recorded and their rooms go back on sale, '
                + 'but nobody is billed. Charge them by hand from No-Show '
                + 'Processing.'
              : 'The audit bills this to the folio when an arrival never comes. '
                + 'A no-show handled by hand first is charged once, not twice.'}
          </p>

          {save.isPending && (
            <p className="mt-2 flex items-center gap-1.5 text-xs text-slate-400">
              <Loader2 size={12} className="animate-spin" /> Saving…
            </p>
          )}
          {save.isError && (
            <p className="mt-2 text-xs text-red-600">
              {(save.error as Error)?.message ?? 'Could not save.'}
            </p>
          )}
          {!data.enabled && (
            <p className="mt-2 rounded-md bg-amber-50 p-2 text-xs text-caution">
              The scheduler is switched off for this deployment, so this hour
              is not currently used.
            </p>
          )}
        </div>
      )}
    </div>
  )
}




/* ------------------------------------------------------------------- page -- */

export default function NightAudit() {
  const propertyId = useActivePropertyId()
  const qc = useQueryClient()
  const [confirming, setConfirming] = useState(false)
  const [report, setReport] = useState<NightAuditRun | null>(null)
  const propertyName = usePropertyName()

  const { data: properties } = useQuery({
    queryKey: ['properties'], queryFn: listProperties,
  })
  const organizationId =
    properties?.find((p) => p.id === propertyId)?.organization_id
    ?? properties?.[0]?.organization_id ?? ''

  const preview = useQuery({
    queryKey: ['night-audit-preview', propertyId],
    queryFn: () => previewNightAudit(propertyId),
    enabled: propertyId !== '',
  })
  const history = useQuery({
    queryKey: ['night-audit-history', propertyId],
    // Only the banner needs this: the newest completed run. History lives on
    // its own page now, so this asks for a handful rather than everything.
    queryFn: () => listNightAuditRuns(propertyId, { limit: 5 }),
    enabled: propertyId !== '',
  })

  const run = useMutation({
    mutationFn: () => runNightAudit({
      organization_id: organizationId,
      property_id: propertyId,
      business_date: preview.data!.business_date,
      // Only ever sent from the override path below, where the exception was
      // named and accepted. Never a default.
      allow_open_shifts: preview.data!.requires_override,
    }),
    onSuccess: () => {
      setConfirming(false)
      qc.invalidateQueries({ queryKey: ['night-audit-preview'] })
      qc.invalidateQueries({ queryKey: ['night-audit-history'] })
      qc.invalidateQueries({ queryKey: ['dashboard'] })
    },
  })

  const p = preview.data
  const busy = run.isPending
  const runs = history.data?.rows ?? []
  // The banner reports the last day actually sealed, which is the newest
  // completed run -- not simply the newest run, because a failed attempt
  // closed nothing.
  const lastClosed = runs.find((r) => r.status === 'completed')


  const blockers = p
    ? (p.no_shows.length > 0 ? 1 : 0) + (p.open_shifts > 0 ? 1 : 0)
      + (p.warnings.length > 0 ? 1 : 0)
    : 0

  return (
    <div className="space-y-3">
      {/* ------------------------------------------------------- header --- */}
      {/* The auto-audit state lives up here rather than in a band of its own:
          it is standing context about the schedule, not news about tonight. */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2.5">
          <Moon size={22} className="shrink-0 text-brand" />
          <div>
            <h1 className="text-display text-ink">
              Night Audit
            </h1>
            <p className="text-[11px] leading-tight text-slate-500">
              Review today's activity and close the business day.
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {/* The last day sealed: a link, not a banner. It is reassurance you
              glance at, and it had a full-width green strip to itself. */}
          {lastClosed && (
            <button type="button" onClick={() => setReport(lastClosed)}
              className="flex items-center gap-1.5 rounded-lg px-2 py-2 text-xs text-slate-500 hover:bg-slate-100">
              <CheckCircle2 size={14} className="shrink-0 text-emerald-600" />
              Last closed
              <strong className="font-semibold text-slate-700">
                {shortDate(lastClosed.business_date)}
              </strong>
              <ArrowRight size={12} className="text-brand" />
            </button>
          )}
          <ScheduleControl propertyId={propertyId} />
          {/* Its own page. Scrolling to a table below the fold made checking
              last Tuesday a trip past tonight's decision; history is a
              different question, asked at a different time. */}
          <Link to="/night-audit/history"
            className="flex items-center gap-2 rounded-lg border border-brand/30 px-3.5 py-2 text-sm font-medium text-brand hover:bg-brand/5">
            <History size={15} /> Audit History
          </Link>
        </div>
      </div>

      {run.isError && (
        <div className="flex items-start gap-2 rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-700">
          <AlertTriangle size={16} className="mt-0.5 shrink-0" />
          <span>
            The audit did not complete, and the day is unchanged.{' '}
            {(run.error as Error)?.message}
          </span>
        </div>
      )}

      {preview.isLoading && (
        <div className="flex items-center gap-2 rounded-xl border border-slate-200 bg-white p-6 text-sm text-slate-500">
          <Loader2 size={15} className="animate-spin" /> Working out what
          closing would do…
        </div>
      )}

      {p && (
        <>
          {/* ------------------------------------- the day, and tonight ---
              The date and the four figures are all facts about the same
              night, so they share one strip instead of a band each. */}
          <div className="flex flex-wrap items-stretch divide-x divide-slate-100 rounded-xl border border-slate-200 bg-white">
            <div className="flex items-center gap-3 px-4 py-2.5">
              <CalendarCheck size={20} className="shrink-0 text-brand" />
              <div>
                <div className="text-[11px] uppercase leading-tight tracking-wide text-slate-400">
                  {p.can_close ? 'Business date to close' : 'Next business date'}
                </div>
                {/* A displayed value, not a heading — but it was the last thing in
                    the app shell still set in Playfair, and every other
                    prominent figure (the dashboard KPIs, folio totals) is Inter.
                    Size kept: this is the date the whole screen is about. */}
                <div className="text-xl font-bold leading-tight text-slate-800">
                  {longDate(p.business_date)}
                </div>
              </div>
            </div>

            <Stat icon={<BedDouble size={15} />} tint="bg-teal-50 text-brand"
              label="Occupied rooms" value={String(p.rooms_to_charge)}
              foot={p.rooms_to_charge === 0 ? 'none' : 'one night each'} />
            <Stat icon={<Receipt size={15} />}
              tint="bg-emerald-50 text-emerald-600"
              label="Charges to post" value={money(p.amount_to_charge)}
              foot="before tax" />
            <Stat icon={<Users size={15} />} tint="bg-amber-50 text-amber-600"
              label="No-shows" value={String(p.no_shows.length)}
              foot={p.no_shows.length === 0 ? 'none' : 'to review'} />
            <Stat icon={<Wallet size={15} />}
              tint={p.open_shifts ? 'bg-red-50 text-red-600'
                                  : 'bg-indigo-50 text-indigo-500'}
              label="Open shifts" value={String(p.open_shifts)}
              foot={p.open_shifts ? 'undeclared' : 'all closed'} />
          </div>

          <div className="grid gap-3 lg:grid-cols-[1fr_400px]">
            {/* ------------------------------------------ before you close - */}
            <div className="rounded-xl border border-slate-200 bg-white p-5">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <h2 className="text-lg font-semibold text-ink">
                    Before you close
                  </h2>
                  <p className="mt-0.5 text-sm text-slate-500">
                    {!p.can_close
                      ? 'Every finished day has been closed. This one is still ahead.'
                      : blockers === 0
                        ? 'Review the following items. All clear to close the business day.'
                        : 'Review the following items. Some need attention first.'}
                  </p>
                </div>
                <span className={`flex shrink-0 items-center gap-2 rounded-lg px-3 py-1.5 text-sm font-medium ${
                  blockers === 0 ? 'bg-emerald-50 text-emerald-700'
                                 : 'bg-amber-50 text-amber-700'}`}>
                  {!p.can_close
                    ? <><CheckCircle2 size={15} /> Up to date</>
                    : blockers === 0
                      ? <><CheckCircle2 size={15} /> Ready for review</>
                      : <><AlertTriangle size={15} /> {blockers} need attention</>}
                </span>
              </div>

              <ul className="mt-3">
                <CheckRow ok
                  title="Room charges reviewed"
                  detail={p.rooms_to_charge === 0
                    ? 'No room charges to post'
                    : `${p.rooms_to_charge} room(s) · ${money(p.amount_to_charge)} will post`}>
                  {p.charge_lines.length > 0 && (
                    <ChargeLines lines={p.charge_lines}
                      total={p.amount_to_charge} />
                  )}
                </CheckRow>
                <CheckRow ok={p.no_shows.length === 0}
                  title="No-shows reviewed"
                  detail={p.no_shows.length === 0
                    ? 'No pending no-shows'
                    : `${p.no_shows.length} booking(s) never checked in`}
                  action={p.no_shows.length > 0
                    ? { label: 'Process all', to: '/reservations/no-show' }
                    : undefined}>
                  {p.no_shows.length > 0 && (
                    <PendingLines rows={p.no_shows}
                      href={(r) => `/reservations/no-show/${r.unit_id}`} />
                  )}
                </CheckRow>
                <CheckRow ok={p.open_shifts === 0}
                  title="Cashier shifts closed"
                  detail={p.open_shifts === 0 ? 'No open shifts'
                    : `${p.open_shifts} shift(s) still open`}
                  action={p.open_shifts > 0
                    ? { label: 'Cashiering', to: '/payments' } : undefined}>
                  {p.open_shift_rows.length > 0 && (
                    <OpenShiftLines rows={p.open_shift_rows} />
                  )}
                </CheckRow>
                {p.overstays.length > 0 && (
                  <CheckRow ok={false}
                    title="Guests past their departure date"
                    detail={`${p.overstays.length} still in-house — charged for tonight`}
                    action={{ label: 'Stay view', to: '/stayview' }}>
                    <PendingLines rows={p.overstays} href={() => '/stayview'} />
                  </CheckRow>
                )}
              </ul>

              {/* The one thing most worth saying about tonight. */}
              {p.warnings.length > 0 ? (
                <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-3">
                  <div className="flex items-center gap-2 text-sm font-medium text-caution">
                    <AlertTriangle size={15} /> Needs attention
                  </div>
                  <ul className="mt-1.5 space-y-1 text-sm text-amber-700">
                    {p.warnings.map((w, i) => <li key={i}>• {w}</li>)}
                  </ul>
                </div>
              ) : p.rooms_to_charge === 0 && p.can_close && (
                <div className="mt-3 flex items-start gap-3 rounded-lg bg-slate-50 p-3.5">
                  <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-white text-slate-400">
                    <BedDouble size={15} />
                  </span>
                  <span>
                    <span className="block text-sm font-medium text-slate-700">
                      No occupied rooms for this date
                    </span>
                    <span className="block text-xs text-slate-500">
                      You can still close the day. No room charges will be posted.
                    </span>
                  </span>
                </div>
              )}

            </div>

            {/* ------------------------------------------ closing summary - */}
            <div className="rounded-xl border border-slate-200 bg-white p-5">
              <h2 className="text-lg font-semibold text-ink">
                Closing summary
              </h2>

              <dl className="mt-4 space-y-2.5 text-sm">
                <div className="flex items-center justify-between">
                  <dt className="text-slate-500">Business date</dt>
                  <dd className="font-medium text-slate-800">
                    {shortDate(p.business_date)}
                  </dd>
                </div>
                <div className="flex items-center justify-between">
                  <dt className="text-slate-500">Charges to post</dt>
                  <dd className="font-medium text-slate-800">{p.rooms_to_charge}</dd>
                </div>
                <div className="flex items-center justify-between">
                  <dt className="text-slate-500">Amount before tax</dt>
                  <dd className="font-medium text-slate-800">
                    {money(p.amount_to_charge)}
                  </dd>
                </div>
                <div className="flex items-center justify-between border-t border-slate-100 pt-3">
                  <dt className="text-slate-500">Next business date</dt>
                  <dd className="text-base font-semibold text-brand">
                    {shortDate(p.next_business_date)}
                  </dd>
                </div>
              </dl>

              <div className="mt-4 flex items-start gap-2 rounded-lg bg-slate-50 p-3 text-xs text-slate-500">
                <Info size={14} className="mt-0.5 shrink-0" />
                <span>
                  Closing locks postings to {shortDate(p.business_date)} and
                  advances the business date.
                </span>
              </div>

              {p.already_closed ? (
                <div className="mt-4 rounded-lg bg-slate-75 py-3 text-center text-sm font-medium text-slate-500">
                  This day is already closed
                </div>
              ) : !p.can_close ? (
                /* A day in the future. The server refuses it too — this is so
                   the refusal is visible before the click, not after. */
                <div className="mt-4 space-y-2">
                  <div className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-caution">
                    <AlertTriangle size={14} className="mt-0.5 shrink-0" />
                    <span>{p.blocked_reason}</span>
                  </div>
                  <div className="rounded-lg bg-slate-75 py-3 text-center text-sm font-medium text-slate-400">
                    Nothing to close yet
                  </div>
                </div>
              ) : confirming ? (
                /* The exceptions are named here, at the point of commitment.
                   They were on a badge further up the page, which is no use to
                   someone who has already scrolled past it: this is the one
                   irreversible action on the screen, and it said the same
                   words whether the day was clean or two drawers were
                   uncounted. */
                <div className="mt-4 space-y-2">
                  {p.requires_override && (
                    <div className="flex items-start gap-2 rounded-lg border border-amber-300 bg-amber-50 p-3 text-xs text-amber-900">
                      <AlertTriangle size={14} className="mt-0.5 shrink-0" />
                      <span>{p.override_reason}</span>
                    </div>
                  )}
                  {p.no_shows.length > 0 && (
                    <div className="flex items-start gap-2 rounded-lg border border-slate-200 bg-slate-50 p-3 text-xs text-slate-600">
                      <UserX size={14} className="mt-0.5 shrink-0" />
                      <span>
                        {p.no_shows.length} no-show(s) are unprocessed. They
                        stay on this date holding their rooms.
                      </span>
                    </div>
                  )}
                  <p className="text-center text-sm text-slate-600">
                    Closing a day cannot be undone.
                  </p>
                  <button type="button" onClick={() => run.mutate()} disabled={busy}
                    className={`flex w-full items-center justify-center gap-2 rounded-lg py-3 text-sm font-semibold text-white hover:opacity-90 disabled:opacity-60 ${
                      p.requires_override ? 'bg-amber-600' : 'bg-brand'}`}>
                    {busy && <Loader2 size={15} className="animate-spin" />}
                    {busy ? 'Closing…'
                      : p.requires_override
                        ? `Close ${shortDate(p.business_date)} over the open till`
                        : `Yes, close ${shortDate(p.business_date)}`}
                  </button>
                  <button type="button" onClick={() => setConfirming(false)}
                    disabled={busy}
                    className="w-full rounded-lg border border-slate-200 py-2.5 text-sm text-slate-600 hover:bg-slate-50">
                    Cancel
                  </button>
                </div>
              ) : (
                <>
                  <button type="button" onClick={() => setConfirming(true)}
                    className="mt-4 flex w-full items-center justify-center gap-2 rounded-lg bg-brand py-3.5 text-base font-semibold text-white hover:opacity-90">
                    Review &amp; Close Day <ArrowRight size={17} />
                  </button>
                  <p className="mt-2 text-center text-xs text-slate-400">
                    Review the details before confirming.
                  </p>
                </>
              )}
            </div>
          </div>
        </>
      )}

      {report && (
        <ReportModal run={report} propertyName={propertyName}
          propertyId={propertyId}
          onClose={() => setReport(null)} />
      )}
    </div>
  )
}
