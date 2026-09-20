import { useEffect, useState } from 'react'
import { Link, Navigate, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle, ArrowLeft, ArrowRight, BedDouble, Building2, CalendarDays, Check, CheckCircle2, ChevronRight, CreditCard, Eye, FileText, Globe, HelpCircle, Info, LayoutGrid, Loader2, Lock, Mail, MapPin, Play, PlayCircle, Rocket, Save, Share2, Tag, Upload, Users,
} from 'lucide-react'
import { useAuth } from '../auth/AuthContext'
import { fmtDateTime } from '../lib/dates'
import {
  getOnboarding, patchOnboarding, activateProperty, getPropertyLogo, listProperties, runTestBooking, resendWelcomeEmails, type OnboardingState, type TestBookingResult, type OnboardingStep,
} from '../api'

/**
 * Getting a property from nothing to open.
 *
 * The wizard is a guide, not a gate. Every step it covers is also reachable
 * from the ordinary screens, and the checklist is derived from the property's
 * real data rather than from a flag the wizard sets — so setting rooms up the
 * normal way ticks the rooms step, and deleting them all un-ticks it. A wizard
 * that can disagree with the product is worse than no wizard.
 */

/** Where each step sends the operator to do the actual work. */
const STEP_LINK: Record<string, string> = {
  account: '/onboarding/account',
  property: '/onboarding/property',
  structure: '/onboarding/structure',
  rooms: '/onboarding/rooms',
  rates: '/onboarding/rates',
  billing: '/onboarding/billing',
  team: '/onboarding/team',
  import: '/onboarding/import',
  connections: '/onboarding/connections',
  golive: '/onboarding/golive',
}

/** The one-line description under each step, from the mockups. */
/** The rail's own labels, for the pre-signup frame that has no server data. */
const STEP_LABEL: Record<string, string> = {
  account: 'Account', property: 'Property', structure: 'Structure',
  rooms: 'Rooms', rates: 'Rates & Policies', billing: 'Billing',
  team: 'Team', import: 'Import Bookings', connections: 'Connections',
  golive: 'Go Live',
}

const STEP_BLURB: Record<string, string> = {
  account: 'Create your account',
  property: 'Tell us about your property',
  structure: 'Set up your structure',
  rooms: 'Add your rooms',
  rates: 'Configure rates and policies',
  billing: 'Set up billing and subscription',
  team: 'Invite your team members',
  import: 'Bring in your existing bookings',
  connections: 'Connect your favorite tools',
  golive: "You're all set!",
}

/**
 * How the readiness checklist names each step.
 *
 * The rail says "Property" because it is a step in a sequence; the checklist
 * says "Property details" because it is a thing to be checked. Same step, two
 * jobs, and the mockups word them differently for that reason.
 */
const CHECK_LABEL: Record<string, string> = {
  property: 'Property details',
  structure: 'Buildings and floors',
  rooms: 'Rooms and inventory',
  rates: 'Rates and policies',
  billing: 'Billing and payments',
  team: 'Team access',
  import: 'Booking import',
  connections: 'Booking connections',
}

/** What the button on a finished row offers to do. */
const CHECK_ACTION: Record<string, string> = {
  import: 'Review',
  connections: 'Set up',
}

/**
 * The rows the readiness checklist leads with, in order.
 *
 * Six of the nine, because structure and team are steps on the way to these
 * rather than things to check before opening the front desk: a property with
 * rooms necessarily has buildings and floors, and team access can be granted
 * the day after opening.
 *
 * They are only left out while they are *finished*. Anything incomplete is
 * appended below regardless of this list — a checklist that can hide a
 * problem is worse than no checklist, and activation is refused server-side
 * for a step the screen never showed, which would be baffling.
 */
const CHECK_ROWS = ['property', 'rooms', 'rates', 'billing', 'import',
                    'connections']

const STEP_ICON: Record<string, typeof BedDouble> = {
  account: Users, property: FileText, structure: LayoutGrid, rooms: BedDouble,
  rates: Tag, billing: CreditCard, team: Users, import: Upload,
  connections: Share2, golive: CheckCircle2,
}

/** The step rail: dark, numbered, and always showing all ten.
 *
 * Every step stays visible whether or not it is reachable yet, because the
 * first question anyone onboarding asks is "how much of this is left" — and a
 * rail that reveals steps as you go cannot answer it.
 */
/** The step rail.
 *
 * White, numbered, with a line under each step saying what it is for. The
 * mockups disagree on this — one set draws it dark — and white is what two of
 * the three show, including the most recent.
 *
 * All ten stay visible whether or not they are reachable, because the first
 * question anyone onboarding asks is how much is left, and a rail that
 * reveals steps as you go cannot answer it.
 */
