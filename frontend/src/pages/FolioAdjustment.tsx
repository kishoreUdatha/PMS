import { useRef, useState } from 'react'
import { fmtDate, fmtDateTime, fmtTime } from '../lib/dates'
import Select from '../components/Select'
import { downloadCsv, datedName } from '../lib/csv'
import { useParams, Link } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useFlash } from '../hooks/useFlash'
import {
  AlertTriangle, ArrowLeft, CheckCircle2, Clock, FileText, Info, Loader2,
  Paperclip, Receipt, ShieldCheck, Undo2, Upload, User, X, Download,} from 'lucide-react'
import {
  getAdjustmentContext, createAdjustment, postAdjustment, reverseAdjustment,
  uploadAdjustmentEvidence, 
  type AdjContext, type AdjCharge, type AdjRow,
} from '../api'
import { useActivePropertyId } from '../hooks/useProperty'
import { errorText } from '../lib/forms'

/**
 * Screen 114 — Guest Folio Adjustment.
 *
 * A folio entry is never edited and never deleted. Taking a wrong charge off a
 * bill means posting a reversing credit beside the original, so the bill keeps
 * showing what was charged, what came off, and why.
 *
 * Nothing on this screen moves money until policy allows it. An adjustment is
 * raised, routed against the property's own approval policy, and posted only
 * once it is cleared — under the threshold the policy clears it immediately and
 * says so, over it the request waits in the queue on screen 042 and the folio
 * balance does not move a rupee in the meantime.
 */

const exact = new Intl.NumberFormat('en-IN', {
  style: 'currency', currency: 'INR', minimumFractionDigits: 2,
})
const money = new Intl.NumberFormat('en-IN', {
  style: 'currency', currency: 'INR', maximumFractionDigits: 0,
})
const day = (s?: string | null) => {
  if (!s) return '—'
  const d = new Date(`${s.slice(0, 10)}T00:00:00`)
  return fmtDate(d)
}
const stamp = (iso?: string | null) =>
  iso ? `${day(iso)}, ${fmtTime(iso)}` : '—'

const STATUS_TINT: Record<string, string> = {
  pending_approval: 'bg-amber-100 text-amber-800',
  approved: 'bg-sky-100 text-sky-700',
  posted: 'bg-emerald-100 text-emerald-700',
  rejected: 'bg-red-100 text-red-700',
  reversed: 'bg-slate-200 text-slate-600',
}
const STATUS_LABEL: Record<string, string> = {
  pending_approval: 'Pending approval', approved: 'Ready to post',
  posted: 'Posted', rejected: 'Rejected', reversed: 'Reversed',
}

const input = 'w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700 outline-none focus:border-brand'
const label = 'mb-1 block text-xs font-medium text-slate-500'


