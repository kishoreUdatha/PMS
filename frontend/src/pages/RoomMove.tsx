import { useEffect, useState } from 'react'
import Select from '../components/Select'
import { useNavigate, useParams } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import DateField from '../components/DateField'
import TimeField from '../components/TimeField'
import { fmtDate } from '../lib/dates'
import {
  AlertTriangle, ArrowLeftRight, BedDouble, CheckCircle2, Info, Loader2,
  Mail, Phone, Search, X,
} from 'lucide-react'
import {
  getRoomMoveView, listMoveCandidates, getMoveQuote, createRoomMove,
  type MoveRoomCard,
} from '../api'
import { Crumbs } from '../components/Crumbs'
import { useActivePropertyId } from '../hooks/useProperty'

/**
 * Screen 053 — Room Move and Upgrade.
 *
 * A move is two facts, not an edit: this room until a moment, that room after
 * it. The old room's occupancy is shortened and the new room gets its own, so
 * the nights already slept stay attached to the room they were slept in.
 *
 * The room list only offers rooms free for every remaining night, but that is
 * convenience — the exclusion constraint decides, and a room taken since this
 * screen loaded comes back as a plain "pick another".
 *
 * An upgrade above the approval threshold is *not* performed. It is recorded as
 * a request and the guest stays where they are, because moving someone into a
 * better room while imagining the paperwork would be worse than waiting.
 */

const money = new Intl.NumberFormat('en-IN', {
  style: 'currency', currency: 'INR', maximumFractionDigits: 0,
})
const exact = new Intl.NumberFormat('en-IN', {
  style: 'currency', currency: 'INR', minimumFractionDigits: 2,
})
const day = (s?: string | null) => {
  if (!s) return '—'
  const d = new Date(`${s.slice(0, 10)}T00:00:00`)
  return fmtDate(d)
}
const today = () => new Date().toISOString().slice(0, 10)

const input = 'w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700 outline-none focus:border-brand'
const label = 'mb-1 block text-sm font-medium text-slate-600'

const READY_TONE: Record<string, string> = {
  ready: 'bg-emerald-100 text-emerald-700',
  cleaning: 'bg-amber-100 text-amber-800',
  dirty: 'bg-rose-100 text-rose-700',
}


