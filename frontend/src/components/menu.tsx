/**
 * One menu, several triggers.
 *
 * There was a good menu on this codebase already — measured placement, arrow
 * keys, Escape, grouping — and it was welded to a vertical-dots button. The
 * split buttons this screen now needs want the same list hanging off a caret
 * instead, and the honest way to get that is to lift the machinery out rather
 * than write a second copy that drifts from the first.
 *
 * So the popup and its behaviour live here, and the trigger is a render prop.
 * ActionsMenu passes a dots button; SplitButton passes a button pair. Both get
 * identical keyboard handling, identical placement, identical item styling —
 * which is the point: a menu that behaves differently depending on what opened
 * it is two things to learn instead of one.
 */
import {
  useCallback, useEffect, useLayoutEffect, useRef, useState,
  type KeyboardEvent as ReactKeyboardEvent, type ReactNode, type RefObject,
} from 'react'
import { createPortal } from 'react-dom'
import type { LucideIcon } from 'lucide-react'

export interface ActionItem {
  label: string
  icon?: LucideIcon
  onSelect: () => void
  /** Greyed out, with the reason offered on hover. */
  disabled?: boolean
  hint?: string
  /** 'primary' is the one the row exists for; 'danger' is destructive. */
  tone?: 'default' | 'primary' | 'danger'
  /** Items are drawn in group order with a rule between groups. Ten flat
   *  items is a wall; the same ten in four groups is scannable. */
  group?: string
  /** One line saying what the action does, under the label. Worth the second
   *  line where the label alone would leave somebody guessing — "Cut folio"
   *  reads as destructive until you know it only closes one and opens another. */
  description?: string
}

/** What the trigger spreads onto whichever element opens the menu. */
export interface TriggerProps {
  ref: RefObject<HTMLButtonElement>
  'aria-haspopup': 'menu'
  'aria-expanded': boolean
  onClick: (e: { stopPropagation: () => void }) => void
  onKeyDown: (e: ReactKeyboardEvent) => void
}

