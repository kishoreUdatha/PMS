import { useEffect, useState } from 'react'
import { CheckCircle2, Copy, Loader2, X } from 'lucide-react'
import { createPortalLink } from '../api'
import { errorText } from '../lib/forms'

/** Make a guest portal link for a booking, text it, and show it to copy.
 *
 *  Opening the dialog creates the link, because there is nothing to choose
 *  first: a link is always for this booking and always replaces the last one.
 *  The URL is shown in full so the desk can also paste it into an email or a
 *  chat the system does not send.
 */
export default function PortalLinkDialog({ reservationId, propertyId, onClose }: {
  reservationId: string
  propertyId: string
  onClose: () => void
}) {
  const [state, setState] = useState<
    { kind: 'busy' } | { kind: 'done'; url: string; message: string | null }
    | { kind: 'error'; text: string }>({ kind: 'busy' })
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    let live = true
    createPortalLink(reservationId, propertyId, true)
      .then((r) => live && setState({ kind: 'done', url: r.url, message: r.message }))
      .catch((e) => live && setState({ kind: 'error', text: errorText(e, 'Could not create the link.') }))
    return () => { live = false }
  }, [reservationId, propertyId])

  async function copy(url: string) {
    try {
      await navigator.clipboard.writeText(url)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch { /* the link is still on screen to select */ }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div role="dialog" aria-modal="true" className="w-full max-w-lg rounded-2xl bg-white p-6 shadow-xl">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold text-ink">Guest portal link</h2>
            <p className="mt-1 text-sm text-slate-500">
              The guest can check in online, send requests and pay from this link.
              Any earlier link for this booking stops working.
            </p>
          </div>
          <button onClick={onClose} aria-label="Close"
            className="rounded-lg p-1 text-slate-400 hover:bg-slate-50"><X size={18} /></button>
        </div>
        {state.kind === 'busy' && (
          <p className="mt-6 flex items-center gap-2 text-sm text-slate-500">
            <Loader2 size={16} className="animate-spin" /> Creating the link…
          </p>
        )}
        {state.kind === 'error' && (
          <p className="mt-6 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{state.text}</p>
        )}
        {state.kind === 'done' && (
          <>
            <div className="mt-5 flex items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 p-2">
              <input readOnly value={state.url} onFocus={(e) => e.target.select()}
                className="min-w-0 flex-1 bg-transparent px-1 text-sm text-slate-700 outline-none" />
              <button onClick={() => copy(state.url)}
                className="flex items-center gap-1 rounded-md bg-brand px-3 py-1.5 text-xs font-semibold text-white hover:bg-brand-dark">
                <Copy size={13} /> {copied ? 'Copied' : 'Copy'}
              </button>
            </div>
            {state.message && (
              <p className="mt-3 flex items-start gap-2 text-sm text-slate-600">
                <CheckCircle2 size={15} className="mt-0.5 shrink-0 text-brand" /> {state.message}.
              </p>
            )}
          </>
        )}
        <div className="mt-6 flex justify-end">
          <button onClick={onClose}
            className="rounded-lg border border-slate-200 px-4 py-2 text-sm font-medium text-slate-600 hover:bg-slate-50">
            Done
          </button>
        </div>
      </div>
    </div>
  )
}
