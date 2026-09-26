import { useEffect, useMemo, useState } from 'react'
import { fmtDateTime } from '../lib/dates'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  Loader2, AlertTriangle, CheckCircle2, ArrowRight, Info, Bed, Tag,
  ChevronDown, Lock as LockIcon,
} from 'lucide-react'
import Select from '../components/Select'
import PartnerMark from '../components/PartnerMark'
import OtaMappingTab from '../components/OtaMappingTab'
import {
  listProperties, listChannelPartners, listChannelConnections,
  getChannelLink, getMappingEditor, getOtaStatus, setChannelMappings,
  updateBookingAttribute, updateChannelConnection, provisionChannelLink,
  setSyncSettings, testOtaHotelId,
  type MappingRoom, type MappingEditor, type ConnectionTest,
} from '../api'
import { errorText } from '../lib/forms'

/**
 * Edit one partner: its terms, and how its rooms line up with ours.
 *
 * A page rather than the dialog this replaces. The dialog could only edit the
 * three fields that live on the registry entry — a name, a code and a status
 * — because that is all it knew about. Everything that makes a channel a
 * channel (the OTA's own property id, the commission, and which of their
 * rooms is which of ours) lives on the connection and the link, and none of
 * it fitted in a modal. A mapping tree in particular needs room to breathe:
 * it is read across, not down.
 *
 * The mappings are written by provisioning and shown here to be corrected,
 * not composed. That is why every counterpart is a dropdown of what actually
 * exists at the channel manager rather than a box to type an id into: the
 * codes a hotel can read off an OTA's own extranet are not the ids an
 * arriving booking carries, and a free-text field invites exactly that
 * mistake.
 */