export function Menu({
  items, width = 200, align = 'right', allowFlip = false, trigger,
}: {
  items: ActionItem[]
  width?: number
  /** Which edge of the trigger the menu lines up with. */
  align?: 'left' | 'right'
  /** May open upwards when there is more room above than below.
   *
   *  Off for menus that repeat down a list — see the placement note below.
   *  On for a button that sits in one fixed place, where there is no row above
   *  and below to be inconsistent with, and where the alternative on a short
   *  window is a five-item menu scrolling inside 120 pixels. */
  allowFlip?: boolean
  trigger: (props: TriggerProps, open: boolean) => ReactNode
}) {
  const btnRef = useRef<HTMLButtonElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const [open, setOpen] = useState(false)
  const [pos, setPos] =
    useState<{ top: number; left: number; maxHeight: number } | null>(null)

  // Measured, not estimated. An earlier version guessed the height from the
  // item count, flipped the menu above the button when it would not fit
  // below, and never checked the result was on screen — a ten-item menu on a
  // row near the top opened at top: -58, with its first items clipped off the
  // top of the window. So: render it hidden, measure it, then place it.
  const place = useCallback(() => {
    if (!btnRef.current || !menuRef.current) return
    const b = btnRef.current.getBoundingClientRect()
    // scrollHeight, not offsetHeight: once a tall menu has been clamped, its
    // rendered height is the clamp, and measuring that on the next placement
    // would make it resize itself smaller on every scroll.
    const height = menuRef.current.scrollHeight
    const GAP = 4
    const EDGE = 8

    // Below the button by default, and for a menu that repeats down a list,
    // always. A menu that chooses its own side moves depending on where the
    // row happens to sit, so the same action is in a different place on each
    // one; anchoring it under the button means the desk learns one position.
    // Where that leaves less room than the menu needs, it scrolls inside
    // itself rather than growing off the bottom of the window.
    //
    // A toolbar button is the exception, and `allowFlip` is how it says so. It
    // sits in one fixed place, so opening upwards there is not inconsistent
    // with anything — and on a short window the rule as written put a
    // five-item menu inside 120 pixels and asked people to scroll it.
    const below = Math.max(0, window.innerHeight - b.bottom - GAP - EDGE)
    const above = Math.max(0, b.top - GAP - EDGE)
    const flip = allowFlip && height > below && above > below

    const wanted = align === 'right' ? b.right - width : b.left
    setPos({
      top: flip
        ? Math.max(EDGE, b.top - GAP - Math.min(height, above))
        : b.bottom + GAP,
      left: Math.max(EDGE, Math.min(wanted, window.innerWidth - width - EDGE)),
      maxHeight: Math.min(height, flip ? above : below),
    })
  }, [align, width, allowFlip])

  useLayoutEffect(() => {
    if (open) place()
  }, [open, items.length, place])

  // Focus moves into the menu on open, so the keyboard lands where the eye
  // does.
  //
  // After placement, not on open — and that distinction is the whole of it.
  // The menu renders `visibility: hidden` for the frame it is being measured
  // in, and a hidden element cannot take focus: `.focus()` on it does nothing
  // and reports nothing. Written the obvious way, against `open`, this ran in
  // exactly that frame, so opening a menu from the keyboard left focus sitting
  // on the trigger with the menu showing beside it. Nothing threw, the
  // typecheck was clean, and it took pressing the key in a browser to see.
  //
  // The guard is a ref rather than a dependency on `pos`, because `pos` is
  // rewritten on every scroll frame while the menu is open, and focus should
  // move once — when it first becomes visible — not follow the page.
  const focused = useRef(false)
  useEffect(() => {
    if (!open) { focused.current = false; return }
    if (!pos || focused.current) return
    focused.current = true
    menuRef.current?.querySelector<HTMLButtonElement>(
      '[role="menuitem"]:not([disabled])')?.focus()
  }, [open, pos])

  useEffect(() => {
    if (!open) return
    function onDown(e: MouseEvent) {
      const t = e.target as Node
      if (!menuRef.current?.contains(t) && !btnRef.current?.contains(t)) {
        setOpen(false)
      }
    }
    function onKey(e: globalThis.KeyboardEvent) {
      if (e.key === 'Escape') { setOpen(false); btnRef.current?.focus(); return }
      // Tabbing out closes it, rather than leaving a menu hanging open over a
      // page whose focus has moved on.
      if (e.key === 'Tab') { setOpen(false); return }
      if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(e.key)) return
      // A front desk lives on the keyboard, and a menu that can only be opened
      // and dismissed with one is not much use. Disabled items are skipped
      // rather than swallowing the focus.
      const all = [...(menuRef.current?.querySelectorAll<HTMLButtonElement>(
        '[role="menuitem"]:not([disabled])') ?? [])]
      if (all.length === 0) return
      e.preventDefault()
      const at = all.indexOf(document.activeElement as HTMLButtonElement)
      const next =
        e.key === 'Home' ? 0
          : e.key === 'End' ? all.length - 1
            : e.key === 'ArrowDown' ? (at + 1) % all.length
              : (at <= 0 ? all.length : at) - 1
      all[next]?.focus()
    }
    // The menu is fixed, so a scroll would leave it behind. It used to close on
    // any scroll or resize; now that placement is measured it simply follows
    // the button, throttled to a frame so a scroll cannot flood React with
    // state updates.
    let frame = 0
    function onScroll() {
      if (frame) return
      frame = requestAnimationFrame(() => { frame = 0; place() })
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    window.addEventListener('scroll', onScroll, true)
    window.addEventListener('resize', onScroll)
    return () => {
      if (frame) cancelAnimationFrame(frame)
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
      window.removeEventListener('scroll', onScroll, true)
      window.removeEventListener('resize', onScroll)
    }
  }, [open, place])

  const triggerProps: TriggerProps = {
    ref: btnRef,
    'aria-haspopup': 'menu',
    'aria-expanded': open,
    onClick: (e) => {
      e.stopPropagation()
      setPos(null)          // re-measure on every open
      setOpen((v) => !v)
    },
    // Down-arrow opens the menu from the closed trigger, which is what every
    // other menu button on a desktop does.
    onKeyDown: (e) => {
      if (e.key === 'ArrowDown' && !open) {
        e.preventDefault(); setPos(null); setOpen(true)
      }
    },
  }

  return (
    <>
      {trigger(triggerProps, open)}
      {open && createPortal(
        <div
          ref={menuRef}
          role="menu"
          style={{
            top: pos?.top ?? 0,
            left: pos?.left ?? 0,
            width,
            maxHeight: pos?.maxHeight,
            // Hidden for the single frame between mounting and being measured,
            // so it is never seen in the wrong place.
            visibility: pos ? 'visible' : 'hidden',
          }}
          className="fixed z-50 overflow-y-auto overscroll-contain rounded-xl border border-slate-200 bg-white py-1.5 shadow-lg"
          onClick={(e) => e.stopPropagation()}>
          {items.map((it, i) => {
            const Icon = it.icon
            const newGroup = i > 0 && it.group !== items[i - 1].group
            const tone = it.tone === 'danger'
              ? 'text-red-600 enabled:hover:bg-red-50 enabled:focus-visible:bg-red-50'
              : it.tone === 'primary'
                ? 'font-semibold text-brand enabled:hover:bg-brand-light enabled:focus-visible:bg-brand-light'
                : 'text-slate-600 enabled:hover:bg-slate-50 enabled:focus-visible:bg-slate-50'
            return (
              <div key={it.label}>
                {newGroup && (
                  <div className="my-1.5 border-t border-slate-100" role="separator" />
                )}
                <button
                  role="menuitem"
                  disabled={it.disabled}
                  title={it.hint}
                  onClick={() => { setOpen(false); it.onSelect() }}
                  className={`flex w-full items-start gap-2.5 px-3 py-1.5 text-left text-sm outline-none disabled:cursor-not-allowed disabled:opacity-40 ${tone}`}>
                  {Icon && <Icon size={15} className="mt-0.5 shrink-0" />}
                  <span className="min-w-0">
                    {it.label}
                    {it.description && (
                      <span className="mt-0.5 block text-xs font-normal leading-snug text-slate-400">
                        {it.description}
                      </span>
                    )}
                  </span>
                </button>
              </div>
            )
          })}
        </div>,
        document.body,
      )}
    </>
  )
}
