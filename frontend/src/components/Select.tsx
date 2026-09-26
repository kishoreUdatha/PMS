import {
  Children, isValidElement, useEffect, useId, useLayoutEffect, useMemo, useRef,
  useState, type ReactNode,
} from 'react'
import { createPortal } from 'react-dom'
import { Check, ChevronDown } from 'lucide-react'

/**
 * A drop-in replacement for `<select>` that the app draws itself.
 *
 * On Windows the operating system paints the open list of a native select, so
 * the brand palette stopped at the closed control and every highlighted option
 * was system blue. CSS cannot reach inside it — `option { background }` is
 * ignored — so the only way to brand it is to draw it.
 *
 * **It is deliberately shaped like the thing it replaces.** It takes the same
 * `<option>` children, and calls `onChange` with `{ target: { value } }`, so
 * every handler written for a native select keeps working untouched. That
 * matters at this size: there are well over a hundred of these, and a change
 * that also rewrote every handler would be a change nobody could review.
 *
 * What it keeps, because losing any of it would be a bad trade for a bit of
 * colour:
 *
 * * **Type-ahead** — typing "del" jumps to Deluxe, as a native select does.
 * * **Keyboard** — Up/Down, Enter, Escape, Home/End, and Tab takes the
 *   highlighted row and then moves focus on as usual, matching native
 *   behaviour. Escape closes only the list, never an enclosing dialog.
 * * **Disabled options stay listed**, greyed, because an option missing
 *   entirely is a worse answer than one you can see you cannot pick.
 * * The WAI-ARIA select-only combobox pattern: the trigger is
 *   `role="combobox"` with `aria-expanded`, `aria-controls` and
 *   `aria-activedescendant`; the list is a labelled `role="listbox"` of
 *   `role="option"` rows with ids. Focus stays on the trigger throughout.
 * * **Forms** — given `name` or `required`, a visually hidden native input
 *   carries the value, so the browser's required check and a plain form
 *   submission behave as they would for a native select.
 *
 * What it does not do, on purpose: no `multiple`, no `optgroup`, no `size`.
 * Nothing in this app uses them, and guessing at semantics nobody needs is
 * how a component like this turns into a liability.
 */

interface Opt {
  value: string
  /** Full text, shown in the open list. */
  label: string
  /**
   * What the closed control shows, when that should be shorter than the row.
   * A row often carries a hint that helps you choose -- "Family Room -- 4
   * left" -- which reads as noise once chosen, and squeezes out the part that
   * identifies the choice. Set `data-label` on the option to keep the hint in
   * the list and off the control.
   */
  short: string
  /**
   * A hint shown right-aligned and muted, via `data-hint`. Kept out of the
   * label so it cannot push the row wider than the field: the name gets the
   * space it needs and the hint sits in the gutter, which is where the eye
   * looks for "how many" anyway.
   */
  hint: string | null
  disabled: boolean
}

/** Flatten the `<option>` children, including those from `.map()` and `&&`. */
function readOptions(children: ReactNode): Opt[] {
  const out: Opt[] = []
  Children.toArray(children).forEach((child) => {
    if (!isValidElement(child)) return
    const props = child.props as {
      value?: string | number
      disabled?: boolean
      children?: ReactNode
      'data-label'?: string
      'data-hint'?: string
    }
    // Text of the option, including pieces stitched from expressions.
    const label = Children.toArray(props.children)
      .map((c) => (typeof c === 'string' || typeof c === 'number' ? String(c) : ''))
      .join('')
      .replace(/\s+/g, ' ')
      .trim()
    out.push({
      // A native option with no value attribute uses its text as the value.
      value: props.value === undefined ? label : String(props.value),
      label,
      short: props['data-label'] ?? label,
      hint: props['data-hint'] ?? null,
      disabled: Boolean(props.disabled),
    })
  })
  return out
}

