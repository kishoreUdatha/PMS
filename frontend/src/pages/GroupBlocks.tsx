/**
 * Group blocks — rooms held for a group, and given back when nobody takes them.
 *
 * A block is what sits between "thirty rooms in March, probably" and thirty
 * named bookings. Without one, a desk either invents thirty guest names or
 * records nothing and hopes.
 *
 * The number that matters on this screen is **still held**: rooms the hotel is
 * carrying for a group that has not claimed them. That is the figure a revenue
 * manager acts on, so it is the one the list leads with, and it is shown
 * against the cut-off date that will take it away.
 */
import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import {
  Users, Plus, Loader2, X, AlertTriangle, CalendarClock, Trash2,
} from 'lucide-react'
import Select from '../components/Select'
import { Crumbs } from '../components/Crumbs'
import RoomingPanel from '../components/RoomingPanel'
import { useActivePropertyId, useOrgId } from '../hooks/useProperty'
import {
  listGroupBlocks, createGroupBlock, getGroupBlock, releaseGroupBlock,
  setBlockCommitment,
  listRoomTypes, listCommercialAccounts,
  type GroupBlockRow, type GroupBlock,
} from '../api'
import { errorText } from '../lib/forms'

const field = 'w-full rounded-lg border border-slate-200 px-3 py-2.5 text-sm outline-none focus:border-brand'
const lbl = 'mb-1 block text-xs font-medium text-slate-500'

const money = (v: string | number | null) =>
  v === null || v === undefined || v === ''
    ? '—'
    : new Intl.NumberFormat('en-IN', {
      style: 'currency', currency: 'INR', maximumFractionDigits: 0,
    }).format(Number(v))

const STATUS_TONE: Record<string, string> = {
  open: 'bg-emerald-50 text-emerald-700',
  released: 'bg-slate-100 text-slate-600',
  cancelled: 'bg-red-50 text-red-700',
}

/** Days until a cut-off, or null when there is none or it has passed. */
function daysToCutOff(cutOff: string | null): number | null {
  if (!cutOff) return null
  const d = Math.ceil(
    (new Date(cutOff + 'T00:00:00').getTime() - Date.now()) / 86_400_000)
  return d >= 0 ? d : null
}

