import { Link, useLocation } from 'react-router-dom'
import { sameThing } from '../lib/crumbs'

export type Crumb = { label: string; to?: string }

/** The trail above a page title.
 *
 *  Every screen used to hand-roll this, in five different shapes, and most of
 *  them ended the trail with the title of the page it sat on -- "Revenue ›
 *  Rate Rules" above the heading "Rate Rules". Two labels, one screen. The
 *  same two rules the platform console uses apply here:
 *
 *  1. A trailing crumb that only restates the title is dropped. Only when it
 *     is plain text: a last crumb carrying a `to` is a link somebody can
 *     follow, and losing a link is worse than repeating a word.
 *  2. A crumb pointing at the page you are already on is not a way back to
 *     anywhere, so it goes too. If nothing survives, no trail is rendered --
 *     a top-level screen has nothing above it.
 *
 *  What counts as a repeat lives in lib/crumbs, shared with the platform
 *  console so the two trails cannot drift apart.
 */

export function Crumbs({ trail, title }: { trail: Crumb[]; title?: string }) {
  const { pathname, search } = useLocation()
  const here = pathname + search

  const items = trail
    .filter((c, i, all) => !(
      i === all.length - 1 && !c.to && title && sameThing(title, c.label)))
    .filter((c) => c.to !== pathname && c.to !== here)

  if (items.length === 0) return null
  return (
    <p className="text-sm text-slate-400">
      {items.map((c, i) => (
        <span key={c.label}>
          {i > 0 && <span className="px-2">&rsaquo;</span>}
          {c.to
            ? <Link to={c.to} className="text-slate-500 hover:text-brand">{c.label}</Link>
            : <span className="font-semibold text-slate-700">{c.label}</span>}
        </span>
      ))}
    </p>
  )
}
