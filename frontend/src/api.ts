import axios from 'axios'
import { announceSessionExpired } from './auth/sessionEvents'
import { markAllStale } from './lib/queryClient'

// All requests go through the gateway (/api/{service}/...).
// In dev, Vite proxies /api -> http://localhost:8000 (see vite.config.ts).
export const api = axios.create({
  baseURL: '/api',
  headers: { 'Content-Type': 'application/json' },
})

// Attach the session token to every request. Set by the auth context on login.
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('session_token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  // The client defaults to JSON, which is wrong for an upload: it overrides the
  // multipart content type and strips the boundary, so the server gets a body
  // it cannot parse and answers 422. Deleting it lets the browser set both.
  if (typeof FormData !== 'undefined' && config.data instanceof FormData) {
    delete config.headers['Content-Type']
  }
  return config
})

/**
 * FastAPI answers a *validation* failure with `detail` as a LIST of objects
 * ({type, loc, msg, input}), not the string our own HTTPExceptions raise. Every
 * screen reads `response.data.detail` and renders it, so an unexpected 422 used
 * to put an object into JSX and take the whole page down with "Objects are not
 * valid as a React child".
 *
 * Rather than teach thirty-one screens to defend themselves, `detail` is
 * flattened here, once, on the way in. Anything that reaches a component is a
 * string it can safely render, and the field name says which one was wrong.
 */
function fieldName(loc: unknown): string {
  if (!Array.isArray(loc)) return ''
  // ["body", "amount"] -> "amount"; drop the wrapper segment.
  const parts = loc.filter((x) => typeof x === 'string' && x !== 'body')
  return parts.join(' → ')
}

function flattenDetail(detail: unknown): string {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    const lines = detail.map((d) => {
      if (typeof d === 'string') return d
      const e = d as { msg?: string; loc?: unknown }
      const where = fieldName(e.loc)
      const what = e.msg ?? 'is not valid'
      return where ? `${where}: ${what}` : what
    })
    return lines.join('; ')
  }
  if (detail && typeof detail === 'object') {
    const e = detail as { msg?: string; message?: string }
    if (typeof e.msg === 'string') return e.msg
    if (typeof e.message === 'string') return e.message
  }
  return ''
}

/** A 401 on an ordinary call means the session behind the stored token has
 *  ended (expired, revoked, signed out elsewhere). The auth endpoints are left
 *  alone: a wrong password or a stale token being checked at start-up answers
 *  401 too, and the caller handles those itself. A request sent without a
 *  token is left alone as well — there was no session to expire, and raising
 *  the event for it could bounce the sign-in page onto itself. */
function isExpiredSession(error: unknown): boolean {
  const e = error as {
    response?: { status?: number }
    config?: { url?: string; headers?: Record<string, unknown> }
  }
  if (e?.response?.status !== 401) return false
  const url = e.config?.url ?? ''
  if (url.includes('iam/auth/')) return false
  return Boolean(e.config?.headers?.Authorization)
}

api.interceptors.response.use(
  (r) => {
    const method = (r.config?.method ?? 'get').toLowerCase()
    if (method !== 'get' && method !== 'head' && method !== 'options') markAllStale()
    return r
  },
  (error) => {
    if (isExpiredSession(error)) announceSessionExpired()
    const data = error?.response?.data
    if (data && typeof data === 'object' && 'detail' in data) {
      const flat = flattenDetail((data as { detail: unknown }).detail)
      ;(data as { detail: unknown }).detail = flat || undefined
    }
    return Promise.reject(error)
  },
)

/** The orchestration endpoints (/flows/*) live at the gateway root, not under
 *  /api. They go through the same client, so the same token, 401 handling and
 *  `detail` flattening apply; only the base differs. */
const FLOWS = { baseURL: '/flows' } as const

/* ---------------- Auth (dev session) ---------------- */
export interface SessionMembership {
  organization_id: string
  property_ids: string[]
}

export interface Session {
  token: string
  subject: string
  user_id: string
  display_name: string
  /** Profile photo, when the account has one. Absent for most accounts, and
   *  the UI then shows initials. */
  avatar_url?: string
  memberships: SessionMembership[]
  /** True when a temporary credential was used and must be replaced. */
  must_change_password?: boolean
  /** True for platform staff. A platform session has no memberships, but so
   *  does a tenant account whose membership lapsed — this says which. */
  is_platform?: boolean
}

/** The dev shortcut: a subject and no password. Refused in production, and
 *  refused for any account that has a password set. */
export async function login(subject: string): Promise<Session> {
  const { data } = await api.post<Session>('/iam/auth/login', { subject })
  return data
}

/** The real sign-in: property code, email and password. */
export async function loginWithPassword(
  propertyCode: string, email: string, password: string,
): Promise<Session> {
  const { data } = await api.post<Session>('/iam/auth/login', {
    property_code: propertyCode, email, password,
  })
  return data
}

/** Platform sign-in: email and password, no property code.
 *
 *  A separate call rather than an option on loginWithPassword, because it is
 *  a separate tier. A platform operator belongs to no organisation and so has
 *  no property code to type, and tenant sign-in resolves the user *through*
 *  one — the two cannot share a request shape.
 */
export class MfaRequired extends Error {
  challenge: string
  constructor(challenge: string) {
    super('Second factor required')
    this.name = 'MfaRequired'
    this.challenge = challenge
  }
}

export async function platformLogin(
  email: string, password: string,
): Promise<Session> {
  try {
    const { data } = await api.post<Session>('/iam/auth/platform-login', {
      email, password,
    })
    return data
  } catch (e) {
    // The password was right and the device has not been proved. The server
    // says so with a header carrying a short-lived challenge — worth nothing
    // on its own, exchangeable for a session by whoever holds the phone.
    const res = (e as { response?: { status?: number; headers?: Record<string, string> } }).response
    const challenge = res?.headers?.['x-mfa-challenge']
    if (res?.status === 401 && challenge) throw new MfaRequired(challenge)
    throw e
  }
}

/** Step two: the challenge from the password, plus a code from the device. */
export async function platformLoginVerify(
  challenge: string, code: string,
): Promise<Session> {
  const { data } = await api.post<Session>(
    '/iam/auth/platform-login/verify', { challenge, code })
  return data
}

/** Use a welcome or reset link to choose a password, and sign in. */
export async function setPassword(
  token: string, password: string,
): Promise<Session> {
  const { data } = await api.post<Session>('/iam/auth/set-password', {
    token, password,
  })
  return data
}

/** Ask for a reset link. Always answers the same way, known address or not. */
export async function forgotPassword(
  propertyCode: string, email: string,
): Promise<void> {
  await api.post('/iam/auth/forgot-password', {
    property_code: propertyCode, email,
  })
}

export interface SignUpBody {
  full_name: string
  email: string
  password: string
  phone?: string
  property_name?: string
  agreed: boolean
}

export interface SignUpResult {
  session: Session
  property_id: string
  property_code: string
}

/**
 * Create an account, an organisation and a property, and sign in.
 *
 * The only part of onboarding reachable without a session — every step after
 * it writes to one named property, so those must be authenticated.
 */
export async function signUp(body: SignUpBody): Promise<SignUpResult> {
  const { data } = await api.post<SignUpResult>('/iam/auth/sign-up', body)
  return data
}

/** Email a six-digit code to prove the address is reachable. */
export async function sendEmailOtp(
  email: string, fullName?: string,
): Promise<{ detail: string }> {
  const { data } = await api.post<{ detail: string }>('/iam/auth/email-otp/send', {
    email, full_name: fullName,
  })
  return data
}

/** Check the code. Marks the address verified for the next 30 minutes. */
export async function verifyEmailOtp(
  email: string, code: string,
): Promise<{ verified: boolean }> {
  const { data } = await api.post<{ verified: boolean }>(
    '/iam/auth/email-otp/verify', { email, code })
  return data
}

export async function fetchMe(): Promise<Session> {
  const { data } = await api.get<Session>('/iam/auth/me')
  return data
}

/** Ends the session server-side. The token is passed in rather than read by
 *  the request interceptor, because the caller clears storage straight after
 *  and the interceptor runs a tick later. */
export async function logoutSession(token: string): Promise<void> {
  await api.post('/iam/auth/logout', null, {
    headers: { Authorization: `Bearer ${token}` },
  })
}

// Example typed calls (booking-core availability).
export interface Availability {
  stay_date: string
  sellable: number
}

export async function getAvailability(params: {
  property_id: string
  room_type_id: string
  arrival_date: string
  departure_date: string
}): Promise<Availability[]> {
  const { data } = await api.get<Availability[]>('/booking/availability', { params })
  return data
}

export interface RoomTypeAvailability {
  room_type_id: string
  code: string | null
  name: string
  max_occupancy: number | null
  /** Minimum sellable across every night of the stay. */
  sellable: number
  nights: number
  nights_loaded: number
  /** The nightly rate this type sells at, or null when none is configured. */
  rate: string | null
  /** null when bookable; 'sold_out' or 'not_loaded' otherwise. */
  blocked_reason: 'sold_out' | 'not_loaded' | null
}

/** Availability for every room type at once, so a picker can show it up front. */
export async function getAvailabilityByRoomType(params: {
  property_id: string
  arrival_date: string
  departure_date: string
}): Promise<RoomTypeAvailability[]> {
  const { data } = await api.get<RoomTypeAvailability[]>(
    '/booking/availability/by-room-type', { params })
  return data
}

/* ---------------- Invoice & Credit Note (screen 116) ---------------- */

export interface InvTaxLine {
  code: string
  rate: string
  taxable_amount: string
  tax_amount: string
}

export interface InvLine {
  entry_id: string
  business_date: string
  description: string
  source_type: string | null
  quantity: string
  unit_price: string
  amount: string
  taxes: InvTaxLine[]
}

export interface InvParty {
  name: string | null
  address_line: string | null
  city: string | null
  state: string | null
  state_code: string | null
  postal_code: string | null
  country: string | null
  phone: string | null
  email: string | null
  gstin: string | null
}

export interface InvCreditNote {
  id: string
  number: number
  display_number: string
  reason: string
  amount: string
  status: string
  issued_at: string | null
  created_at: string
}

export interface Invoice {
  id: string
  status: 'draft' | 'issued' | 'cancelled'
  fiscal_series: string | null
  invoice_number: number | null
  display_number: string
  issued_at: string | null
  created_at: string
  notes: string | null
  cancel_reason: string | null
  folio_id: string
  folio_no: string | null
  reservation_number: string | null
  arrival_date: string | null
  departure_date: string | null
  nights: number | null
  adults: number | null
  children: number | null
  room_code: string | null
  room_type: string | null
  currency: string
  supplier: InvParty
  customer: InvParty
  lines: InvLine[]
  tax_lines: InvTaxLine[]
  subtotal: string
  tax_total: string
  total: string
  amount_received: string
  amount_taken: string
  amount_refunded: string
  credited: string
  balance_due: string
  amount_in_words: string
  payments: {
    id: string; received_at: string; method: string
    reference: string | null; amount: string
  }[]
  credit_notes: InvCreditNote[]
  audit: { label: string; actor: string; at: string; status: string }[]
  tax_compliance: {
    compliant: boolean
    missing: string[]
    supply_type: 'intra_state' | 'inter_state' | null
    note: string
  }
  can_issue: boolean
  can_cancel: boolean
  can_credit: boolean
}

export interface InvoiceListRow {
  id: string
  display_number: string
  status: string
  guest_name: string | null
  reservation_number: string | null
  issued_at: string | null
  created_at: string
  total: string
  balance_due: string
  credited: string
}

export async function listInvoices(
  propertyId: string,
  params: { status?: string; q?: string; page?: number; page_size?: number } = {},
): Promise<{ rows: InvoiceListRow[]; total: number; can_create: boolean }> {
  const { data } = await api.get('/finance/invoices',
    { params: { property_id: propertyId, ...params } })
  return data
}

export async function getInvoice(
  id: string, propertyId: string,
): Promise<Invoice> {
  const { data } = await api.get<Invoice>(`/finance/invoices/${id}`,
    { params: { property_id: propertyId } })
  return data
}

export async function createInvoice(body: {
  property_id: string
  folio_id: string
  customer_name?: string
  customer_gstin?: string
  customer_address?: string
  notes?: string
}): Promise<Invoice> {
  const { data } = await api.post<Invoice>('/finance/invoices', body)
  return data
}

export async function issueInvoice(
  id: string, propertyId: string,
): Promise<Invoice> {
  const { data } = await api.post<Invoice>(`/finance/invoices/${id}/issue`,
    null, { params: { property_id: propertyId } })
  return data
}

export async function cancelInvoice(
  id: string, body: { property_id: string; reason: string },
): Promise<Invoice> {
  const { data } = await api.post<Invoice>(`/finance/invoices/${id}/cancel`, body)
  return data
}

export async function createCreditNote(
  id: string, body: { property_id: string; amount: number; reason: string },
): Promise<Invoice> {
  const { data } = await api.post<Invoice>(
    `/finance/invoices/${id}/credit-notes`, body)
  return data
}

export interface InvoiceSettings {
  legal_name: string | null
  tagline: string | null
  address_line: string | null
  city: string | null
  state: string | null
  state_code: string | null
  postal_code: string | null
  country: string
  phone: string | null
  email: string | null
  /** Asked, not inferred. `null` means nobody has answered yet. */
  gst_registered: boolean | null
  gstin: string | null
  fiscal_series: string
  next_number: number
  footer_note: string | null
  /** Whether a quoted rate already contains tax or has it added. */
  tax_inclusive: boolean
  /** What the desk may take. */
  payment_methods: string[]
}

export async function getInvoiceSettings(
  propertyId: string,
): Promise<InvoiceSettings> {
  const { data } = await api.get<InvoiceSettings>('/finance/invoice-settings',
    { params: { property_id: propertyId } })
  return data
}

export async function saveInvoiceSettings(
  propertyId: string, body: InvoiceSettings,
): Promise<InvoiceSettings> {
  const { data } = await api.put<InvoiceSettings>('/finance/invoice-settings',
    body, { params: { property_id: propertyId } })
  return data
}

/* ---------------- Tax quote (screen 004) ---------------- */

export interface TaxQuote {
  room_charge: string
  nights: number
  /** Added on top of the room charge. */
  tax_total: string
  /** Already inside the charge; shown, never added again. */
  inclusive_tax: string
  total: string
  lines: {
    code: string; rate: string
    /**
     * 'percent' — the rate is a share of the charge — or 'amount', a flat sum
     * per night. Labelling a flat 20-rupee cess as "20%" misreports the bill.
     */
    rate_type?: 'percent' | 'amount'
    taxable_amount: string; tax_amount: string
  }[]
  /** False when no rule applies, so the UI can say "no tax configured". */
  has_rules: boolean
}

/** Quotes tax the way the folio will actually post it. */
export async function getTaxQuote(params: {
  property_id: string
  amount: number
  nights: number
  on_date?: string
}): Promise<TaxQuote> {
  const { data } = await api.get<TaxQuote>('/finance/tax-quote', { params })
  return data
}

/* ---------------- Commercial accounts (companies, agents) ---------------- */

export type AccountKind = 'company' | 'travel_agent' | 'ota' | 'government' | 'other'

export const ACCOUNT_KINDS: { value: AccountKind; label: string }[] = [
  { value: 'company', label: 'Company' },
  { value: 'travel_agent', label: 'Travel Agent' },
  { value: 'ota', label: 'OTA' },
  { value: 'government', label: 'Government' },
  { value: 'other', label: 'Other' },
]

export interface CommercialAccount {
  id: string
  code: string
  name: string
  legal_name: string | null
  kind: AccountKind
  kind_label: string
  gstin: string | null
  address_line: string | null
  city: string | null
  state: string | null
  state_code: string | null
  postal_code: string | null
  country: string | null
  contact_name: string | null
  contact_phone: string | null
  contact_email: string | null
  credit_limit: string | null
  credit_days: number | null
  commission_percent: string | null
  payment_terms: string | null
  status: string
  notes: string | null
  created_at: string
  /** Derived from the ledger, never stored. */
  bookings: number
  open_folios: number
  outstanding: string
  over_limit: boolean
}

export interface AccountIn {
  organization_id: string
  code: string
  name: string
  legal_name?: string | null
  kind?: AccountKind
  gstin?: string | null
  address_line?: string | null
  city?: string | null
  state?: string | null
  state_code?: string | null
  postal_code?: string | null
  country?: string
  contact_name?: string | null
  contact_phone?: string | null
  contact_email?: string | null
  credit_limit?: number | null
  credit_days?: number | null
  commission_percent?: number | null
  payment_terms?: string | null
  status?: string
  notes?: string | null
}

export async function listCommercialAccounts(
  organizationId: string,
  params: { q?: string; kind?: string; status?: string } = {},
): Promise<{
  rows: CommercialAccount[]; total: number
  kinds: { value: string; label: string; count: number }[]
  can_create: boolean
}> {
  const { data } = await api.get('/booking/commercial-accounts',
    { params: { organization_id: organizationId, ...params } })
  return data
}

export async function createCommercialAccount(
  body: AccountIn,
): Promise<CommercialAccount> {
  const { data } = await api.post<CommercialAccount>(
    '/booking/commercial-accounts', body)
  return data
}

export async function updateCommercialAccount(
  id: string, body: AccountIn,
): Promise<CommercialAccount> {
  const { data } = await api.patch<CommercialAccount>(
    `/booking/commercial-accounts/${id}`, body)
  return data
}

/* ---------------- Reservation Details (screen 028) ---------------- */

export type BookingSource = 'direct' | 'website' | 'phone' | 'email'
  | 'walk_in' | 'ota' | 'travel_agent' | 'corporate' | 'group' | 'other'

export const BOOKING_SOURCES: { value: BookingSource; label: string }[] = [
  { value: 'direct', label: 'Direct' },
  { value: 'website', label: 'Direct Website' },
  { value: 'phone', label: 'Phone' },
  { value: 'email', label: 'Email' },
  { value: 'walk_in', label: 'Walk-in' },
  { value: 'ota', label: 'OTA' },
  { value: 'travel_agent', label: 'Travel Agent' },
  { value: 'corporate', label: 'Corporate' },
  { value: 'group', label: 'Group' },
  { value: 'other', label: 'Other' },
]

export interface ResUnit {
  id: string
  status: string
  arrival_date: string
  departure_date: string
  nights: number
  adults: number
  children: number
  room_type: string | null
  room_code: string | null
  rate_plan: string | null
  meal_plan: string | null
  package: string | null
  /** When the guest said they would arrive and leave, as "14:00". */
  expected_arrival_time: string | null
  expected_departure_time: string | null
}

export interface ResGuest {
  id: string | null
  full_name: string | null
  email: string | null
  phone: string | null
  city: string | null
  country: string | null
  nationality: string | null
  id_type: string | null
  id_verified: boolean
  is_primary: boolean
}

export interface ReservationFull {
  id: string
  number: string
  status: string
  currency: string
  created_at: string
  property_id: string
  /** The day the property is trading — the oldest business day the night audit
   *  has not closed. Anything that posts money defaults to this, never to the
   *  browser's clock. */
  business_date: string
  guest: ResGuest | null
  arrival_date: string | null
  departure_date: string | null
  nights: number | null
  adults: number
  children: number
  rooms: number
  booking: {
    source: string | null
    source_label: string | null
    /** Which OTA or agent specifically, as against how the booking arrived. */
    business_source: string | null
    business_source_id: string | null
    market_segment: string | null
    market_segment_id: string | null
    purpose_of_stay: string | null
    company_name: string | null
    travel_agent: string | null
    reference: string | null
    remarks: string | null
    special_requests: string | null
    rate_plan: string | null
    meal_plan: string | null
    package: string | null
  }
  units: ResUnit[]
  guests: ResGuest[]
  charges: {
    id: string; business_date: string; posted_at: string; description: string
    entry_type: string; amount: string; is_reversal: boolean
    /** A debit that gave money back — a payment void, refund or reversal, or a
     *  deposit refund. Shown as a reversal like `is_reversal`, but kept out of
     *  the Adjustments total. */
    is_refund: boolean
    /** For a payment credit, the id of the payment it came from. */
    source_id: string | null
    /** Which folio the line is on. A booking split between a company and its
     *  guest carries entries on more than one. */
    folio_id: string
    folio_no: string | null
    /** Who posted it; null for the night audit and anything else the system
     *  did on its own. */
    posted_by: string | null
  }[]
  payments: {
    id: string; received_at: string; method: string
    reference: string | null; amount: string
    /** The gateway's own id for the capture. No card data is ever stored. */
    provider_transaction_id: string | null
    status: string | null
    /** What has been given back so far. `status` never says so — a payment
     *  voided in full stays 'succeeded'. */
    refunded: string
    /** The whole payment is back with the guest, so the ledger line is dead. */
    fully_reversed: boolean
    /** A void or refund is raised on this payment and not yet posted. Nothing
     *  has moved, but someone has to go and finish it. */
    reversal_pending: boolean
    /** Which kind is open — 'void', 'refund' or 'reversal' — so the menu can
     *  name it rather than guess. */
    reversal_pending_kind: string | null
  }[]
  activity: {
    at: string; label: string; actor: string
    reason: string | null; detail: string | null
  }[]
  financials: {
    currency: string
    total_charges: string
    total_paid: string
    balance_due: string
    paid_percent: number
    folio_id: string | null
    folio_status: string | null
  }
  policy: {
    name: string | null
    free_until_days: number | null
    penalty_nights: number | null
    no_show_refund: boolean | null
    text: string | null
    free_until_date: string | null
  }
  communications_note: string
  can_modify: boolean
  can_cancel: boolean
  can_assign: boolean
}

export interface BookingEdit {
  source?: string | null
  business_source_id?: string | null
  market_segment_id?: string | null
  purpose_of_stay?: string | null
  company_name?: string | null
  travel_agent?: string | null
  reference?: string | null
  remarks?: string | null
  special_requests?: string | null
}

/**
 * Corrects how a booking was recorded. Cannot move dates, change room type
 * or re-price — those go through Modify Stay.
 */
export async function updateBookingDetails(
  id: string, body: BookingEdit,
): Promise<ReservationFull> {
  const { data } = await api.patch<ReservationFull>(
    `/booking/reservations/${id}/booking`, body)
  return data
}

/** Maintenance and housekeeping on the rooms a booking occupies. */
export interface ReservationTask {
  id: string
  kind: 'work_order' | 'housekeeping'
  room: string | null
  title: string
  detail: string | null
  status: string
  priority: string | null
  on_date: string | null
  assigned_to: string | null
}

export async function getReservationTasks(
  id: string,
): Promise<ReservationTask[]> {
  const { data } = await api.get<ReservationTask[]>(
    `/booking/reservations/${id}/tasks`)
  return data
}

export async function getReservationFull(id: string): Promise<ReservationFull> {
  const { data } = await api.get<ReservationFull>(
    `/booking/reservations/${id}/full`)
  return data
}

/* ---------------- Waitlist & Enquiries (screen 032) ---------------- */

export interface EnquiryCard {
  id: string
  full_name: string
  email: string | null
  phone: string | null
  arrival_date: string | null
  departure_date: string | null
  nights: number | null
  adults: number
  children: number
  room_type_id: string | null
  room_type: string | null
  budget_min: string | null
  budget_max: string | null
  channel: string | null
  channel_label: string | null
  status: string
  next_follow_up_at: string | null
  follow_up_due: boolean
  notes: string | null
  converted_reservation_id: string | null
  reservation_number: string | null
  created_at: string
}

export interface EnquiryBoard {
  columns: { key: string; label: string; count: number; cards: EnquiryCard[] }[]
  kpis: {
    new_enquiries: { value: number; delta: number | null }
    waitlisted: { value: number; delta: number | null }
    follow_ups_due: number
    converted: { value: number; delta: number | null }
  }
  channels: { value: string; label: string; count: number }[]
  room_types: { value: string; label: string }[]
  lost_count: number
  can_create: boolean
  can_convert: boolean
}

export interface MatchRoomType {
  room_type_id: string
  name: string
  sellable: number
  nights: number
  nights_loaded: number
  blocked_reason: 'sold_out' | 'not_loaded' | null
  rate: string | null
  stay_total: string | null
  within_budget: boolean | null
  is_requested: boolean
  photo_url: string | null
}

export interface EnquiryMatch {
  enquiry_id: string
  arrival_date: string | null
  departure_date: string | null
  nights: number | null
  checked: boolean
  reason: string | null
  requested: MatchRoomType | null
  alternatives: MatchRoomType[]
  note: string
}

export interface EnquiryIn {
  property_id: string
  full_name: string
  email?: string | null
  phone?: string | null
  arrival_date?: string | null
  departure_date?: string | null
  adults?: number
  children?: number
  room_type_id?: string | null
  budget_min?: number | null
  budget_max?: number | null
  channel?: string | null
  next_follow_up_at?: string | null
  notes?: string | null
}

export async function getEnquiryBoard(
  propertyId: string,
  params: { q?: string; channel?: string; room_type_id?: string; due?: boolean } = {},
): Promise<EnquiryBoard> {
  const { data } = await api.get<EnquiryBoard>('/booking/enquiries',
    { params: { property_id: propertyId, ...params } })
  return data
}

export async function createEnquiry(body: EnquiryIn): Promise<EnquiryCard> {
  const { data } = await api.post<EnquiryCard>('/booking/enquiries', body)
  return data
}

export async function moveEnquiry(id: string, body: {
  property_id: string; status: string; reason?: string
  next_follow_up_at?: string | null
}): Promise<EnquiryCard> {
  const { data } = await api.post<EnquiryCard>(
    `/booking/enquiries/${id}/move`, body)
  return data
}

export async function getEnquiryMatch(
  id: string, propertyId: string,
): Promise<EnquiryMatch> {
  const { data } = await api.get<EnquiryMatch>(`/booking/enquiries/${id}/match`,
    { params: { property_id: propertyId } })
  return data
}

export async function convertEnquiry(id: string, body: {
  property_id: string; room_type_id?: string
  arrival_date?: string; departure_date?: string
}): Promise<EnquiryCard> {
  const { data } = await api.post<EnquiryCard>(
    `/booking/enquiries/${id}/convert`, body)
  return data
}

/* ---------------- Room Rack (screen 002) ---------------- */

export interface RackBar {
  id: string
  kind: 'reservation' | 'maintenance' | 'housekeeping'
  state: 'checked_in' | 'due_out' | 'departed' | 'arriving' | 'reserved'
    | 'cleaning' | 'maintenance'
  label: string
  sublabel: string
  /** Minutes past midnight. */
  start_minute: number
  end_minute: number
  starts_before: boolean
  ends_after: boolean
  /** False when the time is the property's published one, not a recorded one. */
  time_is_actual: boolean
  reservation_unit_id: string | null
  reservation_number: string | null
  guest_name: string | null
}

export interface RackRoom {
  room_id: string
  code: string
  room_type: string | null
  floor: string | null
  building: string | null
  service_status: string | null
  out_of_service: boolean
  cleanliness: string | null
  photo_url: string | null
  lane: 'occupied' | 'maintenance' | 'reserved' | 'dirty' | 'available'
  bars: RackBar[]
}

export interface RoomRack {
  business_date: string
  checkin_time: string
  checkout_time: string
  now_minute: number | null
  rooms: RackRoom[]
  kpis: {
    arrivals: number
    departures: number
    pending_check_ins: number
    unassigned_arrivals: number
  }
  lane_counts: Record<string, number>
  total_rooms: number
}

export async function getRoomRack(
  propertyId: string, onDate?: string,
): Promise<RoomRack> {
  const { data } = await api.get<RoomRack>('/booking/room-rack', {
    params: { property_id: propertyId, on_date: onDate },
  })
  return data
}

/* ---------------- Dashboard ---------------- */
export interface DashboardArrival {
  guest_name: string | null
  room_no: string
  arrival_time: string
  reservation_no: string
  status: string
}

