import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import Select from './Select'
import { useNavigate } from 'react-router-dom'
import {
  ArrowLeftRight, Ban, BedDouble, CalendarPlus, ClipboardList, Copy, FileText, Gift, KeyRound, Loader2, LogOut, MoreVertical, Pencil, Printer, Receipt, ScrollText, Sparkles, UserX, Wallet, X,
} from 'lucide-react'
import { type ActionItem } from './ActionsMenu'
import RowActionsPanel from './RowActionsPanel'
import {
  addFolioCharge, collectFolioPayment, createInvoice, ensureFolio,
  issueInvoice,
  clearComplimentary, getComplimentary, getCompReasons, markComplimentary,
  extendStay, getExtendQuote,
  openFolioPdf, openRegistrationCard, setHousekeepingStatus,
} from '../api'
import { usePaymentMethods } from '../lib/paymentMethods'
import { errorText } from '../lib/forms'

/** Everything a desk can do to one booking from a list.
 *
 * Defined once rather than per tab: the four stay lists differ in which
 * actions apply, never in what an action means or how it is worded. A tab
 * says which row it is showing and what state that row is in; this decides
 * the rest.
 *
 * Actions that do not apply to a booking are left out entirely. The panel can
 * carry a dozen, and on a closed stay a third of them were unusable, so the
 * list read as mostly noise; a desk scanning for what it can do should not
 * have to filter out what it cannot.
 *
 * That also means "Send confirmation" and "Add note" are not built here: there
 * is no mail transport and no notes store, so they could only ever have been
 * permanently greyed rows, and a permanently greyed row is now an invisible
 * one. Add them when there is something behind them.
 */
export interface RowActionCtx {
  propertyId: string
  organizationId?: string | null
  reservationId: string
  reservationUnitId?: string | null
  number: string
  folioId?: string | null
  hasFolio?: boolean
  roomId?: string | null
  room?: string | null
  /** Unit status: reserved | checked_in | checked_out, or a booking status. */
  state?: string | null
  // Shown in the panel header, so the choice is made with the booking in
  // front of you rather than from the row you just left.
  guestName?: string | null
  roomType?: string | null
  statusLabel?: string
  departureDate?: string | null
  nights?: number
  adults?: number
  children?: number
  total?: number
  paid?: number
  balance?: number
  arrivalDate?: string | null
  businessDate: string
  /** Refetch the list after something changes it. */
  onDone: () => void
  /** Assign Room opens a panel the parent owns, keyed by arrival date. */
  onAssignRoom?: () => void
  /** Tabs suppress the action that is simply "where you already are". */
  show?: Partial<Record<ActionKey, boolean>>
}

export type ActionKey =
  | 'checkIn' | 'checkOut' | 'assignRoom' | 'moveRoom' | 'noShow'
  | 'postCharge' | 'collectPayment' | 'housekeeping'
  | 'registrationCard' | 'complimentary'
  | 'viewReservation' | 'modify' | 'cancel' | 'deposits'
  | 'openFolio' | 'printBill' | 'adjustFolio' | 'taxInvoice'
  | 'roomHistory' | 'copyNumber'

const money = new Intl.NumberFormat('en-IN', { minimumFractionDigits: 2 })

