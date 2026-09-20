import { useEffect } from 'react'
import { createPortal } from 'react-dom'
import {
  BedDouble, ChevronRight, CreditCard, MessageSquare, Moon, Receipt,
  Users, Wallet, X,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import type { ActionItem } from './ActionsMenu'
import { fmtDate } from '../lib/dates'

/** The row's actions as a panel rather than a dropdown.
 *
 * A ten-item menu hanging off a "⋮" asked the desk to recognise each action
 * from two or three words and remember which of four tabs it was on. The panel
 * has room to say what each one does, to show who the booking is for while the
 * choice is being made, and to group the actions under headings — so the
 * decision is made with the booking in front of you rather than from memory.
 *
 * Actions with no backing are shown disabled with the reason. Rendering a
 * "Send confirmation" that silently does nothing would be worse than saying
 * there is no mail transport.
 */
export interface PanelSummary {
  number: string
  guestName?: string | null
  roomType?: string | null
  room?: string | null
  statusLabel?: string
  arrival?: string | null
  departure?: string | null
  nights?: number
  adults?: number
  children?: number
  total?: number
  paid?: number
}

const GROUPS: { key: string; title: string; icon: LucideIcon }[] = [
  { key: 'stay', title: 'Stay Actions', icon: BedDouble },
  { key: 'billing', title: 'Billing & Payments', icon: CreditCard },
  { key: 'communication', title: 'Communication', icon: MessageSquare },
]

const money = new Intl.NumberFormat('en-IN', { minimumFractionDigits: 2 })

function initials(name?: string | null): string {
  if (!name) return '—'
  return name.trim().split(/\s+/).slice(0, 2).map((w) => w[0]).join('').toUpperCase()
}

export default function RowActionsPanel({
  open, onClose, items, summary,
}: {
  open: boolean
  onClose: () => void
  items: ActionItem[]
  summary: PanelSummary
}) {
  useEffect(() => {
    if (!open) return
    function onKey(e: KeyboardEvent) { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    // The page behind must not scroll while a full-height panel is over it.
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', onKey)
      document.body.style.overflow = prev
    }
  }, [open, onClose])

  if (!open) return null

  // Only what can actually be done on this booking. An action that does not
  // apply is left out rather than greyed: the panel lists up to a dozen
  // actions, and on a closed stay a third of them were unusable, so the list
  // was mostly noise to read past.
  const usable = items.filter((i) => !i.disabled)
  const hero = usable.find((i) => i.tone === 'primary')
  const rest = usable.filter((i) => i !== hero)
  const HeroIcon = hero?.icon

  const stats: { icon: LucideIcon; label: string; value: string }[] = [
    { icon: Moon, label: 'Stay',
      value: summary.nights != null
        ? `${summary.nights} night${summary.nights === 1 ? '' : 's'}` : '—' },
    { icon: Users, label: 'Guests',
      value: (summary.adults ?? 0) + (summary.children ?? 0) > 0
        ? `${(summary.adults ?? 0) + (summary.children ?? 0)} guest`
          + ((summary.adults ?? 0) + (summary.children ?? 0) === 1 ? '' : 's')
        : '—' },
    { icon: Receipt, label: 'Total (₹)',
      value: summary.total != null ? money.format(summary.total) : '—' },
    { icon: Wallet, label: 'Paid (₹)',
      value: summary.paid != null ? money.format(summary.paid) : '—' },
  ]

  return createPortal(
    <div className="fixed inset-0 z-50 flex justify-end"
      role="dialog" aria-modal="true" aria-label="Reservation actions"
      // A portal escapes the DOM but not React: events raised in here still
      // bubble up the *component* tree, which runs through the table row this
      // panel was opened from. That row's onClick reopens the panel, so every
      // click inside -- the close button, the backdrop, any action -- closed
      // it and immediately reopened it, and the close button looked dead.
      // Stopped at the panel's own root so one guard covers all of them.
      onClick={(e) => e.stopPropagation()}>
      <div className="flex-1 bg-slate-900/30" onClick={onClose} />

      <aside className="flex h-full w-full max-w-[420px] flex-col overflow-y-auto bg-white shadow-2xl">
        {/* ---------------------------------------------------- header --- */}
        <div className="flex items-start justify-between px-5 pb-3 pt-5">
          <div>
            <h2 className="text-lg font-bold text-ink">Reservation Actions</h2>
            <p className="text-sm text-slate-400">Manage this booking</p>
          </div>
          <button onClick={onClose} aria-label="Close"
            className="rounded-lg bg-slate-75 p-1.5 text-slate-500 hover:bg-slate-200">
            <X size={16} />
          </button>
        </div>

        {/* ----------------------------------------------- the booking --- */}
        <div className="flex items-start gap-3 px-5 pb-4">
          <span className="grid h-11 w-11 shrink-0 place-items-center rounded-full bg-brand-light text-sm font-semibold text-brand">
            {initials(summary.guestName)}
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex items-start justify-between gap-2">
              <span className="font-bold text-slate-800">{summary.number}</span>
              {summary.statusLabel && (
                <span className="shrink-0 rounded-full bg-emerald-50 px-2.5 py-1 text-xs font-medium text-emerald-600">
                  {summary.statusLabel}
                </span>
              )}
            </div>
            <p className="truncate font-medium text-slate-700">
              {summary.guestName || 'No guest on file'}
            </p>
            <p className="truncate text-sm text-slate-400">
              {[summary.roomType, summary.room && `Room ${summary.room}`]
                .filter(Boolean).join('  ·  ') || 'No room assigned'}
            </p>
            {summary.arrival && (
              <p className="text-sm text-slate-400">
                {fmtDate(summary.arrival)} – {fmtDate(summary.departure)}
                {summary.nights != null && ` (${summary.nights}N)`}
              </p>
            )}
          </div>
        </div>

        {/* ------------------------------------------------- the facts --- */}
        <div className="mx-5 mb-4 grid grid-cols-4 rounded-xl border border-slate-100">
          {stats.map((s, i) => (
            <div key={s.label}
              className={`min-w-0 px-2 py-2.5 ${i > 0 ? 'border-l border-slate-100' : ''}`}>
              <s.icon size={14} className="mb-1 text-slate-400" />
              <p className="text-[10px] text-slate-400">{s.label}</p>
              {/* Four money-ish figures across a 420px panel: at 14px the
                  totals were being clipped to "17,920...." — the one thing on
                  the strip nobody can afford to read wrong. */}
              <p className="truncate text-[13px] font-semibold tabular-nums text-slate-700">
                {s.value}
              </p>
            </div>
          ))}
        </div>

        {/* ------------------------------------------ the main action --- */}
        {hero && (
          <button
            onClick={() => { onClose(); hero.onSelect() }}
            className="mx-5 mb-4 flex items-center gap-3 rounded-xl border border-brand/20 bg-brand-light px-3 py-3 text-left hover:bg-brand-light/70">
            <span className="grid h-10 w-10 shrink-0 place-items-center rounded-lg bg-brand text-white">
              {HeroIcon && <HeroIcon size={18} />}
            </span>
            <span className="min-w-0 flex-1">
              <span className="block font-semibold text-brand">{hero.label}</span>
              {hero.description && (
                <span className="block text-sm text-brand/70">{hero.description}</span>
              )}
            </span>
            <ChevronRight size={18} className="shrink-0 text-brand/60" />
          </button>
        )}

        {/* ---------------------------------------------- the grouped --- */}
        <div className="space-y-4 px-5 pb-6">
          {rest.length === 0 && !hero && (
            <p className="rounded-xl border border-slate-100 px-4 py-8 text-center text-sm text-slate-400">
              Nothing can be done on this booking from here.
            </p>
          )}
          {GROUPS.map(({ key, title, icon: GroupIcon }) => {
            const group = rest.filter((i) => i.group === key)
            if (group.length === 0) return null
            return (
              <section key={key}
                className="overflow-hidden rounded-xl border border-slate-100">
                <header className="flex items-center gap-2 bg-slate-50 px-3 py-2.5">
                  <span className="grid h-6 w-6 place-items-center rounded-md bg-brand text-white">
                    <GroupIcon size={13} />
                  </span>
                  <h3 className="text-sm font-semibold text-slate-700">{title}</h3>
                </header>
                {group.map((it, i) => {
                  const Icon = it.icon
                  const danger = it.tone === 'danger'
                  return (
                    <button
                      key={it.label}
                      onClick={() => { onClose(); it.onSelect() }}
                      className={`flex w-full items-center gap-3 px-3 py-2.5 text-left ${
                        i > 0 ? 'border-t border-slate-100' : ''} ${
                        danger ? 'bg-red-50/60 hover:bg-red-50' : 'hover:bg-slate-50'}`}>
                      <span className={`shrink-0 ${danger ? 'text-red-500' : 'text-slate-400'}`}>
                        {Icon && <Icon size={17} />}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className={`block text-sm font-medium ${
                          danger ? 'text-red-600' : 'text-slate-700'}`}>
                          {it.label}
                        </span>
                        <span className={`block text-xs ${
                          danger ? 'text-red-400' : 'text-slate-400'}`}>
                          {it.description}
                        </span>
                      </span>
                      <ChevronRight size={16}
                        className={`shrink-0 ${danger ? 'text-red-300' : 'text-slate-300'}`} />
                    </button>
                  )
                })}
              </section>
            )
          })}
        </div>
      </aside>
    </div>,
    document.body,
  )
}
