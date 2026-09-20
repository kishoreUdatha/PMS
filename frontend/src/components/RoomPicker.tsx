import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { BedDouble, Check, ChevronDown, Sparkles } from 'lucide-react'
import type { CandidateRoom } from '../api'

/**
 * Choosing a room to put a guest in.
 *
 * A native `<select>` was doing this, and on Windows the operating system
 * draws the open list — so the brand palette stopped at the closed control and
 * every option was highlighted in system blue. That is cosmetic on a dropdown
 * of dates or currencies. It is not cosmetic here, because each option carries
 * state the clerk is deciding on: whether the room is free, whether it has
 * been cleaned, and what is in the way when it cannot be taken. Colour is
 * carrying meaning, and a native select cannot show it.
 *
 * What this deliberately keeps from the native control, because losing it
 * would be a poor trade:
 *
 * * **Type-ahead.** Typing "203" jumps to room 203, as it does in a select.
 * * **Keyboard.** Up/Down move, Enter takes, Escape closes, Home/End jump.
 * * **Unavailable rooms stay listed**, disabled, with the reason. A clerk
 *   asked for room 101 by name needs an answer, not an absence.
 *
 * The list renders through a portal in fixed position: the panel it sits in
 * scrolls and clips, and an absolutely positioned list would be cut off at
 * the panel edge.
 */

/** What is in the way, in the words the desk would use. */
function blockedLabel(c: CandidateRoom): string | null {
  if (c.available) return null
  if (c.blocked_reason === 'occupied') return `Occupied · ${c.blocked_by}`
  if (c.blocked_reason === 'out_of_service') return `Out of service · ${c.blocked_by}`
  if (c.blocked_reason === 'retired') return 'Retired'
  // A caller may rule a room out for its own reasons — the reservation grid
  // blocks one already taken by another line of the same booking — and says
  // so in blocked_by.
  return c.blocked_by ?? 'Not available'
}

