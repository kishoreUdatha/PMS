import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  Monitor, Users, Search, Check, AlertTriangle, Loader2, ArrowRight,
  ChevronDown, Bed, Tag, RefreshCw,
  ArrowLeft, Info, Link2,
} from 'lucide-react'
import Select from '../components/Select'
import PartnerMark from '../components/PartnerMark'
import {
  listChannelPartners, createBookingAttribute, updateBookingAttribute,
  setChannelPartnerType, listProperties, listRoomTypes, listRatePlans,
  createChannelConnection, listChannelConnections, setChannelMappings, getMappingEditor,
  provisionChannelLink, getChannelLink, testOtaHotelId,
  type ChannelLink, type ConnectionTest,
} from '../api'
import { Crumbs } from '../components/Crumbs'
import { errorText } from '../lib/forms'

/**
 * Add a partner — the booking channel or agent a property sells through.
 *
 * Three steps, as the mockup draws them, but Setup collects the half that is
 * real. The mockup's middle step authorises with the channel and then maps
 * rooms; there is no channel manager integrated, so nothing can be authorised.
 * The mapping, though, is worth having before anything exists to read it: a
 * channel knows room 458721001, not "Deluxe Sea View", and somebody has to sit
 * with the extranet open and write those pairs out. No integration can derive
 * them, every integration needs them, and the work survives whichever channel
 * manager is eventually chosen.
 *
 * So Setup records the channel's own identifiers — their hotel id, the room
 * codes, the commercial terms — and says plainly that recording them connects
 * nothing. That is the mockup's own note from its sidebar, given the
 * prominence it deserves rather than small print.
 *
 * Nothing is written until the last step. A wizard that creates the partner on
 * step one leaves an orphan behind every time somebody backs out of step two.
 */

/** Channels worth offering by name, rather than making somebody type them.
 *
 *  A catalogue, not a set of integrations. Picking one records who the
 *  booking came through; it does not connect to anybody. The list is the
 *  Indian and international OTAs a resort of this kind actually sells on.
 */
/**
 * Letters and digits only, lower-cased — the same reduction the backend
 * applies in ``_auto_code``, which is what makes "Booking.com" and the stored
 * code BOOKINGCOM the same thing.
 *
 * Matching on this rather than on a code is deliberate. The codes in the
 * catalogue below were invented here (BDC, MMT) and the ones in the database
 * are derived from the name, so they disagreed — which is why a genuinely
 * connected Booking.com showed as available while an unconnected Expedia
 * showed as added.
 */
const norm = (s: string) => s.replace(/[^a-z0-9]/gi, '').toLowerCase()

const CATALOGUE: { code: string; name: string }[] = [
  { code: 'BDC', name: 'Booking.com' },
  { code: 'AGODA', name: 'Agoda' },
  { code: 'EXPEDIA', name: 'Expedia' },
  { code: 'MMT', name: 'MakeMyTrip' },
  { code: 'TRIP', name: 'Trip.com' },
  { code: 'AIRBNB', name: 'Airbnb' },
  { code: 'GOIBIBO', name: 'Goibibo' },
  { code: 'CLEARTRIP', name: 'Cleartrip' },
  { code: 'YATRA', name: 'Yatra' },
  { code: 'GOOGLE', name: 'Google Hotels' },
]

type PartnerType = 'online_channel' | 'travel_agent'

