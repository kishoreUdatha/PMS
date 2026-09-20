/** Client for the platform's own operations: providers, domains, messaging,
 *  support, recovery, settings and analytics (screens 16, 18–21, 25–27).
 *
 *  Separate from api.ts for the same reason the routes are separate: api.ts
 *  administers *tenants*, and this administers the platform itself.
 *
 *  One convention worth naming. A provider secret is write-only end to end —
 *  no type here has a field that could hold a decrypted one, so a screen
 *  cannot accidentally render it, and `secret` is optional on save because
 *  omitting it means "leave the stored one alone". There is deliberately no
 *  way to read a secret back to put it in a form.
 */
import { api } from '../api'

const P = '/iam/platform'

// ------------------------------------------------- 16 provider setup ------

export interface ProviderConnection {
  id: string
  provider: string
  environment: 'sandbox' | 'production'
  label: string
  config: Record<string, string>
  status: string
  last_verified_at: string | null
  last_verify_detail: string | null
  rotated_at: string | null
  updated_at: string
  has_secret: boolean
  /** Last four of the *ciphertext*. Tells two rows apart; leaks nothing. */
  secret_hint: string | null
}

export interface ProviderCatalogueEntry {
  provider: string
  name: string
  kind: string
  secret_label: string
  fields: string[]
  environments: { environment: 'sandbox' | 'production'; configured: boolean }[]
}

export interface TenantGateway {
  provider: string
  enabled: boolean
  updated_at: string | null
  tenant_name: string
  tenant_code: string | null
  has_key: boolean
  has_webhook_secret: boolean
}

export interface Providers {
  catalogue: ProviderCatalogueEntry[]
  connections: ProviderConnection[]
  tenant_gateways: TenantGateway[]
  runtime: {
    mail_configured: boolean
    encryption_configured: boolean
    note: string
  }
}

export async function providers(): Promise<Providers> {
  const { data } = await api.get<Providers>(`${P}/providers`)
  return data
}

export async function saveProvider(body: {
  provider: string
  environment: 'sandbox' | 'production'
  label: string
  config?: Record<string, string>
  /** Omit to keep the stored secret. There is no way to read one back. */
  secret?: string
}): Promise<ProviderConnection> {
  const { data } = await api.post<ProviderConnection>(`${P}/providers`, body)
  return data
}

export async function verifyProvider(
  id: string,
): Promise<{ status: string; detail: string; missing: string[] }> {
  const { data } = await api.post(`${P}/providers/${id}/verify`)
  return data
}

// ------------------------------------------ 18 booking engine domains -----

export interface BookingDomain {
  id: string
  hostname: string
  status: 'pending' | 'verified' | 'live' | 'failed' | 'retired'
  verification_method: string
  verification_token: string
  verified_at: string | null
  tls_status: string
  tls_expires_at: string | null
  tls_days_left: number | null
  created_at: string
  property_id: string
  property_code: string
  property_name: string
  tenant_name: string
  tenant_code: string | null
}

export interface DomainsPage {
  domains: BookingDomain[]
  properties: {
    id: string; code: string; name: string
    tenant_name: string; domains: number
    /** Whether /book/{code} actually takes bookings. `not_configured` and
     *  `disabled` both serve the page and then refuse — booking-core folds
     *  the entitlement into the property lookup, so an unentitled property
     *  is indistinguishable from one that does not exist. */
    booking_engine: 'enabled' | 'disabled' | 'not_configured'
    room_types: number
  }[]
  hosted_pattern: string
}

export async function domains(): Promise<DomainsPage> {
  const { data } = await api.get<DomainsPage>(`${P}/domains`)
  return data
}

export async function addDomain(body: {
  property_id: string; hostname: string
}): Promise<BookingDomain & { instruction: string }> {
  const { data } = await api.post(`${P}/domains`, body)
  return data
}

export async function setDomainState(
  id: string, action: 'verify' | 'activate' | 'retire',
): Promise<BookingDomain> {
  const { data } = await api.post(`${P}/domains/${id}/state`, { action })
  return data
}

// ------------------------------------------------------- 19 messaging ----

