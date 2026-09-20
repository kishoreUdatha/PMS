/**
 * The folio workspace: rooms on the left, the ledger in the middle, what you
 * can do about it on the right.
 *
 * Built to the layout in the reference, re-skinned to the brand — the
 * reference is indigo, this application is teal (#007A85), and the type is
 * Inter throughout, so what is borrowed is the arrangement, not the palette.
 *
 * Three decisions worth naming:
 *
 * **The ledger reads newest first, and the balance still means what it says.**
 * This read oldest-first for a while, on the reasoning that a running account
 * only makes sense accumulating downward. That is true of the arithmetic and
 * false of the reading: on a folio with thirty lines the row people came to
 * see -- the last thing posted, and what is owed now -- was off the bottom of
 * the screen. A bank statement solves this the same way, and the trick is the
 * same: the running balance is still computed oldest-first, so every row keeps
 * the balance the account actually stood at after that entry. Only the order
 * they are drawn in is reversed. The top row therefore shows the balance now,
 * which is the number being looked for.
 *
 * **There is no "User" column.** The reference has one; this system does not
 * record who posted a charge, and a column filled with the current operator's
 * name — or with "admin" — would be a fiction on a financial document. When
 * the ledger starts carrying an actor, the column can be real.
 *
 * **Only actions that exist are offered.** The reference shows "Folio
 * Operations" and "More" dropdowns and a "Post Folio" button. Buttons that
 * open nothing teach people the screen is broken, so what is here is what
 * works: take a payment, post a charge, adjust, and print.
 */