/* ------------------------------------------------------------------ page --- */
export default function RoomMove() {
  const { unitId = '' } = useParams()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const propertyId = useActivePropertyId()

  const [roomType, setRoomType] = useState('')
  const [effectiveDate, setEffectiveDate] = useState(today())
  const [effectiveTime, setEffectiveTime] = useState('14:00')
  const [picked, setPicked] = useState('')
  const [reason, setReason] = useState('upgrade_guest_request')
  const [remarks, setRemarks] = useState('')
  const [notifyHk, setNotifyHk] = useState(true)
  const [reissueKey, setReissueKey] = useState(true)
  const [consent, setConsent] = useState(false)
  const [charge, setCharge] = useState(true)
  const [error, setError] = useState('')
  const [done, setDone] = useState<{
    reference: string; status: string; from: string; to: string
    warnings: string[]
  } | null>(null)

  const viewQ = useQuery({
    queryKey: ['roomMoveView', unitId, propertyId],
    queryFn: () => getRoomMoveView(unitId, propertyId),
    enabled: unitId !== '' && propertyId !== '',
    refetchOnWindowFocus: false,
  })
  const v = viewQ.data

  // Default the move to today, or to the arrival if the stay has not started.
  useEffect(() => {
    if (v && v.arrival_date > effectiveDate) setEffectiveDate(v.arrival_date)
  }, [v?.reservation_unit_id]) // eslint-disable-line react-hooks/exhaustive-deps

  const roomsQ = useQuery({
    queryKey: ['moveCandidates', unitId, propertyId, roomType, effectiveDate],
    queryFn: () => listMoveCandidates(unitId, propertyId,
      { room_type_id: roomType, effective_date: effectiveDate }),
    enabled: unitId !== '' && propertyId !== '',
  })
  const rooms = roomsQ.data ?? []

  const quoteQ = useQuery({
    queryKey: ['moveQuote', unitId, propertyId, picked, effectiveDate],
    queryFn: () => getMoveQuote(unitId, propertyId, picked, effectiveDate),
    enabled: picked !== '',
  })
  const quote = quoteQ.data

  const move = useMutation({
    mutationFn: () => createRoomMove(unitId, propertyId, {
      to_room_id: picked, reason, remarks: remarks || null,
      effective_date: effectiveDate, effective_time: effectiveTime,
      notify_housekeeping: notifyHk, reissue_key: reissueKey,
      guest_consent: consent, charge_difference: charge,
    }),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ['rack'] })
      qc.invalidateQueries({ queryKey: ['roomMoveView', unitId] })
      qc.invalidateQueries({ queryKey: ['departures'] })
      setError('')
      setDone({ reference: r.reference, status: r.status, from: r.from_room,
        to: r.to_room, warnings: r.warnings })
    },
    onError: (e) => {
      const er = e as { response?: { data?: { detail?: string } } }
      setError(er.response?.data?.detail ?? 'The move could not be completed.')
    },
  })

  if (viewQ.isLoading) {
    return (
      <p className="flex items-center gap-2 p-8 text-sm text-slate-400">
        <Loader2 size={16} className="animate-spin" /> Loading…
      </p>
    )
  }
  if (viewQ.isError || !v) {
    return (
      <p className="rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700">
        That stay could not be loaded.
      </p>
    )
  }

  const willNeedApproval = Boolean(
    quote && charge && quote.requires_approval && !v.can_approve)
  const canSubmit = picked !== '' && !done

  return (
    <div className="space-y-4">
      {/* -------------------------------------------------------- header --- */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <Crumbs title="Room Move and Upgrade" trail={[
            { label: 'Reservations', to: '/reservations/list' },
            { label: 'Room Move / Upgrade' }]} />
          <h1 className="mt-1 flex items-center gap-2 text-display text-ink">
            <ArrowLeftRight size={26} className="text-brand" /> Room Move and Upgrade
          </h1>
          <p className="text-slate-500">
            Move a guest to another room or request an upgrade with approval.
            Keeps the stay, billing and housekeeping in sync.
          </p>
        </div>
        {done && (
          <div className="rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm">
            <p className="text-xs text-slate-400">Reference No.</p>
            <p className="font-semibold text-slate-800">{done.reference}</p>
          </div>
        )}
      </div>

      {/* Guest strip */}
      <div className="flex flex-wrap items-center gap-x-10 gap-y-3 rounded-2xl border border-slate-100 bg-white p-4 shadow-sm">
        <div>
          <p className="text-lg font-semibold text-slate-800">
            {v.guest_name ?? <span className="italic text-slate-400">No guest on file</span>}
          </p>
          <p className="text-sm text-slate-500">{v.number}</p>
          <div className="mt-1 flex flex-wrap gap-x-4 text-xs text-slate-500">
            {v.guest_phone && (
              <span className="flex items-center gap-1"><Phone size={12} /> {v.guest_phone}</span>
            )}
            {v.guest_email && (
              <span className="flex items-center gap-1"><Mail size={12} /> {v.guest_email}</span>
            )}
          </div>
        </div>
        <Head label="Current Stay"
          value={`${day(v.arrival_date)} – ${day(v.departure_date)}`}
          sub={`${v.nights} Night${v.nights === 1 ? '' : 's'} · ${v.adults} Adults${
            v.children ? `, ${v.children} Child` : ''}`} />
        <Head label="Current Room"
          value={v.current_room ? `Room ${v.current_room.code}` : 'Not assigned'}
          sub={v.current_room?.room_type} />
        <Head label="Stay Balance" value={money.format(v.stay_balance)} />
      </div>

      {v.remaining_nights === 0 && (
        <p className="flex items-start gap-2 rounded-xl bg-amber-50 px-4 py-3 text-sm text-amber-900">
          <Info size={16} className="mt-0.5 shrink-0" />
          <span>
            This stay has no nights left from today ({day(v.departure_date)} was
            the departure). A move can still be recorded for a date inside the
            stay, but there is nothing ahead to move.
          </span>
        </p>
      )}
      {error && (
        <p className="flex items-start gap-2 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700">
          <AlertTriangle size={16} className="mt-0.5 shrink-0" />
          <span>{error}</span>
          <button onClick={() => setError('')} className="ml-auto text-red-400">
            <X size={15} />
          </button>
        </p>
      )}
      {done && (
        <div className={`rounded-xl px-4 py-3 text-sm ${
          done.status === 'completed' ? 'bg-emerald-50 text-emerald-800'
            : 'bg-amber-50 text-amber-900'}`}>
          <p className="flex items-center gap-2 font-semibold">
            {done.status === 'completed'
              ? <><CheckCircle2 size={16} /> Moved from room {done.from} to
                  room {done.to}.</>
              : <><Info size={16} /> Upgrade requested — the guest is still in
                  room {done.from}.</>}
          </p>
          {done.warnings.map((w) => (
            <p key={w} className="mt-1 flex items-start gap-2">
              <Info size={14} className="mt-0.5 shrink-0" /> {w}
            </p>
          ))}
          <button onClick={() => navigate('/reservations/list?tab=arrivals')}
            className="mt-2 rounded-lg bg-brand px-3 py-1.5 text-xs font-semibold text-white">
            Back to Room Rack
          </button>
        </div>
      )}

      {!done && (
        <>
          <div className="grid gap-4 xl:grid-cols-[300px_minmax(0,1fr)]">
            {/* ------------------------------------------- current room --- */}
            <section className="rounded-2xl border border-slate-100 bg-white p-4 shadow-sm">
              <h2 className="mb-3 font-semibold text-ink">
                Current Room (Move From)
              </h2>
              {v.current_room ? <RoomTile room={v.current_room} occupied /> : (
                <p className="text-sm text-amber-700">
                  No room is assigned, so there is nothing to move from. Assign a
                  room from check-in or the rack instead.
                </p>
              )}
            </section>

            {/* --------------------------------------- replacement room --- */}
            <section className="rounded-2xl border border-slate-100 bg-white p-4 shadow-sm">
              <h2 className="mb-3 font-semibold text-ink">
                Replacement Room (Move To / Upgrade)
              </h2>
              <div className="mb-3 grid gap-3 sm:grid-cols-3">
                <label>
                  <span className={label}>Room Type</span>
                  <Select blankIsChoice className={input} value={roomType}
                    onWheel={(e) => (e.target as HTMLSelectElement).blur()}
                    onChange={(e) => { setRoomType(e.target.value); setPicked('') }}>
                    <option value="">All Room Types</option>
                    {v.room_types.map((t) => (
                      <option key={t.id} value={t.id}>{t.name}</option>
                    ))}
                  </Select>
                </label>
                <label>
                  <span className={label}>Effective Date</span>
                  <DateField value={effectiveDate} onChange={(v) => { setEffectiveDate(v); setPicked('') }} min={v.arrival_date} max={v.departure_date} className={input} />
                </label>
                <label>
                  <span className={label}>Time</span>
                  <TimeField className="w-full" value={effectiveTime}
                    label="Effective time"
                    onChange={(v) => setEffectiveTime(v)} />
                </label>
              </div>

              {roomsQ.isLoading && (
                <p className="py-6 text-center text-slate-400">
                  <Loader2 size={16} className="mx-auto animate-spin" />
                </p>
              )}
              {!roomsQ.isLoading && rooms.length === 0 && (
                <p className="flex items-start gap-2 rounded-lg bg-amber-50 px-3 py-2.5 text-sm text-amber-900">
                  <Search size={15} className="mt-0.5 shrink-0" />
                  No room is free for every night from {day(effectiveDate)} to
                  {' '}{day(v.departure_date)}. Try a different date or room type.
                </p>
              )}
              <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
                {rooms.map((r) => (
                  <button key={r.id} onClick={() => setPicked(r.id)}
                    className={`rounded-xl border p-2 text-left ${
                      picked === r.id
                        ? 'border-brand ring-2 ring-brand/30'
                        : 'border-slate-200 hover:border-slate-300'}`}>
                    <RoomTile room={r} compact />
                  </button>
                ))}
              </div>
              {rooms.length > 0 && (
                <p className="mt-2 text-xs text-slate-500">
                  {rooms.length} room{rooms.length === 1 ? '' : 's'} free for the
                  whole remaining stay.
                </p>
              )}
            </section>
          </div>

          <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
            {/* ------------------------------------------------ details --- */}
            <section className="rounded-2xl border border-slate-100 bg-white p-4 shadow-sm">
              <h2 className="mb-3 font-semibold text-ink">
                Move / Upgrade Details
              </h2>
              <div className="grid gap-3 sm:grid-cols-2">
                <label>
                  <span className={label}>Reason <span className="text-rose-500">*</span></span>
                  <Select className={input} value={reason}
                    onWheel={(e) => (e.target as HTMLSelectElement).blur()}
                    onChange={(e) => setReason(e.target.value)}>
                    {v.reasons.map((r) => (
                      <option key={r.code} value={r.code}>{r.label}</option>
                    ))}
                  </Select>
                </label>
                <div className="space-y-2 pt-6">
                  <Check checked={notifyHk} onChange={setNotifyHk}
                    label="Notify Housekeeping"
                    note="Marks the room being vacated as dirty." />
                  <Check checked={reissueKey} onChange={setReissueKey}
                    label="Reissue Key Card"
                    note="Recorded only — no door-lock integration." />
                  <Check checked={consent} onChange={setConsent}
                    label="Guest Consent Obtained"
                    note="That the guest knows and agrees to the move." />
                </div>
                <label className="sm:col-span-2">
                  <span className={label}>Remarks</span>
                  <textarea className={`${input} h-24 resize-none`} value={remarks}
                    maxLength={500}
                    onChange={(e) => setRemarks(e.target.value)} />
                  <span className="mt-1 block text-right text-xs text-slate-400">
                    {remarks.length}/500
                  </span>
                </label>
              </div>
            </section>

            {/* --------------------------------------- rate difference --- */}
            <section className="rounded-2xl border border-slate-100 bg-white p-4 shadow-sm">
              <h2 className="mb-3 font-semibold text-ink">
                Rate Difference (Per Night)
              </h2>
              {!picked ? (
                <p className="py-6 text-center text-sm text-slate-400">
                  Pick a replacement room to see the difference.
                </p>
              ) : quoteQ.isLoading || !quote ? (
                <p className="py-6 text-center text-slate-400">
                  <Loader2 size={16} className="mx-auto animate-spin" />
                </p>
              ) : (
                <>
                  <dl className="space-y-1.5 text-sm">
                    <Row label={`Current Rate (Room ${v.current_room?.code ?? '—'})`}
                      value={exact.format(quote.rate_current)} />
                    <Row label={`New Rate (Room ${
                      rooms.find((r) => r.id === picked)?.code ?? '—'})`}
                      value={exact.format(quote.rate_new)} />
                    <div className={`flex justify-between rounded-lg px-3 py-2 font-semibold ${
                      quote.rate_difference > 0 ? 'bg-amber-50 text-caution'
                        : quote.rate_difference < 0 ? 'bg-sky-50 text-sky-800'
                          : 'bg-slate-50 text-slate-700'}`}>
                      <dt>Difference (Per Night)</dt>
                      <dd className="tabular-nums">{exact.format(quote.rate_difference)}</dd>
                    </div>
                    <Row label="Nights Applicable"
                      value={String(quote.nights_applicable)} />
                    <div className="flex justify-between border-t border-slate-100 pt-2 text-base font-bold text-slate-800">
                      <dt>Total Additional Amount</dt>
                      <dd className="tabular-nums">{exact.format(quote.total_additional)}</dd>
                    </div>
                  </dl>

                  {quote.total_additional > 0 && (
                    <label className="mt-3 flex cursor-pointer items-start gap-2 text-sm">
                      <input type="checkbox" checked={charge} className="mt-0.5"
                        onChange={(e) => setCharge(e.target.checked)} />
                      <span className="text-slate-700">
                        Charge it to the folio
                        <span className="block text-xs text-slate-400">
                          Uncheck for a complimentary upgrade.
                        </span>
                      </span>
                    </label>
                  )}
                  {quote.room_type_changes && (
                    <p className="mt-2 rounded-lg bg-sky-50 px-3 py-2 text-xs text-sky-900">
                      This changes the booked room type, so the reservation will
                      be updated to match.
                    </p>
                  )}
                </>
              )}
            </section>
          </div>

          {/* -------------------------------------------------- footer --- */}
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-slate-100 bg-white p-4 shadow-sm">
            <p className="flex max-w-2xl items-start gap-2 text-sm text-slate-500">
              <Info size={16} className="mt-0.5 shrink-0 text-brand" />
              <span>
                The move updates the guest folio, the room calendar and
                housekeeping. Nights already slept stay attached to the old room.
                {willNeedApproval && (
                  <strong className="block text-caution">
                    {money.format(quote?.total_additional ?? 0)} is above the
                    {' '}{money.format(v.approval_threshold)} threshold — this
                    will be recorded as a request and the guest will NOT be
                    moved.
                  </strong>
                )}
              </span>
            </p>
            <div className="flex gap-2">
              <button onClick={() => navigate('/reservations/list?tab=arrivals')}
                className="rounded-lg border border-slate-200 px-4 py-2.5 text-sm font-semibold text-slate-600 hover:bg-slate-50">
                Cancel
              </button>
              <button disabled={!canSubmit || move.isPending}
                onClick={() => move.mutate()}
                className="flex items-center gap-2 rounded-lg bg-brand px-5 py-2.5 text-sm font-semibold text-white hover:bg-brand-dark disabled:opacity-50">
                {move.isPending
                  ? <Loader2 size={16} className="animate-spin" />
                  : <ArrowLeftRight size={16} />}
                {willNeedApproval ? 'Request Upgrade Approval' : 'Save Room Move'}
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  )
}

/* ------------------------------------------------------------------ bits --- */
function Head({ label: l, value, sub }: {
  label: string; value: string; sub?: string | null
}) {
  return (
    <div>
      <p className="text-xs text-slate-400">{l}</p>
      <p className="font-semibold text-slate-800">{value}</p>
      {sub && <p className="text-xs text-slate-500">{sub}</p>}
    </div>
  )
}

function RoomTile({ room, occupied, compact }: {
  room: MoveRoomCard; occupied?: boolean; compact?: boolean
}) {
  return (
    <div className={compact ? '' : 'space-y-2'}>
      {room.photo_url ? (
        <img src={room.photo_url} alt={`Room ${room.code}`}
          className={`w-full rounded-lg object-cover ${compact ? 'h-16' : 'h-28'}`} />
      ) : (
        <div className={`grid w-full place-items-center rounded-lg bg-slate-75 text-slate-300 ${
          compact ? 'h-16' : 'h-28'}`}>
          <BedDouble size={compact ? 20 : 28} />
        </div>
      )}
      <div className={compact ? 'mt-1.5' : ''}>
        <p className="flex items-center justify-between gap-2">
          <span className="font-semibold text-slate-800">Room {room.code}</span>
          <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${
            occupied ? 'bg-slate-200 text-slate-600'
              : READY_TONE[room.readiness] ?? 'bg-slate-75 text-slate-600'}`}>
            {occupied ? 'Occupied' : room.readiness_label}
          </span>
        </p>
        <p className="text-xs text-slate-500">{room.room_type}</p>
        <p className="mt-0.5 flex flex-wrap gap-x-3 text-[11px] text-slate-400">
          {room.bed_setup && <span>{room.bed_setup}</span>}
          {room.view_type && <span>{room.view_type}</span>}
          {room.floor && <span>Floor {room.floor}</span>}
        </p>
      </div>
    </div>
  )
}

function Row({ label: l, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between">
      <dt className="text-slate-500">{l}</dt>
      <dd className="tabular-nums text-slate-800">{value}</dd>
    </div>
  )
}

function Check({ checked, onChange, label: l, note }: {
  checked: boolean; onChange: (b: boolean) => void; label: string; note?: string
}) {
  return (
    <label className="flex cursor-pointer items-start gap-2 text-sm">
      <input type="checkbox" checked={checked} className="mt-0.5"
        onChange={(e) => onChange(e.target.checked)} />
      <span>
        <span className="text-slate-700">{l}</span>
        {note && <span className="block text-xs text-slate-400">{note}</span>}
      </span>
    </label>
  )
}
