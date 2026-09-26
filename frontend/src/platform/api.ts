/** The platform console's API client.
 *
 *  Kept in its own module rather than added to src/api.ts, for the same reason
 *  the routes live in their own router: every call in here is cross-tenant,
 *  and mixing them into the file every tenant screen imports is how one ends
 *  up being called from a tenant screen by autocomplete.
 *
 *  Nothing here takes a property_id from local storage the way the tenant
 *  client does. A platform operator has no active property — every call names
 *  the organisation or property it acts on explicitly, in the path.
 */
import { api } from '../api'

export interface Organization {
  id: string
  /** TN-001 and friends. What a caller reads out. */
  code: string | null
  name: string
  status: string
  created_on: string
  properties: number
  rooms: number
  active_users: number
  last_activity_at: string | null
  plan_code: string | null
  plan_name: string | null
  subscription_status: string | null
  mrr: string
  /** The subscription's state where there is one, the account's where there
   *  is not — the single word the directory's Status column shows. */
  lifecycle: string
}

export interface OrgProperty {
  id: string
  code: string
  name: string
  status: string
  timezone: string
  currency: string
  contact_email: string | null
}

export interface OrgUser {
  id: string
  display_name: string
  email: string | null
  user_status: string
  membership_status: string
  last_login_at: string | null
}

export interface OrganizationDetail {
  id: string
  code: string | null
  name: string
  status: string
  properties: OrgProperty[]
  users: OrgUser[]
}

export interface PlatformProperty {
  id: string
  code: string
  name: string
  status: string
  timezone: string
  currency: string
  contact_email: string | null
  city: string | null
  /** The postal address region. */
  state: string | null
  /** Readiness, resolved server-side: live | setup | suspended. Distinct
   *  from `state` above, which is an address field — they collided under one
   *  name until this was split. */
  readiness: string
  rooms: number
  organization_id: string
  organization_name: string
  organization_code: string | null
  organization_status: string
  current_step: string | null
  activated_at: string | null
}

export interface UserMembership {
  organization_id: string
  organization: string
  organization_status: string
  membership_status: string
}

export interface PlatformUser {
  id: string
  display_name: string
  email: string | null
  status: string
  last_login_at: string | null
  mfa_status: string | null
  is_platform_admin: boolean
  memberships: UserMembership[]
}

export interface Permission {
  id: string
  resource_code: string
  action_code: string
  description: string | null
  granted_to_roles: number
}

export interface AuditRow {
  id: string
  occurred_at: string
  actor_subject: string | null
  action: string
  entity_type: string
  entity_id: string | null
  reason: string | null
  organization_id: string | null
  organization_name: string | null
  property_id: string | null
  has_before: boolean
  has_after: boolean
}

export interface UsageRow {
  organization_id: string
  organization_name: string
  status: string
  properties: number
  active_users: number
  reservations: number
  folios: number
  last_reservation_at: string | null
}

export interface BusinessDateRow {
  property_id: string
  code: string
  name: string
  timezone: string
  organization_name: string
  audit_hour: number | null
  last_audited_date: string | null
  last_run_status: string | null
  completed_at: string | null
  /** Positive means the property's business date has run ahead of its own
   *  local calendar date — the drift that made every report answer for the
   *  wrong day, and which nothing surfaced until somebody read two screens
   *  against each other. */
  days_ahead: number | null
}

export interface Health {
  migration_heads: Record<string, string | null>
  totals: {
    organizations: number
    active_organizations: number
    properties: number
    active_users: number
    platform_admins: number
    live_sessions: number
  }
}

export interface PlatformAdmin {
  id: string
  user_id: string
  display_name: string
  email: string | null
  status: string
  granted_at: string
  revoked_at: string | null
  note: string | null
  granted_by_name: string | null
  roles?: string[]
  last_active: string | null
  mfa: 'none' | 'pending' | 'active'
}

export interface Module {
  module_code: string
  enabled: boolean
  enabled_at: string | null
}

const P = '/iam/platform'

// ------------------------------------------------------------- tenants ----

export async function listOrganizations(
  params: { query?: string; status?: string; plan?: string } = {},
): Promise<Organization[]> {
  const { data } = await api.get<Organization[]>(`${P}/organizations`, { params })
  return data
}

export async function getOrganization(id: string): Promise<OrganizationDetail> {
  const { data } = await api.get<OrganizationDetail>(`${P}/organizations/${id}`)
  return data
}

export async function renameOrganization(
  id: string, name: string, reason?: string,
): Promise<void> {
  await api.patch(`${P}/organizations/${id}`, { name, reason })
}

/** Correct the address a tenant's invitation went to.
 *
 *  Only while the account is still `invited`; the server refuses once it has
 *  been set up, because a live account's details belong to its owner.
 */
