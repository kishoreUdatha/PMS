import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Activity, AlertTriangle, Check, Copy, ExternalLink, FileText, Laptop,
  Layers, Link2,
  Loader2, RefreshCw, Settings, Share2, Clock,
} from 'lucide-react'
import {
  getChannelLink, getOtaStatus, listPropertyModules, pushChannelLink,
  setPropertyModule, type ChannelLink, type OtaStatus, type OtaRow,
} from '../api'
import { fmtDateTime } from '../lib/dates'
import { errorText } from '../lib/forms'

/** Sales Channels: what sells this property, and whether it is working.
 *
 *  Every figure here is read from somewhere real:
 *
 *    Booking Engine   the property's own module flag
 *    Connected OTAs   channels the channel manager reports as active
 *    Room Mapping     rooms_mapped / rooms_total from the link
 *    Last Sync        last_pushed_at -- null until the first push, and a
 *                     timestamp hours old on a property meant to sync every
 *                     minute is the whole diagnosis
 *    Channel Health   last_push_status and last_provision_status
 *
 *  Nothing is shown as healthy because a screen looks better that way. Where
 *  the channel manager cannot be reached the rows say so rather than falling
 *  back to green, because "we could not ask" and "the answer is yes" are
 *  different facts and only one of them is safe to act on.
 */

const BOOKING_MODULE = 'booking_engine'

