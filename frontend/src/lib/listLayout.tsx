import { useCallback, useEffect, useState } from 'react'
import { LayoutGrid, List } from 'lucide-react'
import StayTable, { type StayRow } from '../components/StayTable'
import StayCards from '../components/StayCards'

/**
 * List or cards, chosen once for the whole Reservations screen.
 *
 * The four tabs — Reservations, Arrivals, In-house, Departures — are four
 * separate components that happen to render the same table. To somebody using
 * it they are one screen, so a layout chosen on one tab has to hold on the
 * next; a toggle that reset every time you moved between them would be worse
 * than no toggle. Hence one stored preference and an event, rather than state
 * in each component.
 *
 * `storage` alone would not do it: that event only fires in *other* tabs of
 * the browser, never the one that made the change, so the three sibling
 * components on this page would not hear it.
 */

export type StayLayout = 'list' | 'cards'

const CHANGED = 'list-layout-changed'

function stored(key: string, fallback: StayLayout): StayLayout {
  try {
    const v = localStorage.getItem(`${key}_layout`)
    return v === 'cards' || v === 'list' ? v : fallback
  } catch {
    return fallback   // private window, or site data blocked
  }
}

/** Remembered per list, not once for the whole app.
 *
 * Someone who wants reservations as cards has said nothing about how they
 * want the guest directory, and forcing both to agree makes the choice worse
 * the moment the two lists are used differently — which is the usual case.
 */
export function useListLayout(
  key: string,
  /** What this screen shows before anyone chooses. Housekeeping opens on its
   *  board, because that is what the screen has always been. */
  fallback: StayLayout = 'list',
): [StayLayout, (v: StayLayout) => void] {
  const [layout, setLayout] = useState<StayLayout>(() => stored(key, fallback))

  useEffect(() => {
    const onChange = () => setLayout(stored(key, fallback))
    window.addEventListener(CHANGED, onChange)
    return () => window.removeEventListener(CHANGED, onChange)
  }, [key, fallback])

  const choose = useCallback((v: StayLayout) => {
    try { localStorage.setItem(`${key}_layout`, v) } catch { /* nowhere to keep it */ }
    setLayout(v)
    window.dispatchEvent(new Event(CHANGED))
  }, [key])

  return [layout, choose]
}

/** The four Reservations tabs are one screen to the person using them, so
 *  they share a key. */
export function useStayLayout() { return useListLayout('reservations') }

/** The toggle itself. Small and iconic: it changes how the list reads, not
 *  what is in it, so it should not compete with the filters beside it. */
export function StayLayoutToggle({ layout, onChange, cardsLabel, cardsIcon }: {
  layout: StayLayout; onChange: (v: StayLayout) => void
  /** Housekeeping's other view is a board, not a set of cards. Calling it
   *  "Card view" there would label the button with the thing it is leaving. */
  cardsLabel?: string
  cardsIcon?: typeof LayoutGrid
}) {
  const CardsIcon = cardsIcon ?? LayoutGrid
  return (
    <span className="flex shrink-0 items-center gap-0.5 rounded-lg border border-slate-200 bg-white p-0.5">
      {([['list', 'List view', List],
         ['cards', cardsLabel ?? 'Card view', CardsIcon]] as const)
        .map(([key, title, Icon]) => (
          <button key={key} onClick={() => onChange(key)}
            title={title} aria-label={title} aria-pressed={layout === key}
            className={`rounded-md px-2 py-1 ${
              layout === key
                ? 'bg-brand text-white'
                : 'text-slate-500 hover:bg-slate-50 hover:text-slate-700'}`}>
            <Icon size={15} />
          </button>
        ))}
    </span>
  )
}

/** The rows, read whichever way was chosen. One mapping feeds both, so the
 *  table and the cards cannot start disagreeing about what a booking is. */
export function StayView({ layout, ...props }: {
  layout: StayLayout
  rows: StayRow[]
  loading?: boolean
  empty: React.ReactNode
  showAction?: boolean
}) {
  return layout === 'cards' ? <StayCards {...props} /> : <StayTable {...props} />
}
