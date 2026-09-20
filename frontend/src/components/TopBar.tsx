import { useEffect, useRef, useState } from 'react'
import { Search, Bell, CalendarDays, ChevronDown, Building2, LogOut } from 'lucide-react'
import { useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'
import { usePropertyName } from '../hooks/useProperty'
import { fmtDateLong } from '../lib/dates'

export default function TopBar() {
  const propertyName = usePropertyName()
  const { session, logout } = useAuth()
  const name = session?.display_name ?? 'Guest'
  const initials = name
    .split(' ')
    .map((w) => w[0])
    .slice(0, 2)
    .join('')
    .toUpperCase()

  const today = fmtDateLong(new Date())

  const navigate = useNavigate()
  const { search: locationSearch } = useLocation()
  const [search, setSearch] = useState('')

  // Keep the box showing whatever the results page is showing, so refining a
  // search starts from the current query and a shared link arrives filled in.
  useEffect(() => {
    const q = new URLSearchParams(locationSearch).get('q')
    if (q !== null) setSearch(q)
  }, [locationSearch])

  // The mockup shows the account as a photo, a name and a disclosure chevron.
  // Sign out lives behind the chevron rather than beside it as a bare icon: an
  // unlabelled door next to someone's name is easy to hit by accident.
  const [menuOpen, setMenuOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!menuOpen) return
    function onDown(e: MouseEvent) {
      if (!menuRef.current?.contains(e.target as Node)) setMenuOpen(false)
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') setMenuOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [menuOpen])

  // h-14, matching the sidebar header exactly. They were 57px and 69px, so
  // the two bottom borders drew two different lines a dozen pixels apart and
  // the property name sat 6px above the chip naming the same property. A
  // shared height is the only thing that keeps them level as either side
  // gains content.
  return (
    <header className="flex h-14 shrink-0 items-center gap-4 border-b border-slate-200 bg-white px-6">
      {/* Property switcher */}
      <button className="flex items-center gap-2 rounded-lg border border-slate-200 px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50">
        <Building2 size={16} className="text-brand" />
        {propertyName || 'Select property'}
        <ChevronDown size={14} className="text-slate-400" />
      </button>

      {/* Search. This was an input with no state and no handler -- it
          offered three things and did none of them. A form, so Enter
          submits the way a search box is expected to. */}
      <form
        role="search"
        onSubmit={(e) => {
          e.preventDefault()
          const q = search.trim()
          if (q.length >= 2) navigate(`/search?q=${encodeURIComponent(q)}`)
        }}
        className="relative flex-1"
      >
        <Search
          size={16}
          className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400"
        />
        <input
          id="app-search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search guests, reservations, rooms..."
          aria-label="Search guests, reservations and rooms"
          className="w-full rounded-lg border border-slate-200 bg-slate-50 py-2 pl-9 pr-3 text-sm outline-none focus:border-brand"
        />
      </form>

      {/* Business date */}
      <div className="flex items-center gap-2 text-sm text-slate-600">
        <CalendarDays size={16} className="text-slate-400" />
        {today}
      </div>

      {/* Notifications */}
      <button className="relative text-slate-500 hover:text-slate-700">
        <Bell size={18} />
        <span className="absolute -right-1 -top-1 flex h-4 w-4 items-center justify-center rounded-full bg-red-500 text-[10px] text-white">
          3
        </span>
      </button>

      {/* Profile */}
      <div className="relative" ref={menuRef}>
        <button
          onClick={() => setMenuOpen((v) => !v)}
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          className="flex items-center gap-2.5 rounded-lg py-1 pl-1 pr-2 hover:bg-slate-50"
        >
          {/* A photo when the account has one. Falling back to initials rather
              than to a stock face: a stranger's portrait over someone's own
              name reads as that person. */}
          {session?.avatar_url ? (
            <img
              src={session.avatar_url}
              alt=""
              className="h-9 w-9 rounded-full object-cover ring-1 ring-slate-200"
            />
          ) : (
            <span className="flex h-9 w-9 items-center justify-center rounded-full bg-brand text-xs font-semibold text-white">
              {initials || 'U'}
            </span>
          )}
          <span className="text-sm font-medium text-slate-800">{name}</span>
          <ChevronDown
            size={16}
            className={`text-slate-400 transition-transform ${menuOpen ? 'rotate-180' : ''}`}
          />
        </button>

        {menuOpen && (
          <div
            role="menu"
            className="absolute right-0 z-30 mt-2 w-44 overflow-hidden rounded-xl border border-slate-200 bg-white py-1 shadow-lg"
          >
            <button
              role="menuitem"
              onClick={logout}
              className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-slate-700 hover:bg-slate-50 hover:text-red-600"
            >
              <LogOut size={16} /> Sign out
            </button>
          </div>
        )}
      </div>
    </header>
  )
}
