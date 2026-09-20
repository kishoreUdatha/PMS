import { type ReactNode } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { sameThing } from '../lib/crumbs'
import { AlertTriangle, ChevronRight, Info, Loader2 } from 'lucide-react'

/** Shared primitives for the platform console.
 *
 *  These use the application's own palette — brand teal, ink headings, the
 *  white canvas and the tuned slate ramp — rather than a theme of their own.
 *  An earlier version of this file was dark slate and amber, which looked
 *  deliberate and was not: the slate ramp in tailwind.config.js is calibrated
 *  for text on *light* backgrounds (slate-400 is #5b6c84, chosen to clear
 *  4.5:1 on white), so reusing those classes on a near-black card put most of
 *  the console's secondary text at roughly 2:1.
 *
 *  The tier is signalled by the banner in PlatformLayout instead, which says
 *  what is actually true — these screens act across tenants — where a colour
 *  could only imply it.
 */

/** The page chrome from the design pack: breadcrumb, eyebrow, title, action.
 *
 *  `eyebrow` is the module name in teal caps above the title, and `crumbs` the
 *  trail above that. Both optional so the screens written before the designs
 *  arrived still render; both supplied, a page matches the artboards.
 */
export function Page({ title, subtitle, actions, eyebrow, crumbs, children }: {
  title: string
  subtitle?: string
  actions?: ReactNode
  eyebrow?: string
  crumbs?: { label: string; to?: string }[]
  children: ReactNode
}) {
  // One line of context, not two.
  //
  // Every screen passed a breadcrumb whose last crumb repeated the title, and
  // an eyebrow naming the section the breadcrumb already named. So the header
  // stacked four rows -- "Overview > Onboarding progress", "TENANTS",
  // "Onboarding progress", subtitle -- to say two things, and pushed the
  // actual content 168px down the page.
  //
  // Built here rather than by editing twenty-three screens: the duplication
  // was in the shape of the component, so the fix belongs in the same place.
  const { pathname } = useLocation()

  // A trailing crumb that only restates the title is dropped -- but only when
  // it is plain text. A last crumb carrying a `to` is a link somebody can
  // follow, and losing a link is worse than repeating a word.
  const trail = (crumbs ?? []).filter(
    (c, i, all) => !(i === all.length - 1 && !c.to && sameThing(title, c.label)))
  // The eyebrow joins the trail only when it adds something. On a screen
  // whose section IS its title -- Tenants under Tenants -- appending it just
  // reinstated the repetition the filter above had removed.
  const adds = eyebrow
    && !sameThing(title, eyebrow)
    && !trail.some((c) => sameThing(c.label, eyebrow))
  // A crumb pointing at the page you are already on is not a way back to
  // anywhere. Every screen passes "Overview -> /platform" as its root, so
  // on /platform itself the trail was a link to itself; dropping it leaves
  // nothing, and the overview correctly shows no breadcrumb at all.
  const context = (adds ? [...trail, { label: eyebrow as string }] : trail)
    .filter((c) => c.to !== pathname)

  return (
    // Left 16px, right 6px -- deliberately not symmetric.
    //
    // The scroll container is <main>, so its scrollbar occupies ~10px INSIDE
    // the padded area. Equal padding therefore reads as a wider right gutter:
    // 16 on the left against 16+10 on the right. Taking the scrollbar off the
    // right padding makes the two *look* equal, which is the only sense in
    // which they should be. Paired with scrollbar-gutter:stable on <main>, so
    // the 10px is reserved whether or not the page scrolls.
    <div className="pb-10 pl-4 pr-1.5 pt-6">
      {context.length > 0 && (
        <nav aria-label="Breadcrumb" className="mb-2 flex items-center gap-2 text-pf-help">
          {context.map((c, i) => (
            <span key={c.label} className="flex items-center gap-2">
              {i > 0 && <ChevronRight size={13} className="text-pf-placeholder" />}
              {c.to
                ? <Link to={c.to} className="text-pf-muted hover:text-pf-deep">{c.label}</Link>
                : <span className="text-pf-muted">{c.label}</span>}
            </span>
          ))}
        </nav>
      )}
      <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-pf-title text-pf-navy">{title}</h1>
          {subtitle && (
            <p className="mt-1.5 text-pf-desc text-pf-muted">{subtitle}</p>
          )}
        </div>
        {actions}
      </div>
      {children}
    </div>
  )
}

/** The KPI strip the artboards put under every list title.
 *
 *  Four cards, label above a large figure, a caption under it. The first
 *  carries a teal top rule — in the pack that marks the headline number, the
 *  one the screen is really about, and it is the only accent on the row.
 */
