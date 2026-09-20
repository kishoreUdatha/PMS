/** When two labels name the same screen.
 *
 *  Used by both breadcrumb trails -- the tenant app's <Crumbs/> and the
 *  platform console's <Page/> -- so the two cannot drift into different ideas
 *  of what counts as a repeat.
 *
 *  Equality was the first attempt and was far too strict. Real pairs that mean
 *  one thing and fail it:
 *
 *      Overview              / Platform overview
 *      Room Types            / Room Type Management
 *      Room Move / Upgrade   / Room Move and Upgrade
 *      Taxes & Charges       / Tax and Service Charge Setup
 *
 *  So compare word sets, after dropping punctuation, the word "and", and the
 *  plural. One label restates the other when every word of the shorter appears
 *  in the longer -- which is the question actually being asked: does this
 *  crumb tell the reader anything the heading below it does not.
 */

/** Singular, lowercase, punctuation-free words. */
function words(s: string): Set<string> {
  return new Set(
    s.toLowerCase()
      .replace(/[^a-z0-9]+/g, ' ')
      .split(' ')
      .filter((w) => w && w !== 'and')
      // "taxes" -> "tax", "companies" -> "companie", "types" -> "type".
      // Crude, and it only has to be consistent: both sides get the same
      // treatment, so an imperfect stem still compares equal to itself.
      .map((w) => (/(x|s|ch|sh)es$/.test(w) ? w.slice(0, -2) : w))
      .map((w) => (w.length > 3 && w.endsWith('s') ? w.slice(0, -1) : w)),
  )
}

export function sameThing(a: string, b: string): boolean {
  const x = words(a)
  const y = words(b)
  if (x.size === 0 || y.size === 0) return false
  const [small, big] = x.size <= y.size ? [x, y] : [y, x]
  return [...small].every((w) => big.has(w))
}
