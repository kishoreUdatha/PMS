import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Search as SearchIcon, Loader2 } from 'lucide-react'
import {
  searchGuests, listReservations, listRooms,
  type Guest, type ReservationRow, type RoomRow,
} from '../api'
import { useActivePropertyId, useOrgId } from '../hooks/useProperty'
import { Crumbs } from '../components/Crumbs'
import { errorText } from '../lib/forms'

/** What the top-bar search box actually does.
 *
 *  It offered "guests, reservations, rooms" and did nothing at all -- no
 *  handler, no state, an input nobody had wired up. The three reads it needed
 *  already existed and already took a query; only the box was missing.
 *
 *  Guests are scoped to the organisation and reservations and rooms to the
 *  property, which is the real shape of the data: a guest is a person the
 *  hotel group knows, a booking and a room belong to one property. Asked
 *  together, reported separately -- one of them failing is a fact about that
 *  section, not a failed search.
 */

const MIN = 2
const CAP = 50

type Result<T> = { rows: T[]; error: string } | null

function errText(e: unknown, fallback: string): string {
  return errorText(e, fallback)
}

function Section({ title, count, head, children }: {
  title: string; count: number; head: string[]; children: React.ReactNode
}) {
  return (
    <div className="overflow-hidden rounded-2xl border border-slate-100 bg-white shadow-sm">
      <div className="flex items-center justify-between px-5 py-4">
        <h2 className="text-base font-semibold text-ink">{title}</h2>
        <span className="text-sm text-slate-400">
          {count} {count === 1 ? 'result' : 'results'}
        </span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-100 bg-slate-50/60 text-left text-slate-600">
              {head.map((h, i) => (
                <th key={h || i} className="px-4 py-3 font-semibold first:pl-5">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-50">{children}</tbody>
        </table>
      </div>
    </div>
  )
}

const cell = 'px-4 py-3 first:pl-5'

export default function Search() {
  const [params, setParams] = useSearchParams()
  const q = (params.get('q') ?? '').trim()
  const orgId = useOrgId()
  const propertyId = useActivePropertyId()

  const [draft, setDraft] = useState(q)
  const [busy, setBusy] = useState(false)
  const [guests, setGuests] = useState<Result<Guest>>(null)
  const [stays, setStays] = useState<Result<ReservationRow>>(null)
  const [rooms, setRooms] = useState<Result<RoomRow>>(null)

  useEffect(() => { setDraft(q) }, [q])

  useEffect(() => {
    if (q.length < MIN) {
      setGuests(null); setStays(null); setRooms(null)
      return
    }
    // Both ids are empty while the property list loads. Asking with "" is
    // asking for somebody else's nothing, so wait rather than search wrongly.
    if (!orgId || !propertyId) return

    let live = true
    setBusy(true)
    Promise.allSettled([
      searchGuests(orgId, q),
      listReservations(propertyId, { query: q }),
      listRooms(propertyId, { search: q, limit: CAP }),
    ]).then(([g, r, m]) => {
      if (!live) return
      setGuests(g.status === 'fulfilled'
        ? { rows: g.value, error: '' }
        : { rows: [], error: errText(g.reason, 'Guests could not be searched.') })
      setStays(r.status === 'fulfilled'
        ? { rows: r.value, error: '' }
        : { rows: [], error: errText(r.reason, 'Reservations could not be searched.') })
      setRooms(m.status === 'fulfilled'
        ? { rows: m.value.items, error: '' }
        : { rows: [], error: errText(m.reason, 'Rooms could not be searched.') })
      setBusy(false)
    })
    return () => { live = false }
  }, [q, orgId, propertyId])

  const total = [guests, stays, rooms]
    .reduce((n, r) => n + (r?.rows.length ?? 0), 0)
  const errors = [guests?.error, stays?.error, rooms?.error].filter(Boolean)

  return (
    <div className="space-y-4">
      <Crumbs trail={[{ label: 'Search' }]} />
      {/* Title and the search box share one line; the box is this screen's
          only control, so it sits where other screens keep their search. */}
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <h1 className="flex min-w-0 items-center gap-2 text-display text-ink">
          <SearchIcon size={26} className="shrink-0 text-brand" />
          {q ? `Results for “${q}”` : 'Search'}
        </h1>
        <form
          role="search"
          onSubmit={(e) => {
            e.preventDefault()
            const next = draft.trim()
            if (next.length >= MIN) setParams({ q: next })
          }}
          className="flex min-w-[240px] flex-1 items-center justify-end gap-2"
        >
          <div className="relative min-w-0 max-w-sm flex-1">
            <SearchIcon size={16}
              className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <input
              id="search-q"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              aria-label="Search guests, reservations and rooms"
              placeholder="Guest name, reservation number or room code"
              className="w-full rounded-lg border border-slate-200 bg-slate-50 py-2 pl-9 pr-3 text-sm outline-none focus:border-brand"
            />
          </div>
          <button type="submit"
            className="flex items-center gap-1.5 whitespace-nowrap rounded-lg bg-brand px-3 py-2 text-sm font-medium text-white hover:bg-brand-dark disabled:opacity-40">
            Search
          </button>
        </form>
      </div>

      {errors.map((e) => (
        <div key={e} className="rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700">{e}</div>
      ))}

      {q.length < MIN && (
        <p className="text-sm text-slate-500">
          Type at least {MIN} characters. Guests match on name, email or
          phone; reservations on number or guest name; rooms on their code or
          room type.
        </p>
      )}

      {q.length >= MIN && busy && (
        <div className="flex items-center gap-2 py-10 text-sm text-slate-400">
          <Loader2 size={16} className="animate-spin" /> Searching…
        </div>
      )}

      {q.length >= MIN && !busy && total === 0 && errors.length === 0 && (
        <p className="text-sm text-slate-500">
          Nothing in this property matches “{q}”. Reservations and rooms are
          scoped to the property you are working in; guests are shared across
          the group.
        </p>
      )}

      {!busy && (guests?.rows.length ?? 0) > 0 && (
        <Section title="Guests" count={guests!.rows.length}
          head={['Guest', 'Email', 'Phone', '']}>
          {guests!.rows.map((g) => (
            <tr key={g.id} className="hover:bg-slate-50/60">
              <td className={`${cell} font-medium text-slate-800`}>{g.full_name}</td>
              <td className={`${cell} text-slate-500`}>{g.email || '—'}</td>
              <td className={`${cell} text-slate-500`}>{g.phone || '—'}</td>
              <td className={`${cell} text-right`}>
                <Link to={`/guests/${g.id}`}
                  className="font-medium text-brand hover:underline">Open</Link>
              </td>
            </tr>
          ))}
        </Section>
      )}

      {!busy && (stays?.rows.length ?? 0) > 0 && (
        <Section title="Reservations" count={stays!.rows.length}
          head={['Number', 'Guest', 'Room type', 'Arrival', 'Status', '']}>
          {stays!.rows.map((r) => (
            <tr key={r.id} className="hover:bg-slate-50/60">
              <td className={`${cell} font-medium text-slate-800`}>{r.number}</td>
              <td className={`${cell} text-slate-600`}>
                {r.guest_name ?? <span className="italic text-slate-400">No guest on file</span>}
              </td>
              <td className={`${cell} text-slate-500`}>{r.room_type}</td>
              <td className={`${cell} text-slate-500`}>{r.arrival_date ?? '—'}</td>
              <td className={`${cell}`}>
                <span className="rounded-full bg-slate-75 px-2.5 py-1 text-xs font-medium capitalize text-slate-600">
                  {r.status}
                </span>
              </td>
              <td className={`${cell} text-right`}>
                <Link to={`/reservations/${r.id}`}
                  className="font-medium text-brand hover:underline">Open</Link>
              </td>
            </tr>
          ))}
        </Section>
      )}

      {!busy && (rooms?.rows.length ?? 0) > 0 && (
        <Section title="Rooms" count={rooms!.rows.length}
          head={['Room', 'Type', 'Building', 'Floor', 'Status', '']}>
          {rooms!.rows.map((r) => (
            <tr key={r.id} className="hover:bg-slate-50/60">
              <td className={`${cell} font-medium text-slate-800`}>{r.code}</td>
              <td className={`${cell} text-slate-500`}>{r.room_type_name}</td>
              <td className={`${cell} text-slate-500`}>{r.building || '—'}</td>
              <td className={`${cell} text-slate-500`}>{r.floor || '—'}</td>
              <td className={`${cell}`}>
                <span className="rounded-full bg-slate-75 px-2.5 py-1 text-xs font-medium capitalize text-slate-600">
                  {r.service_status || r.status}
                </span>
              </td>
              <td className={`${cell} text-right`}>
                <Link to={`/rooms/${r.id}`}
                  className="font-medium text-brand hover:underline">Open</Link>
              </td>
            </tr>
          ))}
        </Section>
      )}

      {!busy && (rooms?.rows.length ?? 0) === CAP && (
        <p className="text-sm text-slate-500">
          Showing the first {CAP} rooms. Narrow the search to see the rest.
        </p>
      )}
    </div>
  )
}