export default function AddPartner() {
  const nav = useNavigate()
  const [step, setStep] = useState<1 | 2 | 3>(1)
  const [type, setType] = useState<PartnerType>('online_channel')
  const [channel, setChannel] = useState('')
  const [agentName, setAgentName] = useState('')
  const [agentCode, setAgentCode] = useState('')
  const [q, setQ] = useState('')
  // Setup. Only the commercial terms, and held here rather than written as we
  // go: nothing is created until the final step, so backing out leaves
  // nothing behind.
  //
  // The channel's own hotel id and room codes used to be collected here too,
  // and must not be. What this system stores is the *channel manager's*
  // property and room ids -- an Agoda booking and a Booking.com booking both
  // arrive carrying those, which is the whole reason the mapping lives on the
  // property's link rather than on one OTA's connection. Typing Agoda's
  // 96019126 into that field would have written a non-routable id into the
  // key arriving bookings are matched on, and left provisioning trying to
  // create rooms under a channel-manager property that does not exist.
  //
  // Agoda's own id belongs in the channel manager, on the channel it is
  // configured against. Provisioning fills in everything this side needs.
  const [commission, setCommission] = useState('')
  const [paymentModel, setPaymentModel] = useState('')
  // The OTA's own id for this hotel. The one identifier a tenant genuinely
  // has to supply -- it comes with their contract with that OTA and nothing
  // can derive it. Distinct from the channel manager's property id, which is
  // created for them and routes arriving bookings.
  const [otaHotelId, setOtaHotelId] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  // What the OTA said about the id typed in. Cleared whenever it changes, so
  // a verdict can never belong to a different number than the one on screen.
  const [test, setTest] = useState<ConnectionTest | null>(null)
  const [testing, setTesting] = useState(false)

  const props = useQuery({ queryKey: ['properties'], queryFn: listProperties })
  const property = props.data?.[0]
  const organizationId = property?.organization_id ?? ''

  const existing = useQuery({
    queryKey: ['channel-partners', organizationId, '', ''],
    queryFn: () => listChannelPartners(organizationId),
    enabled: organizationId !== '',
  })

  const ratePlans = useQuery({
    queryKey: ['rate-plans', property?.id],
    queryFn: () => listRatePlans(property!.id),
    enabled: Boolean(property?.id),
  })

  // What the channel manager already knows about this property — its id
  // there, and which rooms are paired. Read only: this is written by
  // provisioning, and the wizard reports it rather than asking for it.
  const link = useQuery({
    queryKey: ['channel-link', property?.id],
    queryFn: () => getChannelLink(property!.id),
    enabled: Boolean(property?.id),
  })

  // What the channel manager already has for this property, which is what a
  // dropdown here can offer. Only available once the property is linked --
  // before that there are no counterparts yet, because provisioning creates
  // them when the channel is added.
  const editor = useQuery({
    queryKey: ['mapping-editor', link.data?.id],
    queryFn: () => getMappingEditor(link.data!.id),
    enabled: Boolean(link.data?.id),
    retry: false,
  })

  // Our id -> theirs. Seeded once from what is already paired and then owned
  // by the form, so a background refetch cannot pull a half-made change out
  // from under somebody.
  const [map, setMap] = useState<Record<string, string>>({})
  const [seeded, setSeeded] = useState(false)
  useEffect(() => {
    if (seeded || !link.data) return
    setSeeded(true)
    const seed: Record<string, string> = {}
    for (const r of [...link.data.rooms, ...link.data.rates]) {
      if (r.external_id) seed[r.local_id] = r.external_id
    }
    setMap(seed)
  }, [seeded, link.data])

  const roomTypes = useQuery({
    queryKey: ['room-types', property?.id],
    queryFn: () => listRoomTypes(property!.id),
    enabled: Boolean(property?.id),
  })

  // What this property actually sells through. Not the same question as what
  // is in the organisation's partner registry, which is what this used to
  // ask — and the difference is visible: the registry lists Expedia as a
  // business source because somebody once recorded a booking against it, with
  // no commission, no payment model and no connection. Reading that as
  // "already added" both told the tenant a lie and disabled the radio, so the
  // one channel they could not connect was the one they had never connected.
  const connections = useQuery({
    queryKey: ['channel-connections', property?.id],
    queryFn: () => listChannelConnections(property!.id),
    enabled: Boolean(property?.id),
  })

  const already = useMemo(
    () => new Set((connections.data ?? []).map((c) => norm(c.partner_name))),
    [connections.data],
  )

  // The registry, for reuse rather than for the badge. A channel already in
  // it must not be added a second time: two rows named Booking.com split that
  // channel's bookings and revenue in half on every report that groups by
  // source, and nothing on the screen would say why the numbers dropped.
  const registry = useMemo(
    () => new Map((existing.data?.rows ?? []).map(
      (r) => [norm(r.name), r] as const)),
    [existing.data],
  )

  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase()
    return needle
      ? CATALOGUE.filter((c) => c.name.toLowerCase().includes(needle))
      : CATALOGUE
  }, [q])


  const picked = CATALOGUE.find((c) => c.code === channel)
  const ready = type === 'online_channel'
    ? Boolean(picked) && !already.has(norm(picked!.name))
    : agentName.trim().length >= 2

  // An online channel cannot leave Setup without the OTA's own id for this
  // hotel. Without it the partner is a record: no channel is built, nothing
  // is sold, and the tenant is left waiting for bookings that were never
  // coming. The server refuses it too -- this only means finding out here
  // rather than after pressing Add.
  // Present, and not something the OTA has explicitly disowned.
  //
  // Only `rejected` blocks. "Could not be checked" is a different answer and
  // must not be treated as a wrong one -- Agoda has no test at all on this
  // deployment, and refusing on that basis would make Agoda unconnectable.
  const setupReady = type !== 'online_channel'
    || (otaHotelId.trim().length > 0 && test?.verdict !== 'rejected')

  async function runTest() {
    const made = registry.get(norm(picked?.name ?? ''))
    if (!picked || !otaHotelId.trim()) return
    setTesting(true); setTest(null)
    try {
      // Needs a partner row to know which channel to ask. One exists for a
      // channel already in the registry; otherwise the check waits until the
      // partner is created, and provisioning reports the same answer.
      if (!made) {
        setTest({ verdict: 'unverified',
          message: `${picked.name} is not in your partner list yet, so this `
            + 'id is checked when you add it.' })
        return
      }
      setTest(await testOtaHotelId(made.id, otaHotelId.trim()))
    } catch {
      setTest({ verdict: 'unverified',
        message: 'The check could not be run just now.' })
    } finally { setTesting(false) }
  }

  async function add() {
    setErr(''); setBusy(true)
    try {
      const name = type === 'online_channel'
        ? picked!.name : agentName.trim()
      // Reuse before create. The channel may already be in the registry from
      // a booking recorded against it long before anybody connected it.
      const found = registry.get(norm(name))
      const made = found ?? await createBookingAttribute({
        organization_id: organizationId,
        kind: 'business_source',
        name,
        // Left to the backend for a catalogue channel, which derives it from
        // the name exactly as it does everywhere else. Sending a code of our
        // own is what put BDC and BOOKINGCOM in the same table.
        code: type === 'online_channel' ? null : (agentCode.trim() || null),
        status: 'active',
      })
      // Connecting a channel puts it back in use. A registry entry retired
      // last season would otherwise stay retired while quietly taking
      // bookings, and it is the reports that would look wrong, not this page.
      if (found && found.status !== 'active') {
        await updateBookingAttribute(found.id, {
          organization_id: organizationId, kind: 'business_source',
          name: found.name, code: found.code, status: 'active',
        })
      }
      await setChannelPartnerType(made.id, type)

      // Two records, because they have different lifetimes. The commercial
      // terms belong to this OTA; the channel manager's property id and the
      // room mappings belong to the *property* and are shared with every
      // other OTA it sells through. Writing them as one row is what stopped a
      // hotel connecting its second channel.
      if (type === 'online_channel' && property) {
        await createChannelConnection({
          partner_id: made.id,
          property_id: property.id,
          commission_percent: commission.trim() ? Number(commission) : null,
          payment_model: paymentModel || null,
          ota_hotel_id: otaHotelId.trim() || null,
        })
        // Set the property up at the channel manager now rather than waiting
        // for the sweep. The connection alone is enough to make it happen
        // within the quarter hour, but somebody who has just pressed Add is
        // entitled to see the answer rather than a page that says nothing.
        //
        // Best effort: the partner and its terms are saved either way, and a
        // channel manager that is slow this afternoon must not read as "your
        // partner was not added". The sweep retries, and the Sales Channels
        // screen has a button.
        try {
          await provisionChannelLink(property.id)
          // Provisioning writes the pairs it worked out; anything the
          // operator changed on this screen is applied over the top, because
          // theirs is the deliberate one. Read the link back first so the
          // ids exist to write against.
          const fresh = await getChannelLink(property.id)
          if (fresh?.id) {
            const pick = (rows: { local_id: string }[]) => rows
              .filter((r) => map[r.local_id])
              .map((r) => ({ local_id: r.local_id, external_id: map[r.local_id] }))
            const rooms = pick(fresh.rooms)
            const rates = pick(fresh.rates)
            if (rooms.length || rates.length) {
              await setChannelMappings(fresh.id, { rooms, rates })
            }
          }
        } catch {
          // Left to the sweep, deliberately silent.
        }
      }
      nav('/channels')
    } catch (e) {
      setErr(errorText(e, 'Could not add that partner.'))
      setBusy(false)
    }
  }

  return (
    <div className="space-y-5">
      <div>
        <Crumbs trail={[{ label: 'Channel Partners', to: '/channels' }]} />
        <h1 className="mt-1 text-display text-ink">
          {step === 2 && picked ? `Set up ${picked.name}` : 'Add partner'}
        </h1>
        <p className="mt-1 text-slate-500">
          {step === 2
            ? 'Record the channel’s own identifiers and match rooms and rate plans.'
            : 'Choose how this partner will send bookings to your property.'}
        </p>
      </div>

      <Steps step={step} type={type} />

      {err && (
        <p className="flex items-start gap-2 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700">
          <AlertTriangle size={16} className="mt-0.5 shrink-0" /> {err}
        </p>
      )}

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="rounded-2xl border border-slate-100 bg-white p-6">
          {step === 1 ? (
            <>
              <h2 className="mb-3 text-section text-ink">
                Partner type
              </h2>
              <div className="grid gap-3 sm:grid-cols-2">
                <TypeCard
                  selected={type === 'online_channel'}
                  onSelect={() => setType('online_channel')}
                  icon={<Monitor size={20} />}
                  title="Online booking channel"
                  body="An OTA that sends bookings — Booking.com, Agoda, MakeMyTrip."
                />
                <TypeCard
                  selected={type === 'travel_agent'}
                  onSelect={() => setType('travel_agent')}
                  icon={<Users size={20} />}
                  title="Travel agent"
                  body="An agent who books by phone or email, on agreed commission."
                />
              </div>

              {type === 'online_channel' ? (
                <>
                  <h2 className="mb-3 mt-6 text-section text-ink">
                    Select a channel
                  </h2>
                  <label className="relative mb-3 block">
                    <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                    <input value={q} onChange={(e) => setQ(e.target.value)}
                      placeholder="Search booking channels"
                      className="w-full rounded-lg border border-slate-200 py-2.5 pl-9 pr-9 text-sm outline-none focus:border-brand" />
                    <ChevronDown size={16}
                      className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-slate-400" />
                  </label>
                  <div className="grid gap-2 sm:grid-cols-2">
                    {shown.map((c) => {
                      const added = already.has(norm(c.name))
                      return (
                        <div key={c.code}
                          className={`flex items-center gap-3 rounded-xl border px-3 py-3 transition-colors ${
                            added
                              ? 'border-slate-100 bg-slate-50'
                              : channel === c.code
                                ? 'border-brand bg-brand-light/40'
                                : 'border-slate-200 hover:border-brand'}`}>
                          <button disabled={added} role="radio"
                            aria-checked={channel === c.code}
                            aria-label={c.name}
                            onClick={() => setChannel(c.code)}
                            className="flex min-w-0 flex-1 items-center gap-3 text-left disabled:cursor-default">
                            <Radio selected={channel === c.code} />
                            <PartnerMark name={c.name} />
                            <span className="min-w-0 flex-1 text-sm font-semibold text-slate-800">
                              {c.name}
                            </span>
                          </button>
                          {added && (
                            <span className="shrink-0 text-right">
                              <span className="block rounded-full bg-emerald-100 px-2 py-0.5 text-xs font-semibold text-emerald-700">
                                Terms recorded
                              </span>
                              {/* Deliberately not "Already added". This
                                  property has a row recording what this OTA
                                  charges; that row talks to nobody, and
                                  calling it "added" is what let a hotel
                                  believe it was live on a channel that had
                                  never been configured anywhere. */}
                              <Link to="/channels"
                                className="mt-1 block text-xs font-medium text-brand hover:underline">
                                Manage existing
                              </Link>
                            </span>
                          )}
                        </div>
                      )
                    })}
                    {shown.length === 0 && (
                      <p className="col-span-full py-6 text-center text-sm text-slate-500">
                        No channel by that name. Travel agents and smaller
                        channels can be added as a travel agent above.
                      </p>
                    )}
                  </div>
                  <p className="mt-3 text-xs text-slate-500">
                    Existing partners can be managed from Channel Partners.
                  </p>
                </>
              ) : (
                <>
                  <h2 className="mb-3 mt-6 text-section text-ink">
                    Agent details
                  </h2>
                  <label className="block">
                    <span className="mb-1 block text-sm font-medium text-slate-600">
                      Agent name
                    </span>
                    <input value={agentName} maxLength={120}
                      onChange={(e) => setAgentName(e.target.value)}
                      placeholder="Coastal Travels"
                      className="w-full rounded-lg border border-slate-200 px-3 py-2.5 text-sm outline-none focus:border-brand" />
                  </label>
                  <label className="mt-3 block">
                    <span className="mb-1 block text-sm font-medium text-slate-600">
                      Code <span className="font-normal text-slate-400">(optional)</span>
                    </span>
                    <input value={agentCode} maxLength={30}
                      onChange={(e) => setAgentCode(e.target.value.toUpperCase())}
                      placeholder="Derived from the name if left blank"
                      className="w-full rounded-lg border border-slate-200 px-3 py-2.5 text-sm outline-none focus:border-brand" />
                    <span className="mt-1 block text-xs text-slate-400">
                      What reports call this agent. It cannot be changed once
                      bookings carry it.
                    </span>
                  </label>
                </>
              )}
            </>
          ) : step === 2 ? (
            <Setup
              candidateRooms={editor.data?.candidate_rooms ?? []}
              candidateRates={editor.data?.candidate_rates ?? []}
              map={map} setMap={setMap}
              test={test} testing={testing}
              onTest={() => void runTest()} clearTest={() => setTest(null)}
              onRefresh={link.data?.id ? () => void editor.refetch() : undefined}
              refreshing={editor.isFetching}
              channel={picked?.name ?? ''}
              property={property}
              link={link.data} linkLoading={link.isLoading}
              commission={commission} setCommission={setCommission}
              paymentModel={paymentModel} setPaymentModel={setPaymentModel}
              otaHotelId={otaHotelId} setOtaHotelId={setOtaHotelId}
              roomTypes={roomTypes.data ?? []}
              ratePlans={ratePlans.data?.items ?? []}
              loading={roomTypes.isLoading || ratePlans.isLoading}
            />
          ) : (
            <Review type={type} name={picked?.name ?? agentName.trim()}
              code={picked?.code ?? (agentCode.trim() || '(derived)')}
              property={property?.name ?? ''}
              externalId={link.data?.external_property_id ?? ''}
              otaHotelId={otaHotelId.trim()}
              mapped={link.data?.rooms_mapped ?? 0}
              roomTotal={(roomTypes.data ?? []).length}
              ratesMapped={link.data?.rates_mapped ?? 0}
              rateTotal={(ratePlans.data?.items ?? []).length}
              linked={Boolean(link.data?.external_property_id)} />
          )}
        </div>

        {step === 2 ? (
          <SetupSummary
            channel={picked?.name ?? ''}
            property={property}
            roomsMapped={link.data?.rooms_mapped ?? 0}
            roomsTotal={(roomTypes.data ?? []).length}
            ratesMapped={link.data?.rates_mapped ?? 0}
            ratesTotal={(ratePlans.data?.items ?? []).length}
          />
        ) : (
        <Overview
          property={property?.name ?? '—'}
          selection={type === 'online_channel'
            ? (picked?.name ?? 'None selected')
            : (agentName.trim() || 'Not named yet')}
          type={type}
          linked={Boolean(link.data?.external_property_id)}
        />
        )}
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3">
        {step === 1 ? (
          <Link to="/channels"
            className="rounded-xl border border-slate-200 px-4 py-2.5 text-sm font-medium text-slate-600 hover:bg-slate-50">
            Cancel
          </Link>
        ) : (
          <button
            onClick={() => setStep(step === 3 && type === 'online_channel' ? 2 : 1)}
            className="flex items-center gap-2 rounded-xl border border-slate-200 px-4 py-2.5 text-sm font-medium text-slate-600 hover:bg-slate-50">
            <ArrowLeft size={16} /> Back
          </button>
        )}
        {step < 3 ? (
          <button disabled={step === 2 ? !setupReady : !ready}
            title={step === 2 && !setupReady
              ? `Enter ${picked?.name ?? 'the channel'}'s property ID first`
              : undefined}
            onClick={() => setStep(step === 1 && type === 'online_channel' ? 2 : 3)}
            className="flex items-center gap-2 rounded-xl bg-brand px-5 py-2.5 text-sm font-semibold text-white enabled:hover:bg-brand/90 disabled:opacity-50">
            {step === 1 && type === 'online_channel' ? 'Continue to setup' : 'Continue to review'}
            <ArrowRight size={16} />
          </button>
        ) : (
          <button disabled={busy} onClick={() => void add()}
            className="flex items-center gap-2 rounded-xl bg-brand px-5 py-2.5 text-sm font-semibold text-white enabled:hover:bg-brand/90 disabled:opacity-50">
            {busy ? <Loader2 size={16} className="animate-spin" /> : <Check size={16} />}
            Add partner
          </button>
        )}
      </div>
    </div>
  )
}

