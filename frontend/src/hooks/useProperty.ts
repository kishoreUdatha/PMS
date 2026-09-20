import { useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { listProperties, type PropertyRow } from '../api'
import { setDisplayZone } from '../lib/dates'

/** The property the user is currently working in.
 *
 * The name of the resort is tenant data, not a constant. Several screens used
 * to print "Chirala Bay Resort" as a literal, which is invisible on a
 * single-property install and wrong the moment a second one exists — including
 * on the payment receipt and the booking confirmation, which guests keep.
 *
 * While the list is loading there is no name to show, so callers get an empty
 * string rather than a placeholder that would flash a wrong name first.
 */
export function useProperty(): { property?: PropertyRow; name: string } {
  const id = localStorage.getItem('property_id') ?? ''
  const { data } = useQuery({ queryKey: ['properties'], queryFn: listProperties })
  const property = data?.find((p) => p.id === id) ?? data?.[0]
  // Every timestamp in the UI is read in the property's own timezone, and
  // this is where that gets configured. Set here rather than once at startup
  // because switching property has to move the clocks with it: the same
  // payment listed under two properties is one instant, and it should read as
  // each property's own wall clock.
  useEffect(() => { setDisplayZone(property?.timezone) }, [property?.timezone])
  return { property, name: property?.name ?? '' }
}

export function usePropertyName(): string {
  return useProperty().name
}

/** The organisation the current property belongs to.
 *
 * Derived from the property rather than kept in `localStorage`, because the
 * property is the thing the user actually chooses; an organisation id stored
 * alongside it is a second copy that can disagree after a property switch.
 *
 * Empty while the property list is loading, so callers must gate their query
 * on it -- asking the API for "" returns somebody else's nothing.
 */
export function useOrgId(): string {
  return useProperty().property?.organization_id ?? ''
}

/** The id of the property a report runs against.
 *
 * Falls back to the first property on a browser that has never chosen one,
 * and remembers it, so a report screen opened from a bookmark does not sit
 * waiting on a choice nobody has been asked to make. Empty while loading.
 */
export function useActivePropertyId(): string {
  const stored = localStorage.getItem('property_id') ?? ''
  // Always ask, even when something is stored. Trusting the stored value
  // unread is what let one tenant's property id survive into another
  // tenant's session: every request carrying it came back 403 "Property
  // outside caller tenant", which looks like broken permissions rather than
  // a stale browser. The list is this session's own properties, so a value
  // missing from it is not this caller's to use.
  const { data } = useQuery({ queryKey: ['properties'], queryFn: listProperties })
  const mine = data?.some((p) => p.id === stored) ?? false
  const resolved = (mine ? stored : data?.[0]?.id) || ''
  if (resolved && resolved !== stored) localStorage.setItem('property_id', resolved)
  return resolved
}

/**
 * Today **where the property is**, as `YYYY-MM-DD`.
 *
 * Not `new Date().toISOString().slice(0, 10)`, which is the browser's clock
 * converted to UTC. India is UTC+5:30, so from 18:30 local until midnight that
 * expression returns YESTERDAY -- and every screen defaulting to it quietly
 * showed the wrong day for five and a half hours out of every twenty-four.
 *
 * It is not the browser's local date either. A manager in London looking at a
 * resort in Chirala means the resort's today, not their own; the property
 * carries its own timezone and that is the one that decides.
 *
 * Falls back to Asia/Kolkata while the property list is still loading, which
 * matches the server's own fallback rather than inventing a third answer.
 */
export function usePropertyToday(): string {
  const { property } = useProperty()
  const tz = property?.timezone || 'Asia/Kolkata'
  try {
    // en-CA gives YYYY-MM-DD, which is the format every date input and query
    // parameter here expects.
    return new Intl.DateTimeFormat('en-CA', { timeZone: tz }).format(new Date())
  } catch {
    return new Intl.DateTimeFormat('en-CA').format(new Date())
  }
}
