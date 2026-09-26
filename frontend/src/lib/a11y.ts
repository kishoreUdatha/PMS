import type { KeyboardEvent } from 'react'

/** Props that make a clickable table row reachable and operable by keyboard.
 *
 *  A `<tr onClick>` works for a mouse and for nobody else: it is not in the
 *  tab order and Enter does nothing. Spread this onto the row instead of a bare
 *  onClick. `link` for rows that open another screen, `button` for rows that
 *  select something on this one. Keys pressed on a control inside the row
 *  (a checkbox, a menu button) are left to that control. */
export function clickableRow(
  onActivate: () => void, role: 'button' | 'link' = 'button',
) {
  return {
    role,
    tabIndex: 0,
    onClick: onActivate,
    onKeyDown: (e: KeyboardEvent<HTMLElement>) => {
      if (e.target !== e.currentTarget) return
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault()
        onActivate()
      }
    },
  }
}