function Steps({ step, type }: { step: 1 | 2 | 3; type: PartnerType }) {
  // A travel agent has nothing to set up — no hotel id, no room codes — so
  // the middle step is not shown rather than shown empty.
  const items: [number, string][] = type === 'online_channel'
    ? [[1, 'Partner details'], [2, 'Setup'], [3, 'Review']]
    : [[1, 'Partner details'], [3, 'Review']]
  return (
    <div className="flex flex-wrap items-center gap-3">
      {items.map(([n, label], i) => (
        <div key={n} className="flex items-center gap-3">
          <span className={`grid size-7 place-items-center rounded-full text-xs font-bold ${
            step >= n ? 'bg-brand text-white' : 'bg-slate-200 text-slate-600'}`}>
            {n}
          </span>
          <span className={`text-sm font-semibold ${
            step >= n ? 'text-slate-800' : 'text-slate-500'}`}>{label}</span>
          {i < items.length - 1 && <span className="h-px w-12 bg-slate-200" />}
        </div>
      ))}
    </div>
  )
}

function TypeCard({ selected, onSelect, icon, title, body }: {
  selected: boolean; onSelect: () => void
  icon: React.ReactNode; title: string; body: string
}) {
  return (
    <button onClick={onSelect} role="radio" aria-checked={selected}
      className={`flex items-start gap-3 rounded-xl border p-4 text-left transition-colors ${
        selected ? 'border-brand bg-brand-light/40' : 'border-slate-200 hover:border-brand'}`}>
      <Radio selected={selected} />
      <span className={`grid size-10 shrink-0 place-items-center rounded-lg ${
        selected ? 'bg-brand-light text-brand' : 'bg-slate-75 text-slate-600'}`}>
        {icon}
      </span>
      <span className="min-w-0">
        <span className="block text-sm font-semibold text-slate-800">{title}</span>
        <span className="mt-0.5 block text-xs text-slate-500">{body}</span>
      </span>
    </button>
  )
}

