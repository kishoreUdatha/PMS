import { useState } from 'react'
import { fmtDate, fmtDateTime } from '../lib/dates'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Loader2, Printer, FileCheck2, Ban, FilePlus2, AlertTriangle, CheckCircle2,
  Info, X,
} from 'lucide-react'
import {
  getInvoice, issueInvoice, cancelInvoice, createCreditNote, type Invoice,
  openInvoicePdf,
} from '../api'
import { Crumbs } from '../components/Crumbs'
import { useActivePropertyId } from '../hooks/useProperty'

const money = (v: string | number, cur = 'INR') =>
  new Intl.NumberFormat('en-IN', {
    style: 'currency', currency: cur, minimumFractionDigits: 2,
  }).format(Number(v))
const plain = (v: string | number) =>
  new Intl.NumberFormat('en-IN', { minimumFractionDigits: 2 }).format(Number(v))
const day = (d: string | null) => fmtDate(d)
const stamp = (d: string | null) => fmtDateTime(d)

const STATUS: Record<string, { cls: string; note: string }> = {
  draft: { cls: 'bg-amber-50 text-amber-700', note: 'Not yet issued — no number assigned' },
  issued: { cls: 'bg-emerald-50 text-emerald-700', note: 'Issued to the guest' },
  cancelled: { cls: 'bg-slate-75 text-slate-600', note: 'Cancelled before issue' },
}

