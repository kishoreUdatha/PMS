/**
 * The rooming list: who is in each room the group has taken.
 *
 * Names arrive late, in a spreadsheet, and often long after the rooms are
 * held — so this is built around that rather than against it. Rooms may sit
 * unnamed and the count says how many; names are pasted in bulk because that
 * is how they turn up; and a name can be corrected in place, because the
 * spelling on a group's list and the spelling on a passport rarely agree.
 *
 * A name is not a booking of its own. Thirty names make one booking with
 * thirty named rooms, because thirty separate reservations have to be found,
 * changed and cancelled thirty times and no longer add up to a group.
 */
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { AlertTriangle, Check, ListChecks, Loader2, Pencil, X } from 'lucide-react'
import Select from './Select'
import {
  getRoomingList, setRoomGuest, importRoomingList,
  type GroupBlock, type RoomingRow,
} from '../api'
import { errorText } from '../lib/forms'

const field = 'w-full rounded-lg border border-slate-200 px-3 py-2.5 text-sm outline-none focus:border-brand'
const lbl = 'mb-1 block text-xs font-medium text-slate-500'

function Th({ children, right }: { children?: React.ReactNode; right?: boolean }) {
  return <th className={`px-4 py-3 font-medium ${right ? 'text-right' : ''}`}>
    {children}</th>
}

