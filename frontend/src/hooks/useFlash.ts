import { useCallback, useEffect, useRef, useState } from 'react'

/** A success message that clears itself.
 *
 * Thirteen screens show a green banner after an action succeeds. Nine of them
 * hide it again after a few seconds; four never did — Cashiering, Folio
 * Adjustment, Housekeeping and Payment Reversal, which are the money screens,
 * so "Cash payment of ₹1,340.00 taken" sat above the table for the rest of
 * the session, pushing the transactions down a row for a fact the row itself
 * already showed.
 *
 * Shaped exactly like `useState<string>` so adopting it is one line and every
 * existing `setToast(msg)` / `setToast('')` call keeps working.
 *
 * Two things it fixes that the hand-rolled `setTimeout` version got wrong:
 *
 * * **Two messages in quick succession.** The old timer was never cancelled,
 *   so a second message inherited the first one's deadline and could flash up
 *   and vanish almost immediately.
 * * **Unmounting while a timer is pending.** Leaving the screen left a
 *   `setState` scheduled against a component that no longer exists.
 *
 * Errors deliberately do not use this. A failure should stay on screen until
 * the person has dealt with it.
 */
export function useFlash(ms = 3000): [string, (m: string) => void] {
  const [message, setMessage] = useState('')
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const stop = () => {
    if (timer.current !== null) {
      clearTimeout(timer.current)
      timer.current = null
    }
  }

  const show = useCallback((m: string) => {
    stop()
    setMessage(m)
    // An empty message is a caller clearing it by hand; nothing to schedule.
    if (m) timer.current = setTimeout(() => setMessage(''), ms)
  }, [ms])

  useEffect(() => stop, [])

  return [message, show]
}
