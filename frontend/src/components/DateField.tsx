import { useRef } from 'react'
import { Calendar } from 'lucide-react'
import { fmtDate } from '../lib/dates'

/** A date input that reads the same way as every date the app prints.
 *
 * `<input type="date">` renders its text in the *browser's* locale, which the
 * page cannot override. On a machine set to en-US that shows 09/10/2026 while
 * the rows underneath say "10 Sep 2026" — the same day written two ways, and
 * the ambiguous one reads as 9 October to anyone using dd/mm.
 *
 * So the native control is kept for the picker and the keyboard, but made
 * transparent and covered with the same en-IN text the rest of the UI uses.
 */
export default function DateField({
  value, onChange, className = '', min, max, disabled,
  placeholder = 'Pick a date', label = 'Date',
}: {
  value: string
  onChange: (v: string) => void
  className?: string
  min?: string
  max?: string
  disabled?: boolean
  placeholder?: string
  /** Accessible name: the visible text is a sibling span, so the input
   *  needs its own or it reaches a screen reader unnamed. */
  label?: string
}) {
  const ref = useRef<HTMLInputElement>(null)

  function open() {
    const el = ref.current
    if (!el || disabled) return
    // showPicker throws if the browser refuses; focusing still lets the user
    // type or open the picker with the keyboard.
    try { el.showPicker() } catch { el.focus() }
  }

  const shown = value ? fmtDate(value, '') : ''

  return (
    // Same box as every other control: `px-3 py-2`, matching both the filter
    // bars and the form fields this now stands in for. It used to be a size
    // smaller, which was fine while this only appeared beside other date
    // pickers and wrong the moment it sat next to a text input.
    <span className={`relative inline-flex items-center gap-2 rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700 focus-within:border-brand ${disabled ? 'opacity-60' : 'cursor-pointer'} ${className}`}
      onClick={open}>
      <Calendar size={14} className="shrink-0 text-slate-400" />
      <span className={shown ? '' : 'text-slate-400'}>{shown || placeholder}</span>
      <input
        ref={ref} type="date" value={value} min={min} max={max}
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