export function Metrics({ items }: {
  items: {
    label: string
    value: ReactNode
    caption?: string
    tone?: 'default' | 'warn' | 'good'
  }[]
}) {
  // Columns follow the number of metrics, rather than a fixed four.
  //
  // Hard-coding lg:grid-cols-4 meant a fifth metric dropped onto a row of its
  // own, leaving three-quarters of a band empty -- far more wasted space than
  // anything inside the cards.
  //
  // auto-fit was the first attempt and was worse in a subtle way: it fits as
  // many columns as the container allows, so at 950px it still chose four and
  // orphaned the fifth. Whether the row is whole then depends on the window
  // width, which is not something a layout should leave to chance.
  //
  // Written out rather than built from a template literal because Tailwind
  // scans source text for class names -- a computed string produces no CSS.
  const columns = ({
    1: 'grid-cols-1',
    2: 'grid-cols-2',
    3: 'sm:grid-cols-3',
    4: 'sm:grid-cols-2 lg:grid-cols-4',
    5: 'sm:grid-cols-3 lg:grid-cols-5',
    6: 'sm:grid-cols-3 lg:grid-cols-6',
  } as Record<number, string>)[items.length] ?? 'sm:grid-cols-2 lg:grid-cols-4'

  return (
    <div className={`mb-4 grid grid-cols-1 gap-[14px] ${columns}`}>
      {items.map((m, i) => {
        // A long figure steps down a size rather than wrapping: wrapping
        // makes one card taller than its neighbours and the row stops lining
        // up.
        const long = String(m.value ?? '').length > 9
        return (
          // A MINIMUM height, not a fixed one, with real padding instead of
          // flex slack. Fixed at 118px, a card with no caption was half
          // empty -- 59.5px of content in a 118px box -- and every metric row
          // read as a bank of oversized boxes.
          //
          // Nothing is lost by letting content set the height: the cards sit
          // in a CSS grid, and grid items already stretch to the tallest in
          // their row. The alignment the fixed height was protecting comes
          // free.
          <div key={m.label}
            className={`flex min-h-pf-kpi flex-col justify-center rounded-lg border border-pf-border bg-white shadow-pf-card px-5 py-3 ${
              i === 0 ? 'border-t-2 border-t-pf-teal' : ''}`}>
            {/* pf-label, not pf-desc: this is a label, and the spec puts
                labels at 14/500 rather than body's 15/400. */}
            {/* One line, always. Grid items stretch to the tallest in the
                row, so a label that wraps does not just make its own card
                taller -- it pads out every card beside it. One long label
                was adding 90px of nothing to four neighbours. The full text
                stays available on hover rather than being lost. */}
            <div className="truncate text-pf-label text-pf-muted"
              title={m.label}>{m.label}</div>
            <div className={`mt-1 ${long ? 'text-pf-kpi-long' : 'text-pf-kpi'} ${
              m.tone === 'warn' ? 'text-pf-warn-text'
                : m.tone === 'good' ? 'text-pf-ok-text'
                  : i === 0 ? 'text-pf-deep' : 'text-pf-navy'}`}>
              {m.value}
            </div>
            {m.caption && (
              <div className="mt-1.5 truncate text-pf-help text-pf-muted"
                title={m.caption}>{m.caption}</div>
            )}
          </div>
        )
      })}
    </div>
  )
}

/** The filter card: controls on the left, one primary action on the right.
 *
 *  The pack applies filters on a button rather than on change. Kept, because
 *  a directory that re-queries on every keystroke of a select is noisier than
 *  it is helpful, and the button is where the record count settles.
 */
export function FilterBar({ children, onApply, applying }: {
  children: ReactNode; onApply?: () => void; applying?: boolean
}) {
  return (
    <div className="mb-4 flex flex-wrap items-end justify-between gap-4 rounded-lg border border-pf-border bg-white shadow-pf-card p-4">
      <div className="flex flex-wrap items-end gap-4">{children}</div>
      {onApply && (
        <button type="button" onClick={onApply} disabled={applying}
          className="rounded-md bg-pf-teal px-5 py-2.5 text-pf-btn text-white transition hover:bg-pf-hover disabled:opacity-50">
          {applying ? 'Applying…' : 'Apply filters'}
        </button>
      )}
    </div>
  )
}

