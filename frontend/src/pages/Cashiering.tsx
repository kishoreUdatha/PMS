import { useEffect, useState } from 'react'
import { fmtDate, fmtTime } from '../lib/dates'
import DateField from '../components/DateField'
import Select from '../components/Select'
import { CONTROL_TYPE, FILTER_SELECT } from '../lib/controls'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { downloadCsv, datedName } from '../lib/csv'
import { useFlash } from '../hooks/useFlash'
import {
  AlertTriangle, Banknote, CheckCircle2, Clock, CreditCard, FileText, Info,
  Loader2, Plus, Printer, Search, Send, Square, Undo2, Wallet,
  X, Download,
} from 'lucide-react'
import {
  getCashSummary, getCashOptions, getCashTxns, getCashPayment, getCashShifts,
  recordCashDrop,
  openCashShift, closeCashShift, getOpenFolios, collectCashPayment,
  
  type CashTxn, type CashOptions, type CashShift, type OpenFolio,
} from '../api'
import { useActivePropertyId } from '../hooks/useProperty'
import { useMethodLabel } from '../lib/paymentMethods'

/**
 * Screen 036 — Payment & Cashiering Centre.
 *
 * Every figure here is read back out of the ledger. Collecting a payment calls
 * the same `post_payment` the deposit screen and checkout call, so there is one
 * record of money in this system and this screen is not a second one.
 *
 * Two things the mockup shows are not pretended. There are no **outlets** —
 * POS is not built — so the column says where the money genuinely came from:
 * the desk, a deposit, a checkout. And **Send to Guest** is absent: there is no
 * mail transport, so the receipt prints and nothing claims an email went out.
 */

const money = new Intl.NumberFormat('en-IN', {
  style: 'currency', currency: 'INR', maximumFractionDigits: 0,
})
const exact = new Intl.NumberFormat('en-IN', {
  style: 'currency', currency: 'INR', minimumFractionDigits: 2,
})
const day = (s?: string | null) => {
  if (!s) return '—'
  const d = new Date(`${s.slice(0, 10)}T00:00:00`)
  return fmtDate(d)
}
const at = (iso?: string | null) => fmtTime(iso)
const stamp = (iso?: string | null) =>
  iso ? `${day(iso)}, ${at(iso)}` : '—'

// Two helpers lived here that showed a date only when it differed from the
// one already on screen. They were the clever answer to "opened 19:07, closed
// 17:55" — and the wrong one: a reader had to know the rule to trust the
// reading. Every time now carries its own date, so there is no rule to know.

const METHOD_TINT: Record<string, string> = {
  cash: 'bg-emerald-100 text-emerald-700',
  card: 'bg-sky-100 text-sky-700',
  upi: 'bg-violet-100 text-violet-700',
  bank_transfer: 'bg-amber-100 text-amber-700',
  cheque: 'bg-slate-75 text-slate-600',
  wallet: 'bg-rose-100 text-rose-700',
}

const input = 'w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700 outline-none focus:border-brand'
const select = `${FILTER_SELECT} bg-white outline-none focus:border-brand`


/* ------------------------------------------------------------- KPI cards --- */
/**
 * One figure, sized to stand in a row of filter controls.
 *
 * This began as a card: a 44px icon disc, a 3xl number, a caption, and a
 * footnote, five across. The five became two, then the two had to share a
 * line with the filters -- because the thing this screen is actually for is
 * the transaction table, and every row of chrome above it is a row of
 * payments nobody can see.
 *
 * So it is a pill the same height as the dropdowns beside it, and the detail
 * that used to be a footnote is on `title` instead: the method split and the
 * folio count are things you go looking for once, not things to read every
 * time the page loads.
 */
function Stat({ icon, tint, label, value, trend, trendTone, onClick, active,
  title }: {
  icon: React.ReactNode; tint: string; label: string; value: string
  trend?: React.ReactNode; trendTone?: string
  onClick?: () => void; active?: boolean; title?: string
}) {
  // A figure you can act on becomes a button; the rest stay plain, so nothing
  // looks clickable that isn't.
  const Tag = (onClick ? 'button' : 'div') as 'button' | 'div'
  return (
    <Tag onClick={onClick} title={[label, value, title].filter(Boolean).join(' — ')}
      className={`flex shrink-0 items-center gap-2 rounded-lg border px-2.5 py-1.5 text-left ${
        active ? 'border-brand bg-brand'
          : onClick ? 'border-slate-200 bg-white hover:border-slate-300' : 'border-slate-200 bg-white'}`}>
      {/* The word and the comparison are the parts that give way when the row
          is tight -- there is no width at which eight controls and two full
          captions fit, and a wrapped filter bar is worse than an abbreviated
          one. The figure and its coloured icon always stay, and `title`
          carries the full reading at every width. */}
      <span className={`grid h-6 w-6 shrink-0 place-items-center rounded-md ${tint}`}>
        {icon}
      </span>
      <span className={`hidden text-xs sm:inline ${active ? 'text-white/80' : 'text-slate-500'}`}>{label}</span>
      <span className={`text-sm font-semibold ${active ? 'text-white' : 'text-slate-800'}`}>{value}</span>
      {trend && (
        <span className={`hidden text-xs lg:inline ${active ? 'text-white/80' : trendTone ?? 'text-slate-400'}`}>
          {trend}
        </span>
      )}
    </Tag>
  )
}

/* --------------------------------------------------------------- the page --- */
/** Which panel of the Cashiering screen is showing. */
type View = 'transactions' | 'shifts'

