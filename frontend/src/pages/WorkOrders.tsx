import { useEffect, useState } from 'react'
import { keepPreviousData, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle, CheckCircle2, Loader2, Pencil, Plus, Search, Wrench,
} from 'lucide-react'
import {
  createWorkOrder, listWorkOrders, updateWorkOrder,
  type WorkOrder, type WorkOrderIn, type WorkOrderList,
} from '../api'
import DateField from '../components/DateField'
import EntityCards, { type Tone } from '../components/EntityCards'
import { StayLayoutToggle, useListLayout } from '../lib/listLayout'
import { Badge, Field, Modal, ModalButtons } from '../components/FormBits'
import Select from '../components/Select'
import { useActivePropertyId } from '../hooks/useProperty'
import { FILTER_SELECT } from '../lib/controls'
import { fmtDate } from '../lib/dates'
import { errorText, inputCls, inr, orNull } from '../lib/forms'

/**
 * Work orders — what is broken, who is fixing it and by when.
 *
 * Kept apart from housekeeping tasks on purpose: a task ends when a room is
 * clean, a work order when a fault is fixed, and the fault may be a pump
 * nowhere near a room. Status timestamps are stamped by the server as the job
 * moves, so the Work Order List report measures how long work stayed open
 * rather than trusting anyone to remember.
 */

/** The stripe colour, from the same priority the badge shows. */
const CARD_TONE: Record<string, Tone> = {
  urgent: 'bad', high: 'warn', medium: 'info', low: 'muted',
}

const PRIORITY_TONE: Record<string, string> = {
  low: 'bg-slate-75 text-slate-600', medium: 'bg-sky-50 text-sky-700',
  high: 'bg-amber-50 text-amber-700', urgent: 'bg-red-50 text-red-700',
}
const STATUS_TONE: Record<string, string> = {
  open: 'bg-amber-50 text-amber-700', in_progress: 'bg-sky-50 text-sky-700',
  on_hold: 'bg-slate-75 text-slate-600', completed: 'bg-emerald-50 text-emerald-700',
  cancelled: 'bg-slate-75 text-slate-400',
}


/** Work orders as cards.
 *
 * A job is a thing somebody picks up and does, which is what a card is for.
 * The priority and whether it is overdue are the two facts that decide what
 * gets done next, so they carry the stripe and the only red on the card.
 */
function WorkOrderCards({ rows, loading, canEdit, onEdit }: {
  rows: WorkOrder[]
  loading: boolean
  canEdit: boolean
  onEdit: (w: WorkOrder) => void
}) {
  return (
    <EntityCards
      loading={loading}
      empty="No work order matches these filters."
      cards={rows.map((w) => ({
        key: w.id,
        // Overdue outranks priority: a low-priority job three days late is
        // the one being asked about.
        tone: (w.overdue ? 'bad' : CARD_TONE[w.priority] ?? 'muted') as Tone,
        badge: <Wrench size={16} />,
        title: w.title,
        subtitle: `${w.number} · ${fmtDate(w.created_at)}`,
        status: <Badge tone={STATUS_TONE[w.status] ?? ''}>{w.status_label}</Badge>,
        actions: canEdit ? (
          <button onClick={(e) => { e.stopPropagation(); onEdit(w) }}
            title={`Edit ${w.number}`} aria-label={`Edit ${w.number}`}
            className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-50 hover:text-brand">
            <Pencil size={15} />
          </button>
        ) : undefined,
        facts: [
          { label: 'Where', value: w.where },
          { label: 'Category', value: w.category_label },
          {
            label: 'Assigned to',
            value: w.assigned_name
              ?? <span className="text-slate-400">Unassigned</span>,
          },
        ],
        footer: (
          <div className="flex items-center justify-between gap-2 border-t border-slate-100 pt-3 text-sm">
            <Badge tone={PRIORITY_TONE[w.priority] ?? ''}>{w.priority_label}</Badge>
            {w.due_date ? (
              <span className={w.overdue
                ? 'flex items-center gap-1 font-medium text-red-600'
                : 'text-slate-600'}>
                {w.overdue && <AlertTriangle size={13} />} Due {fmtDate(w.due_date)}
              </span>
            ) : <span className="text-xs text-slate-400">No due date</span>}
          </div>
        ),
        onClick: canEdit ? () => onEdit(w) : undefined,
      }))}
    />
  )
}