function Rail({ state, active }: { state: OnboardingState; active: string }) {
  return (
    <aside className="scroll-slim flex w-full shrink-0 flex-col overflow-y-auto border-r border-slate-100 bg-white px-4 py-6 lg:w-64">
      <div className="mb-7 flex items-start gap-2 px-2">
        <svg viewBox="0 0 24 24" className="mt-0.5 h-6 w-6 shrink-0" aria-hidden="true">
          <path d="M2 9c3-3 6-3 9 0s6 3 9 0" className="fill-none stroke-brand"
            strokeWidth="2.2" strokeLinecap="round" />
          <path d="M2 15c3-3 6-3 9 0s6 3 9 0" className="fill-none stroke-brand/50"
            strokeWidth="2.2" strokeLinecap="round" />
        </svg>
        <span>
        <p className="text-base font-bold leading-tight tracking-tight text-brand-dark">
          CHIRALA BAY <span className="text-brand">PMS</span>
        </p>
        <p className="text-[10px] tracking-[0.16em] text-slate-400">
          HOSPITALITY MADE SIMPLE
        </p>
        </span>
      </div>

      <ol className="space-y-0.5">
        {state.steps.map((s, i) => {
          const on = s.key === active
          // Locked when a required step before it is unfinished.
          const locked = state.steps.slice(0, i)
            .some((p) => !p.optional && !p.complete)
          return (
            <li key={s.key}>
              <Link to={locked ? '#' : (STEP_LINK[s.key] ?? '/onboarding')}
                aria-disabled={locked}
                onClick={(e) => { if (locked) e.preventDefault() }}
                className={`flex items-start gap-3 rounded-xl px-3 py-2.5 ${
                  on ? 'bg-brand-light'
                    : locked ? 'cursor-not-allowed opacity-40'
                      : 'hover:bg-slate-50'}`}>
                <span className={`mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-full text-[11px] font-semibold ${
                  on ? 'bg-brand text-white'
                    : s.complete ? 'bg-brand text-white'
                      : 'bg-slate-75 text-slate-500'}`}>
                  {s.complete && !on ? <Check size={13} /> : i + 1}
                </span>
                <span className="min-w-0">
                  <span className={`block text-sm ${
                    on ? 'font-semibold text-brand-dark' : 'font-medium text-slate-700'}`}>
                    {s.label}
                  </span>
                  <span className="block text-[11px] leading-tight text-slate-400">
                    {STEP_BLURB[s.key]}
                  </span>
                </span>
              </Link>
            </li>
          )
        })}
      </ol>

      <div className="mt-auto border-t border-slate-100 pt-4">
        {/* The mockups sit a small beach mark above the sign-off. Drawn
            rather than shipped as an asset: two paths cost less than a file
            to fetch and keep in step with the palette. */}
        <svg viewBox="0 0 64 40" className="mb-2 h-10 w-16" aria-hidden="true">
          <circle cx="46" cy="14" r="7" className="fill-brand/15" />
          <path d="M6 34c6-10 16-10 22 0" className="fill-none stroke-brand/40"
            strokeWidth="2" strokeLinecap="round" />
          <path d="M17 34V16M17 16c-5-4-11-3-13 1M17 16c5-4 11-3 13 1M17 16c-2-6 1-11 6-12"
            className="fill-none stroke-brand/60" strokeWidth="2" strokeLinecap="round" />
          <path d="M0 37h64" className="stroke-brand/20" strokeWidth="2"
            strokeLinecap="round" />
        </svg>
        <p className="text-xs italic text-slate-400">
          Great stays<br />start here.
        </p>
      </div>
    </aside>
  )
}

/**
 * Whether a step may be opened, and whether it may be left.
 *
 * The sequence is enforced rather than suggested: a step is locked until every
 * required step before it is complete, and Continue stays disabled until the
 * step itself is.
 *
 * Optional steps are the exception in both directions — they never lock what
 * follows and never hold Continue — because two of them cannot be completed at
 * all. Connections has no provider integration behind it, so gating on it would
 * mean no property could ever go live.
 *
 * Completeness is still counted from the property's data, so the gate opens the
 * moment the work is genuinely done, whether it was done here or on the
 * ordinary screens.
 */
export function useStepGate(state: OnboardingState | undefined, step: string) {
  if (!state) {
    return { complete: false, optional: false, locked: false,
             blocker: null as string | null, firstOpen: 'account' }
  }
  const i = state.steps.findIndex((s) => s.key === step)
  const me = state.steps[i]
  const before = state.steps.slice(0, Math.max(i, 0))
  const blocking = before.find((s) => !s.optional && !s.complete)

  // The furthest step that may be opened: the first required one that is not
  // finished, or the end if they all are.
  const firstOpen = state.steps.find((s) => !s.optional && !s.complete)?.key
    ?? state.steps[state.steps.length - 1].key

  return {
    complete: Boolean(me?.complete),
    optional: Boolean(me?.optional),
    locked: Boolean(blocking),
    lockedBy: blocking?.label ?? null,
    lockedWhy: blocking?.blocker ?? null,
    blocker: me?.blocker ?? null,
    firstOpen,
  }
}

/** Continue, gated on the step being finished.
 *
 * Two kinds, and the distinction matters more than it looks. A step whose
 * Continue only *navigates* is disabled until the step is complete. A step
 * whose Continue also *saves* is never disabled — because disabling it
 * deadlocks the operator: the rates step is incomplete precisely because the
 * rates have not been saved, and the only way to save them is this button.
 *
 * So saving always runs. Whether it then moves on is the step's decision,
 * made against what the server says once the save has landed.
 */