export default function EditChannelPartner() {
  const { partnerId = '' } = useParams()
  const nav = useNavigate()

  const props = useQuery({ queryKey: ['properties'], queryFn: listProperties })
  const property = props.data?.[0]
  const propertyId = property?.id ?? ''
  const organizationId = property?.organization_id ?? ''

  const partners = useQuery({
    queryKey: ['channel-partners', organizationId, '', ''],
    queryFn: () => listChannelPartners(organizationId),
    enabled: organizationId !== '',
  })
  const partner = (partners.data?.rows ?? []).find((p) => p.id === partnerId)

  const conns = useQuery({
    queryKey: ['channel-connections', propertyId],
    queryFn: () => listChannelConnections(propertyId),
    enabled: propertyId !== '',
    retry: false,
  })
  const conn = (conns.data ?? []).find((c) => c.partner_id === partnerId)

  const link = useQuery({
    queryKey: ['channel-link', propertyId],
    queryFn: () => getChannelLink(propertyId),
    enabled: propertyId !== '',
    retry: false,
  })
  const linkId = link.data?.id ?? ''

  const editor = useQuery({
    queryKey: ['mapping-editor', linkId],
    queryFn: () => getMappingEditor(linkId),
    enabled: linkId !== '',
    retry: false,
  })

  const ota = useQuery({
    queryKey: ['ota-status', propertyId],
    queryFn: () => getOtaStatus(propertyId),
    enabled: propertyId !== '',
    retry: false,
  })
  const otaRow = (ota.data?.rows ?? []).find((r) => r.partner_id === partnerId)

  const [tab, setTab] = useState<'mappings' | 'ota' | 'sync'>('mappings')
  const [name, setName] = useState('')
  const [hotelId, setHotelId] = useState('')
  const [commission, setCommission] = useState('')
  const [paymentModel, setPaymentModel] = useState('')
  // Keyed by our id, holding theirs. Seeded from what is saved and then owned
  // by the form, so an edit survives a background refetch.
  const [map, setMap] = useState<Record<string, string>>({})
  const [loaded, setLoaded] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [ok, setOk] = useState('')
  // What the OTA said about the id in the box. Cleared on every keystroke, so
  // a verdict can never belong to a different number than the one on screen.
  const [test, setTest] = useState<ConnectionTest | null>(null)
  const [testing, setTesting] = useState(false)

  async function runTest() {
    if (!hotelId.trim()) return
    setTesting(true); setTest(null)
    try {
      setTest(await testOtaHotelId(partnerId, hotelId.trim()))
    } catch {
      setTest({ verdict: 'unverified',
        message: 'The check could not be run just now.' })
    } finally { setTesting(false) }
  }

  useEffect(() => {
    if (loaded || !partner || !editor.data) return
    setLoaded(true)
    setName(partner.name)
    setHotelId(conn?.ota_hotel_id ?? '')
    setCommission(conn?.commission_percent ?? '')
    setPaymentModel(conn?.payment_model ?? '')
    const seed: Record<string, string> = {}
    for (const room of editor.data.rooms) {
      if (room.external_id) seed[room.local_id] = room.external_id
      for (const rate of room.rates) {
        if (rate.external_id) seed[rate.local_id] = rate.external_id
      }
    }
    setMap(seed)
  }, [loaded, partner, editor.data, conn])

  const d = editor.data
  const connected = otaRow?.state === 'live'

  const counts = useMemo(() => {
    if (!d) return { rooms: 0, rates: 0, roomsTotal: 0, ratesTotal: 0 }
    let rooms = 0; let rates = 0; let roomsTotal = 0; let ratesTotal = 0
    for (const room of d.rooms) {
      if (room.local_code !== '') {
        roomsTotal += 1
        if (map[room.local_id]) rooms += 1
      }
      for (const rate of room.rates) {
        ratesTotal += 1
        if (map[rate.local_id]) rates += 1
      }
    }
    return { rooms, rates, roomsTotal, ratesTotal }
  }, [d, map])

  const [syncing, setSyncing] = useState(false)

  /** Saved as the switch is flipped. A preference behind a Save button is a
   *  preference people leave unsaved, and this one decides whether a hotel's
   *  prices reach the OTAs at all. */
  async function saveSync(v: {
    send_availability: boolean; send_rates: boolean
    send_restrictions: boolean; notify_on_failure: boolean
  }) {
    if (!d) return
    setErr(''); setOk(''); setSyncing(true)
    try {
      await setSyncSettings(d.link_id, v)
      await editor.refetch()
      setOk('Synchronisation settings saved.')
    } catch (e) {
      const er = e as { response?: { status?: number; data?: { detail?: string } } }
      setErr(er.response?.status === 403
        ? 'Your role does not permit changing synchronisation settings.'
        : errorText(er, 'Could not save those settings.'))
    } finally { setSyncing(false) }
  }

  async function save() {
    if (name.trim().length < 2) return setErr('Give the partner a name.')
    // Same rule as the wizard, and the same rule the server enforces: an
    // online channel with no id of its own is a connection that can never
    // sell anything.
    if (test?.verdict === 'rejected') {
      return setErr(`${partner?.name ?? 'That channel'} does not recognise `
        + 'that property id, so saving it would build a channel that can '
        + 'never sell. Correct it, or clear the check and try again.')
    }
    if (conn && !hotelId.trim()) {
      return setErr(`${partner?.name ?? 'This channel'} needs their own `
        + 'property id for this hotel. It comes with your contract with them, '
        + 'and without it no channel can be built and nothing is sold.')
    }
    setErr(''); setOk(''); setBusy(true)
    try {
      await updateBookingAttribute(partnerId, {
        organization_id: organizationId, kind: 'business_source',
        name: name.trim(), status: partner?.status ?? 'active',
      })

      let built = false
      if (conn) {
        const idChanged = (conn.ota_hotel_id ?? '') !== hotelId.trim()
        await updateChannelConnection(conn.id, {
          partner_id: conn.partner_id, property_id: conn.property_id,
          commission_percent: commission.trim() ? Number(commission) : null,
          payment_model: paymentModel || null,
          ota_hotel_id: hotelId.trim() || null,
        })
        if (idChanged && hotelId.trim()) {
          // A new id is the one field here that can build something. Best
          // effort: everything above is saved either way, and the sweep
          // retries what this misses.
          try { await provisionChannelLink(propertyId); built = true } catch {
            /* left to the sweep */
          }
        }
      }

      if (d) {
        const rooms: { local_id: string; external_id: string }[] = []
        const rates: { local_id: string; external_id: string }[] = []
        for (const room of d.rooms) {
          if (room.local_code !== '') {
            rooms.push({ local_id: room.local_id,
              external_id: map[room.local_id] ?? '' })
          }
          for (const rate of room.rates) {
            rates.push({ local_id: rate.local_id,
              external_id: map[rate.local_id] ?? '' })
          }
        }
        await setChannelMappings(d.link_id, { rooms, rates })
      }

      await Promise.all([editor.refetch(), conns.refetch(), ota.refetch()])
      setOk(built
        ? 'Saved, and the channel was built at the channel manager.'
        : 'Saved. New mappings apply to future synchronisation.')
    } catch (e) {
      const er = e as { response?: { status?: number; data?: { detail?: string } } }
      setErr(er.response?.status === 409
        ? 'Two of your rooms point at the same one of theirs, which would '
          + 'make an arriving booking ambiguous. Give each a different one.'
        : errorText(er, 'Could not save those changes.'))
    } finally { setBusy(false) }
  }

  if (props.isLoading || partners.isLoading || (linkId && editor.isLoading)) {
    return (
      <div className="grid place-items-center py-20">
        <Loader2 className="animate-spin text-slate-400" />
      </div>
    )
  }

  if (!partner) {
    return (
      <div className="rounded-2xl border border-slate-100 bg-white p-8 text-center">
        <p className="text-sm text-slate-500">That partner could not be found.</p>
        <Link to="/channels"
          className="mt-3 inline-block text-sm font-semibold text-brand hover:underline">
          Back to Channel Partners
        </Link>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <div>
        <p className="text-sm text-slate-400">
          <Link to="/channels" className="hover:text-brand">Channel Partners</Link>
          {' / '}{partner.name}{' / '}
          <span className="text-slate-600">Edit</span>
        </p>
        {/* One header row, not two.
            This was a page title, a subtitle, and then a whole bordered card
            below it carrying an avatar, the partner's name, a badge and a
            timestamp. The card cost about a hundred and ten pixels to say one
            line, and the name appeared three times before any field did --
            breadcrumb, card, subtitle. The two facts worth keeping (is it
            selling, when did it last sync) now sit beside the title, and the
            identity is left to the breadcrumb, which is what a breadcrumb is
            for. */}
        <div className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-2">
          <PartnerMark name={partner.name} size={40} />
          <h1 className="text-display text-ink">
            {partner.name}
          </h1>
          <span className={`flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-semibold ${
            connected ? 'bg-emerald-100 text-emerald-700'
              : otaRow?.state === 'configured' ? 'bg-amber-100 text-caution'
                : 'bg-slate-75 text-slate-600'}`}>
            {connected ? <CheckCircle2 size={13} />
              : <AlertTriangle size={13} />}
            {connected ? 'Connected'
              : otaRow?.state === 'configured' ? 'Not switched on'
                : 'Terms recorded only'}
          </span>
          <span className="hidden h-5 w-px bg-slate-200 sm:block" />
          <span className="text-sm text-slate-500">
            {d?.last_pushed_at
              ? `Last sync ${fmtDateTime(d.last_pushed_at)}`
              : 'Never synced'}
          </span>
        </div>
      </div>

      {err && (
        <p className="flex items-start gap-2 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700">
          <AlertTriangle size={16} className="mt-0.5 shrink-0" /> {err}
        </p>
      )}
      {ok && (
        <p className="flex items-start gap-2 rounded-xl bg-emerald-50 px-4 py-3 text-sm text-emerald-700">
          <CheckCircle2 size={16} className="mt-0.5 shrink-0" /> {ok}
        </p>
      )}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="rounded-2xl border border-slate-100 bg-white p-5">
          <h2 className="text-section text-ink">
            Connection details
          </h2>
          {/* All five on one row on a wide screen, two up on a tablet, stacked
              on a phone. Column widths are set rather than equal: a name and a
              property need the room, a commission is four characters and does
              not.

              None of them carries its own hint any more. At a fifth of the
              card each, a two-line hint under one field made that field taller
              than its neighbours and opened a gap across the whole row -- the
              wasted space this is fixing. What those hints said is in the
              caption below, where one line can explain both ids at once and
              nothing has to reflow. */}
          <div className="mt-3 grid gap-x-4 gap-y-3 sm:grid-cols-2 lg:grid-cols-[1.2fr_1.2fr_1fr_0.7fr_1fr]">
            <Field label="Partner name">
              <input value={name} maxLength={120}
                onChange={(e) => setName(e.target.value)} className={INPUT} />
            </Field>
            <Field label="Property">
              <input value={d?.property_name ?? property?.name ?? ''} readOnly
                className={`${INPUT} bg-slate-50 text-slate-600`} />
            </Field>
            <Field label={`${partner.name} ID *`}>
              <span className="flex gap-2">
                <input value={hotelId} maxLength={80} required
                  aria-required="true"
                  onChange={(e) => { setHotelId(e.target.value); setTest(null) }}
                  placeholder="e.g. 96019126"
                  className={`${INPUT} font-mono ${
                    test?.verdict === 'rejected' ? 'border-red-300 bg-red-50/40'
                      : test?.verdict === 'ok' ? 'border-emerald-300'
                        : conn && !hotelId.trim()
                          ? 'border-amber-300 bg-amber-50/40' : ''}`} />
                <button type="button" onClick={() => void runTest()}
                  disabled={testing || !hotelId.trim()}
                  className="shrink-0 rounded-lg border border-slate-200 px-3 text-sm font-semibold text-slate-600 hover:border-brand hover:text-brand disabled:opacity-50">
                  {testing ? <Loader2 size={15} className="animate-spin" />
                    : 'Check'}
                </button>
              </span>
            </Field>
            <Field label="Commission %">
              <input value={commission} type="number" min={0} max={100}
                step="0.01" placeholder="15"
                onChange={(e) => setCommission(e.target.value)}
                className={INPUT} />
            </Field>
            <Field label="Payment model">
              <Select value={paymentModel} className={INPUT}
                onChange={(e) => setPaymentModel(e.target.value)}>
                <option value="">Not set</option>
                <option value="hotel_collect">Hotel collect</option>
                <option value="channel_collect">Channel collect</option>
                <option value="virtual_card">Virtual card</option>
              </Select>
            </Field>
          </div>

          {test && (
            <p className={`mt-2.5 flex items-start gap-2 rounded-xl px-3 py-2.5 text-xs ${
              test.verdict === 'ok' ? 'bg-emerald-50 text-emerald-800'
                : test.verdict === 'rejected' ? 'bg-red-50 text-red-700'
                  : 'bg-slate-50 text-slate-600'}`}>
              {test.verdict === 'ok'
                ? <CheckCircle2 size={14} className="mt-0.5 shrink-0" />
                : test.verdict === 'rejected'
                  ? <AlertTriangle size={14} className="mt-0.5 shrink-0" />
                  : <Info size={14} className="mt-0.5 shrink-0" />}
              {test.message}
            </p>
          )}

          {/* Both ids explained in one line. The channel manager's is shown
              and not hidden because it is what an arriving booking is routed
              by -- somebody asking "why did this booking land here" needs to
              see it -- but it is a caption rather than a field, because
              provisioning writes it and nobody should type over it. */}
          <p className="mt-2.5 text-xs text-slate-400">
            <b className="font-semibold text-slate-500">{partner.name} ID</b>
            {' '}is their own id for this hotel, from your contract with them.
            {' '}&middot; Channel manager ID{' '}
            <span className="font-mono text-slate-500">
              {d?.external_property_id ?? 'not created yet'}
            </span>
            , created for you, and what bookings are routed by.
          </p>

          <div className="mt-4 flex gap-6 border-b border-slate-100">
            {([['mappings', 'Room & rate mappings'],
              ['ota', `${partner.name} rooms & go live`],
              ['sync', 'Sync settings']] as const).map(([key, label]) => (
              <button key={key} onClick={() => setTab(key)}
                className={`-mb-px border-b-2 px-1 pb-2.5 text-sm font-semibold transition-colors ${
                  tab === key
                    ? 'border-brand text-brand'
                    : 'border-transparent text-slate-500 hover:text-slate-700'}`}>
                {label}
              </button>
            ))}
          </div>

          {tab === 'mappings' ? (
            <MappingTree
              rooms={d?.rooms ?? []} map={map} setMap={setMap}
              candidateRooms={d?.candidate_rooms ?? []}
              candidateRates={d?.candidate_rates ?? []}
              unreachable={d?.unreachable ?? null}
              partnerName={partner.name}
            />
          ) : tab === 'ota' ? (
            conn?.external_channel_id ? (
              <OtaMappingTab connectionId={conn.id} partnerName={partner.name}
                onLiveChange={() => void ota.refetch()} />
            ) : (
              <p className="mt-4 flex items-start gap-2 rounded-xl bg-slate-50 px-3 py-2.5 text-sm text-slate-600">
                <Info size={15} className="mt-0.5 shrink-0" />
                The {partner.name} channel is built at the channel manager from
                the {partner.name} ID above, once it is saved. Its rooms can be
                paired here after that.
              </p>
            )
          ) : (
            <SyncTab d={d} saving={syncing}
              onSave={(v) => void saveSync(v)} />
          )}
        </div>

        <aside className="h-fit rounded-2xl border border-slate-100 bg-white p-5">
          <h2 className="text-section text-ink">
            Connection summary
          </h2>
          <dl className="mt-2 divide-y divide-slate-100">
            <Row k="Property" v={d?.property_name ?? '—'} />
            <Row k="Currency" v={d?.currency ?? '—'} />
            <Row k="Timezone" v={d?.timezone ?? '—'} />
            <Row k="Room types"
              v={`${counts.rooms} of ${counts.roomsTotal} mapped`} />
            <Row k="Rate plans"
              v={`${counts.rates} of ${counts.ratesTotal} mapped`} />
          </dl>

          <div className="mt-5 flex items-center justify-between">
            <h2 className="text-section text-ink">
              Sync settings
            </h2>
            <button onClick={() => setTab('sync')}
              className="text-xs font-semibold text-brand hover:underline">
              Edit settings
            </button>
          </div>
          <dl className="mt-2 divide-y divide-slate-100">
            <Row k="Rates &amp; availability"
              v={d?.last_push_status === 'ok' ? 'Sending'
                : d?.last_push_status ? 'Problem' : 'Not yet sent'} />
            {/* Three states, because there are three.
                This said "Not connected" whenever no OTA was live, while the
                Sync tab two clicks away said delivery was "Always on" -- two
                rows describing one thing in opposite words. The webhook being
                registered and an OTA being live are different facts, and the
                middle state (ready, nothing sending yet) is the common one
                for a property that has just been set up. */}
            <Row k="Reservation updates"
              v={connected ? 'Arriving'
                : d?.webhook_registered ? 'Ready, no channel live'
                  : 'Not set up'} />
          </dl>

          <p className="mt-4 flex items-start gap-2 rounded-xl bg-blue-50 px-3 py-2.5 text-xs text-blue-900">
            <Info size={15} className="mt-0.5 shrink-0 text-blue-500" />
            Saved mapping changes apply to future synchronisation. Existing
            reservations keep their current mappings.
          </p>

          <Link to="/rates/calendar"
            className="mt-4 inline-flex items-center gap-1.5 text-sm font-semibold text-brand hover:underline">
            View sync activity <ArrowRight size={14} />
          </Link>
        </aside>
      </div>

      {/* In the flow, not floating. Fixed, it covered the bottom of the
          mapping tree -- the part somebody is most likely to be reading as
          they reach for Save. */}
      <div className="flex justify-between gap-3">
        <button onClick={() => nav('/channels')}
          className="rounded-xl border border-slate-200 bg-white px-6 py-2.5 text-sm font-medium text-slate-600 hover:bg-slate-50">
          Cancel
        </button>
        <button onClick={() => void save()} disabled={busy}
          className="flex items-center gap-2 rounded-xl bg-brand px-6 py-2.5 text-sm font-semibold text-white hover:bg-brand/90 disabled:opacity-50">
          Save changes
          {busy ? <Loader2 size={16} className="animate-spin" />
            : <ArrowRight size={16} />}
        </button>
      </div>
    </div>
  )
}

const INPUT = 'w-full rounded-lg border border-slate-200 px-3 py-2.5 text-sm outline-none focus:border-brand'

function Field({ label, children }: {
  label: string; children: React.ReactNode
}) {
  return (
    <label className="block">
      <span className="mb-1 block truncate text-sm font-medium text-slate-600">
        {label}
      </span>
      {children}
    </label>
  )
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex items-center justify-between gap-4 py-2.5">
      <dt className="shrink-0 text-sm text-slate-500">{k}</dt>
      <dd className="text-right text-sm font-semibold text-slate-800">{v}</dd>
    </div>
  )
}

