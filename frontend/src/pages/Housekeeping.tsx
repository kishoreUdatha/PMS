import { useState } from 'react'
import Select from '../components/Select'
import { StayLayoutToggle, useListLayout } from '../lib/listLayout'
import { fmtDate } from '../lib/dates'
import { TABLE_ROW, TABLE_HEAD, TABLE_SHELL, FILTER_SELECT } from '../lib/controls'
import { downloadCsv, datedName } from '../lib/csv'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle, BedDouble, CheckCircle2, ClipboardCheck, Clock, Columns3, Download, Eye, Loader2, LogOut, Play, Plus, RefreshCw, Shuffle, Sparkles, UserRound, X,
} from 'lucide-react'
import { Broom } from '../components/icons'
import { useFlash } from '../hooks/useFlash'
import {
  getHkBoard, getHkActivity, createHkTask, assignHkTask, advanceHkTask,
  cancelHkTask, autoAssignHk, 
  type HkBoard, type HkCard, type HkAttendant,
} from '../api'
import { useActivePropertyId } from '../hooks/useProperty'
import { errorText } from '../lib/forms'

/**
 * Screen 006 — Housekeeping Operations, Room Board tab.
 *
 * Five lanes, one state machine. Assigned, In Progress, Inspection and Ready
 * are literal task states; Dirty is the rooms the system knows are dirty with
 * nothing open against them — the pile nobody has picked up, which is the
 * reason to open this screen in the morning.
 *
 * Every card is a real room, and moving one moves the room's condition with it,
 * so the rack and the dashboard see the same answer this board does. Attendants
 * are the people who actually hold the housekeeping edit permission here; there
 * is no staff table yet (SCR-021), and inventing one would put names on this
 * screen that mean nothing anywhere else in the product.
 */

const LANE_STYLE: Record<string, { head: string; chip: string; ring: string }> = {
  dirty: { head: 'bg-rose-50 text-rose-700', chip: 'bg-rose-100 text-rose-700', ring: 'border-rose-100' },
  assigned: { head: 'bg-amber-50 text-amber-800', chip: 'bg-amber-100 text-amber-800', ring: 'border-amber-100' },
  in_progress: { head: 'bg-sky-50 text-sky-700', chip: 'bg-sky-100 text-sky-700', ring: 'border-sky-100' },
  inspection: { head: 'bg-violet-50 text-violet-700', chip: 'bg-violet-100 text-violet-700', ring: 'border-violet-100' },
  ready: { head: 'bg-emerald-50 text-emerald-700', chip: 'bg-emerald-100 text-emerald-700', ring: 'border-emerald-100' },
}
const PRIORITY_STYLE: Record<string, string> = {
  urgent: 'bg-red-100 text-red-700',
  high: 'bg-orange-100 text-orange-700',
  normal: 'bg-slate-75 text-slate-600',
  low: 'bg-slate-75 text-slate-400',
}
const PRIORITY_LABEL: Record<string, string> = {
  urgent: 'Urgent', high: 'High', normal: 'Normal', low: 'Low',
}
const KIND_LABEL: Record<string, string> = {
  departure: 'Departure clean', stayover: 'Stayover', turndown: 'Turndown',
  deep_clean: 'Deep clean', inspection_only: 'Inspection only',
}
const TONE: Record<string, string> = {
  rose: 'bg-rose-400', amber: 'bg-amber-400', sky: 'bg-sky-400',
  emerald: 'bg-emerald-400', slate: 'bg-slate-300',
}

// The other five tabs of screen 006 are separate screens that have not been
// built. They are shown so the shape of the module is honest, and disabled so
// nobody follows one into nothing.
const TABS = [
  { key: 'board', label: 'Room Board', built: true },
  { key: 'tasks', label: 'Tasks', built: false, why: 'SCR-071 / 072 — not built yet.' },
  { key: 'linen', label: 'Linen', built: false, why: 'SCR-075 — not built yet.' },
  { key: 'minibar', label: 'Minibar', built: false, why: 'SCR-076 — not built yet.' },
  { key: 'lost', label: 'Lost & Found', built: false, why: 'SCR-074 — not built yet.' },
  { key: 'inspections', label: 'Inspections', built: false, why: 'Part of SCR-072 — not built yet.' },
]

const select = `${FILTER_SELECT} bg-white outline-none focus:border-brand`


