/** Shared bits for the small record-keeping forms (work orders, expense
 *  vouchers, unit owners). Kept out of the component file so that file exports
 *  only components and keeps Fast Refresh. */

export const inputCls =
  'w-full rounded-lg border border-slate-200 px-3 py-2.5 text-sm outline-none focus:border-brand'

/** The server's reason, in words: FastAPI sends a string for refusals it
 *  explains and a list of field errors for a body it could not read. */
export function errorText(e: unknown, fallback = 'Could not save.'): string {
  const er = e as { response?: { data?: { detail?: unknown } }; message?: string }
  const detail = er.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    const msgs = detail
      .map((d) => (d as { msg?: string }).msg ?? '')
      .filter(Boolean)
    if (msgs.length) return msgs.join(' ')
  }
  return er.message ?? fallback
}

/** An empty text box is "not given", not an empty string. */
export const orNull = (v: string): string | null => v.trim() || null

export const inr = (v: string | number | null | undefined): string =>
  v === null || v === undefined || v === ''
    ? '—'
    : new Intl.NumberFormat('en-IN', {
      style: 'currency', currency: 'INR', minimumFractionDigits: 2,
    }).format(Number(v))