export default function RowActions({ ctx, open, onOpenChange }: {
  ctx: RowActionCtx
  /** Controlled, when a parent wants the row itself to open the panel.
   *  Left out, the "⋮" is the only way in and the state stays here. */
  open?: boolean
  onOpenChange?: (open: boolean) => void
}) {
  const navigate = useNavigate()
  const [ownPanel, setOwnPanel] = useState(false)
  const panel = open ?? ownPanel
  const setPanel = (next: boolean) => {
    if (onOpenChange) onOpenChange(next)
    else setOwnPanel(next)
  }
  const [dialog, setDialog] = useState<'charge' | 'payment' | 'hk' | 'comp' | 'extend' | null>(null)
  const [busy, setBusy] = useState('')
  const [note, setNote] = useState('')
  const [err, setErr] = useState('')

  // Most actions are on unless a tab switches them off. These three are the
  // other way round: each is reachable elsewhere in a click or two, and
  // carrying them on every row pushed the menus past the length anyone reads.
  // A tab that wants one back asks for it by name.
  const OPT_IN: ActionKey[] = ['openFolio', 'roomHistory', 'copyNumber']
  const on = (k: ActionKey) =>
    OPT_IN.includes(k) ? ctx.show?.[k] === true : ctx.show?.[k] !== false
  const unit = ctx.reservationUnitId
  const folio = ctx.folioId
  const checkedOut = ctx.state === 'checked_out'
  const inHouse = ctx.state === 'checked_in'

  async function run(what: string, fn: () => Promise<void>) {
    setBusy(what); setErr(''); setNote('')
    try { await fn() } catch (e) {
      setErr(errorText(e, 'That did not go through.'))
    } finally { setBusy('') }
  }

  const items: ActionItem[] = []

  // ---- the thing this row exists for -------------------------------------
  if (on('checkIn') && unit) {
    items.push({
      label: inHouse || checkedOut ? 'View check-in' : 'Check in', group: 'stay', description: 'Complete the check-in process',
      icon: KeyRound, tone: 'primary',
      onSelect: () => navigate(`/front-desk/check-in/${unit}`),
    })
  }
  if (on('checkOut') && unit) {
    items.push({
      label: checkedOut ? 'View check-out' : 'Check out', group: 'stay', description: 'Complete check-out process',
      icon: LogOut, tone: 'primary',
      onSelect: () => navigate(`/front-desk/check-out/${unit}`),
    })
  }

  // ---- the room ----------------------------------------------------------
  if (on('assignRoom') && ctx.onAssignRoom) {
    items.push({
      label: 'Assign room', icon: BedDouble, group: 'stay', description: 'Give this booking a room',
      disabled: Boolean(ctx.room),
      hint: ctx.room ? `Room ${ctx.room} is already assigned.` : undefined,
      onSelect: ctx.onAssignRoom,
    })
  }
  if (on('moveRoom') && unit) {
    items.push({
      label: 'Move room', icon: ArrowLeftRight, group: 'stay', description: 'Change room for this reservation',
      disabled: checkedOut || !ctx.room,
      hint: checkedOut ? 'The stay is closed.'
        : !ctx.room ? 'No room is assigned yet.' : undefined,
      onSelect: () => navigate(`/front-desk/room-move/${unit}`),
    })
  }
  if (on('moveRoom') && inHouse) {
    // Beside Move room deliberately: once a guest is in the house these are
    // the two things that can still change about their stay, and a desk
    // asked "can we stay longer?" should not have to know that the answer
    // lives on a different screen from "can we change rooms?".
    items.push({
      label: 'Extend stay', icon: CalendarPlus, group: 'stay',
      description: 'Keep this guest longer without moving them',
      onSelect: () => setDialog('extend'),
    })
  }
  if (on('housekeeping')) {
    items.push({
      label: 'Housekeeping status', icon: Sparkles, group: 'stay', description: 'Check room cleaning status',
      disabled: !ctx.roomId,
      hint: ctx.roomId ? undefined : 'No room is assigned yet.',
      onSelect: () => setDialog('hk'),
    })
  }
  if (on('roomHistory')) {
    items.push({
      label: 'Room history', icon: ScrollText, group: 'stay', description: 'Past status changes for this room',
      disabled: !ctx.roomId,
      hint: ctx.roomId ? undefined : 'No room is assigned yet.',
      onSelect: () => navigate(`/rooms/${ctx.roomId}/history`),
    })
  }

  if (on('registrationCard')) {
    items.push({
      label: 'Registration card', icon: ClipboardList, group: 'stay',
      description: 'The form the guest checks and signs on arrival',
      disabled: !ctx.reservationUnitId,
      hint: ctx.reservationUnitId ? undefined
        : 'This list is booking-level; open the arrival to print a card.',
      onSelect: () => void openRegistrationCard(
        ctx.propertyId, ctx.reservationUnitId!),
    })
  }

  if (on('complimentary')) {
    items.push({
      label: 'Complimentary / house use', icon: Gift, group: 'stay',
      description: 'Give this room free, or take it for the hotel',
      disabled: !unit,
      hint: unit ? undefined
        : 'This list is booking-level; open the stay to mark one room.',
      onSelect: () => setDialog('comp'),
    })
  }

  // ---- money -------------------------------------------------------------
  if (on('postCharge')) {
    items.push({
      label: 'Post a charge', icon: Receipt, group: 'billing', description: 'Add a charge to the folio',
      disabled: checkedOut,
      hint: checkedOut ? 'The stay is closed — use a folio adjustment.' : undefined,
      onSelect: () => setDialog('charge'),
    })
  }
  if (on('collectPayment')) {
    items.push({
      label: 'Collect payment', icon: Wallet, group: 'billing', description: 'Record a payment for this booking',
      disabled: !folio,
      hint: folio ? undefined : 'Nothing has been posted yet.',
      onSelect: () => setDialog('payment'),
    })
  }
  if (on('adjustFolio')) {
    items.push({
      label: 'Folio adjustment', icon: Pencil, group: 'billing', description: 'Adjust charges or credits',
      disabled: !folio,
      hint: folio ? undefined : 'Nothing has been posted yet.',
      onSelect: () => navigate(`/payments/folios/${folio}/adjust`),
    })
  }
  if (on('taxInvoice')) {
    items.push({
      label: 'Generate tax invoice', icon: FileText, group: 'billing', description: 'Create and download tax invoice',
      disabled: !folio || busy === 'invoice',
      hint: folio ? undefined : 'Nothing has been posted yet.',
      // Raised and issued in one step: a draft nobody issues is not a tax
      // invoice, and the desk asking for one wants the numbered document.
      onSelect: () => void run('invoice', async () => {
        const draft = await createInvoice({
          property_id: ctx.propertyId, folio_id: folio!,
        })
        const issued = await issueInvoice(draft.id, ctx.propertyId)
        ctx.onDone()
        navigate(`/finance/invoices/${issued.id}`)
      }),
    })
  }
  if (on('openFolio')) {
    items.push({
      label: 'Open folio', icon: Receipt, group: 'billing', description: 'Open the full folio',
      onSelect: () => navigate(`/reservations/${ctx.reservationId}/folio`),
    })
  }
  if (on('printBill')) {
    items.push({
      label: 'Print bill', icon: Printer, group: 'billing', description: 'Generate and print guest bill',
      disabled: !folio || busy === 'print',
      hint: folio ? undefined : 'Nothing has been posted yet.',
      onSelect: () => void run('print', () =>
        openFolioPdf(ctx.propertyId, folio!)),
    })
  }
  if (on('deposits')) {
    items.push({
      label: 'Deposit schedule', icon: Wallet, group: 'billing', description: 'Instalments due on this booking',
      onSelect: () => navigate(`/reservations/${ctx.reservationId}/deposits`),
    })
  }

  // ---- the booking -------------------------------------------------------
  if (on('viewReservation')) {
    items.push({
      label: 'View reservation', icon: FileText, group: 'stay', description: 'See full booking details',
      onSelect: () => navigate(`/reservations/${ctx.reservationId}`),
    })
  }
  if (on('modify')) {
    items.push({
      label: 'Modify booking', icon: Pencil, group: 'stay', description: 'Change dates, rooms or occupancy',
      disabled: checkedOut,
      hint: checkedOut ? 'The stay is closed.' : undefined,
      onSelect: () => navigate(`/reservations/${ctx.reservationId}/modify`),
    })
  }
  if (on('noShow') && unit) {
    items.push({
      label: 'Mark no-show', icon: UserX, tone: 'danger', group: 'communication', description: 'Close out a guest who never arrived',
      disabled: inHouse || checkedOut
        || Boolean(ctx.arrivalDate && ctx.arrivalDate >= ctx.businessDate),
      hint: inHouse || checkedOut ? 'The guest has arrived.'
        : 'Only once the arrival date has passed.',
      onSelect: () => navigate(`/reservations/no-show/${unit}`),
    })
  }
  if (on('cancel')) {
    items.push({
      label: 'Cancel reservation', icon: Ban, tone: 'danger', group: 'communication', description: 'Permanently cancel this booking',
      disabled: inHouse || checkedOut,
      hint: inHouse || checkedOut ? 'The guest has already arrived.' : undefined,
      // The modify screen already quotes the penalty and takes the reason;
      // deep-linking to its cancel tab beats a second cancel UI that could
      // disagree with it.
      onSelect: () => navigate(`/reservations/${ctx.reservationId}/modify?tab=cancel`),
    })
  }

  if (on('copyNumber')) {
    items.push({
      label: 'Copy reservation no.', icon: Copy, group: 'communication', description: 'Copy the number to the clipboard',
      onSelect: () => void navigator.clipboard.writeText(ctx.number)
        .then(() => { setNote(`${ctx.number} copied`); setTimeout(() => setNote(''), 2000) })
        .catch(() => setErr('The clipboard is not available.')),
    })
  }

  return (
    <span className="relative inline-flex items-center gap-2">
      {busy && <Loader2 size={14} className="animate-spin text-slate-400" />}
      {note && <span className="whitespace-nowrap text-xs text-emerald-600">{note}</span>}
      {err && (
        <span className="max-w-[14rem] truncate text-xs text-red-600" title={err}>
          {err}
        </span>
      )}
      <button
        aria-label="Actions"
        aria-haspopup="dialog"
        onClick={(e) => { e.stopPropagation(); setPanel(true) }}
        className="grid h-8 w-8 place-items-center rounded-lg border border-slate-200 text-slate-500 hover:bg-slate-50 hover:text-slate-700">
        <MoreVertical size={16} />
      </button>
      <RowActionsPanel
        open={panel}
        onClose={() => setPanel(false)}
        items={items}
        summary={{
          number: ctx.number,
          guestName: ctx.guestName,
          roomType: ctx.roomType,
          room: ctx.room,
          statusLabel: ctx.statusLabel,
          arrival: ctx.arrivalDate,
          departure: ctx.departureDate,
          nights: ctx.nights,
          adults: ctx.adults,
          children: ctx.children,
          total: ctx.total,
          paid: ctx.paid,
        }} />

      {dialog === 'charge' && (
        <ChargeDialog ctx={ctx} onClose={() => setDialog(null)}
          onSaved={() => { setDialog(null); ctx.onDone() }} />
      )}
      {dialog === 'payment' && (
        <PaymentDialog ctx={ctx} onClose={() => setDialog(null)}
          onSaved={() => { setDialog(null); ctx.onDone() }} />
      )}
      {dialog === 'extend' && (
        <ExtendDialog ctx={ctx} onClose={() => setDialog(null)}
          onSaved={() => { setDialog(null); ctx.onDone() }} />
      )}
      {dialog === 'comp' && (
        <CompDialog ctx={ctx} onClose={() => setDialog(null)}
          onSaved={() => { setDialog(null); ctx.onDone() }} />
      )}
      {dialog === 'hk' && (
        <HousekeepingDialog ctx={ctx} onClose={() => setDialog(null)}
          onSaved={() => { setDialog(null); ctx.onDone() }} />
      )}
    </span>
  )
}