export interface DashboardData {
  kpis: {
    occupancy_pct: number
    arrivals: number
    departures: number
    available_rooms: number
    today_revenue: number
  }
  /** Movement against yesterday, for the KPI trend indicators. Occupancy is in
   *  percentage points, revenue is a percentage change, arrivals and
   *  departures are counts. `null` means there is no comparable prior figure,
   *  and the card then shows no indicator at all. Optional: the gateway only
   *  started sending this block alongside the trend indicators. */
  deltas?: {
    occupancy_pct: number | null
    arrivals: number | null
    departures: number | null
    today_revenue: number | null
  }
  room_status: Record<string, number>
  total_rooms: number
  housekeeping: Record<string, number>
  todays_arrivals: DashboardArrival[]
  chart: { date: string; occupancy: number; revenue: number }[]
}

export async function getDashboard(
  propertyId: string,
  businessDate?: string,
): Promise<DashboardData> {
  const { data } = await api.get<DashboardData>('/dashboard', {
    ...FLOWS,
    params: { property_id: propertyId, business_date: businessDate },
  })
  return data
}

/* ---------------- Properties (for default selection) ---------------- */
export interface PropertyRow {
  id: string
  organization_id: string
  code: string
  name: string
  timezone: string
  currency: string
  status: string
}

export async function listProperties(): Promise<PropertyRow[]> {
  const { data } = await api.get<PropertyRow[]>('/iam/properties')
  return data
}

/* ---------------- Property Settings (US-026) ---------------- */
export interface PropertySettings {
  id: string
  organization_id: string
  code: string
  name: string
  timezone: string
  currency: string
  checkin_time?: string | null
  checkout_time?: string | null
  address?: string | null
  status: string
  version: number
}

// A dev subject header stands in for the authenticated user until Keycloak is
// wired; the axios interceptor attaches the session token automatically.

export async function getPropertySettings(propertyId: string): Promise<PropertySettings> {
  const { data } = await api.get<PropertySettings>(
    `/iam/properties/${propertyId}/settings`,
  )
  return data
}

export async function updatePropertySettings(
  propertyId: string,
  body: {
    version: number
    name: string
    timezone: string
    currency: string
    checkin_time?: string | null
    checkout_time?: string | null
    address?: string | null
    reason?: string | null
  },
): Promise<PropertySettings> {
  const { data } = await api.put<PropertySettings>(
    `/iam/properties/${propertyId}/settings`,
    body,
  )
  return data
}

/* ---------------- User Management (US-039) ---------------- */
export interface ManagedUser {
  id: string
  identity_provider: string
  subject_id: string
  display_name: string
  status: string
  version: number
  membership_status?: string | null
  employee_code?: string | null
  department?: string | null
  properties: string[]
  roles: string[]
  last_login_at?: string | null
  mfa_status: string
  active_sessions: number
}

export interface UserFilters {
  query?: string
  status?: string
  roleId?: string
  propertyId?: string
  departmentId?: string
}

export async function listUsers(f: UserFilters = {}): Promise<ManagedUser[]> {
  // Only send params that have a value — empty strings would fail UUID parsing.
  const params: Record<string, string> = {}
  if (f.query) params.query = f.query
  if (f.status) params.status_filter = f.status
  if (f.roleId) params.role_id = f.roleId
  if (f.propertyId) params.property_id = f.propertyId
  if (f.departmentId) params.department_id = f.departmentId
  const { data } = await api.get<ManagedUser[]>('/iam/users', { params })
  return data
}

export interface UserStats {
  active: number
  invited: number
  suspended: number
  active_sessions: number
}

export async function getUserStats(): Promise<UserStats> {
  const { data } = await api.get<UserStats>('/iam/users/stats')
  return data
}

export interface NamedOption { id: string; code: string; name: string }
export async function listDepartments(): Promise<NamedOption[]> {
  const { data } = await api.get<NamedOption[]>('/iam/departments')
  return data
}
export async function listRoles(): Promise<NamedOption[]> {
  const { data } = await api.get<NamedOption[]>('/iam/roles')
  return data
}

export async function createUser(body: {
  identity_provider?: string
  subject_id: string
  display_name: string
}): Promise<ManagedUser> {
  const { data } = await api.post<ManagedUser>('/iam/users', body)
  return data
}

export async function updateUser(
  userId: string,
  body: { version: number; display_name: string; reason?: string | null },
): Promise<ManagedUser> {
  const { data } = await api.patch<ManagedUser>(`/iam/users/${userId}`, body)
  return data
}

export async function setUserActive(
  userId: string,
  active: boolean,
  reason?: string,
): Promise<ManagedUser> {
  const action = active ? 'activate' : 'deactivate'
  const { data } = await api.post<ManagedUser>(`/iam/users/${userId}/${action}`, { reason })
  return data
}

export async function revokeSessions(userId: string, reason?: string): Promise<{ revoked_sessions: number }> {
  const { data } = await api.post(`/iam/users/${userId}/revoke-sessions`, { reason })
  return data
}

export interface ActivityEntry { action: string; entity_type: string; reason?: string | null; occurred_at: string }
export async function getUserActivity(userId: string): Promise<ActivityEntry[]> {
  const { data } = await api.get<ActivityEntry[]>(`/iam/users/${userId}/activity`)
  return data
}

/* ---------------- Invitations (US-040) ---------------- */
export interface InvitationBody {
  property_id: string
  full_name: string
  email: string
  phone?: string
  employee_code?: string
  department_id?: string
  role_ids: string[]
  outlet_ids?: string[]
  mfa_required: boolean
  temporary_access: boolean
  access_expiry?: string | null
  max_discount_approval?: number | null
  refund_approval: boolean
}

export interface InvitationResult {
  id: string
  email: string
  full_name: string
  status: string
  created_user_id?: string | null
  role_count: number
}

export async function listInvitations(): Promise<InvitationResult[]> {
  const { data } = await api.get<InvitationResult[]>('/iam/invitations')
  return data
}

export async function createInvitation(body: InvitationBody): Promise<InvitationResult> {
  const { data } = await api.post<InvitationResult>('/iam/invitations', body)
  return data
}

export interface UserAccess {
  user_id: string
  full_name: string
  email: string
  phone?: string | null
  employee_code?: string | null
  department_id?: string | null
  property_id?: string | null
  role_ids: string[]
  mfa_required: boolean
  temporary_access: boolean
  access_expiry?: string | null
  max_discount_approval?: number | null
  refund_approval: boolean
  version: number
}

export async function getUserAccess(userId: string): Promise<UserAccess> {
  const { data } = await api.get<UserAccess>(`/iam/users/${userId}/access`)
  return data
}

export async function updateUserAccess(
  userId: string,
  body: {
    version: number
    full_name: string
    phone?: string | null
    employee_code?: string | null
    department_id?: string | null
    property_id: string
    role_ids: string[]
    mfa_required: boolean
    temporary_access: boolean
    access_expiry?: string | null
    max_discount_approval?: number | null
    refund_approval: boolean
    reason?: string | null
  },
): Promise<UserAccess> {
  const { data } = await api.put<UserAccess>(`/iam/users/${userId}/access`, body)
  return data
}

/* ---------------- Roles & Permission Matrix (US-041) ---------------- */
export interface RoleCatalogue {
  modules: { code: string; label: string }[]
  actions: string[]
}
export interface RoleListItem { id: string; code: string; name: string; user_count: number }
export interface RoleMatrix {
  id: string
  code: string
  name: string
  user_count: number
  permissions: Record<string, Record<string, boolean>>
  record_scope: string
  property_scope: string
  max_discount?: number | null
  max_refund?: number | null
}

export async function getRoleCatalogue(): Promise<RoleCatalogue> {
  const { data } = await api.get<RoleCatalogue>('/iam/roles/catalogue')
  return data
}
export async function getRolesWithCounts(): Promise<RoleListItem[]> {
  const { data } = await api.get<RoleListItem[]>('/iam/roles/list')
  return data
}
export async function getRoleMatrix(roleId: string): Promise<RoleMatrix> {
  const { data } = await api.get<RoleMatrix>(`/iam/roles/${roleId}/matrix`)
  return data
}
export async function saveRoleMatrix(roleId: string, body: {
  permissions: Record<string, Record<string, boolean>>
  record_scope: string
  property_scope: string
  max_discount?: number | null
  max_refund?: number | null
  reason?: string | null
}): Promise<RoleMatrix> {
  const { data } = await api.put<RoleMatrix>(`/iam/roles/${roleId}/matrix`, body)
  return data
}
export async function addRole(name: string): Promise<RoleListItem> {
  const { data } = await api.post<RoleListItem>('/iam/roles/create', { name })
  return data
}
export async function cloneRole(roleId: string, name: string): Promise<RoleListItem> {
  const { data } = await api.post<RoleListItem>(`/iam/roles/${roleId}/clone`, { name })
  return data
}

/* ---------------- Approvals (US-042) ---------------- */
export interface ApprovalRequest {
  id: string
  category: string
  title: string
  entity_ref?: string | null
  guest_name?: string | null
  requested_by_name?: string | null
  requested_by_role?: string | null
  policy_rule_text?: string | null
  amount: number
  amount_context?: string | null
  status: string
  due_at?: string | null
  due_in?: string | null
}
export interface ApprovalPolicy {
  id: string
  name: string
  category: string
  applies_to?: string | null
  initiator_roles: string[]
  approver_roles: string[]
  threshold_value?: number | null
  threshold_unit: string
  two_level: boolean
  prohibit_self_approval: boolean
}
export interface ApprovalHistoryItem {
  id: string
  category: string
  title: string
  entity_ref?: string | null
  amount: number
  status: string
  decided_by?: string | null
  decided_at?: string | null
}

export async function getApprovalQueue(category?: string): Promise<ApprovalRequest[]> {
  const { data } = await api.get<ApprovalRequest[]>('/iam/approvals/queue', { params: category ? { category } : {} })
  return data
}
export async function getApprovalPolicies(category?: string): Promise<ApprovalPolicy[]> {
  const { data } = await api.get<ApprovalPolicy[]>('/iam/approvals/policies', { params: category ? { category } : {} })
  return data
}
export async function approveRequest(id: string, comment?: string): Promise<void> {
  await api.post(`/iam/approvals/${id}/approve`, { comment })
}
export async function rejectRequest(id: string, comment?: string): Promise<void> {
  await api.post(`/iam/approvals/${id}/reject`, { comment })
}
export async function getApprovalHistory(): Promise<ApprovalHistoryItem[]> {
  const { data } = await api.get<ApprovalHistoryItem[]>('/iam/approvals/history')
  return data
}

/* ---------------- Audit Log (US-043) ---------------- */
export interface AuditEvent {
  id: string
  occurred_at: string
  actor_subject?: string | null
  action: string
  entity_type: string
  entity_id?: string | null
  summary: string
  risk: string
  result: string
  correlation_id?: string | null
}
export interface AuditEventDetail extends AuditEvent {
  reason?: string | null
  before?: Record<string, unknown> | null
  after?: Record<string, unknown> | null
}

export async function getAuditEvents(filters: {
  action?: string; entity_type?: string; query?: string; date_from?: string
  date_to?: string
  /** One id, or a comma-separated set, to scope the trail to one record. */
  entity_id?: string
} = {}): Promise<AuditEvent[]> {
  const params: Record<string, string> = {}
  if (filters.action) params.action = filters.action
  if (filters.entity_type) params.entity_type = filters.entity_type
  if (filters.entity_id) params.entity_id = filters.entity_id
  if (filters.query) params.query = filters.query
  if (filters.date_from) params.date_from = filters.date_from
  if (filters.date_to) params.date_to = filters.date_to
  const { data } = await api.get<AuditEvent[]>('/iam/audit/events', { params })
  return data
}
export async function getAuditEvent(id: string): Promise<AuditEventDetail> {
  const { data } = await api.get<AuditEventDetail>(`/iam/audit/events/${id}`)
  return data
}
export async function getAuditActions(): Promise<string[]> {
  const { data } = await api.get<string[]>('/iam/audit/actions')
  return data
}

/* ---------------- Reservations flow ---------------- */
export interface RoomType {
  id: string
  code: string
  name: string
  max_occupancy: number
}

export async function listRoomTypes(propertyId: string): Promise<RoomType[]> {
  const { data } = await api.get<RoomType[]>('/booking/room-types', {
    params: { property_id: propertyId },
  })
  return data
}

export interface Guest {
  id: string
  title?: string | null
  full_name: string
  email?: string | null
  phone?: string | null
  nationality?: string | null
}

export async function searchGuests(
  organizationId: string,
  query?: string,
): Promise<Guest[]> {
  const { data } = await api.get<Guest[]>('/booking/guests', {
    params: { organization_id: organizationId, query },
  })
  return data
}

export async function createGuest(body: {
  organization_id: string
  full_name: string
  email?: string
  phone?: string
  nationality?: string
  title?: string
  address_line?: string
  city?: string
  state?: string
  postal_code?: string
  country?: string
}): Promise<Guest> {
  const { data } = await api.post<Guest>('/booking/guests', body)
  return data
}

export async function getGuest(id: string): Promise<Guest> {
  const { data } = await api.get<Guest>(`/booking/guests/${id}`)
  return data
}

export async function updateGuest(id: string, body: {
  full_name: string
  email?: string | null
  phone?: string | null
  nationality?: string | null
}): Promise<Guest> {
  const { data } = await api.patch<Guest>(`/booking/guests/${id}`, body)
  return data
}

/* ---------------- Guest Directory (screen 010) ---------------- */

export interface DirectoryRow {
  id: string
  /** G-1001, allocated per organisation. */
  reference: string | null
  full_name: string
  email: string | null
  phone: string | null
  city: string | null
  country: string | null
  nationality: string | null
  last_stay: string | null
  total_stays: number
  lifetime_value: string
  status: 'in_house' | 'returning' | 'new'
  status_label: string
  in_house: boolean
  /** Empty until SCR-067 gives preferences a table. */
  preferences: string[]
}

export interface GuestDirectory {
  kpis: {
    total_guests: number
    in_house: number
    returning: number
    new_guests: number
  }
  rows: DirectoryRow[]
  total: number
  page: number
  page_size: number
  countries: string[]
  can_create: boolean
  can_export: boolean
}

export interface DirectoryQuery {
  q?: string
  status?: string
  country?: string
  last_stay?: string
  sort?: string
  direction?: 'asc' | 'desc'
  page?: number
  page_size?: number
}

export async function getGuestDirectory(
  organizationId: string, params: DirectoryQuery = {},
): Promise<GuestDirectory> {
  const { data } = await api.get<GuestDirectory>('/booking/guests/directory', {
    params: { organization_id: organizationId, ...params },
  })
  return data
}

export interface HoldResult {
  reservation_id: string
  reservation_unit_id: string
  hold_id: string
  number: string
  status: string
  /** One id per room booked. */
  reservation_unit_ids?: string[]
}

/** One room on a booking: its own type, board, occupancy and count. */
export interface HoldLine {
  room_type_id: string
  units?: number
  adults?: number
  children?: number
  rate_plan_id?: string
  meal_plan_id?: string
  /**
   * Agreed rate per night for this room, before tax. Sent so the figure the
   * guest was quoted survives to check-in — without it the desk falls back to
   * the room type's list price and asks for a number nobody agreed to.
   */
  nightly_rate?: number
}

export async function createHold(body: {
  organization_id: string
  property_id: string
  arrival_date: string
  departure_date: string
  idempotency_key: string
  /**
   * One entry per distinct room. Send this, or the flat
   * room_type_id/units/adults/children below, which say the same thing for a
   * booking of a single kind of room.
   */
  lines?: HoldLine[]
  room_type_id?: string
  units?: number
  adults?: number
  children?: number
  guest_id?: string
  source?: BookingSource
  market_segment_id?: string
  purpose_of_stay?: string
  company_name?: string
  travel_agent?: string
  reference?: string
  special_requests?: string
  rate_plan_id?: string
  meal_plan_id?: string
  business_source_id?: string
  bill_to?: 'guest' | 'company'
  commercial_account_id?: string
  /** Draw these rooms from a group block instead of general availability. */
  group_block_id?: string
  expected_arrival_time?: string
  expected_departure_time?: string
}): Promise<HoldResult> {
  const { data } = await api.post<HoldResult>('/booking/holds', body)
  return data
}

export async function confirmReservation(reservationId: string): Promise<void> {
  await api.post(`/booking/reservations/${reservationId}/confirm`, {})
}

/* ---------------- Reservations list/detail (US-028) ---------------- */
export interface ReservationRow {
  id: string
  number: string
  status: string
  /** null when no guest is on file yet. */
  guest_name: string | null
  /** The one type, or the first and a count — sized for a list column. */
  room_type: string
  /** Every distinct type on the booking. */
  room_types?: string[]
  arrival_date?: string | null
  departure_date?: string | null
  nights: number
  units: number
  currency: string
  /** When the booking was taken, as against when the guest arrives. */
  booked_at?: string | null
  /** When the guest said they would arrive and leave. Null is the ordinary
   *  case and means nobody asked — not midnight, and not the property's
   *  standard hour, so a screen with none shows none. */
  expected_arrival_time?: string | null
  expected_departure_time?: string | null
  adults?: number
  children?: number
  /** Rooms actually assigned. */
  room_codes?: string[]
  /** Units still waiting for a room — the desk's job queue. */
  unassigned_units?: number
  /** Board or rate basis, whichever the booking carries. */
  plan?: string | null
  /** All boards on the booking; rooms can sit on different ones. */
  plans?: string[]
  /** Posted to the folio so far. Ties to the folio ledger, and is zero until
   *  the guest has stayed a night — a room is charged the night it is slept
   *  in, so a future booking has no charges at all. */
  total_charges?: string
  /** What the stay is expected to come to: the rate it was sold at for the
   *  whole stay, plus extras already on the folio. This is what a person
   *  means by the total of a booking, and what a list column should show. */
  total_amount?: string
  /** The room half of that on its own, before extras. */
  room_value?: string
  total_paid?: string
  /** Against `total_amount`, so a prepaid booking reads as settled rather
   *  than as a negative balance. */
  balance_due?: string
  has_folio?: boolean
}
export interface ReservationUnitLine {
  id: string
  room_type: string
  arrival_date: string
  departure_date: string
  nights: number
  adults: number
  children: number
  status: string
  assigned_room?: string | null
  // The room's id as well as its code, so a screen can link to the room.
  assigned_room_id?: string | null
  value?: number
}
export interface ReservationDetail {
  id: string
  number: string
  status: string
  currency: string
  guest_name: string | null
  units: ReservationUnitLine[]
  // Room revenue for the booking — what a deposit percentage is a percentage
  // of. See screen 058.
  booking_value?: number
}

export async function listReservations(propertyId: string, filters: {
  status?: string; query?: string; arrival_from?: string; arrival_to?: string
} = {}): Promise<ReservationRow[]> {
  const params: Record<string, string> = { property_id: propertyId }
  if (filters.status) params.status_filter = filters.status
  if (filters.query) params.query = filters.query
  if (filters.arrival_from) params.arrival_from = filters.arrival_from
  if (filters.arrival_to) params.arrival_to = filters.arrival_to
  const { data } = await api.get<ReservationRow[]>('/booking/reservations', { params })
  return data
}
export async function getReservationDetail(id: string): Promise<ReservationDetail> {
  const { data } = await api.get<ReservationDetail>(`/booking/reservations/${id}/detail`)
  return data
}

/* ---------------- Checkout Folio (US-007) ---------------- */
export interface FolioEntry {
  id: string
  business_date: string
  entry_type: string
  amount: number
  currency: string
  source_type: string
  description: string
  department: string
}
export interface FolioSummary {
  folio_id: string
  subtotal: number
  taxes: number
  grand_total: number
  advance_paid: number
  balance_due: number
  currency: string
  tax_lines: {
    tax_code: string
    rate_snapshot: string
    taxable_amount: string
    tax_amount: string
  }[]
}
export interface FolioView {
  reservation_id: string
  number: string
  status: string
  guest_name: string | null
  organization_id: string
  property_id: string
  folio_id: string
  entries: FolioEntry[]
  summary: FolioSummary
  units: ReservationUnitLine[]
}

export async function getFolioView(reservationId: string): Promise<FolioView> {
  const { data } = await api.get<FolioView>(`/reservations/${reservationId}/folio-view`, FLOWS)
  return data
}
/** The reservation's folio, creating it if this is the first thing on it.
 *
 * Idempotent: a reservation that already has one gets the same id back. The
 * first charge of a stay is exactly when a folio should come into existence —
 * refusing to post because nothing has been posted is a loop with no way in.
 */
export async function ensureFolio(
  reservationId: string, organizationId: string, propertyId: string,
): Promise<{ id: string }> {
  const { data } = await api.post<{ id: string }>(
    `/reservations/${reservationId}/folio`,
    {
      organization_id: organizationId,
      property_id: propertyId,
      reservation_id: reservationId,
    },
    FLOWS,
  )
  return data
}

/** What a desk may bill for, and what each is for tax. */
export interface ChargeTypeOption {
  value: string
  label: string
  tax_category: string | null
}

export async function listChargeTypes(): Promise<ChargeTypeOption[]> {
  const { data } = await api.get<ChargeTypeOption[]>('/finance/charge-types')
  return data
}

/* ---------------- Folio operations: split, transfer, cut -------------------
 *
 * Entries are never moved between folios — a transfer posts a credit on one
 * and a debit on the other, so both folios keep their history and the movement
 * shows on both.
 * ------------------------------------------------------------------------ */
/** When a folio's invoice number comes out of the fiscal series. */
export type InvoiceTiming = 'on_checkout' | 'post_checkout'

export interface FolioRow {
  id: string
  folio_no: string | null
  type: string
  status: string
  currency: string
  balance: string
  entry_count: number
  /** Who this folio bills, where it is not simply the booking's guest. */
  sharer_name: string | null
  /** The recipient's GST number — what makes the printed bill a B2B invoice. */
  gstin: string | null
  show_tax_on_folio: boolean
  invoice_number_timing: InvoiceTiming
  /** Null on a master folio; on a child, the folio it hangs off. */
  parent_folio_id: string | null
}

export async function listReservationFolios(
  reservationId: string, propertyId: string,
): Promise<FolioRow[]> {
  const { data } = await api.get<FolioRow[]>(
    `/finance/reservations/${reservationId}/folios`,
    { params: { property_id: propertyId } })
  return data
}

export async function openFolio(
  propertyId: string,
  body: {
    reservation_id: string
    type?: string
    sharer_name?: string | null
    gstin?: string | null
    show_tax_on_folio?: boolean
    invoice_number_timing?: InvoiceTiming
    parent_folio_id?: string | null
  },
): Promise<FolioRow> {
  const { data } = await api.post<FolioRow>('/finance/folios/open', body,
    { params: { property_id: propertyId } })
  return data
}

export async function transferFolio(
  folioId: string, propertyId: string,
  body: { to_folio_id: string; entry_ids?: string[]; reason?: string | null },
): Promise<{ moved: string; lines: number; folios: FolioRow[] }> {
  const { data } = await api.post(
    `/finance/folios/${folioId}/transfer`, body,
    { params: { property_id: propertyId } })
  return data
}

export async function cutFolio(
  folioId: string, propertyId: string, reason?: string | null,
): Promise<{ closed_folio_id: string; new_folio_id: string; folios: FolioRow[] }> {
  const { data } = await api.post(
    `/finance/folios/${folioId}/cut`, { reason },
    { params: { property_id: propertyId } })
  return data
}

export async function addFolioCharge(body: {
  organization_id: string
  property_id: string
  folio_id: string
  /** The net the guest owes, after any discount. */
  amount: number
  /** Omit it: the server stamps the day the property is trading. */
  business_date?: string
  source_type: string
  source_line_key: string
  /** What it was for, in words — the line a guest queries. */
  note?: string | null
  quantity?: number | null
  unit_amount?: number | null
  discount_amount?: number | null
}): Promise<void> {
  await api.post('/finance/charges', body)
}
export async function collectFolioPayment(body: {
  organization_id: string
  property_id: string
  method: string
  /** Omit it: the server stamps the day the property is trading. */
  business_date?: string
  /** Required by the server for every method that has one — card, UPI, bank
   *  transfer, cheque, wallet. Cash has nothing to quote. */
  reference?: string | null
  /** What the payment was for, in the desk's words. Becomes the folio line's
   *  description — `reference` is what the guest quotes, this is what the
   *  hotel reads. */
  note?: string | null
  allocations: { folio_id: string; amount: number }[]
}): Promise<void> {
  await api.post('/finance/payments', body)
}

export interface SettleResult {
  reservation_id: string
  folio_id: string
  room_charge: string
  advance_paid: string
  balance: string
}

export async function settleReservation(
  reservationId: string,
  body: {
    room_charge: number
    business_date: string
    method?: string
    advance_amount?: number
  },
): Promise<SettleResult> {
  const { data } = await api.post<SettleResult>(
    `/reservations/${reservationId}/settle`,
    body,
    FLOWS,
  )
  return data
}

/* ---------------- Group blocks ----------------------------------------------
 *
 * A block holds rooms off sale without selling them, and hands back what the
 * group has not taken by its cut-off. Rooms sit in `allotment_units`, which
 * every availability calculation already subtracts.
 * ------------------------------------------------------------------------ */
export interface GroupBlockLine {
  room_type_id: string
  room_type: string
  rooms_blocked: number
  rooms_picked_up: number
  rooms_still_held: number
  nightly_rate: string | null
}

export interface GroupBlock {
  id: string
  code: string
  name: string
  status: 'open' | 'released' | 'cancelled'
  commitment: 'tentative' | 'definite'
  arrival_date: string
  departure_date: string
  cut_off_date: string | null
  commercial_account_id: string | null
  account_name: string | null
  notes: string | null
  lines: GroupBlockLine[]
  rooms_blocked: number
  rooms_picked_up: number
  rooms_still_held: number
}

export interface GroupBlockRow {
  id: string
  code: string
  name: string
  status: 'open' | 'released' | 'cancelled'
  commitment: 'tentative' | 'definite'
  arrival_date: string
  departure_date: string
  cut_off_date: string | null
  account_name: string | null
  rooms_blocked: number
  rooms_picked_up: number
}

/** A block a booking being taken right now could draw a room from. */
export interface BlockOption {
  id: string
  code: string
  name: string
  arrival_date: string
  departure_date: string
  rooms_still_held: number
  /** The rates agreed for this block, by room type. */
  rates: { room_type_id: string; nightly_rate: string }[]
}

export async function listGroupBlocks(
  propertyId: string, status?: string,
): Promise<GroupBlockRow[]> {
  const { data } = await api.get<GroupBlockRow[]>(
    `/booking/properties/${propertyId}/group-blocks`,
    { params: status ? { status } : {} })
  return data
}

export async function getGroupBlock(
  blockId: string, propertyId: string,
): Promise<GroupBlock> {
  const { data } = await api.get<GroupBlock>(
    `/booking/group-blocks/${blockId}`, { params: { property_id: propertyId } })
  return data
}

export async function createGroupBlock(
  propertyId: string,
  body: {
    name: string
    arrival_date: string
    departure_date: string
    cut_off_date?: string | null
    commitment: 'tentative' | 'definite'
    commercial_account_id?: string | null
    notes?: string | null
    lines: { room_type_id: string; rooms_blocked: number
             nightly_rate?: number | null }[]
  },
): Promise<GroupBlock> {
  const { data } = await api.post<GroupBlock>(
    `/booking/properties/${propertyId}/group-blocks`, body)
  return data
}

/** Give back every room the block still holds. Pick-ups are untouched. */
export async function releaseGroupBlock(
  blockId: string, propertyId: string,
  status: 'released' | 'cancelled' = 'released',
): Promise<GroupBlock> {
  const { data } = await api.post<GroupBlock>(
    `/booking/group-blocks/${blockId}/release`, { status },
    { params: { property_id: propertyId } })
  return data
}

/** Open blocks that still hold a room on every night of these dates. */
export async function blocksForDates(
  propertyId: string, arrival: string, departure: string,
): Promise<BlockOption[]> {
  const { data } = await api.get<BlockOption[]>(
    `/booking/properties/${propertyId}/group-blocks/available`,
    { params: { arrival_date: arrival, departure_date: departure } })
  return data
}

