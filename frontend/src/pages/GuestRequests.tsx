import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { CheckCircle2, ConciergeBell, Loader2 } from 'lucide-react'
import { listGuestRequests, updateGuestRequest, type GuestRequest } from '../api'
import Select from '../components/Select'
import { useActivePropertyId } from '../hooks/useProperty'
import { FILTER_SELECT } from '../lib/controls'
import { errorText, inputCls } from '../lib/forms'

/**
 * Guest requests — what guests asked for from their portal link.
 *
 * Towels, a late check-out, a cab. Worked like any queue: open first, oldest
 * first, so the guest who asked an hour ago is not behind one who asked a
 * minute ago. The guest sees the status and the note typed here, so the note
 * is written for them, not for colleagues.
 */

const KIND_LABEL: Record<string, string> = {
  housekeeping: 'Housekeeping', amenity: 'Extras', food: 'Food & drinks',
  maintenance: 'Maintenance', late_checkout: 'Late check-out',
  transport: 'Transport', other: 'Other',
}
const STATUS: Record<GuestRequest['status'], { label: string; tone: string }> = {
  open: { label: 'New', tone: 'bg-amber-50 text-amber-800' },
  in_progress: { label: 'On it', tone: 'bg-sky-50 text-sky-700' },
  done: { label: 'Done', tone: 'bg-emerald-50 text-emerald-700' },
  declined: { label: 'Not possible', tone: 'bg-slate-100 text-slate-500' },
}

function ago(iso: string): string {
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins} min ago`
  const h = Math.round(mins / 60)
  return h < 24 ? `${h} h ago` : new Date(iso).toLocaleDateString('en-IN', { day: 'numeric', month: 'short' })
}

export default function GuestRequests() {
  const propertyId = useActivePropertyId()
  const [status, setStatus] = useState('active')
  const qc = useQueryClient()
  const { data = [], isLoading, isError, error } = useQuery({
    queryKey: ['guest-requests', propertyId],
    queryFn: () => listGuestRequests(propertyId),
    enabled: propertyId !== '',
    refetchInterval: 30000,
  })
  const rows = data.filter((r) => status === ''
    || (status === 'active' ? r.status === 'open' || r.status === 'in_progress' : r.status === status))
  const counts = {
    open: data.filter((r) => r.status === 'open').length,
    in_progress: data.filter((r) => r.status === 'in_progress').length,
    done: data.filter((r) => r.status === 'done').length,
  }

  return (
    <div className="space-y-4">
      <h1 className="flex items-center gap-2 text-display text-ink">
        <ConciergeBell size={26} className="text-brand" /> Guest Requests
      </h1>
      <p className="-mt-2 text-sm text-slate-500">
        Sent by guests from their portal link. They see the status and your note.
      </p>

      <div className="grid gap-3 sm:grid-cols-3">
        {([['New', counts.open, counts.open ? 'text-amber-700' : 'text-slate-800'],
          ['On it', counts.in_progress, 'text-slate-800'],
          ['Done', counts.done, 'text-slate-800']] as const).map(([label, n, tone]) => (
          <div key={label} className="rounded-xl border border-slate-100 bg-white p-4">
            <p className="text-xs font-medium text-slate-500">{label}</p>
            <p className={`mt-1 text-xl font-semibold ${tone}`}>{n}</p>
          </div>
        ))}
      </div>

      <div className="flex items-center gap-2">
        <Select blankIsChoice value={status} onChange={(e) => setStatus(e.target.value)}
          aria-label="Status" className={`${FILTER_SELECT} bg-white outline-none focus:border-brand`}>
          <option value="active">Needs attention</option>
          <option value="done">Done</option>
          <option value="declined">Not possible</option>
          <option value="">All</option>
        </Select>
      </div>

      {isError && <p className="text-sm text-red-600">{errorText(error, 'Could not load requests.')}</p>}
      {isLoading && <Loader2 className="animate-spin text-slate-300" />}
      {!isLoading && rows.length === 0 && (
        <p className="rounded-xl border border-slate-100 bg-white px-4 py-10 text-center text-sm text-slate-400">
          Nothing waiting. Requests from guests appear here as they come in.
        </p>
      )}
      <div className="grid gap-3 lg:grid-cols-2">
        {rows.map((r) => (
          <RequestCard key={r.id} r={r} propertyId={propertyId}
            onSaved={() => qc.invalidateQueries({ queryKey: ['guest-requests', propertyId] })} />
        ))}
      </div>
    </div>
  )
}

function RequestCard({ r, propertyId, onSaved }: {
  r: GuestRequest; propertyId: string; onSaved: () => void
}) {
  const [note, setNote] = useState(r.staff_note ?? '')
  const [err, setErr] = useState('')
  const save = useMutation({
    mutationFn: (status: string) => updateGuestRequest(r.id, propertyId, {
      status, staff_note: note.trim() || undefined }),
    onSuccess: () => { setErr(''); onSaved() },
    onError: (e) => setErr(errorText(e, 'Not saved.')),
  })
  const st = STATUS[r.status]
  const live = r.status === 'open' || r.status === 'in_progress'
  return (
    <div className="rounded-xl border border-slate-100 bg-white p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="font-semibold text-ink">{KIND_LABEL[r.kind] ?? r.kind}</p>
          <p className="text-xs text-slate-500">
            {r.guest_name ?? 'Guest'}{r.room ? ` · Room ${r.room}` : ''} ·{' '}
            <Link to={`/reservations/${r.reservation_id}`} className="text-brand hover:underline">
              {r.reservation_number}
            </Link>{' '}· {ago(r.created_at)}
          </p>
        </div>
        <span className={`rounded-full px-2.5 py-1 text-xs font-semibold ${st.tone}`}>{st.label}</span>
      </div>
      <p className="mt-3 whitespace-pre-wrap text-sm text-slate-700">{r.message}</p>
      {live ? (
        <div className="mt-3 space-y-2">
          <input className={inputCls} value={note} maxLength={300}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Note for the guest, e.g. On the way in 10 minutes" />
          <div className="flex flex-wrap gap-2">
            {r.status === 'open' && (
              <button onClick={() => save.mutate('in_progress')} disabled={save.isPending}
                className="rounded-lg border border-slate-200 px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50">
                On it
              </button>
            )}
            <button onClick={() => save.mutate('done')} disabled={save.isPending}
              className="flex items-center gap-1.5 rounded-lg bg-brand px-3 py-1.5 text-sm font-medium text-white hover:bg-brand-dark">
              <CheckCircle2 size={14} /> Done
            </button>
            <button onClick={() => save.mutate('declined')} disabled={save.isPending}
              className="rounded-lg px-3 py-1.5 text-sm font-medium text-slate-500 hover:text-red-600">
              Not possible
            </button>
          </div>
        </div>
      ) : r.staff_note && (
        <p className="mt-2 text-xs text-slate-500">Note: {r.staff_note}</p>
      )}
      {err && <p className="mt-2 text-xs text-red-600">{err}</p>}
    </div>
  )
}