export async function correctInvitedUser(
  orgId: string, userId: string,
  body: { email?: string; display_name?: string; reason: string },
): Promise<{ email: string; display_name: string; links_invalidated: number }> {
  const { data } = await api.patch(
    `${P}/organizations/${orgId}/users/${userId}`, body)
  return data
}

/** Send the set-password link again, on a fresh token. */
export async function resendInvitation(
  orgId: string, userId: string,
): Promise<{ email: string; emailed: boolean; hours: number; detail: string }> {
  const { data } = await api.post(
    `${P}/organizations/${orgId}/users/${userId}/resend-invite`)
  return data
}

export async function suspendOrganization(
  id: string, reason: string,
): Promise<{ sessions_revoked: number }> {
  const { data } = await api.post(`${P}/organizations/${id}/suspend`, { reason })
  return data
}

export async function reactivateOrganization(id: string): Promise<void> {
  await api.post(`${P}/organizations/${id}/reactivate`)
}

export interface TenantCreateIn {
  organization_name: string
  property_name?: string
  owner_name: string
  owner_email: string
  owner_phone?: string
  timezone?: string
  currency?: string
}

export interface TenantCreated {
  organization_id: string
  property_id: string
  property_code: string
  owner_user_id: string
  owner_email: string
  welcome_emailed: boolean
}

export async function createTenant(body: TenantCreateIn): Promise<TenantCreated> {
  const { data } = await api.post<TenantCreated>(`${P}/organizations`, body)
  return data
}

// ---------------------------------------------------------- properties ----

export async function listProperties(
  params: { org_id?: string; query?: string; state?: string } = {},
): Promise<PlatformProperty[]> {
  const { data } = await api.get<PlatformProperty[]>(`${P}/properties`, { params })
  return data
}

export async function setPropertyStatus(
  id: string, status: 'active' | 'suspended', reason: string,
): Promise<void> {
  await api.post(`${P}/properties/${id}/status`, { status, reason })
}

export async function listModules(propertyId: string): Promise<Module[]> {
  const { data } = await api.get<Module[]>(`${P}/properties/${propertyId}/modules`)
  return data
}

export async function setModule(
  propertyId: string, moduleCode: string, enabled: boolean, reason?: string,
): Promise<void> {
  await api.put(`${P}/properties/${propertyId}/modules/${moduleCode}`,
    { enabled, reason })
}

// --------------------------------------------------------------- users ----

export async function findUsers(query: string): Promise<PlatformUser[]> {
  const { data } = await api.get<PlatformUser[]>(`${P}/users`, { params: { query } })
  return data
}

export async function setUserStatus(
  id: string, status: 'active' | 'suspended', reason: string,
): Promise<{ sessions_revoked: number }> {
  const { data } = await api.post(`${P}/users/${id}/status`, { status, reason })
  return data
}

export async function revokeSessions(
  id: string, reason: string,
): Promise<{ sessions_revoked: number }> {
  const { data } = await api.post(`${P}/users/${id}/revoke-sessions`, { reason })
  return data
}

// ----------------------------------------------------------- catalogue ----

export async function listPermissions(): Promise<Permission[]> {
  const { data } = await api.get<Permission[]>(`${P}/permissions`)
  return data
}

export async function createPermission(body: {
  resource_code: string; action_code: string; description?: string
}): Promise<Permission> {
  const { data } = await api.post<Permission>(`${P}/permissions`, body)
  return data
}

// ------------------------------------------------------- observability ----

export async function platformAudit(
  params: { org_id?: string; action?: string; actor?: string; limit?: number } = {},
): Promise<AuditRow[]> {
  const { data } = await api.get<AuditRow[]>(`${P}/audit`, { params })
  return data
}

export async function usage(): Promise<UsageRow[]> {
  const { data } = await api.get<UsageRow[]>(`${P}/usage`)
  return data
}

/** What one property owes its channels, and how late any of it is. */
export interface OtaPropertyRow {
  property_id: string
  code: string
  name: string
  organization_name: string
  open_count: number
  overdue_count: number
  reported_count: number
  next_due_at: string | null
  /** How late the worst open obligation is, in hours. Null when none is. */
  oldest_overdue_hours: number | null
}

export async function otaActions(): Promise<OtaPropertyRow[]> {
  const { data } = await api.get<OtaPropertyRow[]>(`${P}/ota-actions`)
  return data
}

export async function businessDates(): Promise<BusinessDateRow[]> {
  const { data } = await api.get<BusinessDateRow[]>(`${P}/business-dates`)
  return data
}

export async function health(): Promise<Health> {
  const { data } = await api.get<Health>(`${P}/health`)
  return data
}

// ------------------------------------------------------------- admins ----