/** The control the mockup draws, rather than a tick that appears after the
 *  fact: a radio says "one of these" before anything is chosen. */
function Radio({ selected }: { selected: boolean }) {
  return (
    <span className={`mt-0.5 grid size-5 shrink-0 place-items-center rounded-full border-2 ${
      selected ? 'border-brand' : 'border-slate-300'}`}>
      {selected && <span className="size-2.5 rounded-full bg-brand" />}
    </span>
  )
}

function Review({ type, name, code, property, externalId, otaHotelId,
  mapped, roomTotal,
                  ratesMapped, rateTotal, linked }: {
  type: PartnerType; name: string; code: string; property: string
  externalId: string; otaHotelId: string; mapped: number; roomTotal: number
  ratesMapped: number; rateTotal: number
  //: Whether this property has a channel manager property id. Everything the
  //: page promises depends on it, so it is passed rather than assumed.
  linked: boolean
}) {
  return (
    <>
      <h2 className="text-section text-ink">Review</h2>
      <p className="mt-1 text-sm text-slate-500">
        Check this before it goes on the books.
      </p>
      <dl className="mt-4 divide-y divide-slate-100 border-y border-slate-100">
        <Row k="Partner" v={name} />
        <Row k="Code" v={code} />
        <Row k="Type" v={type === 'online_channel' ? 'Online channel' : 'Travel agent'} />
        <Row k="Property" v={property} />
        {type === 'online_channel' && (
          <>
            <Row k="Channel manager ID"
              v={externalId || 'Created when you add this'} />
            <Row k="Their property ID"
              v={otaHotelId || 'Not given — channel not built'} />
            <Row k="Rooms mapped" v={`${mapped} of ${roomTotal}`} />
            <Row k="Rate plans mapped" v={`${ratesMapped} of ${rateTotal}`} />
          </>
        )}
      </dl>
      {/* What pressing Add actually does. This note has been wrong twice, in
          both directions: it first said "this connects nothing" for ever,
          then told operators to go and type an id the system now creates
          itself. Both times it described a version of the product that no
          longer existed, which is worse than saying nothing. It is written
          from the two facts that decide the outcome -- whether the property
          is at the channel manager already, and whether we have this OTA's
          own id for it. */}
      {type === 'travel_agent' ? (
        <p className="mt-4 flex items-start gap-2 rounded-xl bg-slate-50 px-4 py-3 text-sm text-slate-600">
          <Info size={16} className="mt-0.5 shrink-0" />
          <span>
            An agent is a record, not a connection. Bookings entered against
            it are attributed to it, and its revenue is reported on Channel
            Partners.
          </span>
        </p>
      ) : otaHotelId ? (
        <p className="mt-4 flex items-start gap-2 rounded-xl bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
          <Check size={16} className="mt-0.5 shrink-0" />
          <span>
            <b>This builds the connection.</b>{' '}
            {linked
              ? `Adding it creates the ${name} channel from property ID `
              : 'Adding it creates this property at the channel manager, '
                + 'mirrors every room type and rate plan, and builds the '
                + `${name} channel from property ID `}
            {otaHotelId}. One step is left afterwards and it is not ours:{' '}
            {name} has to authorise the channel manager in their own extranet,
            and then the channel is switched on.
          </span>
        </p>
      ) : (
        <p className="mt-4 flex items-start gap-2 rounded-xl bg-amber-50 px-4 py-3 text-sm text-caution">
          <Info size={16} className="mt-0.5 shrink-0" />
          <span>
            <b>No channel is built yet.</b> {name} needs their own property ID
            for this hotel &mdash; it comes with your contract with them, and
            nothing can derive it. Without it this partner is a record:
            bookings entered by hand are attributed to it and its revenue is
            reported, but nothing is sold through {name}. Add the ID here or
            later, and the channel is built for you.
          </span>
        </p>
      )}
    </>
  )
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex items-center justify-between gap-4 py-3">
      <dt className="text-sm text-slate-500">{k}</dt>
      <dd className="text-sm font-semibold text-slate-800">{v}</dd>
    </div>
  )
}

