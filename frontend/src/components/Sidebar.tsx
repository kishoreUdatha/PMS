import { useEffect, useState } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import { ChevronDown, PanelLeftClose, PanelLeftOpen } from 'lucide-react'
import { navItems } from '../nav'
import { useQuery } from '@tanstack/react-query'
import { getReservationCounts, listOtaActions } from '../api'
import { usePropertyName, useActivePropertyId } from '../hooks/useProperty'
import { documentTitle } from '../lib/brand'

const STORAGE_KEY = 'sidebar_collapsed'

/**
 * Main navigation.
 *
 * Collapses to an icon rail to hand the width back to the content area. The
 * choice is remembered across sessions. Collapsed, a group cannot show its
 * children in place, so clicking one expands the sidebar and opens it rather
 * than doing nothing.
 */
/** Every path the sidebar links to, top level and nested. */
const ALL_PATHS = navItems.flatMap(
  (i) => [i.path, ...(i.children ?? []).map((c) => c.path)],
)

/**
 * Whether a link must match the URL exactly.
 *
 * NavLink is a prefix match by default, so a path that is the start of
 * another one lights up alongside it: on /reservations/list both "Stayview"
 * (/reservations) and "Bookings" (/reservations/list) showed as selected, and
 * "Room Inventory" (/rooms) did the same next to Today's Rack. Any path that
 * another link is nested under has to be exact.
 */
function exactly(path: string): boolean {
  return path === '/'
    || ALL_PATHS.some((p) => p !== path && p.startsWith(`${path}/`))
}