export async function listAdmins(): Promise<PlatformAdmin[]> {
  const { data } = await api.get<PlatformAdmin[]>(`${P}/admins`)
  return data
}

export async function grantAdmin(email: string, note?: string): Promise<void> {
  await api.post(`${P}/admins`, { email, note })
}

export async function revokeAdmin(userId: string, reason: string): Promise<void> {
  await api.post(`${P}/admins/${userId}/revoke`, { reason })
}

export interface PlatformRole {
  id: string
  code: string
  name: string
  description: string
  is_system: boolean
  capabilities: string[]
  held_by: number
}

export interface Capability {
  code: string
  description: string
  granted_to: string[]
}

export interface Whoami {
  subject: string
  user_id: string
  roles: string[]
  capabilities: string[]
}

export async function whoami(): Promise<Whoami> {
  const { data } = await api.get<Whoami>(`${P}/me`)
  return data
}

// ------------------------------------------------ deleting a tenant ----
//
// Four calls for one act, on purpose. Suspension stays the reversible answer;
// this pathway exists so the irreversible one leaves a record.

export interface DeletionSurvey {
  organization: { id: string; code: string | null; name: string; status: string }
  /** Table name to row count, for everything that would be destroyed. */
  tables: Record<string, number>
  rows_total: number
  suspended: boolean
  cooling_hours: number
}

export interface DeletionRequestRow {
  id: string
  organization_id: string
  organization_code: string | null
  organization_name: string
  reason: string
  status: 'requested' | 'approved' | 'refused' | 'completed' | 'cancelled'
  requested_at: string
  /** The requester's user id, so a screen can apply the two-person rule
   *  before the server has to refuse it. */
  requested_by: string
  executable_after: string
  approved_at: string | null
  completed_at: string | null
  export_location: string | null
  requested_by_name: string | null
  approved_by_name: string | null
  /** False once the tenant is gone — the record outlives its subject. */
  tenant_exists: boolean
  removed: Record<string, number>
}

/** What would be destroyed. Reads only; safe to call for a look. */
export async function deletionSurvey(orgId: string): Promise<DeletionSurvey> {
  const { data } = await api.get<DeletionSurvey>(
    `${P}/organizations/${orgId}/deletion-survey`)
  return data
}

export async function requestDeletion(
  orgId: string, body: { reason: string; confirm_code: string },
): Promise<{ id: string; executable_after: string; rows_to_remove: number }> {
  const { data } = await api.post(
    `${P}/organizations/${orgId}/deletion-requests`, body)
  return data
}

export async function listDeletionRequests(): Promise<DeletionRequestRow[]> {
  const { data } = await api.get<DeletionRequestRow[]>(`${P}/deletion-requests`)
  return data
}

export async function withdrawDeletion(requestId: string): Promise<void> {
  await api.post(`${P}/deletion-requests/${requestId}/withdraw`)
}

export async function approveDeletion(
  requestId: string, confirmCode: string,
): Promise<{ organization: string; rows_removed: number }> {
  const { data } = await api.post(
    `${P}/deletion-requests/${requestId}/approve`, { confirm_code: confirmCode })
  return data
}

export async function listRoles(): Promise<PlatformRole[]> {
  const { data } = await api.get<PlatformRole[]>(`${P}/roles`)
  return data
}

export async function listCapabilities(): Promise<Capability[]> {
  const { data } = await api.get<Capability[]>(`${P}/capabilities`)
  return data
}

export async function setRoleCapabilities(
  roleId: string, capabilities: string[], reason: string,
): Promise<void> {
  await api.put(`${P}/roles/${roleId}/capabilities`, { capabilities, reason })
}

export async function setAdminRoles(
  userId: string, roles: string[], reason: string,
): Promise<void> {
  await api.put(`${P}/admins/${userId}/roles`, { roles, reason })
}

// ------------------------------------- property overview + onboarding ----

export interface PropertyOverview {
  id: string
  code: string
  name: string
  status: string
  timezone: string
  currency: string
  property_type: string | null
  contact_email: string | null
  contact_phone: string | null
  address_line: string | null
  city: string | null
  state: string | null
  postal_code: string | null
  country: string | null
  checkin_time: string | null
  checkout_time: string | null
  organization_id: string
  organization_name: string
  organization_code: string | null
  organization_status: string
  onboarding: {
    current_step: string | null
    steps: Record<string, { visited?: boolean }> | null
    started_at: string | null
    activated_at: string | null
  } | null
  modules: Module[]
  capacity: { rooms: number; room_types: number }
  business_date: {
    last_audited_date: string | null
    last_run_status: string | null
    completed_at: string | null
    days_ahead: number | null
  } | null
  recent_activity: {
    occurred_at: string; actor_subject: string | null
    action: string; entity_type: string; reason: string | null
  }[]
  services: {
    channel_provider: string | null
    last_provision_status: string | null
    last_push_status: string | null
    room_mappings: number
    rate_mappings: number
    ota_connections: number
    bookings_received: number
  } | null
  billing: { plan_name: string; status: string } | null
  /** Modules the tenant's plan grants. Separate from `modules`, which is what
   *  the property has switched on — nothing bridges the two. */
  entitlements: { code: string; source: string }[]
}

