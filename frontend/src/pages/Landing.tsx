import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  BarChart3, BedDouble, BookOpenCheck, Building2, CalendarDays,
  Camera, Check, ChevronDown, ConciergeBell, CreditCard, FileText, Globe,
  Handshake, History, KeyRound, Landmark, Layers, LayoutGrid, Lock, MapPin,
  Loader2, Menu, Moon, Network, Percent, Plane, Receipt, Rocket, ScrollText,
  ShieldCheck, Sparkles, CheckCircle2, Tags, TrendingUp, Upload, UserCheck, Users, Wallet,
  Wrench, X,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { PLATFORM_NAME } from '../lib/brand'
import { useAuth } from '../auth/AuthContext'
import { requestDemo } from '../api'
import { Field } from '../components/FormBits'
import PartnerMark from '../components/PartnerMark'
import { errorText, inputCls } from '../lib/forms'
import { COUNTRIES } from '../lib/options'

/**
 * The public front door: what the product does, for someone who has no
 * account yet.
 *
 * Every feature named here is a screen that exists in this app. No invented
 * customer counts, logos or testimonials. A prospect who signs up and finds
 * a promised screen missing stops trusting the rest of the page.
 *
 * html/body/#root are pinned with overflow hidden (see index.css), so this
 * page scrolls inside its own container, just as the app shell does.
 */

const WORDMARK = PLATFORM_NAME

type Feature = { icon: LucideIcon; title: string; body: string }
type Module = {
  id: string
  label: string
  icon: LucideIcon
  headline: string
  summary: string
  features: Feature[]
}

const MODULES: Module[] = [
  {
    id: 'front-office',
    label: 'Front office',
    icon: ConciergeBell,
    headline: 'The whole stay, from the first enquiry to the last receipt',
    summary:
      'Take an enquiry, turn it into a booking, check the guest in, move them '
      + 'between rooms and check them out, all from one screen.',
    features: [
      { icon: CalendarDays, title: 'Stayview calendar',
        body: 'See every room across the dates in one grid, with gaps, holds and blocks visible at a glance.' },
      { icon: BookOpenCheck, title: 'Reservations & enquiries',
        body: 'Arrivals, departures, in-house and no-shows as tabs. Modify, extend or cancel with the rate recalculated.' },
      { icon: Users, title: 'Group blocks',
        body: 'Tentative holds that tie up no rooms, rooming lists, and a folio for the group as well as each guest.' },
      { icon: UserCheck, title: 'Check-in with ID capture',
        body: 'Scan or photograph the guest ID at the desk. It is stored in object storage, not in the database.' },
      { icon: LayoutGrid, title: 'Room rack & room moves',
        body: 'Live room status, one-click room moves and stay extensions for guests already in-house.' },
      { icon: Sparkles, title: 'Guest profiles & services',
        body: 'Stay history, preferences, VIP and smoking flags, connecting rooms, and orders from a service menu.' },
    ],
  },
  {
    id: 'revenue',
    label: 'Revenue & distribution',
    icon: TrendingUp,
    headline: 'Sell every room on every channel, at the right price',
    summary:
      'Rate plans, packages and rules feed one inventory, and that inventory '
      + 'is published to the OTAs and your own booking engine together.',
    features: [
      { icon: Tags, title: 'Meal plans & packages',
        body: 'Room Only, Breakfast, Half Board, Full Board and All Inclusive out of the box. Bundle them into packages and promotions.' },
      { icon: Percent, title: 'Yield management',
        body: 'Rates move automatically with occupancy, dates, day of week and minimum stay. Simulate a rule before you publish it.' },
      { icon: Network, title: 'Channel manager',
        body: 'Two-way sync of rates and availability with the OTAs. OTA bookings land straight in the calendar.' },
      { icon: Globe, title: 'Direct booking engine',
        body: 'Guests book and pay on your own domain, and a paid hold turns into a confirmed booking automatically.' },
      { icon: Handshake, title: 'Channel partners',
        body: 'Travel agents and corporates with their own contracts, commissions and sales channels.' },
      { icon: Layers, title: 'Bulk rate & inventory updates',
        body: 'Change rates, allotments and stop-sells for many dates and room types in one edit, pushed to every channel.' },
    ],
  },
  {
    id: 'finance',
    label: 'Finance & billing',
    icon: Wallet,
    headline: 'Books that balance every night',
    summary:
      'Folios, payments, invoices and the night audit follow accounting rules. '
      + 'A posted entry is never edited. It is corrected with an entry of its own.',
    features: [
      { icon: Receipt, title: 'Folios & split billing',
        body: 'Several folios per stay, routing between guest and company, discounts and adjustments with the bill attached.' },
      { icon: CreditCard, title: 'Online & desk payments',
        body: 'Card, cash, bank transfer and local methods such as UPI. Online payments run through your own payment gateway account.' },
      { icon: FileText, title: 'Tax invoices & credit notes',
        body: 'Invoices with the full tax breakdown and your own numbering series, including GST with tax-ID checks for India. Corrections go out as credit notes.' },
      { icon: Moon, title: 'Night audit',
        body: 'Close the business day, post room charges and no-shows, and keep a full history of every audit.' },
      { icon: Landmark, title: 'All-in-one cashiering',
        body: 'One cashier centre for guests, companies and travel agents: cash drawer handovers, deposit schedules, refunds and a daily book.' },
      { icon: Building2, title: 'Company & travel agent accounts',
        body: 'Credit accounts with commission rates and a commission report, plus expense vouchers and statements for unit owners.' },
    ],
  },
  {
    id: 'operations',
    label: 'Operations',
    icon: Wrench,
    headline: 'Rooms ready before the guest reaches the desk',
    summary:
      'Housekeeping and maintenance work from the same room status that the '
      + 'front desk sees, so a room is never sold dirty or broken.',
    features: [
      { icon: BedDouble, title: 'Housekeeping board',
        body: 'Clean, dirty, inspected and out of order, updated live and assigned to attendants.' },
      { icon: Wrench, title: 'Work orders',
        body: 'Log a fault, block the room, track the repair and put the room back on sale when it is fixed.' },
      { icon: History, title: 'Room status history',
        body: 'Who changed which room, when, and why, kept for every room in the property.' },
      { icon: BarChart3, title: 'Reports',
        body: 'Occupancy, revenue, ledger and back-office reports, with CSV export.' },
    ],
  },
  {
    id: 'control',
    label: 'Security & control',
    icon: ShieldCheck,
    headline: 'Every payment and every change, accounted for',
    summary:
      'Roles decide who can do what, sensitive actions wait for an approver, '
      + 'and one property can never see another property\'s data.',
    features: [
      { icon: KeyRound, title: 'Roles & permissions',
        body: 'A permission matrix per role. Invite staff by email and they set their own password.' },
      { icon: Check, title: 'Maker-checker approvals',
        body: 'Discounts, reversals and other sensitive actions can wait for a second person to approve them.' },
      { icon: ScrollText, title: 'Audit log',
        body: 'A permanent record of who did what and when, across every screen.' },
      { icon: Building2, title: 'Multiple properties, one login',
        body: 'Run several hotels from one account. Switch property from the top bar, and give each user access only where they work.' },
      { icon: Lock, title: 'Data isolated per property',
        body: 'Row-level security in the database keeps each property\'s data separate, whatever the application does.' },
    ],
  },
]

