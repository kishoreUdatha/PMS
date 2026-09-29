import { usePropertyName } from '../hooks/useProperty'
import { fmtDate, fmtTime } from '../lib/dates'
import Select from '../components/Select'
import { useEffect, useRef, useState } from 'react'
import { downloadCsv, datedName } from '../lib/csv'
import { useParams, useSearchParams, Link } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useFlash } from '../hooks/useFlash'
import {
  AlertTriangle, CheckCircle2, Clock, Info, Loader2,
  Mail, MoreHorizontal, Printer, RotateCcw, ShieldCheck, Undo2, X, Download,} from 'lucide-react'
import {
  getReversalContext, createReversal, postReversal, cancelReversal,
  
  type RevContext, type RevRow, type RevRule,
} from '../api'
import { useActivePropertyId } from '../hooks/useProperty'
import { errorText } from '../lib/forms'

/**
 * Screen 115 — Payment Details and Reversal.
 *
 * Screen 114 corrects a charge; this one undoes a payment. Nothing is deleted:
 * the ledger posts a debit against the payment's allocations, so the folio
 * balance rises back and both the payment and its reversal stay on the bill.
 *
 * Three actions that look alike and are not. A **void** is for a payment taken
 * today that should not have been — whole amount, same business day. A
 * **refund** is money going back to the guest, and is the only one that may be
 * partial. A **reversal** corrects a settled payment recorded against the wrong
 * folio or amount. The screen says which are available and why the others
 * aren't, rather than offering three buttons that mean the same thing.
 */

const exact = new Intl.NumberFormat('en-IN', {
  style: 'currency', currency: 'INR', minimumFractionDigits: 2,
})
const day = (s?: string | null) => {
  if (!s) return '—'
  const d = new Date(`${s.slice(0, 10)}T00:00:00`)
  return fmtDate(d)
}
const at = (iso?: string | null) => fmtTime(iso)
const stamp = (iso?: string | null) => iso ? `${day(iso)}, ${at(iso)}` : '—'

const STATUS_TINT: Record<string, string> = {
  pending_approval: 'bg-amber-100 text-amber-800',
  approved: 'bg-sky-100 text-sky-700',
  posted: 'bg-emerald-100 text-emerald-700',
  rejected: 'bg-red-100 text-red-700',
  cancelled: 'bg-slate-200 text-slate-600',
}
/** What state a request is *really* in.
 *
 * `status` on the request and the approval's own status are two different
 * records, and between the approver saying yes and somebody pressing Post they
 * disagree: the row still reads 'pending_approval' while the approval reads
 * 'approved'. The card showed both at once -- a "Pending approval" badge above
 * a line saying "Approved by ..." -- which tells a cashier nothing about
 * whether they are waiting on a manager or the manager is waiting on them.
 */
const liveStatus = (r: RevRow) =>
  r.status === 'pending_approval' && r.approval_status === 'approved'
    ? 'approved' : r.status

const STATUS_LABEL: Record<string, string> = {
  pending_approval: 'Pending approval', approved: 'Ready to post',
  posted: 'Posted', rejected: 'Rejected', cancelled: 'Withdrawn',
}
// The circled step markers on the mockup's timeline.
const RING: Record<string, string> = {
  emerald: 'bg-emerald-100 text-emerald-600',
  sky: 'bg-sky-100 text-sky-600',
  amber: 'bg-amber-100 text-amber-700',
  rose: 'bg-rose-100 text-rose-600',
  slate: 'bg-slate-75 text-slate-400',
}
const KIND_BLURB: Record<string, string> = {
  void: 'Taken today and should not have been. The whole amount, same business day.',
  refund: 'Money going back to the guest. This is the only one that may be partial.',
  reversal: 'A settled payment recorded against the wrong folio or amount.',
}

const TABS = [
  { key: 'timeline', label: 'Timeline' },
  { key: 'folio', label: 'Folio Items' },
  { key: 'recon', label: 'Reconciliation' },
  { key: 'audit', label: 'Audit Log' },
  { key: 'reversals', label: 'Reversals' },
  { key: 'notes', label: 'Notes' },
] as const

const input = 'w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700 outline-none focus:border-brand'
// The mockup labels the form fields to the left of the control, not above it.
const fieldLabel = 'mb-1 block text-sm font-medium text-slate-600 sm:mb-0'


/* -------------------------------------------------------------- the page --- */
//: A reversal is read later by somebody reconciling a till who was not
//: there. "ok" and "test" are not an explanation, so a few characters
//: are asked for -- stated on the field rather than sprung on submit.
const REMARKS_MIN = 5