function Overview({ property, selection, type, linked }: {
  property: string; selection: string; type: PartnerType; linked: boolean
}) {
  return (
    <aside className="h-fit rounded-2xl border border-slate-100 bg-white p-5">
      <h2 className="text-section text-ink">
        Connection overview
      </h2>
      <dl className="mt-3 space-y-2.5 text-sm">
        <div className="flex items-center justify-between gap-3">
          <dt className="text-slate-500">Property</dt>
          <dd className="font-semibold text-slate-800">{property}</dd>
        </div>
        <div className="flex items-center justify-between gap-3">
          <dt className="text-slate-500">
            {type === 'online_channel' ? 'Selected channel' : 'Agent'}
          </dt>
          <dd className="font-semibold text-slate-800">{selection}</dd>
        </div>
        <div className="flex items-center justify-between gap-3">
          <dt className="text-slate-500">Status</dt>
          <dd>
            <span className={`rounded-full px-2.5 py-0.5 text-xs font-semibold ${
              linked ? 'bg-emerald-100 text-emerald-700'
                : 'bg-slate-200 text-slate-600'}`}>
              {linked ? 'Connected' : 'Not connected'}
            </span>
          </dd>
        </div>
      </dl>

      <h3 className="mt-5 text-sm font-semibold text-ink">
        What happens next
      </h3>
      <ol className="mt-2 space-y-3">
        {linked ? (
          <>
            <Next n={1} title="Rates and availability go out"
              body="Pushed to the channel manager on a schedule, so the OTA sells what you actually have." />
            <Next n={2} title="Bookings arrive on their own"
              body="They appear in Reservations, with the room resolved from your mapping." />
            <Next n={3} title="Revenue is reported"
              body="Channel Partners shows what each partner has actually brought in." />
          </>
        ) : type === 'travel_agent' ? (
          <>
            <Next n={1} title="The agent goes on the books"
              body="It becomes selectable when a booking is taken, so business can be attributed to it." />
            <Next n={2} title="Bookings are attributed"
              body="Entered by your team from the agent's email or phone call." />
            <Next n={3} title="Revenue is reported"
              body="Channel Partners shows what each partner has actually brought in." />
          </>
        ) : (
          <>
            <Next n={1} title="The property is set up for you"
              body="Created at the channel manager with every room type and rate plan mirrored and paired. No ids to copy." />
            <Next n={2} title="The channel is built"
              body="From the OTA's own property ID, if you have it. Without it the partner is still recorded, and the channel is built whenever you add the ID." />
            <Next n={3} title="You switch it on"
              body="After the OTA authorises the channel manager in their extranet. That step is between you and them." />
          </>
        )}
      </ol>

      <p className="mt-4 flex items-start gap-2 rounded-xl bg-slate-50 px-3 py-2.5 text-xs text-slate-600">
        <Link2 size={14} className="mt-0.5 shrink-0" />
        {type === 'travel_agent'
          ? 'An agent is recorded here; nothing is sent anywhere.'
          : linked
            ? 'Sync is per property and shared by every channel it sells through.'
            : 'The channel manager’s property id is created for you. The only id you supply is the OTA’s own.'}
      </p>
    </aside>
  )
}

function Next({ n, title, body }: { n: number; title: string; body: string }) {
  return (
    <li className="flex gap-3">
      <span className="grid size-6 shrink-0 place-items-center rounded-full bg-brand-light text-xs font-bold text-brand">
        {n}
      </span>
      <span className="min-w-0">
        <span className="block text-sm font-semibold text-slate-800">{title}</span>
        <span className="mt-0.5 block text-xs text-slate-500">{body}</span>
      </span>
    </li>
  )
}


/**
 * The step the mockup calls Setup.
 *
 * It does not authorise anything — there is no account to authorise against,
 * so the mockup's "Authorized" badge and its "Refresh channel data" link are
 * absent rather than decorative. What it collects is the part no integration
 * can work out for itself: the channel's own identifiers, and what they call
 * each of our rooms and rate plans.
 *
 * Two departures from the drawing, both forced by what is actually knowable:
 *
 * * **Text fields, not dropdowns.** A dropdown of the channel's room names
 *   means we fetched their catalogue. We cannot. An empty dropdown would be a
 *   dead end; a box you type their code into is the real control.
 * * **Rooms and rate plans as two lists, not a tree.** The mockup nests rate
 *   plans under each room type. In this PMS a rate plan belongs to the
 *   property, not to a room type — ``property.rate_plans`` has no room column —
 *   so drawing that hierarchy would invent a relationship the data does not
 *   have, and somebody would eventually map against it.
 *
 * Everything is optional. This is done with the channel's extranet open in
 * another window, and a form that refuses to save until every box is filled is
 * a form people abandon halfway and never come back to.
 */
