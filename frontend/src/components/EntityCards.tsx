import type { ReactNode } from 'react'
import { Loader2 } from 'lucide-react'

/**
 * The card shell every list-of-things screen shares.
 *
 * Not a generic "render any row as a card" component — that produces cards
 * that show everything and say nothing. This is the *frame*: the grid, the
 * loading and empty states, the click and selected behaviour, the status
 * stripe, and a header that always puts the thing's name where the eye
 * expects it. Each screen decides what facts belong on its own card, because
 * what matters about a guest is not what matters about a work order.
 *
 * Reservations keep their own card (StayCards) rather than being bent onto
 * this: a booking's arrival-nights-departure band is the whole point of that
 * layout and nothing else has one.
 */

export type Tone = 'ok' | 'warn' | 'bad' | 'info' | 'muted'

const STRIPE: Record<Tone, string> = {
  ok: 'bg-emerald-500',
  warn: 'bg-amber-400',
  bad: 'bg-red-400',
  info: 'bg-brand',
  muted: 'bg-slate-300',
}

export interface EntityCard {
  key: string
  /** Shown in the tinted square. Initials, a code, or an icon. */
  badge?: ReactNode
  title: ReactNode
  subtitle?: ReactNode
  /** The rendered status pill, so the card and the table agree. */
  status?: ReactNode
  /** Colour of the edge stripe. Status at a glance, without reading. */
  tone?: Tone
  /** Label/value pairs, in the order they matter. A null value is skipped
   *  rather than shown as a dash — a labelled blank reads as data somebody
   *  failed to fill in. */
  facts?: { label: string; value: ReactNode }[]
  /** Anything that belongs below a rule: money, a progress bar, chips. */
  footer?: ReactNode
  actions?: ReactNode
  onClick?: () => void
  selected?: boolean
}

export default function EntityCards({
  cards, loading, empty, columns = 3,
}: {
  cards: EntityCard[]
  loading?: boolean
  empty: ReactNode
  /** Wider cards for lists whose facts are long. */
  columns?: 2 | 3
}) {
  if (loading) {
    return (
      <div className="grid place-items-center rounded-2xl border border-slate-100 bg-white py-16">
        <Loader2 className="animate-spin text-slate-400" />
      </div>
    )
  }
  if (cards.length === 0) {
    return (
      <div className="rounded-2xl border border-slate-100 bg-white px-4 py-16 text-center text-sm text-slate-500">
        {empty}
      </div>
    )
  }

  return (
    <div className={`grid gap-4 sm:grid-cols-2 ${columns === 3 ? 'xl:grid-cols-3' : ''}`}>
      {cards.map((c) => (
        <article key={c.key} onClick={c.onClick}
          className={`relative flex flex-col overflow-hidden rounded-2xl border bg-white shadow-sm transition-colors ${
            c.onClick ? 'cursor-pointer' : ''} ${
            c.selected
              ? 'border-brand ring-1 ring-brand'
              : 'border-slate-100 hover:border-slate-200'}`}>

          {c.tone && (
            <span className={`absolute left-0 top-0 h-14 w-1.5 rounded-r ${STRIPE[c.tone]}`}
              aria-hidden="true" />
          )}

          <div className="flex items-start gap-3 px-4 pt-4">
            {c.badge && (
              <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-brand-light text-sm font-semibold text-brand">
                {c.badge}
              </span>
            )}
            <div className="min-w-0 flex-1">
              <div className="truncate font-semibold text-ink">{c.title}</div>
              {c.subtitle && (
                <div className="truncate text-xs text-slate-400">{c.subtitle}</div>
              )}
            </div>
            <div className="flex shrink-0 items-center gap-1">
              {c.status}
              {c.actions}
            </div>
          </div>

          {!!c.facts?.length && (
            <dl className="mt-3 space-y-2 px-4 text-sm">
              {c.facts.filter((f) => f.value != null && f.value !== '').map((f) => (
                <div key={f.label}>
                  <dt className="text-xs font-semibold uppercase tracking-wide text-slate-400">
                    {f.label}
                  </dt>
                  <dd className="text-slate-600">{f.value}</dd>
                </div>
              ))}
            </dl>
          )}

          {/* mt-auto, so every card in a row ends on the same line whatever
              the content above did to the height. */}
          <div className="mt-auto px-4 pb-4 pt-3">{c.footer}</div>
        </article>
      ))}
    </div>
  )
}
