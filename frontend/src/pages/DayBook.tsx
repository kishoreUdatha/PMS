import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  BookOpen, Download, Loader2, Search, X,
} from 'lucide-react'
import DateField from '../components/DateField'
import Select from '../components/Select'
import { CONTROL_TYPE, FILTER_SELECT } from '../lib/controls'
import { fmtDate, fmtTime } from '../lib/dates'
import { downloadCsv, datedName } from '../lib/csv'
import { getDayBook, type DayBookRow } from '../api'
import { useActivePropertyId } from '../hooks/useProperty'

/**
 * The Day Book — every movement on every folio, for one business date.
 *
 * The Cashiering Centre answers "what money crossed the counter today". It is
 * deliberately narrow and it was called Transactions, so a charge posted to a
 * folio read as a row the system had lost. It had not: charges never cross a
 * counter. This screen is the other question — "what happened on the folios
 * today" — and it is the one a manager closing a day actually wants.
 *
 * Four kinds in one list, in the order a hotel thinks about them: what guests
 * were charged, what they paid, what was handed back, what was written off.
 * The figures along the top are the day's, never the filter's: a day book
 * that retotalled itself when you searched one room would stop being a day
 * book and become a calculator.
 */

const money = new Intl.NumberFormat('en-IN', {
  style: 'currency', currency: 'INR', minimumFractionDigits: 2,
})
const select = `${FILTER_SELECT} bg-white outline-none focus:border-brand`

/** Each kind's colour, so the eye can pick one out of a long day. */
const KIND_TINT: Record<string, string> = {
  charge: 'bg-amber-100 text-amber-700',
  payment: 'bg-emerald-100 text-emerald-700',
  refund: 'bg-rose-100 text-rose-700',
  adjustment: 'bg-violet-100 text-violet-700',
  deposit: 'bg-sky-100 text-sky-700',
}

/** A total, sized to sit in the filter row like the Cashiering Centre's. */
function Figure({ label, value, tone, title, onClick, active }: {
  label: string; value: string; tone?: string; title?: string
  onClick?: () => void; active?: boolean
}) {
  const Tag = (onClick ? 'button' : 'div') as 'button' | 'div'
  return (
    <Tag onClick={onClick} title={title}
      className={`flex items-baseline gap-1.5 rounded-lg border px-3 py-2 ${
        active ? 'border-brand bg-brand-light/40' : 'border-slate-200 bg-white'
      } ${onClick ? 'hover:border-brand' : ''}`}>
      <span className="text-xs text-slate-500">{label}</span>
      <span className={`text-sm font-semibold ${tone ?? 'text-ink'}`}>{value}</span>
    </Tag>
  )
}