/* --------------------------------------------------------------- shell --- */
function Shell({ title, children, onClose }: {
  title: string; children: React.ReactNode; onClose: () => void
}) {
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-slate-900/50 p-4"
      role="dialog" aria-modal="true" aria-label={title}
      onClick={onClose}>
      <div className="w-full max-w-sm rounded-2xl bg-white shadow-xl"
        onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
          <h2 className="text-sm font-semibold text-ink">{title}</h2>
          <button onClick={onClose} aria-label="Close"
            className="rounded-lg p-1 text-slate-400 hover:bg-slate-100">
            <X size={16} />
          </button>
        </div>
        <div className="space-y-3 p-4">{children}</div>
      </div>
    </div>
  )
}

const field = 'w-full rounded-lg border border-slate-200 px-3 py-2 text-sm outline-none focus:border-brand'
const lbl = 'mb-1 block text-xs font-medium text-slate-500'

function Submit({ busy, label, disabled, onClick }: {
  busy: boolean; label: string; disabled?: boolean; onClick: () => void
}) {
  return (
    <button onClick={onClick} disabled={busy || disabled}
      className="flex w-full items-center justify-center gap-2 rounded-lg bg-brand px-3 py-2 text-sm font-semibold text-white hover:bg-brand-dark disabled:opacity-50">
      {busy && <Loader2 size={14} className="animate-spin" />} {label}
    </button>
  )
}

