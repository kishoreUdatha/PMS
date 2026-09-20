/**
 * One button that does the obvious thing, with its variants behind a caret.
 *
 * A folio toolbar had four buttons and a "More" menu holding eight more, and
 * the eight were not spare — Transfer, Split and Cut are things a desk does
 * every day. They were in "More" because there was nowhere else to put them,
 * which is how a menu named after nothing in particular ends up holding the
 * work.
 *
 * A split button fixes the naming problem rather than the counting one. "Add
 * payment" takes a payment when you click it; the caret beside it offers the
 * refund, which is the same act pointing the other way. Nothing is hidden
 * behind a word as vague as "more" — every item sits under the heading it
 * belongs to.
 *
 * It is deliberately two elements and not one. A single control that behaves
 * differently depending on which end of it you hit is unreachable from a
 * keyboard: there is one tab stop and two actions. Two buttons side by side
 * give Tab somewhere to land for each, and the border between them is drawn
 * rather than implied, so what looks like two targets is two targets.
 */
import type { LucideIcon } from 'lucide-react'
import { ChevronDown } from 'lucide-react'
import { Menu, type ActionItem } from './menu'

export default function SplitButton({
  label, icon: Icon, onClick, items, primary = false, disabled = false,
  // 248, because at 232 exactly one description wrapped to a second line and
  // that item stood 16px taller than its neighbours. One ragged row in a list
  // of five reads as a mistake, and sixteen pixels of width is cheaper than
  // editing the sentence down to fit.
  hint, menuWidth = 248,
}: {
  label: string
  icon?: LucideIcon
  /** What a plain click does — the thing the button is named after. */
  onClick: () => void
  /** The variants, behind the caret. An empty list renders a plain button. */
  items: ActionItem[]
  primary?: boolean
  disabled?: boolean
  hint?: string
  menuWidth?: number
}) {
  // px-2.5, not px-3. Three of these plus a search field and a filter come
  // to 768px in a 738px column at the commonest laptop width, and the row
  // wrapped — putting the filter on a line of its own, right-aligned, which
  // reads as something that fell off rather than something that was placed.
  // Two pixels a side across six buttons is what closed the gap.
  const base = 'flex items-center gap-1.5 px-2.5 py-2 text-sm font-semibold outline-none transition-colors disabled:cursor-not-allowed disabled:opacity-40'
  const skin = primary
    ? 'bg-brand text-white enabled:hover:bg-brand-dark focus-visible:ring-2 focus-visible:ring-brand/40'
    : 'bg-white text-slate-600 enabled:hover:bg-slate-50 focus-visible:ring-2 focus-visible:ring-brand/30'
  const edge = primary ? 'border-brand' : 'border-slate-200'
  // The seam. On the brand fill a slate border would disappear, so it is drawn
  // in the fill's own colour lightened, which reads on both skins.
  const seam = primary ? 'border-white/30' : 'border-slate-200'

  // No variants worth offering — a caret onto an empty menu is a control that
  // lies about having something behind it.
  if (items.length === 0) {
    return (
      <button type="button" onClick={onClick} disabled={disabled} title={hint}
        className={`${base} ${skin} shrink-0 rounded-xl border ${edge}`}>
        {Icon && <Icon size={15} />} {label}
      </button>
    )
  }

  return (
    <span className={`inline-flex shrink-0 rounded-xl border ${edge} ${
      primary ? '' : 'bg-white'}`}>
      <button type="button" onClick={onClick} disabled={disabled} title={hint}
        className={`${base} ${skin} rounded-l-[0.7rem]`}>
        {Icon && <Icon size={15} />} {label}
      </button>
      <Menu items={items} width={menuWidth} align="right" allowFlip
        trigger={(props, open) => (
          <button
            {...props}
            type="button"
            aria-label={`More ${label.toLowerCase()} options`}
            className={`${base} ${skin} rounded-r-[0.7rem] border-l px-1.5 ${seam} ${
              open ? (primary ? 'bg-brand-dark' : 'bg-slate-50') : ''}`}>
            <ChevronDown size={14}
              className={`transition-transform ${open ? 'rotate-180' : ''}`} />
          </button>
        )} />
    </span>
  )
}
