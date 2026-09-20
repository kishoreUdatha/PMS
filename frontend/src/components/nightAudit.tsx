import { useEffect, type ReactNode } from 'react'
import { fmtDate, fmtDateLong, fmtDateTime, fmtTime } from '../lib/dates'
import { createPortal } from 'react-dom'
import {
  AlertTriangle, Check, CheckCircle2, Clock, Lock, Printer, Waves, X,
} from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { getNightAuditReport, type DepartureLine,
         type PaxLine, type ReceiptDetailLine,
         type NightAuditRun,
         type NightAuditReportData } from '../api'

/**
 * The parts of the night audit that both of its screens need.
 *
 * The report opens from the audit screen (the last day closed) and from the
 * history page (any past run). It lived in the page file until the history
 * moved out; a second copy would have been two reports that drift apart, and
 * the one thing a report must not do is disagree with itself.
 */

export const money = (v: string | number) =>
  `₹${Number(v).toLocaleString('en-IN', { minimumFractionDigits: 2,
                                          maximumFractionDigits: 2 })}`

// Both defer to the app-wide spelling rather than asking the browser.
// `toLocaleDateString('en-IN')` writes September as "Sept" -- four letters,
// where the top bar and every list row write "Sep" -- so one screen showed a
// business date of "12 Sept 2026" beside a header reading "13 Sep 2026".
// Whoever chose the month name is not the question; having one answer is.
export const longDate = fmtDateLong
export const shortDate = fmtDate

/**
 * Which bucket a past run belongs in.
 *
 * Three, and they do not overlap — a run is exactly one of them, so the tab
 * counts add up to the total and nothing hides in two places at once.
 *
 * "With exceptions" is the one worth having. Those runs completed, so they
 * look identical to a clean close in a status column, and they are precisely
 * the ones somebody asks about later: the day sealed over a till nobody had
 * counted.
 */
export type RunBucket = 'clean' | 'exception' | 'failed'

export function bucketOf(run: NightAuditRun): RunBucket {
  if (run.status !== 'completed' || run.steps.some((st) => st.status === 'failed')) {
    return 'failed'
  }
  const cash = run.steps.find((st) => st.step_code === 'reconcile_cashiering')
  return Number(cash?.detail?.open_shifts ?? 0) > 0 ? 'exception' : 'clean'
}

export const TABS: { key: RunBucket | 'all'; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'clean', label: 'Clean' },
  { key: 'exception', label: 'With exceptions' },
  { key: 'failed', label: 'Failed' },
]

/*
 * The document's table styling, in one place.
 *
 * Six tables share it. Written inline in each, a later change would have to be
 * made six times and would be made five, which is how a printed report ends up
 * with one section that looks like a different document.
 */
const TH = 'bg-brand px-3 py-2 text-left align-bottom text-[10px] font-semibold uppercase leading-tight tracking-wide text-white'
const TH_R = `${TH} text-right`
const TD = 'px-3 py-2 align-top text-slate-700'
const TD_R = `${TD} text-right`
//: Zebra striping, so the eye can hold a row across a wide table.
const ROW = 'even:bg-slate-50/80'
const TOTAL_ROW = 'bg-brand-light font-semibold text-slate-900'

/** A titled block inside the report sheet. */
function Section({ n, title, note, children }: {
  n: string; title: string; note?: string; children: ReactNode
}) {
  return (
    <div className="px-6 pt-4">
      {/* The banded section head of the printed report: number and title in
          brand on a tint, the count of what is inside it on the right. */}
      <div className="print-keep flex flex-wrap items-baseline justify-between gap-2 rounded bg-brand-light px-3 py-2">
        <h3 className="text-sm font-bold text-brand">
          <span className="mr-2">{n}</span>{title}
        </h3>
        {note && <span className="text-[11px] text-slate-500">{note}</span>}
      </div>
      <div className="mt-2 overflow-x-auto">{children}</div>
    </div>
  )
}

