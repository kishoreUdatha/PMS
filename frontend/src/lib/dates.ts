/** One spelling of a date for the whole UI.
 *
 * The app had three: a fixed table giving "10 Sep 2026" in list rows,
 * `toLocaleDateString('en-IN')` giving "10 Sept 2026" in the top bar, and the
 * native date input rendering "09/10/2026" in whatever the *browser's* locale
 * is — three ways to write one day, on one screen. The ambiguous one is the
 * dangerous one: 09/10/2026 reads as 9 October to anyone using dd/mm.
 *
 * The table is deliberately literal rather than locale-derived, so a browser
 * updating its locale data cannot silently change how the PMS writes dates.
 */
export const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

export const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']

/** The timezone timestamps are DISPLAYED in: the property's own, from config.
 *
 * A `*_at` column is an instant, and an instant has no hour until you name a
 * place to read it in. These helpers used `Date.getHours()`, which names the
 * BROWSER's place -- so a payment taken at 20:10 in Chirala read 14:40 to
 * anyone opening the same screen from London, and every timestamp in the
 * product was quietly relative to whoever was looking.
 *
 * It went unnoticed because the browser and the property have so far been in
 * the same zone. That is a coincidence, not a design: a head office abroad, a
 * consultant on a call, or a second property in another country each break it,
 * and nothing about the screen would say so.
 *
 * Module state rather than a parameter on all twenty-three call sites, and
 * null until the property loads -- in which case the browser's zone is used,
 * the old behaviour, rather than guessing at a zone.
 */
let displayZone: string | null = null

/** Point the timestamp helpers at a property's configured timezone. */
export function setDisplayZone(tz: string | null | undefined): void {
  displayZone = tz || null
}

export function getDisplayZone(): string | null {
  return displayZone
}

/** The parts of an instant, read in the configured zone. */
function partsIn(d: Date): { day: number; month: number; year: number;
                             hour: string; minute: string } {
  if (!displayZone) {
    return {
      day: d.getDate(), month: d.getMonth(), year: d.getFullYear(),
      hour: String(d.getHours()).padStart(2, '0'),
      minute: String(d.getMinutes()).padStart(2, '0'),
    }
  }
  const f = new Intl.DateTimeFormat('en-GB', {
    timeZone: displayZone, year: 'numeric', month: 'numeric', day: 'numeric',
    hour: '2-digit', minute: '2-digit', hour12: false,
  }).formatToParts(d)
  const get = (t: string) => f.find((x) => x.type === t)?.value ?? ''
  return {
    day: Number(get('day')), month: Number(get('month')) - 1,
    year: Number(get('year')),
    // Some locales render midnight as 24; the clock says 00.
    hour: get('hour') === '24' ? '00' : get('hour'),
    minute: get('minute'),
  }
}

function parse(value: string | Date): Date | null {
  const d = typeof value === 'string'
    ? new Date(`${value.slice(0, 10)}T00:00:00`)
    : value
  return Number.isNaN(d.getTime()) ? null : d
}

/** "10 Sep 2026" */
export function fmtDate(value?: string | Date | null, empty = '—'): string {
  const d = value ? parse(value) : null
  if (!d) return empty
  return `${d.getDate()} ${MONTHS[d.getMonth()]} ${d.getFullYear()}`
}

/** "Thu, 10 Sep 2026" */
export function fmtDateLong(value?: string | Date | null, empty = '—'): string {
  const d = value ? parse(value) : null
  if (!d) return empty
  return `${WEEKDAYS[d.getDay()]}, ${fmtDate(d)}`
}

/** "Today", "in 3 days", "2 days ago" — how far off a date is.
 *
 * Lived on the bookings list, where an arrival still sitting in the past is
 * worth noticing. It is worth noticing on every stay list, so it moved here
 * with the rest of the date handling.
 */
export function whenLabel(
  iso?: string | null,
): { text: string; tone: string } | null {
  if (!iso) return null
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  const then = parse(iso)
  if (!then) return null
  const days = Math.round((then.getTime() - today.getTime()) / 86400000)
  if (days === 0) return { text: 'Today', tone: 'bg-brand text-white' }
  if (days === 1) return { text: 'Tomorrow', tone: 'bg-sky-100 text-sky-700' }
  if (days > 1) return { text: `in ${days} days`, tone: 'bg-slate-100 text-slate-500' }
  if (days === -1) return { text: 'Yesterday', tone: 'bg-amber-100 text-amber-800' }
  return { text: `${Math.abs(days)} days ago`, tone: 'bg-amber-100 text-amber-800' }
}

