import { useState } from 'react'

/**
 * The badge that stands for one channel partner, everywhere one is listed.
 *
 * Two things were wrong before this existed. The partner list hashed a tint
 * out of the partner's *name*, so Agoda was whatever colour its letters
 * happened to hash to; the Add Partner picker had its own hand-written tints;
 * and the two disagreed. The same partner had two different colours depending
 * on which screen you were looking at, which is the opposite of what a mark is
 * for — it exists so somebody finds Booking.com in a list without reading.
 *
 * **On the logo files.** Those in `public/ota-logos` are each brand's own
 * touch icon, taken from that brand's own site — Booking.com's from
 * booking.com, and so on. They are trademarks, used here to identify the
 * channel they belong to, which is what a logo is for and what every
 * connectivity partner's brand kit permits. If you have the official kit,
 * replace the file; it is picked up with no other change.
 *
 * Anything below 32px was deliberately left out rather than shipped: three of
 * these sites publish only a 16px favicon, and a 16px image stretched across
 * a 36px tile is a blurry smear that reads as a broken product. Those fall
 * back to the tile below, which is crisp at any size.
 *
 * The fallback is the partner's own brand colour with its initial —
 * recognisable at a glance (Booking.com navy, Agoda pink, Expedia deep blue),
 * nothing trademarked reproduced, and never a broken image, which is worse
 * than no image.
 */

/** Letters and digits, lower-cased: "Booking.com" and "BookingCom" both key
 *  to the same entry, as they do everywhere else in this codebase. */
const norm = (s: string) => s.replace(/[^a-z0-9]/gi, '').toLowerCase()

/**
 * Each channel's primary brand colour, darkened where it had to be.
 *
 * These carry white text at 14px, so each one clears WCAG AA (4.5:1) against
 * white — checked, not assumed. A couple sit a shade deeper than the colour
 * off the brand's own website for that reason: Goibibo's orange and Google's
 * blue are both several points short at their published values, and a mark
 * nobody can read is not on brand either.
 */
const BRANDS: Record<string, { color: string; slug: string }> = {
  bookingcom:   { color: '#003580', slug: 'booking-com' },
  agoda:        { color: '#C2185B', slug: 'agoda' },
  expedia:      { color: '#00355F', slug: 'expedia' },
  makemytrip:   { color: '#C1272D', slug: 'makemytrip' },
  tripcom:      { color: '#1B5FAA', slug: 'trip-com' },
  airbnb:       { color: '#C4384A', slug: 'airbnb' },
  goibibo:      { color: '#C2410C', slug: 'goibibo' },
  cleartrip:    { color: '#B45309', slug: 'cleartrip' },
  yatra:        { color: '#B03060', slug: 'yatra' },
  googlehotels: { color: '#1A73E8', slug: 'google-hotels' },
  easemytrip:   { color: '#0F766E', slug: 'easemytrip' },
}

/** Travel agents and anything else we have no brand for. Hashed from the
 *  name so one agent keeps one colour between loads, which is the whole
 *  reason the old code hashed — it was only ever wrong for the OTAs. */
const NEUTRAL = ['#334155', '#0F766E', '#7C2D12', '#4C1D95', '#155E75', '#831843']

export function partnerColor(name: string): string {
  const brand = BRANDS[norm(name)]
  if (brand) return brand.color
  let h = 0
  for (const c of name) h = (h * 31 + c.charCodeAt(0)) >>> 0
  return NEUTRAL[h % NEUTRAL.length]
}

export default function PartnerMark({ name, size = 36 }: {
  name: string
  /** Pixels. 36 in a list, 48 on a detail page. */
  size?: number
}) {
  const brand = BRANDS[norm(name)]
  // Starts hopeful and gives up quietly. A partner with no logo file must not
  // cost a failed request on every render, so once one 404s we stop asking.
  const [noLogo, setNoLogo] = useState(false)

  if (brand && !noLogo) {
    return (
      <span
        className="grid shrink-0 place-items-center overflow-hidden rounded-lg bg-white ring-1 ring-slate-200"
        style={{ width: size, height: size }}>
        <img src={`/ota-logos/${brand.slug}.png`} alt={name}
          onError={() => setNoLogo(true)}
          className="size-full object-contain p-1" />
      </span>
    )
  }

  return (
    <span
      className="grid shrink-0 place-items-center rounded-lg font-bold text-white"
      style={{
        width: size, height: size,
        backgroundColor: partnerColor(name),
        fontSize: Math.round(size * 0.42),
      }}
      // The letter is decoration beside a name that is always written out
      // next to it; a screen reader saying "A, Agoda" helps nobody.
      aria-hidden="true">
      {name.slice(0, 1).toUpperCase()}
    </span>
  )
}