export default function InvoiceDetail() {
  const { invoiceId = '' } = useParams()
  const propertyId = useActivePropertyId()
  const qc = useQueryClient()
  const nav = useNavigate()
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [printing, setPrinting] = useState(false)
  const [ok, setOk] = useState('')
  const [modal, setModal] = useState<'credit' | 'cancel' | null>(null)

  const { data: inv, isLoading } = useQuery({
    queryKey: ['invoice', invoiceId, propertyId],
    queryFn: () => getInvoice(invoiceId, propertyId),
    enabled: invoiceId !== '' && propertyId !== '',
  })

  function refresh() {
    qc.invalidateQueries({ queryKey: ['invoice', invoiceId] })
    qc.invalidateQueries({ queryKey: ['invoices'] })
  }
  function fail(e: unknown) {
    const er = e as { response?: { data?: { detail?: string } }; message?: string }
    setErr(er.response?.data?.detail ?? er.message ?? 'That did not work.')
  }

  async function doIssue() {
    setErr(''); setOk(''); setBusy(true)
    try {
      const next = await issueInvoice(invoiceId, propertyId)
      setOk(`Issued as ${next.display_number}.`)
      refresh()
    } catch (e) { fail(e) } finally { setBusy(false) }
  }

  if (isLoading) {
    return <p className="py-20 text-center text-slate-400">
      <Loader2 className="mx-auto animate-spin" />
    </p>
  }
  if (!inv) {
    return <p className="py-20 text-center text-sm text-slate-400">
      Invoice not found.
    </p>
  }

  const st = STATUS[inv.status] ?? STATUS.draft
  const cur = inv.currency

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <Crumbs title="Invoice and Credit Note" trail={[
            { label: 'Finance', to: '/finance' },
            { label: 'Invoices', to: '/finance/invoices' },
            { label: inv.display_number }]} />
          <h1 className="mt-1 text-display text-ink">
            Invoice and Credit Note
          </h1>
          <p className="text-slate-500">
            Create, view and manage invoices and credit notes for guest folios.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className={`rounded-xl px-4 py-2 text-sm font-semibold ${st.cls}`}>
            {inv.status[0].toUpperCase() + inv.status.slice(1)}
            <span className="block text-[11px] font-normal opacity-80">{st.note}</span>
          </span>
          {/* The rendered document, not the browser's view of this page. */}
          <button
            disabled={printing}
            onClick={async () => {
              setPrinting(true)
              setErr('')
              try {
                await openInvoicePdf(propertyId, inv.id)
              } catch {
                setErr('The invoice could not be produced.')
              } finally { setPrinting(false) }
            }}
            className="flex items-center gap-2 rounded-xl border border-slate-200 px-4 py-2.5 text-sm font-medium text-slate-600 hover:bg-slate-50 disabled:opacity-50">
            {printing
              ? <Loader2 size={16} className="animate-spin" />
              : <Printer size={16} />} Print
          </button>
        </div>
      </div>

      {err && (
        <p className="flex items-start gap-2 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700">
          <AlertTriangle size={16} className="mt-0.5 shrink-0" /> {err}
        </p>
      )}
      {ok && (
        <p className="flex items-start gap-2 rounded-xl bg-emerald-50 px-4 py-3 text-sm text-emerald-700">
          <CheckCircle2 size={16} className="mt-0.5 shrink-0" /> {ok}
        </p>
      )}

      {!inv.tax_compliance.compliant && (
        <p className="flex items-start gap-2 rounded-xl bg-amber-50 px-4 py-3 text-sm text-caution">
          <Info size={16} className="mt-0.5 shrink-0" />
          <span>
            {inv.tax_compliance.note}
            {inv.tax_compliance.missing.length > 0 && (
              <span className="mt-1 block text-xs">
                Missing: {inv.tax_compliance.missing.join(', ')}.
                {' '}
                <Link to="/finance/invoice-settings" className="font-semibold underline">
                  Configure them
                </Link>
              </span>
            )}
          </span>
        </p>
      )}

      <div className="flex flex-col gap-5 xl:flex-row">
        {/* ---- The document ---- */}
        <div className="min-w-0 flex-1 rounded-2xl border border-slate-100 bg-white p-6">
          <div className="flex flex-wrap items-start justify-between gap-6 border-b border-slate-100 pb-5">
            <div>
              <h2 className="text-2xl font-bold text-ink">
                {inv.tax_compliance.compliant ? 'Tax Invoice' : 'Invoice'}
              </h2>
              <p className="text-lg font-semibold text-brand">{inv.display_number}</p>
            </div>
            <div className="text-sm text-slate-600">
              <p className="font-semibold text-slate-800">
                {inv.supplier.name ?? 'Legal name not configured'}
              </p>
              {inv.supplier.address_line && <p>{inv.supplier.address_line}</p>}
              <p>{[inv.supplier.city, inv.supplier.state, inv.supplier.postal_code]
                .filter(Boolean).join(', ') || '—'}</p>
              {inv.supplier.gstin && <p>GSTIN: {inv.supplier.gstin}</p>}
              {inv.supplier.phone && <p>{inv.supplier.phone}</p>}
            </div>
          </div>

          <div className="grid gap-6 border-b border-slate-100 py-5 sm:grid-cols-3">
            <div className="text-sm">
              <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-400">
                Bill To
              </p>
              <p className="font-semibold text-slate-800">
                {inv.customer.name ?? '—'}
              </p>
              {inv.customer.address_line && (
                <p className="text-slate-600">{inv.customer.address_line}</p>
              )}
              <p className="text-slate-600">
                {[inv.customer.city, inv.customer.state, inv.customer.postal_code]
                  .filter(Boolean).join(', ') || '—'}
              </p>
              {inv.customer.gstin && (
                <p className="mt-1 text-slate-600">GSTIN: {inv.customer.gstin}</p>
              )}
            </div>
            <Facts rows={[
              ['Invoice Date', inv.issued_at ? day(inv.issued_at) : 'Not issued'],
              ['Check-in', day(inv.arrival_date)],
              ['Check-out', day(inv.departure_date)],
              ['No. of Nights', inv.nights ?? '—'],
            ]} />
            <Facts rows={[
              ['No. of Guests', [
                inv.adults ? `${inv.adults} Adult${inv.adults === 1 ? '' : 's'}` : null,
                inv.children ? `${inv.children} Child${inv.children === 1 ? '' : 'ren'}` : null,
              ].filter(Boolean).join(', ') || '—'],
              ['Folio No.', inv.folio_no ?? '—'],
              ['Booking No.', inv.reservation_number ?? '—'],
              ['Room', inv.room_code
                ? `${inv.room_code}${inv.room_type ? ` (${inv.room_type})` : ''}`
                : '—'],
            ]} />
          </div>

          <div className="-mx-6 overflow-x-auto py-5">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="px-6 py-2.5 font-semibold">#</th>
                  <th className="px-3 py-2.5 font-semibold">Description</th>
                  <th className="px-3 py-2.5 font-semibold">Date</th>
                  <th className="px-3 py-2.5 text-right font-semibold">Qty</th>
                  <th className="px-6 py-2.5 text-right font-semibold">
                    Amount ({cur})
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-50">
                {inv.lines.length === 0 && (
                  <tr><td colSpan={5} className="px-6 py-8 text-center text-slate-400">
                    Nothing left to bill on this folio.
                  </td></tr>
                )}
                {inv.lines.map((l, i) => (
                  <tr key={l.entry_id}>
                    <td className="px-6 py-2.5 text-slate-400">{i + 1}</td>
                    <td className="px-3 py-2.5 text-slate-700">{l.description}</td>
                    <td className="px-3 py-2.5 text-slate-500">{day(l.business_date)}</td>
                    <td className="px-3 py-2.5 text-right text-slate-500">
                      {Number(l.quantity)}
                    </td>
                    <td className="px-6 py-2.5 text-right font-medium text-slate-800">
                      {plain(l.amount)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="flex flex-wrap justify-between gap-6 border-t border-slate-100 pt-5">
            <div className="min-w-0 max-w-sm">
              <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">
                Amount in Words
              </p>
              <p className="text-sm text-slate-700">{inv.amount_in_words}</p>
              {inv.notes && (
                <p className="mt-3 text-xs text-slate-500">{inv.notes}</p>
              )}
            </div>
            <dl className="w-full max-w-xs space-y-1.5 text-sm">
              <Row k="Subtotal" v={plain(inv.subtotal)} />
              {inv.tax_lines.map((t) => (
                <Row key={t.code}
                  k={`${t.code}${Number(t.rate) ? ` @ ${Number(t.rate)}%` : ''}`}
                  v={plain(t.tax_amount)} muted />
              ))}
              {inv.tax_lines.length === 0 && (
                <Row k="Tax" v="0.00" muted />
              )}
              <Row k={`Total (${cur})`} v={plain(inv.total)} strong />
              <Row k="Amount Received" v={plain(inv.amount_received)} muted />
              {Number(inv.credited) > 0 && (
                <Row k="Credited" v={`- ${plain(inv.credited)}`} muted />
              )}
              <Row k="Balance Due" v={plain(inv.balance_due)} strong />
            </dl>
          </div>

          <div className="mt-6 flex flex-wrap justify-end gap-2 border-t border-slate-100 pt-5">
            <button onClick={() => nav('/finance/invoices')}
              className="rounded-xl border border-slate-200 px-4 py-2.5 text-sm text-slate-600 hover:bg-slate-50">
              Back to List
            </button>
            {inv.can_cancel && (
              <button onClick={() => setModal('cancel')}
                className="flex items-center gap-2 rounded-xl border border-slate-200 px-4 py-2.5 text-sm font-medium text-slate-600 hover:bg-slate-50">
                <Ban size={16} /> Cancel Draft
              </button>
            )}
            {inv.can_credit && (
              <button onClick={() => setModal('credit')}
                className="flex items-center gap-2 rounded-xl border border-slate-200 px-4 py-2.5 text-sm font-medium text-slate-600 hover:bg-slate-50">
                <FilePlus2 size={16} /> Create Credit Note
              </button>
            )}
            {inv.can_issue && (
              <button onClick={doIssue} disabled={busy}
                className="flex items-center gap-2 rounded-xl bg-brand px-5 py-2.5 text-sm font-medium text-white enabled:hover:bg-brand/90 disabled:opacity-50">
                {busy ? <Loader2 size={16} className="animate-spin" />
                  : <FileCheck2 size={16} />} Issue Invoice
              </button>
            )}
          </div>
        </div>

        {/* ---- Side panels ---- */}
        <div className="w-full shrink-0 space-y-4 xl:w-96">
          <Panel title="Payments" badge={
            Number(inv.balance_due) <= 0
              ? <Pill tone="emerald">Fully paid</Pill>
              : <Pill tone="amber">{money(inv.balance_due, cur)} due</Pill>
          }>
            {inv.payments.length === 0 ? (
              <p className="py-4 text-center text-sm text-slate-400">
                Nothing has been paid against this folio.
              </p>
            ) : (
              <table className="w-full text-sm">
                <thead className="text-left text-xs uppercase tracking-wide text-slate-400">
                  <tr>
                    <th className="pb-2 font-semibold">Date</th>
                    <th className="pb-2 font-semibold">Mode</th>
                    <th className="pb-2 text-right font-semibold">Amount</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-50">
                  {inv.payments.map((p) => (
                    <tr key={p.id}>
                      <td className="py-2 text-slate-500">{day(p.received_at)}</td>
                      <td className="py-2 text-slate-700">
                        {p.method.toUpperCase()}
                        {p.reference && (
                          <span className="block text-[11px] text-slate-400">
                            {p.reference}
                          </span>
                        )}
                      </td>
                      <td className="py-2 text-right text-slate-800">
                        {plain(p.amount)}
                      </td>
                    </tr>
                  ))}
                  {/* Without this line the rows above would not add up to
                      the total, and the total would look wrong. */}
                  {Number(inv.amount_refunded) > 0 && (
                    <tr>
                      <td className="py-2 text-slate-500" colSpan={2}>
                        Refunded to guest
                      </td>
                      <td className="py-2 text-right text-amber-700">
                        - {plain(inv.amount_refunded)}
                      </td>
                    </tr>
                  )}
                  <tr className="font-semibold">
                    <td className="pt-2 text-slate-600" colSpan={2}>Total Received</td>
                    <td className="pt-2 text-right text-slate-800">
                      {plain(inv.amount_received)}
                    </td>
                  </tr>
                </tbody>
              </table>
            )}
          </Panel>

          <Panel title="Credit Notes" badge={
            inv.can_credit ? (
              <button onClick={() => setModal('credit')}
                className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-semibold text-slate-600 hover:bg-slate-50">
                + Create
              </button>
            ) : null
          }>
            {inv.credit_notes.length === 0 ? (
              <p className="py-4 text-center text-sm text-slate-400">
                None issued against this invoice.
              </p>
            ) : (
              <ul className="divide-y divide-slate-50 text-sm">
                {inv.credit_notes.map((c) => (
                  <li key={c.id} className="flex items-start justify-between gap-3 py-2.5">
                    <span className="min-w-0">
                      <span className="block font-semibold text-slate-800">
                        {c.display_number}
                      </span>
                      <span className="block text-xs text-slate-500">{c.reason}</span>
                      <span className="block text-[11px] text-slate-400">
                        {stamp(c.issued_at ?? c.created_at)}
                      </span>
                    </span>
                    <span className="shrink-0 text-right">
                      <span className="block font-semibold text-slate-800">
                        {plain(c.amount)}
                      </span>
                      <Pill tone="emerald">{c.status}</Pill>
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </Panel>

          <Panel title="Approval & Audit">
            {inv.audit.length === 0 ? (
              <p className="py-4 text-center text-sm text-slate-400">
                Nothing recorded yet.
              </p>
            ) : (
              <ul className="space-y-2 text-sm">
                {inv.audit.map((a, i) => (
                  <li key={i} className="flex flex-wrap items-baseline justify-between gap-2">
                    <span className="text-slate-500">{a.label}</span>
                    <span className="text-slate-800">{a.actor}</span>
                    <span className="w-full text-[11px] text-slate-400">
                      {stamp(a.at)} · {a.status}
                    </span>
                  </li>
                ))}
              </ul>
            )}
            {inv.cancel_reason && (
              <p className="mt-3 rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-600">
                Cancelled: “{inv.cancel_reason}”
              </p>
            )}
          </Panel>
        </div>
      </div>

      {modal === 'credit' && (
        <CreditModal inv={inv} onClose={() => setModal(null)}
          onDone={(m) => { setOk(m); setErr(''); refresh() }} onFail={fail} />
      )}
      {modal === 'cancel' && (
        <CancelModal inv={inv} onClose={() => setModal(null)}
          onDone={(m) => { setOk(m); setErr(''); refresh() }} onFail={fail} />
      )}
    </div>
  )
}

function Facts({ rows }: { rows: [string, string | number][] }) {
  return (
    <dl className="space-y-1 text-sm">
      {rows.map(([k, v]) => (
        <div key={k} className="flex justify-between gap-3">
          <dt className="text-slate-500">{k}</dt>
          <dd className="text-right font-medium text-slate-800">{v}</dd>
        </div>
      ))}
    </dl>
  )
}

function Row({ k, v, strong, muted }: {
  k: string; v: string; strong?: boolean; muted?: boolean
}) {
  return (
    <div className={`flex justify-between gap-4 ${
      strong ? 'border-t border-slate-200 pt-1.5' : ''}`}>
      <dt className={strong ? 'font-semibold text-slate-700'
        : muted ? 'text-slate-500' : 'text-slate-600'}>{k}</dt>
      <dd className={strong ? 'font-bold text-slate-900' : 'text-slate-700'}>{v}</dd>
    </div>
  )
}

function Panel({ title, badge, children }: {
  title: string; badge?: React.ReactNode; children: React.ReactNode
}) {
  return (
    <div className="rounded-2xl border border-slate-100 bg-white p-5">
      <div className="mb-3 flex items-center justify-between gap-3">
        <h2 className="text-lg font-semibold text-ink">{title}</h2>
        {badge}
      </div>
      {children}
    </div>
  )
}

function Pill({ tone, children }: { tone: 'emerald' | 'amber'; children: React.ReactNode }) {
  return (
    <span className={`inline-block rounded-full px-2 py-0.5 text-[11px] font-semibold ${
      tone === 'emerald' ? 'bg-emerald-50 text-emerald-700'
        : 'bg-amber-50 text-amber-700'}`}>
      {children}
    </span>
  )
}

function Shell({ title, onClose, children }: {
  title: string; onClose: () => void; children: React.ReactNode
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4"
      onClick={onClose}>
      <div className="w-full max-w-md rounded-2xl bg-white p-6 shadow-xl"
        onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold text-ink">{title}</h2>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600">
            <X size={18} />
          </button>
        </div>
        {children}
      </div>
    </div>
  )
}

const inputCls = 'w-full rounded-lg border border-slate-200 px-3 py-2.5 text-sm outline-none focus:border-brand'

function CreditModal({ inv, onClose, onDone, onFail }: {
  inv: Invoice; onClose: () => void
  onDone: (m: string) => void; onFail: (e: unknown) => void
}) {
  const remaining = Number(inv.total) - Number(inv.credited)
  const [amount, setAmount] = useState('')
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const n = Number(amount)
  const valid = n > 0 && n <= remaining && reason.trim().length >= 3
  const propertyId = useActivePropertyId()

  async function submit() {
    setBusy(true)
    try {
      await createCreditNote(inv.id, {
        property_id: propertyId,
        amount: n, reason: reason.trim(),
      })
      onDone(`Credit note issued for ${money(n, inv.currency)}.`)
      onClose()
    } catch (e) { onFail(e); onClose() } finally { setBusy(false) }
  }

  return (
    <Shell title="Create Credit Note" onClose={onClose}>
      <p className="mt-3 rounded-lg bg-blue-50 px-3 py-2 text-xs text-blue-800">
        A credit note posts a credit to the guest's folio. The invoice is not
        edited — it stays exactly as issued, with the credit recorded against it.
      </p>
      <label className="mt-3 block">
        <span className="mb-1 block text-sm font-medium text-slate-600">
          Amount ({inv.currency})
        </span>
        <input value={amount} onChange={(e) => setAmount(e.target.value)}
          type="number" min="0" step="0.01" className={inputCls}
          placeholder={`Up to ${plain(remaining)}`} autoFocus />
        <span className="mt-1 block text-xs text-slate-400">
          Invoice {plain(inv.total)} · already credited {plain(inv.credited)} ·
          {' '}remaining {plain(remaining)}
        </span>
      </label>
      <label className="mt-3 block">
        <span className="mb-1 block text-sm font-medium text-slate-600">Reason</span>
        <textarea value={reason} onChange={(e) => setReason(e.target.value)}
          rows={3} maxLength={500} className={`${inputCls} resize-none`}
          placeholder="e.g. Spa service not availed" />
      </label>
      <div className="mt-5 flex justify-end gap-2">
        <button onClick={onClose}
          className="rounded-lg border border-slate-200 px-4 py-2 text-sm text-slate-600 hover:bg-slate-50">
          Cancel
        </button>
        <button onClick={submit} disabled={!valid || busy}
          className="flex items-center gap-2 rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white enabled:hover:bg-brand/90 disabled:opacity-50">
          {busy && <Loader2 size={15} className="animate-spin" />} Issue Credit Note
        </button>
      </div>
    </Shell>
  )
}

function CancelModal({ inv, onClose, onDone, onFail }: {
  inv: Invoice; onClose: () => void
  onDone: (m: string) => void; onFail: (e: unknown) => void
}) {
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const propertyId = useActivePropertyId()

  async function submit() {
    setBusy(true)
    try {
      await cancelInvoice(inv.id, {
        property_id: propertyId,
        reason: reason.trim(),
      })
      onDone('Draft cancelled.')
      onClose()
    } catch (e) { onFail(e); onClose() } finally { setBusy(false) }
  }

  return (
    <Shell title="Cancel Draft" onClose={onClose}>
      <p className="mt-3 text-sm text-slate-600">
        The charges go back to being uninvoiced and can be billed again. No
        number is consumed, because a draft never had one.
      </p>
      <label className="mt-3 block">
        <span className="mb-1 block text-sm font-medium text-slate-600">Reason</span>
        <textarea value={reason} onChange={(e) => setReason(e.target.value)}
          rows={3} maxLength={500} className={`${inputCls} resize-none`} autoFocus
          placeholder="Why is this draft being abandoned?" />
      </label>
      <div className="mt-5 flex justify-end gap-2">
        <button onClick={onClose}
          className="rounded-lg border border-slate-200 px-4 py-2 text-sm text-slate-600 hover:bg-slate-50">
          Keep it
        </button>
        <button onClick={submit} disabled={reason.trim().length < 3 || busy}
          className="flex items-center gap-2 rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white enabled:hover:bg-red-700 disabled:opacity-50">
          {busy && <Loader2 size={15} className="animate-spin" />} Cancel Draft
        </button>
      </div>
    </Shell>
  )
}