export function ContinueButton({ step, onClick, busy, label = 'Continue',
  mode = 'navigate', hint }: {
  step: string
  onClick: () => void
  busy?: boolean
  label?: string
  /** 'save' buttons are never disabled; 'navigate' buttons are. */
  mode?: 'navigate' | 'save'
  /** Shown under the button instead of the step's own blocker. */
  hint?: string | null
}) {
  const propertyId = localStorage.getItem('property_id') ?? ''
  const { data } = useQuery({
    queryKey: ['onboarding', propertyId],
    queryFn: () => getOnboarding(propertyId),
    enabled: propertyId !== '',
  })
  const gate = useStepGate(data, step)
  const unfinished = !gate.optional && !gate.complete
  const blocked = mode === 'navigate' && unfinished

  return (
    <span className="text-right">
      <button onClick={onClick} disabled={blocked || busy}
        className="flex items-center gap-2 rounded-xl bg-brand px-6 py-3 text-sm font-semibold text-white hover:bg-brand-dark disabled:opacity-50">
        {busy && <Loader2 size={15} className="animate-spin" />}
        {mode === 'save' && unfinished ? 'Save' : label} <ArrowRight size={15} />
      </button>
      {(hint || (unfinished && gate.blocker)) && (
        <span className="mt-1 block max-w-xs text-xs text-amber-700">
          {hint ?? gate.blocker}
        </span>
      )}
    </span>
  )
}

export const obInput =
  'w-full rounded-lg border border-slate-200 px-3 py-2.5 text-sm text-slate-700 outline-none focus:border-brand'
export const obLabel = 'text-sm font-semibold text-slate-700'

export function Labelled({ label, required, children }: {
  label: string; required?: boolean; children: React.ReactNode
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-sm font-medium text-slate-600">
        {label}{required && <span className="text-red-500"> *</span>}
      </span>
      {children}
    </label>
  )
}

export function WizardFrame({ step, title, blurb, children, footer, stepNo }: {
  step: string
  /** Kept for the callers that pass it; the frame derives it otherwise. */
  eyebrow?: string
  title: string
  blurb: string
  children: React.ReactNode
  footer?: React.ReactNode
  stepNo?: number
}) {
  const propertyId = localStorage.getItem('property_id') ?? ''
  const qc = useQueryClient()
  const { data } = useQuery({
    queryKey: ['onboarding', propertyId],
    queryFn: () => getOnboarding(propertyId),
    enabled: propertyId !== '',
  })

  const gate = useStepGate(data, step)

  // Record where they are, so closing the laptop mid-way is recoverable.
  useEffect(() => {
    if (!propertyId || !data) return
    if (data.current_step === step) return
    void patchOnboarding(propertyId, { current_step: step, visited: step })
      .then(() => qc.invalidateQueries({ queryKey: ['onboarding', propertyId] }))
  }, [propertyId, data, step, qc])

  // Before sign-up there is no property, so there is no onboarding record to
  // read — and the query is disabled rather than pending. Spinning on that
  // meant the very first screen a new visitor sees never rendered at all.
  const blank: OnboardingState | null = propertyId === '' ? {
    property_id: '', property_name: '', property_city: null,
    currency: 'INR', timezone: 'Asia/Kolkata', current_step: 'account',
    steps: Object.keys(STEP_BLURB).map((key) => ({
      key, label: STEP_LABEL[key] ?? key, complete: false,
      optional: key === 'import' || key === 'connections',
      visited: false, skipped: false, blocker: null,
    })),
    counts: { rooms: 0, room_types: 0, staff_invited: 0, bookings: 0 },
    ready_to_activate: false, activated_at: null,
  } : null

  const view = data ?? blank
  if (!view) {
    return <div className="grid h-full place-items-center">
      <Loader2 className="animate-spin text-slate-400" />
    </div>
  }

  // The app shell sets overflow:hidden on the root and this frame runs
  // outside that shell, so it has to provide its own scroller — otherwise
  // everything below the fold is simply unreachable.
  const done = view.steps.filter((x) => x.complete).length

  return (
    <div className="flex h-full overflow-hidden bg-slate-50">
      <Rail state={view} active={step} />
      <main className="scroll-slim min-w-0 flex-1 overflow-y-auto px-8 py-6">
        <div className="mb-6 flex items-start justify-between gap-4">
          <div>
            {/* "STEP 1 OF 10", as the screen itself says. The "01 — ACCOUNT"
                label in the mockups is the annotation above each frame, not
                part of the product. */}
            <p className="text-xs font-semibold uppercase tracking-wider text-slate-400">
              Step {stepNo ?? view.steps.findIndex((x) => x.key === step) + 1} of{' '}
              {view.steps.length}
            </p>
            <h1 className="mt-1 text-display text-ink">{title}</h1>
            <p className="text-slate-500">{blurb}</p>
          </div>
          <div className="flex shrink-0 items-center gap-4">
            {/* Counted from the property, so it is the number of steps
                genuinely finished rather than the number walked past. */}
            <span className="hidden sm:block">
              <span className="block text-right text-xs text-slate-500">
                Your progress: {done} of {view.steps.length}
              </span>
              <span className="mt-1 block h-1.5 w-40 overflow-hidden rounded-full bg-slate-200">
                <span className="block h-full rounded-full bg-brand"
                  style={{ width: `${(done / view.steps.length) * 100}%` }} />
              </span>
            </span>
            <span className="flex items-center gap-1.5 text-sm text-slate-500">
              <HelpCircle size={15} /> Need help?
            </span>
            <Link to="/"
              className="flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-medium text-slate-600 hover:bg-slate-50">
              <Save size={15} /> Save &amp; exit
            </Link>
          </div>
        </div>
        {gate.locked ? (
          <div className="rounded-2xl border border-slate-100 bg-white p-8 text-center">
            <Lock size={22} className="mx-auto text-slate-300" />
            <p className="mt-3 text-lg font-semibold text-slate-800">
              Finish {gate.lockedBy} first
            </p>
            <p className="mx-auto mt-1 max-w-md text-sm text-slate-500">
              {/* The blockers come from the API as fragments — "1 room type(s)
                  have no rate" — so the sentence has to be closed here rather
                  than running into the next one. */}
              {(gate.lockedWhy ?? 'That step is not complete yet')
                .replace(/[.\s]*$/, '')}. Steps run in order, so this one opens
              once it is done.
            </p>
            <Link to={STEP_LINK[gate.firstOpen] ?? '/onboarding'}
              className="mt-5 inline-flex items-center gap-2 rounded-xl bg-brand px-5 py-2.5 text-sm font-semibold text-white hover:bg-brand-dark">
              Go to {view.steps.find((x) => x.key === gate.firstOpen)?.label}
              <ChevronRight size={15} />
            </Link>
          </div>
        ) : children}
        <p className="mt-8 text-right font-serif text-sm italic text-brand/60">
          More guests<br />Brighter Tomorrows
        </p>
        {!gate.locked && footer && (
          <div className="mt-6 flex flex-wrap items-center justify-between gap-3">
            {footer}
          </div>
        )}
      </main>
    </div>
  )
}