/* ---------------- Rooming list ----------------------------------------------
 *
 * Who is in each room. The rooms come first and the names follow, because
 * that is the sequence in real life: a group books thirty rooms in March and
 * sends the names in May. A room may sit unnamed, and the screen counts how
 * many still are.
 * ------------------------------------------------------------------------ */
export interface RoomingRow {
  reservation_unit_id: string
  reservation_id: string
  reservation_number: string
  room_type_id: string
  room_type: string
  room_code: string | null
  arrival_date: string
  departure_date: string
  adults: number
  children: number
  status: string
  guest_id: string | null
  guest_name: string | null
  /** Who made the booking — shown when the room has no name of its own. */
  booked_by: string | null
}

export interface RoomingList {
  block_id: string
  code: string
  name: string
  rooms: RoomingRow[]
  named: number
  unnamed: number
}

export async function getRoomingList(
  blockId: string, propertyId: string,
): Promise<RoomingList> {
  const { data } = await api.get<RoomingList>(
    `/booking/group-blocks/${blockId}/rooming-list`,
    { params: { property_id: propertyId } })
  return data
}

/** Say who is in this room. Send nothing to clear it. */
export async function setRoomGuest(
  unitId: string, propertyId: string,
  body: { guest_id?: string | null; full_name?: string | null
          phone?: string | null; email?: string | null },
): Promise<RoomingRow> {
  const { data } = await api.put<RoomingRow>(
    `/booking/reservation-units/${unitId}/guest`, body,
    { params: { property_id: propertyId } })
  return data
}

/** Turn a list of names into rooms drawn from the block — one booking, many
 *  named rooms. */
export async function importRoomingList(
  blockId: string, propertyId: string,
  body: {
    room_type_id: string
    entries: { full_name: string; phone?: string | null
               email?: string | null; adults?: number; children?: number }[]
    arrival_date?: string | null
    departure_date?: string | null
  },
): Promise<{ reservation_id: string; reservation_number: string
             rooms_created: number; rooming: RoomingList }> {
  const { data } = await api.post(
    `/booking/group-blocks/${blockId}/rooming-list`, body,
    { params: { property_id: propertyId } })
  return data
}

/* ---------------- OTA actions -----------------------------------------------
 *
 * What the hotel owes a channel, and the clock on each. A no-show on an OTA
 * booking has to be reported to that OTA within 24 hours or the hotel pays
 * commission on a room nobody slept in.
 * ------------------------------------------------------------------------ */
export interface OtaAction {
  id: string
  reservation_id: string | null
  reservation_unit_id: string
  reservation_number: string | null
  ota_name: string | null
  ota_reservation_code: string | null
  action_type: string
  action_label: string
  status: 'open' | 'reported' | 'dismissed'
  due_at: string
  /** Negative once the window has closed. Served, so every viewer reads the
   *  same number against the same clock. */
  hours_left: number
  reported_at: string | null
  reference: string | null
  note: string | null
}

export interface OtaSummary {
  rows: OtaAction[]
  open_count: number
  overdue_count: number
  window_hours: number
}

export async function listOtaActions(
  propertyId: string, status?: string,
): Promise<OtaSummary> {
  const { data } = await api.get<OtaSummary>(
    `/booking/properties/${propertyId}/ota-actions`,
    { params: status ? { status } : {} })
  return data
}

/** Say the channel was told, and what they said back. */
export async function closeOtaAction(
  actionId: string, propertyId: string,
  body: { status: 'reported' | 'dismissed'; reference?: string | null
          note?: string | null },
): Promise<OtaAction> {
  const { data } = await api.post<OtaAction>(
    `/booking/ota-actions/${actionId}`, body,
    { params: { property_id: propertyId } })
  return data
}

/* ---------------- Rooms, Room Types & Amenities (screens 008/059/060/062) ---- */
export interface RoomAmenity {
  id: string
  code: string
  name: string
  icon: string | null
  category: string
}

export interface RoomPhoto {
  id: string
  url: string
  caption: string | null
  is_primary: boolean
  sort_order: number
}

export interface RoomRow {
  id: string
  code: string
  room_type_id: string
  room_type_name: string
  /** The structure link; `building`/`floor` are the display names for them. */
  building_id: string | null
  floor_id: string | null
  building: string | null
  floor: string | null
  bed_setup: string | null
  view_type: string | null
  max_adults: number
  max_children: number
  base_rate: string | null
  status: string
  service_status: string
  housekeeping_zone: string | null
  accessibility: string
  near_elevator: boolean
  version: number
  occupancy_state: string
  housekeeping_state: string
  guest_name: string | null
  arrival_date: string | null
  departure_date: string | null
  primary_photo_url: string | null
  amenities: RoomAmenity[]
  /** Today's active block, when there is one. */
  block_group_id: string | null
  block_reason: string | null
  block_start: string | null
  block_end: string | null
}

export interface RoomDetail extends RoomRow {
  notes: string | null
  active_from: string | null
  retired_on: string | null
  created_at: string | null
  updated_at: string | null
  photos: RoomPhoto[]
}

export interface RoomStats {
  total_units: number
  occupied: number
  available: number
  cleaning: number
  maintenance: number
  occupancy_pct: number
  available_pct: number
}

export interface RoomTypeRow {
  id: string
  code: string
  name: string
  description: string | null
  max_adults: number
  max_children: number
  max_occupancy: number
  base_rate: string | null
  bed_setup: string | null
  size_sqft: number | null
  child_policy: string | null
  extra_bed_available: boolean
  extra_bed_charge: string | null
  room_view: string | null
  default_rate_plan: string | null
  status: string
  version: number
  room_count: number
  active_room_count: number
  primary_photo_url: string | null
  amenity_ids: string[]
  photos: RoomPhoto[]
  created_by_name: string | null
  created_at: string | null
  updated_by_name: string | null
  updated_at: string | null
}

export interface RoomTypeStats {
  room_types: number
  active_types: number
  inactive_types: number
  total_rooms: number
  active_rooms: number
  inactive_rooms: number
  max_guest_capacity: number
  average_base_rate: string | null
}

export interface RoomFacets {
  floors: string[]
  buildings: string[]
  housekeeping_zones: string[]
  room_types: RoomTypeRow[]
}

export interface NamedRef { id: string; name: string }

export interface AmenityRow {
  id: string
  code: string
  name: string
  category: string
  category_label: string
  icon: string | null
  is_chargeable: boolean
  guest_visible: boolean
  description: string | null
  status: string
  version: number
  room_count: number
  room_types: NamedRef[]
  created_by_name: string | null
  created_at: string | null
  updated_by_name: string | null
  updated_at: string | null
}

export interface AmenityStats {
  total: number
  guest_visible: number
  active: number
  inactive: number
  by_category: Record<string, number>
}

/** Category codes and labels exactly as screen 062 writes them. */
export const AMENITY_CATEGORIES: { code: string; label: string }[] = [
  { code: 'in_room', label: 'In-Room' },
  { code: 'bathroom', label: 'Bathroom' },
  { code: 'technology', label: 'Technology' },
  { code: 'food_beverage', label: 'Food & Beverage' },
  { code: 'recreation', label: 'Recreation' },
  { code: 'safety_security', label: 'Safety & Security' },
]

export interface RoomListFilters {
  floor?: string
  building?: string
  room_type_id?: string
  state?: string
  search?: string
  /** The endpoint caps this at 500 and defaults to 200. */
  limit?: number
  offset?: number
}

export async function listRooms(
  propertyId: string,
  f: RoomListFilters = {},
): Promise<{ items: RoomRow[]; total: number; stats: RoomStats }> {
  const { data } = await api.get('/booking/rooms', {
    params: { property_id: propertyId, ...f },
  })
  return data
}

/**
 * Permanently remove a room.
 *
 * Refused with a 409 when the room has ever been booked, checked into, or
 * given a housekeeping task — its own message says which. Deactivating is the
 * usual answer; this is for a room typed in by mistake.
 */
export async function deleteRoom(
  propertyId: string, roomId: string,
): Promise<void> {
  await api.delete(`/booking/rooms/${roomId}`,
    { params: { property_id: propertyId } })
}

/** What happened to one room in a bulk delete. */
export interface BulkDeleteRoomResult {
  id: string
  code: string
  deleted: boolean
  /** Why not, when `deleted` is false. */
  reason: string | null
}

export interface BulkDeleteResult {
  deleted: number
  refused: number
  results: BulkDeleteRoomResult[]
}

/**
 * Delete several rooms at once.
 *
 * Partial on purpose: rooms that carry history are kept and come back with
 * the reason, rather than failing the whole batch.
 */
export async function bulkDeleteRooms(
  propertyId: string, roomIds: string[], reason?: string,
): Promise<BulkDeleteResult> {
  const { data } = await api.post<BulkDeleteResult>(
    '/booking/rooms/bulk-delete', { room_ids: roomIds, reason },
    { params: { property_id: propertyId } })
  return data
}

/**
 * Open the guest folio as a printable A4 tax invoice.
 *
 * Fetched rather than linked: the API needs the Authorization header that the
 * axios interceptor adds, and a plain `window.open` carries no headers — it
 * came back 401. The bytes are handed to the browser as a blob so its own PDF
 * viewer opens, which is where the print and save controls already live.
 *
 * The object URL is revoked on a delay rather than immediately; revoking it
 * synchronously can cancel the load before the new tab has read it.
 */
/** Open the rendered tax invoice in a new tab.
 *
 * The same reasoning as the folio: window.print() prints the browser's idea of
 * this screen — sidebar, buttons and all — which is not a document anyone can
 * hand a guest, let alone file as a tax record.
 */
export async function openInvoicePdf(
  propertyId: string, invoiceId: string,
): Promise<void> {
  const { data } = await api.get<Blob>(`/finance/invoices/${invoiceId}/pdf`, {
    params: { property_id: propertyId },
    responseType: 'blob',
  })
  const url = URL.createObjectURL(new Blob([data], { type: 'application/pdf' }))
  window.open(url, '_blank', 'noopener')
  setTimeout(() => URL.revokeObjectURL(url), 60_000)
}

/** Open one payment's receipt in the PDF viewer. */
export async function openPaymentReceipt(
  propertyId: string, paymentId: string,
): Promise<void> {
  const { data } = await api.get<Blob>(
    `/finance/payments/${paymentId}/receipt.pdf`,
    { params: { property_id: propertyId }, responseType: 'blob' })
  const url = URL.createObjectURL(
    new Blob([data], { type: 'application/pdf' }))
  window.open(url, '_blank', 'noopener')
  setTimeout(() => URL.revokeObjectURL(url), 60_000)
}

/** Mail that receipt, with the PDF attached. */
export async function emailPaymentReceipt(
  propertyId: string, paymentId: string, to?: string | null,
): Promise<{ sent_to: string; receipt_no: string }> {
  const { data } = await api.post(
    `/finance/payments/${paymentId}/receipt/email`,
    { to: to ?? null }, { params: { property_id: propertyId } })
  return data
}

export async function openFolioPdf(
  propertyId: string, folioId: string,
): Promise<void> {
  const { data } = await api.get<Blob>(`/finance/folios/${folioId}/pdf`, {
    params: { property_id: propertyId },
    responseType: 'blob',
  })
  const url = URL.createObjectURL(
    new Blob([data], { type: 'application/pdf' }))
  window.open(url, '_blank', 'noopener')
  setTimeout(() => URL.revokeObjectURL(url), 60_000)
}

/** The form the guest checks and signs at the desk.
 *
 * Opened, not downloaded: it is printed and handed over, and the browser's
 * viewer offers Save anyway. Keyed by reservation *unit* because the card is
 * about one room's stay — a booking holding three rooms registers three
 * arrivals, and one card covering all of them has nowhere for three
 * signatures.
 */
export async function openRegistrationCard(
  propertyId: string, reservationUnitId: string,
): Promise<void> {
  const { data } = await api.get<Blob>(
    `/finance/reservation-units/${reservationUnitId}/registration-card`, {
      params: { property_id: propertyId },
      responseType: 'blob',
    })
  const url = URL.createObjectURL(
    new Blob([data], { type: 'application/pdf' }))
  window.open(url, '_blank', 'noopener')
  setTimeout(() => URL.revokeObjectURL(url), 60_000)
}

export async function getRoomFacets(propertyId: string): Promise<RoomFacets> {
  const { data } = await api.get<RoomFacets>('/booking/rooms/facets', {
    params: { property_id: propertyId },
  })
  return data
}

export async function getRoom(propertyId: string, roomId: string): Promise<RoomDetail> {
  const { data } = await api.get<RoomDetail>(`/booking/rooms/${roomId}`, {
    params: { property_id: propertyId },
  })
  return data
}

export interface RoomWriteBody {
  room_type_id: string
  code: string
  /** Moves the room to this floor, setting building, ids and names together. */
  floor_id?: string | null
  building?: string | null
  floor?: string | null
  bed_setup?: string | null
  view_type?: string | null
  max_adults: number
  max_children: number
  base_rate?: string | null
  housekeeping_zone?: string | null
  accessibility: string
  near_elevator: boolean
  status: string
  service_status: string
  notes?: string | null
  amenity_ids: string[]
}

export async function createRoom(
  propertyId: string,
  body: RoomWriteBody,
): Promise<RoomDetail> {
  const { data } = await api.post<RoomDetail>('/booking/rooms', body, {
    params: { property_id: propertyId },
  })
  return data
}

export async function updateRoom(
  propertyId: string,
  roomId: string,
  body: Partial<RoomWriteBody> & { version: number; reason?: string },
): Promise<RoomDetail> {
  const { data } = await api.put<RoomDetail>(`/booking/rooms/${roomId}`, body, {
    params: { property_id: propertyId },
  })
  return data
}

export async function setRoomServiceStatus(
  propertyId: string,
  roomId: string,
  body: { service_status: string; version: number; reason?: string },
): Promise<RoomDetail> {
  const { data } = await api.post<RoomDetail>(
    `/booking/rooms/${roomId}/service-status`,
    body,
    { params: { property_id: propertyId } },
  )
  return data
}







/* ---------------- Room assignment (screen 008 "Assign") ---------------- */
export interface AssignableUnit {
  reservation_unit_id: string
  reservation_id: string
  reservation_number: string | null
  guest_name: string | null
  arrival_date: string
  departure_date: string
  adults: number
  children: number
  status: string
}

export async function listAssignableUnits(
  propertyId: string,
  roomId: string,
): Promise<AssignableUnit[]> {
  const { data } = await api.get<AssignableUnit[]>(
    `/booking/rooms/${roomId}/assignable`,
    { params: { property_id: propertyId } },
  )
  return data
}

/** Assigns via the existing flow endpoint; the GiST exclusion rejects overlaps with 409. */
/* ---------------- Assign Rooms panel ---------------- */

export interface CandidateRoom {
  room_id: string
  code: string
  floor: string | null
  cleanliness: string | null
  /** False when the room still needs cleaning — assignable, but say so. */
  ready: boolean
  /** False when it cannot be taken; still listed so the desk sees why. */
  available: boolean
  blocked_reason: 'occupied' | 'out_of_service' | 'retired' | null
  blocked_by: string | null
}

export interface AssignUnit {
  unit_id: string
  reservation_id: string
  number: string
  guest_name: string | null
  arrival_date: string
  departure_date: string
  nights: number
  adults: number
  children: number
  room_type_id: string | null
  room_type: string | null
  candidates: CandidateRoom[]
  /** How many candidates can actually be taken. */
  free_count: number
}

export interface AssignBoard {
  business_date: string
  on_date: string
  days: { day: string; unassigned: number; is_weekend: boolean; is_today: boolean }[]
  groups: {
    room_type_id: string | null
    name: string
    count: number
    units: AssignUnit[]
  }[]
  total_unassigned: number
  can_assign: boolean
}

/** Rooms free across a stay, before the booking exists. */
export async function getAvailableRooms(params: {
  property_id: string
  room_type_id: string
  arrival_date: string
  departure_date: string
}): Promise<CandidateRoom[]> {
  const { data } = await api.get<CandidateRoom[]>('/booking/available-rooms',
    { params })
  return data
}

export async function getAssignBoard(
  propertyId: string, onDate?: string, days = 7,
): Promise<AssignBoard> {
  const { data } = await api.get<AssignBoard>('/booking/assign-board', {
    params: { property_id: propertyId, on_date: onDate, days },
  })
  return data
}

export async function assignRoomToUnit(
  reservationUnitId: string,
  roomId: string,
): Promise<{ reservation_unit_id: string; room_id: string }> {
  const { data } = await api.post(
    `/booking/reservation-units/${reservationUnitId}/assign`,
    { room_id: roomId },
  )
  return data
}

/* ---------------- Room Type Management (screen 060) ---------------- */
export interface RoomTypeFilters {
  search?: string
  status?: string
  room_view?: string
  sort?: string
}

export async function listManagedRoomTypes(
  propertyId: string,
  f: RoomTypeFilters = {},
): Promise<RoomTypeRow[]> {
  const { data } = await api.get<RoomTypeRow[]>('/booking/room-types/manage', {
    params: { property_id: propertyId, ...f },
  })
  return data
}

export async function getRoomTypeStats(propertyId: string): Promise<RoomTypeStats> {
  const { data } = await api.get<RoomTypeStats>('/booking/room-types/stats', {
    params: { property_id: propertyId },
  })
  return data
}

export async function getRoomTypeViews(propertyId: string): Promise<string[]> {
  const { data } = await api.get<string[]>('/booking/room-types/views', {
    params: { property_id: propertyId },
  })
  return data
}

export async function getManagedRoomType(
  propertyId: string,
  roomTypeId: string,
): Promise<RoomTypeRow> {
  const { data } = await api.get<RoomTypeRow>(
    `/booking/room-types/manage/${roomTypeId}`,
    { params: { property_id: propertyId } },
  )
  return data
}

export async function createManagedRoomType(
  propertyId: string,
  body: Record<string, unknown>,
): Promise<RoomTypeRow> {
  const { data } = await api.post<RoomTypeRow>('/booking/room-types/manage', body, {
    params: { property_id: propertyId },
  })
  return data
}

export async function updateManagedRoomType(
  propertyId: string,
  roomTypeId: string,
  body: Record<string, unknown> & { version: number },
): Promise<RoomTypeRow> {
  const { data } = await api.put<RoomTypeRow>(
    `/booking/room-types/manage/${roomTypeId}`, body,
    { params: { property_id: propertyId } },
  )
  return data
}

/**
 * Permanently remove a room type.
 *
 * Refused with a 409 when anything still depends on it — rooms, bookings,
 * rate plans, packages, rate rules or enquiries — and the message names them.
 * Deactivating is the usual answer; this is for one created by mistake.
 */
export async function deleteManagedRoomType(
  propertyId: string, roomTypeId: string,
): Promise<void> {
  await api.delete(`/booking/room-types/manage/${roomTypeId}`,
    { params: { property_id: propertyId } })
}

export async function duplicateRoomType(
  propertyId: string,
  roomTypeId: string,
  body: { name?: string; code?: string } = {},
): Promise<RoomTypeRow> {
  const { data } = await api.post<RoomTypeRow>(
    `/booking/room-types/manage/${roomTypeId}/duplicate`, body,
    { params: { property_id: propertyId } },
  )
  return data
}

export async function setRoomTypeStatus(
  propertyId: string,
  roomTypeId: string,
  body: { status: string; version: number; reason?: string },
): Promise<RoomTypeRow> {
  const { data } = await api.post<RoomTypeRow>(
    `/booking/room-types/manage/${roomTypeId}/status`, body,
    { params: { property_id: propertyId } },
  )
  return data
}

/* ---------------- Amenities Management (screen 062) ---------------- */
export interface AmenityFilters {
  search?: string
  category?: string
  status?: string
  guest_visible?: boolean
  limit?: number
  offset?: number
}

export async function listAmenities(
  propertyId: string,
  f: AmenityFilters = {},
): Promise<{ items: AmenityRow[]; total: number }> {
  const { data } = await api.get('/booking/amenities', {
    params: { property_id: propertyId, ...f },
  })
  return data
}

export async function getAmenityStats(propertyId: string): Promise<AmenityStats> {
  const { data } = await api.get<AmenityStats>('/booking/amenities/stats', {
    params: { property_id: propertyId },
  })
  return data
}

export async function createAmenity(
  propertyId: string,
  body: Record<string, unknown>,
): Promise<AmenityRow> {
  const { data } = await api.post<AmenityRow>('/booking/amenities', body, {
    params: { property_id: propertyId },
  })
  return data
}

export async function updateAmenity(
  propertyId: string,
  amenityId: string,
  body: Record<string, unknown> & { version: number },
): Promise<AmenityRow> {
  const { data } = await api.put<AmenityRow>(`/booking/amenities/${amenityId}`, body, {
    params: { property_id: propertyId },
  })
  return data
}

export async function mergeAmenity(
  propertyId: string,
  amenityId: string,
  body: { target_amenity_id: string; reason?: string },
): Promise<AmenityRow> {
  const { data } = await api.post<AmenityRow>(
    `/booking/amenities/${amenityId}/merge`, body,
    { params: { property_id: propertyId } },
  )
  return data
}

/* ---------------- Room type photos (screens 060 / 059) ---------------- */
/** Uploads go as multipart; the browser sets its own boundary header. */
export async function uploadRoomTypePhoto(
  propertyId: string,
  roomTypeId: string,
  file: File,
  /** "Change Photo" replaces the hero; "Add More" appends. */
  makePrimary = false,
): Promise<RoomTypeRow> {
  const form = new FormData()
  form.append('file', file)
  form.append('make_primary', String(makePrimary))
  const { data } = await api.post<RoomTypeRow>(
    `/booking/room-types/manage/${roomTypeId}/photos`,
    form,
    { params: { property_id: propertyId }, headers: { 'Content-Type': undefined } },
  )
  return data
}

export async function setRoomTypePrimaryPhoto(
  propertyId: string,
  roomTypeId: string,
  photoId: string,
): Promise<RoomTypeRow> {
  const { data } = await api.post<RoomTypeRow>(
    `/booking/room-types/manage/${roomTypeId}/photos/${photoId}/primary`,
    {},
    { params: { property_id: propertyId } },
  )
  return data
}

export async function deleteRoomTypePhoto(
  propertyId: string,
  roomTypeId: string,
  photoId: string,
): Promise<RoomTypeRow> {
  const { data } = await api.delete<RoomTypeRow>(
    `/booking/room-types/manage/${roomTypeId}/photos/${photoId}`,
    { params: { property_id: propertyId } },
  )
  return data
}

/* ---------------- Buildings & Floors (screen 061) ---------------- */
export interface FloorRow {
  id: string
  building_id: string
  code: string
  name: string
  from_room_no: string | null
  to_room_no: string | null
  display_order: number
  status: string
  version: number
  room_count: number
  created_by_name: string | null
  created_at: string | null
  updated_by_name: string | null
  updated_at: string | null
}

export interface FloorDetail extends FloorRow {
  building_name: string
  active_reservations: number
  can_deactivate: boolean
  blocker_message: string | null
}

export interface BuildingRow {
  id: string
  code: string
  name: string
  display_order: number
  status: string
  version: number
  floor_count: number
  room_count: number
  floors: FloorRow[]
  created_by_name: string | null
  created_at: string | null
  updated_by_name: string | null
  updated_at: string | null
}

export interface DependentRoom {
  id: string
  code: string
  room_type_name: string
  status: string
  service_status: string
  active_reservations: number
}

export async function listBuildings(propertyId: string): Promise<BuildingRow[]> {
  const { data } = await api.get<BuildingRow[]>('/booking/buildings', {
    params: { property_id: propertyId },
  })
  return data
}

export async function createBuilding(
  propertyId: string,
  body: Record<string, unknown>,
): Promise<BuildingRow> {
  const { data } = await api.post<BuildingRow>('/booking/buildings', body, {
    params: { property_id: propertyId },
  })
  return data
}

export async function updateBuilding(
  propertyId: string,
  buildingId: string,
  body: Record<string, unknown> & { version: number },
): Promise<BuildingRow> {
  const { data } = await api.put<BuildingRow>(`/booking/buildings/${buildingId}`, body, {
    params: { property_id: propertyId },
  })
  return data
}

export async function getFloor(
  propertyId: string,
  floorId: string,
): Promise<FloorDetail> {
  const { data } = await api.get<FloorDetail>(`/booking/floors/${floorId}`, {
    params: { property_id: propertyId },
  })
  return data
}

export async function getFloorDependents(
  propertyId: string,
  floorId: string,
): Promise<DependentRoom[]> {
  const { data } = await api.get<DependentRoom[]>(
    `/booking/floors/${floorId}/dependents`,
    { params: { property_id: propertyId } },
  )
  return data
}

export async function createFloor(
  propertyId: string,
  body: Record<string, unknown>,
): Promise<FloorDetail> {
  const { data } = await api.post<FloorDetail>('/booking/floors', body, {
    params: { property_id: propertyId },
  })
  return data
}

export async function updateFloor(
  propertyId: string,
  floorId: string,
  body: Record<string, unknown> & { version: number },
): Promise<FloorDetail> {
  const { data } = await api.put<FloorDetail>(`/booking/floors/${floorId}`, body, {
    params: { property_id: propertyId },
  })
  return data
}

export async function moveFloorRooms(
  propertyId: string,
  floorId: string,
  body: { target_floor_id: string; room_ids?: string[]; reason?: string },
): Promise<FloorDetail> {
  const { data } = await api.post<FloorDetail>(
    `/booking/floors/${floorId}/move-rooms`, body,
    { params: { property_id: propertyId } },
  )
  return data
}

/** A room no floor claims — invisible on the Buildings & Floors tree. */
export interface UnassignedRoom {
  id: string
  code: string
  room_type: string | null
  /** What the seed left in the denormalised columns; often the only clue. */
  floor_hint: string | null
  building_hint: string | null
}

export async function listUnassignedRooms(
  propertyId: string,
): Promise<UnassignedRoom[]> {
  const { data } = await api.get<UnassignedRoom[]>('/booking/rooms/unassigned',
    { params: { property_id: propertyId } })
  return data
}

/**
 * Put rooms onto a floor wherever they are now.
 *
 * `moveFloorRooms` moves rooms *off* a named floor, so it can never reach a
 * room that is on no floor. This is the other direction.
 */
export async function assignRoomsToFloor(
  propertyId: string,
  floorId: string,
  body: { room_ids: string[]; reason?: string },
): Promise<FloorDetail> {
  const { data } = await api.post<FloorDetail>(
    `/booking/floors/${floorId}/assign-rooms`, body,
    { params: { property_id: propertyId } },
  )
  return data
}

export async function reorderBuildings(
  propertyId: string,
  ids: string[],
): Promise<BuildingRow[]> {
  const { data } = await api.post<BuildingRow[]>('/booking/buildings/reorder',
    { ids }, { params: { property_id: propertyId } })
  return data
}

export async function reorderFloors(
  propertyId: string,
  ids: string[],
): Promise<BuildingRow[]> {
  const { data } = await api.post<BuildingRow[]>('/booking/floors/reorder',
    { ids }, { params: { property_id: propertyId } })
  return data
}

/* ---------------- Room photos (screen 059) ---------------- */
export async function uploadRoomPhoto(
  propertyId: string,
  roomId: string,
  file: File,
  makePrimary = false,
): Promise<RoomDetail> {
  const form = new FormData()
  form.append('file', file)
  form.append('make_primary', String(makePrimary))
  const { data } = await api.post<RoomDetail>(
    `/booking/rooms/${roomId}/photos`, form,
    { params: { property_id: propertyId }, headers: { 'Content-Type': undefined } },
  )
  return data
}

export async function setRoomPrimaryPhoto(
  propertyId: string, roomId: string, photoId: string,
): Promise<RoomDetail> {
  const { data } = await api.post<RoomDetail>(
    `/booking/rooms/${roomId}/photos/${photoId}/primary`, {},
    { params: { property_id: propertyId } },
  )
  return data
}

