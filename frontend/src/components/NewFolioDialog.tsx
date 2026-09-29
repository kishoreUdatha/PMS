/**
 * Open another folio on a booking, and say how it should bill.
 *
 * A booking carries more than one folio whenever more than one party is
 * paying: the company settling the room and the guest settling the bar, a
 * sharer paying their own extras, a group master against individual accounts.
 * Opening the second one was possible already — Split Folio does it on the way
 * to moving lines across — but only as a side effect, and with nothing said
 * about who it bills or how it prints.
 *
 * Everything asked for here is something that has to be decided before the
 * bill is produced, not after. A GSTIN added once the invoice is numbered means
 * cancelling the invoice and issuing another against the same stay, which is
 * exactly the thing a fiscal series is not supposed to have to do.
 */
import { useState } from 'react'
import { AlertTriangle, FilePlus2, Loader2, X } from 'lucide-react'
import Select from './Select'
import { GST_STATE_CODES, gstinProblem, normaliseGstin } from '../lib/gstin'
import { openFolio, type InvoiceTiming, type ReservationFull } from '../api'
import { errorText } from '../lib/forms'

const field = 'w-full rounded-lg border border-slate-200 px-3 py-2.5 text-sm outline-none focus:border-brand'
const lbl = 'mb-1 block text-xs font-medium text-slate-600'

/** The types a desk opens by hand. 'guest' is the booking's own and exists
 *  already, so offering it again would make a second one of the same thing. */
const TYPES: { value: string; label: string; hint: string }[] = [
  { value: 'company', label: 'Company',
    hint: 'An employer or agent settling part of the stay' },
  { value: 'guest', label: 'Sharer',
    hint: 'Another occupant paying their own extras' },
  { value: 'paymaster', label: 'Paymaster',
    hint: 'A holding account for charges to be settled later' },
]

export default function NewFolioDialog({ r, onClose, onOpened }: {
  r: ReservationFull
  onClose: () => void
  onOpened: () => void
}) {
  const [type, setType] = useState('company')
  const [sharer, setSharer] = useState('')
  const [gstin, setGstin] = useState('')
  const [showTax, setShowTax] = useState(true)
  const [timing, setTiming] = useState<InvoiceTiming>('on_checkout')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  // Answered while it is being typed, in the same words the server refuses
  // with — `lib/gstin.ts` and `chirala_common/gstin.py` hold the same rule
  // deliberately, so the field never promises something the save then rejects.
  const gstinIssue = gstinProblem(gstin)
  const normalised = normaliseGstin(gstin)
  const registeredIn = normalised && normalised.length >= 2
    ? GST_STATE_CODES[normalised.slice(0, 2)] : undefined
  const valid = !gstinIssue && !busy

  async function save() {
    setErr(''); setBusy(true)
    try {
      await openFolio(r.property_id, {
        reservation_id: r.id,
        type,
        sharer_name: sharer.trim() || null,
        gstin: normalised,
        show_tax_on_folio: showTax,
        invoice_number_timing: timing,
      })
      onOpened()
    } catch (e) {
      setErr(errorText(e, 'The folio could not be opened.'))
    } finally { setBusy(false) }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-slate-900/30 p-4"
      onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()}
        className="mt-12 w-full max-w-lg rounded-2xl bg-white p-6 shadow-xl">
        <div className="mb-5 flex items-start justify-between gap-3">
          <h2 className="flex items-center gap-2 text-lg font-semibold text-ink">
            <FilePlus2 size={18} className="text-brand" /> New folio
          </h2>
          <button onClick={onClose} aria-label="Close"
            className="rounded-lg p-1 text-slate-400 hover:bg-slate-100">
            <X size={18} />
          </button>
        </div>

        <div className="grid grid-cols-2 gap-4">
          <label className="block">
            <span className={lbl}>Folio for</span>
            <Select className={field} value={type}
              onChange={(e) => setType(e.target.value)}>
              {TYPES.map((t) => (
                <option key={t.value + t.label} value={t.value}>{t.label}</option>
              ))}
            </Select>
            <span className="mt-1 block text-xs text-slate-500">
              {TYPES.find((t) => t.value === type)?.hint}
            </span>
          </label>

          <label className="block">
            <span className={lbl}>Sharer</span>
            <input className={field} value={sharer}
              placeholder="Name on this bill"
              onChange={(e) => setSharer(e.target.value)} />
            <span className="mt-1 block text-xs text-slate-500">
              Leave empty to bill {r.guest?.full_name ?? 'the booking'}.
            </span>
          </label>

          <label className="col-span-2 block">
            <span className={lbl}>GSTIN</span>
            <input className={`${field} font-mono uppercase tracking-wide ${
              gstinIssue ? 'border-red-300' : ''}`}
              value={gstin} maxLength={15} placeholder="37ABCDE1234F1ZZ"
              onChange={(e) => setGstin(e.target.value.toUpperCase())} />
            {gstinIssue ? (
              <span className="mt-1 block text-xs text-red-600">{gstinIssue}</span>
            ) : (
              <span className="mt-1 block text-xs text-slate-500">
                {registeredIn
                  ? `Registered in ${registeredIn}.`
                  : 'The payer’s GST number, where they are reclaiming the '
                    + 'tax. Leave empty for a guest who is not.'}
              </span>
            )}
          </label>
        </div>

        <div className="mt-4 space-y-2.5 rounded-xl border border-slate-200 p-3.5">
          {/* Both of these decide what the printed document is, so they sit
              together and away from the fields above, which decide who it is
              for. */}
          <label className="flex cursor-pointer items-start gap-2.5">
            <input type="checkbox" className="mt-0.5 accent-brand"
              checked={showTax}
              onChange={(e) => setShowTax(e.target.checked)} />
            <span className="min-w-0">
              <span className="block text-sm font-medium text-slate-700">
                Show tax on printed folio
              </span>
              <span className="block text-xs text-slate-500">
                Itemises each tax rather than printing tax-inclusive totals.
                A company reclaiming GST needs it broken out.
              </span>
            </span>
          </label>

          <div className="border-t border-slate-100 pt-2.5">
            <span className={lbl}>Generate invoice number</span>
            <Select className={field} value={timing}
              onChange={(e) => setTiming(e.target.value as InvoiceTiming)}>
              <option value="on_checkout">On checkout</option>
              <option value="post_checkout">Post checkout</option>
            </Select>
            <span className="mt-1 block text-xs text-slate-500">
              {timing === 'on_checkout'
                ? 'Numbered as the guest leaves — right for a bill that is '
                  + 'settled at the desk.'
                : 'Numbered later, once the bill stops moving — for a folio '
                  + 'still waiting on a PO or a group reconciliation. A number '
                  + 'issued against a bill that then changes has to be '
                  + 'cancelled, and a fiscal series with holes in it gets '
                  + 'asked about.'}
            </span>
          </div>
        </div>

        {err && (
          <p className="mt-4 flex items-start gap-2 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">
            <AlertTriangle size={14} className="mt-0.5 shrink-0" />{err}
          </p>
        )}

        <div className="mt-5 flex justify-end gap-2">
          <button onClick={onClose}
            className="rounded-lg border border-slate-200 px-4 py-2 text-sm font-semibold text-slate-600 hover:bg-slate-50">
            Cancel
          </button>
          <button onClick={() => void save()} disabled={!valid}
            className="flex items-center gap-2 rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white enabled:hover:bg-brand-dark disabled:opacity-50">
            {busy && <Loader2 size={15} className="animate-spin" />}
            Open folio
          </button>
        </div>
      </div>
    </div>
  )
}
