import { useMemo, useState } from 'react'
import { fmtDate } from '../lib/dates'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  Link2, AlertTriangle, Loader2, Search, Plus, Info, ArrowRight, Globe,
} from 'lucide-react'
import Select from '../components/Select'
import { FILTER_SELECT } from '../lib/controls'
import PartnerMark from '../components/PartnerMark'
import { useActivePropertyId } from '../hooks/useProperty'
import {
  listChannelPartners, setChannelPartnerType, listProperties,
  getChannelLink, getOtaStatus,
  type ChannelPartner,
} from '../api'
import { errorText } from '../lib/forms'

/**
 * Screen 024 — the partners this organisation sells through.
 *
 * The columns report what a channel has actually done, not what it claims to
 * be. **Bookings** is the real measure of a channel, and **Last booking** is
 * the honest version of the mockup's "last sync" — it is what a manager is
 * really checking when they look at a sync time. A partner that is active and
 * has never produced a booking is flagged in its own row, which is where it is
 * useful: the mockup put that count in a summary strip, and a number you
 * cannot click tells you something is wrong without telling you which one.
 *
 * Whether the property actually syncs is a separate question from whether a
 * partner exists, and the panel at the foot answers it from the channel
 * manager link rather than asserting it. It used to say sync was unavailable
 * unconditionally, which was true when it was written and the opposite of the
 * truth a week later — a screen that lies in that direction is as bad as one
 * that lies in the other.
 *
 * Partners sit on `engagement.booking_attributes`, the same registry the
 * booking screens classify against, so this list and that dropdown cannot
 * drift apart.
 */

type Tab = 'all' | 'online_channel' | 'travel_agent'

