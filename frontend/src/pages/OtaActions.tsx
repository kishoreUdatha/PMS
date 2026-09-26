/**
 * OTA Actions — what the hotel owes a channel, and how long it has.
 *
 * A no-show on an OTA booking has to be reported to that OTA within 24 hours
 * or the hotel pays commission on a room nobody slept in. Nothing used to say
 * so and nothing recorded whether anyone had done it: the clerk marked the
 * no-show, the room went back on sale, and the commission quietly stayed owed
 * until a monthly statement said so — by which time the window had closed on
 * every one of them.
 *
 * So the screen is built around the clock, not around the list. Overdue rows
 * come first and say so in words, not just colour; the hours remaining are
 * the loudest thing on each row; and the channel's own reference is shown
 * large, because that is what the extranet is searched by and retyping it
 * wrong is how somebody reports the wrong booking.
 *
 * Reporting is a person going to an extranet. This records that they did, and
 * what came back — a waiver nobody can evidence is a waiver the hotel argues
 * about later and loses.
 */
import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle, CheckCircle2, Clock, ExternalLink, Globe, Loader2, X,
} from 'lucide-react'
import Select from '../components/Select'
import { Crumbs } from '../components/Crumbs'
import { useActivePropertyId } from '../hooks/useProperty'
import {
  listOtaActions, closeOtaAction, type OtaAction,
} from '../api'
import { errorText } from '../lib/forms'

const field = 'w-full rounded-lg border border-slate-200 px-3 py-2.5 text-sm outline-none focus:border-brand'
const lbl = 'mb-1 block text-xs font-medium text-slate-500'

/** How long is left, in the words a person would use. */
function remaining(hours: number): { text: string; tone: string; urgent: boolean } {
  if (hours < 0) {
    const over = Math.abs(hours)
    return {
      text: over < 24
        ? `${Math.floor(over)}h overdue`
        : `${Math.floor(over / 24)}d overdue`,
      tone: 'bg-red-50 text-red-700',
      urgent: true,
    }
  }
  if (hours < 1) {
    return { text: `${Math.max(Math.floor(hours * 60), 1)} min left`,
      tone: 'bg-red-50 text-red-700', urgent: true }
  }
  if (hours < 6) {
    return { text: `${Math.floor(hours)}h left`,
      tone: 'bg-amber-50 text-amber-700', urgent: true }
  }
  return { text: `${Math.floor(hours)}h left`,
    tone: 'bg-slate-100 text-slate-600', urgent: false }
}