function Setup({
  candidateRooms, candidateRates, map, setMap, onRefresh, refreshing,
  test, testing, onTest, clearTest,
  channel, property, link, linkLoading, commission, setCommission,
  paymentModel, setPaymentModel, otaHotelId, setOtaHotelId,
  roomTypes, ratePlans, loading,
}: {
  candidateRooms: { id: string; title: string }[]
  candidateRates: { id: string; title: string; room_type_id: string | null }[]
  map: Record<string, string>
  setMap: (v: Record<string, string>) => void
  onRefresh?: () => void
  refreshing?: boolean
  test: ConnectionTest | null
  testing: boolean
  onTest: () => void
  clearTest: () => void
  channel: string
  property: { name: string; currency: string; timezone: string } | undefined
  link: ChannelLink | null | undefined
  linkLoading: boolean
  commission: string; setCommission: (v: string) => void
  paymentModel: string; setPaymentModel: (v: string) => void
  otaHotelId: string; setOtaHotelId: (v: string) => void
  roomTypes: { id: string; code: string; name: string }[]
  ratePlans: { id: string; code: string; name: string; room_type_ids: string[] }[]
  loading: boolean
}) {
  const input = 'w-full rounded-lg border border-slate-200 px-3 py-2.5 text-sm outline-none focus:border-brand'
  const connected = Boolean(link?.external_property_id)
  const ch = channel || 'the channel'

  return (
    <>
      <h2 className="text-section text-ink">
        Property connection
      </h2>

      <p className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
        <span className="text-slate-500">{property?.name ?? '—'}</span>
        <span className="text-slate-300">&rarr;</span>
        {linkLoading ? (
          <Loader2 size={14} className="animate-spin text-slate-400" />
        ) : connected ? (
          <>
            <span className="flex items-center gap-1 font-semibold text-positive">
              <Check size={14} className="shrink-0" /> created at the channel
              manager
            </span>
            <span className="truncate font-mono text-xs text-slate-400"
              title={link!.external_property_id ?? ''}>
              {link!.external_property_id}
            </span>
          </>
        ) : (
          <span className="font-medium text-slate-500">
            created when you add this channel
          </span>
        )}
      </p>

      {/* Three fields, one row.
          The OTA's id is stored against this one OTA on this one property --
          never on the link, where an earlier version of this field put it and
          made every arriving booking for the property unroutable. */}
      <div className="mt-3 grid gap-x-4 gap-y-3 sm:grid-cols-2 lg:grid-cols-[1.2fr_0.7fr_1fr]">
        <label className="block">
          <span className="mb-1 block truncate text-sm font-medium text-slate-600">
            {ch} property ID
            <span className="text-red-500" aria-hidden="true"> *</span>
          </span>
          <span className="flex gap-2">
            <input value={otaHotelId} maxLength={80} required
              aria-required="true"
              onChange={(e) => { setOtaHotelId(e.target.value); clearTest() }}
              placeholder="e.g. 96019126"
              className={`${input} font-mono ${
                test?.verdict === 'rejected' ? 'border-red-300 bg-red-50/40'
                  : test?.verdict === 'ok' ? 'border-emerald-300'
                    : otaHotelId.trim() ? '' : 'border-amber-300 bg-amber-50/40'}`} />
            <button type="button" onClick={onTest}
              disabled={testing || !otaHotelId.trim()}
              className="shrink-0 rounded-lg border border-slate-200 px-3 text-sm font-semibold text-slate-600 hover:border-brand hover:text-brand disabled:opacity-50">
              {testing ? <Loader2 size={15} className="animate-spin" /> : 'Check'}
            </button>
          </span>
        </label>
        <label className="block">
          <span className="mb-1 block text-sm font-medium text-slate-600">
            Commission %
          </span>
          <input value={commission} type="number" min={0} max={100} step="0.01"
            onChange={(e) => setCommission(e.target.value)}
            placeholder="15" className={input} />
        </label>
        <label className="block">
          <span className="mb-1 block text-sm font-medium text-slate-600">
            Payment model
          </span>
          <Select value={paymentModel} className={input}
            onChange={(e) => setPaymentModel(e.target.value)}>
            <option value="">Not set</option>
            <option value="hotel_collect">Hotel collect</option>
            <option value="channel_collect">Channel collect</option>
            <option value="virtual_card">Virtual card</option>
          </Select>
        </label>
      </div>
      {test ? (
        <p className={`mt-2 flex items-start gap-2 rounded-xl px-3 py-2.5 text-xs ${
          test.verdict === 'ok' ? 'bg-emerald-50 text-emerald-800'
            : test.verdict === 'rejected' ? 'bg-red-50 text-red-700'
              : 'bg-slate-50 text-slate-600'}`}>
          {test.verdict === 'ok' ? <Check size={14} className="mt-0.5 shrink-0" />
            : test.verdict === 'rejected'
              ? <AlertTriangle size={14} className="mt-0.5 shrink-0" />
              : <Info size={14} className="mt-0.5 shrink-0" />}
          {test.message}
        </p>
      ) : otaHotelId.trim() ? (
        <p className="mt-2 text-xs text-slate-400">
          The {ch} ID is theirs, from your contract. Commission is per channel;
          rooms and rates below are shared by every channel this property
          sells through.
        </p>
      ) : (
        // Stated as the blocker it is, where the blocking happens. The old
        // copy said "leave it blank and the terms above are still saved",
        // which was true and was the problem: a tenant could add a channel
        // that could never sell anything and be told it had worked.
        <p className="mt-2 flex items-start gap-2 rounded-xl bg-amber-50 px-3 py-2.5 text-xs text-caution">
          <AlertTriangle size={14} className="mt-0.5 shrink-0" />
          <span>
            <b className="font-semibold">Required.</b> {ch}&rsquo;s own id for
            this hotel comes with your contract with them. Without it no
            channel can be built and nothing is sold through {ch}.
          </span>
        </p>
      )}

      <div className="mt-6 flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-section text-ink">Room &amp; rate mapping</h2>
        {/* Re-reads what the channel manager holds. Worth having because the
            other half of this pairing is not ours: somebody adds a room type
            there, and until this is pressed the dropdown below cannot offer
            it. */}
        {onRefresh && (
          <button type="button" onClick={onRefresh} disabled={refreshing}
            className="flex items-center gap-1.5 text-sm font-semibold text-brand hover:underline disabled:opacity-50">
            <RefreshCw size={14}
              className={refreshing ? 'animate-spin' : ''} />
            Refresh channel data
          </button>
        )}
      </div>
      <p className="mt-1 text-sm text-slate-500">
        Match each PMS room and rate plan to its channel equivalent. Written
        for you when you add the channel.
      </p>

      {loading || linkLoading ? (
        <p className="py-8 text-center text-slate-400">
          <Loader2 className="mx-auto animate-spin" />
        </p>
      ) : (
        <MapTree roomTypes={roomTypes} ratePlans={ratePlans}
          rooms={link?.rooms ?? []} rates={link?.rates ?? []}
          candidateRooms={candidateRooms} candidateRates={candidateRates}
          map={map} setMap={setMap}
          connected={connected} channel={ch} />
      )}
    </>
  )
}

