import { useEffect, useMemo, useState } from 'react'
import { fmtDate } from '../lib/dates'
import Select from '../components/Select'
import { TABLE_ROW, TABLE_HEAD, TABLE_SHELL, FILTER_SELECT } from '../lib/controls'
import { Link, useNavigate } from 'react-router-dom'
import { useQuery, useQueryClient, keepPreviousData } from '@tanstack/react-query'
import {
  Users, Search, UserPlus, Loader2, Pencil, X, Mail, Phone, Globe, Bed,
  RotateCcw, Sparkles, Download, ChevronLeft, ChevronRight, ChevronsUpDown,
  ChevronUp, ChevronDown, Info, Building2,} from 'lucide-react'
import {
  getGuestDirectory, createGuest, updateGuest,
  type DirectoryRow, type Guest, type DirectoryQuery,
} from '../api'
import EntityCards, { type Tone } from '../components/EntityCards'
import { StayLayoutToggle, useListLayout } from '../lib/listLayout'
import { useOrgId } from '../hooks/useProperty'

const AVATAR = ['bg-teal-500', 'bg-rose-500', 'bg-blue-500', 'bg-purple-500',
  'bg-amber-500', 'bg-indigo-500']
const initials = (n: string) =>
  n.trim().split(/\s+/).map((w) => w[0]).slice(0, 2).join('').toUpperCase()

const money = new Intl.NumberFormat('en-IN',
  { style: 'currency', currency: 'INR', maximumFractionDigits: 0 })
const day = (d: string | null) => fmtDate(d)

/** The edge stripe's colour, from the same status the pill uses. */
const STATUS_TONE: Record<string, Tone> = {
  vip: 'info', blacklisted: 'bad', active: 'ok', inactive: 'muted',
}

const STATUS_STYLE: Record<string, string> = {
  in_house: 'bg-teal-50 text-teal-700',
  returning: 'bg-emerald-50 text-emerald-700',
  new: 'bg-blue-50 text-blue-700',
}
const STATUS_ICON: Record<string, typeof Bed> = {
  in_house: Bed, returning: RotateCcw, new: Sparkles,
}

const TYPES = [
  { value: '', label: 'All Types' },
  { value: 'in_house', label: 'In-house' },
  { value: 'returning', label: 'Returning' },
  { value: 'new', label: 'New' },
]
const WINDOWS = [
  { value: '', label: 'All Time' },
  { value: '30d', label: 'Last 30 days' },
  { value: '90d', label: 'Last 90 days' },
  { value: '365d', label: 'Last 12 months' },
  { value: 'never', label: 'Never stayed' },
]

const PAGE_SIZE = 8

type SortKey = 'name' | 'last_stay' | 'total_stays' | 'lifetime_value' | 'status'


/** The guest directory as cards.
 *
 * This is the list that most wants them: a directory is searched, not
 * compared. Somebody has a name or a phone number and wants that one person,
 * whole — where they are from, when they last stayed, what they are worth,
 * what they like. Seven columns answer that by making the eye travel; a card
 * answers it in one place.
 *
 * Built from the same rows the table renders, so the two cannot disagree.
 */
