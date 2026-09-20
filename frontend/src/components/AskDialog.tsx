import { useCallback, useEffect, useRef, useState } from 'react'
import { AlertTriangle, CheckCircle2, X } from 'lucide-react'

/** In-app replacements for window.prompt and window.alert.
 *
 *  The native dialogs were doing real work -- every suspension, revocation and
 *  capability change asks for a reason, and the API rejects the call without
 *  one -- but they are the browser's chrome, not the product's: they say
 *  "localhost:5173 says", they cannot show which tenant is affected in
 *  anything but plain text, a failed validation needed a *second* alert to
 *  report it, and they block the whole page while open.
 *
 *  Two shapes, because two different things were being asked:
 *
 *    askReason / askText  a question. Modal, because the action cannot
 *                         proceed until it is answered, and it can be
 *                         cancelled.
 *    notify               a result. A toast: the work is already done, so
 *                         there is nothing to decide and nothing to block.
 *                         It is fire-and-forget by design -- an alert that
 *                         had to be dismissed before the list would reload
 *                         was making the reader click to see the change they
 *                         had just made.
 *
 *  One <OverlayHost/> is mounted at the app root. The module-level handles are
 *  how a plain async function reaches it without every caller threading a
 *  context down; with no host mounted, a question resolves null rather than
 *  hanging and a result goes to the console, and both say so.
 */

type Ask = {
  kind: 'text'
  title: string
  body?: string
  label: string
  initial?: string
  minLength?: number
  hint?: string
  confirmText: string
  danger?: boolean
  resolve: (value: string | null) => void
}

type Toast = { id: number; title?: string; message: string }

let openAsk: ((a: Ask) => void) | null = null
let pushToast: ((t: Omit<Toast, 'id'>) => void) | null = null

function ask(a: Omit<Ask, 'resolve'>): Promise<string | null> {
  return new Promise((resolve) => {
    if (!openAsk) {
      console.error('<OverlayHost/> is not mounted; dialog skipped:', a.title)
      resolve(null)
      return
    }
    openAsk({ ...a, resolve })
  })
}

/** Insist on a reason before a destructive action.
 *
 *  Asking here rather than sending a placeholder is the difference between an
 *  audit log that explains itself and one full of "reason: reason".
 */
export function askReason(what: string): Promise<string | null> {
  return ask({
    kind: 'text',
    title: what,
    body: 'This is recorded in the audit log against the tenant it affects.',
    label: 'Reason',
    minLength: 3,
    hint: 'At least 3 characters.',
    confirmText: 'Confirm',
    danger: true,
  })
}

/** A value the action needs, with its own rule about what counts. */
export function askText(opts: {
  title: string
  body?: string
  label: string
  initial?: string
  minLength?: number
  hint?: string
  confirmText?: string
}): Promise<string | null> {
  return ask({
    kind: 'text',
    confirmText: opts.confirmText ?? 'Save',
    ...opts,
  })
}

/** Report something that has already happened. Does not block. */
export function notify(message: string, title?: string): void {
  if (!pushToast) {
    console.error('<OverlayHost/> is not mounted; not shown:', title, message)
    return
  }
  pushToast({ message, title })
}

const TOAST_MS = 6000