/** Revenue for the day, by what earned it, charge and tax kept apart. */
function SalesTable({ data }: { data: NightAuditReportData }) {
  if (data.sales.length === 0) {
    return <p className="text-xs text-slate-400">Nothing was charged on this day.</p>
  }
  return (
    <table className="w-full min-w-[560px] border border-slate-200 text-xs">
      <thead>
        <tr>
          <th className={TH}>Category</th>
          <th className={TH_R}>Charges</th>
          <th className={TH_R}>Tax</th>
          <th className={TH_R}>Total</th>
        </tr>
      </thead>
      <tbody>
        {data.sales.map((l) => (
          <tr key={l.category} className={ROW}>
            <td className={TD}>{l.category}</td>
            <td className={TD_R}>{money(l.charges)}</td>
            <td className={TD_R}>{money(l.tax)}</td>
            <td className={`${TD_R} font-medium text-slate-900`}>
              {money(l.total)}
            </td>
          </tr>
        ))}
      </tbody>
      {data.sales_total && (
        <tfoot>
          {/* The reference report printed a Total row of zeroes while the
              rows above plainly summed to something. A total that disagrees
              with its own table is the one line a manager actually reads. */}
          <tr className={TOTAL_ROW}>
            <td className={TD}>Total (₹)</td>
            <td className={TD_R}>{money(data.sales_total.charges)}</td>
            <td className={TD_R}>{money(data.sales_total.tax)}</td>
            <td className={TD_R}>{money(data.sales_total.total)}</td>
          </tr>
        </tfoot>
      )}
    </table>
  )
}

/** What the day earned against what it actually took. */
function BalanceBlock({ data }: { data: NightAuditReportData }) {
  const owed = Number(data.balance.outstanding)
  return (
    <div className="grid grid-cols-3 gap-px overflow-hidden rounded-lg bg-slate-75">
      {[
        { label: 'Charged', v: data.balance.charged, tone: 'text-slate-800' },
        { label: 'Collected', v: data.balance.collected, tone: 'text-emerald-700' },
        { label: 'Outstanding', v: data.balance.outstanding,
          tone: owed > 0 ? 'text-amber-700' : 'text-slate-800' },
      ].map((c) => (
        <div key={c.label} className="bg-white px-3 py-2.5">
          <div className="text-[11px] text-slate-400">{c.label}</div>
          <div className={`text-base font-semibold ${c.tone}`}>{money(c.v)}</div>
        </div>
      ))}
    </div>
  )
}