/* ------------------------------------------------------- 09 connections --- */
export function OnboardingConnections() {
  const navigate = useNavigate()
  const propertyId = localStorage.getItem('property_id') ?? ''

  const skip = () => {
    void patchOnboarding(propertyId, { skipped: 'connections' })
      .finally(() => navigate('/onboarding/golive'))
  }

  return (
    <WizardFrame step="connections" eyebrow="09 — Booking Connections"
      title="Connect your booking sources"
      blurb="Enable direct bookings and connect supported sales channels."
      footer={<>
        {/* Back goes back. This pointed at go-live, which is forward. */}
        <button onClick={() => navigate('/onboarding/import')}
          className="flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-medium text-slate-600 hover:bg-slate-50">
          <ArrowLeft size={15} /> Back
        </button>
        <span className="flex gap-2">
          <button onClick={skip}
            className="rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-medium text-slate-600 hover:bg-slate-50">
            Skip for now
          </button>
          <button onClick={() => navigate('/onboarding/golive')}
            className="flex items-center gap-2 rounded-xl bg-brand px-5 py-2.5 text-sm font-semibold text-white hover:bg-brand-dark">
            Continue <ArrowRight size={15} />
          </button>
        </span>
      </>}>

      <div className="grid gap-4 lg:grid-cols-2">
        <div className="rounded-2xl border border-slate-100 bg-white p-5">
          <Globe size={22} className="text-brand" />
          <h2 className="mt-3 flex items-center gap-2 text-lg font-semibold text-ink">
            Website Booking Engine
            {/* The mockup says "Draft", which would claim a half-built engine
                exists. There is none at all, and the badge is the first thing
                read on this card. */}
            <span className="rounded-full bg-slate-75 px-2 py-0.5 text-xs font-medium text-slate-500">
              Not available
            </span>
          </h2>
          <p className="mt-1 text-sm text-slate-500">
            Accept reservations on your website.
          </p>
          <div className="mt-4 flex flex-wrap gap-2">
            <button disabled
              className="flex items-center gap-2 rounded-xl border border-slate-200 px-4 py-2.5 text-sm font-semibold text-slate-600 disabled:opacity-40">
              <Eye size={15} /> Preview
            </button>
            <button disabled
              className="flex items-center gap-2 rounded-xl bg-brand px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-40">
              Set up <ArrowRight size={15} />
            </button>
          </div>
          <p className="mt-3 text-xs text-slate-500">
            There is no booking engine in this system yet, so there is nothing
            to preview or set up. Direct bookings are taken at the front desk.
          </p>
        </div>

        <div className="rounded-2xl border border-slate-100 bg-white p-5">
          <Share2 size={22} className="text-brand" />
          <h2 className="mt-3 flex items-center gap-2 text-lg font-semibold text-ink">
            Channel Manager
            <span className="rounded-full bg-slate-75 px-2 py-0.5 text-xs font-medium text-slate-500">
              Not connected
            </span>
          </h2>
          <p className="mt-1 text-sm text-slate-500">
            Sync availability, rates and reservations through a supported
            provider.
          </p>
          <div className="mt-4">
            <button disabled
              className="rounded-xl bg-brand px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-40">
              Select provider
            </button>
          </div>
          <p className="mt-3 flex items-start gap-2 text-xs text-slate-500">
            <Info size={13} className="mt-0.5 shrink-0" />
            No provider integration exists yet, so there is nothing to choose
            from. Channel availability would also depend on the provider and on
            account approval.
          </p>
        </div>
      </div>

      {/* The checklist from the mockup, shown as what it is: the work that
          would be required once a provider integration exists. Every row is
          Pending because none of it is built, and saying so beats four rows
          that never change. */}
      <div className="mt-4 rounded-2xl border border-slate-100 bg-white p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold text-ink">
              Connection checklist
            </h2>
            <p className="text-sm text-slate-500">
              What connecting a channel will involve, once a provider is
              supported.
            </p>
          </div>
          <button disabled
            className="flex items-center gap-2 rounded-xl border border-slate-200 px-4 py-2.5 text-sm font-semibold text-slate-600 disabled:opacity-40">
            <PlayCircle size={15} /> Start connection setup
          </button>
        </div>
        <ol className="mt-4 divide-y divide-slate-100 rounded-xl border border-slate-100">
          {['Authorize provider', 'Map room types', 'Map rate plans',
            'Test synchronization'].map((label, i) => (
            <li key={label} className="flex items-center gap-3 px-4 py-3">
              <span className="grid h-6 w-6 place-items-center rounded-full bg-slate-75 text-[11px] font-semibold text-slate-500">
                {i + 1}
              </span>
              <span className="flex-1 text-sm text-slate-600">{label}</span>
              <span className="flex items-center gap-1.5 rounded-full bg-amber-50 px-2.5 py-1 text-xs font-medium text-amber-700">
                <span className="h-1.5 w-1.5 rounded-full bg-amber-400" />
                Pending
              </span>
            </li>
          ))}
        </ol>
      </div>
    </WizardFrame>
  )
}