export default function OtaActions() {
  const propertyId = useActivePropertyId()
  const qc = useQueryClient()
  const [statusFilter, setStatusFilter] = useState('open')
  const [closing, setClosing] = useState<OtaAction | null>(null)

  const q = useQuery({
    queryKey: ['ota-actions', propertyId, statusFilter],
    queryFn: () => listOtaActions(propertyId, statusFilter || undefined),
    enabled: propertyId !== '',
    // The clock is the point of this screen, so it must not go stale while
    // somebody is looking at it.
    refetchInterval: 60_000,
    refetchIntervalInBackground: false,
  })

  if (propertyId === '') {
    return <p className="text-sm text-slate-500">Pick a property first.</p>
  }

  const data = q.data
  const rows = data?.rows ?? []

  return (
    <div>
      <Crumbs trail={[{ label: 'Distribution', to: '/channels' },
        { label: 'OTA Actions' }]} />

      <div className="mt-2">
        <h1 className="flex items-center gap-2 text-3xl font-bold text-ink">
          <Globe size={26} className="text-brand" /> OTA Actions
        </h1>
        <p className="mt-1 max-w-2xl text-sm text-slate-500">
          A no-show on a channel booking has to be reported to that channel
          within {data?.window_hours ?? 24} hours, or the commission stands and
          the hotel pays it on a room nobody slept in.
        </p>
      </div>

      {(data?.overdue_count ?? 0) > 0 && (
        <p className="mt-4 flex items-start gap-2 rounded-xl bg-red-50 px-4 py-3 text-sm font-medium text-red-700">
          <AlertTriangle size={16} className="mt-0.5 shrink-0" />
          {data!.overdue_count} past the deadline. The commission on{' '}
          {data!.overdue_count === 1 ? 'that booking' : 'those bookings'} may no
          longer be recoverable — report{' '}
          {data!.overdue_count === 1 ? 'it' : 'them'} anyway and record what the
          channel says.
        </p>
      )}

      <div className="mt-5 rounded-2xl border border-slate-100 bg-white shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-5 py-3">
          <h2 className="text-sm font-semibold text-slate-700">
            {data ? `${data.open_count} open` : '…'}
            {(data?.overdue_count ?? 0) > 0 && (
              <span className="ml-1 font-normal text-red-600">
                · {data!.overdue_count} overdue
              </span>
            )}
          </h2>
          <Select className="w-48 rounded-lg border border-slate-200 px-3 py-2 text-sm"
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}>
            <option value="open">Still to do</option>
            <option value="reported">Reported</option>
            <option value="dismissed">Did not apply</option>
            <option value="">Everything</option>
          </Select>
        </div>

        {q.isLoading ? (
          <p className="px-5 py-10 text-center text-sm text-slate-400">
            <Loader2 className="mx-auto mb-2 h-5 w-5 animate-spin" /> Loading…
          </p>
        ) : rows.length === 0 ? (
          <div className="px-5 py-12 text-center">
            <CheckCircle2 className="mx-auto mb-2 text-emerald-500" size={28} />
            <p className="text-sm text-slate-600">
              {statusFilter === 'open'
                ? 'Nothing owed to any channel.'
                : 'Nothing here.'}
            </p>
            <p className="mx-auto mt-1 max-w-md text-xs text-slate-400">
              A row appears here when a booking that came from a channel is
              marked a no-show — whether that was done at the desk or by the
              night audit.
            </p>
          </div>
        ) : (
          <div className="divide-y divide-slate-100">
            {rows.map((r) => (
              <Row key={r.id} r={r} onClose={() => setClosing(r)} />
            ))}
          </div>
        )}
      </div>

      {closing && (
        <CloseDialog action={closing} propertyId={propertyId}
          onClose={() => setClosing(null)}
          onDone={() => {
            setClosing(null)
            qc.invalidateQueries({ queryKey: ['ota-actions'] })
          }} />
      )}
    </div>
  )
}

function Row({ r, onClose }: { r: OtaAction; onClose: () => void }) {
  const left = remaining(r.hours_left)
  const open = r.status === 'open'

  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-2 px-5 py-4">
      <div className="min-w-[13rem] flex-1">
        <p className="font-semibold text-slate-800">{r.action_label}</p>
        <p className="mt-0.5 text-sm text-slate-500">
          {r.ota_name ?? 'Channel not recorded'}
          {r.reservation_number && (
            <> · booking <span className="font-medium text-slate-600">
              {r.reservation_number}</span></>
          )}
        </p>
      </div>

      <div className="min-w-[11rem]">
        <p className={lbl}>Their reference</p>
        {/* Large and selectable: this is what gets typed into an extranet,
            and retyping it wrong reports the wrong booking. */}
        {r.ota_reservation_code ? (
          <p className="select-all font-mono text-sm font-semibold text-slate-800">
            {r.ota_reservation_code}
          </p>
        ) : (
          <p className="text-sm text-amber-700">
            Not recorded — find it in the extranet
          </p>
        )}
      </div>

      <div className="min-w-[8rem]">
        {open ? (
          <span className={`inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-sm font-semibold ${left.tone}`}>
            <Clock size={13} />{left.text}
          </span>
        ) : r.status === 'reported' ? (
          <span className="inline-flex items-center gap-1.5 text-sm font-medium text-emerald-700">
            <CheckCircle2 size={14} /> Reported
          </span>
        ) : (
          <span className="text-sm text-slate-500">Did not apply</span>
        )}
        {!open && r.reference && (
          <p className="mt-0.5 font-mono text-xs text-slate-500">
            {r.reference}
          </p>
        )}
        {!open && r.note && (
          <p className="mt-0.5 max-w-xs text-xs text-slate-400">{r.note}</p>
        )}
      </div>

      {open && (
        <button onClick={onClose}
          className="flex items-center gap-1.5 rounded-lg bg-brand px-3 py-2 text-sm font-semibold text-white hover:bg-brand/90">
          <ExternalLink size={14} /> I reported this
        </button>
      )}
    </div>
  )
}