export async function deleteRoomPhoto(
  propertyId: string, roomId: string, photoId: string,
): Promise<RoomDetail> {
  const { data } = await api.delete<RoomDetail>(
    `/booking/rooms/${roomId}/photos/${photoId}`,
    { params: { property_id: propertyId } },
  )
  return data
}

/* ---------------- Room Block & Out of Order (screen 063) ---------------- */
export interface BlockRoom {
  id: string
  room_id: string
  room_code: string
  room_type_name: string
  status: string
  version: number
}

export interface BlockGroup {
  group_id: string
  rooms_label: string
  room_type_name: string
  block_type: string
  block_type_label: string
  reason_category: string
  reason_category_label: string
  reason: string | null
  severity: string
  start_date: string
  end_date: string
  status: string
  linked_reference: string | null
  created_by_name: string | null
  created_at: string | null
  rooms: BlockRoom[]
}

export interface BlockStats {
  currently_blocked: number
  out_of_order: number
  due_to_end_today: number
  total_unavailable: number
}

export interface BlockConflict {
  room_id: string
  room_code: string
  detail: string
}

export interface BlockCreateResult {
  group_id: string | null
  created: BlockRoom[]
  conflicts: BlockConflict[]
}

export interface BlockableRoom {
  id: string
  code: string
  room_type_name: string
  floor: string | null
}

/** Reason categories exactly as screen 063 labels them. */
export const BLOCK_REASONS: { code: string; label: string }[] = [
  { code: 'maintenance_scheduled', label: 'Maintenance - Scheduled' },
  { code: 'maintenance_emergency', label: 'Maintenance - Emergency' },
  { code: 'deep_cleaning', label: 'Deep Cleaning' },
  { code: 'renovation', label: 'Renovation' },
  { code: 'pest_control', label: 'Pest Control' },
  { code: 'vip_hold', label: 'VIP Hold' },
  { code: 'group_hold', label: 'Group Hold' },
  { code: 'damage', label: 'Damage' },
  { code: 'other', label: 'Other' },
]

export interface BlockFilters {
  status?: string
  block_type?: string
  room_type_id?: string
  start_date?: string
  end_date?: string
  search?: string
}

export async function listBlocks(
  propertyId: string,
  f: BlockFilters = {},
): Promise<BlockGroup[]> {
  const { data } = await api.get<BlockGroup[]>('/booking/room-blocks', {
    params: { property_id: propertyId, ...f },
  })
  return data
}

export async function getBlockStats(propertyId: string): Promise<BlockStats> {
  const { data } = await api.get<BlockStats>('/booking/room-blocks/stats', {
    params: { property_id: propertyId },
  })
  return data
}

export async function getBlockableRooms(propertyId: string): Promise<BlockableRoom[]> {
  const { data } = await api.get<BlockableRoom[]>('/booking/room-blocks/rooms', {
    params: { property_id: propertyId },
  })
  return data
}

export async function createBlock(
  propertyId: string,
  body: Record<string, unknown>,
): Promise<BlockCreateResult> {
  const { data } = await api.post<BlockCreateResult>('/booking/room-blocks', body, {
    params: { property_id: propertyId },
  })
  return data
}

export async function endBlock(
  propertyId: string,
  groupId: string,
  body: { end_date?: string; reason?: string },
): Promise<BlockGroup[]> {
  const { data } = await api.post<BlockGroup[]>(
    `/booking/room-blocks/${groupId}/end`, body,
    { params: { property_id: propertyId } },
  )
  return data
}

export async function cancelBlock(
  propertyId: string,
  groupId: string,
  body: { reason?: string } = {},
): Promise<BlockGroup[]> {
  const { data } = await api.post<BlockGroup[]>(
    `/booking/room-blocks/${groupId}/cancel`, body,
    { params: { property_id: propertyId } },
  )
  return data
}

export async function modifyBlock(
  propertyId: string,
  groupId: string,
  body: Record<string, unknown> & { version: number },
): Promise<BlockCreateResult> {
  const { data } = await api.put<BlockCreateResult>(
    `/booking/room-blocks/${groupId}`, body,
    { params: { property_id: propertyId } },
  )
  return data
}

/* ---------------- Room Status History (screen 064) ---------------- */
export interface StatusEvent {
  id: string
  status: string
  status_label: string
  source: string
  source_label: string
  source_reference: string | null
  remarks: string | null
  changed_by_name: string | null
  changed_by_role: string | null
  occurred_at: string
}

export interface LinkedRecord {
  kind: string
  reference: string
  detail: string | null
}

export interface RoomStatusSummary {
  room_id: string
  room_code: string
  room_type_name: string
  floor: string | null
  max_occupancy: number
  current_status: string
  current_status_label: string
  since: string | null
  current_guest: string | null
  reservation_number: string | null
  reservation_state: string | null
  total_changes: number
  created_at: string | null
  last_updated_at: string | null
  primary_photo_url: string | null
  linked_records: LinkedRecord[]
}

export interface StatusHistoryFilters {
  from?: string
  to?: string
  status?: string
  source?: string
  // Filtered on the client by display name: the timeline already carries who
  // made each change, so there is no need for a second round trip by user id.
  user?: string
}

export async function getRoomStatusHistory(
  propertyId: string,
  roomId: string,
  filters: StatusHistoryFilters = {},
): Promise<StatusEvent[]> {
  const { user: _user, ...serverFilters } = filters
  const { data } = await api.get<StatusEvent[]>(
    `/booking/rooms/${roomId}/status-history`,
    { params: { property_id: propertyId, ...serverFilters } },
  )
  return data
}

export async function getRoomStatusSummary(
  propertyId: string,
  roomId: string,
): Promise<RoomStatusSummary> {
  const { data } = await api.get<RoomStatusSummary>(
    `/booking/rooms/${roomId}/status-summary`,
    { params: { property_id: propertyId } },
  )
  return data
}

// The one write on this screen: history itself is read-only, but housekeeping
// can move the room's current state, and that becomes a new event.
export async function setHousekeepingStatus(
  propertyId: string,
  roomId: string,
  body: { status: string; remarks?: string },
): Promise<RoomStatusSummary> {
  const { data } = await api.post<RoomStatusSummary>(
    `/booking/rooms/${roomId}/housekeeping-status`, body,
    { params: { property_id: propertyId } },
  )
  return data
}

/* ---------------- Room Details (screen 011) ---------------- */
export interface UpcomingBooking {
  reservation_id: string
  reservation_number: string
  guest_name: string | null
  arrival_date: string
  departure_date: string
  nights: number
  adults: number
  children: number
  status: string
  status_label: string
  in_house: boolean
}

export interface RoomOverview {
  id: string
  code: string
  room_type_id: string
  room_type_name: string
  building_name: string | null
  floor_name: string | null
  bed_setup: string | null
  view_type: string | null
  size_sqft: number | null
  max_adults: number
  max_children: number
  base_rate: string | null
  housekeeping_zone: string | null
  accessibility: string
  near_elevator: boolean
  notes: string | null
  status: string
  service_status: string
  occupancy_state: string
  occupancy_label: string
  housekeeping_state: string
  housekeeping_label: string
  version: number
  tags: string[]
  photos: RoomPhoto[]
  amenities: RoomAmenity[]
  upcoming_booking: UpcomingBooking | null
  active_block_group_id: string | null
}

export interface RoomReservation {
  reservation_unit_id: string
  reservation_id: string
  reservation_number: string
  guest_name: string | null
  arrival_date: string
  departure_date: string
  nights: number
  adults: number
  children: number
  status: string
  status_label: string
}

export interface RoomMaintenance {
  group_id: string
  block_type: string
  block_type_label: string
  reason_category: string
  reason_category_label: string
  reason: string | null
  severity: string
  start_date: string
  end_date: string
  status: string
  linked_reference: string | null
  created_by_name: string | null
  created_at: string | null
}

export async function getRoomOverview(
  propertyId: string, roomId: string,
): Promise<RoomOverview> {
  const { data } = await api.get<RoomOverview>(`/booking/rooms/${roomId}/overview`, {
    params: { property_id: propertyId },
  })
  return data
}

export async function getRoomReservations(
  propertyId: string, roomId: string,
): Promise<RoomReservation[]> {
  const { data } = await api.get<RoomReservation[]>(
    `/booking/rooms/${roomId}/reservations`, { params: { property_id: propertyId } },
  )
  return data
}

export async function getRoomMaintenance(
  propertyId: string, roomId: string,
): Promise<RoomMaintenance[]> {
  const { data } = await api.get<RoomMaintenance[]>(
    `/booking/rooms/${roomId}/maintenance`, { params: { property_id: propertyId } },
  )
  return data
}

/* ---------------- Rate Plans (screen 033) ---------------- */
export interface MealPlan {
  id: string
  code: string
  name: string
  description: string | null
  display_order: number
  status: string
  rate_plan_count: number
  version: number
}

export interface RatePlan {
  id: string
  code: string
  name: string
  description: string | null
  plan_type: string
  plan_type_label: string
  corporate_account: string | null
  /** The company this rate was negotiated with. An id, so a booking billed to
   *  that account can find its rate — the free-text name it replaced could
   *  not be matched against anything. */
  commercial_account_id: string | null
  commercial_account_name: string | null
  meal_plan_id: string | null
  meal_plan_code: string | null
  meal_plan_name: string | null
  adjustment_direction: string
  adjustment_type: string
  adjustment_value: string
  flat_rate: string | null
  extra_adult_charge: string | null
  child_charge: string | null
  min_stay: number
  min_guests: number
  max_guests: number
  refundable: boolean
  free_cancellation_hours: number | null
  cancellation_policy: string | null
  cancellation_label: string
  status: string
  image_url: string | null
  from_rate: string | null
  room_type_ids: string[]
  applies_to_all_room_types: boolean
  version: number
  created_at: string | null
  updated_at: string | null
  created_by_name: string | null
  updated_by_name: string | null
}

export interface RatePlanStats {
  total: number
  active: number
  inactive: number
  corporate: number
}

export interface RatePlanFilters {
  search?: string
  status?: string
  meal_plan_id?: string
  room_type_id?: string
  plan_type?: string
}

export const ADJUSTMENT_DIRECTIONS = [
  { code: 'increase', label: 'Increase by' },
  { code: 'decrease', label: 'Decrease by' },
]

export const CANCELLATION_POLICIES = [
  'Fully refundable until 24 hours before arrival.',
  'Fully refundable until 48 hours before arrival.',
  'Fully refundable until 7 days before arrival.',
  'One night charged on cancellation.',
  'No refund once booked.',
]

export async function listMealPlans(propertyId: string): Promise<MealPlan[]> {
  const { data } = await api.get<MealPlan[]>('/booking/meal-plans', {
    params: { property_id: propertyId },
  })
  return data
}

export async function createMealPlan(
  propertyId: string, body: Record<string, unknown>,
): Promise<MealPlan> {
  const { data } = await api.post<MealPlan>('/booking/meal-plans', body, {
    params: { property_id: propertyId },
  })
  return data
}

export async function updateMealPlan(
  propertyId: string, mealPlanId: string, body: Record<string, unknown>,
): Promise<MealPlan> {
  const { data } = await api.put<MealPlan>(`/booking/meal-plans/${mealPlanId}`, body, {
    params: { property_id: propertyId },
  })
  return data
}

export async function listRatePlans(
  propertyId: string, filters: RatePlanFilters = {},
): Promise<{ items: RatePlan[]; stats: RatePlanStats }> {
  const { data } = await api.get<{ items: RatePlan[]; stats: RatePlanStats }>(
    '/booking/rate-plans', { params: { property_id: propertyId, ...filters } },
  )
  return data
}

export async function createRatePlan(
  propertyId: string, body: Record<string, unknown>,
): Promise<RatePlan> {
  const { data } = await api.post<RatePlan>('/booking/rate-plans', body, {
    params: { property_id: propertyId },
  })
  return data
}

export async function updateRatePlan(
  propertyId: string, planId: string, body: Record<string, unknown>,
): Promise<RatePlan> {
  const { data } = await api.put<RatePlan>(`/booking/rate-plans/${planId}`, body, {
    params: { property_id: propertyId },
  })
  return data
}

export async function setRatePlanStatus(
  propertyId: string, planId: string,
  body: { status: string; version: number; reason?: string },
): Promise<RatePlan> {
  const { data } = await api.post<RatePlan>(
    `/booking/rate-plans/${planId}/status`, body, { params: { property_id: propertyId } },
  )
  return data
}

export async function duplicateRatePlan(
  propertyId: string, planId: string,
): Promise<RatePlan> {
  const { data } = await api.post<RatePlan>(
    `/booking/rate-plans/${planId}/duplicate`, {}, { params: { property_id: propertyId } },
  )
  return data
}

export async function uploadRatePlanImage(
  propertyId: string, planId: string, file: File,
): Promise<RatePlan> {
  const form = new FormData()
  form.append('file', file)
  const { data } = await api.post<RatePlan>(
    `/booking/rate-plans/${planId}/image`, form,
    { params: { property_id: propertyId },
      headers: { 'Content-Type': 'multipart/form-data' } },
  )
  return data
}

export async function deleteRatePlanImage(
  propertyId: string, planId: string,
): Promise<RatePlan> {
  const { data } = await api.delete<RatePlan>(
    `/booking/rate-plans/${planId}/image`, { params: { property_id: propertyId } },
  )
  return data
}

/* ---------------- Rates & Inventory Calendar (screen 034) ---------------- */
export interface CalendarCell {
  stay_date: string
  rate: string | null
  rate_is_override: boolean
  min_stay: number
  min_stay_is_override: boolean
  stop_sell: boolean
  capacity: number
  out_of_service: number
  reserved: number
  available: number
}

export interface CalendarRow {
  room_type_id: string
  room_type_name: string
  units: number
  base_rate: string | null
  photo_url: string | null
  days: CalendarCell[]
}

export interface RateCalendar {
  date_from: string
  date_to: string
  dates: string[]
  rows: CalendarRow[]
  forecast: { stay_date: string; occupancy_pct: number }[]
  rate_plan_id: string | null
  rate_plan_name: string | null
  /** False while viewing a rate plan: those prices are derived, not editable. */
  editable: boolean
}

export interface BulkPreviewRow {
  room_type_id: string
  room_type_name: string
  current_avg: string | null
  new_avg: string | null
}

export interface BulkUpdateResult {
  rows: BulkPreviewRow[]
  room_type_count: number
  date_count: number
  cell_count: number
  applied: boolean
}

export interface CopyRatesResult {
  days: number
  room_type_count: number
  cell_count: number
  applied: boolean
  target_to: string
}

export async function getRateCalendar(
  propertyId: string, from: string, to: string,
  roomTypeId?: string, ratePlanId?: string,
): Promise<RateCalendar> {
  const { data } = await api.get<RateCalendar>('/booking/rate-calendar', {
    params: {
      property_id: propertyId, from, to,
      room_type_id: roomTypeId, rate_plan_id: ratePlanId,
    },
  })
  return data
}

export async function updateCalendarCell(
  propertyId: string,
  body: {
    room_type_id: string; stay_date: string
    rate?: number | null; clear_rate?: boolean
    min_stay?: number | null; clear_min_stay?: boolean
    stop_sell?: boolean
  },
): Promise<CalendarCell> {
  const { data } = await api.put<CalendarCell>('/booking/rate-calendar/cell', body, {
    params: { property_id: propertyId },
  })
  return data
}

/** One night of a rate plan's own calendar: effective values, and which
 *  of them the plan set for itself. */
export interface PlanNight {
  stay_date: string
  rate: string | null
  min_stay: number
  max_stay: number
  closed_to_arrival: boolean
  closed_to_departure: boolean
  stop_sell: boolean
  overridden: string[]
}

export interface PlanCalendar {
  rate_plan_id: string
  rate_plan_name: string
  room_type_id: string | null
  room_type_name: string | null
  nights: PlanNight[]
}

export async function getRatePlanCalendar(
  propertyId: string, ratePlanId: string, from: string, to: string,
): Promise<PlanCalendar> {
  const { data } = await api.get<PlanCalendar>('/booking/rate-plan-calendar', {
    params: { property_id: propertyId, rate_plan_id: ratePlanId, date_from: from, date_to: to },
  })
  return data
}

export interface PlanChange {
  rate_plan_id: string
  date_from: string
  date_to: string
  weekdays?: number[]
  rate?: number | null
  min_stay?: number | null
  max_stay?: number | null
  closed_to_arrival?: boolean | null
  closed_to_departure?: boolean | null
  stop_sell?: boolean | null
  clear?: string[]
}

export async function setRatePlanCalendar(
  propertyId: string, changes: PlanChange[],
): Promise<{ nights_changed: number }> {
  const { data } = await api.put<{ nights_changed: number }>(
    '/booking/rate-plan-calendar', { changes }, { params: { property_id: propertyId } })
  return data
}

export async function bulkUpdateRates(
  propertyId: string, body: Record<string, unknown>,
): Promise<BulkUpdateResult> {
  const { data } = await api.post<BulkUpdateResult>('/booking/rate-calendar/bulk', body, {
    params: { property_id: propertyId },
  })
  return data
}

export async function copyRates(
  propertyId: string, body: Record<string, unknown>,
): Promise<CopyRatesResult> {
  const { data } = await api.post<CopyRatesResult>('/booking/rate-calendar/copy', body, {
    params: { property_id: propertyId },
  })
  return data
}

/* ---------------- Packages & Promotions (screen 035) ---------------- */
export interface Addon {
  id: string
  code: string
  name: string
  description: string | null
  category: string
  category_label: string
  price: string
  pricing_unit: string
  pricing_unit_label: string
  status: string
  used_in_packages: number
  version: number
}

export interface PackageInclusion {
  id: string
  label: string
  addon_id: string | null
  addon_name: string | null
  sort_order: number
}

export interface ResortPackage {
  id: string
  code: string
  name: string
  tagline: string | null
  description: string | null
  badge: string | null
  valid_from: string
  valid_to: string
  adjustment_direction: string
  adjustment_type: string
  adjustment_value: string
  flat_rate: string | null
  min_nights: number
  max_nights: number | null
  is_featured: boolean
  status: string
  image_url: string | null
  from_rate: string | null
  bookings: number
  revenue: string
  room_type_ids: string[]
  applies_to_all_room_types: boolean
  inclusions: PackageInclusion[]
  version: number
  created_at: string | null
  updated_at: string | null
}

export interface PromoCode {
  id: string
  code: string
  description: string | null
  discount_type: string
  discount_value: string
  value_label: string
  usage_limit: number | null
  usage_count: number
  valid_from: string
  valid_to: string
  min_nights: number | null
  package_id: string | null
  package_name: string | null
  status: string
  state: string
  state_label: string
  version: number
}

export interface PromoRedemption {
  id: string
  promo_code_id: string
  code: string
  guest_name: string | null
  reservation_id: string | null
  reservation_number: string | null
  discount_amount: string | null
  redeemed_by_name: string | null
  redeemed_at: string
}

export interface PromotionStats {
  packages: number
  active_packages: number
  promo_codes: number
  active_promo_codes: number
  redemptions: number
}

export const ADDON_CATEGORIES = [
  { code: 'dining', label: 'Dining' },
  { code: 'spa', label: 'Spa & Wellness' },
  { code: 'transport', label: 'Transport' },
  { code: 'activity', label: 'Activities' },
  { code: 'room_service', label: 'Room Service' },
  { code: 'other', label: 'Other' },
]

export const PRICING_UNITS = [
  { code: 'per_stay', label: 'Per stay' },
  { code: 'per_night', label: 'Per night' },
  { code: 'per_person', label: 'Per person' },
  { code: 'per_person_per_night', label: 'Per person, per night' },
]

export async function listAddons(propertyId: string): Promise<Addon[]> {
  const { data } = await api.get<Addon[]>('/booking/addons', {
    params: { property_id: propertyId },
  })
  return data
}

export async function createAddon(
  propertyId: string, body: Record<string, unknown>,
): Promise<Addon> {
  const { data } = await api.post<Addon>('/booking/addons', body, {
    params: { property_id: propertyId },
  })
  return data
}

export async function updateAddon(
  propertyId: string, addonId: string, body: Record<string, unknown>,
): Promise<Addon> {
  const { data } = await api.put<Addon>(`/booking/addons/${addonId}`, body, {
    params: { property_id: propertyId },
  })
  return data
}

export async function listPackages(
  propertyId: string, filters: { search?: string; status?: string } = {},
): Promise<ResortPackage[]> {
  const { data } = await api.get<ResortPackage[]>('/booking/packages', {
    params: { property_id: propertyId, ...filters },
  })
  return data
}

export async function createPackage(
  propertyId: string, body: Record<string, unknown>,
): Promise<ResortPackage> {
  const { data } = await api.post<ResortPackage>('/booking/packages', body, {
    params: { property_id: propertyId },
  })
  return data
}

export async function updatePackage(
  propertyId: string, packageId: string, body: Record<string, unknown>,
): Promise<ResortPackage> {
  const { data } = await api.put<ResortPackage>(`/booking/packages/${packageId}`, body, {
    params: { property_id: propertyId },
  })
  return data
}

export async function setPackageStatus(
  propertyId: string, packageId: string,
  body: { status: string; version: number; reason?: string },
): Promise<ResortPackage> {
  const { data } = await api.post<ResortPackage>(
    `/booking/packages/${packageId}/status`, body, { params: { property_id: propertyId } },
  )
  return data
}

export async function uploadPackageImage(
  propertyId: string, packageId: string, file: File,
): Promise<ResortPackage> {
  const form = new FormData()
  form.append('file', file)
  const { data } = await api.post<ResortPackage>(
    `/booking/packages/${packageId}/image`, form,
    { params: { property_id: propertyId },
      headers: { 'Content-Type': 'multipart/form-data' } },
  )
  return data
}

export async function deletePackageImage(
  propertyId: string, packageId: string,
): Promise<ResortPackage> {
  const { data } = await api.delete<ResortPackage>(
    `/booking/packages/${packageId}/image`, { params: { property_id: propertyId } },
  )
  return data
}

export async function listPromoCodes(
  propertyId: string, filters: { search?: string; status?: string } = {},
): Promise<PromoCode[]> {
  const { data } = await api.get<PromoCode[]>('/booking/promo-codes', {
    params: { property_id: propertyId, ...filters },
  })
  return data
}

export async function createPromoCode(
  propertyId: string, body: Record<string, unknown>,
): Promise<PromoCode> {
  const { data } = await api.post<PromoCode>('/booking/promo-codes', body, {
    params: { property_id: propertyId },
  })
  return data
}

export async function updatePromoCode(
  propertyId: string, promoId: string, body: Record<string, unknown>,
): Promise<PromoCode> {
  const { data } = await api.put<PromoCode>(`/booking/promo-codes/${promoId}`, body, {
    params: { property_id: propertyId },
  })
  return data
}

export async function redeemPromoCode(
  propertyId: string, promoId: string,
  body: { guest_name?: string; discount_amount?: number; reservation_id?: string },
): Promise<PromoCode> {
  const { data } = await api.post<PromoCode>(
    `/booking/promo-codes/${promoId}/redeem`, body,
    { params: { property_id: propertyId } },
  )
  return data
}

export async function listRedemptions(
  propertyId: string, promoCodeId?: string,
): Promise<PromoRedemption[]> {
  const { data } = await api.get<PromoRedemption[]>('/booking/promo-redemptions', {
    params: { property_id: propertyId, promo_code_id: promoCodeId },
  })
  return data
}

export async function getPromotionStats(propertyId: string): Promise<PromotionStats> {
  const { data } = await api.get<PromotionStats>('/booking/promotions/stats', {
    params: { property_id: propertyId },
  })
  return data
}

/* ---------------- Tax and Service Charge Setup (screen 118) ---------------- */
export interface TaxComponent {
  code: string
  rate: string | number
}

export interface TaxCharge {
  id: string
  code: string
  name: string
  revision: number
  charge_type: string
  charge_type_label: string
  rate_type: string
  rate_value: string
  amount_basis: string | null
  rate_label: string
  apply_as: string
  applicability: string[]
  applicability_label: string
  income_account: string | null
  parent_group_id: string | null
  parent_group_code: string | null
  remarks: string | null
  effective_from: string
  effective_to: string | null
  status: string
  is_default: boolean
  components: TaxComponent[]
  scheduled_count: number
  scheduled_from: string | null
  version: number
  created_at: string | null
  updated_at: string | null
  updated_by_name: string | null
}

export interface TaxStats {
  tax_groups: number
  tax_groups_inactive: number
  gst_components: number
  gst_component_codes: string[]
  service_charges: number
  scheduled_changes: number
  other_taxes: number
  other_tax_names: string[]
  total: number
}

export interface TaxChargeFilters {
  search?: string
  charge_type?: string
  status?: string
  applicability?: string
  limit?: number
  offset?: number
}

export const TAX_CHARGE_TYPES = [
  { code: 'tax_group', label: 'Tax Group' },
  { code: 'gst_component', label: 'GST Component' },
  { code: 'service_charge', label: 'Service Charge' },
  { code: 'other_tax', label: 'Other Tax' },
]

export const TAX_APPLICABILITY = [
  { code: 'rooms', label: 'Rooms' },
  { code: 'spa', label: 'Spa' },
  { code: 'fnb_outlets', label: 'F&B Outlets' },
  { code: 'banquets_events', label: 'Banquets & Events' },
  { code: 'in_room_dining', label: 'In-room Dining' },
  { code: 'other_services', label: 'Other Services' },
]

export const TAX_AMOUNT_BASES = [
  { code: 'per_night', label: 'Per night' },
  { code: 'per_person', label: 'Per person' },
  { code: 'per_stay', label: 'Per stay' },
  { code: 'per_unit', label: 'Per unit' },
]

export async function listTaxCharges(
  propertyId: string, filters: TaxChargeFilters = {},
): Promise<{ items: TaxCharge[]; total: number; stats: TaxStats }> {
  const { data } = await api.get<{ items: TaxCharge[]; total: number; stats: TaxStats }>(
    '/finance/tax-charges', { params: { property_id: propertyId, ...filters } },
  )
  return data
}

export async function listTaxRevisions(
  propertyId: string, code: string,
): Promise<TaxCharge[]> {
  const { data } = await api.get<TaxCharge[]>(
    `/finance/tax-charges/${code}/revisions`, { params: { property_id: propertyId } },
  )
  return data
}

export async function createTaxCharge(
  propertyId: string, body: Record<string, unknown>,
): Promise<TaxCharge> {
  const { data } = await api.post<TaxCharge>('/finance/tax-charges', body, {
    params: { property_id: propertyId },
  })
  return data
}

export async function updateTaxCharge(
  propertyId: string, ruleId: string, body: Record<string, unknown>,
): Promise<TaxCharge> {
  const { data } = await api.put<TaxCharge>(`/finance/tax-charges/${ruleId}`, body, {
    params: { property_id: propertyId },
  })
  return data
}

export async function scheduleTaxChange(
  propertyId: string, ruleId: string, body: Record<string, unknown>,
): Promise<TaxCharge> {
  const { data } = await api.post<TaxCharge>(
    `/finance/tax-charges/${ruleId}/schedule`, body,
    { params: { property_id: propertyId } },
  )
  return data
}

export async function cloneTaxCharge(
  propertyId: string, ruleId: string,
): Promise<TaxCharge> {
  const { data } = await api.post<TaxCharge>(
    `/finance/tax-charges/${ruleId}/clone`, {},
    { params: { property_id: propertyId } },
  )
  return data
}

export async function setTaxChargeStatus(
  propertyId: string, ruleId: string,
  body: { status: string; version: number; reason?: string },
): Promise<TaxCharge> {
  const { data } = await api.post<TaxCharge>(
    `/finance/tax-charges/${ruleId}/status`, body,
    { params: { property_id: propertyId } },
  )
  return data
}