function useSave(onSaved: () => void) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  async function save(fn: () => Promise<unknown>) {
    setBusy(true); setErr('')
    try { await fn(); onSaved() } catch (e) {
      setErr(errorText(e, 'That did not go through.'))
    } finally { setBusy(false) }
  }
  return { busy, err, save }
}

/* -------------------------------------------------------------- charge --- */
const CHARGE_TYPES = [
  ['restaurant', 'Restaurant'], ['in_room_dining', 'In-room Dining'],
  ['minibar', 'Minibar'], ['laundry', 'Laundry'], ['spa', 'Spa'],
  ['transport', 'Transport'], ['other', 'Other'],
] as const

function ChargeDialog({ ctx, onClose, onSaved }: {
  ctx: RowActionCtx; onClose: () => void; onSaved: () => void
}) {
  const [type, setType] = useState<string>('restaurant')
  const [amount, setAmount] = useState('')
  const { busy, err, save } = useSave(onSaved)
  const value = Number(amount)

  return (
    <Shell title={`Post a charge — ${ctx.number}`} onClose={onClose}>
      <div>
        <label className={lbl}>Department</label>
        <Select className={field} value={type} onChange={(e) => setType(e.target.value)}>
          {CHARGE_TYPES.map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </Select>
      </div>
      <div>
        <label className={lbl}>Amount (₹)</label>
        <input className={field} inputMode="decimal" value={amount} autoFocus
          onChange={(e) => setAmount(e.target.value)} placeholder="0.00" />
      </div>
      {err && <p className="text-xs text-red-600">{err}</p>}
      <Submit busy={busy} disabled={!(value > 0)}
        label={value > 0 ? `Post ₹${money.format(value)}` : 'Post charge'}
        onClick={() => void save(async () => {
          // A booking has no folio until something is put on it, and this is
          // usually that something. Sending `ctx.folioId!` regardless is what
          // produced "folio_id: UUID input should be a string, bytes or UUID
          // object" — the assertion silenced TypeScript and the null went to
          // the server anyway. Creating it here is idempotent.
          const folioId = ctx.folioId ?? (await ensureFolio(
            ctx.reservationId, ctx.organizationId!, ctx.propertyId)).id
          await addFolioCharge({
            organization_id: ctx.organizationId!,
            property_id: ctx.propertyId,
            folio_id: folioId,
            amount: value,
            business_date: ctx.businessDate,
            source_type: type,
            // Idempotent per posting, so a double-click does not bill twice.
            source_line_key: `${type}:${ctx.reservationId}:${Date.now()}`,
          })
        })} />
    </Shell>
  )
}

/* ------------------------------------------------------------- payment --- */
// Served, not restated: this copy wrote title-cased values into
// finance.payments, which is why 'UPI' and 'upi' were both in there until
// migration 0032 merged them.

function PaymentDialog({ ctx, onClose, onSaved }: {
  ctx: RowActionCtx; onClose: () => void; onSaved: () => void
}) {
  const due = Math.max(ctx.balance ?? 0, 0)
  const { methods } = usePaymentMethods(ctx.propertyId)
  // Whatever the server lists first, rather than a spelling this file invents.
  const [method, setMethod] = useState('')
  useEffect(() => {
    if (!method && methods.length) setMethod(methods[0].value)
  }, [method, methods])
  const [amount, setAmount] = useState(due > 0 ? String(due) : '')
  const { busy, err, save } = useSave(onSaved)
  const value = Number(amount)

  return (
    <Shell title={`Collect payment — ${ctx.number}`} onClose={onClose}>
      <p className="text-xs text-slate-500">
        Outstanding <span className="font-semibold text-slate-700">
          ₹{money.format(due)}</span>
      </p>
      <div>
        <label className={lbl}>Method</label>
        <Select className={field} value={method} onChange={(e) => setMethod(e.target.value)}>
          {methods.map((m) => (
            <option key={m.value} value={m.value}>{m.label}</option>
          ))}
        </Select>
      </div>
      <div>
        <label className={lbl}>Amount (₹)</label>
        <input className={field} inputMode="decimal" value={amount} autoFocus
          onChange={(e) => setAmount(e.target.value)} />
      </div>
      {err && <p className="text-xs text-red-600">{err}</p>}
      <Submit busy={busy} disabled={!(value > 0)}
        label={value > 0 ? `Take ₹${money.format(value)}` : 'Take payment'}
        onClick={() => void save(() => collectFolioPayment({
          organization_id: ctx.organizationId!,
          property_id: ctx.propertyId,
          method,
          business_date: ctx.businessDate,
          allocations: [{ folio_id: ctx.folioId!, amount: value }],
        }))} />
    </Shell>
  )
}

/* -------------------------------------------------------- housekeeping --- */
const HK_STATES = [
  ['clean', 'Clean'], ['dirty', 'Dirty'], ['inspected', 'Inspected'],
  ['out_of_order', 'Out of order'],
] as const

function HousekeepingDialog({ ctx, onClose, onSaved }: {
  ctx: RowActionCtx; onClose: () => void; onSaved: () => void
}) {
  const [status, setStatus] = useState<string>('dirty')
  const [remarks, setRemarks] = useState('')
  const { busy, err, save } = useSave(onSaved)

  return (
    <Shell title={`Housekeeping — Room ${ctx.room ?? ''}`} onClose={onClose}>
      <div>
        <label className={lbl}>Status</label>
        <Select className={field} value={status} onChange={(e) => setStatus(e.target.value)}>
          {HK_STATES.map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </Select>
      </div>
      <div>
        <label className={lbl}>Remarks</label>
        <input className={field} value={remarks} placeholder="Optional"
          onChange={(e) => setRemarks(e.target.value)} />
      </div>
      {err && <p className="text-xs text-red-600">{err}</p>}
      <Submit busy={busy} label="Update status"
        onClick={() => void save(() => setHousekeepingStatus(
          ctx.propertyId, ctx.roomId!,
          { status, remarks: remarks || undefined }))} />
    </Shell>
  )
}

/* ------------------------------------------------- complimentary / house --- */
/**
 * Declare a room free, and say why.
 *
 * Two kinds, kept apart because they are different facts: `complimentary` is
 * revenue given to a guest, `house_use` is inventory the hotel took for
 * itself. Both keep the room in occupancy; neither counts towards ADR.
 *
 * It does not touch the folio. Waiving what is already posted is a folio
 * adjustment, which is approved and leaves its own trail — doing both from
 * one button would let someone erase posted revenue without that approval.
 * The dialog says so rather than leaving it to be discovered.
 */
function CompDialog({ ctx, onClose, onSaved }: {
  ctx: RowActionCtx; onClose: () => void; onSaved: () => void
}) {
  const [kind, setKind] = useState<'complimentary' | 'house_use'>('complimentary')
  const [reason, setReason] = useState('')
  const [note, setNote] = useState('')
  const { busy, err, save } = useSave(onSaved)

  const reasons = useQuery({ queryKey: ['comp-reasons'], queryFn: getCompReasons })
  const list = (kind === 'house_use'
    ? reasons.data?.house_use : reasons.data?.complimentary) ?? []

  // What this room is already declared as. The dialog used to open blank on a
  // room that had been given away weeks ago, so the desk could not see the
  // reason, the note, or whose decision it was -- and a second mark looked
  // exactly like a first one.
  const current = useQuery({
    queryKey: ['comp-state', ctx.reservationUnitId],
    queryFn: () => getComplimentary(ctx.reservationUnitId!, ctx.propertyId),
    enabled: !!ctx.reservationUnitId,
  })
  const marked = !!current.data?.comp_kind

  // Seed the form from what is recorded, once it arrives. Keyed on the id so
  // editing then re-fetching does not overwrite what is being typed.
  const seeded = useRef<string | null>(null)
  useEffect(() => {
    const d = current.data
    if (!d || seeded.current === d.reservation_unit_id) return
    seeded.current = d.reservation_unit_id
    if (d.comp_kind) {
      setKind(d.comp_kind)
      setReason(d.comp_reason ?? '')
      setNote(d.comp_note ?? '')
    }
  }, [current.data])

  // The two lists share "other" and nothing else, so a reason chosen for one
  // kind is not valid for the other. Cleared where the switch happens, not in
  // an effect on `kind`: an effect cannot tell a person pressing the other
  // tab from the seed above setting the value it already had, so it raced the
  // seed and silently blanked the reason on a room that had one.
  const switchKind = (k: 'complimentary' | 'house_use') => {
    if (k === kind) return
    setKind(k)
    setReason('')
  }

  const needsNote = reason === 'other'
  const valid = !!reason && (!needsNote || note.trim().length > 0)

  return (
    <Shell title={`Complimentary — ${ctx.number}`} onClose={onClose}>
      {marked && (
        <p className="rounded-lg bg-brand-light px-3 py-2 text-xs text-brand">
          Already declared{' '}
          <strong>
            {current.data!.comp_kind === 'house_use' ? 'house use' : 'complimentary'}
          </strong>
          {current.data!.comp_reason_label && <> — {current.data!.comp_reason_label}</>}
          {current.data!.comp_note && <> ({current.data!.comp_note})</>}
          {current.data!.comp_authorised_at && (
            <> on {new Date(current.data!.comp_authorised_at).toLocaleDateString()}</>
          )}
          . Saving again replaces it.
        </p>
      )}
      <div className="flex gap-2">
        {([['complimentary', 'Complimentary'], ['house_use', 'House use']] as const)
          .map(([k, label]) => (
            <button key={k} onClick={() => switchKind(k)}
              className={`flex-1 rounded-lg border px-3 py-2 text-sm font-semibold ${
                kind === k
                  ? 'border-brand bg-brand-light text-brand'
                  : 'border-slate-200 text-slate-600 hover:bg-slate-50'}`}>
              {label}
            </button>
          ))}
      </div>
      <p className="text-xs text-slate-500">
        {kind === 'house_use'
          ? 'The hotel occupies this room. It stays in occupancy but is not a guest stay.'
          : 'A guest stays and is not charged. It stays in occupancy but is left out of ADR.'}
      </p>

      <div>
        <label className={lbl}>Reason</label>
        <Select className={field} value={reason}
          onChange={(e) => setReason(e.target.value)}>
          <option value="">Choose a reason…</option>
          {list.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
        </Select>
      </div>
      <div>
        <label className={lbl}>
          Note {needsNote && <span className="text-red-500">— required for “Other”</span>}
        </label>
        <input className={field} value={note} onChange={(e) => setNote(e.target.value)}
          placeholder="What was agreed, and with whom" />
      </div>

      <p className="rounded-lg bg-amber-50 px-3 py-2 text-xs text-caution">
        This records the decision. Charges already on the folio are not removed
        — use a folio adjustment for those.
      </p>

      {err && <p className="text-xs text-red-600">{err}</p>}
      <Submit busy={busy} disabled={!valid}
        label={marked ? 'Update this room' : 'Mark this room'}
        onClick={() => void save(() => markComplimentary(
          ctx.reservationUnitId!, ctx.propertyId,
          { kind, reason, note: note.trim() || null }))} />
      {/* Offered only when there is something to undo. A room on normal terms
          has nothing to put back, and the button invited a no-op. */}
      {marked && (
        <button
          onClick={() => void save(() => clearComplimentary(
            ctx.reservationUnitId!, ctx.propertyId))}
          className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm font-semibold text-slate-600 hover:bg-slate-50">
          Put back on normal terms
        </button>
      )}
    </Shell>
  )
}


/* -------------------------------------------------------------- extend --- */
/**
 * "Can we stay two more nights?" — the most ordinary request a resort desk
 * gets, and the one change this system could not make until now.
 *
 * Quoted live as the date changes rather than behind a button, because the
 * desk is usually on the phone or facing the guest and the only two things
 * they need are the price and whether it is possible. Both arrive together.
 *
 * Nothing is charged when this is applied. The night audit already accrues a
 * room night for every guest who is checked in and has not left, reading the
 * stay rather than the planned departure, so the added nights bill
 * themselves at each night's own rate. The figure here is what to TELL the
 * guest, not a charge being raised — which is why it is labelled as an
 * estimate.
 */
function ExtendDialog({ ctx, onClose, onSaved }: {
  ctx: RowActionCtx; onClose: () => void; onSaved: () => void
}) {
  // Seeded one night past the current departure: the commonest answer, and
  // it means the quote below is populated the moment the dialog opens
  // instead of after the desk types a date.
  // Formatted from the LOCAL parts, never through toISOString(). That
  // returns UTC, and east of Greenwich local midnight is the previous day
  // there -- so in India this seeded the field with the departure date the
  // guest already has, and the dialog opened refusing itself.
  const nextDay = (iso?: string | null) => {
    if (!iso) return ''
    const d = new Date(`${iso.slice(0, 10)}T00:00:00`)
    d.setDate(d.getDate() + 1)
    const p2 = (n: number) => String(n).padStart(2, '0')
    return `${d.getFullYear()}-${p2(d.getMonth() + 1)}-${p2(d.getDate())}`
  }
  const [newDate, setNewDate] = useState(nextDay(ctx.departureDate))
  const [reason, setReason] = useState('guest_request')
  const [notes, setNotes] = useState('')
  const { busy, err, save } = useSave(onSaved)

  const quote = useQuery({
    queryKey: ['extend-quote', ctx.reservationId, newDate],
    queryFn: () => getExtendQuote(ctx.reservationId, ctx.propertyId, newDate),
    enabled: !!newDate,
  })
  const q = quote.data

  // Named `inr` rather than `money`: this file already has a module-level
  // `money` of a different shape, and one name for two formatters is how a
  // figure ends up rendered without its currency.
  const inr = (v?: string) => v === undefined ? '—'
    : new Intl.NumberFormat('en-IN', {
      style: 'currency', currency: 'INR', minimumFractionDigits: 2,
    }).format(Number(v))

  return (
    <Shell title={`Extend stay — ${ctx.number}`} onClose={onClose}>
      <p className="text-xs text-slate-500">
        {ctx.guestName ?? 'This guest'} is in {ctx.room ? `room ${ctx.room}` : 'house'}
        {ctx.departureDate ? `, due to leave ${ctx.departureDate.slice(0, 10)}` : ''}.
        They stay in the same room — nothing moves.
      </p>

      <div>
        <label className={lbl} htmlFor="extend-date">New departure date</label>
        <input id="extend-date" type="date" className={field} value={newDate}
          min={nextDay(ctx.departureDate)}
          onChange={(e) => setNewDate(e.target.value)} />
      </div>

      {/* The quote, and the two different ways this can be impossible. */}
      {newDate && quote.isLoading && (
        <p className="flex items-center gap-2 text-xs text-slate-400">
          <Loader2 size={13} className="animate-spin" /> Checking availability…
        </p>
      )}
      {q && q.added_nights > 0 && (
        <div className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-sm">
          <div className="flex justify-between">
            <span className="text-slate-500">
              {q.added_nights} extra night{q.added_nights === 1 ? '' : 's'}
              {q.rooms > 1 ? ` × ${q.rooms} rooms` : ''}
            </span>
            <span className="font-semibold text-ink">
              {inr(q.estimated_amount)}
            </span>
          </div>
          <p className="mt-1 text-xs text-slate-400">
            Estimated, at {inr(q.nightly_rate)} for the first added night.
            Each night is charged at its own rate by the night audit — nothing
            is posted now.
          </p>
        </div>
      )}
      {q && q.blocked_reason && (
        <p className="rounded-lg bg-amber-50 p-2 text-xs text-amber-700">
          {q.blocked_reason}
        </p>
      )}
      {/* Room type sold out: there is no room to sell them at all. */}
      {q && q.unavailable_reason && !q.blocked_reason && (
        <p className="rounded-lg bg-rose-50 p-2 text-xs text-rose-700">
          {q.unavailable_reason}
        </p>
      )}
      {/* This guest's own room is taken, though the type has others free.
          A different problem with a different answer, so it says so. */}
      {q && q.room_conflicts.length > 0 && (
        <p className="rounded-lg bg-rose-50 p-2 text-xs text-rose-700">
          Room {q.room_conflicts.join(', ')} is already booked for those
          nights. Move the guest to another room first, then extend.
        </p>
      )}

      <div>
        <label className={lbl} htmlFor="extend-reason">Reason</label>
        <Select id="extend-reason" className={field} value={reason}
          onChange={(e) => setReason(e.target.value)}>
          <option value="guest_request">Guest request</option>
          <option value="travel_plans">Change in travel plans</option>
          <option value="property_initiated">Property initiated</option>
          <option value="other">Other</option>
        </Select>
      </div>
      <div>
        <label className={lbl} htmlFor="extend-notes">Notes</label>
        <input id="extend-notes" className={field} value={notes}
          placeholder="Optional"
          onChange={(e) => setNotes(e.target.value)} />
      </div>

      {err && <p className="text-xs text-rose-600">{err}</p>}
      <Submit busy={busy} label="Extend stay"
        disabled={!q || !q.available}
        onClick={() => save(() => extendStay(
          ctx.reservationId, ctx.propertyId,
          { new_departure_date: newDate, reason, notes: notes || null },
        ))} />
    </Shell>
  )
}
