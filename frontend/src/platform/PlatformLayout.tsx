import { Suspense, useEffect, useState } from 'react'
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import ScreenBoundary from './ScreenBoundary'
import PageSpinner from '../components/PageSpinner'
import {
  Activity, Building2, CalendarClock, ChevronDown, CreditCard, Globe,
  Handshake, Hotel, LayoutGrid, LifeBuoy, LogOut, Mail, PanelLeftClose,
  PanelLeftOpen, Search, Settings,
  ShieldCheck, Users, Waves,
} from 'lucide-react'
import { useAuth } from '../auth/AuthContext'

/** The console shell, built to the design pack's artboards.
 *
 *  White sidebar, teal pill on the active item, a context bar across the top
 *  and a status strip at the foot. The information architecture is the pack's
 *  own: thirteen workspace entries grouping twenty-eight screens, so
 *  Subscriptions, Plans and Invoices sit under one "Subscriptions & billing"
 *  rather than as three separate destinations.
 *
 *  Every entry now routes to a screen reading live data. Where a module is
 *  deliberately partial -- support access records a request without opening
 *  anything, recovery registers backups without taking them -- the screen
 *  itself says so, which is the right place for it: a caveat in the sidebar
 *  is read once and a caveat on the page is read by whoever is about to act.
 */

interface Item {
  to: string
  label: string
  icon: typeof LayoutGrid
  end?: boolean
  children?: { to: string; label: string }[]
}

const NAV: Item[] = [
  { to: '/platform', label: 'Platform overview', icon: LayoutGrid, end: true },
  {
    to: '/platform/tenants', label: 'Tenants', icon: Building2,
    children: [
      { to: '/platform/tenants', label: 'All tenants' },
      { to: '/platform/onboarding', label: 'Onboarding progress' },
      { to: '/platform/business-dates', label: 'Business dates' },
      // Beside business dates because it is the same kind of question: an
      // operational obligation with a deadline that no single property can
      // see itself falling behind on.
      { to: '/platform/ota-actions', label: 'OTA actions' },
    ],
  },
  { to: '/platform/properties', label: 'Properties', icon: Hotel },
  {
    to: '/platform/subscriptions', label: 'Subscriptions & billing',
    icon: CreditCard,
    children: [
      { to: '/platform/subscriptions', label: 'Subscriptions' },
      { to: '/platform/plans', label: 'Plans & pricing' },
      { to: '/platform/invoices', label: 'Invoices & payments' },
    ],
  },
  {
    to: '/platform/admins', label: 'Team & access', icon: Users,
    children: [
      { to: '/platform/admins', label: 'Platform team' },
      { to: '/platform/roles', label: 'Roles & permissions' },
      { to: '/platform/users', label: 'User lookup' },
      { to: '/platform/permissions', label: 'Tenant catalogue' },
    ],
  },
  {
    to: '/platform/channels', label: 'Integrations', icon: Handshake,
    children: [
      { to: '/platform/channels', label: 'Channel health' },
      { to: '/platform/integrations', label: 'Provider setup' },
    ],
  },
  { to: '/platform/booking-engine', label: 'Booking engine', icon: Globe },
  { to: '/platform/messaging', label: 'Messaging', icon: Mail },
  { to: '/platform/support', label: 'Support', icon: LifeBuoy },
  {
    to: '/platform/audit', label: 'Audit & security', icon: ShieldCheck,
    children: [
      { to: '/platform/audit', label: 'Audit logs' },
      { to: '/platform/security', label: 'Security & sessions' },
    ],
  },
  {
    to: '/platform/operations', label: 'System operations', icon: Activity,
    children: [
      { to: '/platform/operations', label: 'Health & jobs' },
      { to: '/platform/recovery', label: 'Backups & recovery' },
    ],
  },
  { to: '/platform/settings', label: 'Platform settings', icon: Settings },
  { to: '/platform/analytics', label: 'Analytics', icon: CalendarClock },
]

function initials(name?: string): string {
  if (!name) return '··'
  const parts = name.trim().split(/\s+/)
  return ((parts[0]?.[0] ?? '') + (parts[1]?.[0] ?? '')).toUpperCase() || '··'
}