/* ---------------- Rate Rules (screen 119) ---------------- */
export interface RateRule {
  id: string
  name: string
  description: string | null
  rule_type: string
  rule_type_label: string
  status: string
  status_label: string
  priority: number
  applicable_for: string
  date_from: string
  date_to: string
  weekdays: number[]
  weekdays_label: string
  adjustment_direction: string
  adjustment_type: string
  adjustment_value: string
  adjustment_label: string
  fixed_rate: string | null
  min_stay: number | null
  max_stay: number | null
  advance_days_min: number | null
  advance_days_max: number | null
  /** Apply only while the date is this full, as a percent of sellable rooms.
   *  min alone is the surcharge on a filling date; max alone is the discount
   *  on an empty one. */
  occupancy_min: number | null
  occupancy_max: number | null
  closed_to_arrival: boolean
  closed_to_departure: boolean
  stop_sell: boolean
  channel_scope: string
  room_type_ids: string[]
  rate_plan_ids: string[]
  applies_to_label: string
  rate_plan_label: string
  restrictions_label: string
  conflict_count: number
  version: number
  created_at: string | null
  updated_at: string | null
  updated_by_name: string | null
}

export interface RateRuleStats {
  total: number
  published: number
  scheduled: number
  draft: number
  paused: number
  inactive: number
  conflicts: number
}

export interface RuleConflict {
  rule_id: string
  rule_name: string
  other_id: string
  other_name: string
  priority: number
  overlap_from: string
  overlap_to: string
  shared_weekdays: number[]
  detail: string
}

export interface SimulateResult {
  stay_date: string
  base_rate: string | null
  final_rate: string | null
  stop_sell: boolean
  steps: { label: string; detail: string; amount: string | null }[]
  applied_rule_ids: string[]
  skipped: string[]
}

export const RULE_STATUSES = [
  { code: 'draft', label: 'Draft' },
  { code: 'scheduled', label: 'Scheduled' },
  { code: 'published', label: 'Published' },
  { code: 'paused', label: 'Paused' },
  { code: 'inactive', label: 'Inactive' },
]

export const RULE_TYPES = [
  { code: 'derived_adjustment', label: 'Derived Rate Adjustment' },
  { code: 'fixed_rate', label: 'Fixed Rate Override' },
]

export const WEEKDAYS = [
  { n: 1, label: 'Mon' }, { n: 2, label: 'Tue' }, { n: 3, label: 'Wed' },
  { n: 4, label: 'Thu' }, { n: 5, label: 'Fri' }, { n: 6, label: 'Sat' },
  { n: 7, label: 'Sun' },
]

export async function listRateRules(
  propertyId: string,
  filters: { search?: string; status?: string; rate_plan_id?: string;
             room_type_id?: string; month?: string } = {},
): Promise<{ items: RateRule[]; total: number; stats: RateRuleStats }> {
  const { data } = await api.get<{ items: RateRule[]; total: number; stats: RateRuleStats }>(
    '/booking/rate-rules', { params: { property_id: propertyId, ...filters } },
  )
  return data
}

export async function listRuleConflicts(propertyId: string): Promise<RuleConflict[]> {
  const { data } = await api.get<RuleConflict[]>('/booking/rate-rules/conflicts', {
    params: { property_id: propertyId },
  })
  return data
}

export async function createRateRule(
  propertyId: string, body: Record<string, unknown>,
): Promise<RateRule> {
  const { data } = await api.post<RateRule>('/booking/rate-rules', body, {
    params: { property_id: propertyId },
  })
  return data
}

export async function updateRateRule(
  propertyId: string, ruleId: string, body: Record<string, unknown>,
): Promise<RateRule> {
  const { data } = await api.put<RateRule>(`/booking/rate-rules/${ruleId}`, body, {
    params: { property_id: propertyId },
  })
  return data
}

export async function setRateRuleStatus(
  propertyId: string, ruleId: string,
  body: { status: string; version: number; reason?: string },
): Promise<RateRule> {
  const { data } = await api.post<RateRule>(
    `/booking/rate-rules/${ruleId}/status`, body,
    { params: { property_id: propertyId } },
  )
  return data
}

export async function duplicateRateRule(
  propertyId: string, ruleId: string,
): Promise<RateRule> {
  const { data } = await api.post<RateRule>(
    `/booking/rate-rules/${ruleId}/duplicate`, {},
    { params: { property_id: propertyId } },
  )
  return data
}

export async function simulateRate(
  propertyId: string,
  body: { stay_date: string; room_type_id: string; rate_plan_id?: string;
          nights?: number; lead_days?: number },
): Promise<SimulateResult> {
  const { data } = await api.post<SimulateResult>(
    '/booking/rate-rules/simulate', body, { params: { property_id: propertyId } },
  )
  return data
}

export async function getRateRule(
  propertyId: string, ruleId: string,
): Promise<RateRule> {
  const { data } = await api.get<RateRule>(`/booking/rate-rules/${ruleId}`, {
    params: { property_id: propertyId },
  })
  return data
}

/* ---------------- Deposit Schedule (screen 058) ---------------- */
export interface DepositAllocation {
  id: string
  amount: number
  method: string
  reference: string | null
  received_on: string
  reason: string | null
  is_reversal: boolean
  reversed: boolean
}
export interface DepositInstallment {
  id: string
  seq: number
  label: string
  amount: number
  percent: number | null
  due_rule: string
  due_date: string | null
  due_label: string
  status: string
  status_label: string
  paid_amount: number
  waived_amount: number
  balance_due: number
  received_on: string | null
  payment_method: string | null
  waiver_status: string
  waiver_reason: string | null
  reminder_state: string
  reminder_sent_at: string | null
  reminder_due_on: string | null
  reminder_label: string
  notes: string | null
  version: number
  allocations: DepositAllocation[]
}
export interface DepositTotals {
  booking_value: number
  deposit_required: number
  deposit_percent: number | null
  received: number
  received_percent: number | null
  waived: number
  balance_due: number
  next_due_date: string | null
  next_due_in_days: number | null
}
export interface DepositSchedule {
  reservation_id: string
  organization_id: string | null
  property_id: string | null
  folio_id: string | null
  currency: string
  booking_value: number
  installments: DepositInstallment[]
  totals: DepositTotals
  waiver_threshold: number
  can_approve_waiver: boolean
}
export interface InstallmentInput {
  label: string
  amount?: number | null
  percent?: number | null
  due_rule: string
  due_date?: string | null
  notes?: string | null
  reminder_due_on?: string | null
}

// The due rules the schedule understands. 'at_booking' and 'at_checkin' are
// tied to the reservation; only 'fixed_date' needs a date typed in.
export const DUE_RULES = [
  { code: 'at_booking', label: 'At time of booking' },
  { code: 'fixed_date', label: 'On a fixed date' },
  { code: 'at_checkin', label: 'At check-in' },
]

export async function getDepositSchedule(
  reservationId: string, propertyId: string, bookingValue: number,
  includeCancelled = false,
): Promise<DepositSchedule> {
  const { data } = await api.get<DepositSchedule>(
    `/finance/reservations/${reservationId}/deposit-schedule`,
    { params: { property_id: propertyId, booking_value: bookingValue,
                include_cancelled: includeCancelled } },
  )
  return data
}

export async function generateDepositSchedule(
  reservationId: string,
  params: { propertyId: string; organizationId: string },
  body: { booking_value: number; folio_id?: string | null;
          arrival_date?: string | null; splits: InstallmentInput[] },
): Promise<DepositSchedule> {
  const { data } = await api.post<DepositSchedule>(
    `/finance/reservations/${reservationId}/deposit-schedule`, body,
    { params: { property_id: params.propertyId,
                organization_id: params.organizationId } },
  )
  return data
}

export async function addDepositInstallment(
  reservationId: string,
  params: { propertyId: string; organizationId: string; bookingValue: number;
            folioId?: string | null },
  body: InstallmentInput,
): Promise<DepositInstallment> {
  const { data } = await api.post<DepositInstallment>(
    `/finance/reservations/${reservationId}/deposit-schedule/installments`, body,
    { params: { property_id: params.propertyId,
                organization_id: params.organizationId,
                booking_value: params.bookingValue,
                folio_id: params.folioId ?? undefined } },
  )
  return data
}

export async function updateDepositInstallment(
  installmentId: string, propertyId: string, bookingValue: number,
  body: InstallmentInput & { version: number },
): Promise<DepositInstallment> {
  const { data } = await api.put<DepositInstallment>(
    `/finance/deposit-installments/${installmentId}`, body,
    { params: { property_id: propertyId, booking_value: bookingValue } },
  )
  return data
}

export async function cancelDepositInstallment(
  installmentId: string, propertyId: string, version: number,
): Promise<DepositInstallment> {
  const { data } = await api.post<DepositInstallment>(
    `/finance/deposit-installments/${installmentId}/cancel`, {},
    { params: { property_id: propertyId, version } },
  )
  return data
}

export async function collectDeposit(
  installmentId: string, propertyId: string, folioId: string | null,
  body: { amount: number; method: string; received_on?: string;
          reference?: string },
): Promise<DepositInstallment> {
  const { data } = await api.post<DepositInstallment>(
    `/finance/deposit-installments/${installmentId}/collect`, body,
    { params: { property_id: propertyId, folio_id: folioId ?? undefined } },
  )
  return data
}

export async function refundDeposit(
  installmentId: string, propertyId: string,
  body: { allocation_id: string; amount: number; reason: string },
): Promise<DepositInstallment> {
  const { data } = await api.post<DepositInstallment>(
    `/finance/deposit-installments/${installmentId}/refund`, body,
    { params: { property_id: propertyId } },
  )
  return data
}

export async function waiveDeposit(
  installmentId: string, propertyId: string,
  body: { amount: number; reason: string },
): Promise<DepositInstallment> {
  const { data } = await api.post<DepositInstallment>(
    `/finance/deposit-installments/${installmentId}/waive`, body,
    { params: { property_id: propertyId } },
  )
  return data
}

export async function decideDepositWaiver(
  installmentId: string, propertyId: string,
  body: { decision: 'approved' | 'rejected'; reason?: string },
): Promise<DepositInstallment> {
  const { data } = await api.post<DepositInstallment>(
    `/finance/deposit-installments/${installmentId}/waiver-decision`, body,
    { params: { property_id: propertyId } },
  )
  return data
}

export async function setDepositReminder(
  installmentId: string, propertyId: string,
  body: { action: 'schedule' | 'mark_sent' | 'clear'; due_on?: string },
): Promise<DepositInstallment> {
  const { data } = await api.post<DepositInstallment>(
    `/finance/deposit-installments/${installmentId}/reminder`, body,
    { params: { property_id: propertyId } },
  )
  return data
}

/* ---------------- Reservation Calendar / room rack (screen 003) ---------------- */
export interface RackRoom {
  id: string
  code: string
  floor: string | null
  /** A price set on this room specifically. Null when it charges its type's
   *  rate, which is the usual case. */
  base_rate: string | null
}
export interface RackBar {
  id: string
  room_id: string | null
  room_type_id: string | null
  reservation_unit_id: string | null
  reservation_id: string | null
  number: string | null
  guest_name: string | null
  label: string
  status: string
  status_label: string
  start_date: string
  end_date: string
  nights: number
  adults: number | null
  children: number | null
  reason: string | null
  starts_before: boolean
  ends_after: boolean
  /** What the stay has been charged and what has come in. */
  has_folio: boolean
  total: string
  paid: string
  balance: string
}
export interface RackGroup {
  room_type_id: string
  name: string
  rooms: RackRoom[]
  unassigned: RackBar[]
  /** One entry per day: rooms left to sell, the night's rate, and whether
   *  the type is closed for sale. */
  availability: number[]
  rates: (string | null)[]
  /** True where the rooms differ in price and `rates` is the cheapest. */
  rate_varies: boolean[]
  stop_sell: boolean[]
}
export interface RackStat {
  value: number
  previous: number | null
  delta_pct: number | null
}
export interface RackView {
  start_date: string
  end_date: string
  days: string[]
  groups: RackGroup[]
  bars: RackBar[]
  /** Footer rows: rooms left to sell, and how full the house is, per day. */
  inventory: number[]
  occupancy_pct: string[]
  /** How the house stands on the first day, for the filter chips. */
  room_states: {
    all_rooms?: number; vacant?: number; occupied?: number
    reserved?: number; blocked?: number; due_out?: number; not_ready?: number
  }
  stats: {
    total_bookings: RackStat
    occupancy_pct: RackStat
    available_tonight: RackStat
    revenue: RackStat
    total_rooms: number
    rooms_booked: number
  }
  unassigned_count: number
}

export const RACK_STATUSES = [
  { code: 'confirmed', label: 'Confirmed', dot: 'bg-emerald-500' },
  { code: 'tentative', label: 'Tentative', dot: 'bg-amber-400' },
  { code: 'checked_in', label: 'Checked In', dot: 'bg-sky-500' },
  // A stay that has ended. Kept on the grid so a past date shows who was in
  // the room, and greyed so it reads as history rather than as inventory
  // still held.
  { code: 'checked_out', label: 'Checked Out', dot: 'bg-slate-400' },
  { code: 'blocked', label: 'Blocked', dot: 'bg-rose-400' },
]

export async function getReservationCalendar(
  propertyId: string,
  range: { start: string; end: string },
  filters: { room_type_id?: string; status?: string } = {},
): Promise<RackView> {
  const { data } = await api.get<RackView>('/booking/reservation-calendar', {
    params: {
      property_id: propertyId, start_date: range.start, end_date: range.end,
      room_type_id: filters.room_type_id || undefined,
      status: filters.status || undefined,
    },
  })
  return data
}

/* ---------------- Guest Check-In (screen 005) ---------------- */
export interface ArrivalRow {
  reservation_unit_id: string
  reservation_id: string
  number: string
  guest_name: string | null
  room_type: string
  room_type_id: string
  arrival_date: string
  departure_date: string
  nights: number
  has_folio: boolean
  organization_id: string | null
  folio_id: string | null
  adults: number
  children: number
  unit_status: string
  reservation_status: string
  assigned_room: string | null
  assigned_room_id: string | null
  checked_in: boolean
  plan: string | null
  /** The same three figures every other stay list carries. */
  total: string
  paid: string
  balance: string
}
export interface GuestDoc {
  id: string
  kind: string
  url: string | null
  original_name: string | null
  content_type: string | null
}
export interface CheckInRoomOption {
  id: string
  code: string
  floor: string | null
  bed_setup: string | null
  view_type: string | null
}
export interface CheckInView {
  reservation_unit_id: string
  reservation_id: string
  number: string
  reservation_status: string
  unit_status: string
  already_checked_in: boolean
  guest_id: string | null
  guest_name: string | null
  email: string | null
  phone: string | null
  nationality: string | null
  address_line: string | null
  city: string | null
  state: string | null
  postal_code: string | null
  country: string | null
  id_type: string | null
  id_type_label: string | null
  id_number: string | null
  id_verified: boolean
  documents: GuestDoc[]
  room_type: string
  room_type_id: string
  room_description: string | null
  assigned_room: string | null
  assigned_room_id: string | null
  available_rooms: CheckInRoomOption[]
  arrival_date: string
  departure_date: string
  nights: number
  adults: number
  children: number
  total_stay_amount: number
  amount_paid: number
  balance: number
  folio_id: string | null
  currency: string
  key_integration_available: boolean
  messaging_available: boolean
}
export interface CompleteCheckInResult {
  reservation_unit_id: string
  stay_id: string
  room_id: string
  room_code: string
  guest_id: string
  deposit_amount: number
  deposit_payment_id: string | null
  warnings: string[]
}

export const ID_TYPES = [
  { code: 'aadhaar', label: 'Aadhaar Card' },
  { code: 'passport', label: 'Passport' },
  { code: 'driving_licence', label: 'Driving Licence' },
  { code: 'voter_id', label: 'Voter ID' },
  { code: 'pan', label: 'PAN Card' },
  { code: 'other', label: 'Other' },
]

export async function listArrivals(
  propertyId: string,
  filters: { on_date?: string; include_checked_in?: boolean; search?: string } = {},
): Promise<ArrivalRow[]> {
  const { data } = await api.get<ArrivalRow[]>('/booking/arrivals', {
    params: {
      property_id: propertyId, on_date: filters.on_date || undefined,
      include_checked_in: filters.include_checked_in || undefined,
      search: filters.search || undefined,
    },
  })
  return data
}

export async function getCheckInView(
  unitId: string, propertyId: string,
): Promise<CheckInView> {
  const { data } = await api.get<CheckInView>(
    `/booking/reservation-units/${unitId}/check-in-view`,
    { params: { property_id: propertyId } },
  )
  return data
}

export async function completeCheckIn(
  unitId: string, propertyId: string, body: Record<string, unknown>,
): Promise<CompleteCheckInResult> {
  const { data } = await api.post<CompleteCheckInResult>(
    `/booking/reservation-units/${unitId}/complete-check-in`, body,
    { params: { property_id: propertyId } },
  )
  return data
}

export async function uploadGuestDocument(
  guestId: string, propertyId: string, kind: string, file: File,
): Promise<GuestDoc> {
  const form = new FormData()
  form.append('kind', kind)
  form.append('file', file)
  const { data } = await api.post<GuestDoc>(
    `/booking/guests/${guestId}/documents`, form,
    { params: { property_id: propertyId },
      headers: { 'Content-Type': 'multipart/form-data' } },
  )
  return data
}

export async function deleteGuestDocument(
  guestId: string, docId: string, propertyId: string,
): Promise<void> {
  await api.delete(`/booking/guests/${guestId}/documents/${docId}`,
    { params: { property_id: propertyId } })
}

/* ---------------- Guest Check-Out (screen 007) ---------------- */
export interface DepartureRow {
  reservation_unit_id: string
  reservation_id: string
  number: string
  guest_name: string | null
  room: string | null
  room_type: string
  arrival_date: string
  departure_date: string
  nights: number
  has_folio: boolean
  organization_id: string | null
  folio_id: string | null
  room_id: string | null
  adults: number
  children: number
  unit_status: string
  /** Board or rate basis, whichever the booking carries. */
  plan: string | null
  /** Charged, settled, and what is still owed — read together. */
  total: number
  paid: number
  balance: number
  checked_out: boolean
  due_label: string
}
export interface CheckOutFolioLine {
  id: string
  business_date: string
  description: string
  department: string
  qty: number
  amount: number
  entry_type: string
}
export interface CheckOutView {
  reservation_unit_id: string
  reservation_id: string
  number: string
  guest_name: string | null
  room: string | null
  room_type: string
  arrival_date: string
  departure_date: string
  nights: number
  unit_status: string
  already_checked_out: boolean
  due_label: string
  folio_id: string | null
  lines: CheckOutFolioLine[]
  subtotal: number
  taxes: number
  grand_total: number
  advance_paid: number
  balance_due: number
  deposit_held: number
  currency: string
}
export interface CompleteCheckOutResult {
  reservation_unit_id: string
  room: string | null
  collected: number
  deposit_refunded: number
  balance_at_checkout: number
  nights_released: number
  warnings: string[]
}

/** How many bookings sit in each state — the numbers on the tab strip. */
export interface ReservationStateCounts {
  reservations: number
  arrivals: number
  in_house: number
  departures: number
  on_date: string
}

export async function getReservationCounts(
  propertyId: string, onDate?: string,
): Promise<ReservationStateCounts> {
  const { data } = await api.get<ReservationStateCounts>(
    '/booking/reservation-counts',
    { params: { property_id: propertyId, on_date: onDate || undefined } })
  return data
}

/**
 * Everyone currently in a room.
 *
 * Same row shape as a departure, because the two lists differ in which guests
 * they hold rather than in what they say about them.
 */
export async function listInHouse(
  propertyId: string,
  filters: { search?: string } = {},
): Promise<DepartureRow[]> {
  const { data } = await api.get<DepartureRow[]>('/booking/in-house', {
    params: { property_id: propertyId, search: filters.search || undefined },
  })
  return data
}

export async function listDepartures(
  propertyId: string,
  filters: { on_date?: string; include_checked_out?: boolean; search?: string } = {},
): Promise<DepartureRow[]> {
  const { data } = await api.get<DepartureRow[]>('/booking/departures', {
    params: {
      property_id: propertyId, on_date: filters.on_date || undefined,
      include_checked_out: filters.include_checked_out || undefined,
      search: filters.search || undefined,
    },
  })
  return data
}

export async function getCheckOutView(
  unitId: string, propertyId: string,
): Promise<CheckOutView> {
  const { data } = await api.get<CheckOutView>(
    `/booking/reservation-units/${unitId}/check-out-view`,
    { params: { property_id: propertyId } },
  )
  return data
}

export async function completeCheckOut(
  unitId: string, propertyId: string, body: Record<string, unknown>,
): Promise<CompleteCheckOutResult> {
  const { data } = await api.post<CompleteCheckOutResult>(
    `/booking/reservation-units/${unitId}/complete-check-out`, body,
    { params: { property_id: propertyId } },
  )
  return data
}

/* ---------------- Room Move and Upgrade (screen 053) ---------------- */
export interface MoveRoomCard {
  id: string
  code: string
  room_type_id: string
  room_type: string
  bed_setup: string | null
  view_type: string | null
  floor: string | null
  base_rate: number | null
  photo_url: string | null
  readiness: string
  readiness_label: string
}
export interface MoveView {
  reservation_unit_id: string
  reservation_id: string
  number: string
  guest_name: string | null
  guest_phone: string | null
  guest_email: string | null
  arrival_date: string
  departure_date: string
  nights: number
  adults: number
  children: number
  unit_status: string
  stay_balance: number
  currency: string
  current_room: MoveRoomCard | null
  room_types: { id: string; name: string; base_rate: string }[]
  approval_threshold: number
  reasons: { code: string; label: string; upgrade: boolean }[]
  can_approve: boolean
  remaining_nights: number
}
export interface MoveQuote {
  rate_current: number
  rate_new: number
  rate_difference: number
  nights_applicable: number
  total_additional: number
  requires_approval: boolean
  room_type_changes: boolean
}
export interface MoveResult {
  id: string
  reference: string
  status: string
  from_room: string
  to_room: string
  effective_at: string
  rate_difference: number
  nights_applicable: number
  total_additional: number
  requires_approval: boolean
  warnings: string[]
}

export async function getRoomMoveView(
  unitId: string, propertyId: string,
): Promise<MoveView> {
  const { data } = await api.get<MoveView>(
    `/booking/reservation-units/${unitId}/room-move-view`,
    { params: { property_id: propertyId } },
  )
  return data
}

export async function listMoveCandidates(
  unitId: string, propertyId: string,
  filters: { room_type_id?: string; effective_date?: string } = {},
): Promise<MoveRoomCard[]> {
  const { data } = await api.get<MoveRoomCard[]>(
    `/booking/reservation-units/${unitId}/move-candidates`,
    { params: {
      property_id: propertyId,
      room_type_id: filters.room_type_id || undefined,
      effective_date: filters.effective_date || undefined,
    } },
  )
  return data
}

export async function getMoveQuote(
  unitId: string, propertyId: string, toRoomId: string, effectiveDate?: string,
): Promise<MoveQuote> {
  const { data } = await api.get<MoveQuote>(
    `/booking/reservation-units/${unitId}/move-quote`,
    { params: {
      property_id: propertyId, to_room_id: toRoomId,
      effective_date: effectiveDate || undefined,
    } },
  )
  return data
}

export async function createRoomMove(
  unitId: string, propertyId: string, body: Record<string, unknown>,
): Promise<MoveResult> {
  const { data } = await api.post<MoveResult>(
    `/booking/reservation-units/${unitId}/room-move`, body,
    { params: { property_id: propertyId } },
  )
  return data
}

/* ---------------- Extend Stay (in-house) ---------------- */
/**
 * Keeping a guest who is already in the house for longer.
 *
 * Separate from `modifyReservation`, which refuses in-house bookings because
 * it releases the assigned room and re-picks it. This extends the room's
 * calendar entry in place, so the guest stays where they are.
 *
 * `available` needs both halves: `unavailable_reason` is the room TYPE being
 * sold out, `room_conflicts` is this guest's own room being taken while the
 * type still has others free. The first means "no room to sell them", the
 * second means "move them first" — different problems, different answers.
 */
export interface ExtendQuote {
  in_house: boolean
  blocked_reason: string | null
  current_departure: string | null
  new_departure: string
  added_nights: number
  rooms: number
  nightly_rate: string
  estimated_amount: string
  available: boolean
  unavailable_reason: string | null
  room_conflicts: string[]
}

export async function getExtendQuote(
  reservationId: string, propertyId: string, newDeparture: string,
): Promise<ExtendQuote> {
  const { data } = await api.post<ExtendQuote>(
    `/booking/reservations/${reservationId}/extend-quote`,
    { new_departure_date: newDeparture },
    { params: { property_id: propertyId } },
  )
  return data
}

export async function extendStay(
  reservationId: string, propertyId: string, body: Record<string, unknown>,
): Promise<ChangeResult> {
  const { data } = await api.post<ChangeResult>(
    `/booking/reservations/${reservationId}/extend`, body,
    { params: { property_id: propertyId } },
  )
  return data
}

/* ---------------- Modify or Cancel Reservation (screen 029) ---------------- */
export interface StaySide {
  arrival_date: string
  departure_date: string
  nights: number
  room_type_id: string | null
  room_type: string
  adults: number
  children: number
  rooms: number
  nightly_rate: number
  total_amount: number
}
export interface CancelPolicy {
  name: string
  free_until_days: number
  penalty_nights: number
  requires_approval: boolean
  approval_above: number
  policy_text: string
}
export interface ChangeView {
  reservation_id: string
  number: string
  status: string
  guest_name: string | null
  guest_phone: string | null
  guest_email: string | null
  currency: string
  current: StaySide
  room_types: { id: string; name: string; base_rate: string }[]
  policy: CancelPolicy
  cancel_reasons: { code: string; label: string }[]
  amount_paid: number
  folio_id: string | null
  can_modify: boolean
  can_cancel: boolean
  blocked_reason: string | null
  can_approve: boolean
}
export interface ModifyQuote {
  current: StaySide
  proposed: StaySide
  difference: number
  available: boolean
  unavailable_reason: string | null
}
export interface CancelQuote {
  original: StaySide
  days_before_arrival: number
  within_penalty_window: boolean
  penalty_amount: number
  amount_paid: number
  refund_estimate: number
  requires_approval: boolean
  policy: CancelPolicy
}
export interface ChangeResult {
  id: string
  kind: string
  status: string
  difference: number
  penalty_amount: number
  refund_estimate: number
  warnings: string[]
}

export async function getChangeView(
  reservationId: string, propertyId: string,
): Promise<ChangeView> {
  const { data } = await api.get<ChangeView>(
    `/booking/reservations/${reservationId}/change-view`,
    { params: { property_id: propertyId } },
  )
  return data
}
export async function getModifyQuote(
  reservationId: string, propertyId: string, body: Record<string, unknown>,
): Promise<ModifyQuote> {
  const { data } = await api.post<ModifyQuote>(
    `/booking/reservations/${reservationId}/modify-quote`, body,
    { params: { property_id: propertyId } },
  )
  return data
}
export async function getCancelQuote(
  reservationId: string, propertyId: string,
): Promise<CancelQuote> {
  const { data } = await api.get<CancelQuote>(
    `/booking/reservations/${reservationId}/cancel-quote`,
    { params: { property_id: propertyId } },
  )
  return data
}
export async function modifyReservation(
  reservationId: string, propertyId: string, body: Record<string, unknown>,
): Promise<ChangeResult> {
  const { data } = await api.post<ChangeResult>(
    `/booking/reservations/${reservationId}/modify`, body,
    { params: { property_id: propertyId } },
  )
  return data
}
export async function cancelReservation(
  reservationId: string, propertyId: string, body: Record<string, unknown>,
): Promise<ChangeResult> {
  const { data } = await api.post<ChangeResult>(
    `/booking/reservations/${reservationId}/cancel`, body,
    { params: { property_id: propertyId } },
  )
  return data
}