/** "09:40" — a clock time, 24-hour, everywhere in the app.
 *
 * The ledger screens always stamped their entries this way while the rest of
 * the app used "9:40 am", which made the same instant read two ways depending
 * on which screen you were on. 24-hour won: on a money trail a misread
 * meridiem is a real mistake, and 14:05 cannot be misread. Hotels run on it
 * anyway — a 14:00 check-in is written 14:00 on every rate sheet in the world.
 *
 * Built rather than delegated for the same reason as `fmtDate`: en-IN renders
 * "9:40:06 am" with seconds nobody reads, and other locales "09:40 AM".
 * Seconds are dropped deliberately — on an audit trail the minute is what a
 * person checks against, and the second is noise in a column.
 *
 * Zero-padded, so times line up down a column and sort as text.
 */
export function fmtTime(value?: string | Date | null, empty = '—'): string {
  const d = value ? (typeof value === 'string' ? new Date(value) : value) : null
  if (!d || Number.isNaN(d.getTime())) return empty
  const p = partsIn(d)
  return `${p.hour}:${p.minute}`
}

/** "13 Sep 2026, 9:40 am" — a moment, spelled like every other date here.
 *
 * The one to reach for on any `*_at` column. `toLocaleString('en-IN')` was
 * scattered across the app and prints "13/9/2026, 9:40:06 am": a numeric day
 * and month in a UI whose every other date is written "13 Sep 2026", and
 * ambiguous on top of that.
 *
 * Takes a timestamp, so unlike `fmtDate` it must NOT be truncated to ten
 * characters first — that would throw the time away before it is read.
 */
export function fmtDateTime(value?: string | Date | null, empty = '—'): string {
  const d = value ? (typeof value === 'string' ? new Date(value) : value) : null
  if (!d || Number.isNaN(d.getTime())) return empty
  // Date parts from the same zone as the time: at 00:38 IST the UTC date is
  // still yesterday, so taking the day from one zone and the hour from
  // another prints a moment that never happened.
  const p = partsIn(d)
  return `${p.day} ${MONTHS[p.month]} ${p.year}, ${p.hour}:${p.minute}`
}

export const MONTHS_LONG = ['January', 'February', 'March', 'April', 'May',
  'June', 'July', 'August', 'September', 'October', 'November', 'December']

/** "13 Sep" — a date with the year left off.
 *
 * For chart axes and calendar strips, where the year is already established by
 * the thing around them and would only steal width from the label. */
export function fmtDayMonth(value?: string | Date | null, empty = '—'): string {
  const d = value ? parse(value) : null
  if (!d) return empty
  return `${d.getDate()} ${MONTHS[d.getMonth()]}`
}

/** "Sep" — the month on its own, for a calendar strip's header. */
export function fmtMonth(value?: string | Date | null, empty = ''): string {
  const d = value ? parse(value) : null
  return d ? MONTHS[d.getMonth()] : empty
}

/** "Sun" */
export function fmtWeekday(value?: string | Date | null, empty = ''): string {
  const d = value ? parse(value) : null
  return d ? WEEKDAYS[d.getDay()] : empty
}

/** "September 2026" — a month heading, spelled out because it has the room. */
export function fmtMonthYear(value?: string | Date | null, empty = '—'): string {
  const d = value ? parse(value) : null
  if (!d) return empty
  return `${MONTHS_LONG[d.getMonth()]} ${d.getFullYear()}`
}

/** "09:40" from a wall-clock string — "9:4", "9:40:00", "09:40".
 *
 * Distinct from `fmtTime`, which takes a moment. A property's checkout time
 * and a guest's expected arrival are times of day with no date attached: they
 * come out of `<input type="time">` and go back to the server as text, and
 * turning them into a Date first would attach today to them and invite a
 * timezone shift on a value that never had one.
 *
 * Returns `empty` for anything it cannot read, so a half-typed value shows
 * the placeholder rather than "NaN:NaN".
 */
export function fmtClock(value?: string | null, empty = ''): string {
  if (!value) return empty
  const m = /^(\d{1,2}):(\d{2})/.exec(value.trim())
  if (!m) return empty
  const h = Number(m[1])
  const min = Number(m[2])
  if (h > 23 || min > 59) return empty
  return `${String(h).padStart(2, '0')}:${String(min).padStart(2, '0')}`
}
