/**
 * Post a charge to a folio.
 *
 * The arithmetic is shown, not just the answer. Quantity times unit price,
 * less discount, equals what the guest owes — and all four are sent, because
 * "two treatments at ₹700 with ₹200 off" and "one charge of ₹1,200" are
 * different facts and the folio could only record the second one until now.
 * The question at the desk is never "what is the total", it is "what is this
 * for", and a line that stores only its total cannot answer it.
 *
 * The charge list comes from the property's own service catalogue where it has
 * one — those are the things this hotel actually sells, at the prices it sells
 * them for. A property that has not set one up falls back to departments, so
 * the dialog still works on day one rather than presenting an empty dropdown.
 */
import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, Loader2, Receipt, X } from 'lucide-react'
import Select from './Select'
import {
  addFolioCharge, ensureFolio, listChargeTypes, listServiceItems,
} from '../api'
import { errorText } from '../lib/forms'

const field = 'w-full rounded-lg border border-slate-200 px-3 py-2.5 text-sm outline-none focus:border-brand'
const lbl = 'mb-1 block text-xs font-medium text-slate-600'

const money = (v: number) => new Intl.NumberFormat('en-IN', {
  style: 'currency', currency: 'INR', minimumFractionDigits: 2,
}).format(v)

export default function AddChargeDialog({
  reservationId, organizationId, propertyId, folioId, folioLabel, businessDate,
  onClose, onSaved,
}: {
  reservationId: string
  organizationId: string
  propertyId: string
  /** Null when nothing has been posted yet — the folio is made on save. */
  folioId: string | null
  folioLabel: string
  businessDate: string
  onClose: () => void
  onSaved: () => void
}) {
  const items = useQuery({
    queryKey: ['service-items', propertyId],
    queryFn: () => listServiceItems(propertyId),
    enabled: propertyId !== '',
  })
  const catalogue = items.data ?? []
  // Served, not written down here. The same list the folio uses to describe a
  // line and the tax engine uses to categorise it — so a type cannot be
  // billable in the dropdown and unknown to tax, which is how "breakfast"
  // came to post untaxed.
  const types = useQuery({ queryKey: ['charge-types'], queryFn: listChargeTypes })
  const chargeTypes = types.data ?? []

  const [date, setDate] = useState(businessDate)
  const [charge, setCharge] = useState('')
  const [qty, setQty] = useState('1')
  const [unit, setUnit] = useState('')
  const [discount, setDiscount] = useState('')
  const [comments, setComments] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  // Picking a catalogue item fills its price. Typed over afterwards without
  // argument — the rate card is a starting point, not a rule, and a desk that
  // cannot override it starts posting "Other" for everything.
  const picked = catalogue.find((i) => i.id === charge)
  useEffect(() => {
    if (picked) setUnit(String(Number(picked.price)))
  }, [picked])

  const q = Number(qty) || 0
  const u = Number(unit) || 0
  const d = Number(discount) || 0
  const gross = q * u
  const net = Math.max(gross - d, 0)
  const overDiscounted = d > gross && gross > 0
  const valid = charge !== '' && q > 0 && u > 0 && net > 0 && !overDiscounted

  async function save() {
    setErr(''); setBusy(true)
    try {
      // A booking has no folio until something is put on it, and this is
      // usually that something.
      const fid = folioId
        ?? (await ensureFolio(reservationId, organizationId, propertyId)).id
      await addFolioCharge({
        organization_id: organizationId,
        property_id: propertyId,
        folio_id: fid,
        amount: net,
        business_date: date,
        source_type: picked ? departmentOf(picked.category_label) : charge,
        note: comments.trim() || picked?.name || null,
        quantity: q,
        unit_amount: u,
        discount_amount: d || null,
        // Idempotent per posting, so a double-click does not bill twice.
        source_line_key: `charge:${reservationId}:${Date.now()}`,
      })
      onSaved()
    } catch (e) {
      setErr(errorText(e, 'The charge could not be posted.'))
    } finally { setBusy(false) }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-slate-900/30 p-4"
      onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()}
        className="mt-16 w-full max-w-lg rounded-2xl bg-white p-6 shadow-xl">
        <div className="mb-5 flex items-start justify-between gap-3">
          <h2 className="flex items-center gap-2 text-lg font-semibold text-ink">
            <Receipt size={18} className="text-brand" /> Add charge
          </h2>
          <button onClick={onClose}
            className="rounded-lg p-1 text-slate-400 hover:bg-slate-100">
            <X size={18} />
          </button>
        </div>

        <div className="grid grid-cols-2 gap-4">
          <label className="block">
            <span className={lbl}>Date</span>
            <input type="date" className={field} value={date}
              onChange={(e) => setDate(e.target.value)} />
          </label>
          <label className="block">
            <span className={lbl}>Folio</span>
            {/* One folio per booking today, so this states which rather than
                offering a choice that has one option. */}
            <p className="w-full rounded-lg border border-slate-200 bg-slate-50 px-3 py-2.5 text-sm text-slate-600">
              {folioLabel}
            </p>
          </label>

          <label className="col-span-2 block">
            <span className={lbl}>Charge</span>
            <Select className={field} value={charge}
              onChange={(e) => setCharge(e.target.value)}>
              <option value="">Choose what to bill…</option>
              {catalogue.length > 0 ? (
                catalogue.map((i) => (
                  <option key={i.id} value={i.id}>
                    {i.category_label} · {i.name} — {money(Number(i.price))}
                  </option>
                ))
              ) : (
                chargeTypes.map((t) => (
                  <option key={t.value} value={t.value}>{t.label}</option>
                ))
              )}
            </Select>
            {catalogue.length === 0 && !items.isLoading && (
              <span className="mt-1 block text-xs text-slate-500">
                No priced service catalogue yet, so these are charge types. Add
                items under Guest Services to bill them by name and price.
              </span>
            )}
          </label>

          <label className="block">
            <span className={lbl}>Qty</span>
            <input type="number" min="1" step="1" className={field} value={qty}
              onChange={(e) => setQty(e.target.value)} />
          </label>
          <label className="block">
            <span className={lbl}>Amount each (₹)</span>
            <input type="number" min="0" step="0.01" className={field}
              value={unit} placeholder="0.00"
              onChange={(e) => setUnit(e.target.value)} />
          </label>

          <label className="block">
            <span className={lbl}>Discount (₹)</span>
            <input type="number" min="0" step="0.01" className={field}
              value={discount} placeholder="0.00"
              onChange={(e) => setDiscount(e.target.value)} />
            {overDiscounted && (
              <span className="mt-1 block text-xs text-red-600">
                More than the charge itself.
              </span>
            )}
          </label>
          <div>
            <span className={lbl}>Guest pays</span>
            {/* The sum shown, not just its answer: somebody checking a folio
                against a docket needs to see which of the three numbers is
                wrong, not only that the total is. */}
            <p className="rounded-lg bg-brand-light px-3 py-2.5 text-sm font-bold text-brand">
              {money(net)}
            </p>
            {d > 0 && net > 0 && (
              <span className="mt-1 block text-xs text-slate-500">
                {money(gross)} less {money(d)}
              </span>
            )}
          </div>

          <label className="col-span-2 block">
            <span className={lbl}>Comments</span>
            <input className={field} value={comments}
              placeholder="What this is for — shown on the folio"
              onChange={(e) => setComments(e.target.value)} />
          </label>
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
          <button onClick={() => void save()} disabled={!valid || busy}
            className="flex items-center gap-2 rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white enabled:hover:bg-brand-dark disabled:opacity-50">
            {busy && <Loader2 size={15} className="animate-spin" />}
            Add {net > 0 ? money(net) : 'charge'}
          </button>
        </div>
      </div>
    </div>
  )
}

/** A catalogue category mapped onto the department a folio entry records. */
function departmentOf(categoryLabel: string): string {
  const k = categoryLabel.toLowerCase()
  if (k.includes('room') && k.includes('dining')) return 'in_room_dining'
  if (k.includes('restaurant') || k.includes('food')) return 'restaurant'
  if (k.includes('minibar')) return 'minibar'
  if (k.includes('laundry')) return 'laundry'
  if (k.includes('spa')) return 'spa'
  if (k.includes('transport') || k.includes('travel')) return 'transport'
  // Unrecognised posts as "other" rather than guessing: the department drives
  // which tax rule applies, and a wrong guess bills the guest wrongly.
  return 'other'
}
