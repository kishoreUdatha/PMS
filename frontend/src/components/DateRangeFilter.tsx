import { useEffect, useRef, useState } from 'react'
import { CalendarDays, ChevronDown, X } from 'lucide-react'
import DateField from './DateField'
import { fmtDate, fmtDayMonth } from '../lib/dates'

/** Local calendar date, `days` from today, as yyyy-mm-dd. Local rather than
 *  toISOString(), which is UTC and names yesterday before 05:30 in India. */
function localIso(days = 0): string {
  const d = new Date()
  d.setDate(d.getDate() + days)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

type Preset = { label: string; range: () => [string, string] }

/** Forward-looking for arrivals and bookings; backward for history screens,
 *  where "Tomorrow" can only ever return nothing. */
const PRESETS: Record<'future' | 'past', Preset[]> = {
  future: [
    { label: 'Today', range: () => [localIso(), localIso()] },
    { label: 'Tomorrow', range: () => [localIso(1), localIso(1)] },
    { label: 'Next 7 days', range: () => [localIso(), localIso(7)] },
    { label: 'Next 30 days', range: () => [localIso(), localIso(30)] },
  ],
  past: [
    { label: 'Today', range: () => [localIso(), localIso()] },
    { label: 'Yesterday', range: () => [localIso(-1), localIso(-1)] },
    { label: 'Last 7 days', range: () => [localIso(-7), localIso()] },
    { label: 'Last 30 days', range: () => [localIso(-30), localIso()] },
  ],
}

/** What the button says, so a filter that is on can be seen without opening it. */
function summary(from: string, to: string): string {
  if (!from && !to) return 'any date'
  if (from && to && from === to) return fmtDate(from)
  if (from && to) return `${fmtDayMonth(from)} – ${fmtDate(to)}`
  return from ? `From ${fmtDate(from)}` : `Until ${fmtDate(to)}`
}

/** A date range behind one button, for filter bars.
 *
 * Two date fields and their presets took a whole row of every list screen,
 * and that row was empty most of the time. Folded into a button that names
 * the range it holds, the filter costs one control's width, stays visible
 * while it is on, and clears with one click.
 */
export default function DateRangeFilter({ label, from, to, onChange, direction = 'future' }: {
  /** What the dates are of, e.g. "Arriving". */
  label: string
  /** Which way the presets look: ahead (arrivals) or back (history). */
  direction?: 'future' | 'past'
  from: string
  to: string
  onChange: (from: string, to: string) => void
}) {
  const [open, setOpen] = useState(false)
  const boxRef = useRef<HTMLDivElement>(null)
  const active = from !== '' || to !== ''

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (!boxRef.current?.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  return (
    <div ref={boxRef} className="relative">
      <div className={`flex shrink-0 items-center rounded-lg border text-sm ${
        active ? 'border-brand bg-brand-light text-brand' : 'border-slate-200 bg-white text-slate-600 hover:border-slate-300'}`}>
        <button onClick={() => setOpen((o) => !o)} aria-expanded={open}
          className={`flex items-center gap-1.5 whitespace-nowrap py-2 pl-3 font-medium ${active ? 'pr-1.5' : 'pr-2.5'}`}>
          <CalendarDays size={14} />
          <span className={active ? '' : 'text-slate-500'}>{label}</span>
          {summary(from, to)}
          {!active && <ChevronDown size={14} className="text-slate-400" />}
        </button>
        {active && (
          <button onClick={() => onChange('', '')} aria-label={`Clear ${label.toLowerCase()} dates`}
            className="mr-1 rounded-full p-1 hover:bg-brand/10">
            <X size={13} />
          </button>
        )}
      </div>

      {open && (
        <div className="absolute left-0 top-full z-30 mt-1 w-72 rounded-xl border border-slate-200 bg-white p-3 shadow-lg">
          <div className="grid grid-cols-2 gap-1.5">
            {PRESETS[direction].map((p) => (
              <button key={p.label}
                onClick={() => { const [a, b] = p.range(); onChange(a, b); setOpen(false) }}
                className="rounded-lg border border-slate-200 px-2.5 py-1.5 text-sm font-medium text-slate-600 hover:border-brand hover:text-brand">
                {p.label}
              </button>
            ))}
          </div>
          <p className="mb-1.5 mt-3 text-xs font-medium text-slate-500">Custom range</p>
          <div className="space-y-2">
            <DateField value={from} onChange={(v) => onChange(v, to && v > to ? v : to)}
              placeholder="From" label={`${label} from`} className="w-full" />
            <DateField value={to} min={from || undefined} onChange={(v) => onChange(from, v)}
              placeholder="To" label={`${label} to`} className="w-full" />
          </div>
          {active && (
            <button onClick={() => { onChange('', ''); setOpen(false) }}
              className="mt-3 text-sm font-medium text-brand hover:underline">
              Clear dates
            </button>
          )}
        </div>
      )}
    </div>
  )
}