import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate, useLocation } from 'react-router-dom'
import {
  ArrowLeftRight, BedDouble, ChevronDown, ChevronRight, CreditCard, FileText,
  Clock, Gift, ListFilter, Percent, Plus, Receipt, Scissors, Search, Split,
  Loader2, Mail, Pencil, Printer, Undo2, UploadCloud, UtensilsCrossed,
  Wallet, X,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { fmtDateTime } from '../lib/dates'
import {
  emailPaymentReceipt, listReservationFolios, openPaymentReceipt,
  type FolioRow, type ReservationFull,
} from '../api'
import ActionsMenu from './ActionsMenu'
import AddChargeDialog from './AddChargeDialog'
import AddPaymentDialog from './AddPaymentDialog'
import ApplyDiscountDialog from './ApplyDiscountDialog'
import SplitButton from './SplitButton'
import FolioOpDialog, { type FolioOp } from './FolioOps'
import type { ActionItem } from './menu'
import NewFolioDialog from './NewFolioDialog'
import { useOrgId } from '../hooks/useProperty'
import { useMethodLabel } from '../lib/paymentMethods'

const money = (v: string | number, cur = 'INR') =>
  new Intl.NumberFormat('en-IN', {
    style: 'currency', currency: cur, minimumFractionDigits: 2,
  }).format(Number(v))

/** How a cancelled line is drawn: ruled through, and greyed so it recedes
 *  behind the lines that still count. Applied to every cell of the row. */
const dead = 'text-slate-400 line-through decoration-slate-400'

/** What the ledger is narrowed to. 'card' is not a kind of entry but a kind
 *  of instrument — it was a tab of its own until it turned out to be a filter
 *  over this same list. */
type Filter = '' | Txn['kind'] | 'card'

type Txn = {
  id: string
  at: string
  kind: 'charge' | 'payment' | 'reversal'
  description: string
  /** How the money moved, where a single payment matches this entry. */
  instrument: string | null
  /** What a person typed against the payment, where a person typed anything.
   *  Never the gateway's own transaction id — see `ledger`. */
  reference: string | null
  /** The gateway's id, for the tooltip. Machine-readable, not desk-readable. */
  gatewayRef: string | null
  /** The payment behind this credit, when one matches. Lets the row offer
   *  the reversal that used to live on a tab of its own. */
  paymentId: string | null
  /** Which bill the line is on, and who put it there. */
  folioNo: string | null
  user: string | null
  /** Signed: a charge raises the balance, a payment lowers it. */
  amount: number
  balance: number
  /** This payment has been given back in full, so the line is struck through.
   *  Only ever true on a payment row, and only for the whole amount: a part
   *  refund leaves a payment that still settles part of the bill. */
  voided: boolean
  /** A void or refund is raised on this payment and waiting to be posted.
   *  Not struck through -- nothing has moved yet -- but the row says so, and
   *  its menu sends you to finish it rather than offering to start again. */
  pending: boolean
  /** Which kind is open, so the menu names the one that actually is. */
  pendingKind: string | null
}

/** The folio ledger, oldest first, with a running balance.
 *
 * Built from `r.charges` alone, which despite its name is *every* folio entry
 * — debits and credits both. Merging it with `r.payments` listed every payment
 * twice: once as the credit entry it is, and again from the payments array, so
 * an ₹8,000 card payment appeared as two ₹8,000 rows. The browser showed it
 * immediately; reading the type name did not.
 *
 * `r.payments` is still used, but only to name the instrument: the entry
 * records "Payment", and the payments array is what knows it was a card.
 *
 * The two are joined on `source_id`, which is the payment's own id, carried on
 * the credit entry that settled it. That sounds obvious and it is; it is worth
 * a note only because this spent a while joining them on the *amount* instead,
 * matching a credit to a payment when exactly one payment of that figure
 * existed and giving up when several did. It gave up more often than anyone
 * expected: a folio holding a ₹1,000 cash payment and a ₹1,000 wallet payment
 * showed the instrument for neither, because the rule could not tell which row
 * was which and would not guess. The column simply went blank, which is how it
 * was noticed.
 *
 * The id was in the database the whole time — `folio_entries.source_id` — and
 * the reservation endpoint was not selecting it. Adding one column to that
 * query deleted the entire heuristic.
 */
function ledger(r: ReservationFull, folioId: string | null): Txn[] {
  const byId = new Map(r.payments.map((p) => [p.id, p]))

  let running = 0
  // Sorted and accumulated oldest-first, then reversed for display. The order
  // of these two steps is the whole point: reverse before accumulating and
  // every balance in the column is wrong.
  return r.charges
    // One folio at a time. The endpoint now returns every entry on the
    // booking, and a running balance computed across two parties' accounts is
    // a number that belongs to neither of them.
    .filter((c) => folioId === null || c.folio_id === folioId)
    .slice()
    .sort((a, b) => (a.posted_at || a.business_date)
      .localeCompare(b.posted_at || b.business_date))
    .map((c) => {
      const credit = c.entry_type === 'credit'
      const amount = (credit ? -1 : 1) * Number(c.amount)
      running += amount
      const paid = credit && c.source_id ? byId.get(c.source_id) : undefined
      return {
        id: c.id,
        at: c.posted_at || c.business_date,
        // A refund debit is a reversal on this screen even though it carries no
        // `reversal_of_id`. Without this it fell through to the last branch and
        // a ₹2,000 void came back looking like a ₹2,000 charge, in the same
        // blue as a spa bill.
        kind: (c.is_reversal || c.is_refund
          ? 'reversal' : credit ? 'payment' : 'charge') as Txn['kind'],
        description: c.description,
        folioNo: c.folio_no,
        user: c.posted_by,
        instrument: paid?.method ?? null,
        // A reference that is character-for-character the gateway's own
        // transaction id is not a reference, it is plumbing: nobody typed
        // "mock_txn_6df63fe7a9bf44b9" and no one at a desk can do anything
        // with it. It was being printed under every card and wallet payment,
        // two lines of machine noise in the column people actually read.
        //
        // Where the two differ, a person wrote it -- "night drinks", "Test" --
        // and that is worth its line. So the row shows what was typed, and the
        // gateway id moves to the tooltip, where the one person a month who is
        // chasing a chargeback can still get at it.
        reference: paid && paid.reference !== paid.provider_transaction_id
          ? paid.reference : null,
        gatewayRef: paid?.provider_transaction_id ?? null,
        paymentId: paid?.id ?? null,
        amount,
        balance: running,
        voided: paid?.fully_reversed ?? false,
        pending: (paid?.reversal_pending ?? false)
          && !(paid?.fully_reversed ?? false),
        pendingKind: paid?.reversal_pending_kind ?? null,
      }
    })
    .reverse()
}

export default function FolioWorkspace({ r, onPosted }: {
  r: ReservationFull
  onPosted?: () => void
}) {
  const nav = useNavigate()
  // Where to send people back to once the reversal is raised.
  const loc = useLocation()
  const cur = r.financials.currency || r.currency
  const [picked, setPicked] = useState<string | null>(null)
  const orgId = useOrgId()
  const methodLabel = useMethodLabel(r.property_id)
  const [paying, setPaying] = useState(false)
  const [adding, setAdding] = useState(false)
  const [discounting, setDiscounting] = useState(false)
  const [q, setQ] = useState('')
  const [kind, setKind] = useState<Filter>('')
  const [op, setOp] = useState<FolioOp | null>(null)
  const [newFolio, setNewFolio] = useState(false)
  // Which rows are ticked, and what the bulk bar is doing about it.
  const [chosen, setChosen] = useState<Set<string>>(new Set())
  const [bulkBusy, setBulkBusy] = useState('')
  const [toast, setToast] = useState('')

  // Listed, because otherwise opening one is a button that appears to do
  // nothing: the rail shows rooms, the ledger shows the first folio, and a
  // second folio sat in the database with nowhere on the screen saying so.
  // Only drawn when there is more than one — a booking with a single folio
  // does not need a list of it.
  const folios = useQuery({
    queryKey: ['reservation-folios', r.id],
    queryFn: () => listReservationFolios(r.id, r.property_id),
  })
  const allFolios = folios.data ?? []

  // The master is the folio without a parent; the API orders masters first, so
  // the first row is it. Selection falls back to the master until somebody
  // picks otherwise, which keeps a single-folio booking behaving exactly as it
  // did.
  // Every folio without a parent, not just the first. An earlier version took
  // `find` here and rendered that one master plus every child — so a second
  // master, which is exactly what a folio opened before the parent column
  // existed is, rendered nowhere at all. A folio that holds money and appears
  // on no screen is the worst thing this rail can do, so the grouping is over
  // the whole list and anything orphaned still gets drawn.
  const masters = allFolios.filter((f) => f.parent_folio_id === null)
  const childrenOf = (id: string) =>
    allFolios.filter((f) => f.parent_folio_id === id)
  const parentIds = new Set(masters.map((f) => f.id))
  const orphans = allFolios.filter((f) =>
    f.parent_folio_id !== null && !parentIds.has(f.parent_folio_id))

  const shownId = picked ?? masters[0]?.id ?? r.financials.folio_id ?? null
  const shown = allFolios.find((f) => f.id === shownId) ?? null
  const hasTree = allFolios.length > 1
  const all = useMemo(() => ledger(r, shownId), [r, shownId])

  // What the dialogs say they are posting to. A booking with one folio reads
  // as it always did; one with two names the party, because "add charge" with
  // no indication of which bill it lands on is how the guest's bar tab ends up
  // on the company's invoice.
  // Both, always. The booking's totals live in one query and the per-folio
  // balances in another, and a posting moves both.
  const refresh = () => { void folios.refetch(); onPosted?.() }

  // What a single row offers. Deliberately different for a payment and a
  // charge, because they are not the same kind of thing: a payment can be
  // voided and receipted, a charge can only be corrected.
  //
  // Nothing here edits a posted line in place, and "Edit" is named "Adjust"
  // for that reason. A ledger you can retype is a ledger nobody can trust six
  // months later — the correction has to be its own entry, with its own
  // author and reason, which is exactly what the adjustment screen produces.
  const rowActions = (t: Txn): ActionItem[] => {
    const items: ActionItem[] = []
    if (t.paymentId) {
      // Offered only while there is something left to void. The API refuses a
      // second one ("The whole payment has already been given back"), but it
      // refuses on the next screen — so the menu used to send people away from
      // the folio to be told no. A dead payment keeps its receipts and loses
      // the one action that cannot work.
      const here = encodeURIComponent(loc.pathname + loc.search)
      if (t.pending) {
        // Already raised and not yet posted. Offering a fresh action here sent
        // people to a screen with all three refused, and said nothing about the
        // request they had already made -- which is how one sat unposted while
        // the folio looked untouched. The item names the real next step, names
        // the kind that is actually open rather than assuming a void, and lands
        // on the tab that has the button.
        items.push({
          label: `Finish the ${t.pendingKind ?? 'reversal'}`,
          icon: Clock,
          group: 'money',
          tone: 'danger',
          description: 'Raised already — waiting to be posted',
          onSelect: () => nav(`/payments/${t.paymentId}/reverse`
            + `?tab=reversals&back=${here}`),
        })
      } else if (!t.voided) {
        // Named for what the destination offers, which is all three: a void, a
        // refund and a reversal. It used to say "Void payment", so a cashier
        // refunding a settled card payment had to click the word "Void" to get
        // there -- and no kind is preselected from here, because which of the
        // three is even allowed depends on rules only that screen has.
        items.push({
          label: 'Refund or reverse',
          icon: Undo2,
          group: 'money',
          tone: 'danger',
          description: 'Void, refund or reverse — with a reason',
          onSelect: () => nav(`/payments/${t.paymentId}/reverse?back=${here}`),
        })
      }
      items.push({
        label: 'Print receipt',
        icon: Printer,
        group: 'doc',
        onSelect: () => void openPaymentReceipt(r.property_id, t.paymentId!),
      }, {
        label: 'Send receipt',
        icon: Mail,
        group: 'doc',
        description: r.guest?.email ? `To ${r.guest.email}` : 'No guest email',
        disabled: !r.guest?.email,
        onSelect: () => void sendReceipt([t.paymentId!]),
      })
    } else {
      items.push({
        label: 'Adjust this line',
        icon: Pencil,
        group: 'money',
        description: 'A correcting entry, not an overwrite',
        disabled: !folioId,
        onSelect: () => nav(`/payments/folios/${folioId}/adjust`),
      })
    }
    return items
  }

  async function sendReceipt(paymentIds: string[]) {
    setBulkBusy('send')
    let sent = 0
    try {
      // One at a time and sequential. Firing a dozen at an SMTP server at once
      // is how a property gets rate-limited, and a partial failure is easier to
      // report when you know which one stopped.
      for (const id of paymentIds) {
        await emailPaymentReceipt(r.property_id, id)
        sent += 1
      }
      setToast(`${sent} receipt${sent === 1 ? '' : 's'} sent to `
        + `${r.guest?.email ?? 'the guest'}.`)
    } catch (e) {
      const ax = e as { response?: { data?: { detail?: string } } }
      setToast(ax?.response?.data?.detail
        ?? `Sent ${sent} of ${paymentIds.length}; the rest failed.`)
    } finally { setBulkBusy('') }
  }

  async function printReceipts(paymentIds: string[]) {
    setBulkBusy('print')
    try {
      for (const id of paymentIds) {
        await openPaymentReceipt(r.property_id, id)
      }
    } finally { setBulkBusy('') }
  }

  const folioLabel = shown
    ? `${shown.folio_no ?? r.number} · ${
      shown.sharer_name ?? TYPE_LABELS[shown.type] ?? shown.type}`
    : `${r.number} · guest folio`
  const folioId = shownId

  // The charge button's variants. Two groups, because they are two different
  // kinds of act: the first three change what this folio says, the last two
  // change what the guest was sold — which re-prices the stay and can be
  // refused on availability, so they belong to Modify Stay, which quotes
  // before it commits. Saying so on the item is cheaper than letting somebody
  // find out after they have clicked.
  const chargeItems: ActionItem[] = [
    {
      label: 'Apply discount',
      icon: Percent,
      group: 'folio',
      description: 'Takes an amount off the folio',
      disabled: !folioId,
      hint: folioId ? undefined : 'Nothing has been posted yet.',
      onSelect: () => setDiscounting(true),
    },
    {
      label: 'Adjustment',
      icon: FileText,
      group: 'folio',
      description: 'With evidence and approval',
      disabled: !folioId,
      onSelect: () => nav(`/payments/folios/${folioId}/adjust`),
    },
    {
      label: 'Upload files',
      icon: UploadCloud,
      group: 'folio',
      description: 'Attach evidence to an adjustment',
      disabled: !folioId,
      onSelect: () => nav(`/payments/folios/${folioId}/adjust`),
    },
    {
      label: 'Meal plan',
      icon: UtensilsCrossed,
      group: 'stay',
      description: 'Re-prices the stay',
      onSelect: () => nav(`/reservations/${r.id}/modify`),
    },
    {
      label: 'Inclusion',
      icon: Gift,
      group: 'stay',
      description: 'Re-prices the stay',
      onSelect: () => nav(`/reservations/${r.id}/modify`),
    },
  ]

  // Filtering never recomputes the balance. The running total belongs to the
  // whole account, and a balance that changes when you search is a balance
  // nobody can trust -- so the column keeps the figure the entry actually had.
  const rows = all.filter((t) =>
    (kind === '' || (kind === 'card' ? t.instrument === 'card' : t.kind === kind))
    && (q.trim() === '' || [t.description, t.instrument, t.reference,
      t.gatewayRef, t.folioNo, t.user]
      .filter(Boolean).join(' ').toLowerCase().includes(q.trim().toLowerCase())))

  // What the tick boxes can act on, derived from what is actually on screen:
  // filter the ledger to Charges and the bulk bar empties, which is right --
  // acting on rows you cannot see is how the wrong payment gets voided.
  // Everything on screen can be ticked. An earlier version allowed payments
  // only -- the reasoning being that a charge has no receipt to print -- which
  // was true and beside the point: the thing a desk most often wants to do to
  // a handful of charges is move them onto somebody else's folio, which is the
  // company-pays-the-room, guest-pays-the-dinner split. Selecting the four
  // restaurant lines and moving them in one go *is* the workflow.
  const chosenRows = rows.filter((t) => chosen.has(t.id))
  const chosenPayments = chosenRows
    .filter((t) => t.paymentId)
    .map((t) => t.paymentId!)
  // Only debits can be moved. A payment belongs to the folio it was taken
  // against, and shifting it would make that folio look unpaid.
  const chosenCharges = chosenRows.filter((t) => t.amount > 0).map((t) => t.id)

  // Reversals only. Counting every credit made this the payments total under
  // another name — an ₹11,224 folio that had been paid and refunded showed
  // "Adjustments ₹11,224" when nothing had been adjusted at all. A reversal
  // is the one thing in this payload that genuinely is an adjustment.
  const adjustments = r.charges
    .filter((c) => c.is_reversal)
    .reduce((n, c) => n + Number(c.amount), 0)

  // lg, not xl. A 1920 screen at Windows' 150% display scaling is a 1277 CSS
  // viewport, and `xl` fires at 1280 — so the commonest laptop setup landed
  // three pixels short and got a single stacked column. The rail is 288px and
  // the ledger has room from 1024 up.
  //
  // Two columns, not three. The right rail held Quick actions — the same four
  // buttons as the toolbar — and a Stay summary whose facts are in the header
  // strip and on Stay Information. What is left is the thing the screen is
  // for: the rooms, and the account. The ledger gets the width back, so
  // Balance stops fighting for space.
  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,17rem)_minmax(0,1fr)]">
      {/* ---------------------------------------------------- rooms ------ */}
      <aside className="rounded-2xl border border-slate-100 bg-white">
        <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
          <h2 className="text-sm font-semibold text-ink">Room / Folio</h2>
          {/* Opens another folio on this booking. It used to send people to
              the room rack, which is a different job under a heading that
              says "Folio" — and it disappeared entirely once a room was
              assigned, so on most bookings this panel had no + at all. */}
          <button onClick={() => setNewFolio(true)}
            title="Open another folio on this booking"
            aria-label="New folio"
            className="rounded-lg bg-brand-light p-1.5 text-brand hover:bg-brand/10">
            <Plus size={15} />
          </button>
        </div>

        <div className="p-3">
          {r.units.length === 0 ? (
            <p className="px-1 py-6 text-center text-xs text-slate-400">
              No rooms on this booking.
            </p>
          ) : (
            // Grouped by room type, which is how a desk reads a multi-room
            // booking — "the two deluxes and the suite", not five rows.
            Object.entries(
              r.units.reduce<Record<string, typeof r.units>>((acc, u) => {
                const k = u.room_type ?? 'Room type not set'
                ;(acc[k] ||= []).push(u)
                return acc
              }, {}),
            ).map(([type, units]) => (
              <RoomGroup key={type} type={type} units={units}
                guest={r.guest?.full_name ?? null} number={r.number} />
            ))
          )}
        </div>

        {/* Always a section, even with nothing in it. A booking has no folio
            until something is posted to it, and the version that rendered
            nothing in that case left a "+" in the header with no indication of
            what it would add — and no answer to "which folio am I looking
            at?", which is the question the panel is named after. */}
        <div className="border-t border-slate-100 px-3 py-3">
          <p className="mb-1.5 px-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
            {allFolios.length === 1 ? 'Folio' : 'Folios'}
          </p>
          {allFolios.length === 0 && (
            <p className="px-1 py-2 text-xs text-slate-500">
              {folios.isLoading
                ? 'Loading…'
                : 'No folio yet — one opens itself when the first charge or '
                  + 'payment is posted.'}
            </p>
          )}
          {allFolios.length > 0 && (
            <>
            {/* Clickable, because a folio is an account and not a label. A
                company books the room and agrees to the room; the guest eats,
                drinks and wants that on their own GSTIN. Those are two bills,
                and the desk's question is always about one of them at a time —
                so picking one here shows that folio's lines, its balance, and
                posts to it.

                Children are indented under their master rather than listed
                flat. The indent is the whole of the relationship: this bill
                hangs off that one. */}
            {masters.map((m) => (
              <div key={m.id}>
                <FolioPick f={m} cur={cur} selected={shownId === m.id}
                  fallbackName={r.guest?.full_name ?? null}
                  showMasterTag={hasTree && childrenOf(m.id).length > 0}
                  onPick={() => setPicked(m.id)} />
                {childrenOf(m.id).map((f) => (
                  <FolioPick key={f.id} f={f} cur={cur} child
                    selected={shownId === f.id}
                    fallbackName={r.guest?.full_name ?? null}
                    onPick={() => setPicked(f.id)} />
                ))}
              </div>
            ))}
            {/* A child whose parent is not on this booking. It should not
                happen and it is drawn anyway, because the alternative is a
                folio with money on it that no screen shows. */}
            {orphans.map((f) => (
              <FolioPick key={f.id} f={f} cur={cur}
                selected={shownId === f.id}
                fallbackName={r.guest?.full_name ?? null}
                showMasterTag={false}
                onPick={() => setPicked(f.id)} />
            ))}
            </>
          )}
        </div>

        {/* The booking's position, whichever folio is open. The figure for
            the folio you are looking at is on its own row above; this is the
            stay, both parties together, which is what somebody chasing a
            balance actually needs. */}
        <div className="border-t border-slate-100 px-4 py-3">
          <div className="flex items-baseline justify-between">
            <span className="text-sm text-slate-500">
              Total{allFolios.length > 1 && (
                <span className="text-xs text-slate-400"> · booking</span>
              )}
            </span>
            <span className="text-base font-bold text-ink">
              {money(r.financials.total_charges, cur)}
            </span>
          </div>
          <div className="mt-1 flex items-baseline justify-between">
            <span className="text-sm text-slate-500">
              Balance{allFolios.length > 1 && (
                <span className="text-xs text-slate-400"> · booking</span>
              )}
            </span>
            <span className={`text-base font-bold ${
              Number(r.financials.balance_due) > 0
                ? 'text-caution' : 'text-emerald-700'}`}>
              {money(r.financials.balance_due, cur)}
            </span>
          </div>
        </div>
      </aside>

      {/* ---------------------------------------------------- ledger ----- */}
      <section className="min-w-0 rounded-2xl border border-slate-100 bg-white">
        <div className="flex flex-wrap items-center gap-2 border-b border-slate-100 p-3">
          {/* Three buttons, eleven actions, and nothing called "More".
              Every item sits under the heading it belongs to: money in, money
              on, money moved between folios. The old fourth control was a
              menu named after nothing, and Transfer, Split and Cut — daily
              work — were inside it.

              Print / Send used to sit here as well as in the page header,
              which is the same action twice on one screen. The header keeps
              it: it prints the booking, not whatever the toolbar is above. */}
          <SplitButton primary icon={Plus} label="Add payment"
            onClick={() => setPaying(true)}
            items={[{
              // Named for where it goes. It used to say "Refund or reverse",
              // which is the one thing the adjustment screen cannot do: that
              // screen corrects a CHARGE (correction, discount, allowance) and
              // never touches a payment. A refund lives on a payment's own row,
              // because it needs a payment to act on.
              label: 'Adjust a charge',
              icon: Pencil,
              description: 'Correction, discount or allowance',
              disabled: !folioId,
              hint: folioId ? undefined : 'Nothing has been posted yet.',
              onSelect: () => nav(`/payments/folios/${folioId}/adjust`),
            }, {
              label: 'Card payments only',
              icon: CreditCard,
              description: 'Filters the ledger to card',
              onSelect: () => setKind('card'),
            }]} />

          <SplitButton icon={Receipt} label="Add charge"
            onClick={() => setAdding(true)}
            items={chargeItems} />

          <SplitButton icon={ArrowLeftRight} label="Transfer"
            disabled={!folioId}
            hint={folioId ? undefined : 'Nothing has been posted yet.'}
            onClick={() => setOp('transfer')}
            items={[{
              label: 'Split folio',
              icon: Split,
              group: 'move',
              description: 'Opens a second folio',
              disabled: !folioId,
              onSelect: () => setOp('split'),
            }, {
              label: 'Cut folio',
              icon: Scissors,
              group: 'move',
              description: 'Closes this one, opens the next',
              disabled: !folioId,
              onSelect: () => setOp('cut'),
            }]} />

          {/* The only flexible thing on the row. Three buttons, a search
              field and a filter came to more than the column at laptop
              width, and the row wrapped — so the search shrinks instead,
              down to a floor where it is still worth typing into. Fixed
              widths were the version of this that broke on the next screen
              size along. */}
          <div className="ml-auto flex min-w-0 flex-1 items-center justify-end gap-2">
            <label className="relative min-w-0 max-w-[11rem] flex-1">
              <Search size={14}
                className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
              <input value={q} onChange={(e) => setQ(e.target.value)}
                placeholder="Search transactions…"
                className="w-full rounded-xl border border-slate-200 py-2 pl-8 pr-3 text-sm outline-none focus:border-brand" />
            </label>
            <label className="relative">
              <ListFilter size={14}
                className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
              <select value={kind}
                onChange={(e) => setKind(e.target.value as typeof kind)}
                className="w-36 shrink-0 appearance-none rounded-xl border border-slate-200 py-2 pl-8 pr-7 text-sm text-slate-600 outline-none focus:border-brand">
                {/* Room Charges and Credit Card were tabs of their own,
                    each a filter over this same ledger rendered as a second
                    table. They are the filter now — same answer, no second
                    table to keep in step with this one. */}
                <option value="">All entries</option>
                <option value="charge">Charges</option>
                <option value="payment">Payments</option>
                <option value="card">By card</option>
                <option value="reversal">Reversals</option>
              </select>
            </label>
          </div>
        </div>

        {/* Appears only when something is ticked, and sits between the
            toolbar and the table so it cannot be missed and cannot be
            mistaken for a permanent control. It says what will happen and to
            how many — "Print 3 receipts", not "Print". */}
        {chosen.size > 0 && (
          <div className="flex flex-wrap items-center gap-2 border-b border-brand/20 bg-brand-light px-3 py-2.5">
            {/* Says what is actually ticked, by kind, because the buttons
                beside it do not all apply to all of it: you can move charges
                and you can receipt payments, and a bar reading "7 selected"
                would leave somebody guessing which of the two it meant. */}
            <span className="text-sm font-semibold text-brand">
              {[
                chosenCharges.length
                  && `${chosenCharges.length} charge${chosenCharges.length === 1 ? '' : 's'}`,
                chosenPayments.length
                  && `${chosenPayments.length} payment${chosenPayments.length === 1 ? '' : 's'}`,
              ].filter(Boolean).join(' · ')} selected
            </span>
            <span className="ml-auto flex flex-wrap items-center gap-2">
              {/* The reason charges are selectable at all. Opens the transfer
                  dialog with these lines already ticked, so picking the four
                  restaurant charges out of the ledger is not then repeated
                  inside the dialog. */}
              {chosenCharges.length > 0 && (
              <button onClick={() => setOp('transfer')}
                disabled={!folioId}
                title="Move these lines to another folio"
                className="flex items-center gap-1.5 rounded-lg border border-brand/30 bg-white px-3 py-1.5 text-sm font-semibold text-brand hover:bg-brand-light disabled:opacity-50">
                <ArrowLeftRight size={14} />
                Move {chosenCharges.length} to folio
              </button>
              )}
              {chosenPayments.length > 0 && (<>
              <button onClick={() => void printReceipts(chosenPayments)}
                disabled={bulkBusy !== ''}
                className="flex items-center gap-1.5 rounded-lg border border-brand/30 bg-white px-3 py-1.5 text-sm font-semibold text-brand hover:bg-brand-light disabled:opacity-50">
                {bulkBusy === 'print'
                  ? <Loader2 size={14} className="animate-spin" />
                  : <Printer size={14} />}
                Print {chosenPayments.length} receipt
                {chosenPayments.length === 1 ? '' : 's'}
              </button>
              <button onClick={() => void sendReceipt(chosenPayments)}
                disabled={bulkBusy !== '' || !r.guest?.email}
                title={r.guest?.email
                  ? `Send to ${r.guest.email}`
                  : 'This booking has no guest email.'}
                className="flex items-center gap-1.5 rounded-lg border border-brand/30 bg-white px-3 py-1.5 text-sm font-semibold text-brand hover:bg-brand-light disabled:opacity-50">
                {bulkBusy === 'send'
                  ? <Loader2 size={14} className="animate-spin" />
                  : <Mail size={14} />}
                Send
              </button>
              </>)}
              <button onClick={() => setChosen(new Set())}
                className="rounded-lg p-1.5 text-brand/70 hover:bg-white"
                aria-label="Clear selection">
                <X size={15} />
              </button>
            </span>
          </div>
        )}

        {toast && (
          <p className="flex items-start justify-between gap-3 border-b border-slate-100 bg-slate-50 px-4 py-2.5 text-sm text-slate-600">
            {toast}
            <button onClick={() => setToast('')} aria-label="Dismiss"
              className="shrink-0 rounded p-0.5 text-slate-400 hover:text-slate-600">
              <X size={14} />
            </button>
          </p>
        )}

        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
              <tr>
                {/* Six columns did not fit beside two rails, and the one
                    that fell off the edge was Balance — the single figure the
                    desk is looking for. The reference lives under the
                    description instead, where it is still findable and costs
                    no width. */}
                <th className="w-9 px-3 py-3">
                  {/* Ticks only what can be acted on. A select-all that also
                      ticks the charges would leave the bulk bar saying
                      "0 payments selected" beside eight ticked rows. */}
                  <input type="checkbox" className="accent-brand"
                    aria-label="Select all rows"
                    checked={rows.length > 0
                      && rows.every((t) => chosen.has(t.id))}
                    onChange={(e) => setChosen(e.target.checked
                      ? new Set(rows.map((t) => t.id))
                      : new Set())} />
                </th>
                <Th>Date &amp; time</Th>
                <Th>Folio</Th>
                <Th>Particulars</Th>
                <Th>Description</Th>
                <Th>User</Th>
                <Th right>Amount</Th>
                <Th right>Balance</Th>
                <Th />
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {rows.map((t) => (
                // The whole line is ruled through, not just the words that
                // name it. Striking the description and the amount but
                // leaving the date, the folio and the balance upright read as
                // a formatting accident rather than a statement about the
                // money -- and on a bill that a guest is handed, the line has
                // to be unmistakably cancelled from one end to the other.
                //
                // `dead` carries the rule and the grey; each cell takes it
                // rather than the row, because text-decoration set on a <tr>
                // is not reliably inherited by <td> across browsers.
                <tr key={t.id} className={`hover:bg-slate-50 ${
                  chosen.has(t.id) ? 'bg-brand-light/40' : ''}`}>
                  <td className="px-3 py-3">
                    <input type="checkbox" className="accent-brand"
                      aria-label={`Select ${t.description}`}
                      checked={chosen.has(t.id)}
                      onChange={() => setChosen((prev) => {
                        const next = new Set(prev)
                        if (next.has(t.id)) next.delete(t.id)
                        else next.add(t.id)
                        return next
                      })} />
                  </td>
                  <td className={`whitespace-nowrap px-4 py-3 ${
                    t.voided ? dead : 'text-slate-600'}`}>
                    {fmtDateTime(t.at)}
                  </td>
                  {/* text-sm, like every other cell. These two were set a
                      step smaller than the rest, so a row read as though its
                      middle had been typed in a different font. Colour carries
                      the hierarchy instead. */}
                  <td className={`whitespace-nowrap px-4 py-3 ${
                    t.voided ? dead : 'text-slate-500'}`}>
                    {t.folioNo ?? '—'}
                  </td>
                  <td className="px-4 py-3">
                    <Kind kind={t.kind} muted={t.voided}
                      method={t.instrument ? methodLabel(t.instrument) : null} />
                  </td>
                  <td className="px-4 py-3 text-slate-700"
                    title={t.gatewayRef ?? undefined}>
                    {/* No method here any more. It moved to Particulars,
                        where it is the thing that column is for, and printing
                        it in both put "Card" twice on one row. */}
                    <span className={t.voided ? dead : ''}>
                      {t.description}
                    </span>
                    {/* The word as well as the rule through it. A strike alone
                        is a visual convention that not everyone reads the same
                        way, and on a financial document the difference between
                        "paid" and "not paid" should survive being printed in
                        black and white or read by a screen reader. */}
                    {t.voided && (
                      <span className="ml-2 rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-500">
                        Voided
                      </span>
                    )}
                    {/* Amber, not struck: the money is still on the folio.
                        What is missing is the last click, and the row is where
                        somebody will be looking for it. */}
                    {t.pending && (
                      <span className="ml-2 rounded-full bg-amber-50 px-2 py-0.5 text-xs font-medium text-caution">
                        Void pending
                      </span>
                    )}
                    {t.reference && (
                      <span className="block truncate text-xs text-slate-500">
                        {t.reference}
                      </span>
                    )}
                  </td>
                  {/* Who posted it. The first question asked when a line is
                      disputed, and the ledger could not answer it: nothing was
                      recorded. "System" is the night audit charging the room
                      because the clock passed midnight, and every line posted
                      before the column existed — neither is a person, and
                      naming one would be worse than saying so. */}
                  <td className={`whitespace-nowrap px-4 py-3 ${
                    t.voided ? dead : 'text-slate-500'}`}>
                    {t.user ?? <span className="text-slate-400">System</span>}
                  </td>
                  {/* A voided payment loses the green as well as gaining the
                      rule. Green on this ledger means money that came in, and
                      it did not stay. */}
                  <td className={`whitespace-nowrap px-4 py-3 text-right font-semibold tabular-nums ${
                    t.voided ? dead
                      : t.amount < 0 ? 'text-emerald-700' : 'text-ink'}`}>
                    {money(t.amount, cur)}
                  </td>
                  <td className={`whitespace-nowrap px-4 py-3 text-right tabular-nums ${
                    t.voided ? dead : 'text-slate-600'}`}>
                    {money(t.balance, cur)}
                  </td>
                  {/* Was a single undo icon, which offered the one action
                      somebody had got round to building. Everything a desk
                      does to a line now lives here, and the charges get their
                      own item rather than nothing at all. */}
                  <td className="px-2 py-3 text-right">
                    <div className="flex justify-end">
                      <ActionsMenu label={`Actions for ${t.description}`}
                        items={rowActions(t)} />
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {rows.length === 0 && (
            <div className="px-4 py-14 text-center">
              <span className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-full bg-slate-75 text-slate-400">
                <FileText size={20} />
              </span>
              <p className="text-sm font-medium text-slate-600">
                {all.length === 0
                  ? 'Nothing on this folio yet'
                  : 'No transactions match'}
              </p>
              <p className="mt-1 text-xs text-slate-400">
                {all.length === 0
                  ? 'Room charges post overnight; anything taken at the desk '
                    + 'appears here as soon as it is entered.'
                  : 'Clear the search or the filter to see all '
                    + `${all.length} entries.`}
              </p>
            </div>
          )}
        </div>

        <div className="grid gap-3 border-t border-slate-100 p-4 sm:grid-cols-2 lg:grid-cols-4">
          <Tile icon={Receipt} tone="text-brand" bg="bg-brand-light"
            label="Total charges" value={money(r.financials.total_charges, cur)} />
          <Tile icon={CreditCard} tone="text-emerald-700" bg="bg-emerald-50"
            label="Total payments" value={money(r.financials.total_paid, cur)} />
          <Tile icon={Percent} tone="text-caution" bg="bg-amber-50"
            label="Adjustments" value={money(adjustments, cur)} />
          <Tile icon={Wallet}
            tone={Number(r.financials.balance_due) > 0
              ? 'text-caution' : 'text-emerald-700'}
            bg={Number(r.financials.balance_due) > 0
              ? 'bg-amber-50' : 'bg-emerald-50'}
            label="Balance" value={money(r.financials.balance_due, cur)} />
        </div>
      </section>

      {paying && (
        <AddPaymentDialog
          reservationId={r.id}
          organizationId={orgId}
          propertyId={r.property_id}
          folioId={folioId}
          folioLabel={folioLabel}
          balanceDue={Number(r.financials.balance_due)}
          businessDate={r.business_date}
          onClose={() => setPaying(false)}
          onSaved={() => { setPaying(false); refresh() }} />
      )}

      {adding && (
        <AddChargeDialog
          reservationId={r.id}
          organizationId={orgId}
          propertyId={r.property_id}
          folioId={folioId}
          folioLabel={folioLabel}
          businessDate={r.business_date}
          onClose={() => setAdding(false)}
          onSaved={() => { setAdding(false); refresh() }} />
      )}

      {discounting && folioId && (
        <ApplyDiscountDialog
          folioId={folioId}
          propertyId={r.property_id}
          onClose={() => setDiscounting(false)}
          onSaved={() => { setDiscounting(false); refresh() }}
          onRefresh={() => refresh()} />
      )}

      {newFolio && (
        <NewFolioDialog r={r}
          onClose={() => setNewFolio(false)}
          onOpened={() => { setNewFolio(false); refresh() }} />
      )}

      {op && folioId && (
        <FolioOpDialog op={op} r={r} folioId={folioId}
          preselected={chosenCharges}
          onClose={() => setOp(null)}
          onDone={() => { setOp(null); refresh() }} />
      )}
    </div>
  )
}

/** One folio in the rail: who it bills, what it stands at, and whether it is
 *  the one on screen. */
function FolioPick({
  f, cur, child, selected, fallbackName, showMasterTag = true, onPick,
}: {
  f: FolioRow
  cur: string
  child?: boolean
  selected: boolean
  /** Who the booking is for, used when nobody was named on the folio itself.
   *  A row reading "Guest" tells the desk nothing it did not already know;
   *  the guest's actual name is the thing they are looking for. */
  fallbackName: string | null
  /** Only worth saying on a booking that has a child. On a booking with one
   *  folio, "MASTER" is a label with nothing to distinguish it from. */
  showMasterTag?: boolean
  onPick: () => void
}) {
  const owed = Number(f.balance)
  return (
    <button type="button" onClick={onPick} aria-pressed={selected}
      className={`mb-0.5 block w-full rounded-lg border px-2.5 py-2 text-left transition-colors ${
        child ? 'ml-3 w-[calc(100%-0.75rem)]' : ''} ${
        selected
          ? 'border-brand/30 bg-brand-light'
          : 'border-transparent hover:bg-slate-50'}`}>
      <span className="flex items-baseline justify-between gap-2">
        <span className={`truncate text-sm font-semibold ${
          selected ? 'text-brand' : 'text-slate-700'}`}>
          {f.folio_no ?? 'Folio'}
        </span>
        <span className={`shrink-0 text-sm font-semibold tabular-nums ${
          owed > 0 ? 'text-caution' : 'text-slate-600'}`}>
          {money(f.balance, cur)}
        </span>
      </span>
      <span className="mt-0.5 flex items-center gap-1.5">
        <span className="truncate text-xs text-slate-500">
          {/* Who it bills, most specific first: the name written on the folio,
              then the booking's guest, and only then the folio type — which is
              a category, not a person, and was all this row used to show. */}
          {f.sharer_name ?? fallbackName ?? TYPE_LABELS[f.type] ?? f.type}
        </span>
        {!child && showMasterTag && (
          <span className="shrink-0 rounded px-1 text-[10px] font-semibold uppercase tracking-wide text-slate-400">
            Master
          </span>
        )}
      </span>
      {f.gstin && (
        <span className="mt-0.5 block truncate font-mono text-[11px] text-slate-400">
          {f.gstin}
        </span>
      )}
    </button>
  )
}

const TYPE_LABELS: Record<string, string> = {
  guest: 'Guest', company: 'Company', group: 'Group', paymaster: 'Paymaster',
}

function Th({ children, right }: {
  children?: React.ReactNode; right?: boolean
}) {
  // font-bold, not font-medium. At 12px uppercase in slate-500 the old weight
  // sat at almost the same visual density as the row text under it, so the
  // header stopped reading as a header and the table lost its top edge.
  return <th className={`px-4 py-3 font-bold ${right ? 'text-right' : ''}`}>
    {children}</th>
}

/** What the line is, said as specifically as the row allows.
 *
 *  On a payment that means *how the money arrived* -- Cash, Card, UPI -- not
 *  the word "Payment", which the Amount column already establishes by being
 *  negative. A desk counting a drawer or chasing a settlement is looking for
 *  the instrument, and it was buried in the Description as a suffix.
 *
 *  A charge has no instrument, so it keeps its kind. A payment whose method
 *  could not be resolved falls back to "Payment" rather than showing an empty
 *  cell that reads as missing data.
 */
function Kind({ kind, method, muted }: {
  kind: Txn['kind']; method?: string | null
  /** The line is cancelled. The pill goes grey rather than gaining a rule
   *  through it: a struck-through badge reads as a broken render, and the
   *  colour is what this chip communicates anyway. */
  muted?: boolean
}) {
  const map = {
    charge: ['Charge', 'bg-brand-light text-brand'],
    payment: ['Payment', 'bg-emerald-50 text-emerald-700'],
    reversal: ['Reversal', 'bg-amber-50 text-caution'],
  } as const
  const [fallback, tone] = map[kind]
  const label = kind === 'charge' ? fallback : (method || fallback)
  return (
    <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${
      muted ? 'bg-slate-100 text-slate-400' : tone}`}>
      {label}
    </span>
  )
}

function Tile({ icon: Icon, label, value, tone, bg }: {
  icon: LucideIcon
  label: string; value: string; tone: string; bg: string
}) {
  return (
    <div className="flex items-center gap-3">
      <span className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-xl ${bg} ${tone}`}>
        <Icon size={18} />
      </span>
      <span className="min-w-0">
        <span className="block text-xs text-slate-500">{label}</span>
        <span className={`block truncate text-lg font-bold ${tone}`}>
          {value}
        </span>
      </span>
    </div>
  )
}

function RoomGroup({ type, units, guest, number }: {
  type: string
  units: ReservationFull['units']
  guest: string | null
  number: string
}) {
  const [open, setOpen] = useState(true)
  return (
    <div className="mb-3 last:mb-0">
      <button onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-1 px-1 pb-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500 hover:text-slate-700">
        {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
        {type}
        <span className="ml-auto font-normal normal-case text-slate-400">
          {units.length}
        </span>
      </button>
      {open && units.map((u) => (
        <div key={u.id}
          className="mb-1.5 rounded-xl border border-brand/20 bg-brand-light/40 px-3 py-2.5 last:mb-0">
          <p className="flex items-center gap-1.5 text-sm font-semibold text-ink">
            <BedDouble size={14} className="text-brand" />
            {u.room_code ?? 'Not assigned'}
          </p>
          <p className="mt-0.5 text-xs text-slate-500">
            {guest ?? 'No guest recorded'}
          </p>
          <p className="text-xs text-slate-400">
            Res. {number} · {u.nights} night{u.nights === 1 ? '' : 's'} ·{' '}
            {u.adults}A{u.children > 0 ? ` ${u.children}C` : ''}
          </p>
        </div>
      ))}
    </div>
  )
}