/* ------------------------------------------------------------ 10 go live --- */
export function OnboardingGoLive() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const propertyId = localStorage.getItem('property_id') ?? ''
  const { data } = useQuery({
    queryKey: ['onboarding', propertyId],
    queryFn: () => getOnboarding(propertyId),
    enabled: propertyId !== '',
  })
  // The mockup shows a photograph of the property. The only picture the
  // property actually owns is the logo it uploaded in step 2, so that is what
  // appears; a stock beach would be a picture of somewhere else.
  const { data: logo } = useQuery({
    queryKey: ['property-logo', propertyId],
    queryFn: () => getPropertyLogo(propertyId),
    enabled: propertyId !== '',
  })

  // Activation opens the front desk on this data. Ticking this is a moment to
  // stop and look, which is the whole point of the step.
  const [reviewed, setReviewed] = useState(false)

  const resend = useMutation({ mutationFn: () => resendWelcomeEmails(propertyId) })

  const [stages, setStages] = useState(false)
  const test = useMutation({
    mutationFn: () => runTestBooking(propertyId),
    onSuccess: () => setStages(true),
  })
  const trial: TestBookingResult | undefined = test.data

  const activate = useMutation({
    mutationFn: () => activateProperty(propertyId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['onboarding', propertyId] })
      navigate('/reservations')
    },
  })

  const err = activate.error as { response?: { data?: { detail?: string } } } | null

  return (
    <WizardFrame step="golive" eyebrow="10 — Review & Go Live"
      title="Your property is ready for review"
      blurb="Check your setup before activating your front desk."
      footer={<>
        <button onClick={() => navigate('/onboarding/connections')}
          className="flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-medium text-slate-600 hover:bg-slate-50">
          <ArrowLeft size={15} /> Back
        </button>
        <span className="text-right">
          <button
            disabled={!data?.ready_to_activate || !reviewed || activate.isPending
                      || Boolean(data?.activated_at)}
            onClick={() => activate.mutate()}
            className="flex items-center gap-2 rounded-xl bg-brand px-6 py-3 text-sm font-semibold text-white hover:bg-brand-dark disabled:opacity-50">
            {activate.isPending && <Loader2 size={15} className="animate-spin" />}
            {data?.activated_at ? 'Already live' : 'Activate Property'}
          </button>
          <span className="mt-1 block text-xs text-slate-400">
            After activation, you will open Stay View.
          </span>
        </span>
      </>}>

      {data && (
        <>
          <div className="flex flex-wrap items-center gap-5 rounded-2xl border border-slate-100 bg-white p-5">
            <span className="grid h-24 w-32 shrink-0 place-items-center overflow-hidden rounded-xl bg-slate-75">
              {logo?.url
                ? <img src={logo.url} alt={data.property_name}
                    className="h-full w-full object-contain p-2" />
                : <Building2 size={26} className="text-slate-300" />}
            </span>
            <span>
              <span className="block text-xl font-bold text-slate-800">
                {data.property_name}
              </span>
              <span className="mt-1 flex items-center gap-1.5 text-sm text-slate-500">
                <MapPin size={14} /> {data.property_city || 'No address on file'}
              </span>
              <span className="mt-0.5 flex items-center gap-1.5 text-sm text-slate-500">
                <Globe size={14} /> {data.currency} · {data.timezone}
              </span>
            </span>
          </div>

          <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            {[
              { icon: BedDouble, n: data.counts.rooms, label: 'Rooms' },
              { icon: LayoutGrid, n: data.counts.room_types, label: 'Room Types' },
              { icon: Users, n: data.counts.staff_invited, label: 'Staff Invited' },
              // The mockup says "Bookings Imported". Nothing is imported yet,
              // and this is every booking the property has.
              { icon: CalendarDays, n: data.counts.bookings, label: 'Bookings' },
            ].map((c) => (
              <div key={c.label}
                className="flex items-center gap-3 rounded-2xl border border-slate-100 bg-white p-4">
                <c.icon size={20} className="text-brand" />
                <span>
                  <span className="block text-2xl font-bold tabular-nums text-slate-800">
                    {c.n}
                  </span>
                  <span className="block text-xs text-slate-500">{c.label}</span>
                </span>
              </div>
            ))}
          </div>

          <div className="mt-4 rounded-2xl border border-slate-100 bg-white p-5">
            <h2 className="text-lg font-semibold text-ink">Readiness checklist</h2>
            <p className="text-sm text-slate-500">
              Make sure everything is complete before going live.
            </p>
            <ul className="mt-4 divide-y divide-slate-100 rounded-xl border border-slate-100">
              {data.steps
                .filter((s) => s.key !== 'golive' && s.key !== 'account')
                // The six, plus anything else still outstanding.
                .filter((s) => CHECK_ROWS.includes(s.key) || !s.complete)
                .sort((a, b) => {
                  const rank = (k: string) => {
                    const i = CHECK_ROWS.indexOf(k)
                    return i === -1 ? CHECK_ROWS.length : i
                  }
                  return rank(a.key) - rank(b.key)
                })
                .map((s) => {
                  const Icon = STEP_ICON[s.key] ?? FileText
                  return (
                    <li key={s.key} className="flex items-center gap-3 px-4 py-3">
                      <Icon size={17} className="shrink-0 text-slate-400" />
                      <span className="min-w-0 flex-1">
                        <span className="block text-sm font-medium text-slate-700">
                          {CHECK_LABEL[s.key] ?? s.label}{s.optional && (
                            <span className="font-normal text-slate-400"> (optional)</span>
                          )}
                        </span>
                        {!s.complete && s.blocker && (
                          <span className="block text-xs text-amber-700">{s.blocker}</span>
                        )}
                      </span>
                      {/* A fixed column, not whatever is left over. Right-
                          aligning these against the button made every row
                          start its status at a different place, because the
                          wordings are different lengths. */}
                      <span className={`flex w-60 shrink-0 items-center gap-1.5 text-sm font-medium lg:w-[27rem] ${
                        s.complete ? 'text-emerald-600' : 'text-amber-600'}`}>
                        {s.complete
                          ? <CheckCircle2 size={15} />
                          : <span className="h-2 w-2 rounded-full bg-amber-400" />}
                        {/* An optional step is not "incomplete" — it is one
                            you have not needed. Saying so, and saying it can
                            wait, keeps the amber from reading as a fault. */}
                        {s.complete ? 'Complete'
                          : s.key === 'connections' ? 'Not connected · Set up later'
                          : s.key === 'import'
                            ? (s.skipped ? 'Skipped · Optional'
                                         : 'Not imported · Optional')
                          : s.optional ? 'Not set up' : 'Incomplete'}
                      </span>
                      {/* Fixed width as well. The status column being fixed
                          is not enough on its own: a wider button ("Review")
                          pushes the status left, so the rows only line up if
                          both right-hand columns are stable. */}
                      <Link to={STEP_LINK[s.key] ?? '/'}
                        className="flex w-24 shrink-0 items-center justify-center gap-1 rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-semibold text-slate-600 hover:bg-slate-50">
                        {/* "Edit" on a finished row, because that is what it
                            offers. "Review" is kept for the import, which is a
                            list to look over rather than a form to change. */}
                        {s.complete ? (CHECK_ACTION[s.key] ?? 'Edit')
                          : (CHECK_ACTION[s.key] ?? 'Set up')} <ChevronRight size={13} />
                      </Link>
                    </li>
                  )
                })}
            </ul>
          </div>

          <div className="mt-4 grid gap-4 lg:grid-cols-2">
            {/* The mockup pairs the confirmation with a green "Test booking
                passed" panel. There is no test-booking facility — a
                reservation that did not touch availability would need its own
                kind, and inventing a green tick for a test nobody ran is the
                one thing a readiness screen must never do. */}
            <div className={`rounded-2xl px-4 py-3 ${
              trial?.passed ? 'bg-emerald-50'
                : trial ? 'bg-red-50' : 'bg-slate-50'}`}>
              <div className="flex items-start gap-3">
                {trial?.passed
                  ? <CheckCircle2 size={20} className="mt-0.5 shrink-0 text-emerald-600" />
                  : trial
                    ? <AlertTriangle size={20} className="mt-0.5 shrink-0 text-red-600" />
                    : <Info size={16} className="mt-0.5 shrink-0 text-slate-400" />}
                <div className="min-w-0 flex-1">
                  <p className={`text-sm font-semibold ${
                    trial?.passed ? 'text-emerald-800'
                      : trial ? 'text-red-800' : 'text-slate-700'}`}>
                    {trial?.passed ? 'Test booking passed'
                      : trial ? 'Test booking failed'
                      : 'Test booking'}
                  </p>
                  <p className={`text-sm ${
                    trial?.passed ? 'text-positive'
                      : trial ? 'text-red-700' : 'text-slate-600'}`}>
                    {trial
                      ? <>
                          {trial.passed
                            ? `A ${trial.room_type} was held, confirmed and undone.`
                            : 'The booking path did not complete.'}
                          {' '}Test reservations do not affect live
                          availability — this one was rolled back, so no
                          reservation or number survives.
                        </>
                      : 'Put a booking through the real flow — hold, confirm, '
                        + 'check — then roll it back. Nothing is written.'}
                  </p>
                  <div className="mt-2 flex flex-wrap gap-2">
                    <button onClick={() => test.mutate()} disabled={test.isPending}
                      className="flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-50">
                      {test.isPending
                        ? <Loader2 size={13} className="animate-spin" />
                        : <Play size={13} />}
                      {trial ? 'Run again' : 'Run test booking'}
                    </button>
                    {trial && (
                      <button onClick={() => setStages((v) => !v)}
                        className="rounded-lg px-3 py-1.5 text-xs font-semibold text-slate-600 underline">
                        {stages ? 'Hide steps' : 'View test booking'}
                      </button>
                    )}
                  </div>
                  {trial && stages && (
                    <ol className="mt-3 space-y-1.5 border-t border-white/60 pt-2">
                      {trial.stages.map((st) => (
                        <li key={st.name} className="flex items-start gap-2 text-xs">
                          {st.passed
                            ? <Check size={13} className="mt-0.5 shrink-0 text-emerald-600" />
                            : <AlertTriangle size={13} className="mt-0.5 shrink-0 text-red-600" />}
                          <span className="text-slate-700">
                            <span className="font-semibold">{st.name}</span>
                            {' — '}{st.detail}
                          </span>
                        </li>
                      ))}
                    </ol>
                  )}
                  {test.error != null && (
                    <p className="mt-2 text-xs text-red-700">
                      The test could not be run.
                    </p>
                  )}
                </div>
              </div>
            </div>
            <label className="flex cursor-pointer items-start gap-3 rounded-2xl border border-slate-200 bg-white px-4 py-3">
              <input type="checkbox" className="mt-0.5 accent-brand"
                checked={reviewed}
                onChange={(e) => setReviewed(e.target.checked)} />
              <span className="text-sm text-slate-700">
                I have reviewed room inventory, rates and opening balances.
              </span>
            </label>
          </div>

          {data.ready_to_activate && !reviewed && !data.activated_at && (
            <p className="mt-4 flex items-start gap-2 rounded-xl bg-slate-50 px-4 py-3 text-sm text-slate-600">
              <Info size={16} className="mt-0.5 shrink-0 text-slate-400" />
              Tick the confirmation above to enable activation.
            </p>
          )}

          {!data.ready_to_activate && (
            <p className="mt-4 flex items-start gap-2 rounded-xl bg-amber-50 px-4 py-3 text-sm text-caution">
              <Info size={16} className="mt-0.5 shrink-0" />
              Activation stays disabled until every required step is complete.
              Optional steps do not hold it up.
            </p>
          )}
          {err && (
            <p className="mt-4 flex items-start gap-2 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700">
              <AlertTriangle size={16} className="mt-0.5 shrink-0" />
              {err.response?.data?.detail ?? 'The property could not be activated.'}
            </p>
          )}
          {data.activated_at && (<>
            <p className="mt-4 flex items-center gap-2 rounded-xl bg-emerald-50 px-4 py-3 text-sm text-emerald-700">
              <Play size={16} /> Live since {fmtDateTime(data.activated_at)}.
            </p>

            {/* Activation sends these once, which is not enough in practice:
                mail is filtered, addresses are mistyped, and the link expires
                after three days over a long weekend. */}
            <div className="mt-3 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-200 bg-white px-4 py-3">
              <p className="flex items-start gap-3 text-sm">
                <Mail size={18} className="mt-0.5 shrink-0 text-slate-400" />
                <span>
                  <span className="block font-semibold text-slate-700">
                    Welcome emails
                  </span>
                  <span className="block text-slate-500">
                    Send the sign-in details and a fresh set-password link to
                    anyone who has not chosen one yet. The previous link stops
                    working.
                  </span>
                </span>
              </p>
              <span className="text-right">
                <button onClick={() => resend.mutate()} disabled={resend.isPending}
                  className="flex items-center gap-2 rounded-xl border border-slate-200 px-4 py-2.5 text-sm font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-50">
                  {resend.isPending
                    ? <Loader2 size={15} className="animate-spin" />
                    : <Mail size={15} />}
                  Resend welcome emails
                </button>
                {resend.data && (
                  <span className={`mt-1 block text-xs ${
                    resend.data.failed.length > 0
                      ? 'text-amber-700' : 'text-positive'}`}>
                    {resend.data.sent === 0 && resend.data.failed.length === 0
                      ? 'Nobody is waiting — everyone has a password already.'
                      : `Sent to ${resend.data.sent}.`}
                    {resend.data.failed.length > 0
                      && ` Could not send to ${resend.data.failed.join(', ')}.`}
                  </span>
                )}
                {resend.error != null && (
                  <span className="mt-1 block max-w-xs text-xs text-red-700">
                    {(resend.error as { response?: { data?: { detail?: string } } })
                      .response?.data?.detail ?? 'They could not be sent.'}
                  </span>
                )}
              </span>
            </div>
          </>)}
        </>
      )}
    </WizardFrame>
  )
}