function CloseDialog({ action, propertyId, onClose, onDone }: {
  action: OtaAction; propertyId: string; onClose: () => void; onDone: () => void
}) {
  const [status, setStatus] = useState<'reported' | 'dismissed'>('reported')
  const [reference, setReference] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  // Dismissing says the obligation was never real. That has to be explained,
  // or a row nobody can check later looks exactly like one that was handled.
  const valid = status === 'reported' || note.trim() !== ''

  async function save() {
    setErr(''); setBusy(true)
    try {
      await closeOtaAction(action.id, propertyId, {
        status,
        reference: reference.trim() || null,
        note: note.trim() || null,
      })
      onDone()
    } catch (e) {
      setErr(errorText(e, 'It could not be closed.'))
    } finally { setBusy(false) }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-slate-900/30 p-4"
      onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()}
        className="mt-16 w-full max-w-lg rounded-2xl bg-white p-6 shadow-xl">
        <div className="mb-4 flex items-start justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold text-ink">
              {action.action_label}
            </h2>
            <p className="mt-0.5 text-sm text-slate-500">
              {action.ota_name}
              {action.ota_reservation_code && (
                <> · <span className="font-mono">{action.ota_reservation_code}</span></>
              )}
            </p>
          </div>
          <button onClick={onClose}
            className="rounded-lg p-1 text-slate-400 hover:bg-slate-100">
            <X size={18} />
          </button>
        </div>

        <div className="flex gap-2">
          {([['reported', 'I reported it'],
             ['dismissed', 'It did not apply']] as const).map(([k, label]) => (
            <button key={k} onClick={() => setStatus(k)}
              className={`flex-1 rounded-lg border px-3 py-2 text-sm font-semibold ${
                status === k
                  ? 'border-brand bg-brand-light text-brand'
                  : 'border-slate-200 text-slate-600 hover:bg-slate-50'}`}>
              {label}
            </button>
          ))}
        </div>

        {status === 'reported' ? (
          <div className="mt-4">
            <label className={lbl}>What the channel gave back</label>
            <input className={field} value={reference}
              placeholder="Waiver or case reference"
              onChange={(e) => setReference(e.target.value)} />
            <span className="mt-1 block text-xs text-slate-400">
              Worth recording even though it is not required: a commission
              waiver nobody can evidence is one the hotel argues about later
              and loses.
            </span>
          </div>
        ) : (
          <p className="mt-4 rounded-lg bg-amber-50 px-3 py-2 text-xs text-caution">
            Use this when the channel had already cancelled the booking, or it
            never came from them. Say which — a dismissed row with no reason
            cannot be checked later.
          </p>
        )}

        <div className="mt-4">
          <label className={lbl}>
            Note {status === 'dismissed' && (
              <span className="text-red-500">— required</span>
            )}
          </label>
          <input className={field} value={note}
            placeholder={status === 'dismissed'
              ? 'Why this did not need reporting'
              : 'Anything worth knowing later'}
            onChange={(e) => setNote(e.target.value)} />
        </div>

        {err && (
          <p className="mt-3 flex items-start gap-2 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">
            <AlertTriangle size={14} className="mt-0.5 shrink-0" />{err}
          </p>
        )}

        <div className="mt-5 flex justify-end gap-2">
          <button onClick={onClose}
            className="rounded-lg border border-slate-200 px-4 py-2 text-sm font-semibold text-slate-600 hover:bg-slate-50">
            Not yet
          </button>
          <button onClick={() => void save()} disabled={!valid || busy}
            className="flex items-center gap-2 rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white enabled:hover:bg-brand/90 disabled:opacity-50">
            {busy && <Loader2 size={15} className="animate-spin" />}
            Close this off
          </button>
        </div>
      </div>
    </div>
  )
}
