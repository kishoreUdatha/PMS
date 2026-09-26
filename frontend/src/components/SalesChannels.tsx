import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Globe, Loader2, AlertTriangle, CheckCircle2, Copy, Check, ExternalLink,
  RefreshCw, Link2, Circle, MinusCircle,
} from 'lucide-react'
import {
  listPropertyModules, setPropertyModule, getChannelLink,
  provisionChannelLink, getOtaStatus, type PropertyModule,
  type ProvisionResult, type OtaStatus,
} from '../api'
import { errorText } from '../lib/forms'

/**
 * Whether this property is on sale to the public, and the link that sells it.
 *
 * Until now this was an API call and nothing else — a property could only be
 * put on sale by somebody with curl, which makes onboarding a tenant a
 * developer's job. It is a one-switch decision that a manager should be able
 * to make and, more importantly, to *see*: the failure it prevents is a hotel
 * that believes it is bookable online and is not.
 *
 * The switch is deliberately not instant-on-click-and-forget. Turning the
 * booking engine on publishes the property to anyone who knows its code, and
 * turning it off takes it off sale the same second — every public endpoint
 * requires the entitlement to resolve the property at all. Both directions get
 * a confirmation that says what will actually happen.
 */
export default function SalesChannels({ propertyId, propertyCode }: {
  propertyId: string
  propertyCode?: string | null
}) {
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const [ok, setOk] = useState('')
  const [confirming, setConfirming] = useState<PropertyModule | null>(null)
  const [copied, setCopied] = useState(false)
  const [syncing, setSyncing] = useState(false)
  const [sync, setSync] = useState<ProvisionResult | null>(null)
  const [syncErr, setSyncErr] = useState('')

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['property-modules', propertyId],
    queryFn: () => listPropertyModules(propertyId),
    enabled: propertyId !== '',
    retry: false,
  })

  // Read-only: whether this property is known to the channel manager at all.
  // A 403 or a missing link is a normal answer, so failure is silence here —
  // the section below simply offers to set it up.
  const { data: link, refetch: refetchLink } = useQuery({
    queryKey: ['channel-link', propertyId],
    queryFn: () => getChannelLink(propertyId),
    enabled: propertyId !== '',
    retry: false,
  })

  // The honest answer to "is it connected", asked of the channel manager
  // rather than inferred from our own tables. A 403 or an outage leaves this
  // undefined, and the section below says so rather than reporting "no".
  const { data: ota, refetch: refetchOta } = useQuery({
    queryKey: ['ota-status', propertyId],
    queryFn: () => getOtaStatus(propertyId),
    enabled: propertyId !== '',
    retry: false,
  })

  /**
   * Set the property up at the channel manager, or catch it up.
   *
   * This already runs by itself the moment a property goes live. The button
   * is for afterwards: a room type added last week is not on sale until
   * somebody mirrors it, and a run that half-failed needs a retry once the
   * cause is fixed. Every step asks what exists first, so pressing it on a
   * property that is already set up changes nothing.
   */
  async function syncChannels() {
    setSyncErr(''); setSync(null); setSyncing(true)
    try {
      const res = await provisionChannelLink(propertyId)
      setSync(res)
      await Promise.all([refetchLink(), refetchOta()])
    } catch (e) {
      const er = e as { response?: { status?: number; data?: { detail?: string } } }
      setSyncErr(er.response?.status === 403
        ? 'Your role does not permit changing channel settings.'
        : errorText(er, 'Could not reach the channel manager.'))
    } finally { setSyncing(false) }
  }

  async function apply(m: PropertyModule, enabled: boolean) {
    setErr(''); setOk(''); setConfirming(null); setBusy(m.module_code)
    try {
      await setPropertyModule(propertyId, m.module_code, enabled)
      await refetch()
      setOk(enabled
        ? `${m.label} is on. The property can now be booked online.`
        : `${m.label} is off. The property is no longer bookable online; `
          + `existing bookings are unaffected.`)
    } catch (e) {
      const er = e as { response?: { status?: number; data?: { detail?: string } } }
      setErr(er.response?.status === 403
        ? 'Your role does not permit changing sales channels. This needs '
          + 'the distribution permission, which sits with management.'
        : errorText(er, 'Could not change that setting.'))
    } finally { setBusy('') }
  }

  // Built from the code, because the code is how the public endpoints address
  // a property — the same string a guest's URL is made of.
  const bookingUrl = propertyCode
    ? `${window.location.origin}/book/${propertyCode}`
    : null

  function copy() {
    if (!bookingUrl) return
    void navigator.clipboard.writeText(bookingUrl)
      .then(() => { setCopied(true); setTimeout(() => setCopied(false), 2000) })
      .catch(() => setErr('The clipboard is not available.'))
  }

  if (isLoading) {
    return (
      <div className="max-w-3xl rounded-2xl border border-slate-100 bg-white p-6 shadow-sm">
        <Loader2 size={18} className="animate-spin text-slate-400" />
      </div>
    )
  }

  // A 403 here is a normal answer for most roles, not a fault worth shouting
  // about — the card simply is not theirs to use.
  if (error) {
    const status = (error as { response?: { status?: number } })?.response?.status
    return (
      <div className="max-w-3xl rounded-2xl border border-slate-100 bg-white p-6 shadow-sm">
        <h2 className="mb-2 flex items-center gap-2 text-section text-ink">
          <Globe size={18} className="text-brand" /> Sales channels
        </h2>
        <p className="text-sm text-slate-500">
          {status === 403
            ? 'Your role does not permit viewing sales channels.'
            : 'Sales channels could not be loaded.'}
        </p>
      </div>
    )
  }

  const engine = (data ?? []).find((m) => m.module_code === 'booking_engine')

  return (
    <div className="max-w-3xl rounded-2xl border border-slate-100 bg-white p-6 shadow-sm">
      <h2 className="flex items-center gap-2 text-section text-ink">
        <Globe size={18} className="text-brand" /> Sales channels
      </h2>
      <p className="mb-4 mt-1 text-sm text-slate-500">
        Where this property can be booked from.
      </p>

      {err && (
        <p className="mb-3 flex items-start gap-2 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700">
          <AlertTriangle size={16} className="mt-0.5 shrink-0" /> {err}
        </p>
      )}
      {ok && (
        <p className="mb-3 flex items-start gap-2 rounded-xl bg-emerald-50 px-4 py-3 text-sm text-emerald-700">
          <CheckCircle2 size={16} className="mt-0.5 shrink-0" /> {ok}
        </p>
      )}

      {(data ?? []).map((m) => (
        <div key={m.module_code}
          className="flex flex-wrap items-start justify-between gap-4 rounded-xl bg-slate-50 px-4 py-3">
          <div className="min-w-0">
            <p className="flex items-center gap-2 text-sm font-semibold text-slate-700">
              {m.label}
              <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${
                m.enabled ? 'bg-emerald-100 text-emerald-700'
                  : 'bg-slate-200 text-slate-600'}`}>
                {m.enabled ? 'On sale' : 'Off sale'}
              </span>
            </p>
            <p className="mt-0.5 text-xs text-slate-500">
              {m.enabled
                ? 'Guests can search and book this property online.'
                : 'Guests cannot find or book this property online.'}
            </p>
          </div>
          <button
            onClick={() => setConfirming(m)}
            disabled={busy === m.module_code}
            className={`shrink-0 rounded-xl px-4 py-2 text-sm font-semibold disabled:opacity-50 ${
              m.enabled
                ? 'border border-slate-300 text-slate-600 hover:border-red-300 hover:text-red-600'
                : 'bg-brand text-white hover:bg-brand/90'}`}>
            {busy === m.module_code
              ? <Loader2 size={15} className="animate-spin" />
              : m.enabled ? 'Take off sale' : 'Put on sale'}
          </button>
        </div>
      ))}

      {confirming && (
        <div className={`mt-3 rounded-xl px-4 py-3 text-sm ${
          confirming.enabled ? 'bg-amber-50 text-caution'
            : 'bg-blue-50 text-blue-800'}`}>
          <p className="flex items-start gap-2">
            <AlertTriangle size={16} className="mt-0.5 shrink-0" />
            <span>
              {confirming.enabled ? (
                <>
                  <b>This takes the property off sale immediately.</b> Guests
                  will no longer find it, and a booking already in progress
                  will fail at payment. Bookings already made are untouched.
                </>
              ) : (
                <>
                  <b>This publishes the property to the internet.</b> Anyone
                  who knows its six-digit code can search it and hold a room —
                  no account and no payment needed to hold. Make sure rates are
                  loaded before you do this.
                </>
              )}
            </span>
          </p>
          <div className="mt-3 flex gap-2">
            <button onClick={() => void apply(confirming, !confirming.enabled)}
              className={`rounded-lg px-3 py-1.5 text-xs font-semibold text-white ${
                confirming.enabled ? 'bg-amber-600' : 'bg-brand'}`}>
              {confirming.enabled ? 'Take it off sale' : 'Put it on sale'}
            </button>
            <button onClick={() => setConfirming(null)}
              className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-600">
              Cancel
            </button>
          </div>
        </div>
      )}

      {engine?.enabled && bookingUrl && (
        <div className="mt-4 border-t border-slate-100 pt-4">
          <p className="mb-2 text-sm font-medium text-slate-600">
            Your booking link
          </p>
          <p className="mb-2 text-xs text-slate-500">
            Put this behind a “Book Now” button on the property’s own website.
          </p>
          <div className="flex items-center gap-2">
            <code className="flex-1 overflow-x-auto whitespace-nowrap rounded-lg bg-slate-50 px-3 py-2.5 text-xs text-slate-700">
              {bookingUrl}
            </code>
            <button onClick={copy}
              className="flex shrink-0 items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-2.5 text-sm font-medium text-slate-600 hover:border-brand hover:text-brand">
              {copied ? <Check size={15} /> : <Copy size={15} />}
              {copied ? 'Copied' : 'Copy'}
            </button>
            <a href={bookingUrl} target="_blank" rel="noreferrer"
              className="flex shrink-0 items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-2.5 text-sm font-medium text-slate-600 hover:border-brand hover:text-brand">
              <ExternalLink size={15} /> Open
            </a>
          </div>
        </div>
      )}

      <div className="mt-4 border-t border-slate-100 pt-4">
        <p className="mb-1 flex items-center gap-2 text-sm font-medium text-slate-600">
          <Link2 size={15} className="text-brand" /> Online travel agents
        </p>
        <p className="mb-3 text-xs text-slate-500">
          {link?.external_property_id ? (
            <>
              This property is set up at the channel manager, and{' '}
              <b className="font-semibold text-slate-600">
                {link.rooms_mapped} of {link.rooms_total}
              </b>{' '}
              room types are mapped. This is kept up to date automatically —
              the button is only for when you do not want to wait.
            </>
          ) : (
            <>
              This happens by itself: when a property goes live, and on a
              regular sweep afterwards. The button runs it now.
            </>
          )}
        </p>

        {/* What the sweep last did. Without this the only record of a
            property that half-provisioned three weeks ago is a container log,
            and the symptom — a room type nobody can book — looks like
            anything but a provisioning failure. */}
        {link?.last_provisioned_at && !sync && (
          <div className={`mb-3 rounded-xl px-4 py-3 text-xs ${
            link.last_provision_status === 'ok'
              ? 'bg-slate-50 text-slate-600'
              : link.last_provision_status === 'partial'
                ? 'bg-amber-50 text-amber-900' : 'bg-red-50 text-red-700'}`}>
            <p className="font-semibold">
              {link.last_provision_status === 'ok'
                ? 'Up to date with the channel manager'
                : link.last_provision_status === 'partial'
                  ? 'Partly set up — needs your attention'
                  : 'Could not be set up'}
              <span className="ml-1.5 font-normal opacity-70">
                · checked {new Date(link.last_provisioned_at)
                  .toLocaleString(undefined, {
                    day: 'numeric', month: 'short', hour: '2-digit',
                    minute: '2-digit',
                  })}
              </span>
            </p>
            {link.last_provision_detail && (
              <ul className="mt-1.5 list-disc space-y-0.5 pl-5">
                {link.last_provision_detail.split('; ').map((d) => (
                  <li key={d}>{d}</li>
                ))}
              </ul>
            )}
          </div>
        )}

        {syncErr && (
          <p className="mb-3 flex items-start gap-2 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700">
            <AlertTriangle size={16} className="mt-0.5 shrink-0" /> {syncErr}
          </p>
        )}

        {sync && (
          <div className={`mb-3 rounded-xl px-4 py-3 text-sm ${
            sync.status === 'ok' ? 'bg-emerald-50 text-emerald-800'
              : sync.status === 'partial' ? 'bg-amber-50 text-amber-900'
                : 'bg-red-50 text-red-700'}`}>
            <p className="font-semibold">
              {sync.status === 'ok'
                ? 'Set up at the channel manager.'
                : sync.status === 'partial'
                  ? 'Partly set up — some of it needs your attention.'
                  : 'Nothing could be set up.'}
            </p>
            {sync.status !== 'failed' && (
              <>
                <p className="mt-1 text-xs">
                  {sync.rooms_mapped} room type{sync.rooms_mapped === 1 ? '' : 's'}
                  {' '}and {sync.rates_mapped} rate plan
                  {sync.rates_mapped === 1 ? '' : 's'} mapped
                  {sync.webhook_registered ? ', bookings will arrive here' : ''}.
                </p>
                {(sync.channels_created > 0 || sync.channels_pending > 0) && (
                  <p className="mt-1 text-xs">
                    {sync.channels_created > 0 && (
                      <>
                        {sync.channels_created} OTA channel
                        {sync.channels_created === 1 ? '' : 's'} built — switch
                        {sync.channels_created === 1 ? ' it' : ' them'} on at
                        the channel manager once the mapping looks right.
                      </>
                    )}
                    {/* Pending is not a failure: the hotel simply has not
                        given us their id with that OTA yet. */}
                    {sync.channels_pending > 0 && (
                      <>
                        {sync.channels_created > 0 ? ' ' : ''}
                        {sync.channels_pending} OTA
                        {sync.channels_pending === 1 ? '' : 's'} still
                        {sync.channels_pending === 1 ? ' needs' : ' need'} their
                        property ID before a channel can be built.
                      </>
                    )}
                  </p>
                )}
              </>
            )}
            {/* Listed, not counted: each line names the room type and what is
                missing, which is what somebody needs to go and fix. */}
            {sync.problems.length > 0 && (
              <ul className="mt-2 list-disc space-y-0.5 pl-5 text-xs">
                {sync.problems.map((p) => <li key={p}>{p}</li>)}
              </ul>
            )}
          </div>
        )}

        {ota && <OtaChain ota={ota} />}

        <button onClick={() => void syncChannels()} disabled={syncing}
          className="flex items-center gap-2 rounded-xl border border-slate-300 px-4 py-2 text-sm font-semibold text-slate-600 hover:border-brand hover:text-brand disabled:opacity-50">
          {syncing
            ? <Loader2 size={15} className="animate-spin" />
            : <RefreshCw size={15} />}
          {syncing ? 'Syncing…' : 'Sync to channel manager'}
        </button>
      </div>
    </div>
  )
}


/**
 * How far along this property actually is, one link at a time.
 *
 * Four things have to be true before a guest on Agoda can book a room here,
 * and only the first two are ours to do. Collapsing them into a single "1
 * connected" badge is what let a hotel believe it was selling on a channel
 * that had never been configured anywhere — so each link is shown separately,
 * and the ones nobody here can perform say who has to perform them.
 */
function OtaChain({ ota }: { ota: OtaStatus }) {
  if (!ota.channel_manager_configured) {
    return (
      <p className="mb-3 rounded-xl bg-slate-50 px-4 py-3 text-xs text-slate-600">
        No channel manager is configured in this deployment, so this property
        cannot reach any OTA. Everything below depends on it.
      </p>
    )
  }

  const rooms = ota.rooms_total > 0 && ota.rooms_mapped === ota.rooms_total
  const rates = ota.rates_total > 0 && ota.rates_mapped === ota.rates_total
  const live = ota.rows.filter((r) => r.state === 'live').length
  const configured = ota.rows.filter((r) => r.state === 'configured').length

  return (
    <div className="mb-3 rounded-xl border border-slate-100 p-4">
      <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
        What is actually live
      </p>

      {ota.unreachable && (
        <p className="mt-2 flex items-start gap-2 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-900">
          <AlertTriangle size={14} className="mt-0.5 shrink-0" />
          {ota.unreachable} The channels below could not be checked, so treat
          them as unknown rather than as disconnected.
        </p>
      )}

      <ol className="mt-3 space-y-2">
        <Link1 done={ota.property_linked}
          label="Property created at the channel manager"
          who="Automatic"
          detail={ota.external_property_id ?? 'Not created yet'} />
        <Link1 done={rooms}
          label={`Room types mirrored and paired (${ota.rooms_mapped} of ${ota.rooms_total})`}
          who="Automatic"
          detail={rooms ? 'Every room type has a counterpart there.'
            : 'Some room types are not paired, so they cannot be sold.'} />
        <Link1 done={rates}
          label={`Rate plans mirrored and paired (${ota.rates_mapped} of ${ota.rates_total})`}
          who="Automatic"
          detail={rates ? 'Every room has a price to send.'
            : 'A room without a rate plan of its own has no price to send.'} />
        <Link1 done={live > 0} partial={configured > 0}
          label="A channel for each OTA, switched on"
          who="You, at the channel manager"
          detail={live > 0
            ? `${live} channel${live === 1 ? '' : 's'} live.`
            : configured > 0
              ? `${configured} channel${configured === 1 ? '' : 's'} created but not switched on.`
              : 'No OTA channel exists yet. Until one does, nothing is sold '
                + 'online through an OTA however complete the rest looks.'} />
      </ol>

      {ota.rows.length > 0 && (
        <div className="mt-4 space-y-2 border-t border-slate-100 pt-3">
          {ota.rows.map((r) => (
            <div key={r.partner_id} className="text-xs">
              <p className="flex flex-wrap items-center gap-2">
                <span className="font-semibold text-slate-700">
                  {r.partner_name}
                </span>
                <span className={`rounded-full px-2 py-0.5 font-semibold ${
                  r.state === 'live' ? 'bg-emerald-100 text-emerald-700'
                    : r.state === 'configured' ? 'bg-amber-100 text-caution'
                      : r.state === 'unknown' ? 'bg-slate-75 text-slate-500'
                        : 'bg-slate-75 text-slate-600'}`}>
                  {r.state === 'live' ? 'Live'
                    : r.state === 'configured' ? 'Not switched on'
                      : r.state === 'unknown' ? 'Unknown'
                        : 'Terms recorded only'}
                </span>
                {r.commission_percent && (
                  <span className="text-slate-400">
                    {r.commission_percent}% commission
                  </span>
                )}
                {r.hotel_id && (
                  <span className="font-mono text-slate-400">
                    their id {r.hotel_id}
                  </span>
                )}
              </p>
              <p className="mt-0.5 text-slate-500">{r.detail}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

/** One link in the chain. `who` names who performs it, because two of the
 *  four are not things this system can do on anybody's behalf. */
function Link1({ done, partial, label, who, detail }: {
  done: boolean; partial?: boolean; label: string; who: string; detail: string
}) {
  return (
    <li className="flex items-start gap-2.5">
      {done ? (
        <CheckCircle2 size={16} className="mt-0.5 shrink-0 text-emerald-600" />
      ) : partial ? (
        <MinusCircle size={16} className="mt-0.5 shrink-0 text-amber-500" />
      ) : (
        <Circle size={16} className="mt-0.5 shrink-0 text-slate-300" />
      )}
      <span className="min-w-0">
        <span className="flex flex-wrap items-center gap-2">
          <span className={`text-xs font-semibold ${
            done ? 'text-slate-700' : 'text-slate-500'}`}>
            {label}
          </span>
          <span className="rounded-full bg-slate-75 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-slate-500">
            {who}
          </span>
        </span>
        <span className="mt-0.5 block break-words text-xs text-slate-500">
          {detail}
        </span>
      </span>
    </li>
  )
}