export default function DayBook() {
  const propertyId = useActivePropertyId()
  const [params, setParams] = useSearchParams()
  const [q, setQ] = useState(params.get('q') ?? '')

  // Every filter lives in the URL, so a day worth talking about can be sent
  // to somebody as a link rather than described over the phone.
  const onDate = params.get('date') ?? ''
  const kind = params.get('kind') ?? ''
  const room = params.get('room') ?? ''
  const set = (k: string, v: string) => {
    const next = new URLSearchParams(params)
    if (v) next.set(k, v); else next.delete(k)
    setParams(next, { replace: true })
  }

  const book = useQuery({
    queryKey: ['daybook', propertyId, onDate, kind, room, params.get('q')],
    enabled: !!propertyId,
    queryFn: () => getDayBook(propertyId!, {
      on_date: onDate || undefined,
      kind: kind || undefined,
      room: room || undefined,
      q: params.get('q') || undefined,
    }),
  })

  const t = book.data?.totals
  const rows = book.data?.rows ?? []
  const filtered = !!(kind || room || params.get('q'))

  const exportCsv = () => {
    downloadCsv(
      datedName('day-book', book.data?.business_date),
      ['Posted', 'Business date', 'Folio', 'Reservation', 'Guest', 'Room',
        'Kind', 'Description', 'Note', 'Debit', 'Credit', 'Posted by'],
      rows.map((r) => [
        `${fmtDate(new Date(r.posted_at))} ${fmtTime(r.posted_at)}`,
        r.business_date, r.folio_no ?? '', r.reservation_number ?? '',
        r.guest_name ?? '', r.room_code ?? '', r.kind_label, r.description,
        r.note ?? '',
        r.debit != null ? Number(r.debit).toFixed(2) : '',
        r.credit != null ? Number(r.credit).toFixed(2) : '',
        r.posted_by ?? '',
      ]),
    )
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="flex items-center gap-2 text-display text-ink">
            <BookOpen size={26} className="text-brand" /> Day Book
          </h1>
          <p className="mt-1 text-sm text-slate-500">
            Every movement on every folio for one business date — charges,
            payments, refunds and adjustments together.
          </p>
        </div>
        <button onClick={exportCsv} disabled={!rows.length}
          className={`flex items-center gap-1.5 rounded-lg border border-slate-200
            bg-white px-3 py-2 ${CONTROL_TYPE} text-slate-600
            hover:border-brand disabled:opacity-40`}>
          <Download size={15} /> Export
        </button>
      </div>

      {/* ------------------------------------------------- figures + filters --- */}
      <div className="flex flex-wrap items-center gap-2">
        {/* Seeded from the answer when the URL names no date. The server
            defaults to the day the property is trading, and a field reading
            "Pick a date" above a list of the 19th's movements leaves the
            reader to guess which day they are looking at — on this screen of
            all of them, where the whole point is which day. */}
        <DateField value={onDate || book.data?.business_date || ''}
          onChange={(v) => set('date', v)} label="Business date" />

        {/* The kind chips double as the day's breakdown, so pressing one
            never reveals a number that was not already on screen. */}
        {(book.data?.kinds ?? []).map((k) => (
          <button key={k.value} disabled={!k.count}
            onClick={() => set('kind', kind === k.value ? '' : k.value)}
            title={`${k.count} ${k.label.toLowerCase()}${
              k.count === 1 ? '' : 's'} — ${money.format(Math.abs(Number(k.total)))}`}
            className={`rounded-full px-3 py-1.5 ${CONTROL_TYPE} ${
              kind === k.value
                ? 'bg-brand text-white'
                : `${KIND_TINT[k.value]} disabled:opacity-40`}`}>
            {k.label}
            <span className="ml-1.5 text-xs opacity-70">{k.count}</span>
          </button>
        ))}

        <Select value={room} onChange={(e) => set('room', e.target.value)}
          title="Room" aria-label="Room" className={select}>
          <option value="">All rooms</option>
          {(book.data?.rooms ?? []).map((r) => (
            <option key={r} value={r}>{r}</option>
          ))}
        </Select>

        <form onSubmit={(e) => { e.preventDefault(); set('q', q) }}
          className="relative">
          <Search size={15}
            className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
          <input value={q} onChange={(e) => setQ(e.target.value)}
            placeholder="Folio, booking or guest"
            className="w-56 rounded-lg border border-slate-200 py-2 pl-8 pr-3
              text-sm text-slate-700 outline-none focus:border-brand" />
        </form>

        {filtered && (
          <button onClick={() => { setQ(''); setParams(
            onDate ? new URLSearchParams({ date: onDate }) : new URLSearchParams(),
            { replace: true }) }}
            title="Clear filters"
            className="rounded-lg border border-slate-200 bg-white p-2
              text-slate-500 hover:border-brand">
            <X size={15} />
          </button>
        )}
      </div>

      {t && (
        <div className="flex flex-wrap items-center gap-2">
          <Figure label="Charges" value={money.format(Number(t.charges))} />
          <Figure label="Payments" value={money.format(Number(t.payments))} />
          <Figure label="Refunds" value={money.format(Number(t.refunds))} />
          <Figure label="Adjustments" value={money.format(Number(t.adjustments))}
            title="Signed: negative means the day's adjustments came off guest bills on balance" />
          {/* The line that ties this screen to the folios. Owed MORE is not
              good news or bad news, so it is not coloured as either. */}
          <Figure label="Net movement" value={money.format(Number(t.net))}
            tone="text-ink"
            title="Debits less credits — the change in what the house is owed across the day" />
          <span className="text-xs text-slate-400">
            {t.count} movement{t.count === 1 ? '' : 's'}
            {filtered && ` · showing ${rows.length}`}
          </span>
        </div>
      )}

      {/* ---------------------------------------------------------- table --- */}
      <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
        <table className="w-full min-w-[56rem] text-sm">
          <thead className="border-b border-slate-200 bg-slate-50 text-left
            text-xs uppercase tracking-wide text-slate-500">
            <tr>
              <th className="px-4 py-3 font-medium">Posted</th>
              <th className="px-4 py-3 font-medium">Folio</th>
              <th className="px-4 py-3 font-medium">Guest</th>
              <th className="px-4 py-3 font-medium">Room</th>
              <th className="px-4 py-3 font-medium">Kind</th>
              <th className="px-4 py-3 font-medium">Description</th>
              <th className="px-4 py-3 text-right font-medium">Charge</th>
              <th className="px-4 py-3 text-right font-medium">Credit</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {book.isLoading && (
              <tr><td colSpan={8} className="px-4 py-10 text-center">
                <Loader2 className="mx-auto h-4 w-4 animate-spin text-slate-400" />
              </td></tr>
            )}
            {!book.isLoading && rows.length === 0 && (
              <tr><td colSpan={8}
                className="px-4 py-10 text-center text-sm text-slate-400">
                {filtered
                  ? 'No movements match these filters.'
                  : 'Nothing was posted to any folio on this business date.'}
              </td></tr>
            )}
            {rows.map((r: DayBookRow) => (
              <tr key={r.entry_id} className="hover:bg-slate-50">
                <td className="whitespace-nowrap px-4 py-3 text-slate-600">
                  <div>{fmtDate(new Date(r.posted_at))}</div>
                  <div className="text-xs text-slate-400">{fmtTime(r.posted_at)}</div>
                </td>
                <td className="whitespace-nowrap px-4 py-3">
                  {r.reservation_id ? (
                    <Link to={`/reservations/${r.reservation_id}`}
                      className="font-medium text-brand hover:underline">
                      {r.folio_no ?? '—'}
                    </Link>
                  ) : (
                    <span className="font-medium text-slate-700">
                      {r.folio_no ?? '—'}
                    </span>
                  )}
                  {r.reservation_number && (
                    <div className="text-xs text-slate-400">
                      {r.reservation_number}
                    </div>
                  )}
                </td>
                <td className="px-4 py-3 text-slate-700">{r.guest_name ?? '—'}</td>
                <td className="px-4 py-3 text-slate-600">{r.room_code ?? '—'}</td>
                <td className="whitespace-nowrap px-4 py-3">
                  <span className={`rounded-full px-2 py-0.5 text-xs font-medium
                    ${KIND_TINT[r.kind]}`}>
                    {r.kind_label}
                  </span>
                </td>
                <td className="px-4 py-3 text-slate-700">
                  {r.description}
                  {r.note && (
                    <div className="text-xs text-slate-400">{r.note}</div>
                  )}
                  {/* A reversing entry read as a second mistake until it said
                      so. Both rows are real and both stay; this names which. */}
                  {r.reverses_entry_id && (
                    <div className="text-xs text-violet-500">Reverses an earlier entry</div>
                  )}
                </td>
                <td className="whitespace-nowrap px-4 py-3 text-right
                  tabular-nums text-slate-700">
                  {r.debit != null ? money.format(Number(r.debit)) : ''}
                </td>
                <td className="whitespace-nowrap px-4 py-3 text-right
                  tabular-nums text-emerald-700">
                  {r.credit != null ? money.format(Number(r.credit)) : ''}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