export default function PlatformLayout() {
  const { session, logout } = useAuth()
  const navigate = useNavigate()
  const { pathname, search: locationSearch } = useLocation()
  const [search, setSearch] = useState('')

  // Collapsed to icons, remembered per browser. A console someone lives in
  // all day is worth 176px of width, and the choice should survive a reload
  // rather than being remade on every visit.
  const [collapsed, setCollapsed] = useState(
    () => localStorage.getItem('pf_nav_collapsed') === '1')
  useEffect(() => {
    localStorage.setItem('pf_nav_collapsed', collapsed ? '1' : '0')
  }, [collapsed])

  // Keep the header box showing whatever the results screen is showing, so
  // refining a search starts from the current query instead of an empty box,
  // and a shared /platform/search?q=... link arrives with the box filled in.
  useEffect(() => {
    const q = new URLSearchParams(locationSearch).get('q')
    if (q !== null) setSearch(q)
  }, [locationSearch])

  return (
    <div className="flex h-full overflow-hidden bg-pf-bg font-platform">
      {/* Navy, per the revised spec. No right border: against the light grey
          page the edge of a dark panel is already the strongest line on the
          screen, and a border on top of it reads as a seam. */}
      <aside
        className={`flex h-full shrink-0 flex-col overflow-hidden bg-pf-sidebar transition-[width] duration-150 motion-reduce:transition-none ${
          collapsed ? 'w-[68px]' : 'w-pf-side'}`}>
        <div className={`flex shrink-0 items-center gap-3 py-5 ${
          collapsed ? 'justify-center px-0' : 'px-5'}`}>
          {/* A tint of the sidebar's own white rather than the light teal
              tile, which was drawn to sit on a white panel and glares on a
              dark one. */}
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-white/10 text-white">
            <Waves size={18} />
          </div>
          {!collapsed && (
            <div className="min-w-0 leading-tight">
              <div className="text-pf-card text-white">Platform Admin</div>
              <div className="text-[10px] font-medium uppercase tracking-[0.12em] text-pf-sidebar-label">
                Hotel &amp; Resort PMS
              </div>
            </div>
          )}
        </div>

        {/* The control lives in the panel it controls, so it moves with it
            and is in the same place whichever state you are in. */}
        <button
          type="button"
          onClick={() => setCollapsed((v) => !v)}
          aria-expanded={!collapsed}
          aria-label={collapsed ? 'Expand navigation' : 'Collapse navigation'}
          title={collapsed ? 'Expand navigation' : 'Collapse navigation'}
          className={`mx-[15px] mb-2 flex h-8 shrink-0 items-center rounded-md text-pf-sidebar-text transition hover:bg-white/10 hover:text-white ${
            collapsed ? 'justify-center px-0' : 'gap-2.5 px-3'}`}>
          {collapsed
            ? <PanelLeftOpen size={16} className="shrink-0" />
            : <PanelLeftClose size={16} className="shrink-0" />}
          {!collapsed && <span className="text-pf-nav">Collapse</span>}
        </button>

        {!collapsed && (
          <div className="shrink-0 px-5 pb-2 text-[10px] font-semibold uppercase tracking-[0.16em] text-pf-sidebar-label">
            Workspace
          </div>
        )}

        <nav className="scroll-slim min-h-0 flex-1 space-y-[9px] overflow-y-auto overflow-x-hidden px-[15px] pb-4">
          {NAV.map((item) => {
            const Icon = item.icon
            const open = !!item.children?.some(
              (c) => pathname === c.to || pathname.startsWith(c.to + '/'))
            return (
              <div key={item.to}>
                <NavLink to={item.to} end={item.end}
                  // The label is the only thing naming the row once the
                  // panel is narrow, so it becomes the tooltip.
                  title={collapsed ? item.label : undefined}
                  // A group's children have nowhere to render on the rail,
                  // so opening one opens the rail with it. Matches the
                  // tenant application's sidebar, which already did this.
                  onClick={() => { if (collapsed && item.children) setCollapsed(false) }}
                  className={({ isActive }) =>
                    `flex h-[42px] w-full items-center rounded-md transition ${
                      collapsed ? 'justify-center px-0' : 'gap-2.5 px-3'
                    } ${
                      isActive || open
                        ? 'bg-pf-nav-active text-pf-nav-on text-white'
                        : 'text-pf-nav text-pf-sidebar-text hover:bg-white/10 hover:text-white'
                    }`}>
                  <Icon size={17} className="shrink-0" />
                  {!collapsed && (
                    <>
                      <span className="min-w-0 flex-1 truncate">{item.label}</span>
                      {item.children && (
                        <ChevronDown size={13} className={
                          'shrink-0 ' + (open ? 'text-white/70' : 'text-white/40')} />
                      )}
                    </>
                  )}
                </NavLink>
                {/* No sub-list while collapsed: there is no width to read it
                    in. The parent row still navigates to the section. */}
                {item.children && open && !collapsed && (
                  <div className="mb-1 ml-[30px] mt-0.5 space-y-0.5 overflow-hidden border-l border-white/15 pl-3 motion-safe:animate-[submenu_140ms_ease-out]">
                    {item.children.map((c) => (
                      <NavLink key={c.to} to={c.to} end
                        className={({ isActive }) =>
                          `block truncate rounded-md px-2.5 py-1.5 transition ${
                            isActive
                              ? 'text-pf-tab text-white'
                              : 'text-pf-nav text-pf-sidebar-text hover:text-white'
                          }`}>
                        {c.label}
                      </NavLink>
                    ))}
                  </div>
                )}
              </div>
            )
          })}
        </nav>

        <div className={`shrink-0 border-t border-white/10 py-4 ${
          collapsed ? 'px-0' : 'px-5'}`}>
          {/* The teal that marks this as the live console is unreadable on
              navy at 2.6:1, so the accent carries it instead -- 7.2:1, and
              still unmistakably the brand hue rather than plain white.
              Collapsed, the warning survives as a dot: the point is that
              this console is not a sandbox, which is worth keeping at any
              width. */}
          {collapsed ? (
            <div className="flex justify-center"
              title="Live console — real tenant data, actions are recorded">
              <span className="h-2 w-2 rounded-full bg-brand-accent" />
            </div>
          ) : (
            <>
              <div className="text-[10px] font-semibold uppercase tracking-[0.16em] text-brand-accent">
                Live console
              </div>
              <div className="mt-1 text-pf-help text-pf-sidebar-label">
                Real tenant data · actions are recorded
              </div>
            </>
          )}
        </div>
      </aside>

      {/* The header scrolls with the content rather than staying put. It used
          to sit outside the scroll container, which is what kept it fixed;
          moving the scroll onto the column that holds both makes the whole
          thing one scrolling surface. The sidebar still scrolls on its own. */}
      <div className="scroll-slim flex h-full min-w-0 flex-1 flex-col overflow-y-auto [scrollbar-gutter:stable]">
        <header className="flex h-pf-header shrink-0 items-center gap-4 border-b border-pf-border bg-white px-6">
          <div className="flex shrink-0 items-center gap-2 text-pf-tab text-pf-body">
            <Building2 size={16} className="text-pf-deep" />
            All tenants
          </div>
          {/* The design pack badges its screens "Sandbox" because they are
              mockups over sample data. This console is neither, and saying so
              is the more useful badge: every action here lands on a real
              customer. */}
          <span className="shrink-0 rounded bg-pf-warn-bg px-2 py-0.5 text-pf-badge text-pf-warn-text">
            Live data
          </span>

          {/* A form, so Enter submits the way a search box is expected to.
              It used to hand the query to the user lookup, which searches
              people only and never read it -- so a tenant name returned an
              empty form, which is what "search does not work" looked like. */}
          <form
            role="search"
            onSubmit={(e) => {
              e.preventDefault()
              const q = search.trim()
              if (q.length >= 2) navigate(`/platform/search?q=${encodeURIComponent(q)}`)
            }}
            className="relative mx-auto hidden w-pf-search md:block"
          >
            <Search size={15}
              className="absolute left-3 top-1/2 -translate-y-1/2 text-pf-placeholder" />
            <input
              id="platform-search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search tenants, properties or people…"
              aria-label="Search tenants, properties or people"
              className="h-[38px] w-full rounded-md border border-pf-border bg-pf-search pl-9 pr-3 text-pf-input text-pf-body outline-none placeholder:text-pf-placeholder focus:border-pf-teal focus:bg-white" />
          </form>

          <div className="ml-auto flex shrink-0 items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-full bg-pf-soft text-xs font-semibold text-pf-deep">
              {initials(session?.display_name)}
            </div>
            <div className="hidden leading-tight sm:block">
              <div className="text-pf-tab text-pf-navy">
                {session?.display_name}
              </div>
              <div className="text-pf-help text-pf-muted">Platform operator</div>
            </div>
            <button
              onClick={() => { logout(); navigate('/platform/login', { replace: true }) }}
              title="Sign out"
              className="rounded-lg p-2 text-pf-muted hover:bg-pf-bg hover:text-pf-body">
              <LogOut size={16} />
            </button>
          </div>
        </header>

        <main className="min-w-0 flex-1">
          {/* Keyed on the path so navigating away clears a failed boundary.
              Without the key React holds the error state forever and every
              screen after the first crash renders the error page instead of
              itself -- which looks exactly like the whole console breaking. */}
          <ScreenBoundary key={pathname} where={pathname}>
            <Suspense fallback={<PageSpinner />}>
              <Outlet />
            </Suspense>
          </ScreenBoundary>
        </main>
      </div>
    </div>
  )
}
