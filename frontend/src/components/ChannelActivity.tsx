import { Fragment, useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Activity, Check, ChevronDown, ChevronRight, Copy, Inbox, KeyRound, Loader2,
  RotateCcw,
} from 'lucide-react'
import {
  getChannelLink, getChannelSyncLog, getMappingEditor, listChannelBookingEvents,
  replayChannelBookingEvent, type ChannelSyncLogRow,
} from '../api'

/**
 * What this property has actually sent to, and received from, the channel
 * manager.
 *
 * Two lists, because they answer the two questions a hotel asks when an OTA
 * looks wrong: "did our change go out?" (every request, with the task id the
 * channel manager returned -- the receipt, and what its support asks for) and
 * "did their booking come in?" (every delivery, with a way to try again one
 * that could not be placed).
 */
export default function ChannelActivity({ propertyId }: { propertyId: string }) {
  const linkQ = useQuery({
    queryKey: ['channel-link', propertyId],
    queryFn: () => getChannelLink(propertyId),
  })
  const linkId = linkQ.data?.id
  const log = useQuery({
    queryKey: ['channel-sync-log', linkId],
    queryFn: () => getChannelSyncLog(linkId!, 50),
    enabled: Boolean(linkId),
    // Changes go out within a sync interval; this is how someone watching
    // (a certification reviewer, a revenue manager) sees them arrive.
    refetchInterval: 10_000,
  })
  const events = useQuery({
    queryKey: ['channel-booking-events', propertyId],
    queryFn: () => listChannelBookingEvents(propertyId, 30),
    refetchInterval: 15_000,
  })

  if (linkQ.isLoading) return null
  if (!linkId) return null

  return (
    <div className="space-y-5">
      <ChannelIds linkId={linkId} />

      <section className="rounded-2xl border border-slate-100 bg-white p-5 shadow-sm">
        <div className="flex items-center gap-2">
          <Activity size={20} className="text-brand" />
          <h2 className="text-lg font-semibold text-ink">Sync log</h2>
          <span className="ml-auto text-xs text-slate-400">
            Every request sent to the channel manager · refreshes every 10s
          </span>
        </div>
        <p className="mt-1 text-sm text-slate-500">
          Only what changed is sent, a few seconds after it changes. The task
          id is the channel manager&rsquo;s receipt for each request.
        </p>
        <div className="mt-4 overflow-x-auto">
          {log.isLoading ? (
            <Loader2 className="mx-auto my-6 animate-spin text-slate-400" />
          ) : (log.data ?? []).length === 0 ? (
            <p className="py-6 text-center text-sm text-slate-400">
              Nothing sent yet.
            </p>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-100 text-left text-xs uppercase tracking-wide text-slate-400">
                  <th className="w-6 py-2" />
                  <th className="py-2 pr-3">When</th>
                  <th className="py-2 pr-3">What</th>
                  <th className="py-2 pr-3">Why</th>
                  <th className="py-2 pr-3">Dates</th>
                  <th className="py-2 pr-3">Result</th>
                  <th className="py-2">Task id</th>
                </tr>
              </thead>
              <tbody>
                {(log.data ?? []).map((r) => <LogRow key={r.id} row={r} />)}
              </tbody>
            </table>
          )}
        </div>
      </section>

      <section className="rounded-2xl border border-slate-100 bg-white p-5 shadow-sm">
        <div className="flex items-center gap-2">
          <Inbox size={20} className="text-brand" />
          <h2 className="text-lg font-semibold text-ink">Booking deliveries</h2>
        </div>
        <p className="mt-1 text-sm text-slate-500">
          New, changed and cancelled OTA bookings as they arrive.
        </p>
        <ul className="mt-3 divide-y divide-slate-100">
          {events.isLoading && (
            <Loader2 className="mx-auto my-6 animate-spin text-slate-400" />
          )}
          {!events.isLoading && (events.data ?? []).length === 0 && (
            <li className="py-6 text-center text-sm text-slate-400">
              No bookings delivered yet.
            </li>
          )}
          {(events.data ?? []).map((e) => (
            <EventRow key={e.revision_id} propertyId={propertyId} e={e} />
          ))}
        </ul>
      </section>
    </div>
  )
}

/**
 * What the channel manager calls this property, each room type and each rate
 * plan -- the ids its support, and its certification form, ask for. Laid out
 * as a property, its rooms, and each room's plans, so each id can be copied
 * against the entity it belongs to.
 */