/* -------------------------------------------------------------- the page --- */
export default function FolioAdjustment() {
  const { folioId = '' } = useParams()
  const propertyId = useActivePropertyId()
  const qc = useQueryClient()
  const fileRef = useRef<HTMLInputElement>(null)

  const [entryId, setEntryId] = useState('')
  const [kind, setKind] = useState('correction')
  const [amount, setAmount] = useState('')
  const [adjustTax, setAdjustTax] = useState(true)
  const [reason, setReason] = useState('')
  const [remarks, setRemarks] = useState('')
  const [pendingFile, setPendingFile] = useState<File | null>(null)
  const [toast, setToast] = useFlash()
  const [error, setError] = useState('')
  const [reversing, setReversing] = useState<AdjRow | null>(null)

  const q = useQuery({
    queryKey: ['adj-context', folioId, propertyId],
    queryFn: () => getAdjustmentContext(folioId, propertyId),
    enabled: propertyId !== '' && folioId !== '',
  })

  const refresh = () => qc.invalidateQueries({ queryKey: ['adj-context'] })
  const fail = (e: unknown) => {
    setError(errorText(e, 'That did not work. Please try again.'))
    setToast('')
  }
  const reset = () => {
    setEntryId(''); setAmount(''); setReason(''); setRemarks('')
    setAdjustTax(true); setKind('correction'); setPendingFile(null)
    if (fileRef.current) fileRef.current.value = ''
  }

  const create = useMutation({
    mutationFn: async () => {
      const made = await createAdjustment(folioId, propertyId, {
        folio_entry_id: entryId || null, kind, amount: Number(amount),
        adjust_tax: adjustTax, reason, remarks: remarks.trim(),
      })
      // The evidence has to wait for the row it hangs off. If attaching it
      // fails the adjustment still exists, and saying otherwise invites a
      // retry that raises a second one — which is exactly what happened.
      let attachFailed = ''
      if (pendingFile) {
        try {
          await uploadAdjustmentEvidence(made.id, propertyId, pendingFile)
        } catch (e) {
          attachFailed = errorText(e, 'the file could not be attached')
        }
      }
      return { ...made, attachFailed }
    },
    onSuccess: (r) => {
      reset(); refresh()
      if (r.attachFailed) {
        setToast('')
        setError(`${r.message} The adjustment was raised — but `
          + `${r.attachFailed}. Attach the evidence again from the list below.`)
      } else {
        setError(''); setToast(r.message)
      }
    },
    onError: fail,
  })
  const post = useMutation({
    mutationFn: (id: string) => postAdjustment(id, propertyId),
    onSuccess: (r) => { setError(''); setToast(r.message); refresh() },
    onError: fail,
  })
  const reverse = useMutation({
    mutationFn: (v: { id: string; remarks: string }) =>
      reverseAdjustment(v.id, propertyId, v.remarks),
    onSuccess: (r) => { setReversing(null); setError(''); setToast(r.message); refresh() },
    onError: fail,
  })

  if (propertyId === '') {
    return <p className="text-sm text-slate-500">Pick a property first.</p>
  }
  if (q.isLoading) {
    return (
      <p className="flex items-center gap-2 text-sm text-slate-500">
        <Loader2 className="h-4 w-4 animate-spin" /> Loading the folio…
      </p>
    )
  }
  if (q.isError || !q.data) {
    const er = q.error as { response?: { data?: { detail?: string } } }
    return (
      <p className="flex items-start gap-2 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
        {errorText(er, 'This folio could not be loaded.')}
      </p>
    )
  }

  const c: AdjContext = q.data
  const picked = c.charges.find((x) => x.entry_id === entryId)
  const over = c.policy.threshold !== null
    && Number(amount) > Number(c.policy.threshold)

  // The tax that comes off with the charge, worked out the same way the API
  // does it, so the figure on screen is the figure that gets posted.
  const taxShare = picked && adjustTax && Number(picked.total) > 0
    ? Number(picked.tax_amount) * Number(amount || 0) / Number(picked.total)
    : 0
  const totalOff = Number(amount || 0) + taxShare

  const missing: string[] = []
  if (!c.adjustable) missing.push('an adjustable folio')
  if (Number(amount) <= 0) missing.push('an amount')
  if (reason === '') missing.push('a reason')
  if (remarks.trim().length < 5) missing.push('remarks')
  if (picked && Number(amount) > Number(picked.total)) missing.push('an amount within the charge')

  return (
    <div className="space-y-4">
      {/* ------------------------------------------------------- header --- */}
      <div>
        <Link to="/payments"
          className="mb-2 inline-flex items-center gap-1.5 text-sm text-slate-500 hover:text-brand">
          <ArrowLeft size={14} /> Cashiering Centre
        </Link>
        <h1 className="text-display text-ink">Guest Folio Adjustment</h1>
        <p className="mt-1 text-sm text-slate-500">
          Correct a charge on a guest folio. Nothing is taken off until policy
          allows it.
        </p>
      </div>

      {/* The form is a rail down the right, starting level with the context
          cards rather than below the charge table. It is the thing this screen
          is for, and it used to begin a screen-height further down -- on a
          folio with a dozen charges you scrolled past all of them to reach the
          first field. The rules that stood in this corner are now an ⓘ on the
          form's own heading, where they are read at the moment they apply. */}
      <div className="grid grid-cols-1 gap-3 xl:grid-cols-[minmax(0,1fr)_24rem]">
      <div className="min-w-0 space-y-3">
      {/* ------------------------------------------------------ context --- */}
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        <div className="rounded-2xl border border-slate-100 bg-white p-4">
          <h2 className="mb-3 flex items-center gap-2 font-semibold text-ink">
            <User size={15} className="text-slate-400" /> Guest & Reservation
          </h2>
          <p className="text-lg font-semibold text-slate-800">
            {c.guest.guest_name ?? 'No guest on this folio'}
          </p>
          <p className="text-sm text-slate-500">{c.guest.reservation_number ?? '—'}</p>
          <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
            <Fact k="Check-in" v={day(c.guest.arrival_date)} />
            <Fact k="Check-out" v={day(c.guest.departure_date)} />
            <Fact k="Room type" v={c.guest.room_type ?? '—'} />
            <Fact k="Room" v={c.guest.room_code ?? 'Not assigned'} />
            <Fact k="Occupancy" v={c.guest.adults === null ? '—'
              : `${c.guest.adults} adult${c.guest.adults === 1 ? '' : 's'}`
                + (c.guest.children ? ` / ${c.guest.children} child` : '')} />
            <Fact k="Booking status" v={(c.guest.reservation_status ?? '—')
              .replace('_', ' ')} />
          </dl>
        </div>

        <div className="rounded-2xl border border-slate-100 bg-white p-4">
          <h2 className="mb-3 flex items-center gap-2 font-semibold text-ink">
            <Receipt size={15} className="text-slate-400" /> Folio Summary
          </h2>
          <dl className="space-y-2 text-sm">
            <Line k="Folio No." v={c.folio.folio_no} />
            <Line k="Folio type" v={c.folio.folio_type} />
            <Line k="Status" v={<span className={`rounded-full px-2 py-0.5 text-xs font-semibold ${
              c.folio.status === 'open' ? 'bg-emerald-100 text-emerald-700'
                : 'bg-slate-200 text-slate-600'}`}>
              {c.folio.status === 'open' ? 'Open' : c.folio.status}
            </span>} />
            <Line k="Total charges" v={exact.format(Number(c.folio.total_charges))} />
            <Line k="Total payments" v={exact.format(Number(c.folio.total_payments))} />
            {Number(c.folio.total_adjustments) !== 0 && (
              <Line k="Adjustments" v={`− ${exact.format(Number(c.folio.total_adjustments))}`} />
            )}
            <div className="flex items-baseline justify-between border-t border-slate-100 pt-2">
              <dt className="font-semibold text-slate-700">Balance</dt>
              <dd className="text-lg font-semibold text-slate-800">
                {exact.format(Number(c.folio.balance))}
              </dd>
            </div>
          </dl>
        </div>

      </div>

      {!c.adjustable && (
        <p className="flex items-start gap-2 rounded-xl bg-amber-50 px-4 py-3 text-sm text-caution">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          {c.not_adjustable_reason}
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

      {/* --------------------------------------------------- the charges --- */}
        <div className="min-w-0 overflow-hidden rounded-2xl border border-slate-100 bg-white">
          <h2 className="border-b border-slate-100 px-4 py-3 font-semibold text-ink">
            Posted Charges
          </h2>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="px-3 py-2.5" />
                  <th className="px-3 py-2.5 font-semibold">Date &amp; time</th>
                  <th className="px-3 py-2.5 font-semibold">Description</th>
                  <th className="px-3 py-2.5 font-semibold">Department</th>
                  <th className="px-3 py-2.5 text-right font-semibold">Amount</th>
                  <th className="px-3 py-2.5 text-right font-semibold">Tax</th>
                  <th className="px-3 py-2.5 text-right font-semibold">Total</th>
                  <th className="px-3 py-2.5 font-semibold">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-50">
                {c.charges.length === 0 && (
                  <tr><td colSpan={8} className="px-4 py-10 text-center text-sm text-slate-400">
                    Nothing has been charged to this folio yet.
                  </td></tr>
                )}
                {c.charges.map((ch: AdjCharge) => (
                  <tr key={ch.entry_id}
                    onClick={(e) => {
                      // The radio handles its own click; only the rest of the
                      // row toggles, so clicking the control never undoes it.
                      if ((e.target as HTMLElement).tagName === 'INPUT') return
                      if (ch.adjustable && c.adjustable) {
                        setEntryId(entryId === ch.entry_id ? '' : ch.entry_id)
                      }
                    }}
                    title={ch.blocked_reason ?? undefined}
                    className={ch.adjustable && c.adjustable
                      ? `cursor-pointer hover:bg-slate-50 ${
                        entryId === ch.entry_id ? 'bg-brand-light/50' : ''}`
                      : 'opacity-50'}>
                    <td className="px-3 py-2.5">
                      {/* A real radio, not a painted one. It used to be
                          readOnly and rely on the row's click bubbling up,
                          which left it unselectable by keyboard and made the
                          selection easy to lose. */}
                      <input type="radio" name="adjust-charge"
                        checked={entryId === ch.entry_id}
                        disabled={!ch.adjustable || !c.adjustable}
                        onChange={() => setEntryId(ch.entry_id)}
                        aria-label={`Adjust ${ch.description} of ${ch.total}`}
                        className="accent-brand" />
                    </td>
                    {/* Date and time, as the folio ledger shows them. Three
                        charges posted on one day all read "18 Sep 2026" here,
                        which is no help at all to somebody choosing which of
                        them to credit back. */}
                    <td className="whitespace-nowrap px-3 py-2.5 text-slate-500">
                      {fmtDateTime(ch.posted_at)}
                    </td>
                    <td className="px-3 py-2.5 font-medium text-slate-700">
                      {ch.description}
                      {ch.blocked_reason && (
                        <span className="block text-xs font-normal text-amber-700">
                          {ch.blocked_reason}
                        </span>
                      )}
                    </td>
                    <td className="px-3 py-2.5 text-slate-500">{ch.department}</td>
                    <td className="px-3 py-2.5 text-right text-slate-600">
                      {exact.format(Number(ch.amount))}
                    </td>
                    <td className="px-3 py-2.5 text-right text-slate-500">
                      {Number(ch.tax_amount) === 0 ? '—'
                        : exact.format(Number(ch.tax_amount))}
                    </td>
                    <td className="px-3 py-2.5 text-right font-semibold text-slate-800">
                      {exact.format(Number(ch.total))}
                    </td>
                    <td className="px-3 py-2.5">
                      <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-xs font-semibold text-emerald-700">
                        Posted
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

      </div>{/* end of the left column */}

        {/* ------------------------------------------ adjustment details --- */}
        <aside className="w-full space-y-3 self-start rounded-2xl border border-slate-100 bg-white p-5">
          <h2 className="flex items-center gap-2 font-semibold text-ink">
            Adjustment Details
            <PolicyInfo policy={c.policy} />
          </h2>

          <div>
            <span className={label}>Adjustment type *</span>
            <div className="flex flex-wrap gap-3">
              {c.kinds.map((k) => (
                <label key={k.value} className="flex items-center gap-1.5 text-sm text-slate-600">
                  <input type="radio" checked={kind === k.value} className="accent-brand"
                    onChange={() => setKind(k.value)} />
                  {k.label}
                </label>
              ))}
            </div>
          </div>

          <div>
            <span className={label}>Selected charge</span>
            <p className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-600">
              {picked
                ? `${day(picked.business_date)} — ${picked.description} (${exact.format(Number(picked.total))})`
                : 'None — the credit will go against the folio as a whole'}
            </p>
          </div>

          <div>
            <label className={label} htmlFor="adj-amount">Amount *</label>
            <input id="adj-amount" type="number" min="0" step="0.01" value={amount}
              onChange={(e) => setAmount(e.target.value)} className={input} />
            {picked && (
              <p className="mt-1 text-[11px] text-slate-400">
                Up to {exact.format(Number(picked.total))} on this charge.
              </p>
            )}
          </div>

          {picked && Number(picked.tax_amount) > 0 && (
            <label className="flex items-start gap-2 text-sm text-slate-600">
              <input type="checkbox" checked={adjustTax} className="mt-0.5 accent-brand"
                onChange={(e) => setAdjustTax(e.target.checked)} />
              <span>
                Take the tax off too
                <span className="block text-[11px] text-slate-400">
                  The tax was only owed because the charge was.
                  {adjustTax && taxShare > 0
                    && ` Adds ${exact.format(taxShare)}.`}
                </span>
              </span>
            </label>
          )}

          <div>
            <label className={label} htmlFor="adj-reason">Reason *</label>
            <Select id="adj-reason" value={reason} onChange={(e) => setReason(e.target.value)}
              className={input}>
              <option value="">Pick a reason</option>
              {c.reasons.map((r) => (
                <option key={r.value} value={r.value}>{r.label}</option>
              ))}
            </Select>
          </div>

          <div>
            <label className={label} htmlFor="adj-remarks">Remarks *</label>
            <textarea id="adj-remarks" rows={3} value={remarks} maxLength={1000}
              onChange={(e) => setRemarks(e.target.value)}
              placeholder="What happened, in the words you would use to the guest"
              className={input} />
            <p className="mt-0.5 text-right text-[11px] text-slate-400">
              {remarks.length}/1000
            </p>
          </div>

          <div>
            <span className={label}>Evidence</span>
            <input ref={fileRef} type="file" className="hidden"
              accept="image/jpeg,image/png,image/webp,application/pdf"
              onChange={(e) => setPendingFile(e.target.files?.[0] ?? null)} />
            <button onClick={() => fileRef.current?.click()}
              className="flex w-full items-center justify-center gap-2 rounded-lg border border-brand px-3 py-2 text-sm font-medium text-brand hover:bg-brand-light">
              <Upload size={14} /> {pendingFile ? 'Change file' : 'Attach file'}
            </button>
            {pendingFile && (
              <p className="mt-1 flex items-center gap-1.5 text-xs text-slate-600">
                <Paperclip size={11} />{pendingFile.name}
                <button onClick={() => { setPendingFile(null)
                  if (fileRef.current) fileRef.current.value = '' }}
                  className="text-slate-400 hover:text-red-600"><X size={12} /></button>
              </p>
            )}
            <p className="mt-1 text-[11px] text-slate-400">
              Bill, guest request or void slip — JPEG, PNG, WebP or PDF.
            </p>
          </div>

          {/* --------------------------------------- financial impact --- */}
          {Number(amount) > 0 && (
            <div className="rounded-xl bg-slate-50 p-3 text-sm">
              <h3 className="mb-2 font-semibold text-slate-700">Financial impact</h3>
              <dl className="space-y-1.5">
                {picked && (
                  <Line k="Original charge" v={exact.format(Number(picked.total))} />
                )}
                <Line k="Coming off" v={`− ${exact.format(totalOff)}`} />
                <div className="flex items-baseline justify-between border-t border-slate-200 pt-1.5">
                  <dt className="font-semibold text-slate-700">Folio balance after</dt>
                  <dd className="font-semibold text-slate-800">
                    {exact.format(Number(c.folio.balance) - totalOff)}
                  </dd>
                </div>
              </dl>
            </div>
          )}

          {over && (
            <p className="flex items-start gap-2 rounded-xl bg-amber-50 px-3 py-2.5 text-xs text-caution">
              <Clock size={13} className="mt-0.5 shrink-0" />
              Above {money.format(Number(c.policy.threshold))}, so this goes to{' '}
              {c.policy.approver_roles.join(' or ') || 'a manager'} for approval.
              Nothing comes off the folio until they decide.
            </p>
          )}

          {missing.length > 0 && (
            <p className="flex flex-wrap items-center gap-x-1.5 gap-y-1 text-xs text-amber-700">
              <AlertTriangle size={12} /> Still needed:
              {missing.map((m) => (
                <span key={m} className="rounded bg-amber-50 px-1.5 py-0.5 font-medium">
                  {m}
                </span>
              ))}
            </p>
          )}

          <button
            disabled={missing.length > 0 || !c.can_create || create.isPending}
            onClick={() => create.mutate()}
            title={c.can_create ? undefined
              : 'You do not have permission to raise an adjustment.'}
            className="flex w-full items-center justify-center gap-2 rounded-lg bg-brand px-4 py-2.5 text-sm font-semibold text-white hover:bg-brand-dark disabled:opacity-40">
            {create.isPending ? <Loader2 size={15} className="animate-spin" />
              : <FileText size={15} />}
            {over ? 'Submit for approval' : 'Raise adjustment'}
          </button>
        </aside>
      </div>

      {/* ------------------------------------------------- audit trail --- */}
      <div className="overflow-hidden rounded-2xl border border-slate-100 bg-white">
        <h2 className="border-b border-slate-100 px-4 py-3 font-semibold text-ink">
          Adjustments on this folio
        </h2>
        {/* Every credit raised on this folio, with who asked, who approved
            and why — the record an auditor asks for, not the screen. */}
        {c.adjustments.length > 0 && (
          <div className="mb-3 flex justify-end">
            <button onClick={() => exportAdjustments(c)}
              className="flex items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-semibold text-slate-600 hover:bg-slate-50">
              <Download size={13} /> Export CSV
            </button>
          </div>
        )}
        {c.adjustments.length === 0 ? (
          <p className="px-4 py-8 text-center text-sm text-slate-400">
            No adjustment has been raised on this folio.
          </p>
        ) : (
          <ul className="divide-y divide-slate-50">
            {c.adjustments.map((a) => (
              <li key={a.id} className="px-4 py-3">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="flex flex-wrap items-center gap-2">
                      <span className="font-semibold text-slate-800">
                        {a.kind_label} of {exact.format(Number(a.amount) + Number(a.tax_amount))}
                      </span>
                      <span className={`rounded-full px-2 py-0.5 text-xs font-semibold ${
                        STATUS_TINT[a.status] ?? 'bg-slate-75 text-slate-600'}`}>
                        {STATUS_LABEL[a.status] ?? a.status}
                      </span>
                      {a.charge_description && (
                        <span className="text-xs text-slate-500">
                          against {a.charge_description}
                        </span>
                      )}
                    </p>
                    <p className="mt-0.5 text-sm text-slate-600">
                      {a.reason_label} — {a.remarks}
                    </p>
                    <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs text-slate-400">
                      <span>Raised by {a.created_by_name ?? 'someone'} · {stamp(a.created_at)}</span>
                      {a.posted_at && (
                        <span>Posted by {a.posted_by_name ?? 'someone'} · {stamp(a.posted_at)}</span>
                      )}
                      {a.decided_at && (
                        <span>
                          {a.approval_status === 'approved' ? 'Approved' : 'Decided'} by{' '}
                          {a.decided_by} · {stamp(a.decided_at)}
                        </span>
                      )}
                      {a.evidence_url && (
                        <a href={a.evidence_url} target="_blank" rel="noreferrer"
                          className="flex items-center gap-1 text-brand hover:underline">
                          <Paperclip size={11} />{a.evidence_name ?? 'Evidence'}
                        </a>
                      )}
                    </p>
                    {a.decision_comment && (
                      <p className="mt-1 rounded-lg bg-slate-50 px-2.5 py-1.5 text-xs text-slate-600">
                        “{a.decision_comment}”
                      </p>
                    )}
                    {a.status === 'pending_approval' && a.policy_rule_text && (
                      <p className="mt-1 flex items-start gap-1.5 text-xs text-amber-700">
                        <Info size={11} className="mt-0.5 shrink-0" />
                        {a.policy_rule_text} Nothing has come off the folio yet.
                      </p>
                    )}
                  </div>
                  <div className="flex shrink-0 gap-2">
                    {a.can_post && (
                      <button disabled={post.isPending}
                        onClick={() => post.mutate(a.id)}
                        className="flex items-center gap-1.5 rounded-lg bg-brand px-3 py-1.5 text-xs font-semibold text-white hover:bg-brand-dark disabled:opacity-40">
                        {post.isPending ? <Loader2 size={12} className="animate-spin" />
                          : <CheckCircle2 size={12} />}
                        Post adjustment
                      </button>
                    )}
                    {a.can_reverse && (
                      <button onClick={() => setReversing(a)}
                        className="flex items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-semibold text-slate-600 hover:bg-slate-50">
                        <Undo2 size={12} /> Reverse
                      </button>
                    )}
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>

      {reversing && (
        <ReverseDialog row={reversing} onClose={() => setReversing(null)}
          pending={reverse.isPending}
          onSubmit={(r) => reverse.mutate({ id: reversing.id, remarks: r })} />
      )}
    </div>
  )
}

/* ----------------------------------------------------------------- bits --- */
function Fact({ k, v }: { k: string; v: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-slate-400">{k}</dt>
      <dd className="truncate text-slate-700" title={v}>{v}</dd>
    </div>
  )
}
function Line({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="text-slate-500">{k}</dt>
      <dd className="text-slate-700">{v}</dd>
    </div>
  )
}
/**
 * The approval rules, on an ⓘ beside the form's heading.
 *
 * They used to be a card of their own in the top-right corner, which is prime
 * space on this screen for four sentences that do not change and that most
 * people have read once. They are still worth having -- "nothing moves until
 * policy allows it" is the whole premise -- so they sit one hover away, beside
 * the form they govern rather than across the page from it.
 *
 * Opens on hover AND on focus, and the trigger is a real button, so it is
 * reachable from the keyboard. `aria-describedby` ties the panel to it, so a
 * screen reader reads the rules rather than announcing a button called "i".
 */
function PolicyInfo({ policy }: { policy: AdjContext['policy'] }) {
  return (
    <span className="group relative inline-flex">
      <button type="button" aria-label="Approval rules"
        aria-describedby="approval-rules"
        className="flex h-5 w-5 items-center justify-center rounded-full text-slate-400 hover:bg-slate-100 hover:text-brand focus:outline-none focus-visible:ring-2 focus-visible:ring-brand">
        <Info size={14} />
      </button>
      {/* Right-anchored: the rail sits against the right edge, and a panel
          hung from the left ran off the screen. */}
      <span id="approval-rules" role="tooltip"
        className="pointer-events-none absolute right-0 top-7 z-30 hidden w-80 rounded-xl border border-slate-200 bg-white p-3 text-left shadow-lg group-hover:block group-focus-within:block">
        <span className="mb-2 flex items-center gap-2 text-sm font-semibold text-ink">
          <ShieldCheck size={14} className="text-slate-400" /> Approval Rules
        </span>
        <ul className="space-y-2 text-sm font-normal text-slate-600">
          <Rule>{policy.rule_text}</Rule>
          <Rule>Every adjustment is recorded with its reason and remarks, and
            lands on the audit trail.</Rule>
          <Rule>Charges may be adjusted while the guest is in house and for{' '}
            {policy.days_after_checkout} days after checkout.</Rule>
          <Rule>The original charge is never deleted — a reversing credit is
            posted beside it.</Rule>
        </ul>
      </span>
    </span>
  )
}

function Rule({ children }: { children: React.ReactNode }) {
  return (
    <li className="flex items-start gap-2">
      <CheckCircle2 size={14} className="mt-0.5 shrink-0 text-emerald-500" />
      <span>{children}</span>
    </li>
  )
}

function ReverseDialog({ row, onClose, onSubmit, pending }: {
  row: AdjRow; onClose: () => void; pending: boolean
  onSubmit: (remarks: string) => void
}) {
  const [remarks, setRemarks] = useState('')
  const total = Number(row.amount) + Number(row.tax_amount)
  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-slate-900/30 p-4">
      <div className="w-full max-w-md rounded-2xl bg-white p-5 shadow-xl">
        <div className="flex items-start justify-between">
          <h2 className="text-lg font-semibold text-ink">Reverse adjustment</h2>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600">
            <X size={18} />
          </button>
        </div>
        <p className="mt-3 text-sm text-slate-600">
          This puts <strong>{exact.format(total)}</strong> back on the folio by
          posting a debit. The adjustment and its credit both stay on the bill —
          deleting either would leave a folio whose history no longer explains
          its own balance.
        </p>
        <label className="mt-4 block text-xs text-slate-500">
          Why is it being reversed? *
          <textarea rows={3} value={remarks} maxLength={1000} autoFocus
            onChange={(e) => setRemarks(e.target.value)} className={`mt-1 ${input}`} />
        </label>
        <div className="mt-5 flex justify-end gap-2">
          <button onClick={onClose}
            className="rounded-lg border border-slate-200 px-4 py-2 text-sm text-slate-600 hover:bg-slate-50">
            Cancel
          </button>
          <button disabled={remarks.trim().length < 5 || pending}
            onClick={() => onSubmit(remarks.trim())}
            className="flex items-center gap-1.5 rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white hover:bg-brand-dark disabled:opacity-40">
            {pending ? <Loader2 size={14} className="animate-spin" /> : <Undo2 size={14} />}
            Reverse it
          </button>
        </div>
      </div>
    </div>
  )
}

/** The folio's adjustments, with the whole approval trail. */
function exportAdjustments(c: AdjContext): void {
  downloadCsv(
    datedName(`adjustments-folio-${c.folio.folio_no ?? 'folio'}`),
    ['Raised', 'Kind', 'Status', 'Charge', 'Amount', 'Tax', 'Reason',
     'Remarks', 'Raised by', 'Approval', 'Decided by', 'Decided at',
     'Decision comment', 'Posted at', 'Posted by', 'Evidence'],
    c.adjustments.map((a) => [
      a.created_at, a.kind_label, a.status, a.charge_description,
      a.amount, a.tax_amount, a.reason_label, a.remarks,
      a.created_by_name, a.approval_status ?? (a.approval_required ? 'required' : 'not required'),
      a.decided_by, a.decided_at, a.decision_comment,
      a.posted_at, a.posted_by_name, a.evidence_name,
    ]),
  )
}