/**
 * Rate plans nested under the room they price, one table.
 *
 * The nesting is not decoration; it is what makes this fit. Printed as two
 * flat tables -- every room in one, every rate plan in another, each with its
 * own heading and header row -- the same information took five hundred pixels
 * and pushed the step's own buttons off the screen. Nested, a room and the
 * plan that prices it are one line and its child, and the second heading and
 * second header row disappear.
 *
 * It is also what the data actually is. A channel rate plan belongs to
 * exactly one room, which is the rule provisioning enforces when it creates
 * them, so a plan that spans several rooms has no single price to send. Those
 * cannot be nested under any one room and cannot be sold, so they are counted
 * at the foot rather than listed -- visible, explained, and not costing four
 * rows to say one thing.
 *
 * The middle column reports rather than offers a dropdown. There is nothing
 * to choose from at this point: the counterparts do not exist until the
 * channel is added, and provisioning writes every pair when it does. Changing
 * one afterwards is what the partner's own page is for.
 */
function MapTree({ roomTypes, ratePlans, rooms, rates, candidateRooms,
  candidateRates, map, setMap, connected, channel }: {
  roomTypes: { id: string; code: string; name: string }[]
  ratePlans: { id: string; code: string; name: string; room_type_ids: string[] }[]
  rooms: { local_id: string; external_id: string; external_name: string | null }[]
  rates: { local_id: string; external_id: string; external_name: string | null }[]
  candidateRooms: { id: string; title: string }[]
  candidateRates: { id: string; title: string; room_type_id: string | null }[]
  map: Record<string, string>
  setMap: (v: Record<string, string>) => void
  connected: boolean
  channel: string
}) {
  const roomMap = new Map(rooms.map((r) => [r.local_id, r]))
  const rateMap = new Map(rates.map((r) => [r.local_id, r]))
  const set = (localId: string, externalId: string) =>
    setMap({ ...map, [localId]: externalId })

  // Open by default: a mapping table exists to be read across, and opening
  // each room one at a time to see where you are defeats that. Collapsing is
  // for a property with thirty of them.
  const [shut, setShut] = useState<Record<string, boolean>>({})

  /**
   * What this row is paired with right now — one answer, from one place.
   *
   * Presence of the key decides, not truthiness of the value. Choosing "Not
   * mapped" stores an empty string, and an empty string is falsy: read as
   * `map[id] || saved`, the form's deliberate blank lost to the stale value
   * still on the server and the row went on claiming it was mapped. Read as
   * "has the form touched this row", a blank is an answer like any other.
   *
   * Before the form is seeded no key exists yet, so this falls back to what
   * the server says — which is also what stops every row flashing "Not
   * mapped" for one render while the link loads.
   */
  const pairedWith = (localId: string, saved?: string) =>
    localId in map ? map[localId] : (saved ?? '')

  // One room, one plan. Anything covering several is set aside below.
  const sellable = ratePlans.filter((p) => p.room_type_ids.length === 1)
  const spanning = ratePlans.filter((p) => p.room_type_ids.length !== 1)
  const under = (roomId: string) =>
    sellable.filter((p) => p.room_type_ids[0] === roomId)

  if (roomTypes.length === 0) {
    return (
      <p className="mt-3 rounded-xl bg-amber-50 px-4 py-3 text-sm text-caution">
        This property has no active room types yet, so there is nothing to map.
      </p>
    )
  }

  const cell = 'grid grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)_104px] items-center gap-3 px-4'

  // Every room, and every plan that can be sold, paired with something. The
  // plans that span several rooms are excluded on purpose -- they cannot be
  // mapped at all, so counting them would mean this never reads complete.
  const allMapped = roomTypes.length > 0 && roomTypes.every((r) =>
    pairedWith(r.id, roomMap.get(r.id)?.external_id)
    && under(r.id).every((p) =>
      pairedWith(p.id, rateMap.get(p.id)?.external_id)))

  return (
    <div className="mt-3 overflow-hidden rounded-xl border border-slate-100">
      <div className={`${cell} border-b border-slate-100 bg-slate-50 py-2.5 text-xs font-semibold uppercase tracking-wide text-slate-500`}>
        <span>PMS room / rate plan</span>
        <span>Channel manager</span>
        <span>Status</span>
      </div>

      {roomTypes.map((room) => {
        const m = roomMap.get(room.id)
        return (
          <div key={room.id} className="border-b border-slate-50 last:border-0">
            <div className={`${cell} py-2.5`}>
              <span className="flex min-w-0 items-center gap-2">
                {under(room.id).length > 0 ? (
                  <button type="button"
                    onClick={() => setShut({ ...shut, [room.id]: !shut[room.id] })}
                    aria-expanded={!shut[room.id]}
                    aria-label={`${room.name} rate plans`}
                    className="shrink-0 rounded p-0.5 text-slate-400 hover:bg-slate-100 hover:text-slate-600">
                    <ChevronDown size={15} className={`transition-transform ${
                      shut[room.id] ? '-rotate-90' : ''}`} />
                  </button>
                ) : <span className="w-[22px] shrink-0" />}
                <Bed size={15} className="shrink-0 text-slate-400" />
                <span className="truncate font-semibold text-slate-800">
                  {room.name}
                </span>
                <span className="shrink-0 font-mono text-xs text-slate-400">
                  {room.code}
                </span>
              </span>
              <Pair localId={room.id} value={pairedWith(room.id, m?.external_id)}
                set={set} options={candidateRooms}
                fallback={m?.external_name} connected={connected} />
              <StatusPill mapped={Boolean(pairedWith(room.id, m?.external_id))}
                connected={connected} />
            </div>

            {!shut[room.id] && under(room.id).map((plan, i, arr) => {
              const pm = rateMap.get(plan.id)
              return (
                <div key={plan.id} className={`${cell} py-2`}>
                  <span className="flex min-w-0 items-center gap-2 pl-3">
                    <span className={`relative ml-[3px] -mt-2 h-[26px] w-3.5 shrink-0 rounded-bl border-b border-l border-slate-200`}>
                      {i !== arr.length - 1 && (
                        <span className="absolute -left-px top-[26px] h-3 w-px bg-slate-200" />
                      )}
                    </span>
                    <Tag size={14} className="shrink-0 text-slate-400" />
                    <span className="truncate text-sm text-slate-600">
                      {plan.name}
                    </span>
                  </span>
                  <Pair localId={plan.id}
                    value={pairedWith(plan.id, pm?.external_id)}
                    set={set} fallback={pm?.external_name}
                    connected={connected}
                    // Only the plans under the room already chosen. A list of
                    // every plan in the property is how a suite's price ends
                    // up on a standard double.
                    options={candidateRates.filter((c) => {
                      const r = pairedWith(room.id, m?.external_id)
                      return !r || c.room_type_id === r
                    })} />
                  <StatusPill mapped={Boolean(pairedWith(plan.id, pm?.external_id))}
                    connected={connected} />
                </div>
              )
            })}
          </div>
        )
      })}

      {allMapped && (
        <p className="flex items-center gap-2 border-t border-slate-100 bg-emerald-50 px-4 py-3 text-sm font-semibold text-emerald-800">
          <span className="grid size-5 shrink-0 place-items-center rounded-full bg-emerald-600">
            <Check size={13} strokeWidth={3.5} className="text-white" />
          </span>
          All mappings complete. Ready for review.
        </p>
      )}

      {spanning.length > 0 && (
        <p className="flex items-start gap-2 border-t border-slate-100 bg-amber-50/60 px-4 py-2.5 text-xs text-caution">
          <AlertTriangle size={14} className="mt-0.5 shrink-0" />
          <span>
            {spanning.length} rate plan{spanning.length === 1 ? '' : 's'}
            {' '}cover{spanning.length === 1 ? 's' : ''} more than one room
            type, so {channel} has no single price to sell. Split{' '}
            {spanning.length === 1 ? 'it' : 'them'} per room to use{' '}
            {spanning.length === 1 ? 'it' : 'them'} here.
          </span>
        </p>
      )}
    </div>
  )
}

