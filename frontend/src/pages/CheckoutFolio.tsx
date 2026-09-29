import { useEffect, useState } from 'react'
import Select from '../components/Select'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Receipt, Plus, Loader2, X, CheckCircle2, ArrowLeft,
  CalendarClock, FileText, Printer,
} from 'lucide-react'
import {
  getFolioView, addFolioCharge, collectFolioPayment, openFolioPdf,
  type FolioView,
} from '../api'
import { usePaymentMethods } from '../lib/paymentMethods'
import { useActivePropertyId } from '../hooks/useProperty'
import { errorText } from '../lib/forms'

const money = new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 })
// Tax components are shown to the paisa: rounding 162.50 to 163 twice makes
// the breakdown disagree with the total the guest is charged.
const taxMoney = new Intl.NumberFormat('en-IN', {
  style: 'currency', currency: 'INR', minimumFractionDigits: 2,
})

// The list is served, not written here. See lib/paymentMethods: these keys
// used to be title-cased and included "Link", which is how the same method
// ended up in finance.payments under two spellings.

export default function CheckoutFolio() {
  const { reservationId } = useParams()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const propertyId = useActivePropertyId()
  const { methods } = usePaymentMethods(propertyId)
  const [showCharge, setShowCharge] = useState(false)
  // Empty until the served list arrives. Naming a default here meant a
  // cashier who never touched the selector posted the literal 'UPI' — the
  // title-cased spelling the ledger stores as a separate method.
  const [method, setMethod] = useState('')
  useEffect(() => {
    if (methods.length > 0) setMethod((m) => m || methods[0].value)
  }, [methods])
  const [payAmount, setPayAmount] = useState<number>(0)
  const [busy, setBusy] = useState(false)
  const [toast, setToast] = useState('')

  const { data, isLoading, isError } = useQuery({
    queryKey: ['folioView', reservationId],
    queryFn: () => getFolioView(reservationId as string),
    enabled: !!reservationId,
  })

  function flash(m: string) { setToast(m); setTimeout(() => setToast(''), 2500) }
  function refresh() { qc.invalidateQueries({ queryKey: ['folioView', reservationId] }) }

  async function collect() {
    if (!data) return
    const amt = payAmount || data.summary.balance_due
    if (amt <= 0) { flash('Nothing due to collect.'); return }
    setBusy(true)
    try {
      await collectFolioPayment({
        organization_id: data.organization_id, property_id: data.property_id,
        // No business_date: the server stamps the day the property is
        // trading. This used to send the browser's date in UTC, which is the
        // calendar's day rather than the one the night audit has reached.
        method,
        allocations: [{ folio_id: data.folio_id, amount: amt }],
      })
      flash('Payment collected.')
      setPayAmount(0)
      refresh()
    } catch (e) {
      flash(errorText(e, 'Payment failed'))
    } finally { setBusy(false) }
  }

  if (isLoading) return <div className="flex items-center gap-2 p-8 text-sm text-slate-400"><Loader2 size={16} className="animate-spin" /> Loading folio…</div>
  if (isError || !data) return <div className="rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700">Failed to load folio.</div>

  return (
    <div className="space-y-5">
      {/* Breadcrumb + header */}
      <div>
        <button onClick={() => navigate('/reservations')} className="mb-2 flex items-center gap-1 text-sm text-brand"><ArrowLeft size={14} /> Reservations</button>
        <h1 className="flex items-center gap-2 text-display text-ink"><Receipt size={26} className="text-brand" /> Checkout &amp; Guest Folio</h1>
      </div>

      {toast && <div className="flex items-center gap-2 rounded-xl bg-emerald-50 px-4 py-3 text-sm text-emerald-700"><CheckCircle2 size={16} />{toast}</div>}

      {/* Guest/folio header card */}
      <div className="flex flex-wrap items-center gap-6 rounded-2xl border border-slate-100 bg-white p-5 shadow-sm">
        <div className="flex items-center gap-3">
          <span className="flex h-12 w-12 items-center justify-center rounded-full bg-brand text-sm font-semibold text-white">{(data.guest_name ?? '?').split(' ').map((w) => w[0]).slice(0, 2).join('').toUpperCase()}</span>
          <div>
            <div className="text-lg font-semibold text-slate-800">{data.guest_name ?? <span className="italic text-slate-400">No guest on file</span>}</div>
            <div className="text-sm text-slate-400">{data.units[0]?.room_type}{data.units[0]?.assigned_room ? ` — Room ${data.units[0].assigned_room}` : ''}</div>
          </div>
        </div>
        <div className="text-sm"><div className="text-xs text-slate-400">Reservation No.</div><div className="font-medium text-slate-700">{data.number}</div></div>
        <div className="text-sm"><div className="text-xs text-slate-400">Status</div><span className="rounded-full bg-amber-50 px-2.5 py-1 text-xs font-medium capitalize text-amber-600">{data.status}</span></div>
        {/* The folio is what has been charged; the schedule is what is owed
            and when. They are two views of the same booking. */}
        <button onClick={() => navigate(`/reservations/${reservationId}/deposits`)} className="ml-auto flex items-center gap-1.5 rounded-lg border border-brand px-3 py-2 text-sm font-medium text-brand hover:bg-brand-light">
          <CalendarClock size={15} /> Deposit Schedule
        </button>
      </div>

      <div className="grid gap-5 xl:grid-cols-[1fr_360px]">
        {/* Folio entries */}
        <div className="rounded-2xl border border-slate-100 bg-white p-5 shadow-sm">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="flex items-center gap-2 text-lg font-semibold text-ink"><Receipt size={18} className="text-brand" /> Guest Folio</h2>
            <div className="flex items-center gap-2">
              {/* Opens the server-rendered folio. Printing from the PDF
                  viewer gives the guest the same document the property files,
                  rather than whatever this screen happens to look like at the
                  width the browser is at. */}
              <button
                onClick={() => { void openFolioPdf(data.property_id, data.folio_id) }}
                className="flex items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-1.5 text-sm font-medium text-slate-600 hover:bg-slate-50">
                <Printer size={15} /> Print Folio
              </button>
              <button onClick={() => navigate(`/payments/folios/${data.folio_id}/adjust`)}
                className="flex items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-1.5 text-sm font-medium text-slate-600 hover:bg-slate-50">
                <FileText size={15} /> Adjust a Charge
              </button>
              <button onClick={() => setShowCharge(true)} className="flex items-center gap-1.5 rounded-lg border border-brand px-3 py-1.5 text-sm font-medium text-brand hover:bg-brand-light"><Plus size={15} /> Add Charge</button>
            </div>
          </div>
          {data.entries.length === 0 ? <div className="py-8 text-center text-sm text-slate-400">No charges yet.</div> : (
            <table className="w-full text-sm">
              <thead><tr className="border-b border-slate-100 text-left text-sm text-slate-600"><th className="py-2 font-semibold">Date</th><th className="py-2 font-semibold">Description</th><th className="py-2 font-semibold">Department</th><th className="py-2 text-right font-semibold">Amount</th></tr></thead>
              <tbody className="divide-y divide-slate-50">
                {data.entries.map((e) => (
                  <tr key={e.id} className="text-slate-700">
                    <td className="py-2.5 text-xs text-slate-500">{e.business_date}</td>
                    <td className="py-2.5">{e.description}</td>
                    <td className="py-2.5 text-slate-500">{e.department}</td>
                    <td className={`py-2.5 text-right font-medium ${e.entry_type === 'credit' ? 'text-emerald-600' : 'text-slate-800'}`}>{e.entry_type === 'credit' ? '−' : ''}{money.format(e.amount)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {/* Payment summary */}
        <div className="rounded-2xl border border-slate-100 bg-white p-5 shadow-sm">
          <h2 className="mb-4 text-lg font-semibold text-ink">Payment Summary</h2>
          <div className="space-y-2 text-sm">
            <Row label="Subtotal" value={money.format(data.summary.subtotal)} />
            <Row label="Taxes & Charges" value={taxMoney.format(data.summary.taxes)} />
            {/* One line per code, at the rate the charge was actually
                billed at — see Administration > Taxes & Charges. */}
            {(data.summary.tax_lines ?? []).map((t) => (
              <div key={`${t.tax_code}-${t.rate_snapshot}`}
                className="flex items-center justify-between px-3 text-xs text-slate-500">
                <span>{t.tax_code} @ {Number(t.rate_snapshot)}</span>
                <span>{taxMoney.format(Number(t.tax_amount))}</span>
              </div>
            ))}
          </div>
          <div className="mt-3 flex items-center justify-between rounded-lg bg-slate-50 px-3 py-2 font-semibold text-slate-800"><span>Grand Total</span><span>{money.format(data.summary.grand_total)}</span></div>
          <div className="mt-2 flex items-center justify-between px-3 text-sm text-emerald-600"><span>Advance Paid</span><span>− {money.format(data.summary.advance_paid)}</span></div>
          <div className="mt-2 flex items-center justify-between rounded-lg bg-amber-50 px-3 py-2 font-semibold text-slate-800"><span>Balance Due</span><span>{money.format(data.summary.balance_due)}</span></div>

          <div className="mt-5">
            <div className="mb-2 text-sm font-medium text-slate-600">Select Payment Method</div>
            <div className="grid grid-cols-2 gap-2">
              {methods.map((m) => (
                <button key={m.value} onClick={() => setMethod(m.value)} className={`flex flex-col items-center gap-1 rounded-xl border px-3 py-3 text-xs font-medium ${method === m.value ? 'border-brand bg-brand-light text-brand' : 'border-slate-200 text-slate-600 hover:bg-slate-50'}`}>{m.icon}{m.label}</button>
              ))}
            </div>
          </div>
          <div className="mt-3">
            <label className="mb-1 block text-xs font-medium text-slate-500">Amount (₹)</label>
            <input type="number" value={payAmount || data.summary.balance_due} onChange={(e) => setPayAmount(Number(e.target.value))} className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm outline-none focus:border-brand" />
          </div>
          <button onClick={collect} disabled={busy} className="mt-4 flex w-full items-center justify-center gap-2 rounded-xl bg-brand px-4 py-3 text-sm font-medium text-white enabled:hover:bg-brand/90 disabled:opacity-50">
            {busy ? <Loader2 size={16} className="animate-spin" /> : <CheckCircle2 size={16} />} Collect Payment
          </button>
        </div>
      </div>

      {showCharge && <ChargeModal fv={data} onClose={() => setShowCharge(false)} onDone={(m) => { flash(m); refresh() }} />}
    </div>
  )
}

function Row({ label, value }: { label: string; value: string }) {
  return <div className="flex items-center justify-between text-slate-600"><span>{label}</span><span className="font-medium text-slate-800">{value}</span></div>
}

function ChargeModal({ fv, onClose, onDone }: { fv: FolioView; onClose: () => void; onDone: (m: string) => void }) {
  const [dept, setDept] = useState('restaurant')
  const [amount, setAmount] = useState<number>(0)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const DEPTS = [['restaurant', 'Restaurant'], ['minibar', 'Minibar'], ['spa', 'Spa'], ['laundry', 'Laundry'], ['transport', 'Transport']]
  async function submit() {
    setErr(''); setBusy(true)
    try {
      await addFolioCharge({
        organization_id: fv.organization_id, property_id: fv.property_id, folio_id: fv.folio_id,
        amount, source_type: dept,
        source_line_key: `manual:${dept}:${crypto.randomUUID()}`,
      })
      onDone('Charge added.'); onClose()
    } catch (e) { const er = e as { response?: { data?: { detail?: string } } }; setErr(errorText(er, 'Failed')) } finally { setBusy(false) }
  }
  const inp = 'w-full rounded-lg border border-slate-200 px-3 py-2.5 text-sm outline-none focus:border-brand'
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4" onClick={onClose}>
      <div className="w-full max-w-sm rounded-2xl bg-white p-6 shadow-xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between"><h2 className="text-lg font-semibold text-ink">Add Charge</h2><button onClick={onClose} className="text-slate-400 hover:text-slate-600"><X size={18} /></button></div>
        {err && <div className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{err}</div>}
        <label className="mt-3 block"><span className="mb-1 block text-sm font-medium text-slate-600">Department</span><Select value={dept} onChange={(e) => setDept(e.target.value)} className={inp}>{DEPTS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</Select></label>
        <label className="mt-3 block"><span className="mb-1 block text-sm font-medium text-slate-600">Amount (₹, incl. tax)</span><input type="number" value={amount} onChange={(e) => setAmount(Number(e.target.value))} className={inp} autoFocus /></label>
        <div className="mt-5 flex justify-end gap-2">
          <button onClick={onClose} className="rounded-lg border border-slate-200 px-4 py-2 text-sm text-slate-600 hover:bg-slate-50">Cancel</button>
          <button onClick={submit} disabled={busy || amount <= 0} className="flex items-center gap-2 rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white enabled:hover:bg-brand/90 disabled:opacity-50">{busy ? <Loader2 size={15} className="animate-spin" /> : <Plus size={15} />} Add</button>
        </div>
      </div>
    </div>
  )
}
