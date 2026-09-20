import { useEffect, useState } from 'react'
import { keepPreviousData, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Ban, Banknote, Check, CheckCircle2, Loader2, Pencil, Plus, Receipt, Search, X,
} from 'lucide-react'
import {
  createExpenseVoucher, decideExpenseVoucher, listExpenseVouchers,
  updateExpenseVoucher,
  type ExpenseVoucher, type ExpenseVoucherIn, type ExpenseVoucherList,
} from '../api'
import DateField from '../components/DateField'
import { Badge, Field, Modal, ModalButtons } from '../components/FormBits'
import Select from '../components/Select'
import { useActivePropertyId } from '../hooks/useProperty'
import { FILTER_SELECT } from '../lib/controls'
import { fmtDate } from '../lib/dates'
import { errorText, inputCls, inr, orNull } from '../lib/forms'

/**
 * Expense vouchers — money going out that is not a guest refund.
 *
 * Raised, then approved or rejected, then paid. Only an undecided voucher can
 * be edited, and a rejection must say why; both rules are the server's, and
 * this screen simply offers the actions the current status allows.
 */

const STATUS_TONE: Record<string, string> = {
  pending_approval: 'bg-amber-50 text-amber-700', approved: 'bg-sky-50 text-sky-700',
  paid: 'bg-emerald-50 text-emerald-700', rejected: 'bg-red-50 text-red-700',
  cancelled: 'bg-slate-75 text-slate-400',
}

type NoteAction = { voucher: ExpenseVoucher; action: 'reject' | 'cancel' }