export default function GroupBlocks() {
  const propertyId = useActivePropertyId()
  const qc = useQueryClient()
  const [creating, setCreating] = useState(false)
  const [openId, setOpenId] = useState<string | null>(null)
  const [statusFilter, setStatusFilter] = useState('')

  const blocks = useQuery({
    queryKey: ['group-blocks', propertyId, statusFilter],
    queryFn: () => listGroupBlocks(propertyId, statusFilter || undefined),
    enabled: propertyId !== '',
  })

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['group-blocks'] })
    qc.invalidateQueries({ queryKey: ['group-block'] })
  }

  if (propertyId === '') {
    return <p className="text-sm text-slate-500">Pick a property first.</p>
  }

  const rows = blocks.data ?? []
  const open = rows.filter((r) => r.status === 'open')
  // Definite blocks only. The tile says "off sale", and after the
  // commitment fix a tentative block takes nothing off sale -- counting it
  // here reported rooms as unsellable that were on sale the whole time,
  // which is the same lie the old behaviour told, just on a different
  // screen.
  const stillHeld = open
    .filter((r) => r.commitment === 'definite')
    .reduce((n, r) => n + Math.max(r.rooms_blocked - r.rooms_picked_up, 0), 0)
  //: Wanted rather than held. Worth showing, because a pipeline of
  //: tentative rooms is what a manager is deciding about -- but it is not
  //: inventory, and it does not share a tile with rooms that are.
  const tentativeRooms = open
    .filter((r) => r.commitment !== 'definite')
    .reduce((n, r) => n + Math.max(r.rooms_blocked - r.rooms_picked_up, 0), 0)

  return (
    <div>
      <Crumbs trail={[{ label: 'Reservations', to: '/reservations' },
        { label: 'Group Blocks' }]} />

      <div className="mt-2 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="flex items-center gap-2 text-3xl font-bold text-ink">
            <Users size={26} className="text-brand" /> Group Blocks
          </h1>
          <p className="mt-1 text-sm text-slate-500">
            Rooms held for a group without being sold. What nobody takes goes
            back on sale at the cut-off.
          </p>
        </div>
        <button onClick={() => setCreating(true)}
          className="flex items-center gap-2 rounded-xl bg-brand px-4 py-2.5 text-sm font-semibold text-white hover:bg-brand/90">
          <Plus size={16} /> New Block
        </button>
      </div>

      <div className="mt-5 rounded-2xl border border-slate-100 bg-white shadow-sm">
        {/* The figures share the table's header line rather than sitting in
            four cards above it. As cards they were about 350px of vertical
            space spent on four numbers that are usually zero, pushing the
            thing the screen is actually for -- the blocks -- below the fold
            on a laptop. The Cashiering Centre made the same journey from
            cards to a single line, for the same reason. */}
        <div className="flex flex-wrap items-center justify-between gap-x-5 gap-y-2 border-b border-slate-100 px-5 py-3">
          <div className="flex flex-wrap items-baseline gap-x-5 gap-y-1">
            <h2 className="text-sm font-semibold text-slate-700">
              {rows.length} block{rows.length === 1 ? '' : 's'}
            </h2>
            <Figure n={open.length} label="open" />
            <Figure n={stillHeld} label="held" tone="text-amber-600"
              title="Rooms off sale for a definite block and not yet claimed" />
            <Figure n={tentativeRooms} label="tentative" tone="text-slate-500"
              title="Asked for but not held — these rooms are still on sale" />
            <Figure n={open.reduce((n, r) => n + r.rooms_picked_up, 0)}
              label="picked up" tone="text-emerald-600"
              title="Named bookings drawn from a block" />
          </div>
          <Select className="w-44 rounded-lg border border-slate-200 px-3 py-2 text-sm"
            value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
            <option value="">All statuses</option>
            <option value="open">Open</option>
            <option value="released">Released</option>
            <option value="cancelled">Cancelled</option>
          </Select>
        </div>

        {blocks.isLoading ? (
          <p className="px-5 py-10 text-center text-sm text-slate-400">
            <Loader2 className="mx-auto mb-2 h-5 w-5 animate-spin" /> Loading…
          </p>
        ) : rows.length === 0 ? (
          <div className="px-5 py-12 text-center">
            <p className="text-sm text-slate-500">No blocks yet.</p>
            <p className="mx-auto mt-1 max-w-md text-xs text-slate-400">
              A block holds rooms for a group without selling them — so a
              wedding can take them one name at a time, and the rooms nobody
              claims go back on sale on the date you agreed.
            </p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <Th>Block</Th><Th>Group</Th><Th>Dates</Th><Th>Cut-off</Th>
                  <Th right>Blocked</Th><Th right>Picked up</Th>
                  <Th right>Still held</Th><Th>Status</Th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {rows.map((b) => (
                  <BlockRow key={b.id} b={b} onOpen={() => setOpenId(b.id)} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {creating && (
        <CreateBlock propertyId={propertyId} onClose={() => setCreating(false)}
          onSaved={() => { setCreating(false); refresh() }} />
      )}
      {openId && (
        <BlockDetail blockId={openId} propertyId={propertyId}
          onClose={() => setOpenId(null)} onChanged={refresh} />
      )}
    </div>
  )
}

function Th({ children, right }: { children: React.ReactNode; right?: boolean }) {
  return <th className={`px-4 py-3 font-medium ${right ? 'text-right' : ''}`}>
    {children}</th>
}

/** One figure on a line of figures.
 *
 * The caption that used to sit under each card now lives on `title`: it is
 * something you read once to learn what "held" means, not every time the
 * page loads.
 */
function Figure({ n, label, tone, title }: {
  n: number; label: string; tone?: string; title?: string
}) {
  return (
    <span className="flex items-baseline gap-1.5" title={title}>
      <span className={`text-base font-semibold ${tone ?? 'text-ink'}`}>{n}</span>
      <span className="text-xs text-slate-500">{label}</span>
    </span>
  )
}

function BlockRow({ b, onOpen }: { b: GroupBlockRow; onOpen: () => void }) {
  const held = Math.max(b.rooms_blocked - b.rooms_picked_up, 0)
  const days = daysToCutOff(b.cut_off_date)
  return (
    <tr onClick={onOpen} className="cursor-pointer hover:bg-slate-50">
      <td className="px-4 py-3 font-semibold text-slate-700">{b.code}</td>
      <td className="px-4 py-3">
        <span className="font-medium text-slate-700">{b.name}</span>
        {b.account_name && (
          <span className="block text-xs text-slate-400">{b.account_name}</span>
        )}
        <span className="mt-0.5 inline-block rounded bg-slate-100 px-1.5 text-[11px] text-slate-500">
          {b.commitment === 'definite' ? 'Definite' : 'Tentative'}
        </span>
      </td>
      <td className="px-4 py-3 text-slate-600">
        {b.arrival_date} → {b.departure_date}
      </td>
      <td className="px-4 py-3 text-slate-600">
        {b.cut_off_date ?? <span className="text-slate-400">None agreed</span>}
        {/* The warning that matters: rooms about to go back on sale. */}
        {b.status === 'open' && days !== null && days <= 7 && (
          <span className="ml-1 inline-flex items-center gap-1 rounded bg-amber-50 px-1.5 text-[11px] font-medium text-amber-700">
            <CalendarClock size={11} />
            {days === 0 ? 'today' : `${days}d`}
          </span>
        )}
      </td>
      <td className="px-4 py-3 text-right tabular-nums text-slate-700">
        {b.rooms_blocked}</td>
      <td className="px-4 py-3 text-right tabular-nums text-emerald-700">
        {b.rooms_picked_up}</td>
      <td className="px-4 py-3 text-right tabular-nums font-semibold text-amber-700">
        {b.status === 'open' ? held : 0}</td>
      <td className="px-4 py-3">
        <span className={`rounded px-2 py-0.5 text-xs font-medium ${
          STATUS_TONE[b.status] ?? 'bg-slate-100 text-slate-600'}`}>
          {b.status}
        </span>
      </td>
    </tr>
  )
}

type DraftLine = { roomTypeId: string; rooms: string; rate: string }

function CreateBlock({ propertyId, onClose, onSaved }: {
  propertyId: string; onClose: () => void; onSaved: () => void
}) {
  const [name, setName] = useState('')
  const [arrival, setArrival] = useState('')
  const [departure, setDeparture] = useState('')
  const [cutOff, setCutOff] = useState('')
  const [commitment, setCommitment] = useState<'tentative' | 'definite'>('tentative')
  const [accountId, setAccountId] = useState('')
  const [notes, setNotes] = useState('')
  const [lines, setLines] = useState<DraftLine[]>([
    { roomTypeId: '', rooms: '1', rate: '' }])
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  const types = useQuery({
    queryKey: ['room-types', propertyId],
    queryFn: () => listRoomTypes(propertyId), enabled: propertyId !== '',
  })
  const orgId = useOrgId()
  const accounts = useQuery({
    queryKey: ['accounts-for-block', orgId],
    queryFn: () => listCommercialAccounts(orgId, { status: 'active' }),
    enabled: orgId !== '',
  })

  const filled = lines.filter((l) => l.roomTypeId && Number(l.rooms) > 0)
  const duplicate = new Set(filled.map((l) => l.roomTypeId)).size !== filled.length
  const cutOffLate = !!cutOff && !!arrival && cutOff > arrival
  const valid = name.trim() !== '' && arrival !== '' && departure > arrival
    && filled.length > 0 && !duplicate && !cutOffLate

  async function save() {
    setErr(''); setBusy(true)
    try {
      await createGroupBlock(propertyId, {
        name: name.trim(), arrival_date: arrival, departure_date: departure,
        cut_off_date: cutOff || null, commitment,
        commercial_account_id: accountId || null,
        notes: notes.trim() || null,
        lines: filled.map((l) => ({
          room_type_id: l.roomTypeId, rooms_blocked: Number(l.rooms),
          nightly_rate: l.rate === '' ? null : Number(l.rate),
        })),
      })
      onSaved()
    } catch (e) {
      setErr(errorText(e, 'The block could not be created.'))
    } finally { setBusy(false) }
  }

  return (
    <Modal title="New group block" onClose={onClose} wide>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div className="sm:col-span-2">
          <label className={lbl}>Group name</label>
          <input className={field} value={name} placeholder="e.g. Sharma Wedding"
            onChange={(e) => setName(e.target.value)} />
        </div>
        <div>
          <label className={lbl}>Arrival</label>
          <input type="date" className={field} value={arrival}
            onChange={(e) => setArrival(e.target.value)} />
        </div>
        <div>
          <label className={lbl}>Departure</label>
          <input type="date" className={field} value={departure}
            onChange={(e) => setDeparture(e.target.value)} />
        </div>
        <div>
          <label className={lbl}>Cut-off date</label>
          <input type="date" className={field} value={cutOff}
            onChange={(e) => setCutOff(e.target.value)} />
          <span className="mt-1 block text-xs text-slate-400">
            Rooms nobody has taken go back on sale that morning. Leave empty to
            hold them until you release the block yourself.
          </span>
          {cutOffLate && (
            <span className="mt-1 block text-xs text-red-600">
              A cut-off after arrival would release rooms the group is already
              sleeping in.
            </span>
          )}
        </div>
        <div>
          <label className={lbl}>Commitment</label>
          <Select className={field} value={commitment}
            onChange={(e) => setCommitment(e.target.value as 'tentative' | 'definite')}>
            <option value="tentative">Tentative</option>
            <option value="definite">Definite</option>
          </Select>
          {/* This said "Both hold the rooms. The difference is what a
              forecast should believe." That was true of the old behaviour
              and is now exactly backwards: tentative holds nothing, which
              is the whole point of the distinction. A label that describes
              the opposite of what the button does is worse than no label. */}
          <span className="mt-1 block text-xs text-slate-400">
            {commitment === 'definite'
              ? 'The rooms come off sale now, and can be booked against.'
              : 'Nothing is held yet and nothing can be booked against it. '
                + 'Mark the block definite when the group confirms.'}
          </span>
        </div>
        <div className="sm:col-span-2">
          <label className={lbl}>Company or agent (optional)</label>
          <Select className={field} value={accountId}
            onChange={(e) => setAccountId(e.target.value)}>
            <option value="">Not billed to an account</option>
            {(accounts.data?.rows ?? []).map((a) => (
              <option key={a.id} value={a.id}>{a.code} — {a.name}</option>
            ))}
          </Select>
        </div>
      </div>

      <div className="mt-5">
        <div className="mb-2 flex items-center justify-between">
          <span className="text-sm font-semibold text-slate-700">Rooms</span>
          <button
            onClick={() => setLines((l) => [...l,
              { roomTypeId: '', rooms: '1', rate: '' }])}
            className="flex items-center gap-1 rounded-lg border border-slate-200 px-2.5 py-1.5 text-xs font-semibold text-slate-600 hover:bg-slate-50">
            <Plus size={13} /> Add room type
          </button>
        </div>
        <div className="space-y-2">
          {lines.map((l, i) => (
            <div key={i} className="flex flex-wrap items-end gap-2">
              <div className="min-w-[10rem] flex-1">
                <label className={lbl}>Room type</label>
                <Select className={field} value={l.roomTypeId}
                  onChange={(e) => setLines((ls) => ls.map((x, j) =>
                    j === i ? { ...x, roomTypeId: e.target.value } : x))}>
                  <option value="">Choose…</option>
                  {(types.data ?? []).map((t) => (
                    <option key={t.id} value={t.id}>{t.name}</option>
                  ))}
                </Select>
              </div>
              <div className="w-24">
                <label className={lbl}>Rooms</label>
                <input type="number" min={1} className={field} value={l.rooms}
                  onChange={(e) => setLines((ls) => ls.map((x, j) =>
                    j === i ? { ...x, rooms: e.target.value } : x))} />
              </div>
              <div className="w-36">
                <label className={lbl}>Group rate (₹)</label>
                <input type="number" min={0} className={field} value={l.rate}
                  placeholder="Room's own rate"
                  onChange={(e) => setLines((ls) => ls.map((x, j) =>
                    j === i ? { ...x, rate: e.target.value } : x))} />
              </div>
              {lines.length > 1 && (
                <button onClick={() => setLines((ls) => ls.filter((_, j) => j !== i))}
                  className="mb-1 rounded-lg border border-slate-200 p-2 text-slate-400 hover:bg-slate-50 hover:text-red-600">
                  <Trash2 size={14} />
                </button>
              )}
            </div>
          ))}
        </div>
        {duplicate && (
          <p className="mt-2 text-xs text-red-600">
            One line per room type — two lines of the same type are two answers
            to how many rooms were agreed.
          </p>
        )}
      </div>

      <div className="mt-4">
        <label className={lbl}>Notes</label>
        <input className={field} value={notes}
          placeholder="What was agreed, and with whom"
          onChange={(e) => setNotes(e.target.value)} />
      </div>

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
        <button onClick={() => void save()} disabled={!valid || busy}
          className="flex items-center gap-2 rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white enabled:hover:bg-brand/90 disabled:opacity-50">
          {busy && <Loader2 size={15} className="animate-spin" />}
          Hold these rooms
        </button>
      </div>
    </Modal>
  )
}

function BlockDetail({ blockId, propertyId, onClose, onChanged }: {
  blockId: string; propertyId: string; onClose: () => void; onChanged: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [confirming, setConfirming] = useState<'released' | 'cancelled' | null>(null)
  const [tab, setTab] = useState<'rooms' | 'list'>('rooms')
  const q = useQuery({
    queryKey: ['group-block', blockId],
    queryFn: () => getGroupBlock(blockId, propertyId),
  })
  const b: GroupBlock | undefined = q.data

  async function commit(next: 'tentative' | 'definite') {
    setErr(''); setBusy(true)
    try {
      await setBlockCommitment(blockId, propertyId, next)
      await q.refetch()
      onChanged()
    } catch (e) {
      // Going definite can legitimately fail: the nights may have sold
      // while the block was only provisional, and that refusal is the
      // honest answer rather than an overbooking.
      setErr(errorText(e, 'The commitment could not be changed.'))
    } finally { setBusy(false) }
  }

  async function end(status: 'released' | 'cancelled') {
    setErr(''); setBusy(true)
    try {
      await releaseGroupBlock(blockId, propertyId, status)
      setConfirming(null)
      await q.refetch()
      onChanged()
    } catch (e) {
      setErr(errorText(e, 'The block could not be released.'))
    } finally { setBusy(false) }
  }

  return (
    <Modal title={b ? `${b.code} — ${b.name}` : 'Block'} onClose={onClose} wide>
      {!b ? (
        <p className="py-10 text-center text-sm text-slate-400">
          <Loader2 className="mx-auto mb-2 h-5 w-5 animate-spin" /> Loading…
        </p>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
            <Fact label="Dates" value={`${b.arrival_date} → ${b.departure_date}`} />
            <Fact label="Cut-off" value={b.cut_off_date ?? 'None agreed'} />
            {/* Not a label any more: this is the switch that decides
                whether the rooms are actually off sale, so it is the thing
                you press. It read as a note for months while quietly
                meaning nothing at all. */}
            <div className="rounded-xl border border-slate-200 bg-white p-3">
              <p className={lbl}>Commitment</p>
              {b.status === 'open' ? (
                <Select className={`${field} mt-0`} value={b.commitment}
                  disabled={busy}
                  onChange={(e) => void commit(
                    e.target.value as 'tentative' | 'definite')}>
                  <option value="tentative">Tentative — no rooms held</option>
                  <option value="definite">Definite — rooms off sale</option>
                </Select>
              ) : (
                <p className="text-sm font-semibold text-ink">
                  {b.commitment === 'definite' ? 'Definite' : 'Tentative'}
                </p>
              )}
            </div>
            <Fact label="Status" value={b.status} />
          </div>
          {b.account_name && (
            <p className="mt-3 text-sm text-slate-600">
              Billed to <strong>{b.account_name}</strong>
            </p>
          )}
          {b.notes && <p className="mt-2 text-sm text-slate-500">{b.notes}</p>}

          <div className="mt-5 flex gap-2 border-b border-slate-100">
            {([['rooms', 'Rooms held'], ['list', 'Rooming list']] as const)
              .map(([k, label]) => (
                <button key={k} onClick={() => setTab(k)}
                  className={`-mb-px border-b-2 px-3 py-2 text-sm font-semibold ${
                    tab === k
                      ? 'border-brand text-brand'
                      : 'border-transparent text-slate-500 hover:text-slate-700'}`}>
                  {label}
                </button>
              ))}
          </div>

          {tab === 'list' ? (
            <RoomingPanel blockId={blockId} propertyId={propertyId}
              block={b} onChanged={onChanged} />
          ) : (
          <table className="mt-5 w-full text-sm">
            <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <Th>Room type</Th><Th right>Blocked</Th><Th right>Picked up</Th>
                <Th right>Still held</Th><Th right>Group rate</Th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {b.lines.map((l) => (
                <tr key={l.room_type_id}>
                  <td className="px-4 py-3 font-medium text-slate-700">
                    {l.room_type}</td>
                  <td className="px-4 py-3 text-right tabular-nums">
                    {l.rooms_blocked}</td>
                  <td className="px-4 py-3 text-right tabular-nums text-emerald-700">
                    {l.rooms_picked_up}</td>
                  <td className="px-4 py-3 text-right tabular-nums font-semibold text-amber-700">
                    {l.rooms_still_held}</td>
                  <td className="px-4 py-3 text-right tabular-nums text-slate-600">
                    {money(l.nightly_rate)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          )}

          {/* Picked up can exceed blocked: a group that took more rooms than it
              agreed bought the extras from general availability. Saying so
              beats leaving somebody to wonder why the numbers do not tally. */}
          {b.rooms_picked_up > b.rooms_blocked && (
            <p className="mt-3 rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-600">
              This group has taken {b.rooms_picked_up - b.rooms_blocked} room
              {b.rooms_picked_up - b.rooms_blocked === 1 ? '' : 's'} beyond the
              block — those came from ordinary availability, not from the rooms
              held here.
            </p>
          )}

          {err && (
            <p className="mt-3 flex items-start gap-2 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">
              <AlertTriangle size={14} className="mt-0.5 shrink-0" />{err}
            </p>
          )}

          {b.status === 'open' ? (
            confirming ? (
              <div className="mt-5 rounded-lg bg-amber-50 px-4 py-3">
                <p className="text-sm font-semibold text-slate-800">
                  {confirming === 'cancelled'
                    ? 'Cancel this block?' : 'Release the unsold rooms?'}
                </p>
                <p className="mt-1 text-xs text-slate-600">
                  {b.rooms_still_held} room{b.rooms_still_held === 1 ? '' : 's'} go
                  back on sale immediately. The {b.rooms_picked_up} booking
                  {b.rooms_picked_up === 1 ? '' : 's'} already made from this
                  block are not affected.
                </p>
                <div className="mt-3 flex gap-2">
                  <button onClick={() => setConfirming(null)}
                    className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-semibold text-slate-600">
                    Keep holding
                  </button>
                  <button onClick={() => void end(confirming)} disabled={busy}
                    className="flex items-center gap-1.5 rounded-lg bg-brand px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-50">
                    {busy && <Loader2 size={12} className="animate-spin" />}
                    Yes, {confirming === 'cancelled' ? 'cancel it' : 'release them'}
                  </button>
                </div>
              </div>
            ) : (
              <div className="mt-5 flex flex-wrap justify-end gap-2">
                <button onClick={() => setConfirming('cancelled')}
                  className="rounded-lg border border-slate-200 px-4 py-2 text-sm font-semibold text-slate-600 hover:bg-slate-50">
                  Group fell through
                </button>
                <button onClick={() => setConfirming('released')}
                  className="rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white hover:bg-brand/90">
                  Release unsold rooms
                </button>
              </div>
            )
          ) : (
            <p className="mt-5 rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-500">
              This block is {b.status}. Its unsold rooms are back on sale; the
              bookings taken from it stand.{' '}
              <Link to="/reservations" className="font-semibold text-brand">
                See the bookings
              </Link>
            </p>
          )}
        </>
      )}
    </Modal>
  )
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-slate-50 px-3 py-2">
      <p className="text-xs text-slate-400">{label}</p>
      <p className="mt-0.5 font-medium text-slate-700">{value}</p>
    </div>
  )
}

function Modal({ title, children, onClose, wide }: {
  title: string; children: React.ReactNode; onClose: () => void; wide?: boolean
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-slate-900/30 p-4"
      onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()}
        className={`mt-10 w-full ${wide ? 'max-w-3xl' : 'max-w-lg'} rounded-2xl bg-white p-6 shadow-xl`}>
        <div className="mb-4 flex items-start justify-between gap-3">
          <h2 className="text-lg font-semibold text-ink">{title}</h2>
          <button onClick={onClose}
            className="rounded-lg p-1 text-slate-400 hover:bg-slate-100">
            <X size={18} />
          </button>
        </div>
        {children}
      </div>
    </div>
  )
}