function GuestCards({ rows, loading, onOpen, onEdit }: {
  rows: DirectoryRow[]
  loading: boolean
  onOpen: (id: string) => void
  onEdit: (g: DirectoryRow) => void
}) {
  return (
    <EntityCards
      loading={loading}
      empty="No guest matches these filters."
      cards={rows.map((g, i) => {
        const Icon = STATUS_ICON[g.status]
        return {
          key: g.id,
          tone: STATUS_TONE[g.status] ?? 'muted',
          badge: (
            <span className={`grid h-9 w-9 place-items-center rounded-lg text-xs font-semibold text-white ${AVATAR[i % AVATAR.length]}`}>
              {initials(g.full_name)}
            </span>
          ),
          title: g.full_name,
          // The reference first: it is what a guest quotes on the phone.
          subtitle: [g.reference, [g.city, g.country].filter(Boolean).join(', ')]
            .filter(Boolean).join(' · '),
          status: (
            <span className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-1 text-xs font-semibold ${STATUS_STYLE[g.status]}`}>
              <Icon size={12} /> {g.status_label}
            </span>
          ),
          actions: (
            <button title={`Edit ${g.full_name}`}
              onClick={(e) => { e.stopPropagation(); onEdit(g) }}
              className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-50 hover:text-brand">
              <Pencil size={15} />
            </button>
          ),
          facts: [
            {
              label: 'Contact',
              value: (g.phone || g.email) ? (
                <span className="block space-y-0.5">
                  {g.phone && (
                    <span className="flex items-center gap-1.5">
                      <Phone size={12} className="shrink-0 text-slate-400" /> {g.phone}
                    </span>
                  )}
                  {g.email && (
                    <span className="flex items-center gap-1.5">
                      <Mail size={12} className="shrink-0 text-slate-400" />
                      <span className="truncate" title={g.email}>{g.email}</span>
                    </span>
                  )}
                </span>
              ) : null,
            },
            {
              label: 'Preferences',
              value: g.preferences.length === 0 ? null : (
                <span className="flex flex-wrap gap-1">
                  {g.preferences.map((pref: string) => (
                    <span key={pref}
                      className="rounded-md bg-slate-75 px-2 py-0.5 text-xs text-slate-600">
                      {pref}
                    </span>
                  ))}
                </span>
              ),
            },
          ],
          footer: (
            <dl className="grid grid-cols-3 gap-2 border-t border-slate-100 pt-3 text-sm">
              <div>
                <dt className="text-xs text-slate-400">Last stay</dt>
                <dd className="truncate font-medium text-slate-700">{day(g.last_stay)}</dd>
              </div>
              <div className="text-center">
                <dt className="text-xs text-slate-400">Stays</dt>
                <dd className="font-medium tabular-nums text-slate-700">{g.total_stays}</dd>
              </div>
              <div className="text-right">
                <dt className="text-xs text-slate-400">Lifetime</dt>
                <dd className="font-semibold tabular-nums text-ink">
                  {money.format(Number(g.lifetime_value))}
                </dd>
              </div>
            </dl>
          ),
          onClick: () => onOpen(g.id),
        }
      })}
    />
  )
}

export default function Guests() {
  const qc = useQueryClient()
  const orgId = useOrgId()
  const navigate = useNavigate()

  const [search, setSearch] = useState('')
  const [q, setQ] = useState('')
  const [status, setStatus] = useState('')
  const [lastStay, setLastStay] = useState('')
  const [country, setCountry] = useState('')
  const [sort, setSort] = useState<SortKey>('name')
  const [direction, setDirection] = useState<'asc' | 'desc'>('asc')
  const [page, setPage] = useState(1)
  const [modal, setModal] = useState<{ mode: 'create' | 'edit'; guest?: Guest } | null>(null)
  const [toast, setToast] = useState('')
  const [exporting, setExporting] = useState(false)

  // Typing should not fire a query per keystroke.
  useEffect(() => {
    const t = setTimeout(() => { setQ(search); setPage(1) }, 300)
    return () => clearTimeout(t)
  }, [search])

  const params: DirectoryQuery = useMemo(() => ({
    q: q || undefined, status: status || undefined,
    last_stay: lastStay || undefined, country: country || undefined,
    sort, direction, page, page_size: PAGE_SIZE,
  }), [q, status, lastStay, country, sort, direction, page])

  const { data, isLoading, isFetching } = useQuery({
    queryKey: ['guest-directory', orgId, params],
    queryFn: () => getGuestDirectory(orgId, params),
    enabled: orgId !== '',
    placeholderData: keepPreviousData,
  })

  const rows = data?.rows ?? []

  const [layout, chooseLayout] = useListLayout('guests')
  const total = data?.total ?? 0
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const from = total === 0 ? 0 : (page - 1) * PAGE_SIZE + 1
  const to = Math.min(page * PAGE_SIZE, total)

  function flash(m: string) { setToast(m); setTimeout(() => setToast(''), 2500) }
  function refresh() { qc.invalidateQueries({ queryKey: ['guest-directory'] }) }
  function reorder(key: SortKey) {
    if (sort === key) setDirection(direction === 'asc' ? 'desc' : 'asc')
    else { setSort(key); setDirection(key === 'name' ? 'asc' : 'desc') }
    setPage(1)
  }

  /** Exports what the filters currently select, not just the page on screen. */
  async function exportCsv() {
    setExporting(true)
    try {
      const all = await getGuestDirectory(orgId, { ...params, page: 1, page_size: 200 })
      const head = ['Guest ID', 'Guest', 'City', 'Country', 'Phone', 'Email',
        'Last stay', 'Total stays', 'Lifetime value', 'Status']
      const cell = (v: unknown) => `"${String(v ?? '').replace(/"/g, '""')}"`
      const csv = [head.map(cell).join(',')].concat(
        all.rows.map((g) => [g.reference, g.full_name, g.city, g.country, g.phone, g.email,
          g.last_stay, g.total_stays, g.lifetime_value, g.status_label]
          .map(cell).join(',')),
      ).join('\r\n')
      const url = URL.createObjectURL(new Blob([`﻿${csv}`],
        { type: 'text/csv;charset=utf-8' }))
      const a = document.createElement('a')
      a.href = url
      a.download = `guests-${new Date().toISOString().slice(0, 10)}.csv`
      a.click()
      URL.revokeObjectURL(url)
      flash(`Exported ${all.rows.length} of ${all.total} guests.`)
    } catch {
      flash('Export failed.')
    } finally { setExporting(false) }
  }

  const k = data?.kpis
  const cards = [
    { label: 'Total Guests', value: k?.total_guests, icon: Users,
      ring: 'bg-blue-50', tint: 'text-blue-600', card: 'bg-blue-50/40' },
    { label: 'In-house', value: k?.in_house, icon: Bed,
      ring: 'bg-emerald-50', tint: 'text-emerald-600', card: 'bg-emerald-50/40' },
    { label: 'Returning', value: k?.returning, icon: RotateCcw,
      ring: 'bg-amber-50', tint: 'text-amber-600', card: 'bg-amber-50/40' },
    { label: 'New', value: k?.new_guests, icon: Sparkles,
      ring: 'bg-rose-50', tint: 'text-rose-500', card: 'bg-rose-50/40' },
  ]

  const sel = `${FILTER_SELECT} bg-white outline-none focus:border-brand`

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <h1 className="flex items-center gap-2 text-display text-ink">
          <Users size={26} className="text-brand" /> Guests
        </h1>
        <div className="flex flex-wrap items-center gap-2">
          {/* Companies and agents live alongside the guest directory rather
              than under their own nav item — they are the other half of
              "who are our customers". */}
          <Link to="/guests/companies"
            className="flex items-center gap-1.5 whitespace-nowrap rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-600 hover:bg-slate-50">
            <Building2 size={15} /> Companies &amp; Agents
          </Link>
          {data?.can_export && (
            <button onClick={exportCsv} disabled={exporting || total === 0}
              className="flex items-center gap-1.5 whitespace-nowrap rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-600 hover:bg-slate-50 disabled:opacity-40">
              {exporting ? <Loader2 size={15} className="animate-spin" />
                : <Download size={15} />} Export
            </button>
          )}
          {data?.can_create !== false && (
            <button onClick={() => setModal({ mode: 'create' })}
              className="flex items-center gap-1.5 whitespace-nowrap rounded-lg bg-brand px-3 py-2 text-sm font-medium text-white hover:bg-brand-dark disabled:opacity-40">
              <UserPlus size={15} /> Add Guest
            </button>
          )}
        </div>
      </div>

      {toast && (
        <div className="rounded-xl bg-emerald-50 px-4 py-3 text-sm text-emerald-700">
          {toast}
        </div>
      )}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {cards.map((c) => (
          <div key={c.label}
            className={`flex items-center gap-4 rounded-2xl border border-slate-100 px-5 py-4 ${c.card}`}>
            <span className={`flex h-12 w-12 shrink-0 items-center justify-center rounded-full ${c.ring}`}>
              <c.icon size={22} className={c.tint} />
            </span>
            <span className="min-w-0">
              <span className="block text-sm text-slate-600">{c.label}</span>
              <span className="block text-2xl font-bold text-slate-800">
                {c.value === undefined
                  ? <span className="text-slate-300">&mdash;</span>
                  : c.value.toLocaleString('en-IN')}
              </span>
            </span>
          </div>
        ))}
      </div>

      {/* One row, no captions: each select's first option says what it
          filters. Search and the layout toggle sit at the right end. */}
      <div className="flex flex-wrap items-center gap-2">
        <Select blankIsChoice value={status} className={sel} aria-label="Guest type"
          onChange={(e) => { setStatus(e.target.value); setPage(1) }}>
          {TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
        </Select>
        <Select blankIsChoice value={lastStay} className={sel} aria-label="Last stay"
          onChange={(e) => { setLastStay(e.target.value); setPage(1) }}>
          {WINDOWS.map((w) => <option key={w.value} value={w.value}>{w.label}</option>)}
        </Select>
        <Select blankIsChoice value={country} className={sel} aria-label="Country"
          onChange={(e) => { setCountry(e.target.value); setPage(1) }}>
          <option value="">All Countries</option>
          {(data?.countries ?? []).map((c) => <option key={c} value={c}>{c}</option>)}
        </Select>
        <div className="flex min-w-[240px] flex-1 items-center justify-end gap-2">
          <div className="relative min-w-0 max-w-sm flex-1">
            <Search size={16}
              className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <input value={search} onChange={(e) => setSearch(e.target.value)}
              placeholder="Search name, ID, phone, email or city"
              className="w-full rounded-lg border border-slate-200 bg-slate-50 py-2 pl-9 pr-3 text-sm outline-none focus:border-brand" />
          </div>
          <StayLayoutToggle layout={layout} onChange={chooseLayout} />
        </div>
      </div>

      {layout === 'cards' && (
        <GuestCards rows={rows} loading={isLoading}
          onOpen={(id) => navigate(`/guests/${id}`)}
          onEdit={(g) => setModal({ mode: 'edit', guest: g as Guest })} />
      )}

      <div className={`${TABLE_SHELL} ${
        layout === 'cards' ? 'border-0 shadow-none' : ''}`}>
        <div className={`overflow-x-auto ${layout === 'cards' ? 'hidden' : ''}`}>
          <table className="w-full text-sm">
            <thead>
              <tr className={TABLE_HEAD}>
                <Th label="Guest" k="name" sort={sort} dir={direction} on={reorder}
                  className="pl-5" />
                <th className="px-4 py-3 font-semibold">Contact</th>
                <Th label="Last Stay" k="last_stay" sort={sort} dir={direction} on={reorder} />
                <Th label="Total Stays" k="total_stays" sort={sort} dir={direction}
                  on={reorder} align="right" />
                <Th label="Lifetime Value" k="lifetime_value" sort={sort} dir={direction}
                  on={reorder} align="right" />
                <th className="px-4 py-3 font-semibold">Preferences</th>
                <Th label="Status" k="status" sort={sort} dir={direction} on={reorder} />
                <th className="w-12 px-4 py-3" />
              </tr>
            </thead>
            <tbody>
              {isLoading && (
                <tr><td colSpan={8} className="p-10 text-center text-slate-400">
                  <Loader2 size={18} className="mx-auto animate-spin" />
                </td></tr>
              )}
              {!isLoading && rows.length === 0 && (
                <tr><td colSpan={8} className="p-10 text-center text-sm text-slate-400">
                  No guest matches these filters.
                </td></tr>
              )}
              {rows.map((g, i) => {
                const Icon = STATUS_ICON[g.status]
                return (
                  // The whole row opens the profile. Keyboard too: a row you
                  // can only reach with a mouse is a row half the desk cannot
                  // use. The Edit button inside stops the click from bubbling,
                  // so the two do not fight over the same pixels.
                  <tr key={g.id} tabIndex={0} role="link"
                    onClick={() => navigate(`/guests/${g.id}`)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault()
                        navigate(`/guests/${g.id}`)
                      }
                    }}
                    className={`${TABLE_ROW} cursor-pointer text-slate-700 outline-none focus-visible:bg-brand/5`}>
                    <td className="py-3 pl-5 pr-4">
                      <div className="flex items-center gap-3">
                        <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-xs font-semibold text-white ${AVATAR[i % AVATAR.length]}`}>
                          {initials(g.full_name)}
                        </span>
                        <span className="min-w-0">
                          <span className="block font-medium text-slate-800">
                            {g.full_name}
                          </span>
                          {/* The reference first: it is what a guest quotes on
                              the phone, and what the search box matches. Joined
                              rather than concatenated so a guest with no city
                              does not get a separator with nothing after it. */}
                          <span className="block text-xs text-slate-500">
                            {[g.reference, [g.city, g.country].filter(Boolean).join(', ')]
                              .filter(Boolean).join(' · ') || '—'}
                          </span>
                        </span>
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      <span className="flex items-center gap-1.5 text-xs text-slate-600">
                        <Phone size={12} className="shrink-0 text-slate-400" />
                        {g.phone || '—'}
                      </span>
                      <span className="flex items-center gap-1.5 text-xs text-slate-600">
                        <Mail size={12} className="shrink-0 text-slate-400" />
                        {g.email || '—'}
                      </span>
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-slate-600">
                      {day(g.last_stay)}
                    </td>
                    <td className="px-4 py-3 text-right text-slate-700">{g.total_stays}</td>
                    <td className="whitespace-nowrap px-4 py-3 text-right font-medium text-slate-800">
                      {money.format(Number(g.lifetime_value))}
                    </td>
                    <td className="px-4 py-3">
                      {g.preferences.length === 0
                        ? <span className="text-slate-300">&mdash;</span>
                        : (
                          <span className="flex flex-wrap gap-1">
                            {g.preferences.map((p) => (
                              <span key={p}
                                className="rounded-md bg-slate-75 px-2 py-0.5 text-xs text-slate-600">
                                {p}
                              </span>
                            ))}
                          </span>
                        )}
                    </td>
                    <td className="px-4 py-3">
                      <span className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-1 text-xs font-semibold ${STATUS_STYLE[g.status]}`}>
                        <Icon size={12} /> {g.status_label}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-right">
                      <button title={`Edit ${g.full_name}`}
                        onClick={(e) => {
                          e.stopPropagation()
                          setModal({ mode: 'edit', guest: g as Guest })
                        }}
                        className="rounded-lg p-2 text-slate-400 hover:bg-slate-50 hover:text-brand">
                        <Pencil size={15} />
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>

        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 px-5 py-3">
          <p className="text-sm text-slate-500">
            Showing {from} &ndash; {to} of {total.toLocaleString('en-IN')} guests
            {isFetching && !isLoading && (
              <Loader2 size={13} className="ml-2 inline animate-spin text-slate-300" />
            )}
          </p>
          {pages > 1 && (
            <div className="flex items-center gap-1">
              <PageBtn disabled={page === 1} onClick={() => setPage(page - 1)}>
                <ChevronLeft size={15} />
              </PageBtn>
              {pageList(page, pages).map((n, i) => typeof n === 'string' ? (
                <span key={`gap${i}`} className="px-1.5 text-slate-400">&hellip;</span>
              ) : (
                <button key={n} onClick={() => setPage(n)}
                  className={`h-8 min-w-8 rounded-lg px-2 text-sm font-medium ${
                    n === page ? 'bg-brand text-white'
                      : 'border border-slate-200 text-slate-600 hover:bg-slate-50'}`}>
                  {n}
                </button>
              ))}
              <PageBtn disabled={page === pages} onClick={() => setPage(page + 1)}>
                <ChevronRight size={15} />
              </PageBtn>
            </div>
          )}
        </div>
      </div>

      {/* Said out loud rather than filled in with plausible-looking chips. */}
      <p className="flex items-start gap-2 text-xs text-slate-400">
        <Info size={13} className="mt-0.5 shrink-0" />
        Status, stays and lifetime value are read from the stay record and the
        ledger, so they cannot drift. Preferences stay empty until guest
        preferences (SCR-067) exists, and there is no VIP or blacklist flag to
        show &mdash; nothing in the system sets one yet.
      </p>

      {modal && (
        <GuestModal mode={modal.mode} guest={modal.guest} orgId={orgId}
          onClose={() => setModal(null)}
          onDone={(m) => { flash(m); refresh() }} />
      )}
    </div>
  )
}

function Th({ label, k, sort, dir, on, align, className = '' }: {
  label: string; k: SortKey; sort: SortKey; dir: 'asc' | 'desc'
  on: (k: SortKey) => void; align?: 'right'; className?: string
}) {
  const active = sort === k
  const Icon = !active ? ChevronsUpDown : dir === 'asc' ? ChevronUp : ChevronDown
  return (
    <th className={`px-4 py-3 font-semibold ${className}`}
      aria-sort={active ? (dir === 'asc' ? 'ascending' : 'descending') : 'none'}>
      <button onClick={() => on(k)}
        className={`flex items-center gap-1 whitespace-nowrap hover:text-brand ${
          align === 'right' ? 'ml-auto' : ''} ${active ? 'text-brand' : ''}`}>
        {label}
        <Icon size={13} className={active ? '' : 'text-slate-300'} />
      </button>
    </th>
  )
}

function PageBtn({ disabled, onClick, children }: {
  disabled: boolean; onClick: () => void; children: React.ReactNode
}) {
  return (
    <button disabled={disabled} onClick={onClick}
      className="flex h-8 w-8 items-center justify-center rounded-lg border border-slate-200 text-slate-500 enabled:hover:bg-slate-50 disabled:opacity-40">
      {children}
    </button>
  )
}

/** 1 ... 4 5 6 ... 20 - never more than seven controls wide. */
function pageList(page: number, pages: number): (number | 'gap')[] {
  if (pages <= 7) return Array.from({ length: pages }, (_, i) => i + 1)
  const out: (number | 'gap')[] = [1]
  const lo = Math.max(2, page - 1)
  const hi = Math.min(pages - 1, page + 1)
  if (lo > 2) out.push('gap')
  for (let n = lo; n <= hi; n++) out.push(n)
  if (hi < pages - 1) out.push('gap')
  out.push(pages)
  return out
}

function GuestModal({ mode, guest, orgId, onClose, onDone }: {
  mode: 'create' | 'edit'; guest?: Guest; orgId: string
  onClose: () => void; onDone: (m: string) => void
}) {
  const [name, setName] = useState(guest?.full_name ?? '')
  const [email, setEmail] = useState(guest?.email ?? '')
  const [phone, setPhone] = useState(guest?.phone ?? '')
  const [nat, setNat] = useState(guest?.nationality ?? '')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  async function submit() {
    setErr(''); setBusy(true)
    try {
      if (mode === 'edit' && guest) {
        await updateGuest(guest.id, {
          full_name: name.trim(), email: email || null,
          phone: phone || null, nationality: nat || null,
        })
        onDone('Guest updated.')
      } else {
        await createGuest({
          organization_id: orgId, full_name: name.trim(),
          email: email || undefined, phone: phone || undefined,
          nationality: nat || undefined,
        })
        onDone('Guest added.')
      }
      onClose()
    } catch (e) {
      const er = e as { response?: { data?: { detail?: string } }; message?: string }
      setErr(er.response?.data?.detail ?? er.message ?? 'Failed to save')
    } finally { setBusy(false) }
  }

  const inp = 'w-full rounded-lg border border-slate-200 px-3 py-2.5 text-sm outline-none focus:border-brand'
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4"
      onClick={onClose}>
      <div className="w-full max-w-md rounded-2xl bg-white p-6 shadow-xl"
        onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold text-ink">
            {mode === 'edit' ? 'Edit Guest' : 'Add Guest'}
          </h2>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600">
            <X size={18} />
          </button>
        </div>
        {err && (
          <div className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{err}</div>
        )}
        <label className="mt-3 block">
          <span className="mb-1 block text-sm font-medium text-slate-600">Full Name</span>
          <input value={name} onChange={(e) => setName(e.target.value)} className={inp} autoFocus />
        </label>
        <label className="mt-3 block">
          <span className="mb-1 flex items-center gap-1.5 text-sm font-medium text-slate-600">
            <Mail size={13} /> Email
          </span>
          <input value={email} onChange={(e) => setEmail(e.target.value)} className={inp} />
        </label>
        <label className="mt-3 block">
          <span className="mb-1 flex items-center gap-1.5 text-sm font-medium text-slate-600">
            <Phone size={13} /> Phone
          </span>
          <input value={phone} onChange={(e) => setPhone(e.target.value)} className={inp} />
        </label>
        <label className="mt-3 block">
          <span className="mb-1 flex items-center gap-1.5 text-sm font-medium text-slate-600">
            <Globe size={13} /> Nationality
          </span>
          <input value={nat} onChange={(e) => setNat(e.target.value)} className={inp} />
        </label>
        <div className="mt-5 flex justify-end gap-2">
          <button onClick={onClose}
            className="rounded-lg border border-slate-200 px-4 py-2 text-sm text-slate-600 hover:bg-slate-50">
            Cancel
          </button>
          <button onClick={submit} disabled={busy || !name.trim()}
            className="flex items-center gap-2 rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white enabled:hover:bg-brand/90 disabled:opacity-50">
            {busy ? <Loader2 size={15} className="animate-spin" />
              : (mode === 'edit' ? <Pencil size={15} /> : <UserPlus size={15} />)}
            {mode === 'edit' ? 'Save' : 'Add'}
          </button>
        </div>
      </div>
    </div>
  )
}