const ago = (iso: string) => {
  const mins = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000))
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins} minute${mins === 1 ? '' : 's'} ago`
  const h = Math.round(mins / 60)
  if (h < 24) return `${h} hour${h === 1 ? '' : 's'} ago`
  const d = Math.round(h / 24)
  return `${d} day${d === 1 ? '' : 's'} ago`
}
const clock = (m: number) =>
  `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`

/* --------------------------------------------------------------- KPI card --- */
function Kpi({ icon, label, value, tint, onClick, active }: {
  icon: React.ReactNode; label: string; value: number; tint: string
  onClick: () => void; active: boolean
}) {
  return (
    // One line rather than three. The number was set at 3xl over a stacked
    // label and a "View all →" caption, which made five of these 130px tall
    // -- a third of the screen, above a board whose own lane headers already
    // carry four of the same five counts. The caption went with it: it read
    // as a link to somewhere else when what it does is filter this board, and
    // the border already says which one is on.
    <button onClick={onClick}
      title={active ? `Showing ${label} only — click to clear` : `Show only ${label}`}
      aria-pressed={active}
      className={`flex flex-1 items-center gap-2.5 rounded-xl border px-3 py-2 text-left transition ${
        active ? 'border-brand bg-brand-light ring-1 ring-brand' : 'border-slate-200 bg-white hover:border-slate-300'}`}>
      <span className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full ${tint}`}>
        {icon}
      </span>
      <span className="min-w-0 truncate text-sm text-slate-500">{label}</span>
      <span className="ml-auto text-lg font-semibold text-slate-800">{value}</span>
    </button>
  )
}

/* ------------------------------------------------------------- room card --- */

/** Every room on one line, whatever lane it is in.
 *
 * The board is the right default — housekeeping is work moving left to right,
 * and lanes show that. What lanes cannot answer is a question across them:
 * which rooms has one attendant got, what has been cleaning longest, which
 * floor is behind. Five columns make you scan five times; a sortable line
 * answers it once. Same cards, same actions, read the other way.
 */
