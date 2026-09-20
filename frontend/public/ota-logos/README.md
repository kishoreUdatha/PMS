# OTA logos

Drop an official logo file here and it appears everywhere that channel is
listed — the partner list, the Add Partner picker, the partner's own page.
Nothing else needs changing; `PartnerMark` looks for the file and falls back to
a brand-coloured tile when it is not there.

## File names

Lower-case, hyphenated, `.svg`:

| Channel | File |
| --- | --- |
| Booking.com | `booking-com.svg` |
| Agoda | `agoda.svg` |
| Expedia | `expedia.svg` |
| MakeMyTrip | `makemytrip.svg` |
| Trip.com | `trip-com.svg` |
| Airbnb | `airbnb.svg` |
| Goibibo | `goibibo.svg` |
| Cleartrip | `cleartrip.svg` |
| Yatra | `yatra.svg` |
| Google Hotels | `google-hotels.svg` |
| EaseMyTrip | `easemytrip.svg` |

SVG because these are drawn from 36px in a table to 48px on a detail page, and
a raster logo is soft at one of those sizes. A transparent-background PNG at
3× works if that is all the brand kit contains — change the extension in
`PartnerMark.tsx` if so.

The mark is rendered on white with 4px of padding, so use the full-colour
version rather than the reversed/white one.

## Where to get them

**Not from a web search.** These are trademarks, and the licence to use one
comes with the connectivity relationship, not with the file being downloadable.
Each OTA publishes a brand or partner asset kit to connected partners:

* Booking.com — Connectivity Partner Portal, Brand assets
* Agoda — YCS partner resources
* Expedia — Expedia Group Partner Central, Brand toolkit
* MakeMyTrip / Goibibo — partner onboarding pack

Using a channel's logo to identify that channel inside a property management
system is ordinarily what those kits permit; it is worth reading the specific
terms, because a few forbid altering the mark's colour or proportions, which
is why this renders the file as-is and does not recolour it.

## Until then

The fallback is the channel's own primary brand colour with its initial —
Booking.com navy, Agoda pink, Expedia deep blue. Recognisable at a glance,
nothing trademarked reproduced, and no broken image if a file is missing.

Every colour carries white text at AA contrast (4.5:1); a couple sit a shade
deeper than the published brand colour for that reason. See the table in
`PartnerMark.tsx`.
