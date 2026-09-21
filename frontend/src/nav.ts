import {
  BarChart3, Building2, CalendarDays, ClipboardList, CreditCard, Globe, Home, ShieldCheck, Tag, Users, Users2, UtensilsCrossed, Wallet,
} from 'lucide-react'
import { Broom } from './components/icons'

/** Lucide icons and our own SVGs both render from a `className` alone. */
export type NavIcon = React.ComponentType<{ className?: string; size?: number | string }>

export interface NavItem {
  label: string
  path: string
  icon: NavIcon
  children?: { label: string; path: string }[]
  /** Which live count to show beside the label, if any. The sidebar then
   *  reads as a worklist rather than a set of destinations: a receptionist
   *  sees that three guests are waiting without opening anything. */
  badge?: 'arrivals' | 'ota'
}

// How this menu is ordered, and the one rule behind it:
//
//   **If somebody does it every day, it is one click. If they set it up once,
//   it lives in a group.**
//
// Frequency, not subject matter. That is the whole rule, and it is worth
// stating because the obvious alternative — grouping by topic — produced the
// menu this replaces: eighteen top-level entries, money reachable from five
// places, distribution from three, and three different buckets for
// configuration ("Property", "Administration" and a "Settings" that was
// itself the property's settings, sitting below the Property group it was not
// part of). Every one of those was defensible on its own. Together nobody
// could say where anything lived.
//
// The rule also settles the arguments the old menu kept having with itself:
//
// * Stayview stays flat. Nesting it puts a click in front of the busiest
//   screen in the application, every time it is opened, for ever.
// * Room Inventory lives in Property Setup, next to the room types and
//   buildings it is configured alongside, rather than with the daily work.
// * Sales Channels sits with Channel Partners under Distribution, and now
//   answers at /distribution/sales-channels too. It used to be listed here
//   while living at /admin/sales-channels, so the sidebar said one thing and
//   the URL and breadcrumb said another; /admin/sales-channels redirects.
// * Rates and Distribution are two groups, not one. They were merged on the
//   reasoning that somebody sets a rate and then pushes it somewhere — true,
//   but it sat "Channel Partners" beside "Sales Channels", two names that
//   read as the same thing and are not, under a heading whose own name
//   repeated a child's ("Rates & Distribution" containing "Rates &
//   Inventory"). Pricing and where-we-sell are different questions.
//
// Three screens that were reachable only from inside other pages — Invoice
// Settings, Room Blocks, Company Accounts — get a home here. They were never
// broken, just unfindable by anyone who had not already been sent to them.
//
// Five screens are deliberately absent, each at the owner's request, and all
// still reachable by URL — being unlisted is not being deleted:
//
// * Today's Rack (/rooms/rack) — an hour-by-hour view of one day. Built and
//   working; not wanted in the menu.
// * Work Orders (/work-orders) — maintenance jobs, with status-driven
//   timestamps and a back-office report behind them. Built and working;
//   faults are not tracked here on this deployment.
// * Enquiries (/reservations/enquiries) — built, and still reachable from the
//   tab strip on the Reservations header, which is where it is used from.
// * Restaurant & POS (/pos) — still a placeholder. A menu entry leading to a
//   dashed "to be implemented" box promises a feature that does not exist;
//   it returns when the screen does. Guest Services (/services) was one of
//   these and is now built, so it is listed above; its price list lives at
//   /services/menu, reached from the screen itself rather than the sidebar,
//   because it is set up once and then left alone.
export const navItems: NavItem[] = [
  // ----------------------------------------------------------------- daily
  // Everything down to Payments is opened by somebody doing their job right
  // now. None of it is nested, and none of it should become nested.
  { label: 'Dashboard', path: '/', icon: Home },
  { label: 'Stayview', path: '/reservations', icon: CalendarDays },
  { label: 'Reservations', path: '/reservations/list', icon: ClipboardList,
    badge: 'arrivals' },
  // Rooms held for a group without being sold. Beside Reservations because a
  // block becomes reservations -- and because the rooms it is holding are
  // rooms the desk cannot sell, which is worth being able to see.
  { label: 'Group Blocks', path: '/reservations/group-blocks', icon: Users2 },
  // Foreign guests reported to the Bureau of Immigration. Its own entry
  // because the 24-hour clock starts at check-in and nobody goes looking
  // for a legal deadline inside a submenu.
  { label: 'Form C', path: '/reservations/form-c', icon: ShieldCheck },
  // One day by the hour, as opposed to Stayview's multi-day grid. Named for
  // the difference, because side by side the two otherwise read as one
  // screen.
  { label: 'Housekeeping', path: '/housekeeping', icon: Broom },
  // What is broken and who is fixing it. Looked at every day, so one
  // click, beside the rooms it is usually about.
  { label: 'Guests', path: '/guests', icon: Users },
  // What a guest orders during the stay -- food, drinks, tiffin, laundry --
  // put on their folio and settled at check-out. Beside Guests and Payments
  // because that is the counter it is used from, by the same person, while
  // the guest is on the phone.
  { label: 'Guest Services', path: '/services', icon: UtensilsCrossed },
  // Cashiering. Out here with the daily work rather than inside Finance: it
  // is a desk screen, used while a guest is standing there.
  //
  // A group of two rather than one entry, against the frequency rule above,
  // and deliberately: counting a drawer and taking a payment are different
  // jobs done by the same person at the same counter, and the drawer was a
  // tab inside the payments screen that nothing could link to. Naming it in
  // the menu is how a cashier finds it at shift end without being told where
  // it hides.
  {
    label: 'Cashiering',
    path: '/payments',
    icon: CreditCard,
    children: [
      { label: 'Cashier Centre', path: '/payments' },
      { label: 'Cash Drawer', path: '/payments/drawer' },
      { label: 'Day Book', path: '/payments/daybook' },
    ],
  },
  { label: 'Reports', path: '/reports', icon: BarChart3 },

  // -------------------------------------------------------------- periodic
  // Opened deliberately, a few times a week, usually by a manager who then
  // stays a while. A click to get in costs nothing here.
  {
    // Pricing. What we sell and for how much.
    //
    // These lived with the distribution screens under one "Rates &
    // Distribution" heading, on the reasoning that somebody sets a rate and
    // then pushes it somewhere. True, but it put "Channel Partners" and
    // "Sales Channels" side by side -- two names that read as the same thing
    // and are not -- under a group whose own name repeated a child's
    // ("Rates & Distribution" containing "Rates & Inventory").
    label: 'Rates',
    path: '/rates',
    icon: Tag,
    children: [
      { label: 'Rate Plans', path: '/rates/plans' },
      { label: 'Rates & Inventory', path: '/rates/calendar' },
      { label: 'Packages & Promotions', path: '/rates/packages' },
      { label: 'Rate Rules', path: '/rates/rules' },
    ],
  },
  {
    // Where the selling happens: who sells for us, and whether our own
    // booking page is live. A different question from what a room costs.
    label: 'Distribution',
    path: '/channels',
    icon: Globe,
    // Badged on the parent, because the work is time-limited and a collapsed
    // section would hide the only thing about it that matters. A no-show
    // reported late costs the same as one never reported at all.
    badge: 'ota',
    children: [
      { label: 'Channel Partners', path: '/channels' },
      { label: 'Sales Channels', path: '/distribution/sales-channels' },
      { label: 'OTA Actions', path: '/distribution/ota-actions' },
    ],
  },
  {
    label: 'Finance',
    path: '/finance',
    icon: Wallet,
    children: [
      { label: 'Invoices', path: '/finance/invoices' },
      // Was in no menu at all, reachable only from inside Invoices.
      { label: 'Invoice Settings', path: '/finance/invoice-settings' },
      { label: 'Expense Vouchers', path: '/finance/expenses' },
      { label: 'Unit Owners', path: '/finance/owners' },
      // Filed here rather than under Guests, on what the screen actually
      // holds: credit limits, credit days, and an outstanding balance summed
      // from the ledger. That is accounts receivable, not a guest record —
      // and Guests must stay a single click for the desk.
      { label: 'Company Accounts', path: '/guests/companies' },
      { label: 'Taxes & Charges', path: '/admin/taxes' },
      // The tenant's bank account by proxy. Still guarded by
      // payments.configure, which is an administrator's permission — the
      // menu it sits in does not change who may open it.
      { label: 'Payment Gateway', path: '/admin/payment-gateway' },
      // Next to the money it closes. A nightly act, done once, by somebody
      // who is already in here looking at the day's takings.
      { label: 'Night Audit', path: '/night-audit' },
    ],
  },

  // ------------------------------------------------------------------ setup
  // Touched when a property is set up and rarely after. Two groups, and the
  // boundary is sayable in one line: this one is the building, the next one
  // is the people.
  {
    label: 'Property Setup',
    path: '/property',
    icon: Building2,
    children: [
      { label: 'Property Settings', path: '/settings' },
      { label: 'Room Types', path: '/property/room-types' },
      { label: 'Room Inventory', path: '/rooms' },
      { label: 'Buildings & Floors', path: '/property/buildings' },
      { label: 'Amenities', path: '/property/amenities' },
      { label: 'Room Blocks', path: '/rooms/blocks' },
    ],
  },
  {
    label: 'Administration',
    path: '/admin',
    icon: ShieldCheck,
    children: [
      { label: 'Users', path: '/staff' },
      { label: 'Roles & Permissions', path: '/admin/roles' },
      { label: 'Approvals', path: '/admin/approvals' },
      { label: 'Audit Log', path: '/admin/audit' },
      // The tenant's own software subscription. Deliberately under
      // Administration and not Payments: Payments is guest money, this is
      // what the property pays us, and confusing the two is expensive.
      { label: 'Subscription', path: '/admin/billing' },
    ],
  },
]