export default function RoomPicker({
  rooms, value, onChange, disabled, placeholder = 'Select a room',
  allowClear = false,
}: {
  rooms: CandidateRoom[]
  value: string
  onChange: (roomId: string) => void
  disabled?: boolean
  /** Shown when nothing is chosen. */
  placeholder?: string
  /** Offer a row that unsets the choice — "Assign later" on a new booking. */
  allowClear?: boolean
}) {
  const btnRef = useRef<HTMLButtonElement>(null)
  const listRef = useRef<HTMLDivElement>(null)
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)
  const [pos, setPos] = useState<{ top: number; left: number; width: number } | null>(null)
  // Type-ahead buffer, cleared when the typing stops.
  const typed = useRef({ text: '', at: 0 })

  const chosen = rooms.find((r) => r.room_id === value) ?? null

  // Measured rather than assumed: the panel scrolls, so the button's place on
  // screen is only known at the moment it opens.
  useLayoutEffect(() => {
    if (!open || !btnRef.current) return
    const place = () => {
      const b = btnRef.current?.getBoundingClientRect()
      if (!b) return
      // Never narrower than the content needs. The reservation grid's Room
      // column is tight, and a list that inherits it truncates the floor to
      // "201 · Fl…" — losing exactly the detail the row exists to show.
      const width = Math.max(b.width, 260)
      // Kept on screen when widening pushes it past the right edge.
      const left = Math.min(b.left, window.innerWidth - width - 8)
      setPos({ top: b.bottom + 4, left: Math.max(8, left), width })
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

  // Keep the highlighted row in view when the keyboard moves it.
  useEffect(() => {
    if (!open) return
    listRef.current
      ?.querySelector(`[data-i="${active}"]`)
      ?.scrollIntoView({ block: 'nearest' })
  }, [active, open])

  const step = (delta: number) => {
    if (rooms.length === 0) return
    let i = active
    for (let n = 0; n < rooms.length; n += 1) {
      i = (i + delta + rooms.length) % rooms.length
      if (rooms[i].available) break
    }
    setActive(i)
  }

  const take = (i: number) => {
    const room = rooms[i]
    if (!room || !room.available) return
    onChange(room.room_id)
    setOpen(false)
    btnRef.current?.focus()
  }

  const onKey = (e: React.KeyboardEvent) => {
    if (!open) {
      if (['Enter', ' ', 'ArrowDown'].includes(e.key)) {
        e.preventDefault()
        setOpen(true)
        setActive(Math.max(0, rooms.findIndex((r) => r.room_id === value)))
      }
      return
    }
    if (e.key === 'Escape') { e.preventDefault(); setOpen(false); return }
    if (e.key === 'ArrowDown') { e.preventDefault(); step(1); return }
    if (e.key === 'ArrowUp') { e.preventDefault(); step(-1); return }
    if (e.key === 'Home') { e.preventDefault(); setActive(0); return }
    if (e.key === 'End') { e.preventDefault(); setActive(rooms.length - 1); return }
    if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); take(active); return }

    if (e.key.length === 1 && /\S/.test(e.key)) {
      const now = Date.now()
      typed.current = {
        text: now - typed.current.at > 800 ? e.key : typed.current.text + e.key,
        at: now,
      }
      const hit = rooms.findIndex((r) =>
        r.available && r.code.toLowerCase().startsWith(typed.current.text.toLowerCase()))
      if (hit >= 0) setActive(hit)
    }
  }

  const free = rooms.filter((r) => r.available).length

  return (
    <>
      <button ref={btnRef} type="button" disabled={disabled}
        aria-haspopup="listbox" aria-expanded={open}
        onClick={() => {
          setOpen((v) => !v)
          setActive(Math.max(0, rooms.findIndex((r) => r.room_id === value)))
        }}
        onKeyDown={onKey}
        className={`flex w-full items-center gap-2 rounded-lg border px-3 py-2 text-left text-sm outline-none ${
          disabled ? 'cursor-not-allowed border-slate-200 bg-slate-50 text-slate-400'
            : open ? 'border-brand ring-1 ring-brand/20'
            : 'border-slate-200 hover:border-slate-300'}`}>
        <BedDouble size={15} className={chosen ? 'text-brand' : 'text-slate-300'} />
        <span className="min-w-0 flex-1 truncate">
          {chosen
            ? <span className="font-medium text-slate-800">
                {chosen.code}
                {chosen.floor && <span className="font-normal text-slate-400">
                  {' · '}Floor {chosen.floor}</span>}
              </span>
            : <span className="text-slate-400">{placeholder}</span>}
        </span>
        {chosen && !chosen.ready && (
          <span className="flex shrink-0 items-center gap-1 rounded-md bg-amber-50 px-1.5 py-0.5 text-[11px] font-medium text-amber-700">
            <Sparkles size={11} /> Needs cleaning
          </span>
        )}
        <ChevronDown size={15}
          className={`shrink-0 text-slate-400 ${open ? 'rotate-180' : ''}`} />
      </button>

      {open && pos && createPortal(
        <div ref={listRef} role="listbox" tabIndex={-1}
          style={{ position: 'fixed', top: pos.top, left: pos.left,
                   width: pos.width, zIndex: 70 }}
          className="scroll-slim max-h-72 overflow-y-auto rounded-xl border border-slate-200 bg-white py-1 shadow-xl">
          <p className="px-3 py-1.5 text-[11px] uppercase tracking-wide text-slate-400">
            {free} of {rooms.length} free for these dates
          </p>
          {allowClear && (
            <button type="button" role="option" aria-selected={value === ''}
              onClick={() => { onChange(''); setOpen(false); btnRef.current?.focus() }}
              className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-slate-600 hover:bg-slate-50">
              <span className="flex-1">{placeholder}</span>
              <Check size={14}
                className={value === '' ? 'text-brand' : 'opacity-0'} />
            </button>
          )}
          {rooms.length === 0 && (
            <p className="px-3 py-6 text-center text-sm text-slate-400">
              No rooms of this type.
            </p>
          )}
          {rooms.map((c, i) => {
            const blocked = blockedLabel(c)
            const selected = c.room_id === value
            return (
              <button key={c.room_id} type="button" data-i={i}
                role="option" aria-selected={selected} disabled={!c.available}
                onMouseEnter={() => c.available && setActive(i)}
                onClick={() => take(i)}
                className={`flex w-full items-center gap-2 px-3 py-2 text-left text-sm ${
                  !c.available ? 'cursor-not-allowed text-slate-300'
                    : active === i ? 'bg-brand/10 text-slate-800'
                    : 'text-slate-700'}`}>
                <span className="min-w-0 flex-1 truncate">
                  <span className="font-medium">{c.code}</span>
                  {c.floor && <span className="text-slate-400">
                    {' · '}Floor {c.floor}</span>}
                </span>

                {blocked ? (
                  <span className="shrink-0 truncate rounded-md bg-slate-75 px-1.5 py-0.5 text-[11px] font-medium text-slate-500">
                    {blocked}
                  </span>
                ) : c.ready ? (
                  <span className="shrink-0 rounded-md bg-emerald-50 px-1.5 py-0.5 text-[11px] font-medium text-emerald-700">
                    Ready
                  </span>
                ) : (
                  <span className="flex shrink-0 items-center gap-1 rounded-md bg-amber-50 px-1.5 py-0.5 text-[11px] font-medium text-amber-700">
                    <Sparkles size={11} /> Needs cleaning
                  </span>
                )}

                <Check size={14}
                  className={selected ? 'shrink-0 text-brand' : 'shrink-0 opacity-0'} />
              </button>
            )
          })}
        </div>,
        document.body,
      )}
    </>
  )
}
