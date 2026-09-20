import type { ReactNode } from 'react'
import { AlertTriangle, Loader2, X } from 'lucide-react'

/** A centred form dialog, the shape Companies & Agents already uses. */
export function Modal({ title, onClose, error, children, footer }: {
  title: string
  onClose: () => void
  error?: string
  children: ReactNode
  footer: ReactNode
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4"
      onClick={onClose}>
      <div role="dialog" aria-modal="true" aria-label={title}
        onClick={(e) => e.stopPropagation()}
        className="max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-2xl bg-white p-6 shadow-xl">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold text-ink">{title}</h2>
          <button onClick={onClose} aria-label="Close"
            className="text-slate-400 hover:text-slate-600">
            <X size={18} />
          </button>
        </div>
        {error && (
          <p className="mt-3 flex items-start gap-2 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">
            <AlertTriangle size={15} className="mt-0.5 shrink-0" /> {error}
          </p>
        )}
        <div className="mt-4 grid gap-3 sm:grid-cols-2">{children}</div>
        <div className="mt-5 flex justify-end gap-2">{footer}</div>
      </div>
    </div>
  )
}

export function Field({ label, required, hint, wide, children }: {
  label: string; required?: boolean; hint?: string; wide?: boolean
  children: ReactNode
}) {
  return (
    <label className={`block ${wide ? 'sm:col-span-2' : ''}`}>
      <span className="mb-1 block text-sm font-medium text-slate-600">
        {label} {required && <span className="text-red-500">*</span>}
      </span>
      {children}
      {hint && <span className="mt-1 block text-xs text-slate-400">{hint}</span>}
    </label>
  )
}

export function ModalButtons({ onClose, onSave, busy, disabled, label }: {
  onClose: () => void; onSave: () => void
  busy: boolean; disabled?: boolean; label: string
}) {
  return (
    <>
      <button onClick={onClose}
        className="rounded-lg border border-slate-200 px-4 py-2 text-sm text-slate-600 hover:bg-slate-50">
        Cancel
      </button>
      <button onClick={onSave} disabled={disabled || busy}
        className="flex items-center gap-2 rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white enabled:hover:bg-brand/90 disabled:opacity-50">
        {busy && <Loader2 size={15} className="animate-spin" />}
        {label}
      </button>
    </>
  )
}

export function Badge({ tone, children }: { tone: string; children: ReactNode }) {
  return (
    <span className={`whitespace-nowrap rounded-md px-2 py-0.5 text-xs font-medium ${tone}`}>
      {children}
    </span>
  )
}