/* ---------------- No-Show Processing (screen 051) ---------------- */
export interface NoShowCandidate {
  reservation_unit_id: string
  reservation_id: string
  number: string
  guest_name: string | null
  room_type: string
  room: string | null
  arrival_date: string
  departure_date: string
  nights: number
  days_overdue: number
  unit_status: string
  total_amount: number
  paid: number
}
export interface PenaltyOption {
  code: string
  label: string
  base_amount: number
  tax_amount: number
  total: number
}
export interface NoShowView {
  reservation_unit_id: string
  reservation_id: string
  number: string
  guest_name: string | null
  guest_email: string | null
  guest_phone: string | null
  guest_city: string | null
  arrival_date: string
  departure_date: string
  nights: number
  adults: number
  children: number
  room_type: string
  room: string | null
  rooms: number
  unit_status: string
  already_no_show: boolean
  blocked_reason: string | null
  nightly_rate: number
  room_charges: number
  taxes: number
  total_stay_amount: number
  advance_received: number
  balance_if_proceeds: number
  policy_text: string
  tax_label: string
  penalty_options: PenaltyOption[]
  deposit_actions: { code: string; label: string }[]
  reasons: { code: string; label: string }[]
  folio_id: string | null
  can_waive: boolean
  currency: string
}
export interface NoShowResult {
  reservation_unit_id: string
  penalty_charged: number
  refund_due: number
  room_released: string | null
  nights_returned: number
  warnings: string[]
}

export async function listNoShowCandidates(
  propertyId: string, onDate?: string,
): Promise<NoShowCandidate[]> {
  const { data } = await api.get<NoShowCandidate[]>('/booking/no-show-candidates',
    { params: { property_id: propertyId, on_date: onDate || undefined } })
  return data
}
export async function getNoShowView(
  unitId: string, propertyId: string,
): Promise<NoShowView> {
  const { data } = await api.get<NoShowView>(
    `/booking/reservation-units/${unitId}/no-show-view`,
    { params: { property_id: propertyId } })
  return data
}
export async function markNoShow(
  unitId: string, propertyId: string, body: Record<string, unknown>,
): Promise<NoShowResult> {
  const { data } = await api.post<NoShowResult>(
    `/booking/reservation-units/${unitId}/no-show`, body,
    { params: { property_id: propertyId } })
  return data
}

export async function saveCheckInGuest(
  unitId: string, propertyId: string, guest: Record<string, unknown>,
): Promise<{ guest_id: string; documents: GuestDoc[] }> {
  const { data } = await api.post<{ guest_id: string; documents: GuestDoc[] }>(
    `/booking/reservation-units/${unitId}/guest`, guest,
    { params: { property_id: propertyId } },
  )
  return data
}

/* ---------------------------------------------------------------- 006 */
export interface HkAttendant {
  user_id: string; name: string; open_tasks: number
  done_today: number; total_today: number; percent_done: number
}
export interface HkCard {
  room_id: string; room_code: string; room_type: string; floor: string | null
  lane: string; condition: string; priority: string
  task_id: string | null; kind: string | null
  attendant_id: string | null; attendant: string | null
  started_at: string | null; elapsed_minutes: number | null
  finished_at: string | null; rejected_count: number; notes: string | null
  departure_note: string | null; arrival_note: string | null
  arriving_in_minutes: number | null; occupied: boolean
}
export interface HkLane { key: string; label: string; count: number; cards: HkCard[] }
export interface HkBoard {
  business_date: string; checkin_time: string | null; checkout_time: string | null
  kpis: { dirty: number; cleaning: number; ready: number
          inspection_due: number; staff_on_duty: number }
  lanes: HkLane[]
  filters: { floors: string[]; room_types: { id: string; name: string }[]
             attendants: HkAttendant[] }
  can_edit: boolean; can_create: boolean; can_approve: boolean
  /** May take the board away as a file — the run sheet is printed and carried. */
  can_export: boolean
  unassigned: number
}
export interface HkActivity { at: string; room_code: string; text: string; tone: string }

export async function getHkBoard(
  propertyId: string, filters: Record<string, string | undefined> = {},
): Promise<HkBoard> {
  const { data } = await api.get<HkBoard>('/booking/housekeeping/board',
    { params: { property_id: propertyId, ...filters } })
  return data
}
export async function getHkActivity(propertyId: string): Promise<HkActivity[]> {
  const { data } = await api.get<HkActivity[]>('/booking/housekeeping/activity',
    { params: { property_id: propertyId, limit: 12 } })
  return data
}
export async function createHkTask(
  propertyId: string, body: Record<string, unknown>,
): Promise<{ id: string; room_code: string; attendant: string | null }> {
  const { data } = await api.post('/booking/housekeeping/tasks', body,
    { params: { property_id: propertyId } })
  return data
}
export async function assignHkTask(
  taskId: string, propertyId: string, body: Record<string, unknown>,
): Promise<{ id: string; attendant: string | null }> {
  const { data } = await api.post(`/booking/housekeeping/tasks/${taskId}/assign`,
    body, { params: { property_id: propertyId } })
  return data
}
export async function advanceHkTask(
  taskId: string, propertyId: string, action: string, notes?: string,
): Promise<{ id: string; state: string; condition: string }> {
  const { data } = await api.post(`/booking/housekeeping/tasks/${taskId}/advance`,
    { action, notes: notes ?? null }, { params: { property_id: propertyId } })
  return data
}
export async function cancelHkTask(
  taskId: string, propertyId: string, notes?: string,
): Promise<{ id: string; state: string }> {
  const { data } = await api.post(`/booking/housekeeping/tasks/${taskId}/cancel`,
    { action: 'cancel', notes: notes ?? null },
    { params: { property_id: propertyId } })
  return data
}
export async function autoAssignHk(
  propertyId: string,
): Promise<{ assigned: number; skipped: number; detail: string }> {
  const { data } = await api.post('/booking/housekeeping/auto-assign', null,
    { params: { property_id: propertyId } })
  return data
}

/* ---------------------------------------------------------------- 036 */
export interface CashSummary {
  business_date: string; currency: string
  collected: string; collected_count: number
  previous_day: string; change_percent: number | null
  methods: { method: string; label: string; amount: string
             percent: number; count: number }[]
  outstanding: string; outstanding_folios: number
}
export interface CashTxn {
  payment_id: string; received_at: string; guest_name: string | null
  folio_id: string | null; reservation_number: string | null
  room_code: string | null; source: string; source_label: string
  method: string; method_label: string; reference: string | null
  amount: string; currency: string; cashier: string | null
  payment_status: string
  /** 'payment' or 'refund'. A refund's `amount` is already negative. */
  kind: 'payment' | 'refund'
}
export interface CashDetail extends CashTxn {
  reservation_id: string | null; provider_transaction_id: string | null
  notes: string | null; allocated_total: string; property_name: string
  allocations: { category: string; description: string; amount: string }[]
}
export interface CashShift {
  id: string; cashier_id: string; cashier: string | null
  business_date: string; status: string; opened_at: string
  closed_at: string | null; opening_float: string
  declared_cash: string | null; expected_cash: string | null
  variance: string | null; cash_taken: string; payments_count: number
  /** Cash handed back out of this drawer. The server already subtracts it
   *  from `expected_cash`; the UI shows it so the arithmetic adds up on
   *  screen instead of only in the ledger. */
  cash_refunded: string; refunds_count: number
  /** Cash banked out of the tray mid-shift. Reduces what the drawer should
   *  hold, without being a refund — the money is still the property's. */
  cash_dropped: string
  drops_count: number
  /** Everything taken in the shift, by every method — the business done at
   *  this desk, as opposed to what is in the tray. */
  total_taken: string
  total_refunded: string
  by_method: { method: string; label: string; amount: string; count: number }[]
  notes: string | null
}
export interface CashOptions {
  methods: { value: string; label: string; needs_reference: boolean }[]
  sources: { value: string; label: string }[]
  /** Rooms money has actually gone through, for the room filter. */
  rooms: { value: string; label: string }[]
  can_collect: boolean; can_manage_shift: boolean; can_export: boolean
}
export interface OpenFolio {
  folio_id: string; guest_name: string | null
  reservation_number: string | null; room_code: string | null
  balance: string; currency: string
}

export async function getCashSummary(
  propertyId: string, onDate?: string,
): Promise<CashSummary> {
  const { data } = await api.get<CashSummary>('/finance/cashiering/summary',
    { params: { property_id: propertyId, on_date: onDate } })
  return data
}
export async function getCashOptions(propertyId: string): Promise<CashOptions> {
  const { data } = await api.get<CashOptions>('/finance/cashiering/options',
    { params: { property_id: propertyId } })
  return data
}
export async function getCashTxns(
  propertyId: string, filters: Record<string, string | undefined> = {},
): Promise<CashTxn[]> {
  const { data } = await api.get<CashTxn[]>('/finance/cashiering/transactions',
    { params: { property_id: propertyId, ...filters } })
  return data
}
/* ------------------------------------------------------------- day book --- */
/**
 * Every folio movement on a business date — charges included.
 *
 * Distinct from `getCashTxns`, which is money that crossed the counter and
 * nothing else. The two answer different questions and the day book is the
 * one people reach for when a charge they posted is "missing" from Payments.
 */
export interface DayBookRow {
  entry_id: string
  posted_at: string
  business_date: string
  folio_id: string
  folio_no: string | null
  reservation_id: string | null
  reservation_number: string | null
  guest_name: string | null
  room_code: string | null
  kind: 'charge' | 'payment' | 'refund' | 'adjustment' | 'deposit'
  kind_label: string
  source_type: string
  description: string
  note: string | null
  entry_type: 'debit' | 'credit'
  amount: number
  /** Exactly one of these is set on every row. */
  debit: number | null
  credit: number | null
  posted_by: string | null
  reverses_entry_id: string | null
}
export interface DayBook {
  business_date: string
  rows: DayBookRow[]
  totals: {
    charges: number; payments: number; refunds: number
    adjustments: number; deposits: number; net: number; count: number
  }
  kinds: { value: string; label: string; count: number; total: number }[]
  rooms: string[]
}
export async function getDayBook(
  propertyId: string, filters: Record<string, string | undefined> = {},
): Promise<DayBook> {
  const { data } = await api.get<DayBook>('/finance/daybook',
    { params: { property_id: propertyId, ...filters } })
  return data
}

export async function getCashPayment(
  paymentId: string, propertyId: string,
): Promise<CashDetail> {
  const { data } = await api.get<CashDetail>(
    `/finance/cashiering/payments/${paymentId}`,
    { params: { property_id: propertyId } })
  return data
}
export async function getCashShifts(
  propertyId: string, onDate?: string,
): Promise<{ shifts: CashShift[]; my_open_shift: CashShift | null; can_open: boolean }> {
  const { data } = await api.get('/finance/cashiering/shifts',
    { params: { property_id: propertyId, on_date: onDate } })
  return data
}
export async function openCashShift(
  propertyId: string, body: Record<string, unknown>,
): Promise<CashShift> {
  const { data } = await api.post<CashShift>('/finance/cashiering/shifts', body,
    { params: { property_id: propertyId } })
  return data
}
export async function closeCashShift(
  shiftId: string, propertyId: string, body: Record<string, unknown>,
): Promise<CashShift> {
  const { data } = await api.post<CashShift>(
    `/finance/cashiering/shifts/${shiftId}/close`, body,
    { params: { property_id: propertyId } })
  return data
}
export interface CashDrop {
  id: string; amount: string; currency: string
  destination: string; destination_label: string
  reference: string | null; note: string | null
  dropped_at: string
  dropped_by_name: string | null; witnessed_by_name: string | null
}
/** Bank part of a drawer without closing it. */
export async function recordCashDrop(
  shiftId: string, propertyId: string, body: Record<string, unknown>,
): Promise<CashDrop> {
  const { data } = await api.post<CashDrop>(
    `/finance/cashiering/shifts/${shiftId}/drops`, body,
    { params: { property_id: propertyId } })
  return data
}
export async function getCashDrops(
  shiftId: string, propertyId: string,
): Promise<CashDrop[]> {
  const { data } = await api.get<CashDrop[]>(
    `/finance/cashiering/shifts/${shiftId}/drops`,
    { params: { property_id: propertyId } })
  return data
}
export async function getOpenFolios(
  propertyId: string, q?: string,
): Promise<OpenFolio[]> {
  const { data } = await api.get<OpenFolio[]>('/finance/cashiering/open-folios',
    { params: { property_id: propertyId, q } })
  return data
}
export async function collectCashPayment(
  propertyId: string, body: Record<string, unknown>,
): Promise<{ payment_id: string; amount: string; method_label: string
            balance_after: string }> {
  const { data } = await api.post('/finance/cashiering/payments', body,
    { params: { property_id: propertyId } })
  return data
}

/* ---------------------------------------------------------------- 114 */
export interface AdjCharge {
  entry_id: string; business_date: string
  /** When the line was posted. The business date alone cannot tell several
   *  charges on one day apart, and this list is where one is chosen. */
  posted_at: string
  description: string
  department: string; source_type: string; amount: string
  tax_amount: string; total: string; entry_status: string
  adjustable: boolean; blocked_reason: string | null
}
export interface AdjRow {
  id: string; kind: string; kind_label: string; status: string
  amount: string; tax_amount: string; adjust_tax: boolean
  reason: string; reason_label: string; remarks: string
  charge_description: string | null
  evidence_name: string | null; evidence_url: string | null
  approval_required: boolean; approval_status: string | null
  policy_rule_text: string | null
  created_by_name: string | null; created_at: string
  posted_at: string | null; posted_by_name: string | null
  decided_at: string | null; decided_by: string | null
  decision_comment: string | null
  can_post: boolean; can_reverse: boolean
}
export interface AdjContext {
  guest: {
    guest_name: string | null; email: string | null; phone: string | null
    reservation_number: string | null; reservation_id: string | null
    arrival_date: string | null; departure_date: string | null
    room_type: string | null; room_code: string | null
    adults: number | null; children: number | null
    reservation_status: string | null
  }
  folio: {
    folio_id: string; folio_no: string; folio_type: string; status: string
    currency: string; total_charges: string; total_payments: string
    total_adjustments: string; balance: string
  }
  charges: AdjCharge[]
  adjustments: AdjRow[]
  policy: {
    name: string | null; rule_text: string; threshold: string | null
    approver_roles: string[]; days_after_checkout: number
  }
  kinds: { value: string; label: string }[]
  reasons: { value: string; label: string }[]
  can_create: boolean; can_post: boolean; can_reverse: boolean
  adjustable: boolean; not_adjustable_reason: string | null
}
export interface AdjDecided {
  id: string; status: string; approval_required: boolean
  policy_rule_text: string; balance_after: string; message: string
}

export async function getAdjustmentContext(
  folioId: string, propertyId: string,
): Promise<AdjContext> {
  const { data } = await api.get<AdjContext>(
    `/finance/folios/${folioId}/adjustment-context`,
    { params: { property_id: propertyId } })
  return data
}
export async function createAdjustment(
  folioId: string, propertyId: string, body: Record<string, unknown>,
): Promise<AdjDecided> {
  const { data } = await api.post<AdjDecided>(
    `/finance/folios/${folioId}/adjustments`, body,
    { params: { property_id: propertyId } })
  return data
}
export async function postAdjustment(
  adjustmentId: string, propertyId: string,
): Promise<AdjDecided> {
  const { data } = await api.post<AdjDecided>(
    `/finance/folio-adjustments/${adjustmentId}/post`, null,
    { params: { property_id: propertyId } })
  return data
}
export async function reverseAdjustment(
  adjustmentId: string, propertyId: string, remarks: string,
): Promise<AdjDecided> {
  const { data } = await api.post<AdjDecided>(
    `/finance/folio-adjustments/${adjustmentId}/reverse`, null,
    { params: { property_id: propertyId, remarks } })
  return data
}
export async function uploadAdjustmentEvidence(
  adjustmentId: string, propertyId: string, file: File,
): Promise<{ name: string; url: string | null }> {
  const form = new FormData()
  form.append('file', file)
  const { data } = await api.post(
    `/finance/folio-adjustments/${adjustmentId}/evidence`, form,
    { params: { property_id: propertyId },
      headers: { 'Content-Type': undefined } })
  return data
}

/* ---------------------------------------------------------------- 115 */
export interface RevTimeline {
  at: string; title: string; detail: string; actor: string; tone: string
}
export interface RevRow {
  id: string; kind: string; kind_label: string; status: string
  amount: string; reason: string; reason_label: string; remarks: string
  approval_required: boolean; approval_status: string | null
  policy_rule_text: string | null
  created_by_name: string | null; created_at: string
  posted_at: string | null; posted_by_name: string | null
  decided_by: string | null; decided_at: string | null
  decision_comment: string | null
  can_post: boolean; can_cancel: boolean
}
export interface RevRule {
  kind: string; label: string; text: string; short: string; allowed: boolean
  blocked_reason: string | null; partial_allowed: boolean
}
export interface RevFolioItem {
  entry_id: string; business_date: string; posted_at: string
  description: string; entry_type: string; amount: string
  /** What the folio stood at after this entry. Accumulated oldest-first by
   *  the server, so it stays correct in a newest-first list. */
  running: string
  is_this_payment: boolean; reverses_something: boolean
}
export interface RevRecon {
  lines: { label: string; amount: string; note: string | null
           emphasis: boolean }[]
  balanced: boolean; verdict: string
}
export interface RevAudit {
  at: string; action: string; label: string; actor: string
  reason: string | null; detail: string | null
}
export interface RevContext {
  payment: {
    payment_id: string
    /** The number on this payment's receipt, as the PDF prints it. */
    receipt_no: string
    received_at: string; business_date: string | null
    guest_name: string | null; reservation_number: string | null
    reservation_id: string | null; room_code: string | null
    room_type: string | null
    arrival_date: string | null; departure_date: string | null
    folio_id: string | null; folio_no: string | null
    method: string; method_label: string
    source: string; source_label: string
    reference: string | null; provider_transaction_id: string | null
    cashier: string | null; notes: string | null; payment_status: string
    settled_at: string | null
    amount: string; currency: string
    refunded: string; refundable: string
    folio_balance: string | null; same_business_day: boolean
  }
  timeline: RevTimeline[]
  folio_items: RevFolioItem[]
  reconciliation: RevRecon
  audit: RevAudit[]
  reversals: RevRow[]
  rules: RevRule[]
  reasons: { value: string; label: string }[]
  can_create: boolean; can_post: boolean
}
export interface RevDecided {
  id: string; status: string; approval_required: boolean
  policy_rule_text: string; folio_balance: string | null; message: string
}

export async function getReversalContext(
  paymentId: string, propertyId: string,
): Promise<RevContext> {
  const { data } = await api.get<RevContext>(
    `/finance/payments/${paymentId}/reversal-context`,
    { params: { property_id: propertyId } })
  return data
}
export async function createReversal(
  paymentId: string, propertyId: string, body: Record<string, unknown>,
): Promise<RevDecided> {
  const { data } = await api.post<RevDecided>(
    `/finance/payments/${paymentId}/reversals`, body,
    { params: { property_id: propertyId } })
  return data
}
export async function postReversal(
  reversalId: string, propertyId: string,
): Promise<RevDecided> {
  const { data } = await api.post<RevDecided>(
    `/finance/payment-reversals/${reversalId}/post`, null,
    { params: { property_id: propertyId } })
  return data
}
export async function cancelReversal(
  reversalId: string, propertyId: string, remarks: string,
): Promise<RevDecided> {
  const { data } = await api.post<RevDecided>(
    `/finance/payment-reversals/${reversalId}/cancel`, null,
    { params: { property_id: propertyId, remarks } })
  return data
}


/* ---------------- Business source & market segment masters ---------------- */

/**
 * One entry in a booking classification vocabulary.
 *
 * Business source says which OTA or agent a booking came through; market
 * segment says what kind of business it is. They share a shape and a table,
 * and differ by `kind`.
 */
export interface BookingAttribute {
  id: string
  kind: BookingAttributeKind
  kind_label: string
  code: string
  name: string
  status: 'active' | 'inactive'
  sort_order: number
  notes: string | null
  created_at: string
  /** Bookings classified by this entry. Derived — it is why delete refuses. */
  bookings: number
}

export type BookingAttributeKind = 'business_source' | 'market_segment'

export interface BookingAttributeList {
  rows: BookingAttribute[]
  total: number
  /** Whether the caller may change the vocabulary, not merely read it. */
  can_configure: boolean
}

export async function listBookingAttributes(
  organizationId: string,
  params: { kind?: BookingAttributeKind; status?: string; q?: string } = {},
): Promise<BookingAttributeList> {
  const { data } = await api.get<BookingAttributeList>(
    '/booking/booking-attributes',
    { params: { organization_id: organizationId, ...params } })
  return data
}

export interface BookingAttributeInput {
  organization_id: string
  kind: BookingAttributeKind
  name: string
  /** Derived from the name when left blank. */
  code?: string | null
  status?: string
  sort_order?: number
  notes?: string | null
}

export async function createBookingAttribute(
  body: BookingAttributeInput,
): Promise<BookingAttribute> {
  const { data } = await api.post<BookingAttribute>(
    '/booking/booking-attributes', body)
  return data
}

export async function updateBookingAttribute(
  id: string, body: BookingAttributeInput,
): Promise<BookingAttribute> {
  const { data } = await api.patch<BookingAttribute>(
    `/booking/booking-attributes/${id}`, body)
  return data
}

/** Refused with a 409 when bookings still point at the entry. */
export async function deleteBookingAttribute(
  id: string, organizationId: string,
): Promise<void> {
  await api.delete(`/booking/booking-attributes/${id}`,
    { params: { organization_id: organizationId } })
}


/* ---------------- Onboarding (multi-tenant property setup) ---------------- */
export interface OnboardingStep {
  key: string
  label: string
  /** Counted from the property's data, never a stored flag. */
  complete: boolean
  optional: boolean
  visited: boolean
  skipped: boolean
  blocker: string | null
}

export interface OnboardingState {
  property_id: string
  property_name: string
  property_city: string | null
  currency: string
  timezone: string
  current_step: string
  steps: OnboardingStep[]
  counts: {
    rooms: number; room_types: number; staff_invited: number; bookings: number
  }
  ready_to_activate: boolean
  activated_at: string | null
}

export async function getOnboarding(propertyId: string): Promise<OnboardingState> {
  const { data } = await api.get<OnboardingState>('/iam/onboarding', {
    params: { property_id: propertyId },
  })
  return data
}

export async function patchOnboarding(
  propertyId: string,
  body: { current_step?: string; visited?: string; skipped?: string },
): Promise<OnboardingState> {
  const { data } = await api.patch<OnboardingState>('/iam/onboarding', body, {
    params: { property_id: propertyId },
  })
  return data
}

export async function activateProperty(propertyId: string): Promise<OnboardingState> {
  const { data } = await api.post<OnboardingState>('/iam/onboarding/activate',
    null, { params: { property_id: propertyId } })
  return data
}


/** The property as onboarding step 2 collects it. */
export interface PropertyProfile {
  id?: string
  code?: string
  name: string
  property_type: string | null
  contact_email: string | null
  contact_phone: string | null
  address_line: string | null
  city: string | null
  state: string | null
  postal_code: string | null
  country: string | null
  timezone: string | null
  currency: string | null
  logo_key?: string | null
}

export async function getPropertyProfile(propertyId: string): Promise<PropertyProfile> {
  const { data } = await api.get<PropertyProfile>('/iam/onboarding/property', {
    params: { property_id: propertyId },
  })
  return data
}

export async function savePropertyProfile(
  propertyId: string, body: PropertyProfile,
): Promise<PropertyProfile> {
  const { data } = await api.put<PropertyProfile>('/iam/onboarding/property', body, {
    params: { property_id: propertyId },
  })
  return data
}


/* ---------------- Booking import (onboarding step 8) ---------------- */
export interface ImportRowIssue {
  row: number
  guest: string
  issue: string
  /** Which field is at fault, so the screen can offer the right fix. */
  field: string
}

export interface ImportCheck {
  total_rows: number
  valid_rows: number
  issues: ImportRowIssue[]
  advance_total: string
  outstanding_total: string
  ignored_columns: string[]
}

/** Read the file and report what is wrong with it. Nothing is written. */
export async function checkBookingImport(
  propertyId: string, file: File,
): Promise<ImportCheck> {
  const form = new FormData()
  form.append('file', file)
  const { data } = await api.post<ImportCheck>('/booking/import/check', form, {
    params: { property_id: propertyId },
  })
  return data
}

/* ---------------- Onboarding step 10: the test booking ---------------- */
export interface TestStage { name: string; passed: boolean; detail: string }

export interface TestBookingResult {
  passed: boolean
  stages: TestStage[]
  room_type: string | null
  arrival: string | null
  departure: string | null
  number: string | null
}

/**
 * Push a booking through the real flow and undo it.
 *
 * Nothing is written: the server runs the sequence inside a savepoint and
 * rolls it back, so no reservation, number or inventory movement survives.
 */
export async function runTestBooking(propertyId: string): Promise<TestBookingResult> {
  const { data } = await api.post<TestBookingResult>(
    '/booking/onboarding/test-booking', null,
    { params: { property_id: propertyId } },
  )
  return data
}

export interface ImportedRow {
  row: number
  guest: string
  created: boolean
  number: string | null
  reason: string | null
}

export interface ImportResult {
  created: number
  skipped: number
  rows: ImportedRow[]
  charged: string
  paid: string
}

/**
 * Create the bookings the file describes.
 *
 * The server re-reads and re-checks the file rather than trusting the review,
 * writes each booking in its own savepoint so one bad row does not cost the
 * rest, and skips any `booking_reference` it has already imported.
 */
export async function commitBookingImport(
  propertyId: string, file: File, validOnly: boolean,
): Promise<ImportResult> {
  const form = new FormData()
  form.append('file', file)
  const { data } = await api.post<ImportResult>('/booking/import/commit', form, {
    params: { property_id: propertyId, valid_only: validOnly },
  })
  return data
}

/**
 * Save the template to disk.
 *
 * Fetched rather than linked: the endpoint needs the session token, and a
 * plain <a href> carries no Authorization header, so a link would simply 401.
 */
export async function downloadImportTemplate(propertyId: string): Promise<void> {
  const { data } = await api.get<Blob>('/booking/import/template', {
    params: { property_id: propertyId }, responseType: 'blob',
  })
  const url = URL.createObjectURL(data)
  const a = document.createElement('a')
  a.href = url
  a.download = 'booking-import-template.csv'
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}


/** Create a numbered run of rooms in one call (onboarding step 4). */
export interface BulkCreateOut {
  created: number
  skipped: number
  results: { code: string; created: boolean; reason: string | null }[]
}

export async function bulkCreateRooms(propertyId: string, body: {
  room_type_id: string
  spec: string
  /** Pins the rooms to the structure; the building and the display names are
   *  taken from the floor record, so the two never drift apart. */
  floor_id?: string | null
  building?: string | null
  floor?: string | null
  max_adults?: number
  max_children?: number
  base_rate?: number | null
  bed_setup?: string | null
}): Promise<BulkCreateOut> {
  const { data } = await api.post<BulkCreateOut>('/booking/rooms/bulk-create',
    body, { params: { property_id: propertyId } })
  return data
}


/** Onboarding step 5: the tariff and the rules a stay is sold under. */
export interface RatesAndPolicies {
  rates: {
    room_type_id: string
    name: string
    base_rate: string | null
    extra_adult_rate: string | null
    extra_child_rate: string | null
    /** Rooms on sale under this type. Zero means the price is academic. */
    rooms: number
  }[]
  policy: {
    checkin_time: string | null
    checkout_time: string | null
    advance_kind: string | null
    advance_value: string | null
    /** "Flexible" | "Moderate" | "Strict" | "Non-refundable". */
    cancellation_name: string | null
    free_until_days: number | null
    policy_text: string | null
  }
}

export async function getOnboardingRates(propertyId: string): Promise<RatesAndPolicies> {
  const { data } = await api.get<RatesAndPolicies>('/iam/onboarding/rates', {
    params: { property_id: propertyId },
  })
  return data
}

