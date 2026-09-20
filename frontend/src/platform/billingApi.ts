/** Platform billing client — specification screens 10 to 13.
 *
 *  Separate from platform/api.ts because billing is gated on its own
 *  capabilities: an operator holding `staff.view` may see the console and not
 *  these screens at all. Keeping the calls apart makes that boundary visible
 *  in the imports rather than only at runtime.
 */
import { api } from '../api'

const P = '/iam/platform/billing'

export interface PlanLimits { [metric: string]: number | null }

export interface Plan {
  plan_id: string
  code: string
  name: string
  summary: string
  sort_order: number
  status: string
  plan_version_id: string
  version_no: number
  amount: string
  currency: string
  billing_cycle: string
  trial_days: number
  version_status: string
  limits: PlanLimits
  modules: string[]
}

export interface SubscriptionRow {
  organization_id: string
  organization_name: string
  organization_status: string
  subscription_id: string | null
  status: string | null
  amount: string | null
  currency: string | null
  billing_cycle: string | null
  quantity: number | null
  trial_ends_on: string | null
  current_period_end: string | null
  grace_until: string | null
  plan_code: string | null
  plan_name: string | null
  version_no: number | null
  period_total: string | null
  unpaid_invoices: number
}

export interface OverLimit {
  metric: string
  in_use: number
  allowed: number
  over_by: number
}

export interface ChangePreview {
  target: {
    plan_version_id: string; code: string; name: string
    amount: string; billing_cycle: string
  }
  current: { code: string; amount: string; version_no: number } | null
  quantity: number
  new_period_total: string
  proration_amount: string
  effective_on: string
  limits: PlanLimits
  usage: Record<string, number>
  over_limit: OverLimit[]
  can_apply: boolean
  subscription_id: string | null
}

export interface SubscriptionDetail {
  organization_id: string
  subscription: (SubscriptionRow & { plan_version_id: string }) | null
  usage: Record<string, number>
  limits?: PlanLimits
  over_limit?: OverLimit[]
  entitlements?: {
    kind: string; code: string; value: number | null
    source: string; note: string | null
  }[]
  changes?: {
    id: string; effective_on: string; proration_amount: string
    status: string; reason: string | null; created_at: string
    applied_at: string | null; from_plan: string | null; to_plan: string
  }[]
}

export interface Invoice {
  id: string
  series: string
  number: number
  status: string
  organization_id: string
  organization_name: string
  period_start: string | null
  period_end: string | null
  due_on: string | null
  currency: string
  subtotal: string
  tax_total: string
  total: string
  amount_paid: string
  issued_at: string | null
  paid_at: string | null
  outstanding: string
}

export async function listPlans(): Promise<Plan[]> {
  const { data } = await api.get<Plan[]>(`${P}/plans`)
  return data
}

export interface PlanVersionIn {
  amount: string
  billing_cycle: 'monthly' | 'annual'
  trial_days: number
  limits: PlanLimits
  modules: string[]
  reason: string
}

export async function createPlanVersion(
  planId: string, body: PlanVersionIn,
): Promise<{ id: string; version_no: number; status: string }> {
  const { data } = await api.post(`${P}/plans/${planId}/versions`, body)
  return data
}

export async function publishPlanVersion(
  versionId: string, reason: string,
): Promise<void> {
  await api.post(`${P}/plan-versions/${versionId}/publish`, { reason })
}

export async function listSubscriptions(
  status?: string,
): Promise<SubscriptionRow[]> {
  const { data } = await api.get<SubscriptionRow[]>(`${P}/subscriptions`, {
    params: status ? { status } : {},
  })
  return data
}

export async function subscriptionDetail(
  orgId: string,
): Promise<SubscriptionDetail> {
  const { data } = await api.get<SubscriptionDetail>(
    `${P}/subscriptions/${orgId}`)
  return data
}

export async function previewChange(
  orgId: string, planCode: string, reason: string,
): Promise<ChangePreview> {
  const { data } = await api.post<ChangePreview>(
    `${P}/subscriptions/${orgId}/preview-change`,
    { plan_code: planCode, reason })
  return data
}

export async function applyChange(
  orgId: string, planCode: string, reason: string,
): Promise<{ plan: string; amount: string; quantity: number }> {
  const { data } = await api.post(
    `${P}/subscriptions/${orgId}/apply-change`,
    { plan_code: planCode, reason })
  return data
}

export async function listInvoices(
  params: { org_id?: string; status?: string } = {},
): Promise<Invoice[]> {
  const { data } = await api.get<Invoice[]>(`${P}/invoices`, { params })
  return data
}

export async function voidInvoice(id: string, reason: string): Promise<void> {
  await api.post(`${P}/invoices/${id}/void`, { reason })
}

/** ₹ with thousands separators, and no decimals unless there are paise.
 *
 *  The pack prints ₹44,996 and ₹9,999. Two trailing zeros on every figure in
 *  a column of round subscription prices is noise, and it pushes a KPI past
 *  the width where it has to step down a size.
 */
export function money(v: string | number | null | undefined): string {
  const n = Number(v ?? 0)
  const whole = Number.isInteger(n)
  return n.toLocaleString('en-IN', {
    style: 'currency', currency: 'INR',
    minimumFractionDigits: whole ? 0 : 2,
    maximumFractionDigits: 2,
  })
}