/** The header menu. Each entry scrolls to a section of this page. */
const MENU = [
  { id: 'products', label: 'Products' },
  { id: 'features', label: 'Features' },
  { id: 'channels', label: 'Channel manager' },
  { id: 'compliance', label: 'Compliance' },
  { id: 'onboarding', label: 'Getting started' },
  { id: 'faq', label: 'FAQ' },
]

const PRODUCTS = [
  { icon: ConciergeBell, title: 'Hotel PMS',
    body: 'Front desk, reservations, housekeeping and night audit.' },
  { icon: Network, title: 'Channel manager',
    body: 'Booking.com, Airbnb, Expedia, Agoda and more, synced both ways.' },
  { icon: Globe, title: 'Booking engine',
    body: 'Commission-free direct bookings on your own domain.' },
  { icon: Receipt, title: 'Billing & tax',
    body: 'Folios, payments, tax invoices and cashiering.' },
]

/** The channels MyGuest provisions through its channel manager. Kept in step
 *  with CHANNEL_CODES in booking-core's channel_provision.py: a name here that
 *  is not there is a promise the demo cannot keep. Each is drawn by
 *  PartnerMark, so the page shows the same mark the app does: the brand's own
 *  logo from public/ota-logos where there is one, its brand-colour tile where
 *  there is not (see the README in that folder for where logos come from). */
const OTAS = [
  'Booking.com', 'Airbnb', 'Expedia', 'Agoda', 'MakeMyTrip', 'Goibibo',
  'Trip.com', 'Cleartrip', 'Yatra', 'Google Hotel Ads',
]

const CHANNEL_FLOW = [
  { icon: TrendingUp, title: 'Rates, availability and restrictions out',
    body: 'Prices, rooms left, minimum stay, closed to arrival or departure and stop-sell, published a full year ahead to every connected channel.' },
  { icon: CalendarDays, title: 'Bookings in, on their own',
    body: 'An OTA booking lands in your calendar with the guest\'s details and the rate they paid. A repeat of the same booking is recognised, never doubled.' },
  { icon: Layers, title: 'Map rooms once',
    body: 'Link each channel\'s rooms to yours one time. A booking for a room that is not mapped is flagged for the desk, never silently lost.' },
  { icon: History, title: 'Cancellations and no-shows tracked',
    body: 'Cancellations land in a queue for the desk, and each no-show shows the clock on reporting it to the OTA before the window closes.' },
  { icon: Handshake, title: 'Commission per channel',
    body: 'Each OTA and travel agent carries its own commission rate, with a commission report to check the statement against.' },
  { icon: Globe, title: 'Your own booking engine beside them',
    body: 'Direct bookings on your website sell from the same inventory as the OTAs, with no commission to anyone.' },
]