/** `/onboarding` — pick up where the operator left off.
 *
 * The step lives in the URL so that refreshing, going back and "Save & exit"
 * all behave, but that alone is not a sequence: without an entry point,
 * nothing resumes and any step can be reached by typing its address. This is
 * the door. It reads the step the server recorded and sends them there.
 *
 * It deliberately does not *lock* the other steps. The checklist is counted
 * from the property's own data, and every step is also reachable from the
 * ordinary screens, so a property that already has rooms should not be made
 * to walk through the rooms step to prove it. The sequence is a
 * recommendation the product makes, not a cage.
 */
export function OnboardingEntry() {
  const { session, loading } = useAuth()
  // Same rule as OnboardingGate, and for the same reason: a property id left
  // in this browser by a previous sign-in is not this caller's to use, and
  // asking the API about it returns 403 -- which reads as "onboarding is
  // broken" rather than "the browser is holding somebody else's id". The hook
  // cannot be used here because this route has to answer before a session
  // exists, so the check is written out.
  const props = useQuery({
    queryKey: ['properties'], queryFn: listProperties,
    enabled: session !== null,
  })
  const stored = localStorage.getItem('property_id') ?? ''
  const mine = props.data?.some((p) => p.id === stored) ?? false
  const propertyId = (mine ? stored : props.data?.[0]?.id) || ''
  useEffect(() => {
    if (propertyId && propertyId !== stored) {
      localStorage.setItem('property_id', propertyId)
    }
  }, [propertyId, stored])

  const { data, isLoading } = useQuery({
    queryKey: ['onboarding', propertyId],
    queryFn: () => getOnboarding(propertyId),
    enabled: propertyId !== '' && session !== null,
  })

  if (loading || props.isLoading) {
    return (
      <div className="grid h-full place-items-center bg-slate-50">
        <Loader2 className="animate-spin text-slate-400" />
      </div>
    )
  }

  // Nobody signed in, or signed in with no property yet: they have not
  // started. Send them to step one rather than to the sign-in page —
  // /onboarding is the front door of the funnel, and asking somebody to log
  // in before they have an account to log in to is a dead end.
  if (session === null || propertyId === '') {
    return <Navigate to="/onboarding/account" replace />
  }

  if (isLoading || !data) {
    return (
      <div className="grid h-full place-items-center bg-slate-50">
        <Loader2 className="animate-spin text-slate-400" />
      </div>
    )
  }

  // A property already live has finished; send it to the review page rather
  // than dropping it back at step one.
  const target = data.activated_at
    ? '/onboarding/golive'
    : STEP_LINK[data.current_step] ?? '/onboarding/account'

  return <Navigate to={target} replace />
}

