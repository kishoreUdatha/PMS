/** Shared bits for the small record-keeping forms (work orders, expense
 *  vouchers, unit owners). Kept out of the component file so that file exports
 *  only components and keeps Fast Refresh. */

export const inputCls =
  'w-full rounded-lg border border-slate-200 px-3 py-2.5 text-sm outline-none focus:border-brand'

/** The server's reason for a failed request, or `fallback`.
 *
 *  The one helper every screen uses to turn a caught error into words. The
 *  HTTP client's response interceptor (api.ts) has already flattened FastAPI's
 *  `detail` -- a string for refusals it explains, a list of field errors for a
 *  body it could not read -- into a single string, so all that is left is to
 *  take it when it is there. Anything else (a network failure, a 500 with an
 *  HTML body, a bug) gets the caller's own wording rather than axios's
 *  "Request failed with status code 500". */
export function errorText(e: unknown, fallback = 'Could not save.'): string {
  const detail = (e as { response?: { data?: { detail?: unknown } } } | null)
    ?.response?.data?.detail
  return typeof detail === 'string' && detail.trim() ? detail : fallback
}

/** An empty text box is "not given", not an empty string. */
export const orNull = (v: string): string | null => v.trim() || null

export const inr = (v: string | number | null | undefined): string =>
  v === null || v === undefined || v === ''
    ? '—'
    : new Intl.NumberFormat('en-IN', {
      style: 'currency', currency: 'INR', minimumFractionDigits: 2,
    }).format(Number(v))