export async function propertyOverview(id: string): Promise<PropertyOverview> {
  const { data } = await api.get<PropertyOverview>(`${P}/properties/${id}`)
  return data
}

export interface OnboardingRow {
  property_id: string
  code: string
  name: string
  status: string
  organization_id: string
  organization_name: string
  current_step: string | null
  started_at: string | null
  activated_at: string | null
  rooms: number
  last_activity_at: string | null
  trial_ends_on: string | null
  live: boolean
  steps: { code: string; label: string; required: boolean; visited: boolean }[]
  visited_count: number
  total_steps: number
  required_outstanding: string[]
  /** Whether a guest can actually book this property today. Onboarding can
   *  reach go-live with every step ticked and this still `not_configured` —
   *  nothing in the wizard, in tenant creation or in billing switches the
   *  booking engine on. */
  booking_engine: 'enabled' | 'disabled' | 'not_configured'
  /** Whether the tenant's plan grants it. Today no plan version lists
   *  booking_engine at all, so this is false everywhere — which is the point
   *  of showing it. */
  booking_entitled: boolean
}

export async function onboardingProgress(): Promise<OnboardingRow[]> {
  const { data } = await api.get<OnboardingRow[]>(`${P}/onboarding`)
  return data
}

// ------------------------------------------------------------------ mfa --

export interface MfaStatus {
  status: 'none' | 'pending' | 'active'
  recovery_codes_unused: number
  required: boolean
}

export async function mfaStatus(): Promise<MfaStatus> {
  const { data } = await api.get<MfaStatus>('/iam/auth/mfa/status')
  return data
}

/** Start (or restart) enrolment. Replacing an active factor needs a current
 *  authenticator code or an unused recovery code -- otherwise anyone holding
 *  a session could quietly swap the second factor for their own. */
export async function mfaEnrol(currentCode?: string): Promise<{
  secret: string; otpauth_uri: string; recovery_codes: string[]
}> {
  const { data } = await api.post('/iam/auth/mfa/enrol',
    currentCode ? { code: currentCode } : {})
  return data
}

export async function mfaConfirm(code: string): Promise<void> {
  await api.post('/iam/auth/mfa/confirm', { code })
}


// ------------------------------------------------------ channel health ----

export interface ChannelLink {
  id: string
  provider: string
  external_property_id: string | null
  property_id: string
  property_code: string
  property_name: string
  organization_id: string
  tenant_code: string | null
  tenant_name: string
  last_push_status: string | null
  last_push_detail: string | null
  last_pushed_at: string | null
  last_provision_status: string | null
  last_provision_detail: string | null
  last_provisioned_at: string | null
  send_availability: boolean
  send_rates: boolean
  send_restrictions: boolean
  notify_on_failure: boolean
  currency: string | null
  room_mappings: number
  rate_mappings: number
  ota_connections: number
  bookings_received: number
  last_booking_at: string | null
  health: string
}

export async function channelHealth(provider?: string): Promise<ChannelLink[]> {
  const { data } = await api.get<ChannelLink[]>(`${P}/channels`, {
    params: provider ? { provider } : {},
  })
  return data
}


// ---------------------------------------------------- system operations ----

export interface Operations {
  outbox: {
    total: number; pending: number
    oldest_pending: string | null; last_published: string | null
  }
  outbox_by_type: { event_type: string; pending: number; oldest: string }[]
  night_audit: { status: string; runs: number; latest: string | null }[]
  /** Dates attempted where no run ever completed. The real alarm. */
  /** Days that are open and finished — from business_days, so a day nobody
   *  ever attempted is counted too. `attempts` tells the two apart. */
  unclosed_days: {
    property_id: string; code: string; property_name: string
    tenant_name: string; business_date: string
    days_open: number; attempts: number
  }[]
  /** Runs unwound on purpose, with the reason recorded. Not a failure. */
  reversed_runs: {
    id: string; property_name: string; business_date: string
    step_code: string; reason: string | null; updated_at: string
  }[]
  failed_steps: {
    step_code: string; status: string; occurrences: number
    last_seen: string | null; last_error: string | null
  }[]
  webhooks: {
    provider: string; event_type: string; outcome: string
    events: number; last_received: string | null
  }[]
}

export async function systemOperations(): Promise<Operations> {
  const { data } = await api.get<Operations>(`${P}/operations`)
  return data
}
