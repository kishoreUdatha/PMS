/**
 * CSV export, in one place.
 *
 * Four screens had grown their own copy of this, which is four chances for
 * the quoting to be subtly different — and a comma inside a guest's address
 * silently splitting a column is the kind of bug nobody reports because the
 * file merely looks odd.
 *
 * Values are always quoted and inner quotes doubled, per RFC 4180, so a
 * reason like `Guest said "too noisy"` survives. Rows are joined with CRLF
 * because Excel expects it, and the file is prefixed with a byte-order mark
 * so Excel reads UTF-8 rather than mangling ₹ and accented names.
 */

/** One row's worth of values, in the same order as the headers. */
export type CsvRow = (string | number | null | undefined)[]

function cell(value: unknown): string {
  if (value === null || value === undefined) return '""'
  return `"${String(value).replace(/"/g, '""')}"`
}

export function toCsv(headers: string[], rows: CsvRow[]): string {
  return [headers.map(cell).join(',')]
    .concat(rows.map((r) => r.map(cell).join(',')))
    .join('\r\n')
}

/**
 * Builds the file and hands it to the browser.
 *
 * The object URL is revoked on the next tick rather than immediately —
 * revoking synchronously can cancel the download in some browsers before it
 * has started reading the blob.
 */
export function downloadCsv(
  filename: string, headers: string[], rows: CsvRow[],
): void {
  const blob = new Blob([`\uFEFF${toCsv(headers, rows)}`],
    { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 0)
}

/** `guests-2026-09-10.csv` — dated so successive exports do not overwrite. */
export function datedName(stem: string, on?: string): string {
  return `${stem}-${on ?? new Date().toISOString().slice(0, 10)}.csv`
}