/** Which rooms were billed, from what the run itself recorded. */
function ChargeDetail({ run }: { run: NightAuditRun }) {
  const step = run.steps.find((st) => st.step_code === 'post_room_charges')
  const lines = (step?.detail?.lines ?? []) as {
    room: string | null; guest: string | null; room_type: string | null
    reservation_number: string | null; amount: string
  }[]
  if (lines.length === 0) {
    return <p className="text-xs text-slate-400">No rooms were billed on this day.</p>
  }
  return (
    <table className="w-full min-w-[560px] border border-slate-200 text-xs">
      <thead>
        <tr>
          <th className={TH}>Room</th>
          <th className={TH}>Guest</th>
          <th className={TH}>Booking</th>
          <th className={TH_R}>Night</th>
        </tr>
      </thead>
      <tbody>
        {lines.map((l, i) => (
          <tr key={`${l.reservation_number}-${i}`} className={ROW}>
            <td className={TD}>
              <span className="font-medium">{l.room ?? 'Unassigned'}</span>
              {l.room_type && (
                <span className="ml-1 text-slate-400">{l.room_type}</span>
              )}
            </td>
            <td className={TD}>{l.guest ?? '—'}</td>
            <td className={`${TD} text-slate-500`}>{l.reservation_number}</td>
            <td className={`${TD_R} font-medium text-slate-900`}>
              {money(l.amount)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

/** Who left, and whether they left anything owing. */
function Departures({ rows }: { rows: DepartureLine[] }) {
  if (rows.length === 0) {
    return <p className="text-xs text-slate-400">Nobody checked out on this day.</p>
  }
  return (
    <table className="w-full min-w-[560px] border border-slate-200 text-xs">
      <thead>
        <tr>
          <th className={TH}>Room</th>
          <th className={TH}>Guest</th>
          <th className={TH_R}>Nights</th>
          <th className={TH_R}>Charged</th>
          <th className={TH_R}>Paid</th>
          <th className={TH_R}>Balance</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((d) => {
          const owing = Number(d.balance) > 0.005
          return (
            <tr key={`${d.reservation_number}-${d.room}`} className={ROW}>
              <td className={`${TD} font-medium`}>
                {d.room ?? '—'}
              </td>
              <td className={TD}>
                {d.guest ?? '—'}
                <span className="ml-1 text-slate-400">{d.reservation_number}</span>
              </td>
              <td className={TD_R}>{d.nights}</td>
              <td className={TD_R}>{money(d.charged)}</td>
              <td className={TD_R}>{money(d.paid)}</td>
              {/* A guest who walked out owing money is the single line on
                  this page worth chasing in the morning. */}
              <td className={`${TD_R} font-semibold ${
                owing ? 'text-amber-700' : 'text-slate-900'}`}>
                {money(d.balance)}
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

/** Every receipt taken on the day, as a cashier's tape reads. */
function ReceiptDetail({ rows, total }: {
  rows: ReceiptDetailLine[]; total: string
}) {
  if (rows.length === 0) {
    return <p className="text-xs text-slate-400">No money was taken on this day.</p>
  }
  return (
    <table className="w-full min-w-[560px] border border-slate-200 text-xs">
      <thead>
        <tr>
          <th className={TH}>Received</th>
          <th className={TH}>Reference</th>
          <th className={TH}>Method</th>
          <th className={TH}>Taken by</th>
          <th className={TH_R}>Amount</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i} className={ROW}>
            <td className={TD}>
              {r.received_at
                ? fmtTime(r.received_at)
                : '—'}
            </td>
            <td className={TD}>
              {r.reference || (r.room ? `Room ${r.room}` : '—')}
            </td>
            <td className={TD}>{r.method}</td>
            <td className={TD}>{r.cashier ?? '—'}</td>
            <td className={`${TD_R} font-medium text-slate-900`}>
              {money(r.amount)}
            </td>
          </tr>
        ))}
      </tbody>
      <tfoot>
        <tr className={TOTAL_ROW}>
          <td className={TD} colSpan={4}>Total received (₹)</td>
          <td className={TD_R}>{money(total)}</td>
        </tr>
      </tfoot>
    </table>
  )
}

/** Rooms and heads under a set of headings. */
function PaxTable({ rows, first }: { rows: PaxLine[]; first: string }) {
  if (rows.length === 0) {
    return <p className="text-xs text-slate-400">Nobody was in the house.</p>
  }
  return (
    <table className="w-full min-w-[560px] border border-slate-200 text-xs">
      <thead>
        <tr>
          <th className={TH}>{first}</th>
          <th className={TH_R}>Rooms</th>
          <th className={TH_R}>Adults</th>
          <th className={TH_R}>Children</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.label} className={ROW}>
            <td className={TD}>{r.label}</td>
            <td className={TD_R}>{r.rooms}</td>
            <td className={TD_R}>{r.adults}</td>
            <td className={TD_R}>{r.children}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

export const STEP_LABEL: Record<string, string> = {
  post_room_charges: 'Room charges posted',
  flag_no_shows: 'No-shows found',
  flag_overstays: 'Overstays found',
  extend_inventory_horizon: 'Booking window extended',
  reconcile_cashiering: 'Cashiering checked',
  close_business_day: 'Business day closed',
  already_closed: 'Day was already closed',
  reversed_future_close: 'Close reversed — day reopened',
}

/** The numbers a step recorded, in words rather than raw JSON. */
export function stepDetail(code: string, d: Record<string, unknown> | null): string {
  if (!d) return ''
  if (code === 'post_room_charges') {
    return `${d.rooms} room(s) · ${money(String(d.amount ?? 0))}`
  }
  if (code === 'extend_inventory_horizon') {
    // Zero is the normal, healthy answer once the window is full: it means
    // every night inside the horizon was already on sale. Reading "+0" as a
    // bare number looks like the step did nothing useful.
    return Number(d.days_added) === 0
      ? 'Already open to the full booking window'
      : `+${d.days_added} night(s) opened for sale`
  }
  if (code === 'reconcile_cashiering') {
    return Number(d.open_shifts) > 0
      ? `${d.open_shifts} shift(s) left open` : 'All drawers declared'
  }
  if ('count' in d) return `${d.count} found`
  return ''
}

/** The order the audit works in, so a report reads as a sequence. */
const STEP_ORDER = [
  'post_room_charges', 'flag_no_shows', 'flag_overstays',
  'extend_inventory_horizon', 'reconcile_cashiering', 'close_business_day',
]

/**
 * What one run did, as a document.
 *
 * This is the thing somebody opens in March to find out what was billed on a
 * night in September, so it is built as a record rather than a status popup:
 * the money first, because that is what the question is usually about; then
 * the steps in the order they ran, because an audit is a sequence and "where
 * did it stop" is the other question people arrive with.
 *
 * **The numbers are what was recorded that night**, read back from each step's
 * own detail — not recomputed now. Re-deriving them would quietly answer a
 * different question: what today's data says about that night, after months of
 * amendments. An audit trail that changes under you is not one.
 *
 * It prints. A report kept for a handover or attached to a query is worth more
 * on paper than a screenshot of a dialog.
 */
export function ReportModal({ run, propertyName, propertyId, onClose }: {
  run: NightAuditRun; propertyName: string; propertyId: string
  onClose: () => void
}) {
  // The money sections come from dated ledger rows rather than from the run,
  // so they stay true months later. Loaded here rather than with the history
  // list: one report is opened at a time, and a list of fifty would otherwise
  // fetch fifty of these to show none of them.
  const { data: figures } = useQuery({
    queryKey: ['night-audit-report', propertyId, run.business_date],
    queryFn: () => getNightAuditReport(propertyId, run.business_date),
    enabled: propertyId !== '',
  })
  // Escape closes, as every dialog should.
  useEffect(() => {
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', key)
    return () => document.removeEventListener('keydown', key)
  }, [onClose])

  const byCode = new Map(run.steps.map((st) => [st.step_code, st]))
  const ordered = [
    ...STEP_ORDER.filter((c) => byCode.has(c)).map((c) => byCode.get(c)!),
    // Anything the server records that this screen has not been taught about
    // still belongs in the report, at the end, rather than vanishing.
    ...run.steps.filter((st) => !STEP_ORDER.includes(st.step_code)),
  ]

  const charges = byCode.get('post_room_charges')
  const rooms = Number(charges?.detail?.rooms ?? 0)
  const posted = Number(charges?.detail?.posted ?? 0)
  const amount = String(charges?.detail?.amount ?? 0)
  const failed = run.steps.filter((st) => st.status === 'failed')

  return createPortal(
    <div data-print-sheet
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 p-4"
      role="dialog" aria-modal="true" aria-label="Night audit report"
      onClick={onClose}>
      {/* Wide, because it is a landscape document: the charge and checkout
          tables carry a dozen columns each, and squeezing them into a dialog
          width turned every section into its own horizontal scrollbar. */}
      <div className="print-sheet max-h-[90vh] w-full max-w-5xl overflow-y-auto rounded-2xl bg-white shadow-2xl"
        onClick={(e) => e.stopPropagation()}>

        {/* ----------------------------------------- document masthead ---
            Laid out as the printed report it is: the property on the left,
            what the document is on the right, then the date and currency it
            is denominated in. A screen dialog and a sheet of paper want the
            same thing here, so there is only one design rather than a
            coloured header that has to be undone for print. */}
        <div className="relative px-6 pt-5">
          <button type="button" onClick={onClose}
            className="print-hide absolute right-4 top-4 rounded-lg p-1 text-slate-400 hover:bg-slate-100">
            <X size={18} />
          </button>
          {/* pr-7 keeps the title clear of the close button, which sits over
              this row and is hidden when printing. */}
          {/* pr-7 keeps the title clear of the close button, which sits over
              this row and is hidden when printing. */}
          <div className="flex flex-wrap items-center justify-between gap-2 pr-7 print:pr-0">
            <span className="flex items-center gap-2.5">
              <Waves size={26} className="shrink-0 text-brand" strokeWidth={2.5} />
              <span className="text-lg font-bold uppercase tracking-wide text-brand-dark">
                {propertyName || 'Property'}
              </span>
            </span>
            <span className="text-xl font-bold text-brand-dark">Night Audit</span>
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-x-10 gap-y-1 border-b-2 border-brand pb-2 text-[11px] uppercase tracking-wide">
            <span className="font-semibold text-brand">
              Business date{' '}
              <span className="normal-case text-slate-800">
                {longDate(run.business_date)}
              </span>
            </span>
            <span className="text-slate-500">
              Currency{' '}
              <span className="font-semibold text-slate-800">
                {figures?.currency ?? 'INR'} (₹)
              </span>
            </span>
          </div>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2 text-[11px] text-slate-500">
            <span className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 font-medium ${
              failed.length ? 'bg-red-50 text-red-700'
                : run.status === 'completed' ? 'bg-emerald-50 text-emerald-700'
                : 'bg-amber-50 text-amber-700'}`}>
              {failed.length ? <AlertTriangle size={10} />
                : run.status === 'completed' ? <CheckCircle2 size={10} />
                : <Clock size={10} />}
              {failed.length ? 'Failed'
                : run.status === 'completed' ? 'Completed' : run.status}
            </span>
            {run.completed_at && (
              <span>Closed {fmtDateTime(run.completed_at)}</span>
            )}
            {/* Who sealed the day. "Closed automatically" is a real answer,
                not a missing one -- and it is the answer that matters when a
                run closed over an uncounted till. */}
            <span>
              by {run.run_by_name ?? 'the schedule (automatic)'}
            </span>
            {run.run_number > 1 && <span>attempt {run.run_number}</span>}
          </div>
        </div>

        {/* --------------------------------------------- what it billed --- */}
        <div className="mx-6 mt-3 grid grid-cols-3 gap-px border border-slate-200 bg-slate-200">
          {[
            { label: 'Rooms occupied', value: String(rooms) },
            { label: 'Charges posted', value: String(posted) },
            { label: 'Amount before tax', value: money(amount) },
          ].map((f) => (
            <div key={f.label} className="bg-white px-4 py-2.5">
              <div className="text-[10px] uppercase tracking-wide text-slate-400">
                {f.label}
              </div>
              <div className="text-lg font-semibold text-slate-800">
                {f.value}
              </div>
            </div>
          ))}
        </div>

        {failed.length > 0 && (
          <div className="border-b border-red-100 bg-red-50 px-6 py-3">
            <div className="flex items-center gap-2 text-sm font-medium text-red-800">
              <AlertTriangle size={15} />
              {failed.length} step(s) failed — the day may not be fully closed.
            </div>
          </div>
        )}

        {figures && (
          <>
            <Section n="01" title="Room charges"
              note={`${(run.steps.find((st) => st.step_code === 'post_room_charges')
                        ?.detail?.lines as unknown[] | undefined)?.length ?? 0} billed`}>
              <ChargeDetail run={run} />
            </Section>

            <Section n="02" title="Checked out"
              note={`${figures.departures.length} departure${
                figures.departures.length === 1 ? '' : 's'}`}>
              <Departures rows={figures.departures} />
            </Section>

            <Section n="03" title="Daily sales"
              note="By what earned it">
              <SalesTable data={figures} />
            </Section>

            <Section n="04" title="Receipts — detail"
              note={`${figures.receipts.length} receipt${
                figures.receipts.length === 1 ? '' : 's'}`}>
              <ReceiptDetail rows={figures.receipts}
                total={figures.receipts_total} />
            </Section>

            <Section n="05" title="Receipts — summary"
              note="By method and by cashier">
              {figures.receipts_by_method.length > 0 && (
                <div className="grid gap-3 sm:grid-cols-2">
                  <div>
                    <div className="text-[11px] uppercase tracking-wide text-slate-400">
                      By payment method
                    </div>
                    <ul className="mt-1 space-y-1">
                      {figures.receipts_by_method.map((r) => (
                        <li key={r.label}
                          className="flex justify-between text-xs text-slate-600">
                          <span>{r.label}
                            <span className="ml-1 text-slate-400">
                              ({r.count})
                            </span>
                          </span>
                          <span className="font-medium text-slate-800">
                            {money(r.amount)}
                          </span>
                        </li>
                      ))}
                    </ul>
                  </div>
                  <div>
                    <div className="text-[11px] uppercase tracking-wide text-slate-400">
                      By cashier
                    </div>
                    <ul className="mt-1 space-y-1">
                      {figures.receipts_by_user.map((r) => (
                        <li key={r.label}
                          className="flex justify-between text-xs text-slate-600">
                          <span>{r.label}</span>
                          <span className="font-medium text-slate-800">
                            {money(r.amount)}
                          </span>
                        </li>
                      ))}
                    </ul>
                  </div>
                </div>
              )}
              {figures.receipts_by_method.length === 0 && (
                <p className="text-xs text-slate-400">
                  No money was taken on this day.
                </p>
              )}
              {/* The two summaries describe the same receipts; adding them
                  together would double the day. */}
              <p className="mt-2 text-[11px] text-slate-400">
                Both views describe the same receipts — their totals are not
                additive.
              </p>
            </Section>

            <Section n="06" title="Balance"
              note="Charged, collected, still owed">
              <BalanceBlock data={figures} />
            </Section>

            <Section n="07" title="The house"
              note={figures.occupancy ? 'As the day closed' : undefined}>
              {figures.occupancy ? (
                <div className="grid grid-cols-4 gap-2 text-center">
                  {[
                    ['Occupied', figures.occupancy.occupied],
                    ['Vacant', figures.occupancy.vacant],
                    ['Arrivals', figures.occupancy.arrivals],
                    ['Departures', figures.occupancy.departures],
                    ['Due out', figures.occupancy.due_out],
                    ['Adults', figures.occupancy.adults],
                    ['Children', figures.occupancy.children],
                    ['Total rooms', figures.occupancy.total_rooms],
                  ].map(([label, v]) => (
                    <div key={String(label)} className="rounded-lg bg-slate-50 py-2">
                      <div className="text-base font-semibold text-slate-800">
                        {String(v)}
                      </div>
                      <div className="text-[11px] text-slate-400">{label}</div>
                    </div>
                  ))}
                </div>
              ) : (
                /* Never invented after the fact: asking "how many rooms were
                   occupied" today gives today's answer, not that night's. */
                <p className="text-xs text-slate-400">
                  Not recorded — this run predates the occupancy snapshot.
                </p>
              )}
            </Section>

            <Section n="08" title="Pax status" note="Guest movements">
              {/* These reconcile by construction: arrived minus checked out
                  is what stayed. The reference report they follow did not —
                  its room status said nobody departed on a day its own
                  checkout section listed one. */}
              <PaxTable rows={figures.pax_status} first="Movement" />
            </Section>

            <Section n="09" title="Pax analysis" note="By rate type">
              <PaxTable rows={figures.pax_by_rate_type} first="Rate type" />
            </Section>
          </>
        )}

        {/* ------------------------------------------------- the steps --- */}
        <div className="border-t border-slate-100 px-6 py-5">
          <div className="text-[11px] uppercase tracking-wide text-slate-400">
            What the audit did
          </div>
          <ol className="mt-3">
            {ordered.map((st, i) => {
              const bad = st.status === 'failed'
              const last = i === ordered.length - 1
              return (
                <li key={`${st.step_code}-${i}`} className="flex gap-3">
                  {/* The rail: a sequence, drawn as one. */}
                  <div className="flex flex-col items-center">
                    <span className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full ${
                      bad ? 'bg-red-100 text-red-600'
                          : 'bg-emerald-100 text-emerald-700'}`}>
                      {bad ? <X size={12} strokeWidth={3} />
                           : <Check size={12} strokeWidth={3} />}
                    </span>
                    {!last && <span className="w-px flex-1 bg-slate-200" />}
                  </div>
                  <div className={`min-w-0 flex-1 ${last ? '' : 'pb-4'}`}>
                    <div className="text-sm font-medium text-slate-800">
                      {STEP_LABEL[st.step_code] ?? st.step_code}
                    </div>
                    <div className="text-xs text-slate-500">
                      {stepDetail(st.step_code, st.detail)}
                    </div>
                    {st.error && (
                      <div className="mt-1 rounded-md bg-red-50 px-2 py-1 text-xs text-red-700">
                        {st.error}
                      </div>
                    )}
                  </div>
                </li>
              )
            })}
            {ordered.length === 0 && (
              <li className="py-3 text-sm text-slate-400">
                This run recorded no steps.
              </li>
            )}
          </ol>
        </div>

        {/* ---------------------------------------------------- footer --- */}
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 bg-slate-50 px-6 py-3.5">
          <span className="flex items-center gap-2 text-xs text-slate-500">
            {run.status === 'completed' && !failed.length ? (
              <>
                <Lock size={13} className="shrink-0" />
                Postings to {shortDate(run.business_date)} are locked.
              </>
            ) : (
              <>
                <AlertTriangle size={13} className="shrink-0 text-amber-500" />
                {shortDate(run.business_date)} is not closed — postings to it
                are still open.
              </>
            )}
          </span>
          <div className="print-hide flex items-center gap-2">
            <button type="button" onClick={() => window.print()}
              className="flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-100">
              <Printer size={13} /> Print
            </button>
            <button type="button" onClick={onClose}
              className="rounded-lg bg-brand px-4 py-1.5 text-xs font-medium text-white hover:opacity-90">
              Close
            </button>
          </div>
        </div>
      </div>
    </div>,
    document.body,
  )
}