/** The dashboard, unless the property has not been set up yet.
 *
 *  Nothing used to gate the application on onboarding: ProtectedRoute checks
 *  that you are signed in and on the right tier, and that is all. So an owner
 *  one step into a nine-step wizard could reach "/" and be shown a dashboard
 *  with no rooms, no rates and no bookings -- which looks like a broken
 *  product rather than an unfinished setup, and gives no hint that there is
 *  work outstanding or where to do it.
 *
 *  Sitting on the index route rather than inside Dashboard so the rule holds
 *  however somebody arrives: signing in, a first sign-in from a welcome link,
 *  a bookmark, or typing the address. Every other screen stays reachable --
 *  rooms and rates are setup work as much as daily work, and locking the
 *  whole application would make the wizard a trap rather than a path.
 */
export function OnboardingGate({ children }: { children: React.ReactNode }) {
  const { session, loading } = useAuth()

  // Asked for, not read from local storage. A tenant created by the platform
  // console has a property from its first minute, but this browser has never
  // heard of it -- and trusting an empty local value here sent a brand-new
  // owner to "create an account", a step they were past before they arrived.
  const props = useQuery({
    queryKey: ['properties'], queryFn: listProperties,
    enabled: session !== null,
  })
  // Same rule as useActivePropertyId: a stored id that is not in this
  // session's own list belongs to somebody else's tenant and is ignored.
  const stored = localStorage.getItem('property_id') ?? ''
  const mine = props.data?.some((p) => p.id === stored) ?? false
  const propertyId = (mine ? stored : props.data?.[0]?.id) || ''
  useEffect(() => {
    if (propertyId && propertyId !== stored) {
      localStorage.setItem('property_id', propertyId)
    }
  }, [propertyId, stored])

  const { data, isLoading } = useQuery({
    queryKey: ['onboarding', propertyId],
    queryFn: () => getOnboarding(propertyId),
    enabled: propertyId !== '' && session !== null,
  })

  if (loading || props.isLoading || (propertyId !== '' && isLoading)) {
    return (
      <div className="grid h-full place-items-center">
        <Loader2 className="animate-spin text-slate-400" />
      </div>
    )
  }

  // No property at all: there is genuinely nothing to show, so the wizard is
  // the page. /onboarding resolves which step that is.
  if (propertyId === '') {
    return <Navigate to="/onboarding" replace />
  }

  // A property that exists but has not gone live still gets its dashboard.
  //
  // This used to redirect, on the reasoning that an empty dashboard looks
  // like a broken product. True of a property on step one -- but it also
  // caught the far commoner case of a property fully set up and simply not
  // switched on yet, and then bounced the owner away from the dashboard
  // every single time they clicked it, with no way to look at their own
  // screen. A prompt says the same thing without taking the page away.
  return (
    <>
      {data && !data.activated_at && <GoLiveBanner data={data} />}
      {children}
    </>
  )
}