export default function Sidebar() {
  // Whose property this is. The header used to say so with a bitmap of one
  // particular hotel's logo, which was wrong for every other tenant.
  const propertyName = usePropertyName()
  // Two letters for the collapsed rail: the property's own, not a constant.
  const initials = (propertyName.match(/[A-Za-z]/g) ?? [])
    .slice(0, 2).join('').toUpperCase() || '··'

  // One poll for the whole shell. The counts endpoint is cheap and this is
  // the only place in the app that needs it continuously.
  // Through the hook, not straight from local storage. The sidebar renders on
  // every tenant page, so validating here means a property id left behind by
  // a previous session is corrected before any screen reads it -- rather than
  // each screen discovering separately that it is holding another tenant's id.
  const propertyId = useActivePropertyId()
  // The tab is labelled with this property, not with whichever hotel the
  // product was first built for. index.html ships a neutral title; this is
  // what makes it specific once we know whose session it is.
  useEffect(() => { document.title = documentTitle(propertyName) }, [propertyName])
  const { data: counts } = useQuery({
    queryKey: ['reservation-counts', propertyId],
    queryFn: () => getReservationCounts(propertyId),
    enabled: propertyId !== '',
    refetchInterval: 60_000,
  })
  const arrivals = counts?.arrivals ?? 0

  // What the hotel owes a channel, and how much of it is past its 24-hour
  // deadline. Counted here so the sidebar can say there is money leaking
  // without anyone opening the section to find out.
  const { data: ota } = useQuery({
    queryKey: ['ota-actions', propertyId, 'open'],
    queryFn: () => listOtaActions(propertyId, 'open'),
    enabled: propertyId !== '',
    refetchInterval: 60_000,
  })
  const otaOpen = ota?.open_count ?? 0
  const otaOverdue = ota?.overdue_count ?? 0

  const location = useLocation()
  const [collapsed, setCollapsed] = useState<boolean>(
    () => localStorage.getItem(STORAGE_KEY) === '1',
  )
  const [open, setOpen] = useState<string | null>(
    navItems.find((i) => i.children?.some((c) => location.pathname.startsWith(c.path)))
      ?.label ?? null,
  )

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, collapsed ? '1' : '0')
  }, [collapsed])

  // Keep the active group open as the route changes.
  useEffect(() => {
    const match = navItems.find((i) =>
      i.children?.some((c) => location.pathname.startsWith(c.path)),
    )
    if (match) setOpen(match.label)
  }, [location.pathname])

  // Every row is full white and semibold.
  //
  // Inactive rows used to be `text-white/75` and their children
  // `text-white/70`, which on the navy read as three greys and made the
  // menu look faded next to the content beside it. Selection is marked by
  // the band and the mint rule on the left -- it does not need the other
  // rows dimmed to be legible, and dimming them cost readability for a
  // distinction those two already draw clearly.
  const itemBase = 'relative flex items-center font-semibold transition-colors'
  const pad = collapsed
    ? 'justify-center gap-3 rounded-lg px-0 py-2 text-sm'
    // py-2, not py-3. At 12px each side a row stood 47px tall and thirteen
    // of them came to 645px in a 472px panel, so the menu scrolled on a
    // laptop -- the whole list could not be seen at once, which is most of
    // what a menu is for. The gap between rows was never the problem: it is
    // 2px. px-7 -> px-5 recovers width for the labels at the same time.
    : 'gap-3 px-5 py-2 text-[15px]'
  // The selected band, and the rule that marks it. Collapsed there is no band
  // to rule, so the rail keeps its pill.
  const activeRow = collapsed
    ? 'bg-brand text-white'
    : "bg-[#086169] text-white before:absolute before:inset-y-0 before:left-0 before:w-[5px] before:bg-[#78e0ca] before:content-['']"

  return (
    <aside
      className={`relative flex h-full shrink-0 flex-col bg-brand-sidebar text-white transition-[width] duration-200 ease-out ${
        collapsed ? 'w-[76px]' : 'w-64'
      }`}
    >
      {/* Header.
          Expanded: logo mark + wordmark + a collapse button.
          Collapsed: the mark IS the expand control — stacking a separate
          button under it made the rail top-heavy for no gain. It swaps to the
          expand icon on hover so the affordance is still discoverable. */}
      <div
        className={`group relative flex border-b border-white/10 ${
          collapsed ? 'h-14 items-center justify-center px-2'
                    : 'h-14 items-center gap-2 px-4'
        }`}
      >
        {collapsed ? (
          <button
            onClick={() => setCollapsed(false)}
            title="Expand menu"
            aria-label="Expand menu"
            className="group grid h-10 w-10 place-items-center rounded-lg bg-white/10 transition-colors hover:bg-white/20"
          >
            <span className="text-xs font-bold tracking-wide group-hover:hidden">
              {initials}
            </span>
            <PanelLeftOpen size={18} className="hidden group-hover:block" />
          </button>
        ) : (
          <>
            {/* The property's own name, not a logo file.
                This was <img src="/brand-logo.png" alt="Chirala Bay Resort">
                -- one hotel's artwork, shipped to every tenant and shown to
                all of them regardless of who they are. It also stood about
                110px tall for a decorative mark, at the top of a menu whose
                job is the list underneath it. */}
            <div className="min-w-0 flex-1 select-none">
              <div className="truncate text-sm font-semibold leading-tight"
                title={propertyName || undefined}>
                {propertyName || 'Loading…'}
              </div>
              <div className="text-[10px] uppercase tracking-[0.12em] text-white/50">
                Property management
              </div>
            </div>
            {/* The mockup shows the mark alone, so the collapse control keeps
                out of the way until the header is hovered or focused. */}
            <button
              onClick={() => setCollapsed(true)}
              title="Collapse menu"
              aria-label="Collapse menu"
              className="absolute right-1.5 top-1.5 grid h-8 w-8 place-items-center rounded-lg text-white/50 opacity-0 transition hover:bg-white/10 hover:text-white focus-visible:opacity-100 group-hover:opacity-100"
            >
              <PanelLeftClose size={17} />
            </button>
          </>
        )}
      </div>

      {/* Nav */}
      <nav className={`scroll-slim scroll-slim-dark flex-1 overflow-y-auto ${collapsed ? 'space-y-0.5 px-2 py-2' : 'space-y-0.5 px-0 py-2'}`}>
        {navItems.map(({ label, path, icon: Icon, children, badge }) => {
          if (children) {
            const isOpen = !collapsed && open === label
            // The same rule the links use, not a bare prefix match. A bare
            // one lit the wrong group: Property Setup owns /rooms, so
            // standing on /rooms/rack -- a top-level entry of its own --
            // highlighted a group the user was not in.
            const anyActive = children.some((c) =>
              exactly(c.path)
                ? location.pathname === c.path
                : location.pathname === c.path
                  || location.pathname.startsWith(`${c.path}/`),
            )
            return (
              <div key={label}>
                <button
                  title={collapsed ? label : undefined}
                  aria-expanded={isOpen}
                  onClick={() => {
                    if (collapsed) {
                      // Children have nowhere to render on the rail — open up.
                      setCollapsed(false)
                      setOpen(label)
                    } else {
                      setOpen(isOpen ? null : label)
                    }
                  }}
                  className={`w-full ${itemBase} ${pad} ${
                    anyActive
                      ? collapsed
                        ? 'bg-brand text-white'
                        : 'text-white'
                      : 'text-white hover:bg-white/10'
                  }`}
                >
                  <Icon size={18} />
                  {!collapsed && (
                    <>
                      <span className="flex-1 text-left">{label}</span>
                      <ChevronDown
                        size={14}
                        className={`transition-transform ${isOpen ? 'rotate-180' : ''}`}
                      />
                    </>
                  )}
                </button>
                {isOpen && (
                  <div className="ml-9 mr-3 mt-1 space-y-1 border-l border-white/10 pl-3">
                    {children.map((c) => (
                      <NavLink
                        key={c.path}
                        to={c.path}
                        end={exactly(c.path)}
                        className={({ isActive }) =>
                          `block rounded-lg px-3 py-2 text-sm font-semibold transition-colors ${
                            isActive
                              ? 'bg-[#086169] text-white'
                              : 'text-white hover:bg-white/10'
                          }`
                        }
                      >
                        {c.label}
                      </NavLink>
                    ))}
                  </div>
                )}
              </div>
            )
          }
          return (
            <NavLink
              key={path}
              to={path}
              end={exactly(path)}
              title={collapsed ? label : undefined}
              className={({ isActive }) =>
                `${itemBase} ${pad} ${
                  isActive
                    ? activeRow
                    : 'text-white hover:bg-white/10'
                }`
              }
            >
              <Icon size={18} />
              {!collapsed && <span className="flex-1">{label}</span>}
              {/* Today's arrivals, so the sidebar says how much work is
                  waiting rather than only where to go. Hidden at zero: a
                  badge that is always there stops being read. */}
              {badge === 'arrivals' && arrivals > 0 && (
                <span className={`rounded-full bg-white/20 px-1.5 py-0.5 text-[11px] font-semibold text-white ${
                  collapsed ? 'absolute right-1 top-1' : ''}`}>
                  {arrivals}
                </span>
              )}
              {/* Red once something is past its deadline: an overdue channel
                  report is money already leaving, not work waiting. */}
              {badge === 'ota' && otaOpen > 0 && (
                <span className={`rounded-full px-1.5 py-0.5 text-[11px] font-semibold text-white ${
                  otaOverdue > 0 ? 'bg-red-500' : 'bg-white/20'} ${
                  collapsed ? 'absolute right-1 top-1' : ''}`}>
                  {otaOpen}
                </span>
              )}
            </NavLink>
          )
        })}
      </nav>

      {!collapsed && (
        <div className="hidden shrink-0 select-none [@media(min-height:820px)]:block">
          <img src="/sidebar-footer.png" alt="" className="w-full" />
        </div>
      )}
    </aside>
  )
}
