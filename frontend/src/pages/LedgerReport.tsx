import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  Building2, CheckCircle2, ChevronLeft, ChevronRight, Download, FileText,
  Gift, Info, Loader2, Printer, Receipt, Search, UserRound, Wallet, X,
} from 'lucide-react'
import {
  getLedgerReport,
  type LedgerGuestRow, type LedgerJournalRow, type LedgerLine,
  type LedgerQuery,
} from '../api'
import DateField from '../components/DateField'
import Select from '../components/Select'
import { useActivePropertyId } from '../hooks/useProperty'
import { downloadCsv, datedName } from '../lib/csv'
import {
  CONTROL, CONTROL_TYPE, FILTER_BOX, FILTER_BOX_ICON, FILTER_SELECT,
} from '../lib/controls'
import { fmtDate } from '../lib/dates'

/**
 * Hotel Ledger Report.
 *
 * Opening, debit, credit and closing per ledger, then the accounts inside the
 * one ledger this system actually keeps. Every figure is read out of
 * `finance.folio_entries` — the same rows the folio and cashiering screens
 * show — so this report cannot disagree with them.
 *
 * **It does not claim to be "balanced".** This is a folio ledger, not a
 * general ledger: a charge is a debit against a guest with no contra entry,
 * so debits and credits are not meant to match, and a green tick saying they
 * do would be a reassuring lie on a finance screen. What it shows instead is
 * the arithmetic of its own summary, recomputed — opening + debit − credit =
 * closing — so a reader can watch the figures tie rather than be told they do.
 *
 * Three of the four ledgers have nothing behind them yet. They are shown and
 * disabled with the reason on hover, the same way Housekeeping treats its
 * unbuilt tabs: the shape of the module is part of the design, and a row of
 * zeroes reads as a finding rather than an absence.
 */

const money = new Intl.NumberFormat('en-IN', {
  style: 'currency', currency: 'INR', minimumFractionDigits: 2,
})
const n = (v: string | number) => Number(v)

/** A balance, coloured by who owes whom. Positive is owed to the resort,
 *  negative is owed back — two different problems, and one colour for "not
 *  zero" would hide which one you are looking at.
 *
 *  `liability` turns that off, because on some ledgers a credit balance is not
 *  a problem at all. A deposit ledger holding nothing is the empty one; money
 *  sitting in it is the whole point — it is the guests' money, held against
 *  stays that have not happened. Painting that red says a hotel doing exactly
 *  the right thing has something wrong with it, and the one balance that
 *  genuinely wants attention loses its colour by being one of many. */
function Balance({ v, bold, liability }: {
  v: number; bold?: boolean; liability?: boolean
}) {
  const tone = liability && v < 0
    ? 'text-slate-700'
    : v === 0 ? 'text-positive' : v > 0 ? 'text-caution' : 'text-red-600'
  return (
    <span className={`${bold ? 'font-semibold' : 'font-medium'} ${tone}`}>
      {money.format(v)}
      {liability && v < 0 && (
        <span className="ml-1.5 align-middle text-[11px] font-normal text-slate-400">
          held
        </span>
      )}
    </span>
  )
}

/** Ledgers whose natural resting state is a credit balance. Money in them is
 *  owed *by* the hotel and always was — it is not a shortfall that appeared. */
const LIABILITY_LEDGERS = new Set(['deposit'])

/** True when the hotel's closing balance is negative and every rupee of that
 *  comes from a liability ledger — money held on someone's behalf rather than
 *  a hole. Once a guest ledger goes into credit as well, this is false and the
 *  total goes back to being the warning it should be. */
function heldOnly(ledgers: LedgerLine[], closing: number): boolean {
  if (closing >= 0) return false
  const held = ledgers
    .filter((l) => LIABILITY_LEDGERS.has(l.key))
    .reduce((t, l) => t + n(l.closing), 0)
  // Everything that is not held money, taken together, is square or better.
  return closing - held >= 0
}

const LEDGER_ICON: Record<string, typeof UserRound> = {
  guest: UserRound, deposit: Wallet, city: Building2, package: Gift,
  transactions: Receipt,
}

const STAY_LABEL: Record<string, string> = {
  reserved: 'Reserved', confirmed: 'Confirmed', tentative: 'Tentative',
  checked_in: 'In-house', checked_out: 'Checked out',
  cancelled: 'Cancelled', no_show: 'No show',
}

