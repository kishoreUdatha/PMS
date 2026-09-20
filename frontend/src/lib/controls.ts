/** One spelling of a filter control, for the whole app.
 *
 * Every list screen grew its own filter bar and its own local `input`
 * constant, so nine screens ended up with nine answers to the same question.
 * On the reservation calendar alone one row carried four: the date range
 * semibold slate-700, a button medium slate-600, two selects regular in
 * placeholder grey, and chips a size smaller again — five controls doing one
 * job, written as though they were five levels of importance.
 *
 * Kept as class strings rather than a component because that is all the
 * variation there is. These bars differ in *what* they filter and in nothing
 * else; wrapping them in a `<FilterBar>` would mean a prop for every layout
 * quirk and a rewrite of nine screens to adopt it.
 *
 * The split matters. `CONTROL_TYPE` is size and weight only; `CONTROL` adds
 * the colour. Anything that paints its own text — a selected chip going
 * brand, a destructive action going red — takes the type and nothing else.
 * Two utilities setting `color` in one class string are resolved by the order
 * of the generated stylesheet, not the order they are written, so a chip
 * carrying both `text-slate-600` and `text-white` is a coin toss.
 */

/** Size and weight. For controls that choose their own colour. */
export const CONTROL_TYPE = 'text-sm font-medium'

/** The default: type plus the row's text colour. */
export const CONTROL = `${CONTROL_TYPE} text-slate-600`

/** The box a bordered filter control is drawn in. Height lives here, so a
 *  select, a button and a date field line up along one baseline. */
const BOX = 'rounded-lg border border-slate-200 py-2'

export const FILTER_BOX = `${BOX} px-3`

/** The same box with room for a leading search icon.
 *
 * A separate token rather than `${FILTER_BOX} pl-9`, because `px-3` and
 * `pl-9` both set padding-left and which one lands is decided by the order
 * Tailwind emits them in, not by the order they are written. */
export const FILTER_BOX_ICON = `${BOX} pl-9 pr-3`

/** A filter dropdown. Pair it with `blankIsChoice` on the `Select`: on a
 *  filter bar an empty value is the answer ("All Room Types"), not an
 *  unfilled field, and painting it placeholder grey says the control is
 *  disabled when it is not. */
export const FILTER_SELECT = `${FILTER_BOX} ${CONTROL}`

/** A pill toggle — the counted chips that sit at the end of a filter row.
 *  Colour is deliberately absent; the caller sets it from on/off state. */
export const FILTER_CHIP =
  `flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full px-3 py-2 ${CONTROL_TYPE} transition-colors`
