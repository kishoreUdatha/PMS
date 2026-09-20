import { useQuery } from '@tanstack/react-query'
import { Globe, Loader2 } from 'lucide-react'
import SalesChannelsBoard from '../components/SalesChannelsBoard'
import { listProperties } from '../api'
import { Crumbs } from '../components/Crumbs'
import { useActivePropertyId } from '../hooks/useProperty'

/**
 * Sales channels, reached from Administration.
 *
 * The same card that sits on Property Settings, given its own page and its own
 * way in. Two routes to one control rather than two implementations of it: the
 * decision belongs to the property, so it belongs on the property's settings,
 * but people look for "is this hotel on sale online" under Administration
 * alongside users, roles and the payment gateway. A screen nobody can find is
 * the same as a screen that does not exist -- which is what this was when it
 * was an API call and nothing else.
 *
 * It resolves the property itself rather than taking one as a prop, because
 * here it is the whole page: there is no settings form above it to have
 * already loaded one.
 */
export default function SalesChannelsPage() {
  const activeId = useActivePropertyId()
  const { data, isLoading } = useQuery({
    queryKey: ['properties'],
    queryFn: listProperties,
  })

  // The property in the top-bar switcher. useActivePropertyId has already
  // checked the stored id against this session's own properties, falling back
  // to the first one when it was left behind by a previous sign-in, so
  // matching on it here cannot resolve to another tenant's property.
  const active = (data ?? []).find((p) => p.id === activeId) ?? data?.[0]

  return (
    <div className="space-y-5">
      <div>
        {/* Distribution, not Administration: the screen moved, and a trail
            that still said Administration was the reason for moving it. */}
        <Crumbs title="Sales Channels" trail={[
          { label: 'Distribution', to: '/channels' },
          { label: 'Sales Channels' }]} />
        <h1 className="mt-1 flex items-center gap-2 text-display text-ink">
          <Globe size={26} className="text-brand" /> Sales Channels
        </h1>
        <p className="text-slate-500">
          Manage direct bookings, OTA connections and channel synchronisation.
        </p>
      </div>

      {isLoading && (
        <p className="py-16 text-center text-slate-400">
          <Loader2 className="mx-auto animate-spin" />
        </p>
      )}

      {!isLoading && !active && (
        <p className="rounded-xl bg-amber-50 px-4 py-3 text-sm text-caution">
          No property is available on your account.
        </p>
      )}

      {active && (
        <SalesChannelsBoard propertyId={active.id} propertyCode={active.code} />
      )}
    </div>
  )
}