function ChannelIds({ linkId }: { linkId: string }) {
  const q = useQuery({
    queryKey: ['mapping-editor', linkId],
    queryFn: () => getMappingEditor(linkId),
  })
  const d = q.data
  if (!d) return null
  const lines = [
    `Property ID at Channex: ${d.external_property_id ?? ''}`,
    ...d.rooms.flatMap((r) => [
      `${r.local_name} ID at Channex: ${r.external_id || '(not mapped)'}`,
      ...r.rates.map((p) =>
        `${r.local_name} ${p.local_name} ID at Channex: ${p.external_id || '(not mapped)'}`),
    ]),
  ]
  return (
    <section className="rounded-2xl border border-slate-100 bg-white p-5 shadow-sm">
      <div className="flex items-center gap-2">
        <KeyRound size={20} className="text-brand" />
        <h2 className="text-lg font-semibold text-ink">Channel manager IDs</h2>
        <CopyButton text={lines.join('\n')} label="Copy all" />
      </div>
      <p className="mt-1 text-sm text-slate-500">
        What the channel manager calls this property, its room types and rate
        plans, read from its API when the property was set up.
      </p>
      <table className="mt-4 w-full text-sm">
        <tbody>
          <IdRow label={`Property · ${d.property_name}`} id={d.external_property_id} strong />
          {d.rooms.map((r) => (
            <Fragment key={r.local_id}>
              <IdRow label={`Room · ${r.local_name}`} id={r.external_id} strong />
              {r.rates.map((p) => (
                <IdRow key={p.local_id} label={`${r.local_name} · ${p.local_name}`}
                  id={p.external_id} indent />
              ))}
            </Fragment>
          ))}
        </tbody>
      </table>
    </section>
  )
}

function IdRow({ label, id, strong, indent }: {
  label: string; id: string | null; strong?: boolean; indent?: boolean
}) {
  return (
    <tr className="border-b border-slate-50">
      <td className={`py-2 pr-3 ${indent ? 'pl-6 text-slate-600' : ''} ${
        strong ? 'font-medium text-slate-700' : ''}`}>{label}</td>
      <td className="py-2 text-right">
        {id ? <TaskId id={id} />
          : <span className="text-xs text-red-600">not mapped</span>}
      </td>
    </tr>
  )
}

function CopyButton({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <button
      onClick={() => {
        void navigator.clipboard?.writeText(text)
        setCopied(true); setTimeout(() => setCopied(false), 1500)
      }}
      className="ml-auto flex items-center gap-1 rounded-lg border border-slate-200 px-2.5 py-1.5 text-xs font-semibold text-slate-600 hover:bg-slate-50">
      {copied ? <Check size={12} className="text-emerald-600" /> : <Copy size={12} />}
      {label}
    </button>
  )
}

const WHAT: Record<string, string> = {
  '/availability': 'Availability',
  '/restrictions': 'Rates & restrictions',
}
const WHY: Record<string, string> = {
  change: 'Change', full_sync: 'Full sync', manual: 'Manual',
}

function fmtTime(iso: string) {
  return new Date(iso).toLocaleString(undefined, {
    day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
    second: '2-digit',
  })
}

function fmtRange(a: string | null, b: string | null) {
  if (!a) return '—'
  const f = (d: string) => new Date(`${d}T00:00:00`).toLocaleDateString(
    undefined, { day: 'numeric', month: 'short', year: '2-digit' })
  return a === b ? f(a) : `${f(a)} → ${f(b ?? a)}`
}

function LogRow({ row }: { row: ChannelSyncLogRow }) {
  const [open, setOpen] = useState(false)
  const tone = row.outcome === 'sent'
    ? 'bg-emerald-50 text-emerald-700'
    : row.outcome === 'throttled'
      ? 'bg-amber-50 text-amber-700' : 'bg-red-50 text-red-700'
  return (
    <>
      <tr className="border-b border-slate-50 align-top">
        <td className="py-2">
          <button onClick={() => setOpen(!open)} aria-label="Show request"
            className="text-slate-400 hover:text-slate-600">
            {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          </button>
        </td>
        <td className="whitespace-nowrap py-2 pr-3 text-slate-600">{fmtTime(row.created_at)}</td>
        <td className="py-2 pr-3">
          <div className="font-medium text-slate-700">{WHAT[row.endpoint] ?? row.endpoint}</div>
          <div className="text-xs text-slate-400">{row.value_count} range{row.value_count === 1 ? '' : 's'}</div>
        </td>
        <td className="py-2 pr-3">
          <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${
            row.trigger === 'full_sync' ? 'bg-indigo-50 text-indigo-700' : 'bg-slate-100 text-slate-600'}`}>
            {WHY[row.trigger] ?? row.trigger}
          </span>
        </td>
        <td className="whitespace-nowrap py-2 pr-3 text-slate-600">{fmtRange(row.date_from, row.date_to)}</td>
        <td className="py-2 pr-3">
          <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${tone}`}>
            {row.outcome === 'sent' ? `Accepted${row.status_code ? ` · ${row.status_code}` : ''}`
              : row.outcome === 'throttled' ? 'Rate limited' : `Failed${row.status_code ? ` · ${row.status_code}` : ''}`}
          </span>
        </td>
        <td className="py-2">
          {row.task_ids.length === 0
            ? <span className="text-xs text-slate-400">—</span>
            : row.task_ids.map((t) => <TaskId key={t} id={t} />)}
        </td>
      </tr>
      {open && (
        <tr className="border-b border-slate-50">
          <td />
          <td colSpan={6} className="pb-3">
            {row.error && <p className="mb-2 text-xs text-red-600">{row.error}</p>}
            {row.summary && <p className="mb-2 text-xs text-slate-500">{row.summary}</p>}
            <pre className="max-h-56 overflow-auto rounded-lg bg-slate-50 p-3 text-[11px] leading-relaxed text-slate-600">
              {pretty(row.request_excerpt)}
            </pre>
          </td>
        </tr>
      )}
    </>
  )
}

