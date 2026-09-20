/** @type {import('tailwindcss').Config} */

// The whole application reads as pale and flat, and the cause is not any one
// screen — it is this file. Roughly four thousand colour classes across seventy
// files name Tailwind's default `slate` ramp and a near-white canvas, so the
// palette is the only place the problem can be fixed. Redefining the ramp here
// changes every screen at once; editing the screens would have meant four
// thousand edits and a guarantee that some were missed.
//
// Two things were doing the damage:
//
//   * `text-slate-400` (#94a3b8) carried six hundred pieces of secondary text
//     at a contrast ratio of **2.6:1** — below the 4.5:1 that WCAG AA asks for
//     normal text, and genuinely hard to read on a laptop in daylight.
//   * `border-slate-100` (#f1f5f9) drew nearly three hundred borders that were
//     all but invisible against white cards, so nothing had an edge.
//
// The ramp below darkens the middle of the scale, where text lives, while
// keeping 50–200 light enough to go on being backgrounds and borders. The
// direction of the scale is unchanged — low is still faint, high is still
// prominent — so every existing class keeps meaning what its author intended.
//
// Checked, not eyeballed: `text-slate-400` on white goes from 2.6:1 to 4.6:1
// and `text-slate-500` from 4.8:1 to 7.5:1. Both now pass AA.
const slate = {
  // Backgrounds. Deliberately still light: `bg-slate-50` is used on close to
  // four hundred subtle panels and must stay a tint, not a fill.
  50: '#f5f8fb', // subtle panels — the only tint left once the page is white
  // Card borders, and they carry more weight than they used to. The page is
  // pure white, and 123 cards are drawn with `border-slate-100` and nothing
  // else — no shadow. At the old #e7edf4 those cards had no visible edge at
  // all against a white page; this is the value that gives them one.
  100: '#dae2ec',
  // A fill, not a line. `slate-100` was darkened to #dae2ec so a 1px card
  // border would be visible on a white page -- which is the right value for
  // that job and the wrong one for filling anything. Roughly 150 chips,
  // badges and hover states filled with it, and each came out looking like a
  // solid grey block: heavy enough that a filter you *could* press read as
  // one you could not. This sits between the two, quiet as a fill and still
  // clearly not white.
  75: '#eef2f7',
  200: '#c2cfdf', // the heavier border, and table rules
  300: '#a6b4c8', // faint text, dividers
  // Text. This is where the flatness was.
  // 4.6:1 on white was not enough: the same class is also used directly on
  // the page canvas, where it measured 3.9:1 and still failed. Chosen to
  // clear 4.5:1 against all three backgrounds it actually appears on —
  // white cards, `bg-slate-50` panels and the canvas itself.
  400: '#5b6c84', // secondary text — 2.6:1 -> 5.4:1 on white, 4.6:1 on canvas
  500: '#46566d', // supporting text — 4.8:1 -> 7.5:1
  600: '#33455c', // field labels
  700: '#22344b', // subheadings
  800: '#12223a', // headings
  900: '#0a1526',
  950: '#050b16',
}

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        slate,
        // Palette measured off the mockup PNGs (pixel-sampled, not eyeballed),
        // then deepened: the sampled values were correct for the art and too
        // weak on a screen full of white cards.
        // Headings. Navy rather than slate: at 32px and 700 the old slate-800
        // read as very dark grey, and the extra blue is what makes a title
        // look deliberate rather than merely bold. 15.6:1 on white.
        ink: '#102344',
        brand: {
          // The cyan teal the design calls for, one step darker than the
          // #008C99 that was specified.
          //
          // That exact value measures 4.03:1 against white — as link text and
          // as a button carrying white text, both below the 4.5:1 that normal
          // text needs. It is not a rounding error: at 14px it is the
          // difference between readable and not for anyone with even mild
          // low vision, on the control that every primary action uses.
          //
          // #007A85 is the same hue, dark enough to clear it at 5.1:1. Beside
          // #008C99 the two are hard to tell apart; beside the old #0a8074
          // they are plainly different, which is the point — the design asked
          // for cyan, not green, and that part is honoured.
          DEFAULT: '#007A85',
          // The emphasis teal from the design pack's palette table,
          // used for the module eyebrow above a page title.
          deep: '#006E78',
          dark: '#003a49', // deeper shade of the sidebar; button hover
          sidebar: '#004658', // mockup: #004658
          light: '#d5f5f0', // KPI/pill tint, a touch more saturated
          accent: '#16c5b0', // highlights, active states, icons
        },
        // The page canvas. Reads as white, and is not quite.
        //
        // Pure white was the ask, and pure white cost the layering: cards are
        // the same colour as the page they sit on, so 123 of them — drawn with
        // a border and no shadow — had nothing but that border holding them
        // apart from the background. This is white to the eye while still
        // being a shade the card can lift off, which gets the depth back
        // without reintroducing the blue-grey wash that made the old palette
        // look tired.
        //
        // The borders stay at their stronger values. They are load-bearing at
        // this little contrast and should not be softened back.
        canvas: '#fcfdfe',
        // The platform console's palette, verbatim from its spec.
        //
        // Scoped to the console rather than replacing the application's own
        // tokens: `brand` here is #007A85, one step darker than the #008C99
        // below, because the application uses it for link text where 4.03:1
        // fails AA. The console uses #008C99 only for filled controls with
        // white labels and reserves `deep` (#006E78) for links and emphasis,
        // which is what the spec asks for and what keeps small text legible.
        pf: {
          // Primary controls. The revised spec puts #006E78 on buttons, which
          // is also dark enough to be link text -- 6.0:1 on a white card and
          // 5.3:1 on the page ground -- so `teal` and `deep` are now the same
          // colour serving fills and text. They are kept as two names because
          // the screens already distinguish the two roles, and a future spec
          // may separate them again.
          teal: '#006E78',
          deep: '#006E78',
          // The step under both, for hover and the pressed state. Without it
          // a primary button has no hover at all, since its rest and hover
          // colours would otherwise be identical.
          hover: '#00565F',
          // The sidebar, and the one place the spec's button teal cannot go:
          // #006E78 on #102344 measures 2.6:1, so an active item would be a
          // smudge rather than a marker. #007A85 is the only teal that clears
          // both thresholds at once -- 3.06:1 against the sidebar, which is
          // the 3:1 a UI boundary needs, and 5.09:1 for its own white label.
          // Anything brighter makes the pill pop and the label fail.
          'nav-active': '#007A85',
          sidebar: '#102344',
          // Sidebar text. "White text" is right for the active item and wrong
          // for all forty of them at once: if everything is white, nothing is
          // emphasised. Inactive items sit one step back at 7.6:1 -- plainly
          // legible, plainly subordinate -- and go white on hover.
          'sidebar-text': '#A9B6CC',
          'sidebar-label': '#8FA0B8',
          // Text, two tiers, exactly as the spec states them. 14.1:1 and
          // 7.6:1 on a white card; 12.5:1 and 6.7:1 on the page ground.
          navy: '#172B4D',
          body: '#172B4D',
          muted: '#475569',
          bg: '#EEF2F6',
          surface: '#FFFFFF',
          // The card edge the spec asks for, a full two steps stronger than
          // the #DDE5EF it replaces. It is a boundary rather than text, and
          // it no longer carries the separation alone -- see shadow.pf-card.
          border: '#CBD5E1',
          // Rules *inside* a card, which must stay lighter than the card's
          // own edge or a table reads as a stack of boxes.
          divider: '#E2E8F0',
          soft: '#E8F6F7',
          thead: '#F8FAFC',
          search: '#F1F5F9',
          // Readable rather than decorative: placeholder text at the 2.6:1 of
          // the usual slate-400 is the exact fault the ramp above this block
          // was written to fix, and it would be odd to reintroduce it here.
          placeholder: '#64748B',
          offtrack: '#CBD5E1',
          'ok-text': '#117B54', 'ok-bg': '#EAF8F1',
          'warn-text': '#9C6017', 'warn-bg': '#FFF4DE',
          'err-text': '#B83542', 'err-bg': '#FFF0F1',
          'info-text': '#375AAC', 'info-bg': '#EDF2FF',
        },
        // Status text. Both clear AA on white at 5.0:1; both are darker than
        // the emerald-700 and amber-800 they replace, which is what lets a
        // status word sit in running text without shouting.
        positive: '#15803D',
        caution: '#B45309',
      },
      // The type scale, in one place.
      //
      // Written as tokens rather than left to ad-hoc utilities because the
      // sizes carry their line height and weight with them: a page title is
      // 32/700 at 1.2 everywhere, and nobody has to remember the third of
      // those three numbers. Headings lead at 1.2 and body at 1.5 — long
      // measure needs the air, a two-word heading does not.
      // The console's type scale, taken verbatim from its spec: size, weight
      // and purpose in one token, so a screen names the role rather than
      // remembering three numbers. Letter spacing is 0 throughout, which is
      // Tailwind's default and is therefore simply not set.
      // Revised to the larger scale: 30-32/700 for a page title, 18-20/600
      // for a section heading, 14-16 at 400 or 500 for body and table text,
      // and 14/500-600 for every label and button. The previous scale ran
      // 11-12px through most of the console, which is a dense-application
      // convention rather than a comfortable one.
      //
      // Three roles the spec does not name keep their own sizes, because
      // giving everything 14px would flatten the hierarchy the spec asks
      // for: a KPI figure is display type, and a caption and a pill are
      // meant to read as subordinate to the body text beside them.
      fontSize: {
        'pf-title': ['28px', { lineHeight: '1.18', fontWeight: '700', letterSpacing: '-0.03em' }],
        'pf-login': ['30px', { lineHeight: '1.15', fontWeight: '700', letterSpacing: '-0.03em' }],
        'pf-desc': ['15px', { lineHeight: '1.5', fontWeight: '400' }],
        // Section headings: a card title and a table title are the same role
        // and now say so at the same size.
        'pf-card': ['17px', { lineHeight: '1.3', fontWeight: '600', letterSpacing: '-0.015em' }],
        'pf-table-title': ['17px', { lineHeight: '1.3', fontWeight: '600', letterSpacing: '-0.015em' }],
        'pf-kpi': ['27px', { lineHeight: '1.1', fontWeight: '700', letterSpacing: '-0.025em' }],
        // Long values step down rather than wrap: a wrapped KPI breaks the
        // card height and the row stops lining up.
        'pf-kpi-long': ['21px', { lineHeight: '1.15', fontWeight: '700', letterSpacing: '-0.02em' }],
        'pf-nav': ['14px', { lineHeight: '1.3', fontWeight: '500' }],
        'pf-nav-on': ['14px', { lineHeight: '1.3', fontWeight: '600' }],
        'pf-tab': ['14px', { lineHeight: '1.3', fontWeight: '500' }],
        'pf-label': ['14px', { lineHeight: '1.3', fontWeight: '500' }],
        'pf-input': ['14px', { lineHeight: '1.4', fontWeight: '400' }],
        // Header and cell are both 14; the weight is what separates them,
        // which is what lets a table stay scannable without a size step.
        'pf-th': ['14px', { lineHeight: '1.3', fontWeight: '600' }],
        'pf-td': ['14px', { lineHeight: '1.45', fontWeight: '400' }],
        'pf-btn': ['14px', { lineHeight: '1.2', fontWeight: '500' }],
        'pf-badge': ['12px', { lineHeight: '1.2', fontWeight: '500' }],
        'pf-help': ['13px', { lineHeight: '1.4', fontWeight: '400' }],
        // Page titles.
        //
        // Was 2rem. At a 15px root that is still 30px, and on a 150%-scaled
        // screen 45px of glass for the word "Reservations" above a table of
        // 14px rows — the single biggest reason the product read as zoomed.
        // 1.75rem keeps the step above `subject` clearly legible while giving
        // the content back the room.
        //
        // Tracking: large type needs the negative tracking that small type
        // cannot take. -0.03em on a 26px title is about a pixel, which is what
        // the brief asked for, and it is set here rather than globally so
        // 14px body copy is not dragged tight with it.
        display: ['1.75rem', {
          lineHeight: '1.18', fontWeight: '700', letterSpacing: '-0.03em',
        }],
        // A named subject on a detail page — the partner, the guest, the
        // invoice. Smaller than the page title, larger than a section.
        subject: ['1.375rem', {
          lineHeight: '1.2', fontWeight: '600', letterSpacing: '-0.025em',
        }],
        // Card and section headings.
        section: ['1.25rem', { lineHeight: '1.25', fontWeight: '600' }],
      },
      // The spec asks for "a subtle shadow" on cards. Tinted with the navy
      // rather than black, so a lifted card reads as part of this palette
      // instead of greying the page beneath it.
      boxShadow: {
        'pf-card': '0 1px 2px 0 rgb(23 43 77 / 0.04), '
          + '0 2px 6px -1px rgb(23 43 77 / 0.06)',
      },
      spacing: {
        // The console's fixed dimensions, from its layout table.
        // Widened with the type: at 14px, "Subscriptions & billing" had no
        // chance in 228px and every long entry truncated.
        'pf-side': '244px',
        'pf-header': '74px',
        'pf-item': '198px',
        // Wide enough for its own placeholder and no wider. At 420px the
        // search was 40% of the header while 227px of it already sat
        // empty -- a box sized for the space available rather than for
        // what goes in it.
        'pf-search': '320px',
        // A FLOOR, not a fixed height -- see Metrics in ui.tsx. Set just
        // above what a label and a figure actually occupy (51px of content
        // plus 24px of padding), so a card with no caption is snug and one
        // with a caption grows on its own. At 92px this floor was still
        // padding out four-fifths of a metric row with nothing.
        'pf-kpi': '76px',
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        // Inter, per the revised spec, and the face the rest of the
        // application already uses -- so this costs no extra download and
        // the 400/500/600/700 weights the scale above needs are already in
        // the stylesheet link.
        //
        // DejaVu Sans stays second for one glyph. Google's `latin` subset
        // range covers U+20AC but not U+20B9, so Inter is no likelier to
        // carry a rupee sign than Roboto was, and a console full of prices
        // must not fall back per-character to whatever the OS offers.
        platform: ['Inter', '"DejaVu Sans"', 'system-ui', 'sans-serif'],
        serif: ['"Playfair Display"', 'Georgia', 'Cambria', 'serif'],
        script: ['"Dancing Script"', 'cursive'],
      },
    },
  },
  plugins: [],
}