export interface MessageTemplate {
  id: string
  code: string
  version: number
  name: string
  channel: string
  subject: string | null
  status: string
  variables: string[]
  updated_at: string
  body_length: number
  has_html: boolean
  sent: number
}

export interface Delivery {
  id: string
  template_code: string | null
  channel: string
  recipient: string
  subject: string | null
  status: string
  detail: string | null
  attempts: number
  sent_at: string
  tenant_name: string | null
}

export interface MessagingPage {
  templates: MessageTemplate[]
  deliveries: Delivery[]
  totals: {
    total: number; sent: number; failed: number; last_sent: string | null
  }
  window_days: number
  mail_configured: boolean
}

export async function messaging(days = 14): Promise<MessagingPage> {
  const { data } = await api.get<MessagingPage>(`${P}/messaging`,
    { params: { days } })
  return data
}

export async function updateTemplate(id: string, body: {
  name?: string; subject?: string; body_text?: string
  status?: 'draft' | 'published'
}): Promise<MessageTemplate> {
  const { data } = await api.put(`${P}/messaging/templates/${id}`, body)
  return data
}

// --------------------------------------------------- 20/21 support -------

export interface Ticket {
  id: string
  reference: string
  subject: string
  priority: 'low' | 'normal' | 'high' | 'urgent'
  status: 'open' | 'waiting' | 'resolved' | 'closed'
  opened_by: string | null
  created_at: string
  updated_at: string
  resolved_at: string | null
  first_response_due: string | null
  resolution_due: string | null
  organization_id: string
  tenant_name: string
  tenant_code: string | null
  property_name: string | null
  property_code: string | null
  assignee: string | null
  replies: number
  first_response_at: string | null
  breached: boolean
}

export interface SupportInbox {
  tickets: Ticket[]
  agents: { id: string; display_name: string; email: string | null
    open_tickets: number }[]
  sla: Record<string,
    { first_response_hours: number; resolution_hours: number }>
}

export interface AccessGrant {
  id: string
  reason: string
  scope: string[]
  minutes: number
  status: string
  created_at: string
  approved_at: string | null
  expires_at: string | null
  revoked_at: string | null
  requested_by_name: string
  approved_by_name: string | null
  currently_valid: boolean
}

export interface TicketDetail {
  ticket: Ticket & { [k: string]: unknown }
  messages: { id: string; author: string; from_side: 'tenant' | 'platform'
    body: string; created_at: string }[]
  grants: AccessGrant[]
  scopes: string[]
}

export async function supportInbox(status?: string): Promise<SupportInbox> {
  const { data } = await api.get<SupportInbox>(`${P}/support`,
    { params: status ? { status } : {} })
  return data
}

export async function ticketDetail(id: string): Promise<TicketDetail> {
  const { data } = await api.get<TicketDetail>(`${P}/support/${id}`)
  return data
}

export async function openTicket(body: {
  organization_id: string; property_id?: string | null; subject: string
  priority: string; opened_by?: string; body: string
}): Promise<Ticket> {
  const { data } = await api.post<Ticket>(`${P}/support`, body)
  return data
}

export async function replyToTicket(id: string, text: string): Promise<void> {
  await api.post(`${P}/support/${id}/reply`, { body: text })
}

export async function updateTicket(id: string, body: {
  status?: string; priority?: string; assigned_to?: string
}): Promise<void> {
  await api.put(`${P}/support/${id}`, body)
}

export async function requestAccess(ticketId: string, body: {
  organization_id: string; property_id?: string | null; reason: string
  scope: string[]; minutes: number
}): Promise<AccessGrant & { note: string }> {
  const { data } = await api.post(`${P}/support/${ticketId}/access`, body)
  return data
}

export async function revokeAccess(grantId: string): Promise<void> {
  await api.post(`${P}/support/access/${grantId}/revoke`)
}

// -------------------------------------------- 25 backups & recovery ------

export interface Snapshot {
  id: string
  label: string
  scope: string
  taken_at: string
  size_bytes: number | null
  location: string | null
  verified_at: string | null
  verify_detail: string | null
  retain_until: string | null
  tenant_name: string | null
  /** Set only on an export taken because a tenant was deleted. Its presence
   *  is what distinguishes one of those from an ordinary backup. */
  deleted_tenant_code: string | null
  deleted_at: string | null
  deletion_reason: string | null
  deletion_requested_by: string | null
  deletion_approved_by: string | null
  age_days: number
}