function pretty(s: string | null) {
  if (!s) return ''
  try { return JSON.stringify(JSON.parse(s), null, 2) } catch { return s }
}

function TaskId({ id }: { id: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <button
      onClick={() => {
        void navigator.clipboard?.writeText(id)
        setCopied(true); setTimeout(() => setCopied(false), 1500)
      }}
      title="Copy task id"
      className="flex items-center gap-1 font-mono text-xs text-slate-600 hover:text-brand">
      <span>{id}</span>
      {copied ? <Check size={12} className="text-emerald-600" /> : <Copy size={12} />}
    </button>
  )
}

const OUTCOME: Record<string, [string, string]> = {
  created: ['Booked', 'bg-emerald-50 text-emerald-700'],
  updated: ['Changed', 'bg-sky-50 text-sky-700'],
  cancelled: ['Cancelled', 'bg-slate-100 text-slate-600'],
  unmapped: ['Room not mapped', 'bg-red-50 text-red-700'],
  no_inventory: ['No room free', 'bg-red-50 text-red-700'],
  failed: ['Failed', 'bg-red-50 text-red-700'],
  claimed: ['Processing', 'bg-amber-50 text-amber-700'],
}

function EventRow({ propertyId, e }: {
  propertyId: string
  e: Awaited<ReturnType<typeof listChannelBookingEvents>>[number]
}) {
  const qc = useQueryClient()
  const [msg, setMsg] = useState('')
  const replay = useMutation({
    mutationFn: () => replayChannelBookingEvent(e.revision_id, propertyId),
    onSuccess: (r) => {
      setMsg(r.status === 'created' || r.status === 'modified'
        ? `Placed${r.reservation_number ? ` as ${r.reservation_number}` : ''}.`
        : `Still ${r.status.replace('_', ' ')}.`)
      qc.invalidateQueries({ queryKey: ['channel-booking-events', propertyId] })
    },
    onError: (err: { response?: { data?: { detail?: string } } }) =>
      setMsg(err?.response?.data?.detail ?? 'Replay failed.'),
  })
  const [label, tone] = OUTCOME[e.outcome] ?? [e.outcome, 'bg-slate-100 text-slate-600']
  return (
    <li className="py-3">
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <span className="font-medium text-slate-700">{e.ota_name || 'Channel'}</span>
            {e.ota_reservation_code && (
              <span className="font-mono text-xs text-slate-500">{e.ota_reservation_code}</span>
            )}
            <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${tone}`}>{label}</span>
          </div>
          <div className="mt-0.5 text-xs text-slate-500">
            {fmtTime(e.created_at)}
            {e.reservation_id && e.reservation_number && (
              <> · <Link to={`/reservations/${e.reservation_id}`}
                className="font-medium text-brand hover:underline">{e.reservation_number}</Link></>
            )}
          </div>
          {e.detail && <p className="mt-1 text-xs text-slate-500">{e.detail}</p>}
          {msg && <p className="mt-1 text-xs font-medium text-slate-700">{msg}</p>}
        </div>
        {e.can_replay && (
          <button onClick={() => replay.mutate()} disabled={replay.isPending}
            className="flex shrink-0 items-center gap-1 rounded-lg border border-slate-200 px-2.5 py-1.5 text-xs font-semibold text-slate-600 hover:bg-slate-50 disabled:opacity-50">
            {replay.isPending ? <Loader2 size={12} className="animate-spin" /> : <RotateCcw size={12} />}
            Replay
          </button>
        )}
      </div>
    </li>
  )
}