export default function ExpenseVouchers() {
  const propertyId = useActivePropertyId()
  const qc = useQueryClient()
  const [status, setStatus] = useState('')
  const [category, setCategory] = useState('')
  const [search, setSearch] = useState('')
  const [q, setQ] = useState('')
  const [editing, setEditing] = useState<ExpenseVoucher | 'new' | null>(null)
  const [noting, setNoting] = useState<NoteAction | null>(null)
  const [toast, setToast] = useState('')
  const [failure, setFailure] = useState('')
  const [working, setWorking] = useState('')

  useEffect(() => {
    const t = setTimeout(() => setQ(search), 300)
    return () => clearTimeout(t)
  }, [search])

  const { data, isLoading, isFetching, isError, error } = useQuery({
    queryKey: ['expense-vouchers', propertyId, status, category, q],
    queryFn: () => listExpenseVouchers(propertyId, {
      status: status || undefined, category: category || undefined, q: q || undefined,
    }),
    enabled: propertyId !== '',
    placeholderData: keepPreviousData,
  })
  const rows = data?.rows ?? []

  function done(message: string) {
    setToast(message)
    setFailure('')
    setTimeout(() => setToast(''), 3500)
    qc.invalidateQueries({ queryKey: ['expense-vouchers'] })
    setEditing(null)
    setNoting(null)
  }

  async function act(v: ExpenseVoucher, action: 'approve' | 'pay') {
    setWorking(v.id)
    try {
      await decideExpenseVoucher(propertyId, v.id, { action })
      done(`${v.voucher_no} ${action === 'approve' ? 'approved' : 'marked paid'}.`)
    } catch (e) {
      setFailure(errorText(e))
    } finally {
      setWorking('')
    }
  }

  const sel = `${FILTER_SELECT} bg-white outline-none focus:border-brand`
  const totals = data?.totals ?? {}

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <h1 className="flex items-center gap-2 text-display text-ink">
          <Receipt size={26} className="text-brand" /> Expense Vouchers
        </h1>
        <div className="flex flex-wrap items-center gap-2">
          {data?.can_create && (
            <button onClick={() => setEditing('new')}
              className="flex items-center gap-1.5 whitespace-nowrap rounded-lg bg-brand px-3 py-2 text-sm font-medium text-white hover:bg-brand-dark disabled:opacity-40">
              <Plus size={15} /> New Voucher
            </button>
          )}
        </div>
      </div>

      {toast && (
        <p className="flex items-start gap-2 rounded-xl bg-emerald-50 px-4 py-3 text-sm text-emerald-700">
          <CheckCircle2 size={16} className="mt-0.5 shrink-0" /> {toast}
        </p>
      )}
      {failure && (
        <p className="flex items-start justify-between gap-2 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700">
          {failure}
          <button onClick={() => setFailure('')} aria-label="Dismiss"><X size={15} /></button>
        </p>
      )}

      <div className="grid gap-3 sm:grid-cols-3">
        {([
          ['Pending approval', totals.pending_approval],
          ['Approved, not yet paid', totals.approved],
          ['Paid', totals.paid],
        ] as const).map(([label, value]) => (
          <div key={label} className="rounded-xl border border-slate-100 bg-white p-4">
            <p className="text-xs font-medium text-slate-500">{label}</p>
            <p className="mt-1 text-xl font-semibold text-slate-800">{inr(value ?? 0)}</p>
            <p className="mt-0.5 text-[11px] text-slate-400">For the vouchers listed</p>
          </div>
        ))}
      </div>

      {/* One row: status as a segmented track (the server's five statuses
          plus All), category, then search at the right. */}
      <div className="flex flex-wrap items-center gap-2">
        <span className="inline-flex shrink-0 items-center gap-0.5 rounded-lg border border-slate-200 bg-white p-0.5">
          {[{ value: '', label: 'All' }, ...(data?.statuses ?? [])].map((st) => (
            <button key={st.value} onClick={() => setStatus(st.value)} aria-pressed={status === st.value}
              className={`whitespace-nowrap rounded-md px-3 py-1.5 text-sm ${status === st.value
                ? 'bg-brand font-semibold text-white'
                : 'font-medium text-slate-500 hover:bg-slate-50 hover:text-slate-700'}`}>
              {st.label}
            </button>
          ))}
        </span>
        <Select blankIsChoice value={category} onChange={(e) => setCategory(e.target.value)}
          aria-label="Category" className={sel}>
          <option value="">All categories</option>
          {(data?.categories ?? []).map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
        </Select>
        {isFetching && !isLoading && <Loader2 size={15} className="animate-spin text-slate-300" />}
        <div className="flex min-w-[240px] flex-1 items-center justify-end gap-2">
          <div className="relative min-w-0 max-w-sm flex-1">
            <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <input value={search} onChange={(e) => setSearch(e.target.value)}
              placeholder="Search by payee, description or voucher number…"
              aria-label="Search vouchers"
              className="w-full rounded-lg border border-slate-200 bg-slate-50 py-2 pl-9 pr-3 text-sm outline-none focus:border-brand" />
          </div>
        </div>
      </div>

      <div className="overflow-hidden rounded-2xl border border-slate-100 bg-white">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="border-b border-slate-100 bg-slate-50/60 text-left text-slate-600">
              <tr>
                <th className="px-5 py-3 font-semibold">Voucher</th>
                <th className="px-4 py-3 font-semibold">Payee</th>
                <th className="px-4 py-3 font-semibold">Category</th>
                <th className="px-4 py-3 font-semibold">Method</th>
                <th className="px-4 py-3 text-right font-semibold">Total</th>
                <th className="px-4 py-3 font-semibold">Status</th>
                <th className="px-4 py-3 text-right font-semibold">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-50">
              {(isLoading || propertyId === '') && (
                <tr><td colSpan={7} className="p-10 text-center text-slate-400">
                  <Loader2 size={18} className="mx-auto animate-spin" />
                </td></tr>
              )}
              {isError && (
                <tr><td colSpan={7} className="p-10 text-center text-sm text-red-600">
                  {errorText(error, 'Vouchers could not be loaded.')}
                </td></tr>
              )}
              {!isLoading && !isError && rows.length === 0 && (
                <tr><td colSpan={7} className="p-10 text-center text-sm text-slate-400">
                  No voucher matches. Raise one with New Voucher.
                </td></tr>
              )}
              {rows.map((v) => (
                <tr key={v.id} className="text-slate-700 hover:bg-slate-50/60">
                  <td className="px-5 py-3">
                    <span className="block font-semibold text-slate-800">{v.voucher_no}</span>
                    <span className="block text-xs text-slate-400">{fmtDate(v.expense_date)}</span>
                  </td>
                  <td className="px-4 py-3">
                    <span className="block font-medium text-slate-800">{v.payee}</span>
                    <span className="block text-xs text-slate-400">
                      {[v.description, v.room_code ? `Room ${v.room_code}` : null]
                        .filter(Boolean).join(' · ') || '—'}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-slate-600">{v.category_label}</td>
                  <td className="px-4 py-3 text-slate-600">{v.method_label}</td>
                  <td className="whitespace-nowrap px-4 py-3 text-right font-medium text-slate-800">
                    {inr(v.total)}
                  </td>
                  <td className="px-4 py-3">
                    <Badge tone={STATUS_TONE[v.status] ?? ''}>{v.status_label}</Badge>
                    {v.decided_by_name && (
                      <span className="mt-0.5 block text-[11px] text-slate-400"
                        title={v.decision_note ?? undefined}>
                        by {v.decided_by_name}
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex justify-end gap-1">
                      {working === v.id && <Loader2 size={15} className="m-2 animate-spin text-slate-400" />}
                      {v.status === 'pending_approval' && data?.can_approve && (
                        <>
                          <IconButton label={`Approve ${v.voucher_no}`} onClick={() => act(v, 'approve')}>
                            <Check size={15} />
                          </IconButton>
                          <IconButton label={`Reject ${v.voucher_no}`}
                            onClick={() => setNoting({ voucher: v, action: 'reject' })}>
                            <X size={15} />
                          </IconButton>
                        </>
                      )}
                      {v.status === 'approved' && data?.can_edit && (
                        <IconButton label={`Mark ${v.voucher_no} paid`} onClick={() => act(v, 'pay')}>
                          <Banknote size={15} />
                        </IconButton>
                      )}
                      {v.status === 'pending_approval' && data?.can_edit && (
                        <IconButton label={`Edit ${v.voucher_no}`} onClick={() => setEditing(v)}>
                          <Pencil size={15} />
                        </IconButton>
                      )}
                      {(v.status === 'pending_approval' || v.status === 'approved') && data?.can_edit && (
                        <IconButton label={`Cancel ${v.voucher_no}`}
                          onClick={() => setNoting({ voucher: v, action: 'cancel' })}>
                          <Ban size={15} />
                        </IconButton>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="border-t border-slate-100 px-5 py-3 text-sm text-slate-500">
          {data?.total ?? 0} voucher{(data?.total ?? 0) === 1 ? '' : 's'}
        </p>
      </div>

      {editing && data && (
        <VoucherModal propertyId={propertyId} list={data}
          voucher={editing === 'new' ? null : editing}
          onClose={() => setEditing(null)} onDone={done} />
      )}
      {noting && (
        <NoteModal propertyId={propertyId} target={noting}
          onClose={() => setNoting(null)} onDone={done} />
      )}
    </div>
  )
}

function IconButton({ label, onClick, children }: {
  label: string; onClick: () => void; children: React.ReactNode
}) {
  return (
    <button onClick={onClick} title={label} aria-label={label}
      className="rounded-lg p-2 text-slate-400 hover:bg-slate-50 hover:text-brand">
      {children}
    </button>
  )
}

function VoucherModal({ propertyId, list, voucher, onClose, onDone }: {
  propertyId: string; list: ExpenseVoucherList; voucher: ExpenseVoucher | null
  onClose: () => void; onDone: (message: string) => void
}) {
  const [f, setF] = useState({
    expense_date: voucher?.expense_date ?? new Date().toISOString().slice(0, 10),
    payee: voucher?.payee ?? '',
    category: voucher?.category ?? 'maintenance',
    description: voucher?.description ?? '',
    amount: voucher?.amount ?? '',
    tax_amount: voucher?.tax_amount ?? '0',
    method: voucher?.method ?? 'cash',
    reference: voucher?.reference ?? '',
    room_id: voucher?.room_id ?? '',
  })
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const set = (k: keyof typeof f) => (v: string) => setF((p) => ({ ...p, [k]: v }))
  const valid = f.payee.trim() !== '' && Number(f.amount) > 0 && f.expense_date !== ''

  async function save() {
    setErr('')
    setBusy(true)
    const body: ExpenseVoucherIn = {
      expense_date: f.expense_date,
      payee: f.payee.trim(),
      category: f.category,
      description: orNull(f.description),
      amount: Number(f.amount),
      tax_amount: f.tax_amount === '' ? 0 : Number(f.tax_amount),
      method: f.method,
      reference: orNull(f.reference),
      room_id: f.room_id || null,
    }
    try {
      if (voucher) {
        const saved = await updateExpenseVoucher(propertyId, voucher.id, body)
        onDone(`${saved.voucher_no} updated.`)
      } else {
        const saved = await createExpenseVoucher(propertyId, body)
        onDone(`${saved.voucher_no} raised and waiting for approval.`)
      }
    } catch (e) {
      setErr(errorText(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal title={voucher ? `Edit ${voucher.voucher_no}` : 'New Expense Voucher'}
      onClose={onClose} error={err}
      footer={<ModalButtons onClose={onClose} onSave={save} busy={busy}
        disabled={!valid} label={voucher ? 'Save Changes' : 'Raise Voucher'} />}>
      <Field label="Payee" required>
        <input value={f.payee} onChange={(e) => set('payee')(e.target.value)}
          placeholder="Who was paid" className={inputCls} autoFocus />
      </Field>
      <Field label="Expense date" required>
        <DateField className="w-full" value={f.expense_date}
          onChange={set('expense_date')} label="Expense date" />
      </Field>
      <Field label="Category">
        <Select value={f.category} className={inputCls}
          onChange={(e) => set('category')(e.target.value)}>
          {list.categories.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
        </Select>
      </Field>
      <Field label="Payment method">
        <Select value={f.method} className={inputCls}
          onChange={(e) => set('method')(e.target.value)}>
          {list.methods.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
        </Select>
      </Field>
      <Field label="Amount (₹)" required hint="Before tax.">
        <input value={f.amount} type="number" min={0} step="0.01" className={inputCls}
          onChange={(e) => set('amount')(e.target.value)} />
      </Field>
      <Field label="Tax (₹)">
        <input value={f.tax_amount} type="number" min={0} step="0.01" className={inputCls}
          onChange={(e) => set('tax_amount')(e.target.value)} />
      </Field>
      <Field label="Reference" hint="Bill, cheque or transfer number.">
        <input value={f.reference} onChange={(e) => set('reference')(e.target.value)}
          className={inputCls} />
      </Field>
      <Field label="Room" hint="Charges an owned unit on its owner statement.">
        <Select blankIsChoice value={f.room_id} className={inputCls}
          onChange={(e) => set('room_id')(e.target.value)}>
          <option value="">Not for a room</option>
          {list.rooms.map((r) => <option key={r.id} value={r.id}>{r.code}</option>)}
        </Select>
      </Field>
      <Field label="Description" wide>
        <textarea value={f.description} rows={2} maxLength={500}
          className={`${inputCls} resize-none`}
          onChange={(e) => set('description')(e.target.value)} />
      </Field>
    </Modal>
  )
}

function NoteModal({ propertyId, target, onClose, onDone }: {
  propertyId: string; target: NoteAction
  onClose: () => void; onDone: (message: string) => void
}) {
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const rejecting = target.action === 'reject'

  async function save() {
    setErr('')
    setBusy(true)
    try {
      await decideExpenseVoucher(propertyId, target.voucher.id,
        { action: target.action, note: orNull(note) })
      onDone(`${target.voucher.voucher_no} ${rejecting ? 'rejected' : 'cancelled'}.`)
    } catch (e) {
      setErr(errorText(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal title={`${rejecting ? 'Reject' : 'Cancel'} ${target.voucher.voucher_no}`}
      onClose={onClose} error={err}
      footer={<ModalButtons onClose={onClose} onSave={save} busy={busy}
        disabled={rejecting && note.trim() === ''}
        label={rejecting ? 'Reject Voucher' : 'Cancel Voucher'} />}>
      <Field label={rejecting ? 'Why is it rejected?' : 'Reason (optional)'}
        required={rejecting} wide
        hint={rejecting ? 'The person who raised it will see this.' : undefined}>
        <textarea value={note} rows={3} maxLength={300} autoFocus
          className={`${inputCls} resize-none`}
          onChange={(e) => setNote(e.target.value)} />
      </Field>
    </Modal>
  )
}