const COMPLIANCE = [
  { icon: FileText, title: 'Taxes set per property',
    body: 'Tax rates and groups configured for each property, printed in full on every invoice, with your own numbering series.' },
  { icon: Plane, title: 'Foreign guest registration',
    body: 'Passport and visa details captured at check-in and kept as a register, ready for the authorities. India\'s Form C is built in.' },
  { icon: Globe, title: 'Any currency, any time zone',
    body: 'Each property keeps its own currency and time zone, so a group can run hotels in more than one country from one account.' },
  { icon: MapPin, title: 'Local where it matters',
    body: 'Ready for India today: GST with tax-ID checks, every state and union territory, and UPI through your own gateway.' },
]

const JOURNEY = [
  { step: 'Enquiry', body: 'Capture the lead' },
  { step: 'Reservation', body: 'Rate, room, deposit' },
  { step: 'Check-in', body: 'ID, registration, keys' },
  { step: 'Stay', body: 'Charges, services' },
  { step: 'Check-out', body: 'Folio, tax invoice' },
  { step: 'Night audit', body: 'Close the day' },
]

const ONBOARDING = [
  { icon: Building2, title: 'Property & structure',
    body: 'Your details, buildings, floors, room types and rooms.' },
  { icon: Tags, title: 'Rates & billing',
    body: 'Rate plans, taxes, invoice series and payment gateway.' },
  { icon: Users, title: 'Team',
    body: 'Invite staff and give each one a role.' },
  { icon: Upload, title: 'Import',
    body: 'Bring in reservations, in-house guests and opening balances from CSV.' },
  { icon: Network, title: 'Connections',
    body: 'Connect your OTAs and your booking engine domain.' },
  { icon: Rocket, title: 'Go live',
    body: 'A final checklist, then open for business.' },
]

const FAQ = [
  { q: 'Does it work for a single small property?',
    a: 'Yes. A 10-room homestay and a multi-building resort use the same screens. The wizard only asks about the parts you actually have.' },
  { q: 'Can I bring my existing bookings across?',
    a: 'Yes. The import step takes your reservations, in-house guests and opening balances from a CSV file, so the calendar is full on day one.' },
  { q: 'Which OTAs are supported?',
    a: 'Booking.com, Airbnb, Expedia, Agoda, MakeMyTrip, Goibibo, Trip.com, Cleartrip, Yatra and Google Hotel Ads, through the integrated channel manager. Rates, availability and restrictions go out to them, and their bookings come into your calendar automatically.' },
  { q: 'Whose payment gateway is used?',
    a: 'Yours. You connect your own gateway account, so guest payments go straight to your bank and never pass through ours.' },
  { q: 'Is my data safe from other properties on the platform?',
    a: 'Each property\'s data is isolated in the database itself with row-level security, not only in the application code, and every action is written to an audit log.' },
  { q: 'Do I need to install anything?',
    a: 'No. It runs in the browser on any laptop or tablet at the front desk, the back office or at home.' },
]