export async function saveOnboardingRates(
  propertyId: string, body: RatesAndPolicies,
): Promise<RatesAndPolicies> {
  const { data } = await api.put<RatesAndPolicies>('/iam/onboarding/rates', body, {
    params: { property_id: propertyId },
  })
  return data
}


/** The property's logo. Only the key is stored; reads are expiring links. */
export interface PropertyLogo {
  logo_key: string | null
  url: string | null
}

export interface WelcomeSendResult {
  sent: number
  /** Addresses the mail server would not take. */
  failed: string[]
}

/**
 * Send the welcome email again to everyone still without a password.
 *
 * Issuing a new link retires the previous unused one, so the old email stops
 * working. Anyone who already has a password is skipped.
 */
export async function resendWelcomeEmails(
  propertyId: string,
): Promise<WelcomeSendResult> {
  const { data } = await api.post<WelcomeSendResult>(
    '/iam/onboarding/resend-welcome', null,
    { params: { property_id: propertyId } },
  )
  return data
}

export async function getPropertyLogo(propertyId: string): Promise<PropertyLogo> {
  const { data } = await api.get<PropertyLogo>('/iam/onboarding/property/logo', {
    params: { property_id: propertyId },
  })
  return data
}

export async function uploadPropertyLogo(
  propertyId: string, file: File,
): Promise<PropertyLogo> {
  const body = new FormData()
  body.append('file', file)
  const { data } = await api.post<PropertyLogo>(
    '/iam/onboarding/property/logo', body,
    { params: { property_id: propertyId } },
  )
  return data
}

export async function deletePropertyLogo(propertyId: string): Promise<PropertyLogo> {
  const { data } = await api.delete<PropertyLogo>('/iam/onboarding/property/logo', {
    params: { property_id: propertyId },
  })
  return data
}


export async function deleteBuilding(
  propertyId: string, buildingId: string,
): Promise<void> {
  await api.delete(`/booking/buildings/${buildingId}`, {
    params: { property_id: propertyId },
  })
}

export async function deleteFloor(
  propertyId: string, floorId: string,
): Promise<void> {
  await api.delete(`/booking/floors/${floorId}`, {
    params: { property_id: propertyId },
  })
}

/* ---------------- Night audit ---------------- */

/** A booking the audit found and a person has to deal with. */
export interface NightAuditPending {
  unit_id: string
  reservation_number: string | null
  room: string | null
  guest: string | null
  arrival_date: string
  departure_date: string
}

/** One room, one night — what the audit is about to bill for it. */
export interface NightAuditChargeLine {
  unit_id: string
  reservation_number: string | null
  room: string | null
  guest: string | null
  room_type: string | null
  amount: string
}

/** A till still open as the day closes. */
export interface NightAuditOpenShift {
  shift_id: string
  cashier: string | null
  opened_at: string | null
  opening_float: string
}

export interface NightAuditPreview {
  property_id: string
  business_date: string
  /** Where the business date moves to. From the server, not date maths here. */
  next_business_date: string
  already_closed: boolean
  /** False when this day cannot be closed, with the reason why. */
  can_close: boolean
  blocked_reason: string | null
  /** Closing is possible but needs an explicit override, and why. */
  requires_override: boolean
  override_reason: string | null
  rooms_to_charge: number
  amount_to_charge: string
  /** The lines behind the total, so the charges can actually be reviewed. */
  charge_lines: NightAuditChargeLine[]
  no_shows: NightAuditPending[]
  overstays: NightAuditPending[]
  open_shifts: number
  /** Which tills, not just how many. */
  open_shift_rows: NightAuditOpenShift[]
  warnings: string[]
  /** Whether anything closes this day on its own, and at what local hour. */
  auto_audit_enabled: boolean
  audit_hour: number
}

export interface NightAuditResult {
  run_id: string
  business_date: string
  charges_posted: number
  next_business_date: string
  steps: string[]
  rooms_charged: number
  amount_charged: string
  no_shows: NightAuditPending[]
  overstays: NightAuditPending[]
  horizon_days_added: number
  open_shifts: number
  warnings: string[]
}

export interface NightAuditStep {
  step_code: string
  status: string
  error: string | null
  detail: Record<string, unknown> | null
}

export interface NightAuditRun {
  run_id: string
  business_date: string
  run_number: number
  status: string
  started_at: string | null
  completed_at: string | null
  /** Who ran it. null means the schedule did, unattended. */
  run_by_name: string | null
  steps: NightAuditStep[]
}

/** What tonight's audit would do, without doing it. */
export async function previewNightAudit(
  propertyId: string, businessDate?: string,
): Promise<NightAuditPreview> {
  const { data } = await api.get<NightAuditPreview>('/finance/night-audit/preview',
    { params: { property_id: propertyId, business_date: businessDate } })
  return data
}

/** A page of runs, and how many match the filter in total. */
export interface NightAuditHistory {
  rows: NightAuditRun[]
  total: number
  limit: number
  offset: number
}

export async function listNightAuditRuns(
  propertyId: string,
  opts: { from?: string; to?: string; limit?: number; offset?: number } = {},
): Promise<NightAuditHistory> {
  const { data } = await api.get<NightAuditHistory>(
    '/finance/night-audit/history',
    { params: {
      property_id: propertyId,
      from_date: opts.from || undefined,
      to_date: opts.to || undefined,
      limit: opts.limit ?? 50,
      offset: opts.offset ?? 0,
    } })
  return data
}

/** Close the day. Charges are derived server-side from who is in the house. */
export async function runNightAudit(body: {
  organization_id: string
  property_id: string
  business_date: string
  /** Close over a till that is still open. Deliberate, and recorded. */
  allow_open_shifts?: boolean
}): Promise<NightAuditResult> {
  const { data } = await api.post<NightAuditResult>(
    '/finance/night-audit/run', body)
  return data
}

/** When a property closes its books. */
export interface NightAuditSettings {
  property_id: string
  /** null = follow the deployment default. */
  audit_hour: number | null
  default_hour: number
  /** The minute past the hour this property actually fires. */
  scheduled_minute: number
  enabled: boolean
  /** What an unattended no-show costs. "none" records it without charging. */
  no_show_penalty: string
  /** Served, so this screen and the No-Show screen cannot drift apart. */
  no_show_options: { code: string; label: string }[]
}

export async function getNightAuditSettings(
  propertyId: string,
): Promise<NightAuditSettings> {
  const { data } = await api.get<NightAuditSettings>(
    '/finance/night-audit/settings', { params: { property_id: propertyId } })
  return data
}

export async function saveNightAuditSettings(
  propertyId: string,
  body: { audit_hour?: number | null; no_show_penalty?: string },
): Promise<NightAuditSettings> {
  const { data } = await api.put<NightAuditSettings>(
    '/finance/night-audit/settings', body,
    { params: { property_id: propertyId } })
  return data
}

/* ---------------- Night audit: the manager's report ---------------- */

export interface SalesLine {
  category: string
  charges: string
  tax: string
  total: string
}

export interface ReceiptLine {
  label: string
  amount: string
  count: number
}

export interface PaxLine {
  label: string
  rooms: number
  adults: number
  children: number
}

export interface ReceiptDetailLine {
  reference: string | null
  room: string | null
  method: string
  amount: string
  cashier: string | null
  received_at: string | null
}

export interface DepartureLine {
  reservation_number: string | null
  room: string | null
  guest: string | null
  arrival_date: string
  departure_date: string
  nights: number
  left_at: string | null
  charged: string
  paid: string
  balance: string
}

export interface NightAuditReportData {
  property_id: string
  business_date: string
  currency: string
  sales: SalesLine[]
  sales_total: SalesLine | null
  receipts_by_method: ReceiptLine[]
  receipts_by_user: ReceiptLine[]
  receipts_total: string
  balance: { charged: string; collected: string; outstanding: string }
  /** Rooms and heads as the day closed; null for runs predating the snapshot. */
  occupancy: Record<string, number> | null
  departures: DepartureLine[]
  receipts: ReceiptDetailLine[]
  pax_status: PaxLine[]
  pax_by_rate_type: PaxLine[]
}

export async function getNightAuditReport(
  propertyId: string, businessDate: string,
): Promise<NightAuditReportData> {
  const { data } = await api.get<NightAuditReportData>(
    '/finance/night-audit/report',
    { params: { property_id: propertyId, business_date: businessDate } })
  return data
}

/* ---------------- Payment gateway credentials (tenant-owned) ---------------- */

/**
 * A tenant's own payment gateway.
 *
 * Organisation-scoped, not property-scoped: a gateway account belongs to a
 * business with a bank account, which is what an organisation is here. The
 * request carries no id at all — the server reads the tenant from the session,
 * because the one field that decides whose bank account guests pay into is the
 * one a caller must not be able to choose.
 *
 * **No secret ever comes back.** The server reports whether a secret is *set*,
 * never what it is, so there is nothing here to re-populate a form with. A
 * blank secret field on save means "keep what is stored", not "clear it".
 */
export interface PaymentCredentials {
  provider: 'mock' | 'razorpay'
  /** 'tenant' | 'deployment' | 'none' — which keys are actually in effect. */
  source: string
  enabled: boolean
  /** The publishable half. Safe to show: it ships to the guest's browser. */
  key_id: string
  key_secret_set: boolean
  webhook_secret_set: boolean
  /** Absent until the first save — the callback ref is created with the row. */
  webhook_url: string | null
  /** False when the deployment has no encryption key, so secrets cannot be
   *  stored at all. Saving one is refused rather than written in the clear. */
  can_store_secrets: boolean
  /** True when this tenant is transacting through the *deployment owner's*
   *  account rather than their own — guests are paying, and the money is not
   *  reaching this tenant. */
  using_deployment_account: boolean
}

export interface PaymentCredentialsInput {
  provider: 'mock' | 'razorpay'
  key_id?: string | null
  /** Omit to keep the stored one. */
  key_secret?: string | null
  webhook_secret?: string | null
  enabled: boolean
}

export async function getPaymentCredentials(): Promise<PaymentCredentials> {
  const { data } = await api.get<PaymentCredentials>('/finance/payments/credentials')
  return data
}

export async function savePaymentCredentials(
  body: PaymentCredentialsInput,
): Promise<PaymentCredentials> {
  const { data } = await api.put<PaymentCredentials>(
    '/finance/payments/credentials', body)
  return data
}

/**
 * Issue a new callback URL.
 *
 * **Payments break until the new URL is saved in the Razorpay dashboard** —
 * callbacks to the old path stop being recognised the moment this returns.
 * That is the point: a leaked URL that keeps working is not rotated.
 */
export async function rotatePaymentCallback(): Promise<PaymentCredentials> {
  const { data } = await api.post<PaymentCredentials>(
    '/finance/payments/credentials/rotate-callback')
  return data
}

/* ---------------- Property modules (what a property is entitled to) -------- */

/**
 * One module and whether this property has it.
 *
 * The booking engine is the one that matters today: switching it on is what
 * makes a property publicly bookable, and switching it off takes it off sale
 * immediately — every public endpoint requires the entitlement to resolve the
 * property at all, so a property without it answers exactly as one that does
 * not exist.
 */
export interface PropertyModule {
  module_code: string
  label: string
  enabled: boolean
  /** When it was last switched on. Null if it never has been. */
  enabled_at: string | null
}

export async function listPropertyModules(
  propertyId: string,
): Promise<PropertyModule[]> {
  const { data } = await api.get<PropertyModule[]>(
    `/iam/properties/${propertyId}/modules`)
  return data
}

/**
 * Grant or withdraw a module for one property.
 *
 * Gated on `distribution:configure` — distribution is the hotel word for the
 * channels a property sells through, which is exactly what this is. Existing
 * bookings are untouched either way: this closes the shop door, it does not
 * cancel anybody.
 */
export async function setPropertyModule(
  propertyId: string, moduleCode: string, enabled: boolean,
): Promise<PropertyModule> {
  const { data } = await api.put<PropertyModule>(
    `/iam/properties/${propertyId}/modules/${moduleCode}`, { enabled })
  return data
}

/* ---------------- Channel partners (screen 024) ---------------- */

/**
 * One partner a property sells through, and the business it has brought.
 *
 * Note what is *not* here: a connection state, a sync timestamp, a mapping
 * status. No channel manager is integrated in this deployment, so none of
 * those can be answered — and a green "Connected · 2 min ago" backed by
 * nothing is the one thing that would stop somebody asking why no bookings
 * have arrived in a fortnight. `last_booking_at` is the honest version of the
 * same question.
 */
export interface ChannelPartner {
  id: string
  code: string
  name: string
  /** null until classified. Shown as "Unclassified", never guessed. */
  partner_type: 'online_channel' | 'travel_agent' | 'direct' | null
  partner_type_label: string | null
  status: string
  bookings: number
  last_booking_at: string | null
  revenue: string
}

export interface ChannelPartnerList {
  summary: {
    partners: number
    online_channels: number
    travel_agents: number
    /** Active partners that have never produced a booking. */
    never_booked: number
  }
  rows: ChannelPartner[]
  can_configure: boolean
  /** False until a channel manager is actually integrated. */
  channel_manager_connected: boolean
}

export async function listChannelPartners(
  organizationId: string,
  params: { partner_type?: string; status?: string; q?: string } = {},
): Promise<ChannelPartnerList> {
  const { data } = await api.get<ChannelPartnerList>(
    '/booking/channel-partners',
    { params: { organization_id: organizationId, ...params } })
  return data
}

export async function setChannelPartnerType(
  partnerId: string, partnerType: string | null,
): Promise<ChannelPartner> {
  const { data } = await api.put<ChannelPartner>(
    `/booking/channel-partners/${partnerId}/type`,
    { partner_type: partnerType })
  return data
}

/* ---------------- Channel manager link & partner connections ------------- */

/**
 * One of ours, and what the channel manager calls it.
 *
 * `external_id` empty means this room type or rate plan is not mapped yet.
 * Unmapped entries come back alongside mapped ones deliberately: a mapping
 * screen that only lists what is done cannot show what is left, which is the
 * only question anybody opens it with.
 */
export interface ChannelMapping {
  id: string
  local_id: string
  local_code: string
  local_name: string
  external_id: string
  external_name: string | null
}

/**
 * A property's link to the channel manager — one per property, whatever
 * number of OTAs hang off it.
 *
 * The aggregator normalises the ids: a booking from Agoda and one from
 * Booking.com both arrive carrying *its* room id, not the OTA's. So one set of
 * mappings serves every channel, and `external_property_id` is what routes an
 * arriving booking to this tenant.
 */
export interface ChannelLink {
  id: string
  property_id: string
  provider: string
  /** What the channel manager calls this property. */
  external_property_id: string | null
  currency: string | null
  rooms: ChannelMapping[]
  rates: ChannelMapping[]
  rooms_mapped: number
  rooms_total: number
  rates_mapped: number
  rates_total: number
  /** Null until the first push. A timestamp hours old on a property meant to
   *  sync every minute is the whole diagnosis. */
  last_pushed_at: string | null
  last_push_status: string | null
  last_push_detail: string | null
  /** The structural half, kept true by a background sweep rather than by
   *  anybody pressing anything. The push fields above say whether the numbers
   *  are current; these say whether the channel manager knows about this
   *  property and all of its rooms at all. */
  last_provisioned_at: string | null
  last_provision_status: string | null
  last_provision_detail: string | null
}

/** Null when this property has no link yet. */
export async function getChannelLink(
  propertyId: string,
): Promise<ChannelLink | null> {
  const { data } = await api.get<ChannelLink | null>('/booking/channel-links',
    { params: { property_id: propertyId } })
  return data
}

/** Create or update the link. 409 if another property claims that id. */
export async function saveChannelLink(body: {
  property_id: string
  external_property_id?: string | null
  currency?: string | null
}): Promise<ChannelLink> {
  const { data } = await api.put<ChannelLink>('/booking/channel-links', body)
  return data
}

/**
 * Record what the channel manager calls our rooms and rate plans.
 *
 * Sent whole rather than one pair at a time — mapping is done in a sitting
 * with the channel's extranet open. An empty `external_id` removes that pair.
 * A 409 means two of ours were pointed at one of theirs, which would make an
 * arriving booking ambiguous.
 */
export async function setChannelMappings(
  linkId: string,
  body: {
    rooms?: { local_id: string; external_id: string; external_name?: string | null }[]
    rates?: { local_id: string; external_id: string; external_name?: string | null }[]
  },
): Promise<ChannelLink> {
  const { data } = await api.put<ChannelLink>(
    `/booking/channel-links/${linkId}/mappings`, body)
  return data
}

/** One OTA, and how far along the chain it actually is. */
export interface OtaRow {
  partner_id: string
  partner_name: string
  commission_percent: string | null
  payment_model: string | null
  /** Whether a channel for this OTA exists at the channel manager at all. */
  channel_exists: boolean
  /** And whether it is switched on there. */
  channel_active: boolean
  channel_title: string | null
  /** The OTA's own id for this hotel, as configured at the channel manager —
   *  which is where it lives, not here. */
  hotel_id: string | null
  /** `terms_only`, `configured`, `live`, or `unknown` when unreachable. */
  state: string
  detail: string
}

/**
 * Whether this property is genuinely selling through its OTAs.
 *
 * Asked of the channel manager rather than inferred from our own tables. A
 * partner row records what an OTA charges and talks to nobody, and reading it
 * as "connected" is what told a hotel it was live on Agoda when no Agoda
 * channel existed anywhere.
 */
export interface OtaStatus {
  channel_manager_configured: boolean
  property_linked: boolean
  external_property_id: string | null
  rooms_mapped: number
  rooms_total: number
  rates_mapped: number
  rates_total: number
  /** Set when the channel manager could not be reached — the rows are then
   *  what we know locally, and absence must not be read as "no". */
  unreachable: string | null
  rows: OtaRow[]
}

export async function getOtaStatus(propertyId: string): Promise<OtaStatus> {
  const { data } = await api.get<OtaStatus>('/booking/channel-links/ota-status',
    { params: { property_id: propertyId } })
  return data
}

/** What the OTA said when asked whether it knows this hotel id. */
export interface ConnectionTest {
  /** `ok` — the OTA recognised it. `rejected` — it did not, and this id will
   *  never work. `unverified` — nobody could say, which is not the same
   *  thing: some channels have no test at all. */
  verdict: 'ok' | 'rejected' | 'unverified'
  message: string
}

/**
 * Ask the OTA, through the channel manager, whether this is really their id
 * for this hotel.
 *
 * Requiring the field only proves somebody typed something. This is the only
 * party that can say whether they typed the right thing — and it answers
 * while they are still looking at the field, rather than as a silent failure
 * to sell three weeks later.
 */
/** One of the OTA's own rates under one of its rooms, as the OTA lists it. */
export interface OtaRate { code: string; title: string | null; occupancy: number | null }
export interface OtaRoom { code: string; title: string | null; rates: OtaRate[] }

/** An OTA room/rate sold as one of this property's rate plans. */
export interface OtaPair {
  ota_room_code: string
  ota_rate_code: string
  rate_plan_id: string
  occupancy?: number | null
}

export interface OtaMapping {
  connection_id: string
  channel_id: string
  channel: string
  live: boolean
  ota_rooms: OtaRoom[]
  /** Why the OTA's rooms could not be listed -- usually not authorised yet. */
  ota_rooms_error: string | null
  plans: { rate_plan_id: string; code: string; name: string;
           room_type_name: string | null; occupancy: number | null }[]
  pairs: OtaPair[]
  /** Pairs on the channel naming a plan this property does not own. */
  foreign_pairs: number
}

export async function getOtaMapping(connectionId: string): Promise<OtaMapping> {
  const { data } = await api.get<OtaMapping>(
    `/booking/channel-connections/${connectionId}/ota-mapping`)
  return data
}

export async function setOtaMapping(
  connectionId: string, pairs: OtaPair[],
): Promise<OtaMapping> {
  const { data } = await api.put<OtaMapping>(
    `/booking/channel-connections/${connectionId}/ota-mapping`, { pairs })
  return data
}

export async function setOtaLive(
  connectionId: string, live: boolean,
): Promise<OtaMapping> {
  const { data } = await api.post<OtaMapping>(
    `/booking/channel-connections/${connectionId}/go-live`, { live })
  return data
}

export async function testOtaHotelId(
  partnerId: string, otaHotelId: string,
): Promise<ConnectionTest> {
  const { data } = await api.post<ConnectionTest>(
    '/booking/channel-connections/test', null,
    { params: { partner_id: partnerId, ota_hotel_id: otaHotelId } })
  return data
}

/** What setting a property up at the channel manager actually did. */
export interface ProvisionResult {
  /** `ok`, `partial` or `failed`. Partial is the common one: the property and
   *  most rooms went across, and one room type has no rate plan to sell. */
  status: string
  external_property_id: string | null
  created_property: boolean
  rooms_created: number
  rooms_mapped: number
  rates_created: number
  rates_mapped: number
  webhook_registered: boolean
  /** OTA channels built at the channel manager this run. */
  channels_created: number
  /** OTAs still waiting for the hotel's own id from that OTA. */
  channels_pending: number
  /** Named, not counted — "Sea View Suite: no rate plan covers this room
   *  type" tells somebody what to do; "2 problems" does not. */
  problems: string[]
}

/**
 * Set this property up at the channel manager: create it there, mirror every
 * room type, give each one a rate plan, register the booking webhook, write
 * the mappings.
 *
 * Runs by itself when a property goes live. Exposed here because it is also
 * how a property catches up after a room type is added, and how somebody
 * retries one that half-failed — every step asks what already exists and
 * creates only what is missing, so running it twice is harmless.
 */
export async function provisionChannelLink(
  propertyId: string,
): Promise<ProvisionResult> {
  const { data } = await api.post<ProvisionResult>(
    '/booking/channel-links/provision', null,
    { params: { property_id: propertyId } })
  return data
}

/** One thing at the channel manager that ours could be paired with. */
export interface MappingCandidate {
  id: string
  title: string
  /** For a rate plan, the channel manager room type it belongs to. */
  room_type_id: string | null
}

export interface MappingRate {
  local_id: string
  local_code: string
  local_name: string
  external_id: string
  external_name: string | null
}

/** A room type and the rate plans that sit under it. */
export interface MappingRoom extends MappingRate {
  rates: MappingRate[]
}

/**
 * Everything the partner edit screen needs, in one call.
 *
 * One call rather than four on purpose: fetched separately they arrive at
 * different moments, and a half-drawn mapping tree is where somebody pairs
 * the wrong two.
 */
export interface MappingEditor {
  link_id: string
  property_id: string
  property_name: string
  currency: string | null
  timezone: string | null
  external_property_id: string | null
  rooms: MappingRoom[]
  rooms_mapped: number
  rooms_total: number
  rates_mapped: number
  rates_total: number
  last_pushed_at: string | null
  last_push_status: string | null
  last_push_detail: string | null
  /** What this property sends. Property-wide, not per OTA: one feed goes to
   *  the channel manager and it fans out, so there is no point at which
   *  availability could be withheld from one channel and not another. */
  send_availability: boolean
  send_rates: boolean
  send_restrictions: boolean
  notify_on_failure: boolean
  /** Whether the channel manager is pointed back at us for this property.
   *  Not the same as an OTA being live: the delivery path can be ready with
   *  nothing yet sending down it. */
  webhook_registered: boolean
  candidate_rooms: MappingCandidate[]
  candidate_rates: MappingCandidate[]
  /** Set when the channel manager could not be reached — the dropdowns are
   *  then empty for a reason the screen must state. */
  unreachable: string | null
}

/** Change what this property sends. Returns the editor, freshly read. */
export async function setSyncSettings(
  linkId: string,
  body: {
    send_availability: boolean; send_rates: boolean
    send_restrictions: boolean; notify_on_failure: boolean
  },
): Promise<MappingEditor> {
  const { data } = await api.put<MappingEditor>(
    `/booking/channel-links/${linkId}/sync-settings`, body)
  return data
}

export async function getMappingEditor(
  linkId: string,
): Promise<MappingEditor> {
  const { data } = await api.get<MappingEditor>(
    `/booking/channel-links/${linkId}/mapping-editor`)
  return data
}

/** Send this property's rates and availability to the channel manager now. */
/** One request sent to the channel manager, with the task ids it returned. */
export interface ChannelSyncLogRow {
  id: string
  created_at: string
  endpoint: string
  trigger: 'change' | 'full_sync' | 'manual'
  outcome: 'sent' | 'failed' | 'throttled'
  status_code: number | null
  value_count: number
  date_from: string | null
  date_to: string | null
  task_ids: string[]
  summary: string | null
  error: string | null
  request_excerpt: string | null
}

export async function getChannelSyncLog(
  linkId: string, limit = 50,
): Promise<ChannelSyncLogRow[]> {
  const { data } = await api.get<ChannelSyncLogRow[]>(
    `/booking/channel-links/${linkId}/sync-log`, { params: { limit } })
  return data
}

/** One booking revision the channel manager delivered. */
export interface ChannelBookingEvent {
  revision_id: string
  event_type: string | null
  outcome: string
  status: string | null
  ota_name: string | null
  ota_reservation_code: string | null
  reservation_id: string | null
  reservation_number: string | null
  detail: string | null
  acknowledged: boolean
  created_at: string
  updated_at: string | null
  can_replay: boolean
}

export async function listChannelBookingEvents(
  propertyId: string, limit = 50,
): Promise<ChannelBookingEvent[]> {
  const { data } = await api.get<ChannelBookingEvent[]>(
    '/booking/channels/events', { params: { property_id: propertyId, limit } })
  return data
}

export async function replayChannelBookingEvent(
  revisionId: string, propertyId: string,
): Promise<{ status: string; reservation_number?: string }> {
  const { data } = await api.post<{ status: string; reservation_number?: string }>(
    `/booking/channels/events/${encodeURIComponent(revisionId)}/replay`, null,
    { params: { property_id: propertyId } })
  return data
}

export async function pushChannelLink(
  linkId: string,
): Promise<{ status: string; detail: string }> {
  const { data } = await api.post<{ status: string; detail: string }>(
    `/booking/channel-links/${linkId}/push`)
  return data
}

/**
 * One OTA on one property. Only what genuinely differs per channel lives
 * here — commission and payment model. Booking.com at 15% hotel-collect and
 * Agoda at 18% channel-collect is the distinction this exists to record.
 */
export interface ChannelConnection {
  id: string
  partner_id: string
  partner_name: string
  partner_code: string
  property_id: string
  commission_percent: string | null
  payment_model: string | null
  status: string
  notes: string | null
  /** This OTA's own id for this hotel, from the hotel's contract with them. */
  ota_hotel_id: string | null
  /** Set once a channel for this OTA exists at the channel manager. */
  external_channel_id: string | null
}

export interface ChannelConnectionInput {
  partner_id: string
  property_id: string
  commission_percent?: number | null
  payment_model?: string | null
  notes?: string | null
  /** The OTA's own id for this hotel. Not the channel manager's id — that one
   *  is created for you and routes arriving bookings. */
  ota_hotel_id?: string | null
}

export async function listChannelConnections(
  propertyId: string,
): Promise<ChannelConnection[]> {
  const { data } = await api.get<ChannelConnection[]>(
    '/booking/channel-connections', { params: { property_id: propertyId } })
  return data
}

/** 409 when this partner is already connected to this property. */
export async function createChannelConnection(
  body: ChannelConnectionInput,
): Promise<ChannelConnection> {
  const { data } = await api.post<ChannelConnection>(
    '/booking/channel-connections', body)
  return data
}

export async function updateChannelConnection(
  id: string, body: ChannelConnectionInput,
): Promise<ChannelConnection> {
  const { data } = await api.put<ChannelConnection>(
    `/booking/channel-connections/${id}`, body)
  return data
}

/**
 * Remove a connection. Allowed, unlike removing a partner — no booking points
 * at a connection, so nothing is orphaned.
 */
export async function deleteChannelConnection(id: string): Promise<void> {
  await api.delete(`/booking/channel-connections/${id}`)
}

/* ---------------- Guest Profile (screen 009) ---------------- */

export interface ProfileStat {
  total_stays: number
  lifetime_value: string
  nights_total: number
  average_nights: number
  cancelled: number
}