function BoardList({ board, onAct, busy }: {
  board: HkBoard
  busy: string | null
  onAct: (what: string, card: HkCard, who?: string) => void
}) {
  const rows = board.lanes.flatMap((l) =>
    l.cards.map((c) => ({ ...c, laneLabel: l.label, laneKey: l.key })))

  if (rows.length === 0) {
    return (
      <div className="rounded-2xl border border-slate-100 bg-white px-4 py-16 text-center text-sm text-slate-500">
        No room matches these filters.
      </div>
    )
  }

  // Matched to `StayTable`, which is what Reservations, Arrivals, In-house
  // and Departures all draw. This table had a softer radius and a lighter
  // border than every other list in the product, so moving between
  // Housekeeping and Reservations looked like moving between two
  // applications -- close enough to feel wrong without being obviously
  // different. One table shape, everywhere.
  return (
    <div className={TABLE_SHELL}>
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead className={TABLE_HEAD}>
            <tr>
              <th className="whitespace-nowrap px-4 py-3 font-semibold">Room</th>
              <th className="whitespace-nowrap px-4 py-3 font-semibold">Status</th>
              <th className="whitespace-nowrap px-4 py-3 font-semibold">Priority</th>
              <th className="whitespace-nowrap px-4 py-3 font-semibold">Attendant</th>
              <th className="whitespace-nowrap px-4 py-3 font-semibold">Elapsed</th>
              <th className="whitespace-nowrap px-4 py-3 font-semibold">Next arrival</th>
              <th className="whitespace-nowrap px-4 py-3 text-right font-semibold">Actions</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((c) => {
              const urgent = c.arriving_in_minutes !== null
                && c.arriving_in_minutes <= 90
              return (
                <tr key={c.room_id} className={`${TABLE_ROW} text-slate-700`}>
                  <td className="px-4 py-3">
                    <span className="block font-semibold text-slate-800">{c.room_code}</span>
                    <span className="block text-xs text-slate-400">
                      {[c.room_type, c.floor].filter(Boolean).join(' · ')}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <span className={`whitespace-nowrap rounded-full px-2.5 py-1 text-xs font-semibold ${
                      LANE_STYLE[c.laneKey]?.chip ?? 'bg-slate-75 text-slate-600'}`}>
                      {c.laneLabel}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <span className={`whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-semibold ${
                      PRIORITY_STYLE[c.priority] ?? PRIORITY_STYLE.normal}`}>
                      {PRIORITY_LABEL[c.priority] ?? c.priority}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    {c.attendant ?? <span className="text-slate-300">Unassigned</span>}
                  </td>
                  <td className="whitespace-nowrap px-4 py-3 tabular-nums">
                    {c.elapsed_minutes !== null
                      ? `${c.elapsed_minutes} min`
                      : <span className="text-slate-300">—</span>}
                  </td>
                  <td className="whitespace-nowrap px-4 py-3">
                    {c.arriving_in_minutes === null
                      ? <span className="text-slate-300">—</span>
                      : (
                        // The one number that reorders somebody's morning, so
                        // it is the only red on the row.
                        <span className={urgent ? 'font-semibold text-red-600' : 'text-slate-600'}>
                          in {c.arriving_in_minutes} min
                        </span>
                      )}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex justify-end">
                      {/* The same card, so the row and the board cannot offer
                          different actions on one room. */}
                      <Card card={c} board={board} onAct={onAct} busy={busy} compact />
                    </div>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function Card({ card, board, onAct, busy, compact }: {
  card: HkCard; board: HkBoard; busy: string | null
  onAct: (what: string, card: HkCard, who?: string) => void
  /** Controls only, for a table row. The same component either way, so the
   *  board and the list can never offer different actions on one room. */
  compact?: boolean
}) {
  const [menu, setMenu] = useState(false)
  const working = busy === (card.task_id ?? card.room_id)
  const urgent = card.arriving_in_minutes !== null && card.arriving_in_minutes <= 90

  // Which single action moves this card forward. Anything else is on the menu.
  const step =
    card.lane === 'dirty' ? { key: 'create', label: 'Assign', icon: <UserRound size={13} /> }
      : card.lane === 'assigned' ? { key: 'start', label: 'Start', icon: <Play size={13} /> }
        : card.lane === 'in_progress' ? { key: 'finish', label: 'Finished', icon: <CheckCircle2 size={13} /> }
          : card.lane === 'inspection' ? { key: 'pass', label: 'Pass', icon: <ClipboardCheck size={13} /> }
            : null
  const allowed = step?.key === 'create' ? board.can_create
    : step?.key === 'pass' ? board.can_approve : board.can_edit

  // The action strip. Lifted out so the compact form can return just
  // this: the board and the list must never offer different actions on
  // the same room.
  const controls = (
      <span className="flex shrink-0 items-center gap-1">
        {step && (
          <button
            disabled={!allowed || working}
            title={allowed ? undefined
              : step.key === 'pass'
                ? 'Signing off an inspection needs the housekeeping approve permission.'
                : 'You do not have permission to change housekeeping work.'}
            onClick={() => onAct(step.key, card)}
            className="flex items-center gap-1 rounded-lg bg-brand px-2 py-1 text-xs font-semibold text-white hover:bg-brand-dark disabled:opacity-40">
            {working ? <Loader2 size={12} className="animate-spin" /> : step.icon}
            {step.label}
          </button>
        )}
        {card.task_id && card.lane !== 'ready' && board.can_edit && (
          <span className="relative">
            <button onClick={() => setMenu(!menu)}
              className="rounded px-1 text-slate-400 hover:text-slate-600">⋮</button>
            {menu && (
              <span className="absolute right-0 top-6 z-20 block w-52 rounded-xl border border-slate-100 bg-white p-1 shadow-lg">
                {board.filters.attendants.map((a) => (
                  <button key={a.user_id}
                    onClick={() => { setMenu(false); onAct('reassign', card, a.user_id) }}
                    className="block w-full truncate rounded-lg px-3 py-1.5 text-left text-xs text-slate-600 hover:bg-slate-50">
                    Reassign to {a.name}
                  </button>
                ))}
                {card.lane === 'inspection' && (
                  <button onClick={() => { setMenu(false); onAct('fail', card) }}
                    disabled={!board.can_approve}
                    className="block w-full rounded-lg px-3 py-1.5 text-left text-xs text-amber-700 hover:bg-amber-50 disabled:opacity-40">
                    Send back to be redone
                  </button>
                )}
                <button onClick={() => { setMenu(false); onAct('cancel', card) }}
                  className="block w-full rounded-lg px-3 py-1.5 text-left text-xs text-red-600 hover:bg-red-50">
                  Cancel this task
                </button>
              </span>
            )}
          </span>
        )}
      </span>
  )

  if (compact) return controls

  return (
    <div className={`rounded-xl border bg-white p-3 shadow-sm ${
      urgent ? 'border-red-200' : 'border-slate-100'}`}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-base font-semibold text-slate-800">{card.room_code}</p>
          <p className="truncate text-xs text-slate-500">{card.room_type}</p>
        </div>
        <span className={`shrink-0 rounded-full px-2 py-0.5 text-xs font-semibold ${
          PRIORITY_STYLE[card.priority] ?? PRIORITY_STYLE.normal}`}>
          {PRIORITY_LABEL[card.priority] ?? card.priority}
        </span>
      </div>

      <div className="mt-2 space-y-1 text-xs text-slate-500">
        {card.departure_note && (
          <p className="flex items-center gap-1.5">
            <LogOut size={12} className="shrink-0" />{card.departure_note}
          </p>
        )}
        {card.arrival_note && (
          <p className={`flex items-center gap-1.5 ${urgent ? 'font-semibold text-red-600' : ''}`}>
            {urgent ? <AlertTriangle size={12} className="shrink-0" />
              : <BedDouble size={12} className="shrink-0" />}
            {urgent && card.arriving_in_minutes !== null
              ? `Guest arriving in ${card.arriving_in_minutes} min`
              : card.arrival_note}
          </p>
        )}
        {card.elapsed_minutes !== null && (
          <p className="flex items-center gap-1.5 font-medium text-sky-700">
            <Clock size={12} className="shrink-0" />
            Cleaning for {clock(card.elapsed_minutes)}
          </p>
        )}
        {card.kind && card.lane !== 'dirty' && (
          <p className="flex items-center gap-1.5">
            <Sparkles size={12} className="shrink-0" />
            {KIND_LABEL[card.kind] ?? card.kind}
            {card.rejected_count > 0 && (
              <span className="rounded bg-red-50 px-1 font-medium text-red-600">
                redo ×{card.rejected_count}
              </span>
            )}
          </p>
        )}
      </div>

      <div className="mt-3 flex items-center justify-between gap-2 border-t border-slate-50 pt-2">
        <span className="flex min-w-0 items-center gap-1.5 text-xs text-slate-600">
          {card.attendant && card.lane !== 'dirty' ? (
            <>
              <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-brand-light text-xs font-semibold text-brand">
                {card.attendant.slice(0, 1)}
              </span>
              <span className="truncate">{card.attendant}</span>
            </>
          ) : card.lane === 'dirty' && card.attendant ? (
            <span className="truncate text-slate-400">
              Last cleaned by {card.attendant}
            </span>
          ) : <span className="text-slate-400">Unassigned</span>}
        </span>
        {controls}
      </div>
    </div>
  )
}

/* ------------------------------------------------------------- workload --- */
function Workload({ people }: { people: HkAttendant[] }) {
  return (
    <div className="rounded-2xl border border-slate-100 bg-white p-4">
      <div className="flex items-center justify-between">
        <h2 className="font-semibold text-ink">Team Workload</h2>
        <span className="flex items-center gap-1.5 text-xs text-slate-500">
          <span className="h-2 w-2 rounded-full bg-emerald-500" />
          On duty: {people.filter((p) => p.total_today > 0).length}
        </span>
      </div>
      {people.length === 0 ? (
        <p className="mt-3 text-xs text-slate-500">
          Nobody in this property holds the housekeeping edit permission, so
          there is no one to assign rooms to. Grant the Housekeeping role in
          User Management first.
        </p>
      ) : (
        <ul className="mt-3 space-y-3">
          {people.map((p) => (
            <li key={p.user_id} className="flex items-center gap-3">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-brand-light text-sm font-semibold text-brand">
                {p.name.slice(0, 1)}
              </span>
              <span className="min-w-0 flex-1">
                <span className="flex items-baseline justify-between gap-2">
                  <span className="truncate text-sm font-medium text-slate-700">{p.name}</span>
                  <span className="shrink-0 text-xs text-slate-500">
                    {p.open_tasks} open
                  </span>
                </span>
                <span className="mt-1 block h-1.5 w-full rounded-full bg-slate-100">
                  <span className="block h-1.5 rounded-full bg-brand"
                    style={{ width: `${p.percent_done}%` }} />
                </span>
                <span className="mt-0.5 block text-xs text-slate-400">
                  {p.total_today === 0 ? 'No rooms today'
                    : `${p.done_today} of ${p.total_today} done · ${p.percent_done}%`}
                </span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

/* ------------------------------------------------------------ the screen --- */
export default function Housekeeping() {
  const propertyId = useActivePropertyId()
  const qc = useQueryClient()
  const [floor, setFloor] = useState('')
  const [roomType, setRoomType] = useState('')
  const [lane, setLane] = useState('')
  const [attendant, setAttendant] = useState('')
  const [error, setError] = useState('')
  const [toast, setToast] = useFlash()
  const [busy, setBusy] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [layout, chooseLayout] = useListLayout('housekeeping', 'cards')

  const filters = {
    floor: floor || undefined,
    room_type_id: roomType || undefined,
    lane: lane || undefined,
    attendant_id: attendant || undefined,
  }
  const q = useQuery({
    queryKey: ['hk-board', propertyId, filters],
    queryFn: () => getHkBoard(propertyId, filters),
    enabled: propertyId !== '',
  })
  const act = useQuery({
    queryKey: ['hk-activity', propertyId],
    queryFn: () => getHkActivity(propertyId),
    enabled: propertyId !== '',
  })

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['hk-board'] })
    qc.invalidateQueries({ queryKey: ['hk-activity'] })
  }
  const fail = (e: unknown) => {
    setError(errorText(e, 'That did not work. Please try again.'))
    setToast('')
  }
  const done = (msg: string) => { setToast(msg); setError(''); refresh() }

  const board = q.data

  async function onAct(what: string, card: HkCard, who?: string) {
    if (!board) return
    setBusy(card.task_id ?? card.room_id)
    setError(''); setToast('')
    try {
      if (what === 'create') {
        // A dirty room with no task: raise one and hand it to whoever holds
        // the least work, which is the same rule Auto Assign uses.
        const people = [...board.filters.attendants]
          .sort((a, b) => a.open_tasks - b.open_tasks)
        if (people.length === 0) {
          setError('Nobody holds the housekeeping edit permission in this '
            + 'property, so there is no one to assign this room to.')
          return
        }
        const r = await createHkTask(propertyId, {
          room_id: card.room_id, kind: 'departure',
          priority: card.priority, assigned_to: people[0].user_id,
        })
        done(`Room ${r.room_code} assigned to ${r.attendant}.`)
      } else if (what === 'reassign' && card.task_id) {
        const r = await assignHkTask(card.task_id, propertyId, { assigned_to: who })
        done(`Room ${card.room_code} reassigned to ${r.attendant}.`)
      } else if (what === 'cancel' && card.task_id) {
        await cancelHkTask(card.task_id, propertyId)
        done(`Task on room ${card.room_code} cancelled.`)
      } else if (card.task_id) {
        const r = await advanceHkTask(card.task_id, propertyId, what)
        const said: Record<string, string> = {
          start: `Room ${card.room_code} is being cleaned.`,
          finish: `Room ${card.room_code} is cleaned and waiting for inspection.`,
          pass: `Room ${card.room_code} passed inspection and is ready to sell.`,
          fail: `Room ${card.room_code} sent back to be redone.`,
        }
        done(said[what] ?? `Room ${card.room_code} is now ${r.state}.`)
      }
    } catch (e) { fail(e) } finally { setBusy(null) }
  }

  const auto = useMutation({
    mutationFn: () => autoAssignHk(propertyId),
    onSuccess: (r) => done(r.detail),
    onError: fail,
  })

  const create = useMutation({
    mutationFn: (body: Record<string, unknown>) => createHkTask(propertyId, body),
    onSuccess: (r) => {
      setCreating(false)
      done(`Task raised on room ${r.room_code}${r.attendant ? ` for ${r.attendant}` : ''}.`)
    },
    onError: fail,
  })

  if (propertyId === '') {
    return <p className="text-sm text-slate-500">Pick a property first.</p>
  }
  if (q.isLoading) {
    return (
      <p className="flex items-center gap-2 text-sm text-slate-500">
        <Loader2 className="h-4 w-4 animate-spin" /> Loading the board…
      </p>
    )
  }
  if (q.isError || !board) {
    const er = q.error as { response?: { data?: { detail?: string }; status?: number } }
    return (
      <p className="flex items-start gap-2 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
        {errorText(er, 'The housekeeping board could not be loaded.')}
      </p>
    )
  }

  const k = board.kpis

  return (
    <div className="space-y-4">
      {/* ------------------------------------------------------- header --- */}
      {/* One line, as on Reservations: title on the left, the screen's
          actions on the right. The business date rides beside the title in
          small type -- a board is always *for* a date, but it is not worth a
          line of rooms to say so. */}
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <div className="flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-1">
          <h1 className="flex items-center gap-2 text-display text-ink">
            <Broom size={26} className="text-brand" /> Housekeeping
          </h1>
          <span className="text-sm text-slate-500">
            {fmtDate(board.business_date)} · checkout {board.checkout_time}
            {' · '}check-in {board.checkin_time}
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {/* The attendants' run sheet — every room in every lane, in the
              order the board shows them, so it can be printed and carried. */}
          {board.can_export && (
            <button onClick={() => exportBoard(board)}
              className="flex items-center gap-1.5 whitespace-nowrap rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-600 hover:bg-slate-50">
              <Download size={15} /> Export
            </button>
          )}
          <button
            disabled={!board.can_edit || auto.isPending || board.unassigned === 0}
            title={!board.can_edit ? 'You do not have permission to assign rooms.'
              : board.unassigned === 0 ? 'Every dirty room already has someone on it.'
                : `Spread ${board.unassigned} unassigned room(s) across the team.`}
            onClick={() => auto.mutate()}
            className="flex items-center gap-1.5 whitespace-nowrap rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-600 hover:bg-slate-50 disabled:opacity-40">
            {auto.isPending ? <Loader2 size={14} className="animate-spin" />
              : <Shuffle size={14} />} Auto Assign
          </button>
          <button disabled={!board.can_create} onClick={() => setCreating(true)}
            title={board.can_create ? undefined
              : 'You do not have permission to raise housekeeping tasks.'}
            className="flex items-center gap-1.5 whitespace-nowrap rounded-lg bg-brand px-3 py-2 text-sm font-medium text-white hover:bg-brand-dark disabled:opacity-40">
            <Plus size={15} /> Create Task
          </button>
        </div>
      </div>

      <div className="flex gap-1 overflow-x-auto border-b border-slate-200">
        {TABS.map((t) => t.built ? (
          <span key={t.key}
            className="whitespace-nowrap border-b-2 border-brand px-4 py-2.5 text-sm font-semibold text-brand">
            {t.label}
          </span>
        ) : (
          <span key={t.key} title={t.why}
            className="cursor-not-allowed whitespace-nowrap px-4 py-2.5 text-sm text-slate-300">
            {t.label}
          </span>
        ))}
      </div>

      {/* --------------------------------------------------------- KPIs --- */}
      <div className="flex flex-wrap gap-3">
        <Kpi icon={<Broom className="h-4 w-4 text-rose-600" />} label="Dirty Rooms"
          value={k.dirty} tint="bg-rose-100" active={lane === 'dirty'}
          onClick={() => setLane(lane === 'dirty' ? '' : 'dirty')} />
        <Kpi icon={<Sparkles className="h-4 w-4 text-amber-600" />} label="Cleaning"
          value={k.cleaning} tint="bg-amber-100" active={lane === 'in_progress'}
          onClick={() => setLane(lane === 'in_progress' ? '' : 'in_progress')} />
        <Kpi icon={<CheckCircle2 className="h-4 w-4 text-emerald-600" />} label="Ready"
          value={k.ready} tint="bg-emerald-100" active={lane === 'ready'}
          onClick={() => setLane(lane === 'ready' ? '' : 'ready')} />
        <Kpi icon={<Eye className="h-4 w-4 text-violet-600" />} label="Inspection Due"
          value={k.inspection_due} tint="bg-violet-100" active={lane === 'inspection'}
          onClick={() => setLane(lane === 'inspection' ? '' : 'inspection')} />
        <Kpi icon={<UserRound className="h-4 w-4 text-sky-600" />} label="Staff On Duty"
          value={k.staff_on_duty} tint="bg-sky-100" active={false}
          onClick={() => setAttendant('')} />
      </div>

      {/* ------------------------------------------------------ filters --- */}
      {/* One row, no captions. Each select already says what it holds
          ("All Floors", "Floor 2"), so a label stacked above it only added
          height. Refresh and the layout toggle sit at the right end, where
          Reservations keeps its search and toggle. */}
      <div className="flex flex-wrap items-center gap-2">
        <Select blankIsChoice value={floor} onChange={(e) => setFloor(e.target.value)}
          aria-label="Floor" className={select}>
          <option value="">All Floors</option>
          {board.filters.floors.map((f) => (
            <option key={f} value={f}>Floor {f}</option>
          ))}
        </Select>
        <Select blankIsChoice value={roomType} onChange={(e) => setRoomType(e.target.value)}
          aria-label="Room type" className={select}>
          <option value="">All Room Types</option>
          {board.filters.room_types.map((t) => (
            <option key={t.id} value={t.id}>{t.name}</option>
          ))}
        </Select>
        <Select blankIsChoice value={lane} onChange={(e) => setLane(e.target.value)}
          aria-label="Status" className={select}>
          <option value="">All Statuses</option>
          {board.lanes.map((l) => (
            <option key={l.key} value={l.key}>{l.label}</option>
          ))}
        </Select>
        <Select blankIsChoice value={attendant} onChange={(e) => setAttendant(e.target.value)}
          aria-label="Attendant" className={select}>
          <option value="">All Attendants</option>
          {board.filters.attendants.map((a) => (
            <option key={a.user_id} value={a.user_id}>{a.name}</option>
          ))}
        </Select>

        <span className="ml-auto flex items-center gap-2">
          <button onClick={refresh} title="Refresh the board"
            className="flex items-center gap-1.5 whitespace-nowrap rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-600 hover:bg-slate-50">
            <RefreshCw size={14} /> Refresh
          </button>
          <StayLayoutToggle layout={layout} onChange={chooseLayout}
            cardsLabel="Board view" cardsIcon={Columns3} />
        </span>
      </div>

      {toast && (
        <p className="flex items-center gap-2 rounded-xl bg-emerald-50 px-4 py-3 text-sm text-emerald-700">
          <CheckCircle2 className="h-4 w-4 shrink-0" />{toast}
        </p>
      )}
      {error && (
        <p className="flex items-start gap-2 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />{error}
        </p>
      )}

      {/* -------------------------------------------------------- board --- */}
      {/* Lanes by default: housekeeping is work moving left to right and the
          board shows that. The list answers the questions that cut across
          lanes -- one attendant's rooms, what has been cleaning longest. */}
      {layout === 'list' ? (
        <BoardList board={board} onAct={onAct} busy={busy} />
      ) : (
      <div className="flex flex-col gap-4 min-[1800px]:flex-row">
        <div className="flex min-w-0 flex-1 gap-3 overflow-x-auto pb-2">
          {board.lanes.map((l) => {
            const s = LANE_STYLE[l.key]
            return (
              <div key={l.key}
                className={`w-[236px] shrink-0 rounded-2xl border ${s.ring} bg-slate-50/60 p-2`}>
                <div className={`mb-2 flex items-center justify-between rounded-xl px-3 py-2 ${s.head}`}>
                  <span className="text-sm font-semibold">{l.label}</span>
                  <span className={`rounded-full px-2 py-0.5 text-xs font-semibold ${s.chip}`}>
                    {l.count}
                  </span>
                </div>
                <div className="space-y-2">
                  {l.cards.map((c) => (
                    <Card key={c.room_id} card={c} board={board}
                      onAct={onAct} busy={busy} />
                  ))}
                  {l.count === 0 && (
                    <p className="px-3 py-6 text-center text-xs text-slate-400">
                      {l.key === 'dirty' ? 'Nothing waiting.' : 'Empty.'}
                    </p>
                  )}
                </div>
              </div>
            )
          })}
        </div>

        {/* ------------------------------------------------------ aside --- */}
        <aside className="grid w-full shrink-0 grid-cols-1 gap-3 md:grid-cols-2 min-[1800px]:w-72 min-[1800px]:grid-cols-1">
          <Workload people={board.filters.attendants} />
          <div className="rounded-2xl border border-slate-100 bg-white p-4">
            <h2 className="font-semibold text-ink">Recent Activity</h2>
            {(act.data ?? []).length === 0 ? (
              <p className="mt-3 text-xs text-slate-400">Nothing yet today.</p>
            ) : (
              <ul className="mt-3 space-y-3">
                {(act.data ?? []).map((a, i) => (
                  <li key={i} className="flex gap-2.5">
                    <span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${
                      TONE[a.tone] ?? TONE.slate}`} />
                    <span className="min-w-0">
                      <span className="block text-xs font-medium text-slate-700">{a.text}</span>
                      <span className="block text-xs text-slate-400">{ago(a.at)}</span>
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </aside>
      </div>
      )}

      {creating && (
        <CreateTask board={board} onClose={() => setCreating(false)}
          onSubmit={(b) => create.mutate(b)} pending={create.isPending} />
      )}
    </div>
  )
}

/* ----------------------------------------------------------- create task --- */
function CreateTask({ board, onClose, onSubmit, pending }: {
  board: HkBoard; onClose: () => void; pending: boolean
  onSubmit: (body: Record<string, unknown>) => void
}) {
  // Every active room is offerable; the API refuses a second open task on a
  // room, so the list does not have to guess which ones are free.
  const rooms = board.lanes.flatMap((l) => l.cards)
    .slice().sort((a, b) => a.room_code.localeCompare(b.room_code))
  const [roomId, setRoomId] = useState('')
  const [kind, setKind] = useState('departure')
  const [priority, setPriority] = useState('normal')
  const [who, setWho] = useState('')
  const [notes, setNotes] = useState('')

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-slate-900/30 p-4">
      <div className="w-full max-w-md rounded-2xl bg-white p-5 shadow-xl">
        <div className="flex items-start justify-between">
          <h2 className="text-lg font-semibold text-ink">Create Task</h2>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600">
            <X size={18} />
          </button>
        </div>
        <div className="mt-4 space-y-3">
          <label className="block text-xs text-slate-500">
            Room *
            <Select value={roomId} onChange={(e) => setRoomId(e.target.value)}
              className={`mt-1 w-full ${select}`}>
              <option value="">Pick a room</option>
              {rooms.map((r) => (
                <option key={r.room_id} value={r.room_id}>
                  {r.room_code} — {r.room_type}
                  {r.lane !== 'dirty' && r.lane !== 'ready' ? ' (already has a task)' : ''}
                </option>
              ))}
            </Select>
          </label>
          <div className="flex gap-3">
            <label className="block flex-1 text-xs text-slate-500">
              Task type
              <Select value={kind} onChange={(e) => setKind(e.target.value)}
                className={`mt-1 w-full ${select}`}>
                {Object.entries(KIND_LABEL).map(([k, v]) => (
                  <option key={k} value={k}>{v}</option>
                ))}
              </Select>
            </label>
            <label className="block flex-1 text-xs text-slate-500">
              Priority
              <Select value={priority} onChange={(e) => setPriority(e.target.value)}
                className={`mt-1 w-full ${select}`}>
                {Object.entries(PRIORITY_LABEL).map(([k, v]) => (
                  <option key={k} value={k}>{v}</option>
                ))}
              </Select>
            </label>
          </div>
          <label className="block text-xs text-slate-500">
            Attendant
            <Select value={who} onChange={(e) => setWho(e.target.value)}
              className={`mt-1 w-full ${select}`}>
              <option value="">Leave unassigned</option>
              {board.filters.attendants.map((a) => (
                <option key={a.user_id} value={a.user_id}>
                  {a.name} — {a.open_tasks} open
                </option>
              ))}
            </Select>
          </label>
          <label className="block text-xs text-slate-500">
            Notes
            <textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={2}
              maxLength={600} placeholder="Anything the attendant should know"
              className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700 outline-none focus:border-brand" />
          </label>
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <button onClick={onClose}
            className="rounded-lg border border-slate-200 px-4 py-2 text-sm text-slate-600 hover:bg-slate-50">
            Cancel
          </button>
          <button disabled={roomId === '' || pending}
            onClick={() => onSubmit({
              room_id: roomId, kind, priority,
              assigned_to: who || null, notes: notes.trim() || null,
            })}
            className="flex items-center gap-1.5 rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white hover:bg-brand-dark disabled:opacity-40">
            {pending ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}
            Create Task
          </button>
        </div>
      </div>
    </div>
  )
}

/** The board as a run sheet: one row per room, in board order. */
function exportBoard(board: HkBoard): void {
  downloadCsv(
    datedName('housekeeping', board.business_date),
    ['Lane', 'Room', 'Room Type', 'Floor', 'Condition', 'Priority', 'Kind',
     'Attendant', 'Started', 'Minutes', 'Finished', 'Rejected', 'Occupied',
     'Notes'],
    board.lanes.flatMap((lane) => lane.cards.map((c) => [
      lane.label, c.room_code, c.room_type, c.floor, c.condition, c.priority,
      c.kind, c.attendant, c.started_at, c.elapsed_minutes, c.finished_at,
      c.rejected_count, c.occupied ? 'yes' : 'no', c.notes,
    ])),
  )
}