export default function Landing() {
  const [active, setActive] = useState(MODULES[0].id)
  const [openFaq, setOpenFaq] = useState<number | null>(0)
  const mod = MODULES.find((m) => m.id === active) ?? MODULES[0]
  // This page sits at / for everyone, so somebody already signed in needs a
  // way back into the app rather than being offered a sign-in form. A
  // platform operator's way in is the console.
  const { session } = useAuth()
  const appHome = session?.is_platform ? '/platform' : '/dashboard'
  const signIn = session
    ? { to: appHome, label: 'Go to dashboard' }
    : { to: '/login', label: 'Sign in' }

  const [demoOpen, setDemoOpen] = useState(false)
  const openDemo = () => setDemoOpen(true)
  const [menuOpen, setMenuOpen] = useState(false)
  // Scrolled to, not linked: an #anchor would put a fragment in the address
  // bar, and this page keeps its address as the bare site.
  const go = (id: string) => {
    setMenuOpen(false)
    document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  return (
    <div className="h-full overflow-y-auto scroll-smooth bg-white text-slate-700">
      {/* ── Header ─────────────────────────────────────────────── */}
      {/* The menu scrolls within this page and changes no address, so the
          only places anyone can leave for are the two actions: sign in, or
          book a demo. A hotel becomes a customer through a conversation with
          the team, so the self-service sign-up is not linked from here. */}
      <header className="sticky top-0 z-30 border-b border-slate-100 bg-white/90 backdrop-blur">
        <div className="mx-auto flex h-16 max-w-7xl items-center justify-between gap-3 px-4 sm:px-6">
          <button type="button" onClick={() => go('top')} className="flex items-center gap-2.5">
            <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-brand text-white">
              <BedDouble size={20} />
            </span>
            <span className="text-lg font-bold tracking-tight text-ink">{WORDMARK}</span>
          </button>
          <nav className="hidden items-center gap-7 lg:flex">
            {MENU.map((m) => (
              <button key={m.id} type="button" onClick={() => go(m.id)}
                className="text-sm font-medium text-slate-500 hover:text-brand">
                {m.label}
              </button>
            ))}
          </nav>
          <div className="flex items-center gap-2 sm:gap-3">
            <Link to={signIn.to}
              className="hidden rounded-lg px-4 py-2 text-sm font-semibold text-ink hover:bg-slate-75 sm:block">
              {signIn.label}
            </Link>
            <button type="button" onClick={openDemo}
              className="rounded-lg bg-brand px-3 py-2 text-sm font-semibold text-white shadow-sm hover:bg-brand-dark sm:px-4">
              Book a demo
            </button>
            <button type="button" aria-label="Menu" aria-expanded={menuOpen}
              onClick={() => setMenuOpen((o) => !o)}
              className="rounded-lg p-2 text-ink lg:hidden">
              {menuOpen ? <X size={22} /> : <Menu size={22} />}
            </button>
          </div>
        </div>
        {menuOpen && (
          <div className="border-t border-slate-100 bg-white px-4 pb-4 lg:hidden">
            {MENU.map((m) => (
              <button key={m.id} type="button" onClick={() => go(m.id)}
                className="block w-full py-3 text-left text-sm font-medium text-slate-600">
                {m.label}
              </button>
            ))}
            <Link to={signIn.to}
              className="mt-2 block rounded-lg border border-slate-200 py-2.5 text-center text-sm font-semibold text-ink sm:hidden">
              {signIn.label}
            </Link>
          </div>
        )}
      </header>

      <main>
      {/* ── Hero ───────────────────────────────────────────────── */}
      <section id="top" className="relative overflow-hidden bg-gradient-to-b from-[#f0fafa] via-white to-white">
        <div className="pointer-events-none absolute -right-40 -top-40 h-[520px] w-[520px] rounded-full bg-brand-accent/10 blur-3xl" />
        <div className="mx-auto grid max-w-7xl items-center gap-14 px-4 pb-20 pt-16 sm:px-6 lg:grid-cols-[1.05fr_1fr] lg:pt-24">
          <div>
            <h1 className="text-4xl font-bold leading-[1.1] tracking-tight text-ink sm:text-5xl lg:text-[3.4rem]">
              Run your whole property<br className="hidden sm:block" />{' '}
              <span className="text-brand">from one screen.</span>
            </h1>
            <p className="mt-6 max-w-xl text-lg leading-relaxed text-slate-500">
              Reservations, front desk, housekeeping, channel manager, tax invoicing,
              guest registration and the night audit in one system, with every change written
              to an audit trail.
            </p>
            <ul className="mt-9 grid gap-2.5 text-sm text-slate-500 sm:grid-cols-2">
              {['Guided setup after your demo', 'Import your existing bookings',
                'Your own payment gateway', 'Works in any browser'].map((t) => (
                <li key={t} className="flex items-center gap-2">
                  <Check size={16} className="shrink-0 text-positive" /> {t}
                </li>
              ))}
            </ul>
          </div>
          <HeroMock />
        </div>
      </section>

      {/* ── Four products, one system ──────────────────────────── */}
      <section id="products" aria-labelledby="products-title"
        className="mx-auto max-w-7xl scroll-mt-24 px-4 pb-16 sm:px-6">
        <h2 id="products-title" className="sr-only">Products</h2>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {PRODUCTS.map((p) => {
            const Icon = p.icon
            return (
              <div key={p.title}
                className="flex items-start gap-4 rounded-2xl border border-slate-100 bg-white p-5 shadow-pf-card">
                <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-brand text-white">
                  <Icon size={22} />
                </span>
                <div>
                  <h3 className="font-semibold text-ink">{p.title}</h3>
                  <p className="mt-1 text-sm text-slate-500">{p.body}</p>
                </div>
              </div>
            )
          })}
        </div>
        <p className="mt-5 text-center text-sm text-slate-400">
          Four products in one system, with one login.
        </p>
      </section>

      {/* ── Journey strip ──────────────────────────────────────── */}
      <section className="border-y border-slate-100 bg-slate-50">
        <div className="mx-auto max-w-7xl px-4 py-12 sm:px-6">
          <p className="text-center text-sm font-semibold uppercase tracking-wider text-brand-deep">
            One guest, one record, start to finish
          </p>
          <ol className="mt-8 grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
            {JOURNEY.map((j, i) => (
              <li key={j.step} className="relative rounded-xl border border-slate-100 bg-white p-4 text-center shadow-pf-card">
                <span className="mx-auto flex h-8 w-8 items-center justify-center rounded-full bg-brand text-sm font-bold text-white">
                  {i + 1}
                </span>
                <p className="mt-3 font-semibold text-ink">{j.step}</p>
                <p className="mt-1 text-xs text-slate-400">{j.body}</p>
              </li>
            ))}
          </ol>
        </div>
      </section>

      {/* ── Features, by module ────────────────────────────────── */}
      <section id="features" className="scroll-mt-16">
        <div className="mx-auto max-w-7xl px-4 py-24 sm:px-6">
          <SectionHead eyebrow="Everything in one place"
            title="Everything a hotel runs on"
            body="Five modules share one database, so a room sold on an OTA, cleaned by housekeeping and billed at checkout is the same room everywhere." />

          <div className="mt-12 flex gap-2 overflow-x-auto pb-2 lg:justify-center">
            {MODULES.map((m) => {
              const on = m.id === active
              const Icon = m.icon
              return (
                <button key={m.id} type="button" onClick={() => setActive(m.id)}
                  className={`flex shrink-0 items-center gap-2 rounded-full px-5 py-2.5 text-sm font-semibold transition ${
                    on ? 'bg-ink text-white shadow-md'
                      : 'border border-slate-200 bg-white text-slate-500 hover:border-brand hover:text-brand'}`}>
                  <Icon size={16} /> {m.label}
                </button>
              )
            })}
          </div>

          <div className="mt-10 rounded-3xl border border-slate-100 bg-gradient-to-br from-slate-50 to-white p-6 sm:p-10">
            <div className="max-w-2xl">
              <h3 className="text-2xl font-bold tracking-tight text-ink sm:text-3xl">{mod.headline}</h3>
              <p className="mt-3 text-slate-500">{mod.summary}</p>
            </div>
            <div className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {mod.features.map((f) => <FeatureCard key={f.title} {...f} />)}
            </div>
          </div>
        </div>
      </section>

      {/* ── Channel manager & OTAs ──────────────────────────────── */}
      <section id="channels" className="scroll-mt-16 bg-slate-50">
        <div className="mx-auto max-w-7xl px-4 py-24 sm:px-6">
          <SectionHead eyebrow="Channel manager"
            title="Sell on every major OTA from one calendar"
            body="A built-in channel manager keeps your rooms, rates and restrictions in step across the online travel agencies, and brings their bookings straight into MyGuest." />
          <ul className="mx-auto mt-10 grid max-w-5xl grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
            {OTAS.map((o) => (
              <li key={o}
                className="flex items-center gap-3 rounded-2xl border border-slate-100 bg-white px-4 py-3.5 shadow-pf-card">
                <PartnerMark name={o} size={44} />
                <span className="text-sm font-semibold leading-tight text-ink">{o}</span>
              </li>
            ))}
          </ul>
          <div className="mt-12 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {CHANNEL_FLOW.map((f) => <FeatureCard key={f.title} {...f} level="h3" />)}
          </div>
        </div>
      </section>

      {/* ── Compliance ─────────────────────────────────────────── */}
      <section id="compliance" className="scroll-mt-16 bg-ink text-white">
        <div className="mx-auto grid max-w-7xl gap-12 px-4 py-24 sm:px-6 lg:grid-cols-[1fr_1.4fr]">
          <div>
            <p className="text-sm font-semibold uppercase tracking-wider text-brand-accent">Compliance</p>
            <h2 className="mt-3 text-3xl font-bold tracking-tight sm:text-4xl">
              The paperwork, handled while you work
            </h2>
            <p className="mt-5 text-lg leading-relaxed text-[#b9c6da]">
              Tax invoices and guest registration are part of check-out and
              check-in, not a separate chore at the end of the month. The details
              are collected as you go, and the records are ready when someone
              asks for them.
            </p>
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            {COMPLIANCE.map((c) => {
              const Icon = c.icon
              return (
                <div key={c.title} className="rounded-2xl border border-white/10 bg-white/5 p-6">
                  <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-brand-accent/15 text-brand-accent">
                    <Icon size={22} />
                  </span>
                  <h3 className="mt-4 text-lg font-semibold">{c.title}</h3>
                  <p className="mt-2 text-sm leading-relaxed text-[#b9c6da]">{c.body}</p>
                </div>
              )
            })}
          </div>
        </div>
      </section>

      {/* ── Onboarding ─────────────────────────────────────────── */}
      <section id="onboarding" className="scroll-mt-16">
        <div className="mx-auto max-w-7xl px-4 py-24 sm:px-6">
          <SectionHead eyebrow="Getting started" title="From demo to first check-in in six steps"
            body="Once you have seen MyGuest, setup is six guided steps. It asks only what it needs, and you can stop at any step and pick up where you left off." />
          <div className="mt-14 grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
            {ONBOARDING.map((o, i) => {
              const Icon = o.icon
              return (
                <div key={o.title} className="group relative rounded-2xl border border-slate-100 bg-white p-6 shadow-pf-card transition hover:-translate-y-0.5 hover:border-brand/40">
                  <span className="absolute right-6 top-6 text-4xl font-bold text-slate-75 group-hover:text-brand-light">
                    {String(i + 1).padStart(2, '0')}
                  </span>
                  <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-brand-light text-brand-deep">
                    <Icon size={22} />
                  </span>
                  <h3 className="mt-4 text-lg font-semibold text-ink">{o.title}</h3>
                  <p className="mt-1.5 text-sm text-slate-500">{o.body}</p>
                </div>
              )
            })}
          </div>
        </div>
      </section>

      {/* ── FAQ ────────────────────────────────────────────────── */}
      <section id="faq" className="scroll-mt-16 bg-slate-50">
        <div className="mx-auto max-w-3xl px-4 py-24 sm:px-6">
          <SectionHead eyebrow="FAQ" title="Questions hoteliers ask" />
          <div className="mt-10 divide-y divide-slate-100 rounded-2xl border border-slate-100 bg-white">
            {FAQ.map((f, i) => {
              const open = openFaq === i
              return (
                <div key={f.q}>
                  <button type="button" onClick={() => setOpenFaq(open ? null : i)}
                    aria-expanded={open}
                    className="flex w-full items-center justify-between gap-4 px-6 py-5 text-left font-semibold text-ink">
                    {f.q}
                    <ChevronDown size={18}
                      className={`shrink-0 text-slate-400 transition-transform ${open ? 'rotate-180' : ''}`} />
                  </button>
                  {open && <p className="-mt-1 px-6 pb-5 text-sm leading-relaxed text-slate-500">{f.a}</p>}
                </div>
              )
            })}
          </div>
        </div>
      </section>

      </main>

      {/* ── Footer ─────────────────────────────────────────────── */}
      {/* Dark, so the page visibly ends. Every link scrolls within this page,
          like the header menu. Sign in and Book a demo stay in the sticky
          header only, so they are never on screen twice. No contact details, social links or legal pages yet:
          those are the business's to supply, and a link to a page that does
          not exist is worse than no link. */}
      <footer className="bg-ink text-[#b9c6da]">
        <div className="mx-auto grid max-w-7xl gap-10 px-4 py-14 sm:grid-cols-2 sm:px-6 lg:grid-cols-[2fr_1fr_1fr]">
          <div>
            <button type="button" onClick={() => go('top')} className="flex items-center gap-2.5">
              <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-brand text-white">
                <BedDouble size={20} />
              </span>
              <span className="text-lg font-bold tracking-tight text-white">{WORDMARK}</span>
            </button>
            <p className="mt-4 max-w-xs text-sm leading-relaxed">
              Hotel management software for hotels and resorts: front desk,
              channel manager, booking engine and billing in one system.
            </p>
          </div>

          <FooterColumn title="Product" items={[
            { label: 'Hotel PMS', to: 'products' },
            { label: 'Channel manager', to: 'channels' },
            { label: 'Booking engine', to: 'products' },
            { label: 'Billing & tax', to: 'features' },
          ]} go={go} />

          <FooterColumn title="Explore" items={[
            { label: 'Features', to: 'features' },
            { label: 'Compliance', to: 'compliance' },
            { label: 'Getting started', to: 'onboarding' },
            { label: 'FAQ', to: 'faq' },
          ]} go={go} />

        </div>
        <div className="border-t border-white/10">
          <div className="mx-auto flex max-w-7xl flex-col gap-2 px-4 py-6 text-xs sm:flex-row sm:items-center sm:justify-between sm:px-6">
            <span>© {new Date().getFullYear()} {WORDMARK}. All rights reserved.</span>
            <span>Made for hotels, resorts and homestays.</span>
          </div>
        </div>
      </footer>
      {demoOpen && <DemoDialog onClose={() => setDemoOpen(false)} />}
    </div>
  )
}


function FooterColumn({ title, items, go }: {
  title: string
  items: { label: string; to: string }[]
  go: (id: string) => void
}) {
  return (
    <div>
      <p className="text-sm font-semibold text-white">{title}</p>
      <ul className="mt-3 space-y-1">
        {items.map((i) => (
          <li key={i.label}>
            <button type="button" onClick={() => go(i.to)}
              className="-my-1 py-2 text-sm hover:text-white">
              {i.label}
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}

function SectionHead({ eyebrow, title, body }: { eyebrow: string; title: string; body?: string }) {
  return (
    <div className="mx-auto max-w-2xl text-center">
      <p className="text-sm font-semibold uppercase tracking-wider text-brand-deep">{eyebrow}</p>
      <h2 className="mt-3 text-3xl font-bold tracking-tight text-ink sm:text-4xl">{title}</h2>
      {body && <p className="mt-4 text-lg text-slate-500">{body}</p>}
    </div>
  )
}

function FeatureCard({ icon: Icon, title, body, level = 'h4' }: Feature & {
  /** h4 under a module headline (h3); h3 directly under a section's h2. */
  level?: 'h3' | 'h4'
}) {
  const Heading = level
  return (
    <div className="rounded-2xl border border-slate-100 bg-white p-6 shadow-pf-card transition hover:-translate-y-0.5 hover:border-brand/40">
      <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-brand-light text-brand-deep">
        <Icon size={22} />
      </span>
      <Heading className="mt-4 font-semibold text-ink">{title}</Heading>
      <p className="mt-1.5 text-sm leading-relaxed text-slate-500">{body}</p>
    </div>
  )
}

/** An illustrative Stayview: not live data, just a picture of the screen.
 *  The guest names are obviously placeholder, so nobody takes it for a real
 *  booking. */
function HeroMock() {
  const days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
  const rows: { room: string; bars: { from: number; span: number; tone: string; label: string }[] }[] = [
    { room: '101', bars: [{ from: 0, span: 3, tone: 'bg-brand', label: 'Guest A' }] },
    { room: '102', bars: [{ from: 1, span: 4, tone: 'bg-[#375AAC]', label: 'Group B' }] },
    { room: '103', bars: [{ from: 0, span: 2, tone: 'bg-brand-accent', label: 'OTA' }, { from: 4, span: 3, tone: 'bg-brand', label: 'Guest C' }] },
    { room: '201', bars: [{ from: 2, span: 3, tone: 'bg-[#9C6017]', label: 'Hold' }] },
    { room: '202', bars: [{ from: 3, span: 4, tone: 'bg-brand-accent', label: 'Direct' }] },
  ]
  const kpis = [
    { label: 'Occupancy', value: '84%' },
    { label: 'Arrivals', value: '12' },
    { label: 'Departures', value: '9' },
  ]
  return (
    <div className="relative" aria-hidden="true">
      <div className="rounded-2xl border border-slate-100 bg-white p-4 shadow-2xl shadow-ink/10 sm:p-5">
        <div className="flex items-center gap-1.5 pb-4">
          <span className="h-2.5 w-2.5 rounded-full bg-[#f87171]" />
          <span className="h-2.5 w-2.5 rounded-full bg-[#fbbf24]" />
          <span className="h-2.5 w-2.5 rounded-full bg-[#34d399]" />
          <span className="ml-3 text-xs font-semibold text-slate-400">Stayview</span>
        </div>
        <div className="grid grid-cols-3 gap-3">
          {kpis.map((k) => (
            <div key={k.label} className="rounded-xl bg-slate-50 p-3">
              <p className="text-[11px] font-medium text-slate-400">{k.label}</p>
              <p className="text-xl font-bold text-ink">{k.value}</p>
            </div>
          ))}
        </div>
        <div className="mt-4 overflow-hidden rounded-xl border border-slate-100">
          <div className="grid grid-cols-[44px_repeat(7,1fr)] bg-slate-50 text-[11px] font-semibold text-slate-400">
            <span className="px-2 py-2">Room</span>
            {days.map((d) => <span key={d} className="py-2 text-center">{d}</span>)}
          </div>
          {rows.map((r) => (
            <div key={r.room} className="relative grid h-10 grid-cols-[44px_repeat(7,1fr)] border-t border-slate-100">
              <span className="flex items-center px-2 text-xs font-semibold text-ink">{r.room}</span>
              {days.map((d) => <span key={d} className="border-l border-slate-75" />)}
              {r.bars.map((b) => (
                <span key={b.label}
                  className={`absolute top-1.5 flex h-7 items-center rounded-md px-2 text-[11px] font-semibold text-white ${b.tone}`}
                  style={{
                    left: `calc(44px + (100% - 44px) * ${b.from / 7} + 2px)`,
                    width: `calc((100% - 44px) * ${b.span / 7} - 4px)`,
                  }}>
                  <span className="truncate">{b.label}</span>
                </span>
              ))}
            </div>
          ))}
        </div>
      </div>
      <div className="absolute -bottom-6 -left-4 hidden items-center gap-3 rounded-xl border border-slate-100 bg-white px-4 py-3 shadow-xl sm:flex">
        <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-pf-ok-bg text-pf-ok-text">
          <Camera size={18} />
        </span>
        <div>
          <p className="text-xs font-semibold text-ink">Guest checked in</p>
          <p className="text-[11px] text-slate-400">ID captured · guest registered</p>
        </div>
      </div>
      <div className="absolute -right-3 -top-5 hidden items-center gap-3 rounded-xl border border-slate-100 bg-white px-4 py-3 shadow-xl sm:flex">
        <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-brand-light text-brand-deep">
          <Globe size={18} />
        </span>
        <div>
          <p className="text-xs font-semibold text-ink">New OTA booking</p>
          <p className="text-[11px] text-slate-400">Synced to calendar</p>
        </div>
      </div>
    </div>
  )
}

/** "Book a demo": a short form, stored as a lead for the platform team.
 *
 *  Name, property, phone and email are required. Without any one of them the
 *  team cannot call back or knows nothing about the hotel. The rest is
 *  optional, so asking for it never costs a lead. */
function DemoDialog({ onClose }: { onClose: () => void }) {
  const [f, setF] = useState({
    full_name: '', property_name: '', phone: '', email: '',
    city: '', country: '', rooms: '', message: '', website: '',
  })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [done, setDone] = useState('')
  const set = (k: keyof typeof f) =>
    (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) =>
      setF((v) => ({ ...v, [k]: e.target.value }))

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setError(''); setBusy(true)
    try {
      const res = await requestDemo({
        full_name: f.full_name.trim(),
        property_name: f.property_name.trim(),
        phone: f.phone.trim(),
        email: f.email.trim(),
        city: f.city.trim() || null,
        country: f.country || null,
        rooms: f.rooms ? Number(f.rooms) : null,
        message: f.message.trim() || null,
        website: f.website,
      })
      setDone(res.detail)
    } catch (err) {
      setError(errorText(err, 'Could not send your request. Please try again.'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 p-4"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div role="dialog" aria-modal="true" aria-labelledby="demo-title"
        className="max-h-full w-full max-w-xl overflow-y-auto rounded-2xl bg-white shadow-2xl">
        <div className="flex items-start justify-between gap-4 border-b border-slate-100 px-6 py-5">
          <div>
            <h2 id="demo-title" className="text-xl font-bold tracking-tight text-ink">Book a demo</h2>
            <p className="mt-1 text-sm text-slate-500">
              Tell us about your property and we will set up a walkthrough.
            </p>
          </div>
          <button type="button" aria-label="Close" onClick={onClose}
            className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-75 hover:text-ink">
            <X size={20} />
          </button>
        </div>

        {done ? (
          <div className="px-6 py-12 text-center">
            <CheckCircle2 size={44} className="mx-auto text-positive" />
            <p className="mt-4 text-lg font-semibold text-ink">Request received</p>
            <p className="mx-auto mt-2 max-w-sm text-sm text-slate-500">{done}</p>
            <button type="button" onClick={onClose}
              className="mt-8 rounded-xl bg-brand px-6 py-3 font-semibold text-white hover:bg-brand-dark">
              Done
            </button>
          </div>
        ) : (
          <form onSubmit={submit} className="relative px-6 py-5">
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Your name" required>
                <input className={inputCls} value={f.full_name} onChange={set('full_name')}
                  required minLength={2} maxLength={120} autoComplete="name" autoFocus />
              </Field>
              <Field label="Property name" required>
                <input className={inputCls} value={f.property_name} onChange={set('property_name')}
                  required minLength={2} maxLength={160} autoComplete="organization" />
              </Field>
              <Field label="Mobile number" required>
                <input className={inputCls} value={f.phone} onChange={set('phone')}
                  required type="tel" inputMode="tel" minLength={7} maxLength={20}
                  placeholder="With country code, e.g. +44 20 7946 0000" autoComplete="tel" />
              </Field>
              <Field label="Email" required>
                <input className={inputCls} value={f.email} onChange={set('email')}
                  required type="email" maxLength={254} autoComplete="email" />
              </Field>
              <Field label="City">
                <input className={inputCls} value={f.city} onChange={set('city')}
                  maxLength={80} autoComplete="address-level2" />
              </Field>
              <Field label="Country">
                <select className={inputCls} value={f.country} onChange={set('country')}>
                  <option value="">Select country</option>
                  {COUNTRIES.map((c) => <option key={c} value={c}>{c}</option>)}
                </select>
              </Field>
              <Field label="Number of rooms">
                <input className={inputCls} value={f.rooms} onChange={set('rooms')}
                  type="number" min={1} max={5000} inputMode="numeric" />
              </Field>
              <Field label="Anything we should know?" wide>
                <textarea className={`${inputCls} min-h-[84px] resize-y`} value={f.message}
                  onChange={set('message')} maxLength={1000}
                  placeholder="The system you use today, number of properties, a good time to call" />
              </Field>
            </div>
            {/* Honeypot: hidden from people and screen readers. A bot fills it. */}
            <input type="text" name="website" value={f.website} onChange={set('website')}
              tabIndex={-1} autoComplete="off" aria-hidden="true"
              className="pointer-events-none absolute left-0 top-0 h-0 w-0 opacity-0" />
            {error && (
              <p className="mt-4 rounded-lg bg-pf-err-bg px-3 py-2 text-sm text-pf-err-text">{error}</p>
            )}
            <div className="mt-6 flex flex-col-reverse gap-3 sm:flex-row sm:justify-end">
              <button type="button" onClick={onClose}
                className="rounded-xl border border-slate-200 px-5 py-3 text-sm font-semibold text-slate-600 hover:bg-slate-50">
                Cancel
              </button>
              <button type="submit" disabled={busy}
                className="inline-flex items-center justify-center gap-2 rounded-xl bg-brand px-6 py-3 text-sm font-semibold text-white hover:bg-brand-dark disabled:opacity-60">
                {busy && <Loader2 size={16} className="animate-spin" />}
                Request demo
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  )
}