export default function ChannelPartners() {
  const [tab, setTab] = useState<Tab>('all')
  const [q, setQ] = useState('')
  const [status, setStatus] = useState('')
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')

  const activePropertyId = useActivePropertyId()
  const props = useQuery({ queryKey: ['properties'], queryFn: listProperties })
  // The property in the top-bar switcher, not simply the first one on the
  // account. The OTA panel below is per-property -- its link, its mapping,
  // its sync -- so on a tenant with more than one property this showed the
  // first property's state under whichever property the user had chosen.
  // Partners themselves are organisation-wide, so that stays as it was.
  const active = props.data?.find((x) => x.id === activePropertyId)
    ?? props.data?.[0]
  const organizationId = active?.organization_id ?? ''
  const propertyId = active?.id ?? ''

  // Whether this property actually sells through an OTA, asked of the channel
  // manager rather than inferred.
  //
  // The panel below has now been wrong in both directions. It began by
  // asserting nothing ever synced, which stopped being true the day the
  // integration landed. It was then made conditional on the *channel manager
  // link* -- and read "bookings from any connected OTA arrive on their own"
  // while no OTA channel existed anywhere. Being linked to a channel manager
  // and being on sale are different facts, and only the second one is what
  // anybody reads this panel to learn.
  const channelLink = useQuery({
    queryKey: ['channel-link', propertyId],
    queryFn: () => getChannelLink(propertyId),
    enabled: propertyId !== '',
    retry: false,
  })
  const ota = useQuery({
    queryKey: ['ota-status', propertyId],
    queryFn: () => getOtaStatus(propertyId),
    enabled: propertyId !== '',
    retry: false,
  })
  const linked = Boolean(channelLink.data?.external_property_id)
  const liveChannels = (ota.data?.rows ?? []).filter(
    (r) => r.state === 'live').length
  // Three states, not two: set up and selling, set up and selling nothing,
  // and not set up. The middle one is the common one and used to be reported
  // as the first.
  const sync: 'live' | 'ready' | 'none' =
    liveChannels > 0 ? 'live' : linked ? 'ready' : 'none'

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['channel-partners', organizationId, status, q],
    queryFn: () => listChannelPartners(organizationId, {
      status: status || undefined, q: q || undefined,
    }),
    enabled: organizationId !== '',
    retry: false,
  })

  // Filtered here rather than refetched: the tabs are a view of one small list
  // and a round trip per tab would make them feel slower than they are.
  const rows = useMemo(() => {
    const all = data?.rows ?? []
    return tab === 'all' ? all : all.filter((r) => r.partner_type === tab)
  }, [data, tab])

  async function classify(p: ChannelPartner, value: string) {
    setErr(''); setBusy(p.id)
    try {
      await setChannelPartnerType(p.id, value || null)
      await refetch()
    } catch (e) {
      setErr(errorText(e, 'Could not update that partner.'))
    } finally { setBusy('') }
  }

  if (isLoading || props.isLoading) {
    return <p className="py-20 text-center text-slate-400">
      <Loader2 className="mx-auto animate-spin" />
    </p>
  }

  if (error) {
    const status_ = (error as { response?: { status?: number } })?.response?.status
    return (
      <div className="max-w-3xl space-y-4">
        <Header />
        <p className="flex items-start gap-2 rounded-xl bg-amber-50 px-4 py-3 text-sm text-caution">
          <AlertTriangle size={16} className="mt-0.5 shrink-0" />
          {status_ === 403
            ? 'Your role does not permit viewing channel partners. This needs '
              + 'the distribution permission, which sits with management.'
            : 'Channel partners could not be loaded.'}
        </p>
      </div>
    )
  }

  const s = data!.summary
  const counts: Record<Tab, number> = {
    all: s.partners, online_channel: s.online_channels,
    travel_agent: s.travel_agents,
  }

  return (
    <div className="space-y-4">
      <Header>
        {data!.can_configure && (
          <Link to="/channels/add"
            className="flex items-center gap-1.5 whitespace-nowrap rounded-lg bg-brand px-3 py-2 text-sm font-medium text-white hover:bg-brand-dark disabled:opacity-40">
            <Plus size={15} /> Add partner
          </Link>
        )}
      </Header>

      {err && (
        <p className="flex items-start gap-2 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700">
          <AlertTriangle size={16} className="mt-0.5 shrink-0" /> {err}
        </p>
      )}

      <div className="flex flex-wrap items-center gap-1 border-b border-slate-200">
        {([['all', 'All partners'], ['online_channel', 'Online channels'],
           ['travel_agent', 'Travel agents']] as [Tab, string][]).map(([k, label]) => (
          <button key={k} onClick={() => setTab(k)}
            className={`-mb-px flex items-center gap-2 border-b-2 px-4 py-2.5 text-sm font-semibold transition-colors ${
              tab === k
                ? 'border-brand text-brand'
                : 'border-transparent text-slate-500 hover:text-slate-700'}`}>
            {label}
            <span className={`rounded-md px-1.5 py-0.5 text-xs font-semibold ${
              tab === k ? 'bg-brand text-white' : 'bg-slate-75 text-slate-600'}`}>
              {counts[k]}
            </span>
          </button>
        ))}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Select blankIsChoice value={status} onChange={(e) => setStatus(e.target.value)}
          aria-label="Status"
          className={`${FILTER_SELECT} bg-white outline-none focus:border-brand`}>
          <option value="">All statuses</option>
          <option value="active">Active</option>
          <option value="inactive">Inactive</option>
        </Select>
        <div className="flex min-w-[240px] flex-1 items-center justify-end gap-2">
          <div className="relative min-w-0 max-w-sm flex-1">
            <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <input value={q} onChange={(e) => setQ(e.target.value)}
              placeholder="Search partners…" aria-label="Search partners"
              className="w-full rounded-lg border border-slate-200 bg-slate-50 py-2 pl-9 pr-3 text-sm outline-none focus:border-brand" />
          </div>
        </div>
      </div>

      <div className="overflow-hidden rounded-2xl border border-slate-100 bg-white">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[720px] text-sm">
            <thead>
              <tr className="border-b border-slate-100 bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-600">
                <th className="px-4 py-3 font-semibold">Partner</th>
                <th className="px-4 py-3 font-semibold">Type</th>
                <th className="px-4 py-3 font-semibold">Bookings</th>
                <th className="px-4 py-3 font-semibold">Last booking</th>
                <th className="px-4 py-3 text-right font-semibold">Revenue</th>
                <th className="px-4 py-3 font-semibold">Status</th>
                <th className="px-4 py-3 text-right font-semibold">Action</th>
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 && (
                <tr>
                  <td colSpan={7} className="px-4 py-10 text-center text-slate-500">
                    {data!.summary.partners === 0
                      ? 'No partners yet. Add the OTAs and travel agents you '
                        + 'sell through, and bookings will be attributed to them.'
                      : 'No partners match those filters.'}
                  </td>
                </tr>
              )}
              {rows.map((p) => (
                <tr key={p.id} className="border-b border-slate-100 last:border-0">
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-3">
                      <PartnerMark name={p.name} />
                      <div className="min-w-0">
                        <div className="font-semibold text-slate-800">{p.name}</div>
                        <div className="text-xs text-slate-500">{p.code}</div>
                      </div>
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    {data!.can_configure ? (
                      <Select value={p.partner_type ?? ''}
                        onChange={(e) => void classify(p, e.target.value)}
                        className="rounded-lg border border-slate-200 px-2.5 py-1.5 text-sm outline-none focus:border-brand">
                        <option value="">Unclassified</option>
                        <option value="online_channel">Online channel</option>
                        <option value="travel_agent">Travel agent</option>
                        <option value="direct">Direct</option>
                      </Select>
                    ) : (
                      <span className="text-slate-600">
                        {p.partner_type_label ?? 'Unclassified'}
                      </span>
                    )}
                    {busy === p.id && (
                      <Loader2 size={13} className="ml-2 inline animate-spin text-slate-400" />
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <span className={p.bookings ? 'font-semibold text-slate-800'
                      : 'text-slate-500'}>{p.bookings}</span>
                  </td>
                  <td className="px-4 py-3 text-slate-600">
                    {p.last_booking_at ? relative(p.last_booking_at) : (
                      <span className="inline-flex items-center gap-1.5 text-amber-700">
                        <AlertTriangle size={14} /> never
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-right font-semibold text-slate-800">
                    {money(p.revenue)}
                  </td>
                  <td className="px-4 py-3">
                    <span className={`rounded-full px-2.5 py-1 text-xs font-semibold capitalize ${
                      p.status === 'active'
                        ? 'bg-emerald-100 text-emerald-700'
                        : 'bg-slate-75 text-slate-600'}`}>
                      {p.status}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-right">
                    {data!.can_configure && (
                      <Link to={`/channels/${p.id}/edit`}
                        className="inline-block rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-semibold text-slate-600 hover:border-brand hover:text-brand">
                        Edit
                      </Link>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <p className="text-sm text-slate-500">
        Showing {rows.length} of {data!.summary.partners} partner{data!.summary.partners === 1 ? '' : 's'}
      </p>

      {/* The mockup's "Connect a new booking channel" panel. Stated as it is
          rather than as a button that opens nothing — the same call the
          onboarding screen makes about a gateway it cannot connect. */}
      <div className="flex flex-wrap items-start gap-4 rounded-2xl border border-slate-100 bg-slate-50 px-5 py-4">
        <span className="grid size-11 shrink-0 place-items-center rounded-full bg-brand-light">
          <Link2 size={20} className="text-brand" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="flex items-center gap-2 font-semibold text-slate-800">
            Live channel connections
            <span className={`rounded-full px-2 py-0.5 text-xs font-semibold ${
              sync === 'live' ? 'bg-emerald-100 text-emerald-700'
                : sync === 'ready' ? 'bg-amber-100 text-caution'
                  : 'bg-slate-200 text-slate-600'}`}>
              {sync === 'live'
                ? `${liveChannels} live`
                : sync === 'ready' ? 'Nothing selling yet' : 'Not set up'}
            </span>
          </p>
          <p className="mt-1 text-sm text-slate-600">
            {sync === 'live'
              ? 'Rates and availability are pushed on a schedule, and bookings '
                + 'from a live channel arrive in Reservations on their own.'
              : sync === 'ready'
                ? 'This property is set up at the channel manager and its rates '
                  + 'are going out, but no OTA channel is switched on — so '
                  + 'nothing is being sold online yet. Add an OTA\u2019s property '
                  + 'ID to a partner above to build its channel.'
                : 'This property has no channel manager link yet, so nothing '
                  + 'syncs and OTA bookings have to be entered by hand. '
                  + 'Partners above still attribute those bookings, so the '
                  + 'revenue each channel brings is measured either way.'}
          </p>
          <p className="mt-2 flex items-start gap-2 text-xs text-slate-500">
            <Info size={14} className="mt-0.5 shrink-0" />
            Rates and availability are maintained in
            <Link to="/rates/calendar"
              className="inline-flex items-center gap-1 font-semibold text-brand hover:underline">
              Rates &amp; Inventory <ArrowRight size={12} />
            </Link>
          </p>
        </div>
      </div>


    </div>
  )
}

/** Title and the screen's actions on one line. */
function Header({ children }: { children?: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
      <h1 className="flex items-center gap-2 text-display text-ink">
        <Globe size={26} className="text-brand" /> Channel Partners
      </h1>
      {children && <div className="flex flex-wrap items-center gap-2">{children}</div>}
    </div>
  )
}


const money = (v: string) => new Intl.NumberFormat('en-IN', {
  style: 'currency', currency: 'INR', maximumFractionDigits: 0,
}).format(Number(v))

/** "3 days ago" reads faster than a date when the question is recency. */
function relative(iso: string): string {
  const then = new Date(iso).getTime()
  const mins = Math.max(0, Math.round((Date.now() - then) / 60000))
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins} min ago`
  const hours = Math.round(mins / 60)
  if (hours < 24) return `${hours} hour${hours === 1 ? '' : 's'} ago`
  const days = Math.round(hours / 24)
  if (days < 30) return `${days} day${days === 1 ? '' : 's'} ago`
  return fmtDate(iso)
}

