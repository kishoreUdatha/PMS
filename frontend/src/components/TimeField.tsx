import { useRef } from 'react'
import { Clock } from 'lucide-react'
import { fmtClock } from '../lib/dates'

/** A time input that reads the same way as every time the app prints.
 *
 * The twin of `DateField`, and it exists for the same reason: `<input
 * type="time">` renders its text in the *browser's* locale, which the page
 * cannot override. On a machine set to en-US it shows "11:00 AM" while every
 * other time in this product — night audit stamps, cashier shifts, folio
 * entries, the housekeeping header — is written 11:00.
 *
 * That mattered more once the app settled on 24-hour everywhere: these were
 * the last controls still saying AM and PM, and they were sitting directly
 * beside dates that had already been fixed.
 *
 * So the native control is kept for the picker and the keyboard, but made
 * transparent and covered with our own text. The value it carries is
 * unchanged — HTML time inputs are already "HH:MM" in 24-hour, whatever the
 * browser chooses to draw — so nothing on the server sees a difference.
 */
export default function TimeField({
  value, onChange, className = '', min, max, step, disabled,
  placeholder = 'Pick a time', label = 'Time', title,
}: {
  value: string
  onChange: (v: string) => void
  className?: string
  min?: string
  max?: string
  step?: number
  disabled?: boolean
  placeholder?: string
  /** Accessible name: the visible text is a sibling span, so the input
   *  needs its own or it reaches a screen reader unnamed. */
  label?: string
  title?: string
}) {
  const ref = useRef<HTMLInputElement>(null)

  function open() {
    const el = ref.current
    if (!el || disabled) return
    // showPicker throws if the browser refuses; focusing still lets the user
    // type or open the picker with the keyboard.
    try { el.showPicker() } catch { el.focus() }
  }

  const shown = fmtClock(value)

  return (
    <span className={`relative inline-flex items-center gap-2 rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700 focus-within:border-brand ${disabled ? 'opacity-60' : 'cursor-pointer'} ${className}`}
      title={title}
      onClick={open}>
      <Clock size={14} className="shrink-0 text-slate-400" />
      <span className={shown ? '' : 'text-slate-400'}>{shown || placeholder}</span>
      <input
        ref={ref} type="time" value={value} min={min} max={max} step={step}
        aria-label={label}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        // Present and focusable so showPicker() has somewhere to anchor, but
        // invisible so its own locale formatting never shows through.
        className="absolute inset-0 h-full w-full cursor-pointer opacity-0"
      />
    </span>
  )
}