export interface ProfileStay {
  reservation_id: string
  unit_id: string
  number: string
  property_name: string | null
  room_type: string | null
  room: string | null
  arrival_date: string
  departure_date: string
  nights: number
  adults: number
  children: number
  status: string
  source: string | null
  charges: string
  paid: string
}

export interface ProfileActivity {
  at: string
  kind: 'booking' | 'checkin' | 'checkout' | 'move' | 'document' | 'note'
  title: string
  detail: string | null
  reservation_number: string | null
}

export interface GuestPreference { id: string; kind: string; label: string }
export interface GuestNote {
  id: string; body: string; created_at: string; author: string | null
}

export interface GuestProfile {
  id: string
  reference: string | null
  full_name: string
  title: string | null
  email: string | null
  phone: string | null
  city: string | null
  state: string | null
  country: string | null
  nationality: string | null
  address_line: string | null
  occupation: string | null
  date_of_birth: string | null
  id_type: string | null
  id_verified: boolean
  member_since: string
  initials: string
  in_house: boolean
  stats: ProfileStat
  last_stay: string | null
  next_stay: string | null
  current: ProfileStay | null
  upcoming: ProfileStay | null
  stays: ProfileStay[]
  activity: ProfileActivity[]
  preferences: GuestPreference[]
  tags: string[]
  notes: GuestNote[]
  documents: number
  can_edit: boolean
  can_book: boolean
  /** Panels the mockup shows that have no data source, keyed by panel. */
  unavailable: Record<string, string>
}

export async function getGuestProfile(
  organizationId: string, guestId: string,
): Promise<GuestProfile> {
  const { data } = await api.get<GuestProfile>(
    `/booking/guests/${guestId}/profile`,
    { params: { organization_id: organizationId } })
  return data
}

export async function addGuestNote(
  organizationId: string, guestId: string, body: string,
): Promise<GuestNote> {
  const { data } = await api.post<GuestNote>(
    `/booking/guests/${guestId}/notes`, { body },
    { params: { organization_id: organizationId } })
  return data
}

export async function addGuestTag(
  organizationId: string, guestId: string, tag: string,
): Promise<string[]> {
  const { data } = await api.post<string[]>(
    `/booking/guests/${guestId}/tags`, { tag },
    { params: { organization_id: organizationId } })
  return data
}

export async function removeGuestTag(
  organizationId: string, guestId: string, tag: string,
): Promise<string[]> {
  const { data } = await api.delete<string[]>(
    `/booking/guests/${guestId}/tags`,
    { params: { organization_id: organizationId, tag } })
  return data
}

export async function setGuestPreferences(
  organizationId: string, guestId: string,
  items: { kind: string; label: string }[],
): Promise<GuestPreference[]> {
  const { data } = await api.put<GuestPreference[]>(
    `/booking/guests/${guestId}/preferences`, { items },
    { params: { organization_id: organizationId } })
  return data
}

/* ---------------- Hotel ledger report ---------------- */

export interface LedgerLine {
  key: string; label: string
  /** False when nothing in this deployment writes to this ledger yet. */
  available: boolean
  reason: string | null
  opening: string; debit: string; credit: string; closing: string
  /** Accounts this ledger touched inside the window — movement, not standing. */
  accounts: number
  /** Accounts still carrying a balance, over all time. What the table lists. */
  open_accounts: number
}
export interface LedgerGuestRow {
  folio_id: string; folio_no: string
  reservation_id: string | null; confirmation_no: string | null
  guest_name: string | null; room_code: string | null
  arrival_date: string | null; departure_date: string | null
  stay_status: string | null
  room_charges: string; other_charges: string
  payments: string; balance: string
}
export interface LedgerJournalRow {
  entry_id: string; business_date: string; posted_at: string
  folio_id: string; folio_no: string
  reservation_id: string | null; confirmation_no: string | null
  guest_name: string | null
  particulars: string; source_type: string
  debit: string; credit: string; running: string
}
export interface LedgerCheck {
  opening: string; debit: string; credit: string; closing: string
  ties: boolean
  /** One line, sized so the tile matches the summary table's height. */
  note: string
  /** The whole reasoning, shown on hover. */
  note_long: string
}
export interface LedgerOption { value: string; label: string }
export interface LedgerFilterOptions {
  ledger_types: LedgerOption[]
  transaction_codes: LedgerOption[]
  payment_methods: LedgerOption[]
  markets: LedgerOption[]
  rooms: LedgerOption[]
}
/** Everything the filter panel can narrow by. Empty strings are omitted. */
export interface LedgerQuery {
  date_from?: string; date_to?: string
  ledger_type?: string; q?: string; room?: string
  confirmation_no?: string; folio_no?: string
  transaction_code?: string; payment_method?: string; market?: string
}

export interface HotelLedgerReport {
  property_id: string; property_name: string; currency: string
  date_from: string; date_to: string
  ledgers: LedgerLine[]
  hotel: LedgerLine
  check: LedgerCheck
  /** The accounts of whichever ledger is selected. */
  guest_rows: LedgerGuestRow[]
  guest_total: string
  /** Which ledger those rows belong to. */
  rows_ledger: string
  journal: LedgerJournalRow[]
  journal_truncated: boolean
  options: LedgerFilterOptions
}

export async function getLedgerReport(
  propertyId: string, query: LedgerQuery = {},
): Promise<HotelLedgerReport> {
  // Blank fields are dropped rather than sent as empty strings: the server
  // treats a present-but-empty parameter as a filter, and "" matches nothing.
  const params: Record<string, string> = { property_id: propertyId }
  for (const [k, v] of Object.entries(query)) if (v) params[k] = v
  const { data } = await api.get<HotelLedgerReport>(
    '/finance/reports/ledger', { params })
  return data
}

/* ---------------- Back office reports ---------------- */

export type ReportCell = string | number | null
export type ReportColumnKind =
  | 'text' | 'status' | 'date' | 'datetime' | 'money' | 'number' | 'percent'
export interface ReportColumn { key: string; label: string; kind: ReportColumnKind }
/** A tile's figure, computed in the browser over the rows on screen. `where`
 *  and `value` narrow the rows first (`negate` inverts that). */
export interface ReportMetric {
  label: string
  op: 'count' | 'sum' | 'sumcols' | 'difference' | 'unique' | 'max' | 'nonzero' | 'ratio'
  col: number | number[] | null
  den: number | null
  where: number | null
  value: string | null
  negate: boolean
  fmt: 'money' | 'number' | 'percent'
  sub: string
}
export interface ReportParam { key: string; label: string; kind: 'number' | 'text' }

export interface BackOfficeReportInfo {
  /** The report's number in the back-office design; 0 for the Hotel Ledger. */
  number: number
  slug: string; title: string; category: string; description: string
  available: boolean
  /** Why it cannot be opened yet, in full. */
  unavailable_reason: string | null
  /** The same, in two or three words, for the tile badge. */
  planned: string | null
  /** Set for reports with a screen of their own rather than this one. */
  href: string | null
}

export interface BackOfficeReportResult extends BackOfficeReportInfo {
  basis: string
  dates: 'range' | 'single' | 'none'
  date_from: string; date_to: string; business_date: string
  property_name: string; currency: string
  columns: ReportColumn[]
  metrics: ReportMetric[]
  /** Column indexes the filter panel offers a dropdown for. */
  filters: number[]
  /** Column indexes totalled in the footer. */
  totals: number[]
  params: ReportParam[]
  param_values: Record<string, string | number>
  note: string
  empty_text: string
  rows: ReportCell[][]
  /** The reservation behind each row, where there is one. */
  links: (string | null)[]
  truncated: boolean
}

export async function listBackOfficeReports(
  propertyId: string,
): Promise<BackOfficeReportInfo[]> {
  const { data } = await api.get<BackOfficeReportInfo[]>(
    '/finance/reports/backoffice', { params: { property_id: propertyId } })
  return data
}

export async function runBackOfficeReport(
  slug: string, propertyId: string, query: Record<string, string> = {},
): Promise<BackOfficeReportResult> {
  // Blank values are dropped, as for the ledger: "" is not "no filter".
  const params: Record<string, string> = { property_id: propertyId }
  for (const [k, v] of Object.entries(query)) if (v) params[k] = v
  const { data } = await api.get<BackOfficeReportResult>(
    `/finance/reports/backoffice/${encodeURIComponent(slug)}`, { params })
  return data
}

/* ---------------- Work orders ---------------- */

export interface LabelledValue { value: string; label: string }

export interface WorkOrder {
  id: string
  number: string
  title: string
  description: string | null
  room_id: string | null
  room_code: string | null
  location: string | null
  /** Room and location together, for display. */
  where: string
  category: string; category_label: string
  priority: string; priority_label: string
  status: string; status_label: string
  assigned_to: string | null
  assigned_name: string | null
  due_date: string | null
  overdue: boolean
  cost: string | null
  resolution: string | null
  reported_by_name: string | null
  created_at: string
  started_at: string | null
  completed_at: string | null
}

export interface WorkOrderIn {
  title: string
  description?: string | null
  room_id?: string | null
  location?: string | null
  category: string
  priority: string
  status: string
  assigned_to?: string | null
  due_date?: string | null
  cost?: number | null
  resolution?: string | null
}

export interface WorkOrderList {
  rows: WorkOrder[]
  total: number
  /** Per status for the whole property, plus `overdue`. */
  counts: Record<string, number>
  assignees: { id: string; name: string }[]
  rooms: { id: string; code: string }[]
  categories: LabelledValue[]
  priorities: LabelledValue[]
  statuses: LabelledValue[]
  can_create: boolean
  can_edit: boolean
}

export async function listWorkOrders(
  propertyId: string,
  params: { status?: string; priority?: string; q?: string } = {},
): Promise<WorkOrderList> {
  const { data } = await api.get<WorkOrderList>('/booking/work-orders',
    { params: { property_id: propertyId, ...params } })
  return data
}

export async function createWorkOrder(
  propertyId: string, body: WorkOrderIn,
): Promise<WorkOrder> {
  const { data } = await api.post<WorkOrder>('/booking/work-orders', body,
    { params: { property_id: propertyId } })
  return data
}

export async function updateWorkOrder(
  propertyId: string, id: string, body: WorkOrderIn,
): Promise<WorkOrder> {
  const { data } = await api.patch<WorkOrder>(`/booking/work-orders/${id}`, body,
    { params: { property_id: propertyId } })
  return data
}

/* ---------------- Expense vouchers ---------------- */

export interface ExpenseVoucher {
  id: string
  voucher_no: string
  expense_date: string
  payee: string
  category: string; category_label: string
  description: string | null
  amount: string; tax_amount: string; total: string
  method: string; method_label: string
  reference: string | null
  room_id: string | null
  room_code: string | null
  status: string; status_label: string
  decided_by_name: string | null
  decided_at: string | null
  decision_note: string | null
  paid_at: string | null
  created_by_name: string | null
  created_at: string
}

export interface ExpenseVoucherIn {
  expense_date: string
  payee: string
  category: string
  description?: string | null
  amount: number
  tax_amount: number
  method: string
  reference?: string | null
  room_id?: string | null
}

export interface ExpenseVoucherList {
  rows: ExpenseVoucher[]
  total: number
  /** Voucher totals per status over the rows returned. */
  totals: Record<string, string>
  categories: LabelledValue[]
  methods: LabelledValue[]
  statuses: LabelledValue[]
  rooms: { id: string; code: string }[]
  can_create: boolean
  can_edit: boolean
  can_approve: boolean
}

export async function listExpenseVouchers(
  propertyId: string,
  params: { status?: string; category?: string; q?: string } = {},
): Promise<ExpenseVoucherList> {
  const { data } = await api.get<ExpenseVoucherList>('/finance/expenses',
    { params: { property_id: propertyId, ...params } })
  return data
}

export async function createExpenseVoucher(
  propertyId: string, body: ExpenseVoucherIn,
): Promise<ExpenseVoucher> {
  const { data } = await api.post<ExpenseVoucher>('/finance/expenses', body,
    { params: { property_id: propertyId } })
  return data
}

export async function updateExpenseVoucher(
  propertyId: string, id: string, body: ExpenseVoucherIn,
): Promise<ExpenseVoucher> {
  const { data } = await api.patch<ExpenseVoucher>(`/finance/expenses/${id}`, body,
    { params: { property_id: propertyId } })
  return data
}

export async function decideExpenseVoucher(
  propertyId: string, id: string,
  body: { action: 'approve' | 'reject' | 'pay' | 'cancel'; note?: string | null },
): Promise<ExpenseVoucher> {
  const { data } = await api.post<ExpenseVoucher>(`/finance/expenses/${id}/decision`,
    body, { params: { property_id: propertyId } })
  return data
}

/* ---------------- Unit owners ---------------- */

export interface OwnerContract {
  id: string
  owner_id: string
  room_id: string
  room_code: string | null
  room_type: string | null
  management_fee_percent: string
  start_date: string
  end_date: string | null
  /** Today falls inside the contract's dates. */
  current: boolean
  notes: string | null
}

export interface UnitOwner {
  id: string
  name: string
  email: string | null
  phone: string | null
  pan: string | null
  gstin: string | null
  bank_details: string | null
  status: string
  notes: string | null
  contracts: OwnerContract[]
}

export interface UnitOwnerIn {
  name: string
  email?: string | null
  phone?: string | null
  pan?: string | null
  gstin?: string | null
  bank_details?: string | null
  status: string
  notes?: string | null
}

export interface OwnerContractIn {
  room_id: string
  management_fee_percent: number
  start_date: string
  end_date?: string | null
  notes?: string | null
}

export interface UnitOwnerList {
  rows: UnitOwner[]
  rooms: { id: string; code: string; room_type: string | null; owner: string | null }[]
  can_edit: boolean
}

export async function listUnitOwners(propertyId: string): Promise<UnitOwnerList> {
  const { data } = await api.get<UnitOwnerList>('/finance/unit-owners',
    { params: { property_id: propertyId } })
  return data
}

export async function createUnitOwner(
  propertyId: string, body: UnitOwnerIn,
): Promise<UnitOwnerList> {
  const { data } = await api.post<UnitOwnerList>('/finance/unit-owners', body,
    { params: { property_id: propertyId } })
  return data
}

export async function updateUnitOwner(
  propertyId: string, id: string, body: UnitOwnerIn,
): Promise<UnitOwnerList> {
  const { data } = await api.patch<UnitOwnerList>(`/finance/unit-owners/${id}`, body,
    { params: { property_id: propertyId } })
  return data
}

export async function createOwnerContract(
  propertyId: string, ownerId: string, body: OwnerContractIn,
): Promise<UnitOwnerList> {
  const { data } = await api.post<UnitOwnerList>(
    `/finance/unit-owners/${ownerId}/contracts`, body,
    { params: { property_id: propertyId } })
  return data
}

export async function updateOwnerContract(
  propertyId: string, id: string, body: OwnerContractIn,
): Promise<UnitOwnerList> {
  const { data } = await api.patch<UnitOwnerList>(
    `/finance/unit-owner-contracts/${id}`, body,
    { params: { property_id: propertyId } })
  return data
}

/* --------------------------------------------------------------------------
 * Guest services — the priced list, and putting an order on a guest's bill
 *
 * The charge endpoint already existed; what it could not carry was *what the
 * charge was for*. These add the menu behind it, so a folio line says "Masala
 * Dosa x2" instead of "restaurant 240".
 * ------------------------------------------------------------------------ */
/** A department the ledger can price tax for. Fetched, never hardcoded: the
 *  list belongs to the tax engine, and a copy here would drift from it. */
export interface ServiceDepartment { value: string; label: string }

/** A category as *this property* names it. Every tenant has their own. */
export interface ServiceCategory {
  id: string; name: string
  bills_as: string; bills_as_label: string
  sort_order: number; is_active: boolean; item_count: number
}

export interface ServiceItem {
  id: string; code: string; name: string
  category_id: string; category_label: string
  price: string; currency: string
  is_active: boolean; sort_order: number
}

export async function listServiceDepartments(): Promise<ServiceDepartment[]> {
  const { data } = await api.get<ServiceDepartment[]>(
    '/finance/service-categories/departments')
  return data
}

export async function listServiceCategories(
  propertyId: string, includeInactive = false,
): Promise<ServiceCategory[]> {
  const { data } = await api.get<ServiceCategory[]>(
    '/finance/service-categories',
    { params: { property_id: propertyId, include_inactive: includeInactive } })
  return data
}

export async function createServiceCategory(body: {
  property_id: string; name: string; bills_as: string; sort_order?: number
}): Promise<ServiceCategory> {
  const { data } = await api.post<ServiceCategory>(
    '/finance/service-categories', body)
  return data
}

export async function updateServiceCategory(
  categoryId: string, propertyId: string,
  body: Partial<{ name: string; bills_as: string; sort_order: number; is_active: boolean }>,
): Promise<ServiceCategory> {
  const { data } = await api.patch<ServiceCategory>(
    `/finance/service-categories/${categoryId}`, body,
    { params: { property_id: propertyId } })
  return data
}

export async function deleteServiceCategory(
  categoryId: string, propertyId: string,
): Promise<void> {
  await api.delete(`/finance/service-categories/${categoryId}`,
    { params: { property_id: propertyId } })
}

export interface InHouseGuest {
  reservation_id: string; reservation_number: string | null
  folio_id: string | null; guest_name: string | null
  room_code: string | null; departure_date: string | null
  balance: string; currency: string
}

export interface ServiceOrderLine {
  item_name: string
  // The category name as it stood when the guest was billed, not a live
  // lookup — renaming a category must not rewrite a paid bill.
  category_label: string
  quantity: string; unit_price: string; amount: string
}

export interface ServiceOrder {
  id: string; business_date: string; posted_at: string
  note: string | null; total: string; currency: string
  lines: ServiceOrderLine[]
}

export async function listServiceItems(
  propertyId: string, includeInactive = false,
): Promise<ServiceItem[]> {
  const { data } = await api.get<ServiceItem[]>('/finance/service-items',
    { params: { property_id: propertyId, include_inactive: includeInactive } })
  return data
}

export async function createServiceItem(body: {
  property_id: string; code: string; name: string
  category_id: string; price: number
  is_active?: boolean; sort_order?: number
}): Promise<ServiceItem> {
  const { data } = await api.post<ServiceItem>('/finance/service-items', body)
  return data
}

export async function updateServiceItem(
  itemId: string, propertyId: string,
  body: Partial<{
    code: string; name: string; category_id: string
    price: number; is_active: boolean; sort_order: number
  }>,
): Promise<ServiceItem> {
  const { data } = await api.patch<ServiceItem>(
    `/finance/service-items/${itemId}`, body,
    { params: { property_id: propertyId } })
  return data
}

export async function retireServiceItem(
  itemId: string, propertyId: string,
): Promise<void> {
  await api.delete(`/finance/service-items/${itemId}`,
    { params: { property_id: propertyId } })
}

export async function listInHouseGuests(
  propertyId: string, q?: string,
): Promise<InHouseGuest[]> {
  const { data } = await api.get<InHouseGuest[]>(
    '/finance/service-orders/in-house',
    { params: { property_id: propertyId, q: q || undefined } })
  return data
}

export async function listServiceOrders(
  propertyId: string, folioId: string,
): Promise<ServiceOrder[]> {
  const { data } = await api.get<ServiceOrder[]>('/finance/service-orders',
    { params: { property_id: propertyId, folio_id: folioId } })
  return data
}

export async function postServiceOrder(body: {
  property_id: string; folio_id: string; reservation_id?: string | null
  /** Omit it: the server stamps the day the property is trading. */
  business_date?: string; note?: string | null
  lines: { item_id: string; quantity: number }[]
  // Generated once per Post and repeated on every retry of it. Without it a
  // slow network and an impatient second tap bill the guest twice — which is
  // exactly what happened before this existed.
  client_key: string
}): Promise<ServiceOrder> {
  const { data } = await api.post<ServiceOrder>('/finance/service-orders', body)
  return data
}

/* --------------------------------------------------------------------------
 * Publishing rate rules into the rate calendar
 *
 * The step that used to be missing. Rules described weekend and festival
 * pricing; nothing carried it out, because every path that quotes money reads
 * the rate calendar and nothing wrote rules into it. These do.
 * ------------------------------------------------------------------------ */
export interface PublishDay {
  stay_date: string
  room_type_id: string
  room_type_name: string
  base_rate: string
  current_rate: string | null
  new_rate: string
  /** write = a rule prices it; clear = a rule no longer does, so the stored
   *  rate is removed and the night follows the room's base rate again;
   *  none = correctly absent; skip = hand-typed or unpriceable. */
  action: 'write' | 'clear' | 'none' | 'skip'
  /** Percent of sellable rooms already sold for this night, where known. */
  occupancy: number | null
  applied: string[]
  skipped: string | null
}

export interface PublishPreview {
  days: PublishDay[]
  total_days: number
  changing: number
  protected: number
  unchanged: number
  clearing: number
}

export interface PublishResult {
  written: number
  cleared: number
  protected: number
  message: string
}

export interface PublishIn {
  date_from: string
  date_to: string
  room_type_ids?: string[]
  rule_id?: string | null
}

export async function previewRulePublish(
  propertyId: string, body: PublishIn,
): Promise<PublishPreview> {
  const { data } = await api.post<PublishPreview>(
    `/booking/properties/${propertyId}/rate-rules/preview-publish`, body)
  return data
}

export async function publishRules(
  propertyId: string, body: PublishIn,
): Promise<PublishResult> {
  const { data } = await api.post<PublishResult>(
    `/booking/properties/${propertyId}/rate-rules/publish`, body)
  return data
}

/* --------------------------------------------------------------------------
 * Complimentary and house-use rooms
 *
 * Declared rather than inferred. The Complimentary Room Report used to guess
 * from a zero rate, which cannot tell a room given away from a rate nobody
 * typed, and carried nobody's name.
 * ------------------------------------------------------------------------ */
export interface CompReason { value: string; label: string }
export interface CompOptions {
  complimentary: CompReason[]
  house_use: CompReason[]
}
export interface CompState {
  reservation_unit_id: string
  comp_kind: 'complimentary' | 'house_use' | null
  comp_reason: string | null
  comp_reason_label: string | null
  comp_note: string | null
  comp_authorised_by: string | null
  comp_authorised_at: string | null
}

export async function getCompReasons(): Promise<CompOptions> {
  const { data } = await api.get<CompOptions>('/booking/complimentary/reasons')
  return data
}

export async function getComplimentary(
  unitId: string, propertyId: string,
): Promise<CompState> {
  const { data } = await api.get<CompState>(
    `/booking/reservation-units/${unitId}/complimentary`,
    { params: { property_id: propertyId } })
  return data
}

export async function markComplimentary(
  unitId: string, propertyId: string,
  body: { kind: 'complimentary' | 'house_use'; reason: string; note?: string | null },
): Promise<CompState> {
  const { data } = await api.post<CompState>(
    `/booking/reservation-units/${unitId}/complimentary`, body,
    { params: { property_id: propertyId } })
  return data
}

export async function clearComplimentary(
  unitId: string, propertyId: string,
): Promise<CompState> {
  const { data } = await api.delete<CompState>(
    `/booking/reservation-units/${unitId}/complimentary`,
    { params: { property_id: propertyId } })
  return data
}

/** The rates negotiated with one company.
 *
 * So taking a booking billed to that company can quote the agreed rate,
 * instead of relying on whoever is at the desk remembering it exists.
 */
export async function listAccountRatePlans(
  propertyId: string, commercialAccountId: string,
): Promise<RatePlan[]> {
  const { data } = await api.get<RatePlan[]>('/booking/rate-plans/for-account', {
    params: { property_id: propertyId, commercial_account_id: commercialAccountId },
  })
  return data
}

/** What a plan charges for a room whose own rate is `base`.
 *
 * The same arithmetic the rate calendar and the rule simulator use: a flat
 * rate replaces the room's rate outright, otherwise the adjustment is applied
 * to it. Kept in one function so a quote at the desk and the rate that
 * eventually posts cannot disagree.
 */
export function ratePlanPrice(plan: RatePlan, base: number): number {
  if (plan.flat_rate != null) return Number(plan.flat_rate)
  const value = Number(plan.adjustment_value ?? 0)
  const signed = plan.adjustment_direction === 'decrease' ? -value : value
  const out = plan.adjustment_type === 'percent'
    ? base * (1 + signed / 100)
    : base + signed
  return Math.max(0, Math.round(out * 100) / 100)
}

/* ---------------- Form C — foreign guest reporting (FRRO) ---------------- */
/**
 * Rule 14 of the Registration of Foreigners Rules 1992: a foreign guest's
 * arrival is reported to the Bureau of Immigration within 24 hours.
 *
 * `nationality_state` is three-valued on purpose. `unknown` means nobody
 * recorded a nationality — a data gap, not a missed filing — and it is kept
 * out of the overdue count so the register does not cry wolf.
 */
export interface FormCRow {
  reservation_unit_id: string
  reservation_number: string | null
  guest_name: string | null
  nationality: string | null
  nationality_state: 'foreign' | 'indian' | 'unknown'
  room_code: string | null
  arrival_date: string | null
  departure_date: string | null
  checked_in_at: string | null
  status: 'pending' | 'filed' | 'exempt'
  filed_at: string | null
  acknowledgement_no: string | null
  missing_count: number
  hours_left: number | null
}
export interface FormCRegister {
  rows: FormCRow[]
  pending: number
  overdue: number
  filed: number
  unknown: number
}
export interface FormCDetail {
  reservation_unit_id: string
  reservation_number: string | null
  room_code: string | null
  arrival_date: string | null
  departure_date: string | null
  status: string
  filed_at: string | null
  acknowledgement_no: string | null
  required: boolean
  missing: string[]
  exists: boolean
  full_name: string | null
  sex: string | null
  date_of_birth: string | null
  nationality: string | null
  passport_number: string | null
  passport_issue_place: string | null
  passport_issue_date: string | null
  passport_expiry_date: string | null
  visa_number: string | null
  visa_type: string | null
  visa_issue_place: string | null
  visa_issue_date: string | null
  visa_expiry_date: string | null
  arrived_in_india_on: string | null
  arrived_in_india_at: string | null
  address_in_india: string | null
  permanent_address: string | null
  purpose_of_visit: string | null
  next_destination: string | null
  notes: string | null
}

export async function getFormCRegister(
  propertyId: string, params: Record<string, string | undefined> = {},
): Promise<FormCRegister> {
  const { data } = await api.get<FormCRegister>('/booking/form-c/register',
    { params: { property_id: propertyId, ...params } })
  return data
}
export async function getFormC(
  unitId: string, propertyId: string,
): Promise<FormCDetail> {
  const { data } = await api.get<FormCDetail>(
    `/booking/reservation-units/${unitId}/form-c`,
    { params: { property_id: propertyId } })
  return data
}
export async function saveFormC(
  unitId: string, propertyId: string, body: Record<string, unknown>,
): Promise<FormCDetail> {
  const { data } = await api.put<FormCDetail>(
    `/booking/reservation-units/${unitId}/form-c`, body,
    { params: { property_id: propertyId } })
  return data
}
export async function fileFormC(
  unitId: string, propertyId: string, body: Record<string, unknown>,
): Promise<FormCDetail> {
  const { data } = await api.post<FormCDetail>(
    `/booking/reservation-units/${unitId}/form-c/file`, body,
    { params: { property_id: propertyId } })
  return data
}

/** Firm a block up, or let it go back to provisional.
 *
 * This is the switch that decides whether the rooms are off sale. Definite
 * takes them; tentative hands them back. Bookings already picked up are
 * untouched either way — a reservation does not become provisional because
 * the block it came from did.
 */
export async function setBlockCommitment(
  blockId: string, propertyId: string,
  commitment: 'tentative' | 'definite',
): Promise<GroupBlock> {
  const { data } = await api.post<GroupBlock>(
    `/booking/group-blocks/${blockId}/commitment`, { commitment },
    { params: { property_id: propertyId } })
  return data
}
