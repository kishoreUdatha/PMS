/**
 * Take money off a folio, without leaving the folio.
 *
 * "Apply discount" used to navigate to the full Folio Adjustment screen. That
 * screen is the right place for the difficult cases — evidence, approval
 * chains, reversing an adjustment somebody made last week — but it is a whole
 * page away for the common one, which is a clerk taking ₹200 off a spa charge
 * while the guest waits. Losing your place on the folio to do that is what
 * made the button feel broken.
 *
 * So the ordinary case happens here and the difficult one is a link.
 *
 * Nothing is invented: it posts through the same ``createAdjustment`` endpoint
 * the full screen uses, with the same kinds and reasons served by the server,
 * so the policy, the approval threshold and the audit trail are identical
 * whichever door you came in by. A second implementation of "take money off"
 * is the last thing a folio needs.
 */
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { AlertTriangle, Loader2, Percent, X } from 'lucide-react'
import Select from './Select'
import { getAdjustmentContext, createAdjustment, postAdjustment } from '../api'
import { errorText } from '../lib/forms'

const field = 'w-full rounded-lg border border-slate-200 px-3 py-2.5 text-sm outline-none focus:border-brand'
const lbl = 'mb-1 block text-xs font-medium text-slate-600'

const money = (v: string | number) => new Intl.NumberFormat('en-IN', {
  style: 'currency', currency: 'INR', minimumFractionDigits: 2,
}).format(Number(v))

