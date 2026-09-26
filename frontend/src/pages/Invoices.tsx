import { useEffect, useState } from 'react'
import { fmtDate } from '../lib/dates'
import Select from '../components/Select'
import { Link, useNavigate } from 'react-router-dom'
import { useQuery, keepPreviousData } from '@tanstack/react-query'
import {
  FileText, Search, Loader2, Settings2, FilePlus2, AlertTriangle, X,
} from 'lucide-react'
import {
  listInvoices, createInvoice, getOpenFolios, type OpenFolio,
} from '../api'
import { useActivePropertyId } from '../hooks/useProperty'
import { errorText } from '../lib/forms'

const plain = (v: string | number) =>
  new Intl.NumberFormat('en-IN', { minimumFractionDigits: 2 }).format(Number(v))
const stamp = (d: string | null) => fmtDate(d)

const TABS = [
  { key: '', label: 'All' },
  { key: 'draft', label: 'Drafts' },
  { key: 'issued', label: 'Issued' },
  { key: 'cancelled', label: 'Cancelled' },
]
const TONE: Record<string, string> = {
  draft: 'bg-amber-50 text-amber-700',
  issued: 'bg-emerald-50 text-emerald-700',
  cancelled: 'bg-slate-75 text-slate-500',
}

export default function Invoices() {
  const propertyId = useActivePropertyId()
  const nav = useNavigate()
  const [status, setStatus] = useState('')
  const [search, setSearch] = useState('')
  const [q, setQ] = useState('')
  const [newOpen, setNewOpen] = useState(false)

  useEffect(() => {
    const t = setTimeout(() => setQ(search), 300)
    return () => clearTimeout(t)
  }, [search])

  const { data, isLoading, isFetching, error } = useQuery({
    queryKey: ['invoices', propertyId, status, q],
    queryFn: () => listInvoices(propertyId, { status: status || undefined, q: q || undefined }),
    enabled: propertyId !== '',
    placeholderData: keepPreviousData,
  })
  const rows = data?.rows ?? []

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <h1 className="flex items-center gap-2 text-display text-ink">
          <FileText size={26} className="text-brand" /> Invoices &amp; Credit Notes
        </h1>
        <div className="flex flex-wrap items-center gap-2">
          <Link to="/finance/invoice-settings"
            className="flex items-center gap-1.5 whitespace-nowrap rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-600 hover:bg-slate-50">
            <Settings2 size={15} /> Invoice Settings
          </Link>
          {data?.can_create !== false && (
            <button onClick={() => setNewOpen(true)}
              className="flex items-center gap-1.5 whitespace-nowrap rounded-lg bg-brand px-3 py-2 text-sm font-medium text-white hover:bg-brand-dark disabled:opacity-40">
              <FilePlus2 size={15} /> New Invoice
            </button>
          )}
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <span className="inline-flex shrink-0 items-center gap-0.5 rounded-lg border border-slate-200 bg-white p-0.5">
          {TABS.map((t) => (
            <button key={t.key} onClick={() => setStatus(t.key)} aria-pressed={status === t.key}
              className={`whitespace-nowrap rounded-md px-3 py-1.5 text-sm ${status === t.key
                ? 'bg-brand font-semibold text-white'
                : 'font-medium text-slate-500 hover:bg-slate-50 hover:text-slate-700'}`}>
              {t.label}
            </button>
          ))}
        </span>
        <div className="flex min-w-[240px] flex-1 items-center justify-end gap-2">
          <div className="relative min-w-0 max-w-sm flex-1">
            <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <input value={search} onChange={(e) => setSearch(e.target.value)}
              placeholder="Search guest, booking or invoice number…"
              aria-label="Search invoices"
              className="w-full rounded-lg border border-slate-200 bg-slate-50 py-2 pl-9 pr-3 text-sm outline-none focus:border-brand" />
          </div>
        </div>
      </div>

      <div className="overflow-hidden rounded-2xl border border-slate-100 bg-white">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="border-b border-slate-100 bg-slate-50/60 text-left text-slate-600">
              <tr>
                <th className="px-5 py-3 font-semibold">Invoice</th>
                <th className="px-4 py-3 font-semibold">Guest</th>
                <th className="px-4 py-3 font-semibold">Booking</th>
                <th className="px-4 py-3 font-semibold">Date</th>
                <th className="px-4 py-3 text-right font-semibold">Total</th>
                <th className="px-4 py-3 text-right font-semibold">Credited</th>
                <th className="px-4 py-3 text-right font-semibold">Balance</th>
                <th className="px-5 py-3 font-semibold">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-50">
              {isLoading && (
                <tr><td colSpan={8} className="p-10 text-center text-slate-400">
                  <Loader2 size={18} className="mx-auto animate-spin" />
                </td></tr>
              )}
              {!isLoading && error && (
                <tr><td colSpan={8}
                  className="p-10 text-center text-sm text-red-600">
                  <AlertTriangle size={16} className="mx-auto mb-2" />
                  Could not load invoices.
                  {' '}{errorText(error, (error as Error).message)}
                </td></tr>
              )}
              {!isLoading && !error && rows.length === 0 && (
                <tr><td colSpan={8} className="p-10 text-center text-sm text-slate-400">
                  No invoice yet. Start one from an open folio.
                </td></tr>
              )}
              {rows.map((r) => (
                <tr key={r.id} onClick={() => nav(`/finance/invoices/${r.id}`)}
                  className="cursor-pointer text-slate-700 hover:bg-slate-50/60">
                  <td className="px-5 py-3 font-semibold text-slate-800">
                    {r.display_number}
                  </td>
                  <td className="px-4 py-3">{r.guest_name ?? '—'}</td>
                  <td className="px-4 py-3 text-slate-500">
                    {r.reservation_number ?? '—'}
                  </td>
                  <td className="px-4 py-3 text-slate-500">
                    {stamp(r.issued_at ?? r.created_at)}
                  </td>
                  <td className="px-4 py-3 text-right font-medium">{plain(r.total)}</td>
                  <td className="px-4 py-3 text-right text-slate-500">
                    {Number(r.credited) > 0 ? plain(r.credited) : '—'}
                  </td>
                  <td className="px-4 py-3 text-right font-medium">
                    {plain(r.balance_due)}
                  </td>
                  <td className="px-5 py-3">
                    <span className={`rounded-full px-2.5 py-1 text-xs font-semibold ${TONE[r.status]}`}>
                      {r.status}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="border-t border-slate-100 px-5 py-3 text-sm text-slate-500">
          {data?.total ?? 0} invoice{(data?.total ?? 0) === 1 ? '' : 's'}
          {isFetching && !isLoading && (
            <Loader2 size={13} className="ml-2 inline animate-spin text-slate-300" />
          )}
        </p>
      </div>

      {newOpen && <NewInvoice propertyId={propertyId} onClose={() => setNewOpen(false)} />}
    </div>
  )
}

function NewInvoice({ propertyId, onClose }: {
  propertyId: string; onClose: () => void
}) {
  const nav = useNavigate()
  const [folioId, setFolioId] = useState('')
  const [name, setName] = useState('')
  const [gstin, setGstin] = useState('')
  const [address, setAddress] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  const { data: folios, isLoading } = useQuery({
    queryKey: ['open-folios', propertyId],
    queryFn: () => getOpenFolios(propertyId),
    enabled: propertyId !== '',
  })

  async function submit() {
    setErr(''); setBusy(true)
    try {
      const inv = await createInvoice({
        property_id: propertyId, folio_id: folioId,
        customer_name: name.trim() || undefined,
        customer_gstin: gstin.trim() || undefined,
        customer_address: address.trim() || undefined,
      })
      nav(`/finance/invoices/${inv.id}`)
    } catch (e) {
      setErr(errorText(e, 'Could not start the invoice.'))
    } finally { setBusy(false) }
  }

  const inputCls = 'w-full rounded-lg border border-slate-200 px-3 py-2.5 text-sm outline-none focus:border-brand'
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4"
      onClick={onClose}>
      <div className="w-full max-w-md rounded-2xl bg-white p-6 shadow-xl"
        onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold text-ink">New Invoice</h2>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600">
            <X size={18} />
          </button>
        </div>
        {err && (
          <p className="mt-3 flex items-start gap-2 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">
            <AlertTriangle size={15} className="mt-0.5 shrink-0" /> {err}
          </p>
        )}
        <p className="mt-3 text-sm text-slate-500">
          The draft picks up every charge on the folio that has not been
          invoiced already. Payments are shown against it, not billed on it.
        </p>
        <label className="mt-3 block">
          <span className="mb-1 block text-sm font-medium text-slate-600">Folio</span>
          <Select value={folioId} onChange={(e) => setFolioId(e.target.value)}
            className={inputCls} disabled={isLoading}>
            <option value="">
              {isLoading ? 'Loading folios…' : 'Select a folio'}
            </option>
            {(folios ?? []).map((f: OpenFolio) => (
              <option key={f.folio_id} value={f.folio_id}>
                {f.guest_name ?? 'Unnamed'} — {f.reservation_number ?? f.folio_id.slice(0, 8)}
                {' '}({plain(f.balance)})
              </option>
            ))}
          </Select>
        </label>
        <p className="mt-4 text-xs font-semibold uppercase tracking-wide text-slate-400">
          Bill to (optional)
        </p>
        <p className="mb-2 text-xs text-slate-400">
          Leave blank to bill the guest as recorded. Fill these in for a company
          booking — there is no GSTIN field on a guest record, so it is captured
          per invoice.
        </p>
        <input value={name} onChange={(e) => setName(e.target.value)}
          placeholder="Company or customer name" className={inputCls} />
        <input value={gstin} onChange={(e) => setGstin(e.target.value)}
          placeholder="Customer GSTIN" className={`${inputCls} mt-2`} />
        <textarea value={address} onChange={(e) => setAddress(e.target.value)}
          placeholder="Billing address" rows={2}
          className={`${inputCls} mt-2 resize-none`} />
        <div className="mt-5 flex justify-end gap-2">
          <button onClick={onClose}
            className="rounded-lg border border-slate-200 px-4 py-2 text-sm text-slate-600 hover:bg-slate-50">
            Cancel
          </button>
          <button onClick={submit} disabled={!folioId || busy}
            className="flex items-center gap-2 rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white enabled:hover:bg-brand/90 disabled:opacity-50">
            {busy && <Loader2 size={15} className="animate-spin" />} Create Draft
          </button>
        </div>
      </div>
    </div>
  )
}