export default function PaymentReversal() {
  const propertyName = usePropertyName()
  const { paymentId = '' } = useParams()
  const propertyId = useActivePropertyId()
  const qc = useQueryClient()

  // The action the caller already chose, and where they came from.
  //
  // The folio's row menu says "Void payment" in as many words. Arriving with
  // nothing selected made the next screen contradict it -- Refund is the
  // primary button and draws as chosen while `kind` is still empty -- so the
  // click said void and the screen said refund. The menu's intent travels with
  // the navigation instead.
  const [params] = useSearchParams()
  const wanted = params.get('kind') ?? ''
  const back = params.get('back')
  const [kind, setKind] = useState(
    ['void', 'refund', 'reversal'].includes(wanted) ? wanted : '')
  const [amount, setAmount] = useState('')
  const [reason, setReason] = useState('')
  const [remarks, setRemarks] = useState('')
  const [toast, setToast] = useFlash()
  const [error, setError] = useState('')
  const [withdrawing, setWithdrawing] = useState<RevRow | null>(null)
  type Tab = 'timeline' | 'folio' | 'recon' | 'audit' | 'reversals' | 'notes'
  const asked = params.get('tab')
  const [tab, setTab] = useState<Tab>(
    TABS.some((t) => t.key === asked) ? asked as Tab : 'timeline')
  // Whether the auto-switch below has already had its turn. Without this it
  // fought the operator: every refetch after posting dragged them back to
  // Reversals from whichever tab they had just chosen.
  const steered = useRef(false)
  // The same, for the action. The folio's menu no longer says which of the
  // three this will be, because it cannot know: whether a void is even legal
  // depends on the business date, and whether anything is left to give back
  // depends on what has already gone. Those rules arrive with this query, so
  // the choice is made here -- and only when it is not a choice at all.
  const picked = useRef(false)
  const [more, setMore] = useState(false)

  const q = useQuery({
    queryKey: ['rev-context', paymentId, propertyId],
    queryFn: () => getReversalContext(paymentId, propertyId),
    enabled: propertyId !== '' && paymentId !== '',
  })

  // An open request is the only thing on this screen anybody has to act on,
  // and its button lives on a tab that is not the one the screen opens on.
  // That is how a void gets raised, approved, and then left unposted: the
  // Timeline says "pending approval" and shows nothing to press.
  const open = q.data?.reversals.find((r) => r.can_post || r.can_cancel)
  useEffect(() => {
    if (!steered.current && open && asked === null) {
      steered.current = true
      setTab('reversals')
    }
  }, [open, asked])

  // Exactly one action allowed is not a decision, it is an answer, so it is
  // filled in. Two or more and the form stays empty on purpose: picking the
  // wrong one of void/refund/reversal is not a typo a cashier can see, and the
  // rules panel beside the form is what tells them apart.
  const only = q.data?.rules.filter((r) => r.allowed) ?? []
  useEffect(() => {
    if (!picked.current && asked === null && kind === '' && only.length === 1) {
      picked.current = true
      setKind(only[0].kind)
    }
  }, [only, asked, kind])

  const errorRef = useRef<HTMLParagraphElement>(null)
  const refresh = () => qc.invalidateQueries({ queryKey: ['rev-context'] })
  const fail = (e: unknown) => {
    setError(errorText(e, 'That did not work. Please try again.'))
    setToast('')
    // The banner lives at the top of a page tall enough that the button which
    // caused the error is usually off screen by the time it appears. A refusal
    // nobody sees reads as a button that did nothing — and the messages this
    // screen gets back are ones the user has to act on ("open a cashier shift
    // first"), not ones they can shrug off.
    requestAnimationFrame(() => {
      errorRef.current?.scrollIntoView({ block: 'center', behavior: 'smooth' })
    })
  }
  const reset = () => { setKind(''); setAmount(''); setReason(''); setRemarks('') }

  const create = useMutation({
    mutationFn: () => createReversal(paymentId, propertyId, {
      kind, amount: amount === '' ? null : Number(amount),
      reason, remarks: remarks.trim(),
    }),
    onSuccess: (r) => {
      setError(''); setToast(r.message); reset(); refresh()
      // The request now exists and somebody has to post it. Leaving the
      // operator on the form they just submitted is what made that invisible.
      setTab('reversals')
    },
    onError: fail,
  })
  const post = useMutation({
    mutationFn: (id: string) => postReversal(id, propertyId),
    onSuccess: (r) => { setError(''); setToast(r.message); refresh() },
    onError: fail,
  })
  const withdraw = useMutation({
    mutationFn: (v: { id: string; remarks: string }) =>
      cancelReversal(v.id, propertyId, v.remarks),
    onSuccess: (r) => { setWithdrawing(null); setError(''); setToast(r.message); refresh() },
    onError: fail,
  })

  if (propertyId === '') {
    return <p className="text-sm text-slate-500">Pick a property first.</p>
  }
  if (q.isLoading) {
    return (
      <p className="flex items-center gap-2 text-sm text-slate-500">
        <Loader2 className="h-4 w-4 animate-spin" /> Loading the payment…
      </p>
    )
  }
  if (q.isError || !q.data) {
    const er = q.error as { response?: { data?: { detail?: string } } }
    return (
      <p className="flex items-start gap-2 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
        {errorText(er, 'This payment could not be loaded.')}
      </p>
    )
  }

  const c: RevContext = q.data
  const p = c.payment
  const chosen = c.rules.find((r) => r.kind === kind)
  const refundable = Number(p.refundable)
  const anyAllowed = c.rules.some((r) => r.allowed)
  // Everything anyone wrote about this payment, in one place: the note the
  // cashier left when taking it, and the remarks on each reversal request.
  const notes: { text: string; who: string; when: string }[] = [
    ...(p.notes
      ? [{ text: p.notes, who: p.cashier ?? 'The cashier',
           when: `when the payment was taken, ${stamp(p.received_at)}` }]
      : []),
    ...c.reversals.map((r) => ({
      text: r.remarks,
      who: r.created_by_name ?? 'Someone',
      when: `${r.kind_label.toLowerCase()} request, ${stamp(r.created_at)}`,
    })),
  ]
  // Served, not composed here. This screen used to build its own --
  // `CBR-RCP-20260917-713ACD36` -- while the PDF handed the guest
  // `RCPT-713ACD36` for the same payment, so the number a guest read off their
  // receipt was one this screen could not match. The `CBR-` was a hardcoded
  // brand prefix besides, printed on every property's receipts.
  const receiptNo = p.receipt_no

  const missing: string[] = []
  if (kind === '') missing.push('an action')
  if (reason === '') missing.push('a reason')
  // Say the length, not just the field name. A box with "test" in it and a
  // chip reading "remarks" tells the cashier nothing is missing and refuses
  // anyway; the rule only becomes visible by being broken.
  if (remarks.trim().length < REMARKS_MIN) {
    missing.push(remarks.trim()
      ? `remarks — ${REMARKS_MIN} characters or more`
      : 'remarks')
  }
  if (chosen?.partial_allowed && amount !== '' && Number(amount) > refundable) {
    missing.push('an amount within what is left')
  }

  // What will actually go back: the whole refundable balance unless this is a
  // refund and the operator typed something smaller.
  const going = chosen?.partial_allowed && amount !== ''
    ? Number(amount) : refundable

  // Whether what is being asked sits inside the governing threshold, and who
  // it reaches when it does not. Both read out of the rule text the API
  // returned, so the pill and the approver line cannot disagree with policy.
  const threshold = (() => {
    const m = chosen?.text.match(/Rs ([0-9,]+)/)
    return m ? Number(m[1].replace(/,/g, '')) : null
  })()
  const approverRoles = chosen?.text.match(/approval by (.+?)\.$/)?.[1] ?? ''
  const withinPolicy = threshold !== null && going <= threshold
  // Before an action is picked there is still a meaningful answer: whether
  // giving the whole remaining balance back would clear on its own.
  const defaultThreshold = (() => {
    const m = c.rules.find((r) => r.allowed)?.text.match(/Rs ([0-9,]+)/)
    return m ? Number(m[1].replace(/,/g, '')) : null
  })()
  const pillWithin = chosen ? withinPolicy
    : defaultThreshold !== null && refundable <= defaultThreshold


  return (
    <div className="space-y-4">
      {/* ------------------------------------------------------- header --- */}
      <nav className="flex items-center gap-1.5 text-sm text-slate-400">
        <Link to="/payments" className="hover:text-brand">Finance</Link>
        <span>›</span>
        {/* Back to the bill this payment is on, when that is where the
            operator came from. The trail used to lead to Cashiering from
            every crumb, so voiding a payment from a folio left no way back
            to it -- the guest is still at the desk and the screen they were
            reading is gone. */}
        {back ? (
          <>
            <Link to={back} className="hover:text-brand">Folio</Link>
            <span>›</span>
          </>
        ) : null}
        <Link to="/payments" className="hover:text-brand">Payment Details</Link>
        <span>›</span>
        <span className="font-medium text-slate-600">{receiptNo}</span>
      </nav>

      {/* The one thing left to do on this payment, said once, at the top,
          above the tabs rather than inside one of them.
          A void is three steps -- raise it, have it approved, post it -- and
          only the last moves money. Nothing on this screen said so, so a
          request could be raised, approved, and left: the folio showed an
          untouched payment and the Timeline showed "pending approval" with
          nothing to press. */}
      {open && (
        <div className="flex flex-wrap items-center gap-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3">
          <Clock size={16} className="shrink-0 text-caution" />
          <span className="min-w-0 text-sm text-amber-900">
            <span className="font-semibold">
              {open.kind_label} of {exact.format(Number(open.amount))}
            </span>{' '}
            {liveStatus(open) === 'approved'
              ? 'is approved and waiting to be posted. Nothing has gone back yet.'
              : 'has been raised and is waiting on approval. Nothing has gone back yet.'}
          </span>
          {open.can_post && (
            <button onClick={() => post.mutate(open.id)} disabled={post.isPending}
              className="ml-auto flex items-center gap-1.5 rounded-lg bg-brand px-3 py-1.5 text-xs font-semibold text-white hover:bg-brand-dark disabled:opacity-40">
              {post.isPending ? <Loader2 size={12} className="animate-spin" />
                : <RotateCcw size={12} />}
              Post {open.kind_label.toLowerCase()}
            </button>
          )}
          <button onClick={() => setTab('reversals')}
            className={`rounded-lg border border-amber-300 px-3 py-1.5 text-xs font-semibold text-amber-900 hover:bg-amber-100 ${
              open.can_post ? '' : 'ml-auto'}`}>
            See the request
          </button>
        </div>
      )}

      {/* Title and actions share the first row; the subtitle gets the whole
          width beneath them so it stays on one line. Squeezed beside the
          buttons it had only ~400px and broke in two. */}
      <div>
        <div className="flex items-center justify-between gap-4">
          <h1 className="text-display text-ink">
            Payment Details &amp; Reversal
          </h1>
        {/* The mockup's action row. Each one selects that action in the form
            below rather than doing it outright — money never moves from a
            single click. Disabled buttons say why in their tooltip. */}
        <div className="flex shrink-0 items-center gap-1.5">
          <button onClick={() => printReceipt(p, receiptNo, propertyName)}
            className="flex items-center gap-1.5 rounded-lg border border-slate-200 px-2.5 py-1.5 text-[13px] font-medium text-slate-600 hover:bg-slate-50">
            <Printer size={15} /> Print Receipt
          </button>
          {/* Email Receipt is on the mockup and stays disabled: there is no
              mail transport in this system, so the button would be a promise
              the product cannot keep. */}
          <button disabled
            title="There is no mail or SMS transport in this system yet, so a receipt cannot be emailed."
            className="flex items-center gap-1.5 rounded-lg border border-slate-200 px-2.5 py-1.5 text-[13px] font-medium text-slate-400"
            >
            <Mail size={14} /> Email Receipt
          </button>
          {/* Ordered as the mockup has them, Refund first. None of the three
              is drawn as chosen until it is chosen: filling Refund in by
              default made an unselected form look answered, and the form
              refuses to submit in that state anyway. */}
          {['refund', 'void', 'reversal'].map((k) => {
            const r = c.rules.find((x) => x.kind === k)
            if (!r) return null
            return (
              <button key={r.kind} disabled={!r.allowed}
                title={r.blocked_reason ?? undefined}
                onClick={() => { setKind(r.kind); setAmount('') }}
                className={`flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-[13px] font-semibold disabled:opacity-40 ${
                  kind === r.kind
                    ? 'bg-brand text-white'
                    : 'border border-brand text-brand hover:bg-brand-light'}`}>
                {r.kind === 'refund' ? <RotateCcw size={14} />
                  : r.kind === 'void' ? <X size={14} /> : <Undo2 size={14} />}
                {r.kind === 'void' ? 'Void (Same Day)'
                  : r.kind === 'reversal' ? 'Reverse' : 'Refund'}
              </button>
            )
          })}
          <span className="relative">
            <button onClick={() => setMore(!more)}
              aria-label="More actions"
              className="flex items-center rounded-lg border border-slate-200 px-2.5 py-2 text-slate-500 hover:bg-slate-50">
              <MoreHorizontal size={15} />
            </button>
            {more && (
              <span className="absolute right-0 top-9 z-20 block w-60 rounded-xl border border-slate-100 bg-white p-1 text-left shadow-lg">
                <Link to={`/payments/folios/${p.folio_id}/adjust`}
                  className="block rounded-lg px-3 py-2 text-sm text-slate-600 hover:bg-slate-50">
                  Adjust a charge on this folio
                </Link>
                <Link to="/payments"
                  className="block rounded-lg px-3 py-2 text-sm text-slate-600 hover:bg-slate-50">
                  Back to the Cashiering Centre
                </Link>
              </span>
            )}
          </span>
          </div>
        </div>
        <p className="mt-1 text-sm text-slate-500">
          View payment information, settlement status, and process refund,
          void or reversal as per resort policy.
        </p>
      </div>

      {toast && (
        <p className="flex items-start gap-2 rounded-xl bg-emerald-50 px-4 py-3 text-sm text-emerald-700">
          <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />{toast}
        </p>
      )}
      {error && (
        <p ref={errorRef} tabIndex={-1} role="alert"
          className="flex items-start gap-2 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span className="flex-1">{error}</span>
          <button onClick={() => setError('')} aria-label="Dismiss"
            className="shrink-0 rounded p-0.5 text-red-400 hover:bg-red-100 hover:text-red-700">
            <X size={15} />
          </button>
        </p>
      )}

      {/* --------------------------------------------------------- body ---
          Laid out as the mockup has it: the payment across two thirds with
          the tabs beneath it, and Amount, Policy and the Reversal form
          stacked down the right where they stay visible while deciding. */}
      {/* Two thirds to the record, one to the actions.
          It was 3:2, which left the payment card narrow enough that its own
          values wrapped — stay dates over two lines, a transaction id broken
          mid-string — while the right column carried three short label/value
          rows and a form, with room to spare on every one of them. The right
          side still has to hold the reversal form, so it does not shrink past
          what that needs. */}
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <div className="min-w-0 space-y-4 xl:col-span-2">
          <div className="rounded-2xl border border-slate-100 bg-white p-5">
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 pb-4">
              <span className="flex items-center gap-3">
                <span className={`flex items-center gap-1.5 rounded-full px-3 py-1 text-sm font-semibold ${
                  p.payment_status === 'succeeded'
                    ? 'bg-emerald-100 text-emerald-700'
                    : 'bg-amber-100 text-caution'}`}>
                  <CheckCircle2 size={14} />
                  {p.payment_status === 'succeeded' ? 'Settled' : p.payment_status}
                </span>
                <span className="text-sm text-slate-500">
                  {Number(p.refunded) > 0
                    ? `${exact.format(Number(p.refunded))} has been given back`
                    : 'Payment received successfully'}
                </span>
              </span>
              <span className="text-right">
                <span className="block text-xs text-slate-400">Receipt No.</span>
                <span className="block font-semibold text-slate-800">{receiptNo}</span>
              </span>
            </div>
            <div className="grid grid-cols-1 gap-x-10 gap-y-3 sm:grid-cols-2 sm:divide-x sm:divide-slate-100">
              <dl className="space-y-2.5">
                <Fact k="Guest Name" v={p.guest_name ?? 'Unlinked payment'} />
                <Fact k="Booking No." v={p.reservation_number ?? '—'} />
                <Fact k="Folio No." v={p.folio_no ?? '—'} />
                <Fact k="Room No." v={p.room_code
                  ? `${p.room_code}${p.room_type ? ` (${p.room_type})` : ''}`
                  : p.room_type ?? '—'} />
                {/* On the mockup and kept here so the card reads the same, but
                    there is no company module yet (SCR-038) and no purpose
                    field, so both say so rather than showing a made-up value. */}
                <Fact k="Company" v="—" hint="Company billing arrives with SCR-038" />
                <Fact k="Stay Dates" v={p.arrival_date
                  ? `${day(p.arrival_date)} – ${day(p.departure_date)}` : '—'} />
                <Fact k="Purpose" v="—" hint="No purpose field is captured on a payment" />
              </dl>
              <dl className="space-y-2.5 sm:pl-10">
                <Fact k="Payment Date & Time" v={stamp(p.received_at)} />
                <Fact k="Payment Method" v={p.method_label} />
                <Fact k="Reference No." v={p.reference ?? '—'} />
                <Fact k="Gateway" v="—"
                  hint="No payment gateway is integrated; payments are recorded directly" />
                <Fact k="Transaction ID" v={p.provider_transaction_id ?? '—'} />
                <div className="grid grid-cols-[8.75rem_1fr] items-baseline gap-3">
                  <dt className="whitespace-nowrap text-sm text-slate-400">Gateway Status</dt>
                  <dd>
                    <span className={`inline-block rounded-md px-2 py-0.5 text-xs font-semibold ${
                      p.payment_status === 'succeeded'
                        ? 'bg-emerald-100 text-emerald-700'
                        : 'bg-amber-100 text-caution'}`}>
                      {p.payment_status === 'succeeded' ? 'Success' : p.payment_status}
                    </span>
                  </dd>
                </div>
                {/* Both of these only earn a row when they say something the
                    reader cannot already see. Business date matches the
                    payment's own date until night audit (SCR-022) starts
                    rolling it, and every payment is taken at the front desk
                    until POS (SCR-013) exists. Shown the moment either stops
                    being true, so there is nothing to remember later. */}
                {p.business_date
                  && p.business_date !== p.received_at.slice(0, 10) && (
                  <Fact k="Business Date" v={day(p.business_date)} />
                )}
                {p.source !== 'front_desk' && (
                  <Fact k="Taken At" v={p.source_label} />
                )}
                <Fact k="Cashier" v={p.cashier ?? '—'} />
                <div className="grid grid-cols-[minmax(6rem,8.75rem)_1fr] items-baseline gap-3">
                  <dt className="text-sm text-slate-400">Settlement Status</dt>
                  {/* The pill was one `whitespace-nowrap` line reading
                      "Settled (13 Sep 2026, 18:30)" in a column that could not
                      shrink, so it ran out through the side of the card. The
                      word is the status; the timestamp is detail, and it sits
                      under the pill where it has room. */}
                  <dd className="min-w-0">
                    {p.settled_at ? (
                      <>
                        <span className="inline-block rounded-md bg-emerald-100 px-2 py-0.5 text-xs font-semibold text-emerald-700">
                          Settled
                        </span>
                        <span className="mt-0.5 block text-xs text-slate-400">
                          {stamp(p.settled_at)}
                        </span>
                      </>
                    ) : (
                      <span className="inline-block rounded-md bg-amber-100 px-2 py-0.5 text-xs font-semibold text-caution">
                        Not credited to a folio
                      </span>
                    )}
                  </dd>
                </div>
              </dl>
            </div>
          </div>

          <div className="min-w-0 flex-1 rounded-2xl border border-slate-100 bg-white">
            {/* The mockup's four views of the same payment. They are tabs rather
                than four stacked panels because only one is ever the question
                being asked: what happened, what was it against, does it add up,
                who touched it. */}
            {/* Wraps rather than scrolls. Six tabs do not fit the narrow
                column, and `overflow-x-auto` answered that with a scrollbar
                under the strip — which hid whichever tabs were off the end
                behind a gesture nobody thinks to try on a row of tabs. */}
            <div className="flex flex-wrap gap-1 border-b border-slate-100 px-2">
              {TABS.map((t) => {
                const n = t.key === 'folio' ? c.folio_items.length
                  : t.key === 'audit' ? c.audit.length
                    : t.key === 'timeline' ? c.timeline.length
                      : t.key === 'notes' ? notes.length
                        : t.key === 'reversals' ? c.reversals.length : 0
                return (
                  <button key={t.key} onClick={() => setTab(t.key)}
                    className={`flex items-center gap-1.5 whitespace-nowrap border-b-2 px-4 py-2.5 text-sm font-semibold ${
                      tab === t.key ? 'border-brand text-brand'
                        : 'border-transparent text-slate-500 hover:text-slate-700'}`}>
                    {t.label}{n > 0 ? ` (${n})` : ''}
                    {t.key === 'recon' && (
                      <span className={`h-1.5 w-1.5 rounded-full ${
                        c.reconciliation.balanced ? 'bg-emerald-500' : 'bg-red-500'}`} />
                    )}
                  </button>
                )
              })}
            </div>

            <div className="p-5">
              {tab === 'timeline' && (
                <ul className="space-y-5">
                  {c.timeline.map((t, i) => (
                    <li key={i} className="flex gap-3">
                      <span className="flex flex-col items-center">
                        <span className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full ${
                          RING[t.tone] ?? RING.slate}`}>
                          {t.tone === 'emerald' ? <CheckCircle2 size={13} />
                            : t.tone === 'rose' ? <Undo2 size={12} />
                              : <Info size={13} />}
                        </span>
                        {i < c.timeline.length - 1 && (
                          <span className="mt-1 w-px flex-1 bg-slate-75" />
                        )}
                      </span>
                      <span className="grid min-w-0 flex-1 grid-cols-1 gap-x-4 pb-1 sm:grid-cols-[13rem_1fr_auto]">
                        <span className="min-w-0">
                          <span className="block text-sm font-semibold text-slate-800">
                            {t.title}
                          </span>
                          <span className="block text-[11px] text-slate-400">
                            {stamp(t.at)}
                          </span>
                        </span>
                        <span className="min-w-0 text-sm text-slate-500">{t.detail}</span>
                        <span className="whitespace-nowrap text-sm text-slate-400">
                          {t.actor}
                        </span>
                      </span>
                    </li>
                  ))}
                </ul>
              )}

              {tab === 'folio' && (
                c.folio_items.length === 0 ? (
                  <p className="py-8 text-center text-sm text-slate-400">
                    This payment is not allocated to a folio.
                  </p>
                ) : (
                  <div className="-mx-5 -my-5 overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
                        <tr>
                          <th className="px-5 py-2.5 font-semibold">Date &amp; time</th>
                          <th className="px-4 py-2.5 font-semibold">Description</th>
                          <th className="px-4 py-2.5 text-right font-semibold">Charge</th>
                          <th className="px-4 py-2.5 text-right font-semibold">Credit</th>
                          <th className="px-5 py-2.5 text-right font-semibold">Balance</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-50">
                        {c.folio_items.map((f) => (
                          <tr key={f.entry_id}
                            className={f.is_this_payment ? 'bg-brand-light/40' : ''}>
                            {/* The posting stamp, not the business date:
                                several entries on one day all read the same
                                date, and this list is read to tell them
                                apart. */}
                            <td className="whitespace-nowrap px-5 py-2.5 text-slate-500">
                              {stamp(f.posted_at)}
                            </td>
                            <td className="px-4 py-2.5">
                              <span className="text-slate-700">{f.description}</span>
                              {f.is_this_payment && (
                                <span className="ml-2 rounded bg-brand px-1.5 py-0.5 text-[10px] font-semibold text-white">
                                  this payment
                                </span>
                              )}
                              {f.reverses_something && (
                                <span className="ml-2 text-xs text-slate-400">
                                  reverses an earlier line
                                </span>
                              )}
                            </td>
                            <td className="px-4 py-2.5 text-right text-slate-700">
                              {f.entry_type === 'debit'
                                ? exact.format(Number(f.amount)) : '—'}
                            </td>
                            <td className="px-4 py-2.5 text-right text-positive">
                              {f.entry_type === 'credit'
                                ? exact.format(Number(f.amount)) : '—'}
                            </td>
                            {/* Served, not summed here: the rows arrive
                                newest-first and a balance accumulated in that
                                order would be wrong on every line. */}
                            <td className="whitespace-nowrap px-5 py-2.5 text-right tabular-nums text-slate-600">
                              {exact.format(Number(f.running))}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )
              )}

              {tab === 'recon' && (
                <div className="space-y-3">
                  <dl className="space-y-2 text-sm">
                    {c.reconciliation.lines.map((l, i) => (
                      <div key={i}
                        className={`flex items-baseline justify-between gap-4 ${
                          l.emphasis ? 'border-t border-slate-200 pt-2' : ''}`}>
                        <dt className="min-w-0">
                          <span className={l.emphasis
                            ? 'font-semibold text-slate-700' : 'text-slate-600'}>
                            {l.label}
                          </span>
                          {l.note && (
                            <span className="block text-[11px] text-slate-400">
                              {l.note}
                            </span>
                          )}
                        </dt>
                        <dd className={`shrink-0 ${l.emphasis
                          ? 'text-lg font-semibold text-slate-800' : 'text-slate-700'}`}>
                          {exact.format(Number(l.amount))}
                        </dd>
                      </div>
                    ))}
                  </dl>
                  <p className={`flex items-start gap-2 rounded-xl px-4 py-3 text-sm ${
                    c.reconciliation.balanced
                      ? 'bg-emerald-50 text-emerald-700' : 'bg-red-50 text-red-700'}`}>
                    {c.reconciliation.balanced
                      ? <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />
                      : <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />}
                    {c.reconciliation.verdict}
                  </p>
                </div>
              )}

              {tab === 'reversals' && (
              <>
              {/* Voids, refunds and reversals on this payment, with who
                  approved each — money handed back needs a paper trail. */}
              {c.reversals.length > 0 && (
                <div className="mb-3 flex justify-end">
                  <button onClick={() => exportReversals(c)}
                    className="flex items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-semibold text-slate-600 hover:bg-slate-50">
                    <Download size={13} /> Export CSV
                  </button>
                </div>
              )}
              </>
            )}
            {tab === 'reversals' && (
                c.reversals.length === 0 ? (
                  <p className="px-4 py-8 text-center text-sm text-slate-400">
                    Nothing has been given back on this payment.
                  </p>
                ) : (
                  <ul className="divide-y divide-slate-50">
                    {c.reversals.map((r) => (
                      <li key={r.id} className="px-4 py-3">
                        <div className="flex flex-wrap items-start justify-between gap-3">
                          <div className="min-w-0">
                            <p className="flex flex-wrap items-center gap-2">
                              <span className="font-semibold text-slate-800">
                                {r.kind_label} of {exact.format(Number(r.amount))}
                              </span>
                              <span className={`rounded-full px-2 py-0.5 text-xs font-semibold ${
                                STATUS_TINT[liveStatus(r)] ?? 'bg-slate-75 text-slate-600'}`}>
                                {STATUS_LABEL[liveStatus(r)] ?? liveStatus(r)}
                              </span>
                            </p>
                            <p className="mt-0.5 text-sm text-slate-600">
                              {r.reason_label} — {r.remarks}
                            </p>
                            <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs text-slate-400">
                              <span>Raised by {r.created_by_name ?? 'someone'} · {stamp(r.created_at)}</span>
                              {r.posted_at && (
                                <span>Posted by {r.posted_by_name ?? 'someone'} · {stamp(r.posted_at)}</span>
                              )}
                              {r.decided_at && (
                                <span>
                                  {r.approval_status === 'approved' ? 'Approved' : 'Decided'} by{' '}
                                  {r.decided_by} · {stamp(r.decided_at)}
                                </span>
                              )}
                            </p>
                            {r.decision_comment && (
                              <p className="mt-1 rounded-lg bg-slate-50 px-2.5 py-1.5 text-xs text-slate-600">
                                “{r.decision_comment}”
                              </p>
                            )}
                            {liveStatus(r) === 'pending_approval' && r.policy_rule_text && (
                              <p className="mt-1 flex items-start gap-1.5 text-xs text-amber-700">
                                <Clock size={11} className="mt-0.5 shrink-0" />
                                {r.policy_rule_text} Nothing has gone back yet.
                              </p>
                            )}
                          </div>
                          <div className="flex shrink-0 gap-2">
                            {r.can_post && (
                              <button disabled={post.isPending} onClick={() => post.mutate(r.id)}
                                className="flex items-center gap-1.5 rounded-lg bg-brand px-3 py-1.5 text-xs font-semibold text-white hover:bg-brand-dark disabled:opacity-40">
                                {post.isPending ? <Loader2 size={12} className="animate-spin" />
                                  : <RotateCcw size={12} />}
                                Post {r.kind_label.toLowerCase()}
                              </button>
                            )}
                            {r.can_cancel && (
                              <button onClick={() => setWithdrawing(r)}
                                className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-semibold text-slate-600 hover:bg-slate-50">
                                Withdraw
                              </button>
                            )}
                          </div>
                        </div>
                      </li>
                    ))}
                  </ul>
                )
              )}

              {tab === 'notes' && (
                notes.length === 0 ? (
                  <p className="py-8 text-center text-sm text-slate-400">
                    Nobody has written anything about this payment.
                  </p>
                ) : (
                  <ul className="divide-y divide-slate-50">
                    {notes.map((n, i) => (
                      <li key={i} className="py-3">
                        <p className="text-sm text-slate-700">{n.text}</p>
                        <p className="mt-0.5 text-[11px] text-slate-400">
                          {n.who} · {n.when}
                        </p>
                      </li>
                    ))}
                  </ul>
                )
              )}

              {tab === 'audit' && (
                c.audit.length === 0 ? (
                  <p className="py-8 text-center text-sm text-slate-400">
                    Nothing has been recorded against this payment.
                  </p>
                ) : (
                  <ul className="divide-y divide-slate-50">
                    {c.audit.map((a, i) => (
                      <li key={i} className="flex flex-wrap items-baseline justify-between gap-2 py-2.5">
                        <span className="min-w-0">
                          <span className="block text-sm font-medium text-slate-800">
                            {a.label}
                          </span>
                          {a.detail && (
                            <span className="block text-xs text-slate-500">{a.detail}</span>
                          )}
                          {a.reason && (
                            <span className="block text-xs italic text-slate-400">
                              “{a.reason}”
                            </span>
                          )}
                        </span>
                        <span className="shrink-0 text-right text-[11px] text-slate-400">
                          <span className="block">{a.actor}</span>
                          <span className="block">{stamp(a.at)}</span>
                        </span>
                      </li>
                    ))}
                  </ul>
                )
              )}
            </div>
          </div>

        </div>

        <div className="min-w-0 space-y-3">
          <div className="rounded-2xl border border-slate-100 bg-white p-5">
          <h2 className="mb-2.5 font-semibold text-ink">
            Amount Details ({p.currency})
          </h2>
          <dl className="space-y-2 text-sm">
            <Line k="Payment Amount" v={exact.format(Number(p.amount))} />
            <div className="flex items-baseline justify-between gap-3"
              title="Tax is carried on the folio charges in this ledger, not on the money handed over.">
              <dt className="text-slate-500">Tax</dt>
              <dd className="text-slate-400">on the charges</dd>
            </div>
            {Number(p.refunded) > 0 && (
              <Line k="Already given back"
                v={`− ${exact.format(Number(p.refunded))}`} />
            )}
            {/* The mockup's highlighted row, which it called "Total Paid".
                The number under it is not that: it is the payment less
                anything already given back. On a fully refunded payment the
                two are 4,000 and 0, and "Total Paid 0.00" against a guest who
                did pay is simply false -- it reads as though the payment
                failed. What is actually zero is how much of it the resort is
                still holding, which is also the number that decides how much
                can be refunded. So the label says that. */}
            <div className="-mx-5 flex items-baseline justify-between bg-slate-50 px-5 py-2">
              <dt className="font-semibold text-slate-700">Still held</dt>
              <dd className="text-lg font-semibold text-slate-800">
                {exact.format(refundable)}
              </dd>
            </div>
          </dl>
          {p.folio_balance !== null && (
            /* Named for the folio, because that is what it is. Under a heading
               of Amount Details, directly below this payment's own figures,
               "Outstanding Balance" reads as this payment's -- and it is not.
               A folio with two payments shows the balance of both: a card
               payment refunded to zero still sat under -5,000 outstanding,
               which belonged to a cash payment further down the list, and
               sent the reader to the wrong screen looking for it. */
            <div className="mt-2.5 border-t border-slate-100 pt-2.5">
              <div className="flex items-baseline justify-between gap-3">
                <span className="text-sm text-slate-500">
                  {p.folio_no ? `Folio ${p.folio_no} balance` : 'Folio balance'}
                </span>
                <span className={`text-lg font-semibold ${
                  Number(p.folio_balance) > 0 ? 'text-slate-800'
                    : Number(p.folio_balance) < 0 ? 'text-amber-700'
                      : 'text-emerald-600'}`}>
                  {exact.format(Number(p.folio_balance))}
                </span>
              </div>
              <p className="mt-0.5 text-right text-xs text-slate-400">
                {Number(p.folio_balance) === 0 ? 'Folio fully settled'
                  : Number(p.folio_balance) < 0
                    ? 'Owed back to the guest — across every payment on this folio'
                    : 'Still due from the guest — across every payment on this folio'}
              </p>
            </div>
          )}

          </div>

          <div className="rounded-2xl border border-slate-100 bg-white p-5">
            <div className="mb-3 flex items-center justify-between gap-2">
              <h2 className="flex items-center gap-2 font-semibold text-ink">
                <ShieldCheck size={16} className="text-brand" /> Approval Rules
              </h2>
              {/* Real, not decorative: green while the amount on the form sits
                  inside the governing threshold, amber once it needs a
                  signature. */}
              {/* Only once there is something to judge. Before an action is
                  picked there is no amount, and calling that "Needs Approval"
                  would be a verdict on nothing. */}
              {(chosen || anyAllowed) && (
                <span className={`flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-semibold ${
                  pillWithin ? 'bg-emerald-100 text-emerald-700'
                    : 'bg-amber-100 text-caution'}`}>
                  {pillWithin ? <CheckCircle2 size={11} /> : <Clock size={11} />}
                  {pillWithin ? 'Within Policy' : 'Needs Approval'}
                </span>
              )}
            </div>
            {/* The mockup's two-column read: what the action is called on the
                left, what policy allows on the right. */}
            <dl className="space-y-2 text-sm">
              {c.rules.map((r) => (
                <div key={r.kind} className="grid grid-cols-[7.25rem_1fr] gap-2"
                  title={r.text}>
                  <dt className={`whitespace-nowrap text-[13px] font-medium ${
                    r.allowed ? 'text-slate-700' : 'text-slate-400'}`}>
                    {r.kind === 'void' ? 'Same-day void'
                      : r.kind === 'reversal' ? 'Reverse (Settled)' : 'Refund'}
                  </dt>
                  <dd className={`truncate text-[13px] ${
                    r.allowed ? 'text-slate-600' : 'text-slate-400'}`}>
                    {r.short}
                    {r.blocked_reason && (
                      <span className="block text-xs text-amber-700">
                        {r.blocked_reason}
                      </span>
                    )}
                  </dd>
                </div>
              ))}
            </dl>
            <p className="mt-3 flex items-start gap-1.5 text-xs text-slate-400">
              <Info size={12} className="mt-0.5 shrink-0" />
              All reversals are logged and cannot be deleted.
            </p>
          </div>

          <aside className="space-y-3 rounded-2xl border border-slate-100 bg-white p-5">
            <h2 className="font-semibold text-ink">Reversal / Refund</h2>
            <p className="flex items-start gap-1.5 rounded-lg bg-sky-50 px-3 py-2 text-xs text-sky-800">
              <Info size={12} className="mt-0.5 shrink-0" />
              This posts a reversing entry. The original payment stays on the bill.
            </p>

            {!anyAllowed ? (
              <p className="rounded-lg bg-slate-50 px-3 py-3 text-sm text-slate-500">
                {c.rules[0]?.blocked_reason
                  ?? 'Nothing can be given back on this payment.'}
              </p>
            ) : (
              <>
                <div className="sm:grid sm:grid-cols-[6.5rem_1fr] sm:items-center sm:gap-3">
                  <label className={fieldLabel} htmlFor="rev-kind">
                    Action Type <span className="text-red-500">*</span>
                  </label>
                  <Select id="rev-kind" value={kind} className={input}
                    onChange={(e) => { setKind(e.target.value); setAmount('') }}>
                    <option value="">Select Action</option>
                    {c.rules.map((r: RevRule) => (
                      <option key={r.kind} value={r.kind} disabled={!r.allowed}>
                        {r.kind === 'void' ? 'Void (Same Day)' : r.label}
                        {r.allowed ? '' : ' — ' + r.blocked_reason}
                      </option>
                    ))}
                  </Select>
                  {chosen && (
                    <p className="mt-1 text-[11px] text-slate-500 sm:col-start-2">
                      {KIND_BLURB[chosen.kind]}
                    </p>
                  )}
                </div>

                <div className="sm:grid sm:grid-cols-[6.5rem_1fr] sm:items-center sm:gap-3">
                  <label className={fieldLabel} htmlFor="rev-amount">Amount</label>
                  <input id="rev-amount" type="number" min="0" step="0.01"
                    value={amount} disabled={!chosen?.partial_allowed}
                    placeholder={chosen?.partial_allowed
                      ? 'Whole remaining amount' : ''}
                    onChange={(e) => setAmount(e.target.value)}
                    className={`${input} disabled:bg-slate-50`} />
                  <p className="mt-1 text-[11px] text-slate-400 sm:col-start-2">
                    {chosen?.partial_allowed
                      ? `Leave blank to give back the whole ${exact.format(refundable)}.`
                      : chosen
                        ? `A ${chosen.label.toLowerCase()} always takes the whole ${exact.format(refundable)}.`
                        : `${exact.format(refundable)} is left to give back.`}
                  </p>
                </div>

                <div className="sm:grid sm:grid-cols-[6.5rem_1fr] sm:items-center sm:gap-3">
                  <label className={fieldLabel} htmlFor="rev-reason">
                    Reason <span className="text-red-500">*</span>
                  </label>
                  <Select id="rev-reason" value={reason}
                    onChange={(e) => setReason(e.target.value)} className={input}>
                    <option value="">Pick a reason</option>
                    {c.reasons.map((r) => (
                      <option key={r.value} value={r.value}>{r.label}</option>
                    ))}
                  </Select>
                </div>

                <div className="sm:grid sm:grid-cols-[6.5rem_1fr] sm:gap-3">
                  <label className={fieldLabel} htmlFor="rev-remarks">
                    Remarks <span className="text-red-500">*</span>
                  </label>
                  <textarea id="rev-remarks" rows={3} value={remarks} maxLength={1000}
                    onChange={(e) => setRemarks(e.target.value)}
                    placeholder="What happened, in the words you would use to the guest"
                    className={input} />
                  <p className="mt-1 text-xs text-slate-400 sm:col-start-2">
                    {remarks.trim().length >= REMARKS_MIN
                      ? 'Recorded against the refund and shown on the approval.'
                      : `At least ${REMARKS_MIN} characters — this is what the next
                         person reconciling the till will read.`}
                  </p>
                </div>

                {kind !== '' && (
                  <div className="rounded-xl bg-slate-50 p-3 text-sm">
                    <h3 className="mb-2 font-semibold text-slate-700">What happens</h3>
                    <dl className="space-y-1.5">
                      <Line k={amount === '' && chosen?.partial_allowed
                      ? 'Going back (whole remaining amount)' : 'Going back'}
                      v={`− ${exact.format(going)}`} />
                      {p.folio_balance !== null && (
                        <div className="flex items-baseline justify-between border-t border-slate-200 pt-1.5">
                          <dt className="font-semibold text-slate-700">Folio balance after</dt>
                          <dd className="font-semibold text-slate-800">
                            {exact.format(Number(p.folio_balance) + going)}
                          </dd>
                        </div>
                      )}
                    </dl>
                  </div>
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

                <div className="sm:grid sm:grid-cols-[6.5rem_1fr] sm:items-center sm:gap-3">
                  <span className={fieldLabel}>Approver</span>
                  {/* The mockup lets you pick a person. Approvals here route to
                      a ROLE by policy and anyone holding it may decide, which
                      is what stops a request being parked on someone who is
                      off shift. So this shows who it reaches; it is not a
                      chooser. */}
                  <p className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-600">
                    {!chosen ? 'Pick an action to see who decides'
                      : withinPolicy
                        ? 'Cleared by policy — no approver needed'
                        : approverRoles || 'A manager'}
                  </p>
                </div>

                <div className="flex justify-end gap-2 pt-1">
                  <button onClick={reset}
                    className="rounded-lg border border-slate-200 px-4 py-2.5 text-sm font-medium text-slate-600 hover:bg-slate-50">
                    Cancel
                  </button>
                  <button
                    disabled={missing.length > 0 || !c.can_create || create.isPending}
                    onClick={() => create.mutate()}
                    title={c.can_create ? undefined
                      : 'You do not have permission to raise a reversal.'}
                    className="flex items-center justify-center gap-2 rounded-lg bg-brand px-4 py-2.5 text-sm font-semibold text-white hover:bg-brand-dark disabled:opacity-40">
                    {create.isPending ? <Loader2 size={15} className="animate-spin" />
                      : <Undo2 size={15} />}
                    {withinPolicy ? 'Post request' : 'Submit for Approval'}
                  </button>
                </div>
              </>
            )}
          </aside>
        </div>
      </div>


      {withdrawing && (
        <WithdrawDialog row={withdrawing} onClose={() => setWithdrawing(null)}
          pending={withdraw.isPending}
          onSubmit={(r) => withdraw.mutate({ id: withdrawing.id, remarks: r })} />
      )}
    </div>
  )
}


/**
 * The mockup's Print Receipt. Everything on it is data this screen already
 * holds, so nothing has to be invented — and there is no Email Receipt beside
 * it because there is no mail transport to send one with.
 */
function printReceipt(
  p: RevContext['payment'], receiptNo: string, propertyName: string,
) {
  const esc = (v: unknown) => String(v ?? '—')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  const given = Number(p.refunded)
  const w = window.open('', '_blank', 'width=420,height=680')
  if (!w) return
  w.document.write(`<!doctype html><meta charset="utf-8"><title>${esc(receiptNo)}</title>
<style>
 body{font:13px/1.5 system-ui,sans-serif;color:#1e293b;padding:24px;max-width:360px}
 h1{font-size:16px;margin:0 0 2px} .sub{color:#64748b;font-size:12px;margin:0 0 16px}
 table{width:100%;border-collapse:collapse;margin:12px 0}
 td{padding:4px 0;border-bottom:1px solid #d5dde8} .r{text-align:right}
 .k{color:#64748b} .tot td{font-weight:700;border-top:2px solid #1e293b;border-bottom:none}
 .foot{color:#46566d;font-size:11px;margin-top:20px}
</style>
<h1>${esc(propertyName)}</h1>
<p class="sub">Payment receipt &middot; ${esc(receiptNo)}</p>
<table>
 <tr><td class="k">Date</td><td class="r">${esc(stamp(p.received_at))}</td></tr>
 <tr><td class="k">Guest</td><td class="r">${esc(p.guest_name)}</td></tr>
 <tr><td class="k">Booking</td><td class="r">${esc(p.reservation_number)}</td></tr>
 <tr><td class="k">Folio</td><td class="r">${esc(p.folio_no)}</td></tr>
 <tr><td class="k">Room</td><td class="r">${esc(p.room_code)}</td></tr>
 <tr><td class="k">Method</td><td class="r">${esc(p.method_label)}</td></tr>
 <tr><td class="k">Reference</td><td class="r">${esc(p.reference)}</td></tr>
 <tr><td class="k">Received by</td><td class="r">${esc(p.cashier)}</td></tr>
</table>
<table>
 <tr><td>Payment received</td><td class="r">${exact.format(Number(p.amount))}</td></tr>
 ${given > 0
    ? `<tr><td>Given back</td><td class="r">- ${exact.format(given)}</td></tr>` : ''}
 <tr class="tot"><td>Net received</td><td class="r">${
    exact.format(Number(p.amount) - given)}</td></tr>
</table>
<p class="foot">This receipt was produced by the property management system and
records a payment held in its ledger.</p>`)
  w.document.close()
  w.focus()
  w.print()
}

/* ----------------------------------------------------------------- bits --- */
/**
 * A field as the mockup draws it: label on the left in grey, value on the
 * right in dark, both on one line and aligned down the column. Stacking the
 * label above the value made the card read as a list of headings.
 */
function Fact({ k, v, hint }: { k: string; v: string; hint?: string }) {
  return (
    // The label column gives way before the value does. At 8.75rem fixed it
    // could not, so in the narrow left-hand card every long value lost its
    // tail: "12 Sep 2026 – 13 Se…", "mock_txn_4e…". An ellipsis is fine on a
    // name you can recognise from its first half; it is not fine on a
    // transaction id, which is only useful entire.
    <div className="grid grid-cols-[minmax(6rem,8.75rem)_1fr] items-baseline gap-3">
      <dt className="text-sm text-slate-400">{k}</dt>
      <dd className={`min-w-0 break-words text-sm ${
        v === '—' ? 'text-slate-400' : 'font-medium text-slate-800'}`}
        title={hint ?? v}>
        {v}
      </dd>
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

function WithdrawDialog({ row, onClose, onSubmit, pending }: {
  row: RevRow; onClose: () => void; pending: boolean
  onSubmit: (remarks: string) => void
}) {
  const [remarks, setRemarks] = useState('')
  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-slate-900/30 p-4">
      <div className="w-full max-w-md rounded-2xl bg-white p-5 shadow-xl">
        <div className="flex items-start justify-between">
          <h2 className="text-lg font-semibold text-ink">Withdraw request</h2>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600">
            <X size={18} />
          </button>
        </div>
        <p className="mt-3 text-sm text-slate-600">
          No money has moved on this {row.kind_label.toLowerCase()}, so nothing
          is unwound. The record of having asked stays on the payment.
        </p>
        <label className="mt-4 block text-xs text-slate-500">
          Why is it being withdrawn? *
          <textarea rows={3} value={remarks} maxLength={1000} autoFocus
            onChange={(e) => setRemarks(e.target.value)} className={`mt-1 ${input}`} />
        </label>
        <div className="mt-5 flex justify-end gap-2">
          <button onClick={onClose}
            className="rounded-lg border border-slate-200 px-4 py-2 text-sm text-slate-600 hover:bg-slate-50">
            Keep it
          </button>
          <button disabled={remarks.trim().length < 5 || pending}
            onClick={() => onSubmit(remarks.trim())}
            className="flex items-center gap-1.5 rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white hover:bg-brand-dark disabled:opacity-40">
            {pending ? <Loader2 size={14} className="animate-spin" /> : <X size={14} />}
            Withdraw it
          </button>
        </div>
      </div>
    </div>
  )
}

/** Voids, refunds and reversals on one payment, with the approval trail. */
function exportReversals(c: RevContext): void {
  downloadCsv(
    datedName(`reversals-${c.payment.reference ?? 'payment'}`),
    ['Requested', 'Kind', 'Status', 'Amount', 'Reason', 'Remarks',
     'Requested by', 'Approval', 'Decided by', 'Decided at',
     'Decision comment', 'Posted at', 'Posted by'],
    c.reversals.map((r) => [
      r.created_at, r.kind_label, r.status, r.amount, r.reason_label,
      r.remarks, r.created_by_name,
      r.approval_status ?? (r.approval_required ? 'required' : 'not required'),
      r.decided_by, r.decided_at, r.decision_comment,
      r.posted_at, r.posted_by_name,
    ]),
  )
}
