/**
 * Transfer, Split and Cut — one capability wearing three names.
 *
 * All three are money moving between two folios on one booking, so they share
 * a backend and, here, a dialog. Splitting opens the second folio then moves
 * lines onto it; transferring moves lines between folios that already exist;
 * cutting closes one and starts its successor.
 *
 * Nothing is ever moved by re-pointing a row. A transfer posts a credit on the
 * folio giving the money up and a debit on the one taking it, so both still
 * add up and the movement is visible on both — which is why the totals on each
 * side change while the sum across them does not.
 *
 * This used to sit behind a "More" button alongside five items that were not
 * folio operations at all. The menu is gone; the dialog it opened is the half
 * worth keeping, so it lives here on its own and the folio toolbar opens it
 * from under "Transfer", where somebody looking for it would think to look.
 */
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, Loader2 } from 'lucide-react'
import Select from './Select'
import {
  listReservationFolios, openFolio, transferFolio, cutFolio,
  type ReservationFull,
} from '../api'
import { errorText } from '../lib/forms'

const field = 'w-full rounded-lg border border-slate-200 px-3 py-2.5 text-sm outline-none focus:border-brand'
const lbl = 'mb-1 block text-xs font-medium text-slate-600'

const money = (v: string | number) => new Intl.NumberFormat('en-IN', {
  style: 'currency', currency: 'INR', minimumFractionDigits: 2,
}).format(Number(v))

export type FolioOp = 'transfer' | 'split' | 'cut'

export default function FolioOpDialog({
  op, r, folioId, preselected, onClose, onDone,
}: {
  op: FolioOp
  r: ReservationFull
  folioId: string
  /** Lines already ticked in the ledger before this opened. Somebody who has
   *  picked the four restaurant charges out of a folio should not have to pick
   *  them a second time in here. */
  preselected?: string[]
  onClose: () => void
  onDone: () => void
}) {
  const folios = useQuery({
    queryKey: ['reservation-folios', r.id],
    queryFn: () => listReservationFolios(r.id, r.property_id),
  })
  const all = folios.data ?? []
  const here = all.find((f) => f.id === folioId)
  const others = all.filter((f) => f.id !== folioId && f.status === 'open')

  const [target, setTarget] = useState('')
  const [entryIds, setEntryIds] = useState<string[]>(preselected ?? [])
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  // Only debits can be moved on their own. A payment belongs to the folio it
  // was taken against, and shifting it would make that folio look unpaid.
  const lines = r.charges.filter((c) => c.entry_type === 'debit')
  const chosen = lines.filter((c) => entryIds.includes(c.id))
  const moving = chosen.reduce((n, c) => n + Number(c.amount), 0)

  const title = op === 'transfer' ? 'Transfer to another folio'
    : op === 'split' ? 'Split folio' : 'Cut folio'

  const valid = op === 'cut' ? true
    : op === 'split' ? entryIds.length > 0
      : target !== '' && entryIds.length > 0

  async function run() {
    setErr(''); setBusy(true)
    try {
      if (op === 'cut') {
        await cutFolio(folioId, r.property_id, reason || null)
      } else {
        const to = op === 'split'
          ? (await openFolio(r.property_id,
            { reservation_id: r.id, type: 'company' })).id
          : target
        await transferFolio(folioId, r.property_id, {
          to_folio_id: to, entry_ids: entryIds, reason: reason || null,
        })
      }
      onDone()
    } catch (e) {
      setErr(errorText(e, 'That could not be done.'))
    } finally { setBusy(false) }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-slate-900/30 p-4"
      onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()}
        className="mt-16 w-full max-w-lg rounded-2xl bg-white p-6 shadow-xl">
        <h2 className="mb-4 text-lg font-semibold text-ink">{title}</h2>

        {op === 'cut' ? (
          <>
            <p className="text-sm text-slate-600">
              Closes {here?.folio_no ?? 'this folio'} and opens a new one for
              everything posted from now on. The closed folio keeps every line
              it has and can be invoiced.
            </p>
            {here && Number(here.balance) !== 0 && (
              <p className="mt-3 flex items-start gap-2 rounded-lg bg-amber-50 px-3 py-2 text-xs text-caution">
                <AlertTriangle size={14} className="mt-0.5 shrink-0" />
                This folio stands at {money(here.balance)}. Settle it, or
                transfer the balance, before cutting — a debt on a closed folio
                is one nobody looks at again.
              </p>
            )}
          </>
        ) : (
          <>
            {op === 'transfer' && (
              <label className="mb-4 block">
                <span className={lbl}>Move to</span>
                <Select className={field} value={target}
                  onChange={(e) => setTarget(e.target.value)}>
                  <option value="">Choose a folio…</option>
                  {others.map((f) => (
                    <option key={f.id} value={f.id}>
                      {f.folio_no} · {f.type} · {money(f.balance)}
                    </option>
                  ))}
                </Select>
                {others.length === 0 && (
                  <span className="mt-1 block text-xs text-slate-500">
                    This booking has only one folio. Use Split Folio to open a
                    second and move lines onto it in one step.
                  </span>
                )}
              </label>
            )}
            {op === 'split' && (
              <p className="mb-4 text-sm text-slate-600">
                Opens a second folio on this booking and moves the lines you
                pick onto it — for the company paying the room and the guest
                paying the rest.
              </p>
            )}

            <span className={lbl}>Which lines</span>
            <div className="max-h-52 overflow-y-auto rounded-lg border border-slate-200">
              {lines.length === 0 ? (
                <p className="px-3 py-6 text-center text-xs text-slate-500">
                  No charges on this folio to move.
                </p>
              ) : lines.map((c) => (
                <label key={c.id}
                  className="flex cursor-pointer items-center gap-2.5 border-b border-slate-100 px-3 py-2 text-sm last:border-0 hover:bg-slate-50">
                  <input type="checkbox" className="accent-brand"
                    checked={entryIds.includes(c.id)}
                    onChange={() => setEntryIds((p) => p.includes(c.id)
                      ? p.filter((x) => x !== c.id) : [...p, c.id])} />
                  <span className="min-w-0 flex-1 truncate text-slate-700">
                    {c.description}
                  </span>
                  <span className="tabular-nums text-slate-600">
                    {money(c.amount)}
                  </span>
                </label>
              ))}
            </div>
            {entryIds.length > 0 && (
              <p className="mt-2 text-sm font-semibold text-brand">
                Moving {money(moving)} across {entryIds.length} line
                {entryIds.length === 1 ? '' : 's'}
              </p>
            )}
          </>
        )}

        <label className="mt-4 block">
          <span className={lbl}>Reason</span>
          <input className={field} value={reason}
            placeholder="Why — shown on both folios"
            onChange={(e) => setReason(e.target.value)} />
        </label>

        {err && (
          <p className="mt-3 flex items-start gap-2 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">
            <AlertTriangle size={14} className="mt-0.5 shrink-0" />{err}
          </p>
        )}

        <div className="mt-5 flex justify-end gap-2">
          <button onClick={onClose}
            className="rounded-lg border border-slate-200 px-4 py-2 text-sm font-semibold text-slate-600 hover:bg-slate-50">
            Cancel
          </button>
          <button onClick={() => void run()} disabled={!valid || busy}
            className="flex items-center gap-2 rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white enabled:hover:bg-brand-dark disabled:opacity-50">
            {busy && <Loader2 size={15} className="animate-spin" />}
            {op === 'cut' ? 'Cut folio'
              : op === 'split' ? 'Split' : 'Transfer'}
          </button>
        </div>
      </div>
    </div>
  )
}