/**
 * Ours on the left, theirs on the right, rate plans nested under the room
 * they belong to.
 *
 * Nested rather than listed flat because that is what they are: a channel
 * rate plan belongs to exactly one room, which is the rule provisioning
 * enforces when it creates them. Two flat lists would hide the only
 * relationship that matters and let somebody pair a suite's price with a
 * standard double.
 */
function MappingTree({ rooms, map, setMap, candidateRooms, candidateRates,
  unreachable, partnerName }: {
  rooms: MappingRoom[]
  map: Record<string, string>
  setMap: (v: Record<string, string>) => void
  candidateRooms: { id: string; title: string }[]
  candidateRates: { id: string; title: string; room_type_id: string | null }[]
  unreachable: string | null
  partnerName: string
}) {
  const set = (localId: string, externalId: string) =>
    setMap({ ...map, [localId]: externalId })

  // Open by default. A mapping screen exists to be read across, and opening
  // three rooms one at a time to see where you are is the opposite of that;
  // collapsing is for a property with thirty of them.
  const [shut, setShut] = useState<Record<string, boolean>>({})
  const toggle = (id: string) => setShut({ ...shut, [id]: !shut[id] })

  return (
    <div className="mt-4">
      <h3 className="text-sm font-semibold text-slate-700">
        Room &amp; rate mappings
      </h3>
      <p className="mt-1 text-sm text-slate-500">
        Written for you when the property was set up. Change one only to
        correct it.
      </p>

      {unreachable && (
        <p className="mt-3 flex items-start gap-2 rounded-xl bg-amber-50 px-4 py-3 text-sm text-amber-900">
          <AlertTriangle size={16} className="mt-0.5 shrink-0" /> {unreachable}
        </p>
      )}

      <div className="mt-3 overflow-hidden rounded-xl border border-slate-100">
        {/* xs, uppercase, tracked -- the table-header style twenty-one other
            screens in this app already use. The mockup sets it in sentence
            case at 14px, and following that here would make this the one
            table in the product whose header looks different. A mockup is one
            screen; a convention is the whole application. */}
        <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-4 border-b border-slate-100 bg-slate-50 px-4 py-2.5 text-xs font-semibold uppercase tracking-wide text-slate-500">
          <span>PMS room / rate plan</span>
          {/* Named for what it actually holds. "Agoda mapping" would be the
              friendlier label and the wrong one: these are the channel
              manager's ids, shared by every channel this property sells
              through, and an arriving Agoda booking carries them rather than
              anything from Agoda's own extranet. */}
          <span>Channel manager mapping</span>
        </div>

        {rooms.length === 0 && (
          <p className="px-4 py-6 text-center text-sm text-slate-400">
            Nothing to map yet.
          </p>
        )}

        {rooms.map((room) => {
          // The group that holds plans spanning several room types. They
          // cannot be sold on a channel, so they are shown without a
          // dropdown rather than silently dropped.
          const sellable = room.local_code !== ''
          const theirRoom = map[room.local_id] ?? ''
          return (
            <div key={room.local_id}
              className="border-b border-slate-50 last:border-0">
              <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)] items-center gap-4 px-4 py-3">
                <span className="flex min-w-0 items-center gap-2">
                  {sellable && room.rates.length > 0 ? (
                    <button onClick={() => toggle(room.local_id)}
                      aria-expanded={!shut[room.local_id]}
                      aria-label={`${room.local_name} rate plans`}
                      className="shrink-0 rounded p-0.5 text-slate-400 hover:bg-slate-100 hover:text-slate-600">
                      <ChevronDown size={15} className={`transition-transform ${
                        shut[room.local_id] ? '-rotate-90' : ''}`} />
                    </button>
                  ) : <span className="w-[22px] shrink-0" />}
                  {sellable
                    ? <Bed size={15} className="shrink-0 text-slate-400" />
                    : <AlertTriangle size={15} className="shrink-0 text-amber-500" />}
                  <span className="truncate font-semibold text-slate-800">
                    {room.local_name}
                  </span>
                  {sellable && (
                    <span className="shrink-0 font-mono text-xs text-slate-400">
                      {room.local_code}
                    </span>
                  )}
                </span>
                {sellable ? (
                  <Select value={theirRoom}
                    onChange={(e) => set(room.local_id, e.target.value)}
                    className={INPUT}>
                    <option value="">Not mapped</option>
                    {candidateRooms.map((c) => (
                      <option key={c.id} value={c.id}>{c.title}</option>
                    ))}
                  </Select>
                ) : (
                  <span className="text-xs text-caution">
                    Covers more than one room type, so {partnerName} has no
                    single price to sell. Split it to use it here.
                  </span>
                )}
              </div>

              {!shut[room.local_id] && room.rates.map((rate, i) => {
                // Only the plans that sit under the room already chosen. A
                // dropdown of every plan in the property is how a suite's
                // price ends up on a standard double.
                const options = theirRoom
                  ? candidateRates.filter((c) => c.room_type_id === theirRoom)
                  : candidateRates
                return (
                  <div key={rate.local_id}
                    className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)] items-center gap-4 px-4 py-2">
                    <span className="flex min-w-0 items-center gap-2 pl-3">
                      {/* The connector the mockup draws: a stem down from the
                          room, turning into the row. Last child stops at the
                          turn so the line does not run past the tree. */}
                      {/* Reaches up through the row's padding so the stem
                          meets the room above it. Stopped short of that and
                          it reads as a stray tick rather than a tree. */}
                      <span className="relative ml-[3px] -mt-2 h-[26px] w-3.5 shrink-0 rounded-bl border-b border-l border-slate-200">
                        {i !== room.rates.length - 1 && (
                          <span className="absolute -left-px top-[26px] h-4 w-px bg-slate-200" />
                        )}
                      </span>
                      <Tag size={14} className="shrink-0 text-slate-400" />
                      <span className="truncate text-sm text-slate-600">
                        {rate.local_name}
                      </span>
                    </span>
                    {sellable ? (
                      <Select value={map[rate.local_id] ?? ''}
                        onChange={(e) => set(rate.local_id, e.target.value)}
                        className={INPUT}>
                        <option value="">Not mapped</option>
                        {options.map((c) => (
                          <option key={c.id} value={c.id}>{c.title}</option>
                        ))}
                      </Select>
                    ) : (
                      <span className="text-xs text-slate-400">—</span>
                    )}
                  </div>
                )
              })}
            </div>
          )
        })}
      </div>
    </div>
  )
}