export function OverlayHost() {
  const [req, setReq] = useState<Ask | null>(null)
  const [value, setValue] = useState('')
  const [touched, setTouched] = useState(false)
  const [toasts, setToasts] = useState<Toast[]>([])
  const inputRef = useRef<HTMLInputElement>(null)
  const seq = useRef(0)
  const timers = useRef<number[]>([])

  const drop = useCallback((id: number) => {
    setToasts((all) => all.filter((t) => t.id !== id))
  }, [])

  useEffect(() => {
    openAsk = (a) => { setReq(a); setValue(a.initial ?? ''); setTouched(false) }
    pushToast = (t) => {
      const id = (seq.current += 1)
      setToasts((all) => [...all, { ...t, id }])
      timers.current.push(window.setTimeout(() => drop(id), TOAST_MS))
    }
    return () => {
      openAsk = null
      pushToast = null
      timers.current.forEach(window.clearTimeout)
      timers.current = []
    }
  }, [drop])

  // Select the seeded value: a rename starts from the current name, and
  // typing over it should not mean clearing it first.
  useEffect(() => { if (req) inputRef.current?.select() }, [req])

  const close = useCallback((v: string | null) => {
    setReq((current) => { current?.resolve(v); return null })
  }, [])

  useEffect(() => {
    if (!req) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.preventDefault(); close(null) }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [req, close])

  const min = req?.minLength ?? 0
  const trimmed = value.trim()
  const ok = trimmed.length >= min
  const showHint = Boolean(req) && touched && !ok && Boolean(req?.hint)

  function submit() {
    if (!ok) { setTouched(true); return }
    close(trimmed)
  }

  return (
    <>
      {req && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4"
          onMouseDown={(e) => { if (e.target === e.currentTarget) close(null) }}
        >
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="ask-title"
            className="w-full max-w-md rounded-2xl border border-slate-200 bg-white p-5 shadow-xl"
          >
            <div className="flex items-start gap-3">
              {req.danger && (
                <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-amber-50 text-amber-600">
                  <AlertTriangle size={17} />
                </span>
              )}
              <div className="min-w-0">
                <h2 id="ask-title" className="text-base font-semibold text-slate-900">
                  {req.title}
                </h2>
                {req.body && (
                  <p className="mt-1 text-sm text-slate-500">{req.body}</p>
                )}
              </div>
            </div>

            <div className="mt-4">
              <label htmlFor="ask-value"
                className="mb-1.5 block text-sm font-medium text-slate-700">
                {req.label}
              </label>
              <input
                id="ask-value"
                ref={inputRef}
                value={value}
                onChange={(e) => setValue(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); submit() } }}
                aria-invalid={showHint || undefined}
                aria-describedby={showHint ? 'ask-hint' : undefined}
                className={`w-full rounded-lg border px-3 py-2 text-sm text-slate-900 outline-none focus:ring-2 focus:ring-brand-deep/25 ${
                  showHint ? 'border-red-400' : 'border-slate-300 focus:border-brand-deep'}`}
              />
              {showHint && (
                <p id="ask-hint" className="mt-1.5 text-sm text-red-600">{req.hint}</p>
              )}
            </div>

            <div className="mt-5 flex justify-end gap-2">
              <button type="button" onClick={() => close(null)}
                className="rounded-lg border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50">
                Cancel
              </button>
              <button type="button" onClick={submit} disabled={touched && !ok}
                className="rounded-lg bg-brand-deep px-4 py-2 text-sm font-semibold text-white hover:bg-brand-dark disabled:opacity-60">
                {req.confirmText}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Results. aria-live rather than a role=alert: these are confirmations
          of something the reader just did, not interruptions. */}
      {toasts.length > 0 && (
        <div
          aria-live="polite"
          className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-[min(22rem,calc(100vw-2rem))] flex-col gap-2"
        >
          {toasts.map((t) => (
            <div
              key={t.id}
              className="pointer-events-auto flex items-start gap-2.5 rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-lg motion-safe:animate-[toast-in_160ms_ease-out]"
            >
              <CheckCircle2 size={17} className="mt-0.5 shrink-0 text-emerald-600" />
              <div className="min-w-0 flex-1">
                {t.title && (
                  <div className="text-sm font-semibold text-slate-900">{t.title}</div>
                )}
                <div className="break-words text-sm text-slate-600">{t.message}</div>
              </div>
              <button type="button" onClick={() => drop(t.id)} aria-label="Dismiss"
                className="-mr-1 shrink-0 rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600">
                <X size={14} />
              </button>
            </div>
          ))}
        </div>
      )}
    </>
  )
}