export default function ApplyDiscountDialog({
  folioId, propertyId, onClose, onSaved, onRefresh,
}: {
  folioId: string
  propertyId: string
  onClose: () => void
  /** Applied and posted: close and reload. */
  onSaved: () => void
  /** Raised but awaiting approval: reload behind the dialog, keep it open. */
  onRefresh: () => void
}) {
  const ctx = useQuery({
    queryKey: ['adjustment-context', folioId],
    queryFn: () => getAdjustmentContext(folioId, propertyId),
  })
  const d = ctx.data

  const [entryId, setEntryId] = useState('')
  const [kind, setKind] = useState('discount')
  const [amount, setAmount] = useState('')
  const [reason, setReason] = useState('')
  const [remarks, setRemarks] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [note, setNote] = useState('')

  const charges = (d?.charges ?? []).filter((c) => c.adjustable)
  const picked = charges.find((c) => c.entry_id === entryId)
  const value = Number(amount) || 0
  // Taking off more than the line is worth turns a discount into a credit the
  // guest could walk away with. The server refuses it; saying so here saves a
  // round trip and a confusing message.
  const overAmount = !!picked && value > Number(picked.total)
  const valid = value > 0 && reason !== '' && remarks.trim() !== ''
    && !overAmount && (d?.can_create ?? false)

  async function save() {
    setErr(''); setNote(''); setBusy(true)
    try {
      const made = await createAdjustment(folioId, propertyId, {
        folio_entry_id: entryId || null,
        kind, amount: value, adjust_tax: true,
        reason, remarks: remarks.trim(),
      })
      // Posted straight away when policy allows it; otherwise it waits for an
      // approver.
      if (!made.approval_required) {
        try { await postAdjustment(made.id, propertyId) } catch { /* below */ }
        onSaved()
        return
      }
      // Kept open, deliberately. Setting the message and closing in the same
      // breath — which is what this did — means the folio blinks, the totals
      // do not move and nobody is told why: it reads exactly like a button
      // that did nothing. The adjustment is real and waiting for an approver,
      // and the person who raised it has to be told that.
      onRefresh()
      setNote(made.message
        || 'Recorded, and waiting for approval before it comes off the folio.')
    } catch (e) {
      setErr(errorText(e, 'The adjustment could not be made.'))
    } finally { setBusy(false) }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-slate-900/30 p-4"
      onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()}
        className="mt-16 w-full max-w-lg rounded-2xl bg-white p-6 shadow-xl">
        <div className="mb-5 flex items-start justify-between gap-3">
          <h2 className="flex items-center gap-2 text-lg font-semibold text-ink">
            <Percent size={18} className="text-brand" /> Apply discount
          </h2>
          <button onClick={onClose}
            className="rounded-lg p-1 text-slate-400 hover:bg-slate-100">
            <X size={18} />
          </button>
        </div>

        {ctx.isLoading ? (
          <p className="py-8 text-center text-sm text-slate-400">
            <Loader2 className="mx-auto mb-2 h-5 w-5 animate-spin" /> Loading…
          </p>
        ) : !d ? (
          <p className="py-6 text-sm text-slate-500">
            This folio could not be loaded.
          </p>
        ) : !d.adjustable ? (
          <p className="flex items-start gap-2 rounded-lg bg-amber-50 px-3 py-2.5 text-sm text-caution">
            <AlertTriangle size={15} className="mt-0.5 shrink-0" />
            {d.not_adjustable_reason
              ?? 'This folio cannot be adjusted.'}
          </p>
        ) : (
          <>
            <div className="grid gap-4">
              <label className="block">
                <span className={lbl}>Which charge</span>
                <Select className={field} value={entryId}
                  onChange={(e) => setEntryId(e.target.value)}>
                  <option value="">
                    The folio as a whole — no single charge
                  </option>
                  {charges.map((c) => (
                    <option key={c.entry_id} value={c.entry_id}>
                      {c.description} · {c.department} · {money(c.total)}
                    </option>
                  ))}
                </Select>
                {charges.length === 0 && (
                  <span className="mt-1 block text-xs text-slate-500">
                    No individual charge can be adjusted, so this will credit
                    the folio as a whole.
                  </span>
                )}
              </label>

              <div className="grid grid-cols-2 gap-4">
                <label className="block">
                  <span className={lbl}>Type</span>
                  <Select className={field} value={kind}
                    onChange={(e) => setKind(e.target.value)}>
                    {d.kinds.map((k) => (
                      <option key={k.value} value={k.value}>{k.label}</option>
                    ))}
                  </Select>
                </label>
                <label className="block">
                  <span className={lbl}>Amount off (₹)</span>
                  <input type="number" min="0" step="0.01" className={field}
                    value={amount} placeholder="0.00"
                    onChange={(e) => setAmount(e.target.value)} />
                  {picked && (
                    <span className={`mt-1 block text-xs ${
                      overAmount ? 'text-red-600' : 'text-slate-500'}`}>
                      {overAmount
                        ? `More than the charge itself (${money(picked.total)}).`
                        : `Charge is ${money(picked.total)}.`}
                    </span>
                  )}
                </label>
              </div>

              <label className="block">
                <span className={lbl}>Reason</span>
                <Select className={field} value={reason}
                  onChange={(e) => setReason(e.target.value)}>
                  <option value="">Choose a reason…</option>
                  {d.reasons.map((x) => (
                    <option key={x.value} value={x.value}>{x.label}</option>
                  ))}
                </Select>
              </label>

              <label className="block">
                <span className={lbl}>
                  Remarks <span className="text-red-500">— required</span>
                </span>
                {/* Every adjustment lands on the audit trail, and a reason
                    code alone cannot answer "why was this one given". */}
                <input className={field} value={remarks}
                  placeholder="What was agreed, and with whom"
                  onChange={(e) => setRemarks(e.target.value)} />
              </label>
            </div>

            <p className="mt-4 rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-600">
              The original charge is never deleted — a reversing credit is
              posted beside it, and both stay on the folio.{' '}
              <Link to={`/payments/folios/${folioId}/adjust`}
                className="font-semibold text-brand hover:underline">
                Full adjustment screen
              </Link>{' '}
              for evidence, approvals or reversing an earlier one.
            </p>
          </>
        )}

        {note && (
          <p className="mt-3 rounded-lg bg-amber-50 px-3 py-2 text-xs text-caution">
            {note}
          </p>
        )}
        {err && (
          <p className="mt-3 flex items-start gap-2 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">
            <AlertTriangle size={14} className="mt-0.5 shrink-0" />{err}
          </p>
        )}

        <div className="mt-5 flex justify-end gap-2">
          {note ? (
            <button onClick={onClose}
              className="rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white hover:bg-brand-dark">
              Done
            </button>
          ) : (
            <>
              <button onClick={onClose}
                className="rounded-lg border border-slate-200 px-4 py-2 text-sm font-semibold text-slate-600 hover:bg-slate-50">
                Cancel
              </button>
              <button onClick={() => void save()} disabled={!valid || busy}
                className="flex items-center gap-2 rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white enabled:hover:bg-brand-dark disabled:opacity-50">
                {busy && <Loader2 size={15} className="animate-spin" />}
                Take off {value > 0 ? money(value) : 'discount'}
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