/**
 * What the channel manager calls ours — picked, not typed.
 *
 * A dropdown of what actually exists at the channel manager rather than a box
 * to type an id into. The codes a hotel can read off an OTA's own extranet
 * are not the ids an arriving booking carries, and a free-text field invites
 * exactly that mistake — it is the bug that made every booking for one
 * property unroutable earlier in this project.
 *
 * Disabled until the property is linked, because there is genuinely nothing
 * to choose from: the counterparts do not exist until provisioning creates
 * them. It still renders as a select rather than switching to plain text, so
 * the row does not change shape the moment a channel is added.
 */
function Pair({ localId, value, set, options, fallback, connected }: {
  localId: string
  /** Already resolved by the caller, so the select and the status beside it
   *  cannot disagree about what this row is paired with. */
  value: string
  set: (localId: string, externalId: string) => void
  options: { id: string; title: string }[]
  fallback?: string | null
  connected: boolean
}) {
  if (!connected || options.length === 0) {
    return (
      <span className="truncate text-sm text-slate-400">
        {fallback || 'Created on adding'}
      </span>
    )
  }
  return (
    <Select value={value}
      onChange={(e) => set(localId, e.target.value)}
      className="w-full rounded-lg border border-slate-200 px-2.5 py-1.5 text-sm outline-none focus:border-brand">
      <option value="">Not mapped</option>
      {options.map((o) => (
        <option key={o.id} value={o.id}>{o.title}</option>
      ))}
    </Select>
  )
}

/** A filled green disc with a white tick, as the design draws it. Lucide's
 *  CheckCircle2 is an outline; at 13px an outlined tick on white reads as
 *  grey, and "mapped" is the one thing on this row somebody scans for. */
function StatusPill({ mapped, connected }: {
  mapped: boolean; connected: boolean
}) {
  if (mapped) {
    return (
      <span className="flex w-fit items-center gap-1.5 rounded-full bg-emerald-50 py-1 pl-1 pr-2.5 text-xs font-semibold text-emerald-700">
        <span className="grid size-4 shrink-0 place-items-center rounded-full bg-emerald-600">
          <Check size={11} strokeWidth={3.5} className="text-white" />
        </span>
        Mapped
      </span>
    )
  }
  return (
    <span className="w-fit rounded-full bg-slate-75 px-2.5 py-1 text-xs font-medium text-slate-500">
      {connected ? 'Not mapped' : 'On adding'}
    </span>
  )
}

function SetupSummary({ channel, property, roomsMapped, roomsTotal,
                        ratesMapped, ratesTotal }: {
  channel: string
  property: { name: string; currency: string; timezone: string } | undefined
  roomsMapped: number; roomsTotal: number
  ratesMapped: number; ratesTotal: number
}) {
  return (
    <aside className="h-fit rounded-2xl border border-slate-100 bg-white p-5">
      <h2 className="text-section text-ink">Setup summary</h2>
      <p className="mt-3 text-lg font-bold text-slate-800">{channel}</p>
      <dl className="mt-3 space-y-2.5 border-t border-slate-100 pt-3 text-sm">
        <SumRow k="Property" v={property?.name ?? '—'} />
        <SumRow k="Room types" v={`${roomsMapped} of ${roomsTotal} mapped`} />
        <SumRow k="Rate plans" v={`${ratesMapped} of ${ratesTotal} mapped`} />
        <SumRow k="Currency" v={property?.currency ?? '—'} />
        <SumRow k="Timezone" v={property?.timezone ?? '—'} />
      </dl>
      <div className="mt-3 flex items-center justify-between gap-3 border-t border-slate-100 pt-3">
        <span className="text-sm text-slate-500">Status</span>
        <span className="rounded-full bg-slate-200 px-2.5 py-0.5 text-xs font-semibold text-slate-600">
          Sync not active
        </span>
      </div>
      {/* The mockup promises rates will be sent after activation. Nothing
          sends anything, so this says what is true instead. */}
      <p className="mt-4 flex items-start gap-2 rounded-xl bg-slate-50 px-3 py-2.5 text-xs text-slate-600">
        <Info size={14} className="mt-0.5 shrink-0" />
        These identifiers are what the sync runs on: the property id says where
        to send, and the mapping says which of their rooms is which of yours.
        Rates and availability go out on a schedule once they are saved.
      </p>
    </aside>
  )
}

function SumRow({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <dt className="text-slate-500">{k}</dt>
      <dd className="text-right font-semibold text-slate-800">{v}</dd>
    </div>
  )
}