/** A labelled select, sized and spaced like the pack's filter controls. */
export function Select({ label, value, onChange, options, id }: {
  label: string
  value: string
  onChange: (v: string) => void
  options: { value: string; label: string }[]
  id: string
}) {
  return (
    <div className="w-56">
      <label htmlFor={id} className="mb-1.5 block text-pf-label text-pf-muted">
        {label}
      </label>
      <select id={id} value={value} onChange={(e) => onChange(e.target.value)}
        className="w-full rounded-md border border-pf-border bg-white px-3 py-2.5 text-pf-input text-pf-body outline-none focus:border-pf-teal">
        {options.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
    </div>
  )
}

/** A table inside a titled card, the way every list in the pack is drawn.
 *
 *  The header carries the record count and the footer says what is being
 *  shown out of what. Both matter on a console: "6 records" and "showing 6 of
 *  6" together tell you whether a filter is hiding something, which a bare
 *  table cannot.
 */
export function DataTable({ title, head, count, footnote, action, children,
  empty }: {
  title: string
  head: string[]
  count?: number
  footnote?: ReactNode
  action?: ReactNode
  children: ReactNode
  empty?: string
}) {
  const rows = Array.isArray(children) ? children.flat() : [children]
  const isEmpty = rows.filter(Boolean).length === 0
  return (
    <div className="rounded-lg border border-pf-border bg-white shadow-pf-card">
      <div className="flex items-center justify-between gap-3 px-5 py-4">
        <h2 className="text-pf-table-title text-pf-navy">{title}</h2>
        {count !== undefined && (
          <span className="text-pf-help text-pf-muted">
            {count} record{count === 1 ? '' : 's'}
          </span>
        )}
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-y border-pf-divider bg-pf-thead text-left text-pf-th text-pf-muted">
              {head.map((h) => (
                <th key={h} className="whitespace-nowrap px-5 py-3">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-pf-divider">
            {isEmpty ? (
              <tr>
                <td colSpan={head.length}
                  className="px-5 py-12 text-center text-sm text-pf-muted">
                  {empty || 'Nothing to show.'}
                </td>
              </tr>
            ) : children}
          </tbody>
        </table>
      </div>
      {(footnote || action) && (
        <div className="flex flex-wrap items-center justify-between gap-2 px-5 py-3.5 text-pf-help text-pf-muted">
          <span>{footnote}</span>
          {action}
        </div>
      )}
    </div>
  )
}

/** The side panel the artboards pair with a list: a title over label/value
 *  rows, right-aligned values.
 *
 *  Used for the overview's Action queue and Service snapshot. Deliberately not
 *  a table — these are a handful of named facts, and a table's header row
 *  would be three words of chrome over two rows of content.
 */
export function Panel({ title, rows, empty }: {
  title: string
  rows: { label: string; value: ReactNode; tone?: 'default' | 'warn' | 'good' }[]
  empty?: string
}) {
  return (
    <div className="rounded-lg border border-pf-border bg-white shadow-pf-card p-5">
      <h2 className="text-pf-card text-pf-navy">{title}</h2>
      {rows.length === 0 ? (
        <p className="mt-3 text-pf-help text-pf-muted">
          {empty || 'Nothing to report.'}
        </p>
      ) : (
        <dl className="mt-3">
          {rows.map((r) => (
            <div key={r.label}
              className="flex items-baseline justify-between gap-4 border-b border-pf-divider py-2.5 last:border-0 last:pb-0">
              <dt className="shrink-0 text-pf-td text-pf-muted">{r.label}</dt>
              <dd className={`text-right text-pf-td font-medium ${
                r.tone === 'warn' ? 'text-pf-warn-text'
                  : r.tone === 'good' ? 'text-pf-ok-text' : 'text-pf-navy'}`}>
                {r.value}
              </dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  )
}

/** The artboards' main layout: a list at 944px beside a 412px rail, 20px
 *  apart. Below the breakpoint the rail drops underneath, because a 412px
 *  panel beside a table on a laptop leaves neither enough room.
 */
/** The pack's two-column layout: a wide list beside a narrow rail.
 *
 *  Proportional rather than fixed, and that is the whole of the fix. The pack
 *  specifies 944px + 412px, which needs 1376px of content area -- about a
 *  1660px window once the sidebar and gutters are paid for. Below that the
 *  rail kept its 412px and the list absorbed every missing pixel, so the
 *  tenant table was handed 556px for 652px of columns and clipped its last
 *  one.
 *
 *  Two thirds and one third holds the pack's proportion (944:412 is very
 *  nearly 7:3) at any width, and the rail is capped at the specified 412 so a
 *  very wide screen still matches the design rather than growing a giant
 *  sidebar. Below `xl` the two stack, which is what they already did.
 */
export function SplitLayout({ main, side }: { main: ReactNode; side: ReactNode }) {
  return (
    <div className="flex flex-col gap-5 xl:flex-row">
      {/* min-w-0 so the table inside can shrink rather than forcing the row
          wider than the page -- without it a flex item refuses to go below
          its content's width and the whole layout overflows sideways. */}
      <div className="min-w-0 xl:flex-[2]">{main}</div>
      <div className="space-y-5 xl:flex-[1] xl:min-w-[300px] xl:max-w-[412px]">
        {side}
      </div>
    </div>
  )
}

/** The ⓘ line the pack puts under a list, explaining its scope. */
export function Note({ children }: { children: ReactNode }) {
  return (
    <p className="mt-4 flex items-start gap-2.5 text-pf-help text-pf-muted">
      <Info size={15} className="mt-0.5 shrink-0 text-pf-placeholder" />
      <span>{children}</span>
    </p>
  )
}

export function Card({ children, className = '' }: {
  children: ReactNode; className?: string
}) {
  return (
    <div className={`rounded-lg border border-pf-border bg-white shadow-pf-card ${className}`}>
      {children}
    </div>
  )
}

export function Table({ head, children, empty }: {
  head: string[]; children: ReactNode; empty?: string
}) {
  const rows = Array.isArray(children) ? children.flat() : [children]
  const isEmpty = rows.filter(Boolean).length === 0
  return (
    <Card className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-pf-divider text-left text-xs uppercase tracking-wide text-pf-muted">
            {head.map((h) => (
              <th key={h} className="px-4 py-3 font-medium">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-pf-divider">
          {isEmpty ? (
            <tr>
              <td colSpan={head.length}
                className="px-4 py-10 text-center text-sm text-pf-muted">
                {empty || 'Nothing to show.'}
              </td>
            </tr>
          ) : children}
        </tbody>
      </table>
    </Card>
  )
}

export function Td({ children, className = '' }: {
  children: ReactNode; className?: string
}) {
  return (
    <td className={`px-5 py-3.5 align-middle text-pf-td text-pf-body ${className}`}>
      {children}
    </td>
  )
}

/** A status word, coloured by what it means rather than by its spelling. */
export function Pill({ value }: { value: string }) {
  const v = (value || '').toLowerCase()
  const tone = v === 'active' || v === 'completed' || v === 'inspected'
    ? 'bg-pf-ok-bg text-pf-ok-text'
    : v === 'suspended' || v === 'revoked' || v === 'failed'
      ? 'bg-pf-err-bg text-pf-err-text'
      : v === 'invited' || v === 'pending' || v === 'running'
        ? 'bg-pf-warn-bg text-pf-warn-text'
        : 'bg-pf-bg text-pf-muted'
  // The database says `past_due`; a person reads "Past due". The pill is the
  // one place an enum value is shown to somebody, so it is the one place
  // worth translating it.
  const label = (value || '')
    .replace(/_/g, ' ')
    .replace(/^./, (c) => c.toUpperCase())
  return (
    <span className={`inline-block rounded-full px-2.5 py-1 text-pf-badge ${tone}`}>
      {label}
    </span>
  )
}

export function Button({ children, onClick, type = 'button', tone = 'default',
  disabled, className = '' }: {
  children: ReactNode
  onClick?: () => void
  type?: 'button' | 'submit'
  tone?: 'default' | 'primary' | 'danger'
  disabled?: boolean
  className?: string
}) {
  const tones = {
    default: 'border border-pf-border text-pf-navy hover:bg-pf-bg',
    primary: 'bg-pf-teal text-white hover:bg-pf-hover',
    danger: 'bg-pf-err-text text-white hover:brightness-90',
  }
  return (
    <button type={type} onClick={onClick} disabled={disabled}
      className={`rounded-md px-3.5 py-2 text-pf-btn transition disabled:opacity-50 ${tones[tone]} ${className}`}>
      {children}
    </button>
  )
}

export const inputClass =
  'w-full rounded-md border border-pf-border bg-white px-3 py-2.5 text-pf-input '
  + 'text-pf-body outline-none placeholder:text-pf-placeholder focus:border-pf-teal'

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-pf-label text-pf-muted">{label}</span>
      {children}
    </label>
  )
}

export function Busy() {
  return (
    <div className="flex items-center justify-center py-16 text-pf-placeholder">
      <Loader2 className="animate-spin" size={20} />
    </div>
  )
}

export function ErrorNote({ children }: { children: ReactNode }) {
  if (!children) return null
  return (
    <div className="mb-4 flex items-start gap-2 rounded-lg bg-pf-err-bg px-3 py-2 text-sm text-pf-err-text">
      <AlertTriangle size={15} className="mt-0.5 shrink-0" />
      <span>{children}</span>
    </div>
  )
}

/** Pull the server's message out of an axios error.
 *
 *  The platform routes refuse things for reasons worth reading — "that account
 *  belongs to Lekhana Resort", "this is the only platform administrator left"
 *  — and a generic "Request failed" would throw all of it away.
 */
export function errorText(err: unknown, fallback = 'Something went wrong.'): string {
  const detail = (err as { response?: { data?: { detail?: unknown } } })
    .response?.data?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail) && detail.length) {
    const first = detail[0] as { msg?: string }
    if (first?.msg) return first.msg
  }
  return fallback
}

/** Re-exported so the console's screens keep importing it from one place.
 *  The implementation is shared with the tenant application.
 */
export { askReason, askText, notify } from '../components/AskDialog'