export interface RestoreRequest {
  id: string
  scope: string
  reason: string
  status: string
  created_at: string
  approved_at: string | null
  verified_at: string | null
  completed_at: string | null
  snapshot_label: string
  taken_at: string
  tenant_name: string | null
  requested_by_name: string
  approved_by_name: string | null
}

export interface RecoveryPage {
  snapshots: Snapshot[]
  requests: RestoreRequest[]
  summary: {
    snapshots: number; verified: number
    newest: string | null; last_verified: string | null
  }
  can_approve: boolean
  note: string
}

export async function recovery(): Promise<RecoveryPage> {
  const { data } = await api.get<RecoveryPage>(`${P}/recovery`)
  return data
}

export async function registerSnapshot(body: {
  label: string
  scope: 'platform' | 'tenant'
  organization_id?: string | null
  taken_at?: string | null
  size_bytes?: number | null
  location?: string | null
  retain_until?: string | null
}): Promise<Snapshot> {
  const { data } = await api.post<Snapshot>(`${P}/recovery/snapshots`, body)
  return data
}

/** Record that this backup was restored and came back intact.
 *
 *  `detail` is required by the server, not decoration: "verified" with no
 *  statement of what was checked is the tick that makes a register worthless.
 */
export async function verifySnapshot(
  id: string, detail: string,
): Promise<Snapshot> {
  const { data } = await api.post(`${P}/recovery/snapshots/${id}/verify`,
    { detail })
  return data
}

export async function requestRestore(body: {
  snapshot_id: string; organization_id?: string | null
  scope: string; reason: string
}): Promise<RestoreRequest> {
  const { data } = await api.post(`${P}/recovery/requests`, body)
  return data
}

export async function decideRestore(
  id: string, decision: 'approve' | 'refuse', note?: string,
): Promise<void> {
  await api.post(`${P}/recovery/requests/${id}/decision`, { decision, note })
}

// ------------------------------------------------ 26 platform settings ---

export interface PlatformSetting {
  key: string
  value: unknown
  description: string
  updated_at: string
  updated_by_name: string | null
}

export interface FeatureFlag {
  code: string
  name: string
  description: string
  enabled: boolean
  updated_at: string
  overrides: number
}

export interface SettingsPage {
  settings: PlatformSetting[]
  flags: FeatureFlag[]
  overrides: {
    flag_code: string; enabled: boolean; note: string | null
    updated_at: string; organization_id: string; tenant_name: string
  }[]
  runtime: { key: string; value: string; note: string }[]
}

export async function platformSettings(): Promise<SettingsPage> {
  const { data } = await api.get<SettingsPage>(`${P}/settings`)
  return data
}

export async function putSetting(key: string, value: unknown): Promise<void> {
  await api.put(`${P}/settings/${key}`, { value })
}

export async function putFlag(code: string, body: {
  enabled: boolean; organization_id?: string | null; note?: string
}): Promise<{ detail: string }> {
  const { data } = await api.put(`${P}/settings/flags/${code}`, body)
  return data
}

// ----------------------------------------------- 27 platform analytics ---

export interface AnalyticsPage {
  totals: {
    tenants: number; suspended: number; properties: number
    committed_mrr: string; trialing_mrr: string; first_signup: string | null
  }
  mix: {
    plan_code: string; plan_name: string; tenants: number; mrr: string
    trialing: number; active: number
  }[]
  cohorts: {
    cohort: string; cohort_start: string; signed_up: number
    still_active: number; paying: number
  }[]
  adoption: {
    module_code: string; enabled_on: number; available_on: number
    percent: number | null
  }[]
  invoices: {
    month: string; month_start: string; invoices: number
    billed: string | null; collected: string | null
  }[]
  churn: {
    cancelled: number; months_of_history: number
    measurable: boolean; note: string
  }
}

export async function analytics(): Promise<AnalyticsPage> {
  const { data } = await api.get<AnalyticsPage>(`${P}/analytics`)
  return data
}