/** Says what is left before the property is trading, without blocking it. */
function GoLiveBanner({ data }: { data: OnboardingState }) {
  const navigate = useNavigate()
  const ready = data.ready_to_activate
  const left = (data.steps ?? []).filter(
    (s: OnboardingStep) => !s.complete && !s.optional && !s.skipped).length

  return (
    <div className={`mb-4 flex flex-wrap items-center gap-3 rounded-xl border px-4 py-3 ${
      ready
        ? 'border-emerald-200 bg-emerald-50'
        : 'border-amber-200 bg-amber-50'}`}>
      <Rocket size={18} className={ready ? 'text-emerald-600' : 'text-caution'} />
      <div className="min-w-0 flex-1">
        <p className={`text-sm font-semibold ${
          ready ? 'text-emerald-800' : 'text-caution'}`}>
          {ready
            ? 'This property is ready to go live.'
            : `Setup is not finished — ${left} step${left === 1 ? '' : 's'} left.`}
        </p>
        <p className="text-xs text-slate-600">
          {ready
            ? 'Rooms, rates and billing are all set. Switch it on to start taking bookings.'
            : 'You can look around, but figures stay empty until the setup is done.'}
        </p>
      </div>
      <button
        onClick={() => navigate(ready ? '/onboarding/golive' : '/onboarding')}
        className={`shrink-0 rounded-lg px-3 py-2 text-sm font-semibold text-white ${
          ready ? 'bg-emerald-600 hover:bg-emerald-700' : 'bg-brand hover:bg-brand-dark'}`}>
        {ready ? 'Go live' : 'Finish setup'}
      </button>
    </div>
  )
}