/** How long ago, in the words somebody would use. */
function ago(iso: string | null): string {
  if (!iso) return 'never'
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins} min ago`
  const hrs = Math.round(mins / 60)
  if (hrs < 24) return `${hrs} hour${hrs === 1 ? '' : 's'} ago`
  const days = Math.round(hrs / 24)
  return `${days} day${days === 1 ? '' : 's'} ago`
}

function Kpi({ icon: Icon, tint, label, value, hint }: {
  icon: typeof Laptop; tint: string; label: string
  value: string; hint?: string
}) {
  return (
    <div className="flex items-center gap-3 rounded-2xl border border-slate-100 bg-white p-4 shadow-sm">
      <span className={`grid h-10 w-10 shrink-0 place-items-center rounded-xl ${tint}`}>
        <Icon size={20} />
      </span>
      <div className="min-w-0">
        <div className="text-sm text-slate-500">{label}</div>
        {/* text-xl, not display size: three of these four read "Live",
            "3 of 3", "1 min ago". They are statuses, not figures, and setting
            a word at the size reserved for a number only inflates the tile
            around it -- these were taller than the dashboard's own KPI cards,
            which carry a sparkline and a delta row as well. */}
        <div className="truncate text-xl font-semibold leading-tight text-ink" title={value}>
          {value}
        </div>
        {hint && <div className="mt-0.5 truncate text-xs text-slate-400" title={hint}>{hint}</div>}
      </div>
    </div>
  )
}

type Health = 'ok' | 'partial' | 'failing' | 'unknown'

function HealthRow({ label, ok, detail }: {
  label: string; ok: Health; detail?: string | null
}) {
  // Four states, not two. `unknown` is "we could not ask", which must not be
  // drawn as yes -- and `partial` is the channel manager's own answer for
  // "registered, but one step did not take". Drawing that red said the
  // property was not registered when it was, and sent somebody looking for
  // the wrong fault.
  const tone = { ok: 'text-emerald-600', partial: 'text-amber-600',
                 failing: 'text-red-600', unknown: 'text-slate-400' }[ok]
  const word = { ok: 'Healthy', partial: 'Partial',
                 failing: 'Failing', unknown: 'Unknown' }[ok]
  return (
    <div className="flex items-center justify-between border-b border-slate-50 py-2.5 last:border-0">
      <span className="text-sm text-slate-600">{label}</span>
      <span className={`flex items-center gap-1.5 text-sm font-medium ${tone}`}
        title={detail ?? undefined}>
        {ok === 'unknown'
          ? <Clock size={15} />
          : ok === 'partial'
            ? <AlertTriangle size={15} />
            : <span className={`grid h-4 w-4 place-items-center rounded-full ${
                ok === 'ok' ? 'bg-emerald-500' : 'bg-red-500'} text-white`}>
                <Check size={11} strokeWidth={3} />
              </span>}
        {word}
      </span>
    </div>
  )
}

function OtaCard({ row, onConnect }: { row: OtaRow; onConnect: () => void }) {
  const live = row.state === 'live'
  const configured = row.state === 'configured'
  const badge = live
    ? { text: 'Connected', cls: 'bg-emerald-50 text-emerald-700' }
    : configured
      ? { text: 'Configured', cls: 'bg-amber-50 text-amber-700' }
      : row.state === 'unknown'
        ? { text: 'Unknown', cls: 'bg-slate-75 text-slate-500' }
        : { text: 'Not connected', cls: 'bg-slate-75 text-slate-500' }

  return (
    <div className="flex flex-col rounded-xl border border-slate-100 p-4">
      <div className="flex items-start gap-3">
        <span className="grid h-10 w-10 shrink-0 place-items-center rounded-lg bg-slate-75 text-sm font-bold text-slate-600">
          {row.partner_name.slice(0, 2).toUpperCase()}
        </span>
        <div className="min-w-0">
          <div className="truncate font-semibold text-ink" title={row.partner_name}>
            {row.partner_name}
          </div>
          <span className={`mt-0.5 inline-block rounded-full px-2 py-0.5 text-xs font-medium ${badge.cls}`}>
            {badge.text}
          </span>
        </div>
      </div>
      {/* The channel manager's own explanation, not a marketing line. */}
      <p className="mt-3 min-h-[2.5rem] text-sm text-slate-500">{row.detail}</p>
      <button
        onClick={onConnect}
        className="mt-3 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm font-medium text-slate-700 hover:border-brand hover:text-brand">
        {live ? 'Manage' : 'Connect'}
      </button>
    </div>
  )
}

export default function SalesChannelsBoard({ propertyId, propertyCode, onOpenSettings }: {
  propertyId: string
  propertyCode?: string | null
  onOpenSettings?: () => void
}) {
  const qc = useQueryClient()
  const [copied, setCopied] = useState(false)
  const [note, setNote] = useState('')

  const modules = useQuery({
    queryKey: ['property-modules', propertyId],
    queryFn: () => listPropertyModules(propertyId),
    enabled: propertyId !== '',
  })
  const linkQ = useQuery({
    queryKey: ['channel-link', propertyId],
    queryFn: () => getChannelLink(propertyId),
    enabled: propertyId !== '',
  })
  const otaQ = useQuery({
    queryKey: ['ota-status', propertyId],
    queryFn: () => getOtaStatus(propertyId),
    enabled: propertyId !== '',
  })

  const link: ChannelLink | null = linkQ.data ?? null
  const ota: OtaStatus | undefined = otaQ.data
  const engineOn = modules.data?.some(
    (m) => m.module_code === BOOKING_MODULE && m.enabled) ?? false
  const bookingUrl = propertyCode
    ? `${window.location.origin}/book/${propertyCode}`
    : ''

  const connected = (ota?.rows ?? []).filter((r) => r.channel_active).length
  const roomsMapped = ota?.rooms_mapped ?? link?.rooms_mapped ?? 0
  const roomsTotal = ota?.rooms_total ?? link?.rooms_total ?? 0
  const ratesMapped = ota?.rates_mapped ?? link?.rates_mapped ?? 0
  const ratesTotal = ota?.rates_total ?? link?.rates_total ?? 0

  // Unreachable means unknown, not healthy.
  const reachable = !ota?.unreachable
  const health = (status: string | null | undefined): Health => {
    const v = (status ?? '').toLowerCase()
    if (!v) return 'unknown'
    if (v === 'ok') return 'ok'
    if (v === 'partial') return 'partial'
    return 'failing'
  }
  const pushOk = health(link?.last_push_status)
  const provisionOk = health(link?.last_provision_status)
  const anyBad = pushOk === 'failing' || provisionOk === 'failing'
  const anyPartial = pushOk === 'partial' || provisionOk === 'partial'

  const steps = [
    { label: 'Property connected', done: Boolean(link?.external_property_id) },
    { label: 'Room types mapped', done: roomsTotal > 0 && roomsMapped === roomsTotal },
    { label: 'Rate plans mapped', done: ratesTotal > 0 && ratesMapped === ratesTotal },
    { label: 'Connect your first OTA', done: connected > 0 },
  ]
  const complete = Math.round(100 * steps.filter((s) => s.done).length / steps.length)

  const sync = useMutation({
    mutationFn: () => pushChannelLink(link!.id),
    onSuccess: (r) => {
      setNote(r.detail || 'Full sync sent.')
      setTimeout(() => setNote(''), 6000)
      qc.invalidateQueries({ queryKey: ['channel-link', propertyId] })
      qc.invalidateQueries({ queryKey: ['ota-status', propertyId] })
    },
    onError: (e: { response?: { data?: { detail?: string } } }) =>
      setNote(errorText(e, 'The full sync failed.')),
  })

  const toggleEngine = useMutation({
    mutationFn: () => setPropertyModule(propertyId, BOOKING_MODULE, !engineOn),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['property-modules', propertyId] }),
  })

  if (modules.isLoading || linkQ.isLoading) {
    return (
      <div className="grid h-64 place-items-center">
        <Loader2 className="animate-spin text-slate-400" />
      </div>
    )
  }

  return (
    <div className="space-y-5">
      {note && (
        <p className="rounded-xl bg-emerald-50 px-4 py-3 text-sm text-emerald-700">{note}</p>
      )}

      {/* ------------------------------------------------------------ kpis */}
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Kpi icon={Laptop} tint="bg-emerald-50 text-emerald-600"
          label="Booking Engine" value={engineOn ? 'Live' : 'Off'}
          hint={engineOn ? 'Taking direct bookings' : 'Not selling online'} />
        <Kpi icon={Share2} tint="bg-blue-50 text-blue-600"
          label="Connected OTAs" value={reachable ? String(connected) : '—'}
          hint={reachable ? 'Active at the channel manager' : 'Channel manager unreachable'} />
        <Kpi icon={Layers} tint="bg-purple-50 text-purple-600"
          label="Room Mapping" value={`${roomsMapped} of ${roomsTotal}`}
          hint={roomsTotal && roomsMapped === roomsTotal
            ? 'Every room type mapped' : 'Unmapped rooms cannot sell'} />
        <Kpi icon={Clock} tint="bg-teal-50 text-teal-600"
          label="Last Sync" value={ago(link?.last_pushed_at ?? null)}
          hint={link?.last_push_detail ?? undefined} />
      </div>

      <div className="grid gap-5 xl:grid-cols-[2fr,1fr]">
        <div className="space-y-5">
          {/* ------------------------------------------ direct booking */}
          <section className="rounded-2xl border border-slate-100 bg-white p-5 shadow-sm">
            <div className="flex flex-wrap items-center gap-2">
              <Laptop size={20} className="text-brand" />
              <h2 className="text-lg font-semibold text-ink">Direct Booking Engine</h2>
              <span className={`rounded-full px-2 py-0.5 text-xs font-semibold ${
                engineOn ? 'bg-emerald-50 text-emerald-700' : 'bg-slate-75 text-slate-500'}`}>
                {engineOn ? 'LIVE' : 'OFF'}
              </span>
              <button onClick={() => toggleEngine.mutate()}
                disabled={toggleEngine.isPending}
                className="ml-auto rounded-lg border border-slate-200 px-3 py-1.5 text-sm font-medium text-slate-600 hover:border-brand hover:text-brand">
                {engineOn ? 'Take off sale' : 'Put on sale'}
              </button>
            </div>
            <p className="mt-1 text-sm text-slate-500">
              Accept commission-free bookings from your own website.
            </p>

            <div className="mt-4 flex flex-wrap items-center gap-2">
              <span className="flex min-w-0 flex-1 items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
                <Link2 size={15} className="shrink-0 text-slate-400" />
                <span className="truncate font-mono text-sm text-slate-600"
                  title={bookingUrl || 'No property code yet'}>
                  {bookingUrl || 'No property code yet'}
                </span>
              </span>
              <button
                disabled={!bookingUrl}
                onClick={() => {
                  navigator.clipboard?.writeText(bookingUrl)
                  setCopied(true)
                  setTimeout(() => setCopied(false), 2000)
                }}
                className="flex items-center gap-2 whitespace-nowrap rounded-lg border border-slate-200 px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50">
                {copied ? <Check size={15} /> : <Copy size={15} />}
                {copied ? 'Copied' : 'Copy link'}
              </button>
              <a href={bookingUrl || undefined} target="_blank" rel="noreferrer"
                className={`flex items-center gap-2 whitespace-nowrap rounded-lg border border-slate-200 px-3 py-2 text-sm font-medium ${
                  bookingUrl ? 'text-slate-700 hover:bg-slate-50'
                             : 'pointer-events-none text-slate-300'}`}>
                <ExternalLink size={15} /> Open booking page
              </a>
              {onOpenSettings && (
                <button onClick={onOpenSettings}
                  className="flex items-center gap-2 whitespace-nowrap rounded-lg border border-slate-200 px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50">
                  <Settings size={15} /> Settings
                </button>
              )}
            </div>
          </section>

          {/* -------------------------------------------------- the OTAs */}
          <section className="rounded-2xl border border-slate-100 bg-white p-5 shadow-sm">
            <div className="flex flex-wrap items-center gap-2">
              <Share2 size={20} className="text-brand" />
              <h2 className="text-lg font-semibold text-ink">Online Travel Agencies</h2>
              <a href="/channels"
                className="ml-auto text-sm font-medium text-brand hover:underline">
                View all channels →
              </a>
            </div>
            <p className="mt-1 text-sm text-slate-500">
              Connect channels to publish rates and availability.
            </p>

            {otaQ.isLoading ? (
              <div className="grid h-32 place-items-center">
                <Loader2 className="animate-spin text-slate-400" />
              </div>
            ) : ota?.unreachable ? (
              <p className="mt-4 rounded-xl bg-amber-50 px-4 py-3 text-sm text-amber-800">
                The channel manager could not be reached, so these are the
                partners on file rather than what is live: {ota.unreachable}
              </p>
            ) : (ota?.rows.length ?? 0) === 0 ? (
              <p className="mt-4 rounded-xl border border-dashed border-slate-200 px-4 py-6 text-center text-sm text-slate-500">
                No channel partners yet. Add one under Channel Partners, and it
                appears here once the channel manager knows about it.
              </p>
            ) : (
              <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                {ota!.rows.map((r) => (
                  <OtaCard key={r.partner_id} row={r}
                    onConnect={() => { window.location.href = '/channels' }} />
                ))}
              </div>
            )}
          </section>
        </div>

        {/* ------------------------------------------------- right column */}
        <div className="space-y-5">
          <section className="rounded-2xl border border-slate-100 bg-white p-5 shadow-sm">
            <div className="flex items-center gap-2">
              <Activity size={20} className="text-brand" />
              <h2 className="text-lg font-semibold text-ink">Channel Health</h2>
            </div>

            <div className={`mt-3 flex items-center gap-3 rounded-xl px-4 py-3 ${
              anyBad ? 'bg-red-50'
                : !reachable || anyPartial ? 'bg-amber-50' : 'bg-emerald-50'}`}>
              <span className={`grid h-8 w-8 place-items-center rounded-full text-white ${
                anyBad ? 'bg-red-500'
                  : !reachable || anyPartial ? 'bg-amber-500' : 'bg-emerald-500'}`}>
                {anyBad || anyPartial
                  ? <AlertTriangle size={16} />
                  : <Check size={16} strokeWidth={3} />}
              </span>
              <span className={`font-semibold ${
                anyBad ? 'text-red-700'
                  : !reachable || anyPartial ? 'text-amber-800' : 'text-emerald-700'}`}>
                {anyBad ? 'Something is failing'
                  : !reachable ? 'Cannot reach the channel manager'
                    : anyPartial ? 'Selling, with one step incomplete'
                      : 'All systems operational'}
              </span>
            </div>

            <div className="mt-3">
              <HealthRow label="Property registered" ok={provisionOk}
                detail={link?.last_provision_detail} />
              <HealthRow label="Rates and availability" ok={pushOk}
                detail={link?.last_push_detail} />
              <HealthRow label="Room mapping"
                ok={roomsTotal === 0 ? 'unknown'
                  : roomsMapped === roomsTotal ? 'ok' : 'failing'}
                detail={`${roomsMapped} of ${roomsTotal} mapped`} />
            </div>

            <button
              onClick={() => sync.mutate()}
              disabled={!link || sync.isPending}
              className="mt-4 flex w-full items-center justify-center gap-2 rounded-xl bg-brand px-4 py-2.5 text-sm font-semibold text-white hover:bg-brand-dark disabled:opacity-50">
              {sync.isPending
                ? <Loader2 size={16} className="animate-spin" />
                : <RefreshCw size={16} />}
              {link ? 'Full sync (500 days)' : 'Not connected'}
            </button>
            {link && (
              <p className="mt-2 text-center text-xs text-slate-400">
                Changes go out on their own within seconds. Use this at go-live
                or to recover after an outage.
              </p>
            )}
          </section>

          <section className="rounded-2xl border border-slate-100 bg-white p-5 shadow-sm">
            <div className="flex items-center gap-2">
              <FileText size={20} className="text-brand" />
              <h2 className="text-lg font-semibold text-ink">Setup Progress</h2>
              <span className="ml-auto text-sm font-semibold text-brand">
                {complete}% complete
              </span>
            </div>
            <div className="mt-3 h-2 overflow-hidden rounded-full bg-slate-100">
              <div className="h-full rounded-full bg-brand transition-[width]"
                style={{ width: `${complete}%` }} />
            </div>
            <ul className="mt-4 space-y-2.5">
              {steps.map((s) => (
                <li key={s.label} className="flex items-center gap-2.5 text-sm">
                  <span className={`grid h-5 w-5 shrink-0 place-items-center rounded-full ${
                    s.done ? 'bg-emerald-500 text-white' : 'border border-slate-300'}`}>
                    {s.done && <Check size={12} strokeWidth={3} />}
                  </span>
                  <span className={s.done ? 'text-slate-600' : 'text-slate-500'}>
                    {s.label}
                  </span>
                </li>
              ))}
            </ul>
            {link?.last_pushed_at && (
              <p className="mt-4 text-xs text-slate-400">
                Last pushed {fmtDateTime(link.last_pushed_at)}
              </p>
            )}
          </section>
        </div>
      </div>
    </div>
  )
}
