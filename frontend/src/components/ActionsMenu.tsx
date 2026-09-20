/** The row's actions, behind one button.
 *
 * A stay list carries up to four things a desk might do to a booking, and
 * spelling them all out put three or four controls in every row — the widest
 * column on the screen was the one with no data in it. They collapse to a
 * single vertical-dots button here.
 *
 * The placement, keyboard handling and item styling moved to `menu.tsx` when
 * the split buttons needed the same popup off a different trigger. This is now
 * that menu with a dots button in front of it; the props are unchanged, so the
 * hundred-odd call sites did not have to know.
 */
import { MoreVertical } from 'lucide-react'
import { Menu, type ActionItem } from './menu'

export type { ActionItem }

export default function ActionsMenu({ items, label = 'Actions' }: {
  items: ActionItem[]
  label?: string
}) {
  if (items.length === 0) return null

  return (
    <Menu items={items} width={200} align="right"
      trigger={(props, open) => (
        <button
          {...props}
          type="button"
          aria-label={label}
          className={`grid h-8 w-8 place-items-center rounded-lg border text-slate-500 outline-none hover:bg-slate-50 hover:text-slate-700 focus-visible:ring-2 focus-visible:ring-brand/30 ${
            open ? 'border-slate-300 bg-slate-50' : 'border-slate-200'}`}>
          <MoreVertical size={16} />
        </button>
      )} />
  )
}