/**
 * What this property sends, what it receives, and who hears when it breaks.
 *
 * The switches are **property-wide**, and the screen says so rather than
 * implying otherwise. The design this follows puts them under "Send to
 * Trip.com", which reads better and would be a lie here: rates and
 * availability are pushed once per property and the channel manager fans them
 * out to every channel, so there is no moment at which availability could be
 * withheld from one OTA and not another. A switch that cannot do what its
 * label claims is worse than one that is missing.
 *
 * Receiving is shown locked because it is: bookings arrive on a webhook
 * registered against the property, and there is no state in which a connected
 * channel should be able to stop delivering them. A reservation that the OTA
 * has taken exists whether or not we feel like hearing about it.
 */
function SyncTab({ d, onSave, saving }: {
  d: MappingEditor | undefined
  onSave: (v: {
    send_availability: boolean; send_rates: boolean
    send_restrictions: boolean; notify_on_failure: boolean
  }) => void
  saving: boolean
}) {
  const [avail, setAvail] = useState(true)
  const [rates, setRates] = useState(true)
  const [limits, setLimits] = useState(true)
  const [notify, setNotify] = useState(true)
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    if (!d || loaded) return
    setLoaded(true)
    setAvail(d.send_availability)
    setRates(d.send_rates)
    setLimits(d.send_restrictions)
    setNotify(d.notify_on_failure)
  }, [d, loaded])

  const change = (next: Partial<{
    a: boolean; r: boolean; x: boolean; n: boolean
  }>) => {
    const a = next.a ?? avail, r = next.r ?? rates
    const x = next.x ?? limits, n = next.n ?? notify
    setAvail(a); setRates(r); setLimits(x); setNotify(n)
    onSave({ send_availability: a, send_rates: r,
      send_restrictions: x, notify_on_failure: n })
  }

  return (
    <div className="mt-4">
      <h3 className="flex flex-wrap items-baseline gap-2 text-sm font-semibold text-slate-700">
        Send to the channel manager
        <span className="text-xs font-normal text-slate-400">
          PMS &rarr; every channel this property sells through
        </span>
      </h3>

      <div className="mt-3 divide-y divide-slate-100 rounded-xl border border-slate-100">
        <Toggle on={avail} disabled={saving}
          onChange={(v) => change({ a: v })}
          title="Room availability"
          body="How many of each mapped room type are sellable, every night for the next year." />
        <Toggle on={rates} disabled={saving}
          onChange={(v) => change({ r: v })}
          title="Room rates"
          body="The nightly price of each mapped rate plan." />
        {/* A real switch now. It was deliberately not one while nothing
            behind it sent anything: a toggle that does nothing is worse than
            an absent one, because a hotel would have believed its
            minimum-stay rules were reaching the OTAs. */}
        <Toggle on={limits} disabled={saving}
          onChange={(v) => change({ x: v })}
          title="Booking restrictions"
          body="Minimum and maximum stay, closed to arrival or departure, and stop-sell — from your published rate rules." />
      </div>

      <h3 className="mt-6 flex flex-wrap items-baseline gap-2 text-sm font-semibold text-slate-700">
        Receive from the channel manager
        <span className="text-xs font-normal text-slate-400">
          every channel &rarr; PMS
        </span>
      </h3>
      <div className="mt-3 rounded-xl border border-slate-100">
        <div className="flex flex-wrap items-center justify-between gap-4 px-4 py-3">
          <span className="min-w-0">
            <span className="block text-sm font-semibold text-slate-800">
              Bookings, changes &amp; cancellations
            </span>
            <span className="mt-0.5 block text-xs text-slate-500">
              {d?.webhook_registered
                ? 'The channel manager is pointed back at this property, so a '
                  + 'booking from any live channel arrives in Reservations on '
                  + 'its own, with the room resolved from your mapping.'
                : 'Set up when the property is created at the channel manager '
                  + 'so bookings can be delivered back.'}
            </span>
          </span>
          <span className="flex shrink-0 items-center gap-1.5 rounded-full bg-emerald-50 px-3 py-1 text-xs font-semibold text-emerald-700">
            <LockIcon size={12} /> Always on
          </span>
        </div>
      </div>

      <h3 className="mt-6 text-sm font-semibold text-slate-700">Sync alerts</h3>
      <label className="mt-3 flex cursor-pointer items-start gap-3 rounded-xl border border-slate-100 px-4 py-3">
        <input type="checkbox" checked={notify} disabled={saving}
          onChange={(e) => change({ n: e.target.checked })}
          className="mt-0.5 size-4 shrink-0 accent-brand" />
        <span className="min-w-0">
          <span className="block text-sm font-semibold text-slate-800">
            Tell property admins when synchronisation fails
          </span>
          <span className="mt-0.5 block text-xs text-slate-500">
            A failed push is otherwise invisible: the OTA simply keeps selling
            the last prices it heard, and the first symptom is a booking at
            last season&rsquo;s rate.
          </span>
        </span>
      </label>

      {/* The one thing somebody switching this off is most likely to get
          wrong, said where they are switching it off. */}
      {(!avail || !rates || !limits) && (
        <p className="mt-4 flex items-start gap-2 rounded-xl bg-amber-50 px-4 py-3 text-sm text-caution">
          <AlertTriangle size={16} className="mt-0.5 shrink-0" />
          <span>
            <b className="font-semibold">This does not close your rooms.</b>{' '}
            Whatever the channel manager already holds is what the OTAs keep
            selling, at the price they last heard. Stopping the feed freezes
            it; it does not withdraw it. To come off sale, switch the channel
            off at the channel manager.
          </span>
        </p>
      )}

      <h3 className="mt-6 text-sm font-semibold text-slate-700">
        What happened last time
      </h3>
      <dl className="mt-3 divide-y divide-slate-100 rounded-xl border border-slate-100 px-4">
        <Row k="Last sent"
          v={d?.last_pushed_at ? fmtDateTime(d.last_pushed_at) : 'Never'} />
        <Row k="Result" v={d?.last_push_status === 'ok' ? 'Everything sent'
          : d?.last_push_status === 'partial' ? 'Sent with problems'
            : d?.last_push_status === 'failed' ? 'Failed' : '—'} />
      </dl>
      {d?.last_push_detail && d.last_push_status !== 'ok' && (
        <p className="mt-2 text-xs text-slate-500">{d.last_push_detail}</p>
      )}
    </div>
  )
}

/** A labelled switch. Saves as it is flipped — a preference with its own Save
 *  button is a preference people leave unsaved. */
function Toggle({ on, onChange, title, body, disabled }: {
  on: boolean; onChange: (v: boolean) => void
  title: string; body: string; disabled?: boolean
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-4 px-4 py-3">
      <span className="min-w-0 flex-1">
        <span className="block text-sm font-semibold text-slate-800">{title}</span>
        <span className="mt-0.5 block text-xs text-slate-500">{body}</span>
      </span>
      <button type="button" role="switch" aria-checked={on} aria-label={title}
        disabled={disabled}
        onClick={() => onChange(!on)}
        className={`flex shrink-0 items-center gap-2 disabled:opacity-50`}>
        <span className={`relative h-6 w-11 rounded-full transition-colors ${
          on ? 'bg-brand' : 'bg-slate-300'}`}>
          <span className={`absolute top-0.5 size-5 rounded-full bg-white shadow transition-all ${
            on ? 'left-[22px]' : 'left-0.5'}`} />
        </span>
        <span className={`w-8 text-xs font-bold ${
          on ? 'text-brand' : 'text-slate-400'}`}>
          {on ? 'ON' : 'OFF'}
        </span>
      </button>
    </div>
  )
}