export default function Cashiering() {
  const propertyId = useActivePropertyId()
  const qc = useQueryClient()
  const [onDate, setOnDate] = useState('')
  const [method, setMethod] = useState('')
  const [source, setSource] = useState('')
  const [shift, setShift] = useState('')
  const [q, setQ] = useState('')
  const [room, setRoom] = useState('')
  const [selected, setSelected] = useState<string | null>(null)
  // The table shows either the day's payments or the folios that still owe.
  // Which panel is showing, taken from the URL so the sidebar can point at
  // one. Cash Drawer and Cashier Centre are two entries in the menu and were
  // one screen with a tab nobody could link to; /payments/drawer is that tab
  // with an address. Still switchable by clicking, and clicking rewrites the
  // URL, so a drawer somebody navigated to can be sent to the next person.
  const nav = useNavigate()
  const routedView: View = useLocation().pathname.endsWith('/drawer')
    ? 'shifts' : 'transactions'
  const [view, setViewState] = useState<View>(routedView)
  // Keep the two in step when the route changes under us -- a menu click
  // while already on this screen changes the path and nothing else.
  useEffect(() => { setViewState(routedView) }, [routedView])
  const setView = (v: View) => {
    setViewState(v)
    nav(v === 'shifts' ? '/payments/drawer' : '/payments', { replace: true })
  }
  const [collectFor, setCollectFor] = useState('')
  const [collecting, setCollecting] = useState(false)
  const [closing, setClosing] = useState<CashShift | null>(null)
  const [dropping, setDropping] = useState<CashShift | null>(null)
  const [opening, setOpening] = useState(false)
  const [toast, setToast] = useFlash()
  const [error, setError] = useState('')

  const filters = {
    on_date: onDate || undefined, method: method || undefined,
    source: source || undefined, shift_id: shift || undefined,
    room: room || undefined,
    q: q.trim() || undefined,
  }
  function exportTxns() {
    const rows = txns.data ?? []
    downloadCsv(
      datedName('takings', onDate || undefined),
      ['Date', 'Time', 'Type', 'Guest', 'Booking', 'Room', 'Source', 'Method',
       'Reference', 'Amount', 'Currency', 'Cashier', 'Status'],
      rows.map((t) => [
        day(t.received_at), at(t.received_at),
        t.kind === 'refund' ? 'Refund' : 'Payment',
        t.guest_name, t.reservation_number, t.room_code,
        t.source_label, t.method_label, t.reference, t.amount, t.currency,
        t.cashier, t.payment_status,
      ]),
    )
  }

  const sum = useQuery({
    queryKey: ['cash-summary', propertyId, onDate],
    queryFn: () => getCashSummary(propertyId, onDate || undefined),
    enabled: propertyId !== '',
  })
  const opts = useQuery({
    queryKey: ['cash-options', propertyId],
    queryFn: () => getCashOptions(propertyId), enabled: propertyId !== '',
  })
  const txns = useQuery({
    queryKey: ['cash-txns', propertyId, filters],
    queryFn: () => getCashTxns(propertyId, filters), enabled: propertyId !== '',
  })
  const shifts = useQuery({
    queryKey: ['cash-shifts', propertyId, onDate],
    queryFn: () => getCashShifts(propertyId, onDate || undefined),
    enabled: propertyId !== '',
  })

  const refresh = () => {
    for (const k of ['cash-summary', 'cash-txns', 'cash-shifts', 'cash-owing']) {
      qc.invalidateQueries({ queryKey: [k] })
    }
  }
  const fail = (e: unknown) => {
    const er = e as { response?: { data?: { detail?: string } } }
    setError(er.response?.data?.detail ?? 'That did not work. Please try again.')
    setToast('')
  }
  const done = (msg: string) => { setToast(msg); setError(''); refresh() }

  const openShift = useMutation({
    mutationFn: (body: Record<string, unknown>) => openCashShift(propertyId, body),
    onSuccess: (s) => { setOpening(false); done(`Shift open with ${exact.format(Number(s.opening_float))} float.`) },
    onError: fail,
  })
  const closeShift = useMutation({
    mutationFn: (v: { id: string; body: Record<string, unknown> }) =>
      closeCashShift(v.id, propertyId, v.body),
    onSuccess: (s) => {
      setClosing(null)
      const v = Number(s.variance)
      done(v === 0
        ? `Shift closed. The drawer balanced at ${exact.format(Number(s.declared_cash))}.`
        : `Shift closed with a ${exact.format(Math.abs(v))} ${v < 0 ? 'shortfall' : 'excess'}.`)
    },
    onError: fail,
  })
  const collect = useMutation({
    mutationFn: (body: Record<string, unknown>) => collectCashPayment(propertyId, body),
    onSuccess: (r) => {
      setCollecting(false)
      done(`${r.method_label} payment of ${exact.format(Number(r.amount))} taken. `
        + `Folio balance is now ${exact.format(Number(r.balance_after))}.`)
    },
    onError: fail,
  })

  const methodLabel = useMethodLabel(propertyId)

  if (propertyId === '') {
    return <p className="text-sm text-slate-500">Pick a property first.</p>
  }
  if (sum.isError) {
    const er = sum.error as { response?: { data?: { detail?: string } } }
    return (
      <p className="flex items-start gap-2 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
        {er?.response?.data?.detail ?? 'The cashiering centre could not be loaded.'}
      </p>
    )
  }

  const s = sum.data
  const o = opts.data
  const mine = shifts.data?.my_open_shift ?? null

  /** Whether anything narrows the list. Drives the clear button's
   *  visibility, not its presence -- see the button for why. */
  const filtered = Boolean(source || shift || method || onDate || q || room)

  /** The business date this table is showing.
   *  `onDate` is empty until somebody picks one, and the server then falls
   *  back to the property's business date -- so the screen always knows the
   *  day it is showing, even when the filter looks blank, and should say so. */
  const showing = onDate || s?.business_date || ''
  const refunds = (txns.data ?? []).filter((t) => t.kind === 'refund').length

  return (
    <div className="space-y-4">
      {/* ------------------------------------------------------- header --- */}
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <h1 className="flex items-center gap-2 text-display text-ink">
          <CreditCard size={26} className="text-brand" /> Cashiering Centre
        </h1>
        <div className="flex flex-wrap items-center gap-2">
          {/* The day's takings as a file -- the shift report a cashier hands
              over with the drawer -- exporting exactly what the filters select. */}
          {view === 'transactions' && o?.can_export && (txns.data?.length ?? 0) > 0 && (
            <button onClick={exportTxns}
              className="flex items-center gap-1.5 whitespace-nowrap rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-600 hover:bg-slate-50">
              <Download size={15} /> Export CSV
            </button>
          )}
          {mine === null ? (
            <button disabled={!shifts.data?.can_open} onClick={() => setOpening(true)}
              title={shifts.data?.can_open ? undefined
                : 'You do not have permission to open a cashier shift.'}
              className="flex items-center gap-1.5 whitespace-nowrap rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-600 hover:bg-slate-50 disabled:opacity-40">
              <Clock size={15} /> Open Cashier Shift
            </button>
          ) : (
            <>
              {/* Banking part of the tray is a routine thing done several
                  times on a busy day, and it is not closing up -- so it sits
                  beside Close Shift rather than inside it. */}
              <button onClick={() => setDropping(mine)}
                title="Move cash from the drawer to the safe without closing the shift"
                className="flex items-center gap-1.5 whitespace-nowrap rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-600 hover:bg-slate-50">
                <Banknote size={15} /> Cash Drop
              </button>
              {/* Amber rather than grey: a drawer is open and still to be counted. */}
              <button onClick={() => setClosing(mine)}
                className="flex items-center gap-1.5 whitespace-nowrap rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-sm font-medium text-caution hover:bg-amber-100">
                <Square size={14} /> Close Shift
              </button>
            </>
          )}
          <button disabled={!o?.can_collect} onClick={() => setCollecting(true)}
            title={o?.can_collect ? undefined
              : 'You do not have permission to take payments.'}
            className="flex items-center gap-1.5 whitespace-nowrap rounded-lg bg-brand px-3 py-2 text-sm font-medium text-white hover:bg-brand-dark disabled:opacity-40">
            <Plus size={15} /> Collect Payment
          </button>
        </div>
      </div>

      {mine && (
        <p className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-xl bg-emerald-50 px-4 py-2.5 text-sm text-emerald-800">
          <Clock size={14} className="shrink-0" />
          Your drawer has been open since <strong>{stamp(mine.opened_at)}</strong> with
          a <strong>{exact.format(Number(mine.opening_float))}</strong> float.
          <span className="text-positive">
            {mine.payments_count === 0 ? 'No cash taken yet.'
              : `${exact.format(Number(mine.cash_taken))} cash taken in ${mine.payments_count} payment${mine.payments_count === 1 ? '' : 's'}.`}
          </span>
        </p>
      )}

      {toast && (
        <p className="flex items-start gap-2 rounded-xl bg-emerald-50 px-4 py-3 text-sm text-emerald-700">
          <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />{toast}
        </p>
      )}
      {error && (
        <p className="flex items-start gap-2 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />{error}
        </p>
      )}

      {/* --------------------------------------------- table + details --- */}
      {/* Three tables, one at a time.
          Transactions and Cashier Shifts used to be stacked on the page, so
          reading the shift a drawer was handed over on meant scrolling past
          every payment of the day -- and the two tables have nothing in
          common but the date: one is a list of money taken, the other a list
          of people who took it. Outstanding was already a swap rather than a
          stack, which is the shape all three should have had. */}
      <div className="flex gap-1 border-b border-slate-200">
        {([
          // "Transactions" was the label here, and it was read as every
          // movement on a folio -- which is what the word means everywhere
          // else in this system. This tab is narrower than that: money that
          // crossed the counter. A Day use charge is a transaction on the
          // folio and will never appear here, and the old label made that
          // look like a fault three times over before it was the name that
          // got fixed rather than the question re-answered.
          ['transactions', 'Payments & Refunds', txns.data?.length],
          ['shifts', 'Cashier Shifts', shifts.data?.shifts?.length],
        ] as const).map(([key, label, count]) => (
          <button key={key} onClick={() => setView(key)}
            title={key === 'transactions'
              ? 'Money taken and handed back at the desk. Charges posted to a '
                + 'folio are not here — see the folio itself.'
                + (refunds > 0
                   ? ` Including ${refunds} refund${refunds === 1 ? '' : 's'}.`
                   : '')
              : undefined}
            className={`whitespace-nowrap px-4 py-2.5 ${CONTROL_TYPE} ${
              view === key ? 'border-b-2 border-brand text-brand'
                : 'text-slate-500 hover:text-slate-700'}`}>
            {label}
            {count !== undefined && count !== null && (
              <span className={`ml-1.5 text-xs ${
                view === key ? 'text-brand/70' : 'text-slate-400'}`}>{count}</span>
            )}
          </button>
        ))}
        {/* The figures live on the tab row, not with the filters.
            They were on the filter line, and there is no width at which two
            captioned figures plus four filters plus a search box fit -- with
            the sidebar expanded this row gets 976px and needs 1100. Putting
            them here costs no height at all, because the tab strip was
            already a row with empty space to its right, and it reads better:
            the tabs choose which table you are looking at, the figures
            summarise it. */}
        <span className="ml-auto flex items-center gap-2 pb-1.5">
        <Stat icon={<Wallet size={16} className="text-amber-700" />}
          tint="bg-amber-100" label="Collected"
          value={s ? money.format(Number(s.collected)) : '—'}
          // The method split lives here now. It is detail you go looking for,
          // not something to read every time -- and the table below breaks
          // payments down by method anyway.
          // Built from the methods the shift actually took, not from three
          // named here: a property taking bank transfers had them collected,
          // counted in the total, and missing from this split entirely.
          title={s ? (s.methods ?? []).map((sl) => (
            `${methodLabel(sl.method)} ${money.format(Number(sl.amount ?? 0))}`
            + ` (${sl.count})`
          )).join('  ·  ') || undefined : undefined}
          trendTone={s?.change_percent === null ? 'text-slate-400'
            : (s?.change_percent ?? 0) >= 0 ? 'text-emerald-600' : 'text-red-600'}
          trend={s && (s.change_percent === null ? 'first day'
            : `${s.change_percent >= 0 ? '↑' : '↓'} ${Math.abs(s.change_percent)}%`)} />
        <Stat icon={<Clock size={16} className="text-red-700" />}
          tint="bg-red-100" label="Outstanding"
          value={s ? money.format(Number(s.outstanding)) : '—'}
          // Opens High Balance Guest rather than a tab of its own.
          //
          // The list used to live here, and did not belong: this screen is a
          // shift -- one business day, one drawer -- while outstanding debt is
          // scoped to neither, so a cashier counting a till was shown every
          // unpaid folio the property has ever had. On this data all sixty-five
          // of them were cancelled bookings: nobody a cashier could collect
          // from, and a Room column empty on every row because a cancelled
          // booking never got one.
          //
          // Report 39 already answers it properly -- departed guests first,
          // with stay status and a balance threshold -- so the number stays
          // visible here and the click goes where the answer is.
          onClick={() => nav('/reports/high-balance-guest')}
          title={s ? `${s.outstanding_folios} folio${
            s.outstanding_folios === 1 ? '' : 's'} still owing — open the High `
            + 'Balance Guest report'
            : undefined} />
        </span>
      </div>

      {/* ------------------------------------------------------ filters --- */}
      {/* Under the tabs, as on every other list screen: the tab picks the
          table, these narrow it. One row, no captions -- "Source" over a
          control reading "All Sources" is the same word twice. The date field
          always shows the business date the tables are for, which is why the
          table no longer needs a caption row saying so. */}
      <div className="flex flex-wrap items-center gap-2">
        <Select blankIsChoice value={source} onChange={(e) => setSource(e.target.value)}
          title="Booking source" aria-label="Booking source" className={select}>
          <option value="">All Sources</option>
          {(o?.sources ?? []).map((x) => (
            <option key={x.value} value={x.value}>{x.label}</option>
          ))}
        </Select>
        <Select blankIsChoice value={shift} onChange={(e) => setShift(e.target.value)}
          title="Cashier shift" aria-label="Cashier shift" className={select}>
          <option value="">All Shifts</option>
          {(shifts.data?.shifts ?? []).map((x) => (
            <option key={x.id} value={x.id}>
              {x.cashier ?? 'Cashier'} · {at(x.opened_at)}
              {x.status === 'open' ? ' (open)' : ''}
            </option>
          ))}
        </Select>
        <Select blankIsChoice value={method} onChange={(e) => setMethod(e.target.value)}
          title="Payment method" aria-label="Payment method" className={select}>
          <option value="">All Methods</option>
          {(o?.methods ?? []).map((x) => (
            <option key={x.value} value={x.value}>{x.label}</option>
          ))}
        </Select>
        {/* Room, exact. The search box beside it also reaches room_code, but
            it reaches guest, booking and reference too -- so "201" there is a
            guess, and this is an answer. Offered only for rooms money has
            actually gone through. */}
        <Select blankIsChoice value={room} onChange={(e) => setRoom(e.target.value)}
          title="Room" aria-label="Room" className={select}>
          <option value="">All Rooms</option>
          {(o?.rooms ?? []).map((x) => (
            // The bare number, as the ROOM column prints it. Labelling these
            // "Room 201" broke the control's type-ahead -- it matches from the
            // start of the text, so finding 201 meant typing "room 2".
            <option key={x.value} value={x.value}>{x.label}</option>
          ))}
        </Select>
        {/* Native `<input type="date">` prints its text in the browser's
            locale, so this read 09/12/2026 beside a table headed
            "12 Sep 2026" -- and 09/12 is 9 December to half the world.
            DateField keeps the picker and draws our own spelling over it. */}
        <DateField value={showing}
          onChange={setOnDate} label="Business date" />
        {/* Search at the right, as on Reservations. */}
        <div className="flex min-w-[240px] flex-1 items-center justify-end gap-2">
          {/* Always in the layout, visible only when there is something to
              clear.
              Rendering it conditionally was a mistake: the row fitted exactly
              until somebody used a filter, and then this button appeared, found
              no room, and wrapped -- so the one action that proves the bar is
              working was also the thing that broke it. Held with `invisible`
              rather than removed, the row is the same width whether or not a
              filter is set and never reflows. */}
          <button onClick={() => { setSource(''); setShift(''); setMethod(''); setOnDate(''); setQ(''); setRoom('') }}
            title="Clear filters" aria-label="Clear filters"
            aria-hidden={!filtered}
            tabIndex={filtered ? 0 : -1}
            className={`shrink-0 rounded-lg border border-slate-200 p-2 text-slate-400 hover:bg-slate-50 hover:text-brand ${
              filtered ? '' : 'invisible pointer-events-none'}`}>
            <X size={15} />
          </button>
          <div className="relative min-w-0 max-w-sm flex-1">
            <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <input value={q} onChange={(e) => setQ(e.target.value)}
              placeholder="Guest, booking, room or reference"
              aria-label="Search payments"
              className="w-full rounded-lg border border-slate-200 bg-slate-50 py-2 pl-9 pr-3 text-sm outline-none focus:border-brand" />
          </div>
        </div>
      </div>

      {/* Which shift is being shown, and how to stop showing it. Selecting a
          shift makes the list ignore the date — a drawer can span two
          business dates — so without this the reader sees rows from a day
          the date box does not name and has no way to know why. */}
      {view === 'transactions' && shift && (() => {
        const s = (shifts.data?.shifts ?? []).find((x) => x.id === shift)
        return (
          <div className="flex flex-wrap items-center gap-2 rounded-xl border
            border-brand/30 bg-brand-light/30 px-4 py-2.5 text-sm text-slate-700">
            <Wallet size={15} className="text-brand" />
            <span>
              <span className="font-medium">{s?.cashier ?? 'Cashier'}</span>
              {"’s shift — opened "}{stamp(s?.opened_at)}
              {s?.closed_at ? `, closed ${stamp(s.closed_at)}`
                : ', still open'}
            </span>
            <span className="text-slate-400">
              · {txns.data?.length ?? 0} transaction
              {(txns.data?.length ?? 0) === 1 ? '' : 's'} in this shift,
              whichever business date they fell on
            </span>
            <button onClick={() => setShift('')}
              className={`ml-auto flex items-center gap-1 rounded-lg border
                border-slate-200 bg-white px-2.5 py-1 ${CONTROL_TYPE}
                text-slate-600 hover:border-brand`}>
              <X size={13} /> Show the whole day
            </button>
          </div>
        )
      })()}

      <div className="flex gap-4">
        <div className="min-w-0 flex-1 overflow-hidden rounded-2xl border border-slate-100 bg-white">
          {view === 'shifts' ? (
            (shifts.data?.shifts ?? []).length === 0 ? (
              <p className="px-4 py-12 text-center text-sm text-slate-400">
                No cashier shift has been opened on this date.
              </p>
            ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="px-4 py-2.5 font-semibold">Cashier</th>
                  <th className="px-4 py-2.5 font-semibold">Opened</th>
                  <th className="px-4 py-2.5 font-semibold">Closed</th>
                  <th className="px-4 py-2.5 text-right font-semibold">Float</th>
                  <th className="px-4 py-2.5 text-right font-semibold"
                    title="Cash payments into this drawer. Card and UPI are not counted here — they never reach the drawer, so they cannot be in the count.">
                    Cash Taken
                  </th>
                  {/* Placed between what went in and what should be there, so
                      the row reads left to right as the sum it is:
                      float + taken − refunded = expected. */}
                  <th className="px-4 py-2.5 text-right font-semibold"
                    title="Cash handed back out of this drawer. Card and UPI refunds go back to the card, not the drawer, so they are not counted here.">
                    Refunded
                  </th>
                  {/* Cash lifted to the safe mid-shift. Sits between what
                      went out to guests and what should be left, because it
                      is the third term in the same sum. */}
                  <th className="px-4 py-2.5 text-right font-semibold"
                    title="Cash banked out of the drawer during the shift. Not a refund — the money is still the property's, it is just no longer in the tray.">
                    Dropped
                  </th>
                  {/* The shift's business, as opposed to its drawer. Every
                      column to the left of this one is cash, because a drawer
                      holds nothing else -- which left the row unable to say
                      how much a cashier actually took. A shift with 90,000 on
                      cards and 4,000 in cash read as a 4,000 shift. */}
                  <th className="px-4 py-2.5 text-right font-semibold"
                    title="Everything taken during the shift, by every method. Not a drawer figure — card and UPI never reach the tray.">
                    Takings
                  </th>
                  {/* The three columns nobody can read without being told the
                      arithmetic, so the headers carry it. */}
                  <th className="px-4 py-2.5 text-right font-semibold"
                    title="What the ledger says should be in the drawer: float + cash taken, less cash refunded">
                    Expected
                  </th>
                  <th className="px-4 py-2.5 text-right font-semibold"
                    title="What the cashier counted when closing the drawer">
                    Counted
                  </th>
                  <th className="px-4 py-2.5 text-right font-semibold"
                    title="Counted minus expected. Negative is short — money missing. Positive is over — cash the ledger does not know about.">
                    Variance
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-50">
                {(shifts.data?.shifts ?? []).map((x) => {
                  const v = x.variance === null ? null : Number(x.variance)
                  return (
                    // The row is the way into the shift's own takings. Every
                    // figure across it is a total, and a total with nothing
                    // behind it is a number you can only believe or doubt --
                    // "₹5,298 in 1 payment" was not answerable without
                    // knowing the shift filter on the other tab existed.
                    <tr key={x.id} tabIndex={0} role="button"
                      title={`Show the payments and refunds in ${
                        x.cashier ?? 'this cashier'}'s shift`}
                      onClick={() => { setShift(x.id); setView('transactions') }}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                          e.preventDefault()
                          setShift(x.id); setView('transactions')
                        }
                      }}
                      className="cursor-pointer hover:bg-slate-50
                        focus:bg-slate-50 focus:outline-none">
                      <td className="px-4 py-2.5">
                        <span className="font-medium text-slate-700">{x.cashier ?? '—'}</span>
                        {x.status === 'open' && (
                          <span className="ml-2 rounded-full bg-emerald-100 px-2 py-0.5 text-xs font-semibold text-emerald-700">
                            Open
                          </span>
                        )}
                      </td>
                      {/* Both columns carry their date. A drawer opened 18:29
                          and closed 18:49 reads fine either way — but a night
                          shift opens one evening and closes the next, and the
                          bare clocks made that look like a close before the
                          open. Stacked, so the column keeps its width. */}
                      <td className="whitespace-nowrap px-4 py-2.5">
                        <span className="block text-slate-700">{at(x.opened_at)}</span>
                        <span className="block text-xs text-slate-400">
                          {day(x.opened_at)}
                        </span>
                      </td>
                      <td className="whitespace-nowrap px-4 py-2.5">
                        {x.closed_at ? (
                          <>
                            <span className="block text-slate-700">{at(x.closed_at)}</span>
                            <span className="block text-xs text-slate-400">
                              {day(x.closed_at)}
                            </span>
                          </>
                        ) : (
                          <span className="text-slate-400">Still open</span>
                        )}
                      </td>
                      <td className="px-4 py-2.5 text-right text-slate-600">{exact.format(Number(x.opening_float))}</td>
                      {/* Cash only. A shift that took 10,000 cash and 10,000
                          online expects 10,000 in the drawer, not 20,000 —
                          card and UPI never reach it. */}
                      <td className="px-4 py-2.5 text-right text-slate-600">
                        {exact.format(Number(x.cash_taken))}
                        {x.payments_count > 0 && (
                          <span className="block text-xs text-slate-400">
                            {x.payments_count} payment{x.payments_count === 1 ? '' : 's'}
                          </span>
                        )}
                      </td>
                      {/* Money that left the drawer. Zero is the normal case,
                          so it is greyed rather than shouted — the eye should
                          stop only on the shifts where cash actually went
                          back out. */}
                      <td className={`px-4 py-2.5 text-right ${
                        Number(x.cash_refunded) > 0 ? 'text-caution' : 'text-slate-300'}`}>
                        {Number(x.cash_refunded) > 0
                          ? `−${exact.format(Number(x.cash_refunded))}`
                          : exact.format(0)}
                        {x.refunds_count > 0 && (
                          <span className="block text-xs text-caution/80">
                            {x.refunds_count} refund{x.refunds_count === 1 ? '' : 's'}
                          </span>
                        )}
                      </td>
                      <td className={`px-4 py-2.5 text-right ${
                        Number(x.cash_dropped) > 0 ? 'text-slate-600' : 'text-slate-300'}`}>
                        {exact.format(Number(x.cash_dropped))}
                        {x.drops_count > 0 && (
                          <span className="block text-xs text-slate-400">
                            {x.drops_count} drop{x.drops_count === 1 ? '' : 's'}
                          </span>
                        )}
                      </td>
                      {/* Bold, because on most shifts this is the figure
                          being looked for, and the methods under it so the
                          total can be taken apart without another screen. */}
                      <td className="px-4 py-2.5 text-right">
                        <span className="font-semibold text-slate-700">
                          {exact.format(Number(x.total_taken))}
                        </span>
                        {x.by_method.length > 0 && (
                          <span className="block text-xs text-slate-400">
                            {x.by_method.map((m) =>
                              `${m.label} ${exact.format(Number(m.amount))}`).join(' · ')}
                          </span>
                        )}
                        {Number(x.total_refunded) > 0 && (
                          <span className="block text-xs text-caution/80">
                            less {exact.format(Number(x.total_refunded))} given back
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-2.5 text-right text-slate-600">
                        {x.expected_cash === null ? '—' : exact.format(Number(x.expected_cash))}
                      </td>
                      <td className="px-4 py-2.5 text-right text-slate-600">
                        {x.declared_cash === null ? '—' : exact.format(Number(x.declared_cash))}
                      </td>
                      {/* Short and over are not the same finding, and this
                          column used to paint both red.
                          Short means cash the ledger says was taken is not in
                          the drawer — money missing, and red is right. Over
                          means the drawer holds more than the ledger knows
                          about: a payment taken and not rung up, or a
                          miscount. It needs looking into, but nothing is
                          lost, so it is amber. Painting them alike taught a
                          desk to read red as "the count is done" rather than
                          as "something is wrong". */}
                      <td className={`px-4 py-2.5 text-right font-semibold ${
                        v === null ? 'text-slate-400'
                          : v === 0 ? 'text-positive'
                          : v < 0 ? 'text-red-600' : 'text-caution'}`}
                        title={v === null ? undefined
                          : `Counted ${exact.format(Number(x.declared_cash))} `
                            + `against ${exact.format(Number(x.expected_cash))} expected `
                            + `(${exact.format(Number(x.opening_float))} float `
                            + `+ ${exact.format(Number(x.cash_taken))} cash taken`
                            + (Number(x.cash_refunded) > 0
                              ? ` − ${exact.format(Number(x.cash_refunded))} refunded` : '')
                            + '). Card and UPI are excluded — they never reach the drawer. '
                            + (v === 0 ? 'The drawer agrees with the ledger.'
                              : v < 0 ? 'The drawer is short.'
                              : 'The drawer holds more than the ledger expected.')}>
                        {v === null ? '—' : v === 0 ? 'Balanced'
                          : `${v > 0 ? '+' : '−'}${exact.format(Math.abs(v))}`}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
            )
          ) : (
          <>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="px-4 py-3 font-semibold">Date &amp; Time</th>
                  <th className="px-4 py-3 font-semibold">Guest / Booking</th>
                  <th className="px-4 py-3 font-semibold">Room</th>
                  <th className="px-4 py-3 font-semibold">Source</th>
                  <th className="px-4 py-3 font-semibold">Method</th>
                  <th className="px-4 py-3 font-semibold">Reference</th>
                  <th className="px-4 py-3 text-right font-semibold">Amount</th>
                  <th className="px-4 py-3 font-semibold">Cashier</th>
                  <th className="px-4 py-3 font-semibold">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-50">
                {txns.isLoading && (
                  <tr><td colSpan={9} className="px-4 py-10 text-center text-slate-400">
                    <Loader2 className="mx-auto h-4 w-4 animate-spin" />
                  </td></tr>
                )}
                {txns.data?.length === 0 && (
                  <tr><td colSpan={9} className="px-4 py-10 text-center text-sm text-slate-400">
                    No payments match these filters.
                    <span className="mt-1 block text-xs">
                      Charges posted to a folio are not listed here — open the
                      folio to see those.
                    </span>
                  </td></tr>
                )}
                {(txns.data ?? []).map((t: CashTxn) => (
                  <tr key={t.payment_id}
                    onClick={() => setSelected(t.payment_id)}
                    className={`cursor-pointer hover:bg-slate-50 ${
                      selected === t.payment_id ? 'bg-brand-light/50' : ''}`}>
                    {/* Date and time on every row, stacked so the column
                        stays narrow. The caption above names the day too, but
                        a row that carries its own date is one you can read in
                        isolation -- copied into a message, or printed. */}
                    <td className="whitespace-nowrap px-4 py-3">
                      <span className="block text-slate-700">{at(t.received_at)}</span>
                      <span className="block text-xs text-slate-400">
                        {day(t.received_at)}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      <span className="block font-medium text-slate-800">
                        {t.guest_name ?? 'Unlinked payment'}
                      </span>
                      <span className="block text-xs text-slate-400">
                        {t.reservation_number ?? 'No booking'}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-slate-600">{t.room_code ?? '—'}</td>
                    <td className="px-4 py-3 text-slate-600">{t.source_label}</td>
                    <td className="px-4 py-3">
                      <span className={`rounded-md px-2 py-0.5 text-xs font-semibold ${
                        METHOD_TINT[t.method] ?? 'bg-slate-75 text-slate-600'}`}>
                        {t.method_label}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-slate-500">{t.reference ?? '—'}</td>
                    {/* Money out is amber and already negative from the
                        server, so the column adds up to what actually moved
                        through the desk rather than to takings alone. */}
                    <td className={`px-4 py-3 text-right font-semibold ${
                      t.kind === 'refund' ? 'text-caution' : 'text-slate-800'}`}>
                      {exact.format(Number(t.amount))}
                    </td>
                    <td className="px-4 py-3 text-slate-600">{t.cashier ?? '—'}</td>
                    <td className="px-4 py-3">
                      <span className={`rounded-full px-2 py-0.5 text-xs font-semibold ${
                        t.kind === 'refund' ? 'bg-amber-100 text-amber-800'
                          : t.payment_status === 'succeeded'
                          ? 'bg-emerald-100 text-emerald-700'
                          : 'bg-amber-100 text-amber-700'}`}>
                        {t.kind === 'refund' ? 'Refunded'
                          : t.payment_status === 'succeeded' ? 'Paid'
                          : t.payment_status}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
          </table>
          </div>
          </>
          )}
        </div>

        {selected && view === 'transactions' && (
          <Detail paymentId={selected} propertyId={propertyId}
            onClose={() => setSelected(null)} />
        )}
      </div>

      {collecting && (
        <CollectDialog propertyId={propertyId} options={o}
          hasOpenShift={mine !== null} initialFolioId={collectFor}
          onClose={() => { setCollecting(false); setCollectFor('') }}
          onSubmit={(b) => collect.mutate(b)} pending={collect.isPending} />
      )}
      {opening && (
        <OpenShiftDialog onClose={() => setOpening(false)}
          onSubmit={(b) => openShift.mutate(b)} pending={openShift.isPending} />
      )}
      {dropping && (
        <CashDropDialog shift={dropping} onClose={() => setDropping(null)}
          onDone={(msg) => {
            setDropping(null); setToast(msg)
            void shifts.refetch()
          }} />
      )}
      {closing && (
        <CloseShiftDialog shift={closing} onClose={() => setClosing(null)}
          onSubmit={(b) => closeShift.mutate({ id: closing.id, body: b })}
          pending={closeShift.isPending} />
      )}
    </div>
  )
}

/* ------------------------------------------------------ folios still owing --- */

/* ---------------------------------------------------------- detail panel --- */
function Detail({ paymentId, propertyId, onClose }: {
  paymentId: string; propertyId: string; onClose: () => void
}) {
  const { data: d, isLoading } = useQuery({
    queryKey: ['cash-payment', paymentId, propertyId],
    queryFn: () => getCashPayment(paymentId, propertyId),
  })

  if (isLoading || !d) {
    return (
      <aside className="fixed inset-y-0 right-0 z-30 w-[min(24rem,92vw)] space-y-4 overflow-y-auto border-l border-slate-200 bg-white p-5 shadow-2xl lg:inset-auto lg:sticky lg:top-4 lg:z-auto lg:max-h-[calc(100vh-2rem)] lg:w-96 lg:shrink-0 lg:self-start lg:rounded-2xl lg:border lg:border-slate-100 lg:shadow-none">
        <Loader2 className="mx-auto h-4 w-4 animate-spin text-slate-400" />
      </aside>
    )
  }

  return (
    <aside className="fixed inset-y-0 right-0 z-30 w-[min(24rem,92vw)] space-y-4 overflow-y-auto border-l border-slate-200 bg-white p-5 shadow-2xl lg:inset-auto lg:sticky lg:top-4 lg:z-auto lg:max-h-[calc(100vh-2rem)] lg:w-96 lg:shrink-0 lg:self-start lg:rounded-2xl lg:border lg:border-slate-100 lg:shadow-none">
      <div className="flex items-start justify-between gap-2">
        <span className="flex items-center gap-2">
          <h2 className="text-lg font-semibold text-ink">Payment Details</h2>
          <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-xs font-semibold text-emerald-700">
            {d.payment_status === 'succeeded' ? 'Paid' : d.payment_status}
          </span>
        </span>
        <button onClick={onClose} className="text-slate-400 hover:text-slate-600">
          <X size={18} />
        </button>
      </div>

      <dl className="grid grid-cols-2 gap-x-4 gap-y-3 text-sm">
        <Fact label="Guest Name" value={d.guest_name ?? '—'} />
        <Fact label="Booking" value={d.reservation_number ?? '—'} />
        <Fact label="Room No." value={d.room_code ?? '—'} />
        <Fact label="Date & Time" value={stamp(d.received_at)} />
        <Fact label="Source" value={d.source_label} />
        <Fact label="Cashier" value={d.cashier ?? '—'} />
        <Fact label="Payment Method" value={d.method_label} />
        <Fact label="Reference No." value={d.reference ?? '—'} />
        <Fact label="Amount" value={exact.format(Number(d.amount))} strong />
      </dl>
      {d.notes && (
        <p className="rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-600">{d.notes}</p>
      )}

      <div>
        <h3 className="mb-2 font-semibold text-ink">Allocation</h3>
        <table className="w-full text-sm">
          <thead className="text-left text-xs text-slate-500">
            <tr>
              <th className="pb-1 font-semibold">What it settled</th>
              <th className="pb-1 text-right font-semibold">Amount</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-50">
            {d.allocations.map((a, i) => (
              <tr key={i}>
                <td className="py-1.5 text-slate-600">{a.category}</td>
                <td className="py-1.5 text-right text-slate-700">
                  {exact.format(Number(a.amount))}
                </td>
              </tr>
            ))}
            <tr className="bg-amber-50">
              <td className="py-1.5 pl-1 font-semibold text-slate-700">Total Allocated</td>
              <td className="py-1.5 pr-1 text-right font-semibold text-slate-800">
                {exact.format(Number(d.allocated_total))}
              </td>
            </tr>
          </tbody>
        </table>
        <p className="mt-2 flex items-start gap-1.5 text-xs text-slate-400">
          <Info size={12} className="mt-0.5 shrink-0" />
          Apportioned across what the folio owed when this payment landed. The
          payment credits the folio as a whole; it is not tied to single charges.
        </p>
      </div>

      <Link to={`/payments/${d.payment_id}/reverse`}
        className="flex w-full items-center justify-center gap-2 rounded-lg border border-slate-200 px-4 py-2 text-sm font-semibold text-slate-600 hover:bg-slate-50">
        <Undo2 size={15} /> Refund or reverse
      </Link>

      {d.folio_id && (
        <Link to={`/payments/folios/${d.folio_id}/adjust`}
          className="flex w-full items-center justify-center gap-2 rounded-lg border border-brand px-4 py-2 text-sm font-semibold text-brand hover:bg-brand-light">
          <FileText size={15} /> Adjust this folio
        </Link>
      )}

      {d.folio_id && (
        <Link to={`/payments/folios/${d.folio_id}/adjust`}
          className="flex w-full items-center justify-center gap-2 rounded-lg border border-brand px-4 py-2 text-sm font-semibold text-brand hover:bg-brand-light">
          <FileText size={15} /> Adjust this bill
        </Link>
      )}

      <div>
        <h3 className="mb-2 font-semibold text-ink">Receipt</h3>
        <button onClick={() => printReceipt(d)}
          className="flex w-full items-center justify-center gap-2 rounded-lg border border-slate-200 px-4 py-2 text-sm font-medium text-slate-600 hover:bg-slate-50">
          <Printer size={15} /> View / Print Receipt
        </button>
        <p className="mt-2 flex items-start gap-1.5 text-xs text-slate-400">
          <Send size={12} className="mt-0.5 shrink-0" />
          There is no mail or SMS transport in this system yet, so a receipt
          cannot be sent to the guest from here. Print it or hand it over.
        </p>
      </div>
    </aside>
  )
}

function Fact({ label, value, strong }: {
  label: string; value: string; strong?: boolean
}) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-slate-400">{label}</dt>
      <dd className={`truncate ${strong ? 'font-semibold text-slate-800' : 'text-slate-700'}`}
        title={value}>
        {value}
      </dd>
    </div>
  )
}

/** Render the receipt into a print window. Everything on it is data we hold. */
function printReceipt(d: {
  property_name: string; payment_id: string; received_at: string
  guest_name: string | null; reservation_number: string | null
  room_code: string | null; method_label: string; reference: string | null
  amount: string; cashier: string | null
  allocations: { category: string; amount: string }[]
}) {
  const esc = (v: unknown) => String(v ?? '—')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  const rows = d.allocations.map((a) =>
    `<tr><td>${esc(a.category)}</td><td class="r">${exact.format(Number(a.amount))}</td></tr>`).join('')
  const w = window.open('', '_blank', 'width=420,height=640')
  if (!w) return
  w.document.write(`<!doctype html><meta charset="utf-8"><title>Receipt ${esc(d.payment_id).slice(0, 8)}</title>
<style>
 body{font:13px/1.5 system-ui,sans-serif;color:#1e293b;padding:24px;max-width:360px}
 h1{font-size:16px;margin:0 0 2px} .sub{color:#64748b;font-size:12px;margin:0 0 16px}
 table{width:100%;border-collapse:collapse;margin:12px 0}
 td{padding:4px 0;border-bottom:1px solid #d5dde8} .r{text-align:right}
 .k{color:#64748b} .tot td{font-weight:700;border-top:2px solid #1e293b;border-bottom:none}
 .foot{color:#46566d;font-size:11px;margin-top:20px}
</style>
<h1>${esc(d.property_name)}</h1>
<p class="sub">Payment receipt · ${esc(d.payment_id).slice(0, 8).toUpperCase()}</p>
<table>
 <tr><td class="k">Date</td><td class="r">${esc(stamp(d.received_at))}</td></tr>
 <tr><td class="k">Guest</td><td class="r">${esc(d.guest_name)}</td></tr>
 <tr><td class="k">Booking</td><td class="r">${esc(d.reservation_number)}</td></tr>
 <tr><td class="k">Room</td><td class="r">${esc(d.room_code)}</td></tr>
 <tr><td class="k">Method</td><td class="r">${esc(d.method_label)}</td></tr>
 <tr><td class="k">Reference</td><td class="r">${esc(d.reference)}</td></tr>
 <tr><td class="k">Received by</td><td class="r">${esc(d.cashier)}</td></tr>
</table>
<table>${rows}
 <tr class="tot"><td>Total Paid</td><td class="r">${exact.format(Number(d.amount))}</td></tr>
</table>
<p class="foot">This receipt was produced by the property management system and
records a payment held in its ledger.</p>`)
  w.document.close()
  w.focus()
  w.print()
}

/* ---------------------------------------------------------------- dialogs --- */
function Shell({ title, onClose, children, footer }: {
  title: string; onClose: () => void
  children: React.ReactNode; footer: React.ReactNode
}) {
  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-slate-900/30 p-4">
      <div className="w-full max-w-md rounded-2xl bg-white p-5 shadow-xl">
        <div className="flex items-start justify-between">
          <h2 className="text-lg font-semibold text-ink">{title}</h2>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600">
            <X size={18} />
          </button>
        </div>
        <div className="mt-4 space-y-3">{children}</div>
        <div className="mt-5 flex justify-end gap-2">{footer}</div>
      </div>
    </div>
  )
}

const ghost = 'rounded-lg border border-slate-200 px-4 py-2 text-sm text-slate-600 hover:bg-slate-50'
const primary = 'flex items-center gap-1.5 rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white hover:bg-brand-dark disabled:opacity-40'

function CollectDialog({ propertyId, options, hasOpenShift, initialFolioId,
                        onClose, onSubmit, pending }: {
  propertyId: string; options?: CashOptions; hasOpenShift: boolean
  initialFolioId?: string
  onClose: () => void; pending: boolean
  onSubmit: (body: Record<string, unknown>) => void
}) {
  const [search, setSearch] = useState('')
  const [folioId, setFolioId] = useState(initialFolioId ?? '')
  const [amount, setAmount] = useState('')
  const [method, setMethod] = useState('cash')
  const [reference, setReference] = useState('')
  const [notes, setNotes] = useState('')

  const { data: folios } = useQuery({
    queryKey: ['open-folios', propertyId, search],
    queryFn: () => getOpenFolios(propertyId, search.trim() || undefined),
  })
  const picked = (folios ?? []).find((f: OpenFolio) => f.folio_id === folioId)
  const spec = options?.methods.find((m) => m.value === method)
  const needsRef = spec?.needs_reference ?? false

  // Offer the whole balance by default — it is what a cashier reaches for.
  useEffect(() => {
    if (picked && amount === '') setAmount(String(Number(picked.balance)))
  }, [picked, amount])

  const missing: string[] = []
  if (folioId === '') missing.push('a folio')
  if (Number(amount) <= 0) missing.push('an amount')
  if (needsRef && reference.trim() === '') missing.push('a reference')
  if (method === 'cash' && !hasOpenShift) missing.push('an open cashier shift')

  return (
    <Shell title="Collect Payment" onClose={onClose} footer={<>
      <button onClick={onClose} className={ghost}>Cancel</button>
      <button disabled={missing.length > 0 || pending} className={primary}
        onClick={() => onSubmit({
          folio_id: folioId, amount: Number(amount), method,
          reference: reference.trim() || null, notes: notes.trim() || null,
        })}>
        {pending ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}
        Take Payment
      </button>
    </>}>
      <label className="block text-xs text-slate-500">
        Find the folio
        <input value={search} onChange={(e) => setSearch(e.target.value)}
          placeholder="Guest, booking or room" className={`mt-1 ${input}`} />
      </label>
      <label className="block text-xs text-slate-500">
        Folio *
        <Select value={folioId}
          onChange={(e) => { setFolioId(e.target.value); setAmount('') }}
          className={`mt-1 w-full ${select}`}>
          <option value="">Pick a folio with a balance</option>
          {(folios ?? []).map((f: OpenFolio) => (
            <option key={f.folio_id} value={f.folio_id}>
              {f.guest_name ?? 'Unnamed'} · {f.room_code ?? f.reservation_number ?? '—'}
              {' '}· owes {exact.format(Number(f.balance))}
            </option>
          ))}
        </Select>
      </label>
      {(folios ?? []).length === 0 && (
        <p className="text-xs text-slate-400">
          No open folio has a balance right now.
        </p>
      )}
      <div className="flex gap-3">
        <label className="block flex-1 text-xs text-slate-500">
          Amount *
          <input type="number" min="0" step="0.01" value={amount}
            onChange={(e) => setAmount(e.target.value)} className={`mt-1 ${input}`} />
          {picked && (
            <span className="mt-1 block text-[11px] text-slate-400">
              Balance {exact.format(Number(picked.balance))}
            </span>
          )}
        </label>
        <label className="block flex-1 text-xs text-slate-500">
          Method *
          <Select value={method} onChange={(e) => setMethod(e.target.value)}
            className={`mt-1 w-full ${select}`}>
            {(options?.methods ?? []).map((m) => (
              <option key={m.value} value={m.value}>{m.label}</option>
            ))}
          </Select>
        </label>
      </div>
      <label className="block text-xs text-slate-500">
        Reference {needsRef && <span className="text-red-500">*</span>}
        <input value={reference} onChange={(e) => setReference(e.target.value)}
          placeholder={needsRef ? 'e.g. HDFC/8821' : 'Not needed for cash'}
          disabled={!needsRef} className={`mt-1 ${input} disabled:bg-slate-50`} />
      </label>
      <label className="block text-xs text-slate-500">
        Notes
        <input value={notes} onChange={(e) => setNotes(e.target.value)}
          className={`mt-1 ${input}`} />
      </label>
      {missing.length > 0 && (
        <p className="flex flex-wrap items-center gap-x-1.5 text-xs text-amber-700">
          <AlertTriangle size={12} /> Still needed:
          {missing.map((m) => (
            <span key={m} className="rounded bg-amber-50 px-1.5 py-0.5 font-medium">{m}</span>
          ))}
        </p>
      )}
    </Shell>
  )
}

function OpenShiftDialog({ onClose, onSubmit, pending }: {
  onClose: () => void; pending: boolean
  onSubmit: (body: Record<string, unknown>) => void
}) {
  const [float_, setFloat] = useState('0')
  const [notes, setNotes] = useState('')
  return (
    <Shell title="Open Cashier Shift" onClose={onClose} footer={<>
      <button onClick={onClose} className={ghost}>Cancel</button>
      <button disabled={pending || Number(float_) < 0} className={primary}
        onClick={() => onSubmit({
          opening_float: Number(float_), notes: notes.trim() || null,
        })}>
        {pending ? <Loader2 size={14} className="animate-spin" /> : <Clock size={14} />}
        Open Shift
      </button>
    </>}>
      <label className="block text-xs text-slate-500">
        Opening float
        <input type="number" min="0" step="0.01" value={float_}
          onChange={(e) => setFloat(e.target.value)} className={`mt-1 ${input}`} />
        <span className="mt-1 block text-[11px] text-slate-400">
          The cash physically placed in the drawer. It is added to what you take
          during the shift to work out what should be there at the end.
        </span>
      </label>
      <label className="block text-xs text-slate-500">
        Notes
        <input value={notes} onChange={(e) => setNotes(e.target.value)}
          className={`mt-1 ${input}`} />
      </label>
    </Shell>
  )
}

function CashDropDialog({ shift, onClose, onDone }: {
  shift: CashShift; onClose: () => void; onDone: (msg: string) => void
}) {
  const propertyId = useActivePropertyId()
  const [amount, setAmount] = useState('')
  const [destination, setDestination] = useState('safe')
  const [reference, setReference] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  // What is actually in the tray right now -- the ceiling on a drop. The
  // server refuses more than this; saying so here means the cashier is not
  // told only after typing it.
  const inTray = Number(shift.opening_float) + Number(shift.cash_taken)
    - Number(shift.cash_refunded) - Number(shift.cash_dropped)
  const value = amount === '' ? 0 : Number(amount)
  const tooMuch = value > inTray
  const valid = value > 0 && !tooMuch && !busy

  async function save() {
    setErr(''); setBusy(true)
    try {
      await recordCashDrop(shift.id, propertyId, {
        amount: value, destination,
        reference: reference.trim() || null,
        note: note.trim() || null,
      })
      onDone(`${exact.format(value)} banked to the safe.`)
    } catch (e) {
      const ax = e as { response?: { data?: { detail?: string } } }
      setErr(ax?.response?.data?.detail ?? 'The drop could not be recorded.')
    } finally { setBusy(false) }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-slate-900/30 p-4"
      onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()}
        className="mt-16 w-full max-w-md rounded-2xl bg-white p-6 shadow-xl">
        <div className="mb-1 flex items-center justify-between">
          <h2 className="flex items-center gap-2 text-lg font-semibold text-ink">
            <Banknote size={18} className="text-slate-400" /> Cash Drop
          </h2>
          <button onClick={onClose} aria-label="Close"
            className="rounded-lg p-1 text-slate-400 hover:bg-slate-100">
            <X size={18} />
          </button>
        </div>
        <p className="mb-4 text-sm text-slate-500">
          Move cash from the drawer to the safe. The shift stays open, and this
          is not a refund — the money is still the property&rsquo;s.
        </p>

        <label className="mb-3 block">
          <span className="mb-1 block text-sm font-medium text-slate-600">
            Amount *
          </span>
          <input type="number" min="0" step="0.01" value={amount}
            onChange={(e) => setAmount(e.target.value)} placeholder="0.00"
            className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm outline-none focus:border-brand" />
          <span className={`mt-1 block text-xs ${
            tooMuch ? 'text-red-600' : 'text-slate-500'}`}>
            {tooMuch
              ? `The drawer holds ${exact.format(inTray)}.`
              : `${exact.format(inTray)} in the drawer now.`}
          </span>
        </label>

        <label className="mb-3 block">
          <span className="mb-1 block text-sm font-medium text-slate-600">
            Where it went
          </span>
          <Select value={destination} onChange={(e) => setDestination(e.target.value)}
            className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm">
            <option value="safe">Property safe</option>
            <option value="bank">Bank deposit</option>
            <option value="head_office">Head office</option>
          </Select>
        </label>

        <label className="mb-3 block">
          <span className="mb-1 block text-sm font-medium text-slate-600">
            Reference
          </span>
          <input value={reference} onChange={(e) => setReference(e.target.value)}
            placeholder="Bag or deposit slip number"
            className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm outline-none focus:border-brand" />
        </label>

        <label className="mb-4 block">
          <span className="mb-1 block text-sm font-medium text-slate-600">
            Note
          </span>
          <input value={note} onChange={(e) => setNote(e.target.value)}
            placeholder="Anything the next person counting should know"
            className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm outline-none focus:border-brand" />
        </label>

        {err && (
          <p className="mb-3 flex items-start gap-2 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">
            <AlertTriangle size={14} className="mt-0.5 shrink-0" />{err}
          </p>
        )}

        <div className="flex justify-end gap-2">
          <button onClick={onClose}
            className="rounded-lg border border-slate-200 px-4 py-2 text-sm font-medium text-slate-600 hover:bg-slate-50">
            Cancel
          </button>
          <button onClick={() => void save()} disabled={!valid}
            className="flex items-center gap-1.5 rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white hover:bg-brand-dark disabled:opacity-40">
            {busy && <Loader2 size={14} className="animate-spin" />}
            Record drop
          </button>
        </div>
      </div>
    </div>
  )
}

function CloseShiftDialog({ shift, onClose, onSubmit, pending }: {
  shift: CashShift; onClose: () => void; pending: boolean
  onSubmit: (body: Record<string, unknown>) => void
}) {
  const [counted, setCounted] = useState('')
  const [notes, setNotes] = useState('')
  // The same arithmetic the server files: in, less what went back out.
  // Leaving refunds out here did not change what was recorded, only what the
  // cashier was shown before recording it -- which is worse, because they
  // agreed to a number they were never given.
  const refunded = Number(shift.cash_refunded)
  // Drops belong in this sum for the same reason refunds do: the server
  // subtracts them, so a dialog that left them out would show the cashier one
  // expected figure and file another.
  const dropped = Number(shift.cash_dropped)
  const expected = Number(shift.opening_float) + Number(shift.cash_taken)
    - refunded - dropped
  // A drawer cannot hold less than nothing. When it says otherwise, the float
  // is the figure that was wrong.
  const impossible = expected < 0
  const variance = counted === '' ? null : Number(counted) - expected

  return (
    <Shell title="Close Shift" onClose={onClose} footer={<>
      <button onClick={onClose} className={ghost}>Cancel</button>
      <button disabled={pending || counted === '' || Number(counted) < 0}
        className={primary}
        onClick={() => onSubmit({
          declared_cash: Number(counted), notes: notes.trim() || null,
        })}>
        {pending ? <Loader2 size={14} className="animate-spin" /> : <Square size={13} />}
        Close Shift
      </button>
    </>}>
      <dl className="space-y-1.5 rounded-xl bg-slate-50 p-3 text-sm">
        <div className="flex justify-between">
          <dt className="text-slate-500">Opening float</dt>
          <dd className="text-slate-700">{exact.format(Number(shift.opening_float))}</dd>
        </div>
        <div className="flex justify-between">
          <dt className="text-slate-500">
            Cash taken ({shift.payments_count} payment{shift.payments_count === 1 ? '' : 's'})
          </dt>
          <dd className="text-slate-700">{exact.format(Number(shift.cash_taken))}</dd>
        </div>
        {refunded > 0 && (
          <div className="flex justify-between">
            <dt className="text-slate-500">
              Cash refunded ({shift.refunds_count} refund{shift.refunds_count === 1 ? '' : 's'})
            </dt>
            <dd className="text-slate-700">− {exact.format(refunded)}</dd>
          </div>
        )}
        {dropped > 0 && (
          <div className="flex justify-between">
            <dt className="text-slate-500">
              Banked to the safe ({shift.drops_count} drop{shift.drops_count === 1 ? '' : 's'})
            </dt>
            <dd className="text-slate-700">− {exact.format(dropped)}</dd>
          </div>
        )}
        <div className="flex justify-between border-t border-slate-200 pt-1.5">
          <dt className="font-semibold text-slate-700">Should be in the drawer</dt>
          <dd className={`font-semibold ${
            impossible ? 'text-red-600' : 'text-slate-800'}`}>
            {exact.format(expected)}
          </dd>
        </div>
      </dl>

      {impossible && (
        <p className="flex items-start gap-2 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800">
          <AlertTriangle size={13} className="mt-0.5 shrink-0" />
          <span>
            A drawer cannot hold less than nothing. {exact.format(refunded)} was
            handed back from a float of {exact.format(Number(shift.opening_float))},
            so the float was almost certainly understated when this shift opened.
            Closing now files a variance of about {exact.format(Math.abs(expected))}
            that is not a real difference. Count the drawer and note the reason —
            the float cannot be corrected once this is closed.
          </span>
        </p>
      )}
      <label className="block text-xs text-slate-500">
        Counted cash *
        <input type="number" min="0" step="0.01" value={counted} autoFocus
          onChange={(e) => setCounted(e.target.value)} className={`mt-1 ${input}`} />
        <span className="mt-1 block text-[11px] text-slate-400">
          Card and UPI never entered the drawer, so they play no part in this count.
        </span>
      </label>
      {variance !== null && (
        <p className={`rounded-lg px-3 py-2 text-sm ${
          variance === 0 ? 'bg-emerald-50 text-emerald-700'
            : 'bg-red-50 text-red-700'}`}>
          {variance === 0 ? 'The drawer balances.'
            : `${exact.format(Math.abs(variance))} ${variance < 0 ? 'short' : 'over'}. `
              + 'The difference is recorded against this shift.'}
        </p>
      )}
      <label className="block text-xs text-slate-500">
        Notes
        <input value={notes} onChange={(e) => setNotes(e.target.value)}
          placeholder={variance ? 'Explain the difference' : ''}
          className={`mt-1 ${input}`} />
      </label>
    </Shell>
  )
}