export default function RoomingPanel({ blockId, propertyId, block, onChanged }: {
  blockId: string; propertyId: string; block: GroupBlock; onChanged: () => void
}) {
  const [adding, setAdding] = useState(false)
  const [err, setErr] = useState('')
  const q = useQuery({
    queryKey: ['rooming-list', blockId],
    queryFn: () => getRoomingList(blockId, propertyId),
  })
  const list = q.data

  const refresh = async () => { await q.refetch(); onChanged() }

  if (!list) {
    return <p className="py-8 text-center text-sm text-slate-400">
      <Loader2 className="mx-auto mb-2 h-5 w-5 animate-spin" /> Loading…</p>
  }

  return (
    <div className="mt-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-slate-600">
          <strong className="text-slate-800">{list.named}</strong> named
          {list.unnamed > 0 && (
            <>
              {' · '}
              <strong className="text-amber-700">{list.unnamed}</strong> still
              to name
            </>
          )}
          {list.rooms.length === 0 && ' — no rooms taken from this block yet'}
        </p>
        {block.status === 'open' && (
          <button onClick={() => { setAdding(true); setErr('') }}
            className="flex items-center gap-1.5 rounded-lg bg-brand px-3 py-1.5 text-xs font-semibold text-white hover:bg-brand/90">
            <ListChecks size={14} /> Add names
          </button>
        )}
      </div>

      {adding && (
        <AddNames blockId={blockId} propertyId={propertyId} block={block}
          onClose={() => setAdding(false)}
          onDone={async () => { setAdding(false); await refresh() }}
          onError={setErr} />
      )}
      {err && (
        <p className="mt-3 flex items-start gap-2 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">
          <AlertTriangle size={14} className="mt-0.5 shrink-0" />{err}
        </p>
      )}

      {list.rooms.length > 0 && (
        <div className="mt-4 overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <Th>Guest</Th><Th>Room type</Th><Th>Room</Th>
                <Th>Dates</Th><Th right>Occupancy</Th><Th>Booking</Th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {list.rooms.map((r) => (
                <RoomRow key={r.reservation_unit_id} r={r}
                  propertyId={propertyId} onSaved={refresh} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function RoomRow({ r, propertyId, onSaved }: {
  r: RoomingRow; propertyId: string; onSaved: () => void
}) {
  const [editing, setEditing] = useState(false)
  const [name, setName] = useState(r.guest_name ?? '')
  const [busy, setBusy] = useState(false)

  async function save() {
    setBusy(true)
    try {
      // An empty box clears the name rather than saving a blank one: the room
      // goes back to "whoever booked", which is true, instead of being named
      // after nobody.
      await setRoomGuest(r.reservation_unit_id, propertyId,
        name.trim() ? { full_name: name.trim() } : {})
      setEditing(false)
      onSaved()
    } finally { setBusy(false) }
  }

  return (
    <tr className="hover:bg-slate-50">
      <td className="px-4 py-2.5">
        {editing ? (
          <div className="flex items-center gap-1.5">
            <input autoFocus
              className="w-48 rounded-lg border border-slate-200 px-2 py-1.5 text-sm outline-none focus:border-brand"
              value={name} placeholder="Who is in this room"
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') void save() }} />
            <button onClick={() => void save()} disabled={busy}
              className="rounded-lg bg-brand p-1.5 text-white disabled:opacity-50">
              {busy ? <Loader2 size={13} className="animate-spin" />
                : <Check size={13} />}
            </button>
            <button onClick={() => { setEditing(false); setName(r.guest_name ?? '') }}
              className="rounded-lg border border-slate-200 p-1.5 text-slate-400">
              <X size={13} />
            </button>
          </div>
        ) : (
          <button onClick={() => setEditing(true)}
            className="group flex items-center gap-1.5 text-left">
            {r.guest_name ? (
              <span className="font-medium text-slate-700">{r.guest_name}</span>
            ) : (
              <span className="rounded bg-amber-50 px-1.5 py-0.5 text-xs font-medium text-amber-700">
                Not yet named
              </span>
            )}
            <Pencil size={12}
              className="text-slate-300 opacity-0 group-hover:opacity-100" />
          </button>
        )}
      </td>
      <td className="px-4 py-2.5 text-slate-600">{r.room_type}</td>
      <td className="px-4 py-2.5 text-slate-600">
        {r.room_code ?? <span className="text-slate-400">Not assigned</span>}
      </td>
      <td className="px-4 py-2.5 text-slate-600">
        {r.arrival_date} → {r.departure_date}
      </td>
      <td className="px-4 py-2.5 text-right tabular-nums text-slate-600">
        {r.adults}A{r.children > 0 ? ` ${r.children}C` : ''}
      </td>
      <td className="px-4 py-2.5">
        <Link to={`/reservations/list?q=${r.reservation_number}`}
          className="font-medium text-brand hover:underline">
          {r.reservation_number}
        </Link>
        {!r.guest_name && r.booked_by && (
          <span className="block text-xs text-slate-400">
            under {r.booked_by}
          </span>
        )}
      </td>
    </tr>
  )
}

function AddNames({ blockId, propertyId, block, onClose, onDone, onError }: {
  blockId: string; propertyId: string; block: GroupBlock
  onClose: () => void; onDone: () => void; onError: (m: string) => void
}) {
  // Only types this block still holds rooms of. Offering a type with nothing
  // left produces a refusal the person could not have predicted.
  const available = block.lines.filter((l) => l.rooms_still_held > 0)
  const [roomTypeId, setRoomTypeId] = useState(available[0]?.room_type_id ?? '')
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)

  const names = text.split('\n').map((l) => l.trim()).filter(Boolean)
  const line = block.lines.find((l) => l.room_type_id === roomTypeId)
  const spare = line?.rooms_still_held ?? 0
  const tooMany = names.length > spare

  async function go() {
    setBusy(true)
    onError('')
    try {
      await importRoomingList(blockId, propertyId, {
        room_type_id: roomTypeId,
        entries: names.map((n) => ({ full_name: n })),
      })
      onDone()
    } catch (e) {
      onError(errorText(e, 'The names could not be added.'))
    } finally { setBusy(false) }
  }

  if (available.length === 0) {
    return (
      <p className="mt-3 rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-500">
        This block has no rooms left to name — every room it held has been
        taken. Add rooms to the block, or book the extras from{' '}
        <Link to="/reservations/new" className="font-semibold text-brand">
          New Reservation
        </Link>.
      </p>
    )
  }

  return (
    <div className="mt-3 rounded-xl border border-slate-200 bg-slate-50/60 p-4">
      <div className="flex flex-wrap items-end gap-3">
        <div className="w-56">
          <label className={lbl}>Room type</label>
          <Select className={field} value={roomTypeId}
            onChange={(e) => setRoomTypeId(e.target.value)}>
            {available.map((l) => (
              <option key={l.room_type_id} value={l.room_type_id}>
                {l.room_type} ({l.rooms_still_held} left)
              </option>
            ))}
          </Select>
        </div>
        <p className="mb-2 max-w-sm text-xs text-slate-500">
          One name per line. Each becomes a room drawn from this block, all on
          one booking.
        </p>
      </div>
      <textarea rows={5} value={text} onChange={(e) => setText(e.target.value)}
        placeholder={'Anitha Rao\nBhaskar Nair\nChitra Menon'}
        className="mt-2 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm outline-none focus:border-brand" />
      <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-slate-500">
          {names.length} name{names.length === 1 ? '' : 's'}
          {tooMany && (
            <span className="ml-1 font-medium text-red-600">
              — the block only has {spare} room{spare === 1 ? '' : 's'} of this
              type left
            </span>
          )}
        </p>
        <div className="flex gap-2">
          <button onClick={onClose}
            className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-semibold text-slate-600">
            Cancel
          </button>
          <button onClick={() => void go()}
            disabled={busy || names.length === 0 || tooMany || !roomTypeId}
            className="flex items-center gap-1.5 rounded-lg bg-brand px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-50">
            {busy && <Loader2 size={12} className="animate-spin" />}
            Add {names.length || ''} room{names.length === 1 ? '' : 's'}
          </button>
        </div>
      </div>
    </div>
  )
}