export default function LedgerReportScreen() {
  const propertyId = useActivePropertyId()
  const [tab, setTab] = useState('guest')
  const [page, setPage] = useState(1)
  const [perPage, setPerPage] = useState(10)
  const [picked, setPicked] = useState<LedgerGuestRow | null>(null)

  // A draft the panel edits, and the query the report was actually run with.
  // Kept apart on purpose: this is a report, and half-typed filters should
  // not send it running on every keystroke. "Run Report" is what applies them.
  const [draft, setDraft] = useState<LedgerQuery>({})
  const [applied, setApplied] = useState<LedgerQuery>({})
  const set = (k: keyof LedgerQuery) => (v: string) =>
    setDraft((x) => ({ ...x, [k]: v }))

  const q = useQuery({
    queryKey: ['ledger-report', propertyId, applied],
    queryFn: () => getLedgerReport(propertyId, applied),
    enabled: propertyId !== '',
  })
  const d = q.data

  function run() {
    setPage(1)
    setPicked(null)
    setApplied(draft)
  }
  function reset() {
    setDraft({})
    setApplied({})
    setPage(1)
    setPicked(null)
  }

  function exportGuest() {
    if (!d) return
    downloadCsv(
      datedName('guest-ledger', d.date_to),
      ['Room', 'Guest', 'Confirmation', 'Arrival', 'Departure', 'Folio',
       'Room charges', 'Other charges', 'Payments', 'Balance'],
      d.guest_rows.map((g) => [
        g.room_code, g.guest_name, g.confirmation_no,
        g.arrival_date ? fmtDate(g.arrival_date) : '',
        g.departure_date ? fmtDate(g.departure_date) : '',
        g.folio_no, g.room_charges, g.other_charges, g.payments, g.balance,
      ]),
    )
  }

  function exportJournal() {
    if (!d) return
    downloadCsv(
      datedName('ledger-transactions', d.date_from),
      ['Business date', 'Particulars', 'Guest', 'Confirmation', 'Folio',
       'Debit', 'Credit', 'Folio balance'],
      d.journal.map((j) => [
        fmtDate(j.business_date), j.particulars, j.guest_name,
        j.confirmation_no, j.folio_no, j.debit, j.credit, j.running,
      ]),
    )
  }

  if (q.isLoading) {
    return (
      <p className="flex items-center gap-2 py-16 text-sm text-slate-400">
        <Loader2 size={15} className="animate-spin" /> Reading the ledger…
      </p>
    )
  }
  if (q.isError || !d) {
    return (
      <p className="py-16 text-center text-sm text-slate-500">
        The ledger could not be read. {(q.error as Error)?.message}
      </p>
    )
  }

  // The page of accounts this table renders. The total in the footer stays
  // the whole ledger, and says so -- a total that silently meant "this page"
  // would be a number somebody reconciles against and gets wrong.
  const shown = d.guest_rows.slice((page - 1) * perPage, page * perPage)

  const tabs = [
    ...d.ledgers.map((l) => ({
      key: l.key, label: l.label,
      available: l.available, reason: l.reason,
      // What the table under this tab will actually list. It used to be
      // `accounts` until you clicked, and `guest_rows.length` after -- so a
      // ledger whose accounts were all settled read 18, then 0.
      count: l.open_accounts,
    })),
    {
      key: 'transactions', label: 'Transactions', available: true,
      reason: null, count: d.journal.length,
    },
  ]

  return (
    <div className={picked ? 'flex gap-4' : ''}>
    <div className="min-w-0 flex-1 space-y-4">
      {/* ---------------------------------------------------------- head --- */}
      <nav className="flex items-center gap-1.5 text-xs text-slate-400">
        <Link to="/reports" className="hover:text-slate-600">Reports</Link>
        <ChevronRight size={12} />
        <Link to="/reports?category=Finance%20%26%20ledgers"
          className="hover:text-slate-600">Finance &amp; ledgers</Link>
        <ChevronRight size={12} />
        <span className="font-medium text-slate-600">Hotel Ledger Report</span>
      </nav>

      <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
        <div>
          <h1 className="text-display text-ink">Hotel Ledger Report</h1>
          <p className="mt-1 text-sm text-slate-500">
            Reconcile guest, deposit, city ledger and package balances by
            business date.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button onClick={tab === 'transactions' ? exportJournal : exportGuest}
            className={`flex items-center gap-2 ${FILTER_BOX} ${CONTROL} hover:bg-slate-50`}>
            <Download size={15} /> Export
          </button>
          <button onClick={() => window.print()}
            className={`flex items-center gap-2 ${FILTER_BOX} ${CONTROL} hover:bg-slate-50`}>
            <Printer size={15} /> Print
          </button>
        </div>
      </div>

      {/* -------------------------------------------------------- filters --- */}
      {/* A panel, not a filter bar: a report is run rather than browsed, so
          ten fields are set together and applied once. Labels sit above each
          field here -- unlike the list screens, where a control's own default
          names it -- because "All Codes" does not tell you it means the
          transaction code. */}
      <section className="rounded-xl border border-slate-100 bg-white p-4">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
          <label className="text-xs text-slate-500">
            <span className="block">Business Date From</span>
            <DateField className="mt-1 w-full"
              value={draft.date_from ?? d.date_from}
              onChange={set('date_from')}
              max={draft.date_to ?? d.date_to} label="Business date from" />
          </label>
          <label className="text-xs text-slate-500">
            <span className="block">Business Date To</span>
            <DateField className="mt-1 w-full"
              value={draft.date_to ?? d.date_to}
              onChange={set('date_to')}
              min={draft.date_from ?? d.date_from} label="Business date to" />
          </label>
          <label className="text-xs text-slate-500">
            <span className="block">Ledger Type</span>
            <Select blankIsChoice className={`mt-1 w-full ${FILTER_SELECT}`}
              value={draft.ledger_type ?? ''}
              onChange={(e) => set('ledger_type')(e.target.value)}>
              <option value="">All Ledger Types</option>
              {d.options.ledger_types.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </Select>
          </label>
          <label className="text-xs text-slate-500">
            <span className="block">Account / Guest / Company</span>
            <span className="relative mt-1 block">
              <Search size={14} className="absolute left-3 top-2.5 text-slate-400" />
              <input value={draft.q ?? ''}
                onChange={(e) => set('q')(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') run() }}
                placeholder="Search by name, confirmation or folio"
                className={`w-full ${FILTER_BOX_ICON} text-sm text-slate-700 outline-none focus:border-brand`} />
            </span>
          </label>
          <label className="text-xs text-slate-500">
            <span className="block">Room No.</span>
            <Select blankIsChoice className={`mt-1 w-full ${FILTER_SELECT}`}
              value={draft.room ?? ''}
              onChange={(e) => set('room')(e.target.value)}>
              <option value="">All Rooms</option>
              {d.options.rooms.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </Select>
          </label>
          <label className="text-xs text-slate-500">
            <span className="block">Transaction Code</span>
            <Select blankIsChoice className={`mt-1 w-full ${FILTER_SELECT}`}
              value={draft.transaction_code ?? ''}
              onChange={(e) => set('transaction_code')(e.target.value)}>
              <option value="">All Codes</option>
              {d.options.transaction_codes.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </Select>
          </label>
          <label className="text-xs text-slate-500">
            <span className="block">Payment Method</span>
            <Select blankIsChoice className={`mt-1 w-full ${FILTER_SELECT}`}
              value={draft.payment_method ?? ''}
              onChange={(e) => set('payment_method')(e.target.value)}>
              <option value="">All Methods</option>
              {d.options.payment_methods.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </Select>
          </label>
          <label className="text-xs text-slate-500">
            <span className="block">Market / Source</span>
            <Select blankIsChoice className={`mt-1 w-full ${FILTER_SELECT}`}
              value={draft.market ?? ''}
              onChange={(e) => set('market')(e.target.value)}>
              <option value="">All Markets</option>
              {d.options.markets.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </Select>
          </label>
          <label className="text-xs text-slate-500">
            <span className="block">Reservation / Confirmation No.</span>
            <input value={draft.confirmation_no ?? ''}
              onChange={(e) => set('confirmation_no')(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') run() }}
              placeholder="e.g. CBR6916AB46"
              className={`mt-1 w-full ${FILTER_BOX} text-sm text-slate-700 outline-none focus:border-brand`} />
          </label>
          <label className="text-xs text-slate-500">
            <span className="block">Folio / Bill No.</span>
            <input value={draft.folio_no ?? ''}
              onChange={(e) => set('folio_no')(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') run() }}
              placeholder="e.g. FOL-1020"
              className={`mt-1 w-full ${FILTER_BOX} text-sm text-slate-700 outline-none focus:border-brand`} />
          </label>
          <div className="flex items-end gap-2">
            {Object.values(applied).some(Boolean) && (
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
      </section>

      {/* ------------------------------------------------------- summary --- */}
      <section className="overflow-hidden rounded-xl border border-slate-100 bg-white">
        <header className="border-b border-slate-100 px-4 py-3">
          {/* "As on", not a range: the Opening and Closing columns are
              positions at two instants, and the closing one is this date. */}
          <h2 className="text-section font-semibold text-slate-800">
            Ledger Summary{' '}
            <span className="text-base font-normal text-slate-400">
              (As on {fmtDate(d.date_to)})
            </span>
          </h2>
        </header>
        <div className="grid gap-4 p-4 lg:grid-cols-[minmax(0,1fr)_18rem]">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[560px] border-collapse overflow-hidden rounded-lg text-sm [&_td]:border [&_td]:border-slate-100 [&_th]:border [&_th]:border-slate-100">
              <thead className="bg-slate-50/80 text-left text-xs font-semibold text-slate-500">
                <tr>
                  <th className="px-3 py-2 font-semibold">Ledger</th>
                  <th className="px-3 py-2 text-right font-semibold">Opening Balance</th>
                  <th className="px-3 py-2 text-right font-semibold">Debit</th>
                  <th className="px-3 py-2 text-right font-semibold">Credit</th>
                  <th className="px-3 py-2 text-right font-semibold">Closing Balance</th>
                </tr>
              </thead>
              <tbody>
                {d.ledgers.map((l: LedgerLine) => (
                  <tr key={l.key} title={l.reason ?? undefined}
                    className={l.available ? '' : 'text-slate-300'}>
                    <td className="px-3 py-2 font-medium">
                      {l.label}
                      {!l.available && (
                        <span className="ml-2 rounded bg-slate-75 px-1.5 py-0.5 text-[11px] font-semibold text-slate-400">
                          none yet
                        </span>
                      )}
                    </td>
                    <td className="px-3 py-2 text-right">{money.format(n(l.opening))}</td>
                    <td className="px-3 py-2 text-right">{money.format(n(l.debit))}</td>
                    <td className="px-3 py-2 text-right">{money.format(n(l.credit))}</td>
                    <td className="px-3 py-2 text-right">
                      {l.available
                        ? <Balance v={n(l.closing)}
                            liability={LIABILITY_LEDGERS.has(l.key)} />
                        : money.format(0)}
                    </td>
                  </tr>
                ))}
              </tbody>
              <tfoot className="bg-brand/5 font-semibold text-brand-dark">
                <tr>
                  <td className="px-3 py-2.5">{d.hotel.label}</td>
                  <td className="px-3 py-2.5 text-right">{money.format(n(d.hotel.opening))}</td>
                  <td className="px-3 py-2.5 text-right">{money.format(n(d.hotel.debit))}</td>
                  <td className="px-3 py-2.5 text-right">{money.format(n(d.hotel.credit))}</td>
                  <td className="px-3 py-2.5 text-right">
                    {/* The total is only a shortfall if something other than
                        held money made it negative. A hotel whose whole
                        negative is guests' deposits is not down anything. */}
                    <Balance v={n(d.hotel.closing)} bold
                      liability={heldOnly(d.ledgers, n(d.hotel.closing))} />
                  </td>
                </tr>
              </tfoot>
            </table>
          </div>

          {/* Where the mockup put a green "Balanced" tick. This says the thing
              that is actually true of a folio ledger, and shows the sum. */}
          {/* The mockup's green tile. The tick and the colour are kept; the
              words are not -- it said "Status: Balanced", and a folio ledger
              is never balanced in the double-entry sense. What is green here
              is that the report's own arithmetic holds. */}
          <div className={`flex flex-col justify-center rounded-xl border p-4 text-center ${
            d.check.ties
              ? 'border-emerald-200 bg-emerald-50/70'
              : 'border-red-200 bg-red-50/70'}`}>
            <span className={`mx-auto grid h-14 w-14 place-items-center rounded-full ${
              d.check.ties ? 'bg-emerald-100' : 'bg-red-100'}`}>
              <CheckCircle2
                className={`h-8 w-8 ${d.check.ties ? 'text-positive' : 'text-red-600'}`} />
            </span>
            <p className={`mt-3 text-base font-bold ${
              d.check.ties ? 'text-emerald-800' : 'text-red-700'}`}>
              {d.check.ties ? 'Figures tie' : 'Figures do not tie'}
            </p>
            <p className={`mt-1.5 text-xs ${
              d.check.ties ? 'text-emerald-700/80' : 'text-red-700/80'}`}>
              {money.format(n(d.check.opening))} + {money.format(n(d.check.debit))}
              {' − '}{money.format(n(d.check.credit))}
              {' = '}
              <span className="font-semibold">{money.format(n(d.check.closing))}</span>
            </p>
            {/* Short on screen so this tile is never taller than the table
                it sits beside; the full reasoning is on hover. */}
            <p className="mt-2 text-xs leading-relaxed text-slate-500"
              title={d.check.note_long}>
              {d.check.note}
            </p>
          </div>
        </div>
      </section>

      {/* ---------------------------------------------------------- tabs --- */}
      <section className="overflow-hidden rounded-xl border border-slate-100 bg-white">
        <div className="flex flex-wrap gap-1 border-b border-slate-200 px-2">
          {tabs.map((t) => {
            const Icon = LEDGER_ICON[t.key] ?? Receipt
            return t.available ? (
              <button key={t.key}
                onClick={() => {
                  setTab(t.key)
                  setPage(1)
                  setPicked(null)
                  // The rows come from the server, so choosing a ledger is a
                  // question to ask it — there is nothing in the browser to
                  // filter down to a ledger it was never sent.
                  if (t.key !== 'transactions') {
                    setDraft((x) => ({ ...x, ledger_type: t.key }))
                    setApplied((x) => ({ ...x, ledger_type: t.key }))
                  }
                }}
                className={`flex items-center gap-1.5 whitespace-nowrap px-4 py-2.5 ${CONTROL_TYPE} ${
                  tab === t.key ? 'border-b-2 border-brand text-brand'
                    : 'text-slate-500 hover:text-slate-700'}`}>
                <Icon size={15} /> {t.label}
                {t.count > 0 && (
                  <span className="text-xs text-slate-400">{t.count}</span>
                )}
              </button>
            ) : (
              // Shown and disabled rather than dropped: the module's shape is
              // part of the design, and the reason is the useful part.
              <span key={t.key} title={t.reason ?? undefined}
                className={`flex cursor-not-allowed items-center gap-1.5 whitespace-nowrap px-4 py-2.5 ${CONTROL_TYPE} text-slate-300`}>
                <Icon size={15} /> {t.label}
              </span>
            )
          })}
        </div>

        {tab !== 'transactions' && (
          d.guest_rows.length === 0 ? (
            <p className="px-4 py-12 text-center text-sm text-slate-500">
              {(() => {
                const l = d.ledgers.find((x) => x.key === d.rows_ledger)
                if (!l?.available) return l?.reason ?? 'Nothing in this ledger yet.'
                // Settled and never used look identical in an empty table, and
                // they mean opposite things: one is finished business, the
                // other is none.
                if (l.accounts === 0) {
                  return 'No account in this ledger has been used in this range.'
                }
                // "Nothing is outstanding" is a bigger claim than this tab is
                // entitled to make: it can only see its own ledger, and the
                // hotel may still owe money sitting one tab over. Say where.
                const elsewhere = d.ledgers.filter(
                  (x) => x.key !== l.key && x.open_accounts > 0)
                const settled =
                  `All ${l.accounts} accounts in this ledger are settled.`
                if (elsewhere.length === 0) return settled
                return `${settled} ${elsewhere.map((x) =>
                  `${money.format(Math.abs(n(x.closing)))} is still open in the `
                  + `${x.label}`).join(', ')}.`
              })()}
            </p>
          ) : (
            <>
              <p className="flex items-start gap-2 border-b border-slate-100 bg-slate-50/60 px-4 py-2 text-xs text-slate-500">
                <Info size={13} className="mt-0.5 shrink-0" />
                Every guest folio with a balance at this property, not only
                those touched in the range above — a guest who owes money owes
                it whatever window you are looking at.
              </p>
              <div className="overflow-x-auto">
                <table className="w-full min-w-[1000px] border-collapse text-sm [&_th]:whitespace-nowrap [&_td]:border [&_td]:border-slate-100 [&_th]:border [&_th]:border-slate-100">
                  <thead className="bg-slate-50/80 text-left text-xs font-semibold text-slate-500">
                    <tr>
                      <th className="px-3 py-2.5 font-semibold">Room</th>
                      <th className="px-3 py-2.5 font-semibold">Guest Name</th>
                      <th className="px-3 py-2.5 font-semibold">Conf. No.</th>
                      <th className="px-3 py-2.5 font-semibold">Arrival</th>
                      <th className="px-3 py-2.5 font-semibold">Departure</th>
                      <th className="px-3 py-2.5 font-semibold">Folio</th>
                      <th className="px-3 py-2.5 text-right font-semibold">Room Charges</th>
                      <th className="px-3 py-2.5 text-right font-semibold">Other Charges</th>
                      <th className="px-3 py-2.5 text-right font-semibold">Payments / Credits</th>
                      <th className="px-3 py-2.5 text-right font-semibold">Current Balance</th>
                    </tr>
                  </thead>
                  <tbody>
                    {shown.map((g: LedgerGuestRow) => (
                      <tr key={g.folio_id} tabIndex={0}
                        onClick={() => setPicked(g)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter' || e.key === ' ') {
                            e.preventDefault()
                            setPicked(g)
                          }
                        }}
                        className={`cursor-pointer outline-none focus-visible:bg-brand/5 ${
                          picked?.folio_id === g.folio_id
                            ? 'bg-brand/10 [&_td]:text-brand-dark [&_td]:font-medium'
                            : 'hover:bg-slate-50/60'}`}>
                        <td className="px-3 py-2.5 font-medium text-slate-700">
                          {g.room_code ?? '—'}
                        </td>
                        {/* The stay status is in the drawer already. Under the
                            name it cost every row a second line \u2014 and on
                            "Full Amount Test" a third \u2014 for something the
                            mockup does not show at all. It is on hover. */}
                        <td className="whitespace-nowrap px-3 py-2.5">
                          <span className="font-medium text-slate-800"
                            title={g.stay_status
                              ? STAY_LABEL[g.stay_status] ?? g.stay_status
                              : undefined}>
                            {g.guest_name ?? '\u2014'}
                          </span>
                        </td>
                        <td className="px-3 py-2.5 text-slate-600">
                          {g.confirmation_no ?? '—'}
                        </td>
                        <td className="whitespace-nowrap px-3 py-2.5 text-slate-600">
                          {g.arrival_date ? fmtDate(g.arrival_date) : '—'}
                        </td>
                        <td className="whitespace-nowrap px-3 py-2.5 text-slate-600">
                          {g.departure_date ? fmtDate(g.departure_date) : '—'}
                        </td>
                        <td className="px-3 py-2.5 text-slate-500">{g.folio_no}</td>
                        <td className="px-3 py-2.5 text-right text-slate-600">
                          {money.format(n(g.room_charges))}
                        </td>
                        <td className="px-3 py-2.5 text-right text-slate-600">
                          {money.format(n(g.other_charges))}
                        </td>
                        <td className="px-3 py-2.5 text-right text-slate-600">
                          {money.format(n(g.payments))}
                        </td>
                        <td className="px-3 py-2.5 text-right">
                          <Balance v={n(g.balance)} bold />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                  <tfoot className="bg-brand/5 font-semibold text-brand-dark">
                    <tr>
                      <td className="px-3 py-2.5 text-right" colSpan={9}>
                        {shown.length !== d.guest_rows.length && (
                          <span className="mr-2 text-xs font-normal text-slate-400">
                            all {d.guest_rows.length} accounts, not just this page
                          </span>
                        )}
                        {d.ledgers.find((l) => l.key === d.rows_ledger)?.label
                  ?? 'Ledger'} Total:
                      </td>
                      <td className="px-3 py-2.5 text-right">
                        <Balance v={n(d.guest_total)} bold />
                      </td>
                    </tr>
                  </tfoot>
                </table>
              </div>
              <Pager total={d.guest_rows.length} page={page} perPage={perPage}
                noun="guest accounts"
                onPage={setPage} onPerPage={(v) => { setPerPage(v); setPage(1) }} />
            </>
          )
        )}

        {tab === 'transactions' && (
          d.journal.length === 0 ? (
            <p className="px-4 py-12 text-center text-sm text-slate-400">
              Nothing was posted to the ledger in this range.
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[820px] border-collapse text-sm [&_td]:border [&_td]:border-slate-100 [&_th]:border [&_th]:border-slate-100">
                <thead className="bg-slate-50/80 text-left text-xs font-semibold text-slate-500">
                  <tr>
                    <th className="px-3 py-2.5 font-semibold">Date</th>
                    <th className="px-3 py-2.5 font-semibold">Particulars</th>
                    <th className="px-3 py-2.5 font-semibold">Guest / Account</th>
                    <th className="px-3 py-2.5 font-semibold">Folio</th>
                    <th className="px-3 py-2.5 text-right font-semibold">Debit</th>
                    <th className="px-3 py-2.5 text-right font-semibold">Credit</th>
                    <th className="px-3 py-2.5 text-right font-semibold"
                      title="This folio's balance immediately after this entry, counting everything on the folio — including entries outside the dates above. Negative means the guest is in credit: they have paid more than has been charged.">
                      Folio Balance
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {d.journal.map((j: LedgerJournalRow) => (
                    <tr key={j.entry_id} className="hover:bg-slate-50/60">
                      <td className="whitespace-nowrap px-3 py-2.5 text-slate-600">
                        {fmtDate(j.business_date)}
                      </td>
                      <td className="px-3 py-2.5 text-slate-700">{j.particulars}</td>
                      <td className="px-3 py-2.5">
                        {j.reservation_id ? (
                          <Link to={`/reservations/${j.reservation_id}`}
                            className="text-brand hover:underline">
                            {j.guest_name ?? j.confirmation_no ?? 'Guest'}
                          </Link>
                        ) : (j.guest_name ?? '—')}
                      </td>
                      <td className="px-3 py-2.5 text-slate-500">{j.folio_no}</td>
                      <td className="px-3 py-2.5 text-right text-slate-600">
                        {n(j.debit) ? money.format(n(j.debit)) : '—'}
                      </td>
                      <td className="px-3 py-2.5 text-right text-slate-600">
                        {n(j.credit) ? money.format(n(j.credit)) : '—'}
                      </td>
                      <td className="px-3 py-2.5 text-right">
                        <Balance v={n(j.running)} />
                      </td>
                    </tr>
                  ))}
                </tbody>
                {/* What the rows add up to. Without this the only way to check
                    the journal against the Ledger Summary above is to add 52
                    numbers by hand — and these two agreeing is the whole point
                    of showing both. No total under Folio Balance: those belong
                    to different accounts and summing them means nothing. */}
                <tfoot className="bg-slate-50/80 font-semibold text-slate-700">
                  <tr>
                    <td className="px-3 py-2.5" colSpan={3}>
                      {d.journal_truncated
                        ? `First ${d.journal.length} transactions — partial total`
                        : `${d.journal.length} transactions`}
                    </td>
                    <td className="px-3 py-2.5 text-right text-slate-400">Total</td>
                    <td className="px-3 py-2.5 text-right">
                      {money.format(d.journal.reduce((t, j) => t + n(j.debit), 0))}
                    </td>
                    <td className="px-3 py-2.5 text-right">
                      {money.format(d.journal.reduce((t, j) => t + n(j.credit), 0))}
                    </td>
                    <td className="px-3 py-2.5" />
                  </tr>
                </tfoot>
              </table>
              {d.journal_truncated && (
                <p className="border-t border-slate-100 px-4 py-2.5 text-xs text-caution">
                  Showing the first {d.journal.length} entries. Narrow the date
                  range to see the rest.
                </p>
              )}
            </div>
          )
        )}
      </section>
    </div>

    {picked && <GuestDrawer g={picked} onClose={() => setPicked(null)} />}
    </div>
  )
}

/** One account, opened from a row.
 *
 * The mockup's right-hand panel. Every field on it is read from the row the
 * table already has, so opening the drawer costs no request -- and the two
 * can never disagree, which a second fetch would eventually manage.
 */
function GuestDrawer({ g, onClose }: {
  g: LedgerGuestRow; onClose: () => void
}) {
  const bal = n(g.balance)
  const initials = (g.guest_name ?? '?')
    .split(/\s+/).filter(Boolean).slice(0, 2)
    .map((w) => w[0]).join('').toUpperCase() || '?'
  return (
    <aside className="w-[22rem] shrink-0 space-y-4 rounded-xl border border-slate-100 bg-white p-4">
      <header className="flex items-start justify-between gap-3">
        <h2 className="text-section font-semibold text-slate-800">
          Guest / Ledger Details
        </h2>
        <button onClick={onClose} aria-label="Close"
          className="rounded p-1 text-slate-400 hover:bg-slate-50 hover:text-slate-600">
          <X size={16} />
        </button>
      </header>

      <div className="flex items-center gap-3">
        <span className="grid h-11 w-11 shrink-0 place-items-center rounded-full bg-brand/10 text-sm font-semibold text-brand">
          {initials}
        </span>
        <div className="min-w-0">
          <p className="flex flex-wrap items-center gap-2">
            <span className="truncate font-semibold text-slate-800">
              {g.guest_name ?? 'Guest'}
            </span>
            {g.stay_status && (
              <span className={`rounded-md px-2 py-0.5 text-xs font-medium ${
                g.stay_status === 'checked_in'
                  ? 'bg-emerald-50 text-emerald-700'
                  : 'bg-slate-75 text-slate-600'}`}>
                {STAY_LABEL[g.stay_status] ?? g.stay_status}
              </span>
            )}
          </p>
          <p className="text-xs text-slate-500">
            {g.room_code ? `Room ${g.room_code}` : 'No room assigned'}
          </p>
        </div>
      </div>

      <dl className="space-y-1.5 border-t border-slate-100 pt-3 text-sm">
        <Row k="Ledger Type" v="Guest Ledger" />
        <Row k="Account" v={g.guest_name ?? '\u2014'} />
        <Row k="Reservation No." v={g.confirmation_no ?? '\u2014'} />
        <Row k="Folio No." v={g.folio_no} />
        <Row k="Arrival" v={g.arrival_date ? fmtDate(g.arrival_date) : '\u2014'} />
        <Row k="Departure" v={g.departure_date ? fmtDate(g.departure_date) : '\u2014'} />
        <Row k="Room" v={g.room_code ?? '\u2014'} />
      </dl>

      <dl className="space-y-1.5 border-t border-slate-100 pt-3 text-sm">
        <Row k="Room Charges" v={money.format(n(g.room_charges))} />
        <Row k="Other Charges" v={money.format(n(g.other_charges))} />
        <Row k="Payments / Credits" v={money.format(n(g.payments))} />
      </dl>

      <div className="flex items-center justify-between rounded-lg bg-slate-50 px-3 py-2.5">
        <span className="text-sm font-medium text-slate-600">Current Balance</span>
        <Balance v={bal} bold />
      </div>

      {/* Composed from the row rather than stored. A narration field would be
          another thing to keep true; this cannot go stale because it is only
          ever a reading of the figures beside it. */}
      <p className="rounded-lg bg-slate-50/70 p-3 text-xs leading-relaxed text-slate-500">
        {bal > 0
          ? `This folio still owes ${money.format(bal)}.`
          : bal < 0
            ? `This guest has paid ${money.format(Math.abs(bal))} more than has been billed to the folio.`
            : 'This folio is settled.'}
        {' '}
        {g.stay_status === 'checked_out' && bal !== 0
          ? 'The stay has ended, so this needs settling rather than waiting.'
          : g.stay_status === 'checked_in'
            ? 'The guest is still in house, so charges may still be posted.'
            : ''}
      </p>

      <div className="flex flex-wrap gap-2">
        {g.reservation_id && (
          <>
            <Link to={`/reservations/${g.reservation_id}/folio`}
              className={`flex flex-1 items-center justify-center gap-2 ${FILTER_BOX} ${CONTROL} hover:bg-slate-50`}>
              <FileText size={15} /> View Folio
            </Link>
            <Link to={`/reservations/${g.reservation_id}`}
              className={`flex flex-1 items-center justify-center gap-2 ${FILTER_BOX} ${CONTROL} hover:bg-slate-50`}>
              <Receipt size={15} /> Reservation
            </Link>
          </>
        )}
      </div>
    </aside>
  )
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="text-slate-500">{k}</dt>
      <dd className="min-w-0 break-words text-right font-medium text-slate-800">
        {v}
      </dd>
    </div>
  )
}

/** Page controls, shaped like the mockup's. */
function Pager({ total, page, perPage, noun, onPage, onPerPage }: {
  total: number; page: number; perPage: number; noun: string
  onPage: (p: number) => void; onPerPage: (v: number) => void
}) {
  const pages = Math.max(1, Math.ceil(total / perPage))
  const from = total === 0 ? 0 : (page - 1) * perPage + 1
  const to = Math.min(total, page * perPage)
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 px-4 py-2.5">
      <p className="text-xs text-slate-500">
        Showing {from} &ndash; {to} of {total} {noun}
      </p>
      <div className="flex items-center gap-1.5">
        <button onClick={() => onPage(Math.max(1, page - 1))}
          disabled={page <= 1} aria-label="Previous page"
          className="rounded-lg border border-slate-200 p-1.5 text-slate-500 hover:bg-slate-50 disabled:opacity-40">
          <ChevronLeft size={15} />
        </button>
        {Array.from({ length: pages }, (_, i) => i + 1).slice(0, 6).map((p) => (
          <button key={p} onClick={() => onPage(p)}
            className={`min-w-[2rem] rounded-lg px-2 py-1 text-xs font-semibold ${
              p === page ? 'bg-brand text-white'
                : 'border border-slate-200 text-slate-600 hover:bg-slate-50'}`}>
            {p}
          </button>
        ))}
        <button onClick={() => onPage(Math.min(pages, page + 1))}
          disabled={page >= pages} aria-label="Next page"
          className="rounded-lg border border-slate-200 p-1.5 text-slate-500 hover:bg-slate-50 disabled:opacity-40">
          <ChevronRight size={15} />
        </button>
        <Select value={String(perPage)}
          onChange={(e) => onPerPage(Number(e.target.value))}
          className={`ml-1 ${FILTER_SELECT}`}>
          {[10, 25, 50].map((v) => (
            <option key={v} value={String(v)}>{v} per page</option>
          ))}
        </Select>
      </div>
    </div>
  )
}