type ButtonRest = Omit<
  React.ComponentPropsWithoutRef<'button'>,
  'onChange' | 'value' | 'children' | 'type' | 'onClick' | 'onKeyDown'
>

export default function Select({
  value = '', onChange, children, className = '', required, name,
  blankIsChoice = false, ...rest
}: {
  /** Optional, like a native select used purely to display a fixed choice. */
  value?: string | number
  /**
   * Typed exactly as a native select's, so every existing handler assigns
   * without change — including the ones shared between inputs and selects.
   * What is handed to it is a stand-in carrying `target.value`, which is the
   * only thing any of them read.
   */
  onChange?: (e: React.ChangeEvent<HTMLSelectElement>) => void
  children: ReactNode
  className?: string
  required?: boolean
  /**
   * Whether an empty value is a real answer rather than an unfilled field.
   *
   * A form's select shows placeholder grey until somebody chooses, which is
   * right: "" means unanswered. A *filter's* select is the opposite — "All
   * Room Types" is the answer, and the default one. Greying it there says the
   * control is empty or disabled when it is neither, and on a filter bar it
   * left two of the five controls a shade lighter than the rest of the row.
   */
  blankIsChoice?: boolean
} & ButtonRest) {
  const options = useMemo(() => readOptions(children), [children])
  const current = String(value ?? '')
  const chosen = options.find((o) => o.value === current) ?? null

  const btnRef = useRef<HTMLButtonElement>(null)
  const listRef = useRef<HTMLDivElement>(null)
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)
  const [pos, setPos] = useState<{
    top?: number; bottom?: number; left: number
    width: number; maxHeight: number
  } | null>(null)
  const typed = useRef({ text: '', at: 0 })
  const uid = useId()
  const triggerId = rest.id ?? `${uid}-trigger`
  const listboxId = `${uid}-listbox`
  const optionId = (i: number) => `${uid}-opt-${i}`

  // Measured, never assumed: these sit in panels and tables that scroll, and
  // the room below the control decides whether the list opens down or up.
  useLayoutEffect(() => {
    if (!open) return
    const place = () => {
      const b = btnRef.current?.getBoundingClientRect()
      if (!b) return
      // Exactly the width of the control. A list wider than the field it
      // belongs to reads as misaligned -- its right edge juts past the column
      // and lines up with nothing. Rows earn their space instead: a hint sits
      // right-aligned and compact rather than lengthening the row.
      const width = b.width
      const left = Math.max(8, Math.min(b.left, window.innerWidth - 8 - width))
      const below = window.innerHeight - b.bottom - 12
      const above = b.top - 12
      // How tall this list actually wants to be. It is already in the DOM --
      // rendered hidden for exactly this measurement -- so this is the real
      // height, not the 288 cap. Comparing against the cap made a four-row
      // list flip upward over the form whenever there was less than 288px
      // below it, even with room to spare: it needs 186, not 288.
      const wants = Math.min(288, listRef.current?.scrollHeight || 288)
      // Down whenever it fits; otherwise whichever side has more room.
      const down = below >= wants || below >= above
      setPos({
        // Opening upward anchors the list's *bottom* to the control. Pinning
        // its top instead meant a two-item list was positioned as though it
        // were full height, and floated halfway up the page away from the
        // control it belongs to.
        ...(down
          ? { top: b.bottom + 4 }
          : { bottom: window.innerHeight - b.top + 4 }),
        left,
        width,
        maxHeight: Math.min(288, down ? below : above),
      })
    }
    place()
    window.addEventListener('scroll', place, true)
    window.addEventListener('resize', place)
    return () => {
      window.removeEventListener('scroll', place, true)
      window.removeEventListener('resize', place)
    }
  }, [open])

  useEffect(() => {
    if (!open) return
    const away = (e: MouseEvent) => {
      if (!listRef.current?.contains(e.target as Node)
          && !btnRef.current?.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', away)
    return () => document.removeEventListener('mousedown', away)
  }, [open])

  useEffect(() => {
    if (!open) return
    listRef.current?.querySelector(`[data-i="${active}"]`)
      ?.scrollIntoView({ block: 'nearest' })
  }, [active, open])

  const openAt = () => {
    const i = options.findIndex((o) => o.value === current)
    setActive(i >= 0 ? i : 0)
    setPos(null)
    setOpen(true)
  }

  const step = (delta: number) => {
    if (options.length === 0) return
    let i = active
    for (let n = 0; n < options.length; n += 1) {
      i = (i + delta + options.length) % options.length
      if (!options[i].disabled) break
    }
    setActive(i)
  }

  const take = (i: number, refocus = true) => {
    const opt = options[i]
    if (!opt || opt.disabled) return
    // Only `target.value` is ever read by a select handler; the cast says so
    // rather than pretending to build a whole synthetic DOM event.
    onChange?.({ target: { value: opt.value } } as unknown as
      React.ChangeEvent<HTMLSelectElement>)
    setOpen(false)
    if (refocus) btnRef.current?.focus()
  }

  const onKey = (e: React.KeyboardEvent) => {
    if (!open) {
      if (['Enter', ' ', 'ArrowDown', 'ArrowUp'].includes(e.key)) {
        e.preventDefault()
        openAt()
      }
      return
    }
    // Escape belongs to the list while it is open. Without stopping it here
    // the same keypress also reached the dialog around the field and closed
    // that, throwing away a half-filled form.
    if (e.key === 'Escape') {
      e.preventDefault(); e.stopPropagation(); setOpen(false); return
    }
    if (e.key === 'ArrowDown') { e.preventDefault(); step(1); return }
    if (e.key === 'ArrowUp') { e.preventDefault(); step(-1); return }
    if (e.key === 'Home') { e.preventDefault(); setActive(0); return }
    if (e.key === 'End') { e.preventDefault(); setActive(options.length - 1); return }
    if (e.key === 'Enter') { e.preventDefault(); take(active); return }
    // Tab takes the highlighted row and closes, then lets the browser move
    // focus on. Swallowing it trapped keyboard users on an open list.
    if (e.key === 'Tab') { take(active, false); setOpen(false); return }

    if (e.key.length === 1 && /\S/.test(e.key)) {
      const now = Date.now()
      typed.current = {
        text: now - typed.current.at > 800 ? e.key : typed.current.text + e.key,
        at: now,
      }
      const hit = options.findIndex((o) =>
        !o.disabled && o.label.toLowerCase().startsWith(typed.current.text.toLowerCase()))
      if (hit >= 0) setActive(hit)
    }
  }

  return (
    <>
      {/* Everything else the caller passed — onWheel, onBlur, title, id,
          tabIndex — goes straight through, because a select in a form is
          wired up in ways this component has no business knowing about. */}
      <button {...rest} id={triggerId} ref={btnRef} type="button"
        role="combobox" aria-required={required}
        aria-haspopup="listbox" aria-expanded={open}
        aria-controls={open ? listboxId : undefined}
        aria-activedescendant={open && options[active] ? optionId(active) : undefined}
        onClick={() => (open ? setOpen(false) : openAt())}
        onKeyDown={onKey}
        // The caller's classes still describe the control: they were written
        // for a select in that exact spot, and re-deriving widths and borders
        // per call site would be a hundred small regressions.
        className={`${className} flex items-center gap-2 text-left ${
          rest.disabled ? 'cursor-not-allowed' : 'cursor-pointer'} ${
          open ? 'border-brand' : ''}`}>
        {/* Slate when something is chosen, not the user agent's black. The
            trigger is a button, so it never picked up the colour set on
            input/select in index.css -- leaving the chosen value the darkest
            text on any screen with a dropdown, darker than its own heading.
            Fixed here so every Select in the app is right at once. */}
        <span className={`min-w-0 flex-1 truncate ${
          chosen && chosen.value !== '' ? 'text-slate-800'
            // The row's own text colour. Stated here rather than left to the
            // caller's className because this span always beats the button's
            // colour by inheritance anyway -- so a call site that set one was
            // never being read, and relying on it would have been a silent
            // difference between the screens that set a colour and those that
            // did not.
            : blankIsChoice ? 'text-slate-600'
            : 'text-slate-400'}`}>
          {chosen ? chosen.short : ''}
        </span>
        <ChevronDown size={15}
          className={`shrink-0 text-slate-400 ${open ? 'rotate-180' : ''}`} />
      </button>

      {/* The value as a form sees it. A button takes no part in validation or
          submission, so `required` on its own was only ever a hint; this input
          is what the browser checks and posts. Visually hidden, out of the tab
          order, and if validation tries to focus it, focus goes to the
          control the user can actually operate. */}
      {(name !== undefined || required) && (
        <input tabIndex={-1} aria-hidden="true" name={name} required={required}
          value={current} onChange={() => undefined}
          disabled={rest.disabled}
          onFocus={() => btnRef.current?.focus()}
          style={{ position: 'absolute', width: 1, height: 1, padding: 0,
                   margin: -1, overflow: 'hidden', clip: 'rect(0 0 0 0)',
                   whiteSpace: 'nowrap', border: 0, opacity: 0 }} />
      )}

      {open && createPortal(
        // Rendered as soon as the control opens, but hidden until measured:
        // its own height decides whether it opens down or up, and that cannot
        // be known until it exists. Hidden rather than unmounted so there is
        // no flash of a list in the wrong place.
        <div ref={listRef} id={listboxId} role="listbox" tabIndex={-1}
          aria-label={rest['aria-label']}
          aria-labelledby={rest['aria-label'] ? undefined
            : rest['aria-labelledby'] ?? triggerId}
          style={{ position: 'fixed', top: pos?.top, bottom: pos?.bottom,
                   left: pos?.left ?? 0, width: pos?.width,
                   maxHeight: pos?.maxHeight,
                   visibility: pos ? 'visible' : 'hidden', zIndex: 70 }}
          className="scroll-slim overflow-y-auto rounded-xl border border-slate-200 bg-white py-1 shadow-xl">
          {options.length === 0 && (
            <p className="px-3 py-4 text-center text-sm text-slate-400">
              Nothing to choose from.
            </p>
          )}
          {options.map((o, i) => (
            <div key={`${o.value}-${i}`} data-i={i} id={optionId(i)}
              role="option" aria-selected={o.value === current}
              aria-disabled={o.disabled || undefined}
              onMouseEnter={() => !o.disabled && setActive(i)}
              // Keep focus on the trigger, which owns the keyboard and the
              // active-descendant; a click on a row must not steal it.
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => take(i)}
              // The full text on hover: the row is only as wide as the
              // field, so a long name can still be clipped.
              title={o.hint ? `${o.label} — ${o.hint}` : o.label}
              className={`flex w-full items-center gap-2 px-3 py-2 text-left text-sm ${
                o.disabled ? 'cursor-not-allowed text-slate-300'
                  : active === i ? 'cursor-pointer bg-brand/10 text-slate-800'
                  : 'cursor-pointer text-slate-700'}`}>
              <span className="min-w-0 flex-1 truncate">
                {o.label || <span className="text-slate-300">—</span>}
              </span>
              {o.hint && (
                <span className={`shrink-0 text-[11px] ${
                  o.disabled ? 'text-slate-300' : 'text-slate-400'}`}>
                  {o.hint}
                </span>
              )}
              <Check size={14}
                className={o.value === current ? 'shrink-0 text-brand'
                                               : 'shrink-0 opacity-0'} />
            </div>
          ))}
        </div>,
        document.body,
      )}
    </>
  )
}