export default function WorkOrders() {
  const propertyId = useActivePropertyId()
  const qc = useQueryClient()
  const [status, setStatus] = useState('active')
  const [priority, setPriority] = useState('')
  const [search, setSearch] = useState('')
  const [q, setQ] = useState('')
  const [editing, setEditing] = useState<WorkOrder | 'new' | null>(null)
  const [toast, setToast] = useState('')

  useEffect(() => {
    const t = setTimeout(() => setQ(search), 300)
    return () => clearTimeout(t)
  }, [search])

  const { data, isLoading, isFetching, isError, error } = useQuery({
    queryKey: ['work-orders', propertyId, status, priority, q],
    queryFn: () => listWorkOrders(propertyId, {
      status: status || undefined, priority: priority || undefined,
      q: q || undefined,
    }),
    enabled: propertyId !== '',
    placeholderData: keepPreviousData,
  })
  const rows = data?.rows ?? []
  const [layout, chooseLayout] = useListLayout('work_orders')

  function done(message: string) {
    setToast(message)
    setTimeout(() => setToast(''), 3500)
    qc.invalidateQueries({ queryKey: ['work-orders'] })
    setEditing(null)
  }

  const counts = data?.counts ?? {}
  const sel = `${FILTER_SELECT} bg-white outline-none focus:border-brand`

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <h1 className="flex items-center gap-2 text-display text-ink">
          <Wrench size={26} className="text-brand" /> Work Orders
        </h1>
        <div className="flex flex-wrap items-center gap-2">
          {data?.can_create && (
            <button onClick={() => setEditing('new')}
              className="flex items-center gap-1.5 whitespace-nowrap rounded-lg bg-brand px-3 py-2 text-sm font-medium text-white hover:bg-brand-dark disabled:opacity-40">
              <Plus size={15} /> New Work Order
            </button>
          )}
        </div>
      </div>

      {toast && (
        <p className="flex items-start gap-2 rounded-xl bg-emerald-50 px-4 py-3 text-sm text-emerald-700">
          <CheckCircle2 size={16} className="mt-0.5 shrink-0" /> {toast}
        </p>
      )}

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {([
          ['Open', counts.open ?? 0, 'text-slate-800'],
          ['In progress', counts.in_progress ?? 0, 'text-slate-800'],
          ['On hold', counts.on_hold ?? 0, 'text-slate-800'],
          ['Overdue', counts.overdue ?? 0, (counts.overdue ?? 0) > 0 ? 'text-red-600' : 'text-slate-800'],
        ] as const).map(([label, n, tone]) => (
          <div key={label} className="rounded-xl border border-slate-100 bg-white p-4">
            <p className="text-xs font-medium text-slate-500">{label}</p>
            <p className={`mt-1 text-xl font-semibold ${tone}`}>{n}</p>
          </div>
        ))}
      </div>

      {/* One row: status and priority selects, then search and the layout
          toggle at the right, as on Reservations. */}
      <div className="flex flex-wrap items-center gap-2">
        <Select blankIsChoice value={status} onChange={(e) => setStatus(e.target.value)}
          aria-label="Status" className={sel}>
          <option value="active">Open work</option>
          {(data?.statuses ?? []).map((s) => (
            <option key={s.value} value={s.value}>{s.label}</option>
          ))}
          <option value="">All</option>
        </Select>
        <Select blankIsChoice value={priority} onChange={(e) => setPriority(e.target.value)}
          aria-label="Priority" className={sel}>
          <option value="">All priorities</option>
          {(data?.priorities ?? []).map((p) => (
            <option key={p.value} value={p.value}>{p.label}</option>
          ))}
        </Select>
        {isFetching && !isLoading && <Loader2 size={15} className="animate-spin text-slate-300" />}
        <div className="flex min-w-[240px] flex-1 items-center justify-end gap-2">
          <div className="relative min-w-0 max-w-sm flex-1">
            <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <input value={search} onChange={(e) => setSearch(e.target.value)}
              placeholder="Search by issue, room, location or number…"
              aria-label="Search work orders"
              className="w-full rounded-lg border border-slate-200 bg-slate-50 py-2 pl-9 pr-3 text-sm outline-none focus:border-brand" />
          </div>
          <StayLayoutToggle layout={layout} onChange={chooseLayout} />
        </div>
      </div>

      {layout === 'cards' && (
        <WorkOrderCards rows={rows} loading={isLoading}
          canEdit={!!data?.can_edit} onEdit={setEditing} />
      )}

      <div className={`overflow-hidden rounded-2xl border border-slate-100 bg-white ${
        layout === 'cards' ? 'border-0' : ''}`}>
        <div className={`overflow-x-auto ${layout === 'cards' ? 'hidden' : ''}`}>
          <table className="w-full text-sm">
            <thead className="border-b border-slate-100 bg-slate-50/60 text-left text-slate-600">
              <tr>
                <th className="px-5 py-3 font-semibold">Work order</th>
                <th className="px-4 py-3 font-semibold">Issue</th>
                <th className="px-4 py-3 font-semibold">Category</th>
                <th className="px-4 py-3 font-semibold">Priority</th>
                <th className="px-4 py-3 font-semibold">Assigned to</th>
                <th className="px-4 py-3 font-semibold">Due</th>
                <th className="px-4 py-3 font-semibold">Status</th>
                <th className="w-12 px-4 py-3" />
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-50">
              {(isLoading || propertyId === '') && (
                <tr><td colSpan={8} className="p-10 text-center text-slate-400">
                  <Loader2 size={18} className="mx-auto animate-spin" />
                </td></tr>
              )}
              {isError && (
                <tr><td colSpan={8} className="p-10 text-center text-sm text-red-600">
                  {errorText(error, 'Work orders could not be loaded.')}
                </td></tr>
              )}
              {!isLoading && !isError && rows.length === 0 && (
                <tr><td colSpan={8} className="p-10 text-center text-sm text-slate-400">
                  No work order matches. Report a fault with New Work Order.
                </td></tr>
              )}
              {rows.map((w) => (
                <tr key={w.id} className="text-slate-700 hover:bg-slate-50/60">
                  <td className="px-5 py-3">
                    <span className="block font-semibold text-slate-800">{w.number}</span>
                    <span className="block text-xs text-slate-400">{fmtDate(w.created_at)}</span>
                  </td>
                  <td className="px-4 py-3">
                    <span className="block font-medium text-slate-800">{w.title}</span>
                    <span className="block text-xs text-slate-400">{w.where}</span>
                  </td>
                  <td className="px-4 py-3 text-slate-600">{w.category_label}</td>
                  <td className="px-4 py-3">
                    <Badge tone={PRIORITY_TONE[w.priority] ?? ''}>{w.priority_label}</Badge>
                  </td>
                  <td className="px-4 py-3 text-slate-600">
                    {w.assigned_name ?? <span className="text-slate-300">Unassigned</span>}
                  </td>
                  <td className="whitespace-nowrap px-4 py-3">
                    {w.due_date ? (
                      <span className={w.overdue ? 'flex items-center gap-1 font-medium text-red-600' : 'text-slate-600'}>
                        {w.overdue && <AlertTriangle size={13} />} {fmtDate(w.due_date)}
                      </span>
                    ) : <span className="text-slate-300">—</span>}
                  </td>
                  <td className="px-4 py-3">
                    <Badge tone={STATUS_TONE[w.status] ?? ''}>{w.status_label}</Badge>
                  </td>
                  <td className="px-4 py-3 text-right">
                    {data?.can_edit && (
                      <button onClick={() => setEditing(w)} title={`Edit ${w.number}`}
                        aria-label={`Edit ${w.number}`}
                        className="rounded-lg p-2 text-slate-400 hover:bg-slate-50 hover:text-brand">
                        <Pencil size={15} />
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="border-t border-slate-100 px-5 py-3 text-sm text-slate-500">
          {data?.total ?? 0} work order{(data?.total ?? 0) === 1 ? '' : 's'}
        </p>
      </div>

      {editing && data && (
        <WorkOrderModal propertyId={propertyId} list={data}
          order={editing === 'new' ? null : editing}
          onClose={() => setEditing(null)} onDone={done} />
      )}
    </div>
  )
}

function WorkOrderModal({ propertyId, list, order, onClose, onDone }: {
  propertyId: string; list: WorkOrderList; order: WorkOrder | null
  onClose: () => void; onDone: (message: string) => void
}) {
  const [f, setF] = useState({
    title: order?.title ?? '',
    room_id: order?.room_id ?? '',
    location: order?.location ?? '',
    category: order?.category ?? 'other',
    priority: order?.priority ?? 'medium',
    status: order?.status ?? 'open',
    assigned_to: order?.assigned_to ?? '',
    due_date: order?.due_date ?? '',
    cost: order?.cost ?? '',
    description: order?.description ?? '',
    resolution: order?.resolution ?? '',
  })
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const set = (k: keyof typeof f) => (v: string) => setF((p) => ({ ...p, [k]: v }))
  const valid = f.title.trim() !== '' && (f.room_id !== '' || f.location.trim() !== '')

  async function save() {
    setErr('')
    setBusy(true)
    const body: WorkOrderIn = {
      title: f.title.trim(),
      room_id: f.room_id || null,
      location: orNull(f.location),
      category: f.category,
      priority: f.priority,
      status: f.status,
      assigned_to: f.assigned_to || null,
      due_date: f.due_date || null,
      cost: f.cost === '' ? null : Number(f.cost),
      description: orNull(f.description),
      resolution: orNull(f.resolution),
    }
    try {
      if (order) {
        const saved = await updateWorkOrder(propertyId, order.id, body)
        onDone(`${saved.number} updated.`)
      } else {
        const saved = await createWorkOrder(propertyId, body)
        onDone(`${saved.number} raised.`)
      }
    } catch (e) {
      setErr(errorText(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal title={order ? `Edit ${order.number}` : 'New Work Order'} onClose={onClose}
      error={err}
      footer={<ModalButtons onClose={onClose} onSave={save} busy={busy}
        disabled={!valid} label={order ? 'Save Changes' : 'Raise Work Order'} />}>
      <Field label="Issue" required wide>
        <input value={f.title} onChange={(e) => set('title')(e.target.value)}
          placeholder="e.g. AC not cooling" className={inputCls} autoFocus />
      </Field>
      <Field label="Room" hint="Leave empty for work elsewhere on the property.">
        <Select blankIsChoice value={f.room_id} className={inputCls}
          onChange={(e) => set('room_id')(e.target.value)}>
          <option value="">No room</option>
          {list.rooms.map((r) => <option key={r.id} value={r.id}>{r.code}</option>)}
        </Select>
      </Field>
      <Field label="Location" hint="Required when no room is chosen.">
        <input value={f.location} onChange={(e) => set('location')(e.target.value)}
          placeholder="e.g. Pool pump room" className={inputCls} />
      </Field>
      <Field label="Category">
        <Select value={f.category} className={inputCls}
          onChange={(e) => set('category')(e.target.value)}>
          {list.categories.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
        </Select>
      </Field>
      <Field label="Priority">
        <Select value={f.priority} className={inputCls}
          onChange={(e) => set('priority')(e.target.value)}>
          {list.priorities.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
        </Select>
      </Field>
      <Field label="Assigned to">
        <Select blankIsChoice value={f.assigned_to} className={inputCls}
          onChange={(e) => set('assigned_to')(e.target.value)}>
          <option value="">Unassigned</option>
          {list.assignees.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
        </Select>
      </Field>
      <Field label="Due date">
        <DateField className="w-full" value={f.due_date} onChange={set('due_date')}
          label="Due date" />
      </Field>
      <Field label="Status" hint="In progress and Completed are timestamped automatically.">
        <Select value={f.status} className={inputCls}
          onChange={(e) => set('status')(e.target.value)}>
          {list.statuses.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
        </Select>
      </Field>
      <Field label="Cost (₹)" hint={order?.cost ? `Recorded: ${inr(order.cost)}` : undefined}>
        <input value={f.cost} type="number" min={0} step="0.01" className={inputCls}
          onChange={(e) => set('cost')(e.target.value)} />
      </Field>
      <Field label="Details" wide>
        <textarea value={f.description} rows={3} className={`${inputCls} resize-none`}
          onChange={(e) => set('description')(e.target.value)} />
      </Field>
      <Field label="Resolution" wide hint="What was done, once it is fixed.">
        <textarea value={f.resolution} rows={2} className={`${inputCls} resize-none`}
          onChange={(e) => set('resolution')(e.target.value)} />
      </Field>
    </Modal>
  )
}
