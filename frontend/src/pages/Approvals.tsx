import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ClipboardCheck, Tag, RotateCcw, TrendingUp, Clock, ShieldCheck, Check, X,
  Loader2, ShieldAlert, CheckCircle2,
} from 'lucide-react'
import {
  getApprovalQueue, getApprovalPolicies, approveRequest, rejectRequest,
  getApprovalHistory, type ApprovalRequest, type ApprovalPolicy,
} from '../api'
import { errorText } from '../lib/forms'

const CATEGORIES = [
  { key: '', label: 'All' },
  { key: 'discount', label: 'Discounts' },
  { key: 'refund', label: 'Refunds' },
  { key: 'rate_override', label: 'Rate Overrides' },
  { key: 'other', label: 'Other' },
]

function catIcon(c: string) {
  if (c === 'discount') return <Tag size={18} />
  if (c === 'refund') return <RotateCcw size={18} />
  if (c === 'rate_override') return <TrendingUp size={18} />
  return <Clock size={18} />
}
const money = new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 })

export default function Approvals() {
  const qc = useQueryClient()
  const [tab, setTab] = useState<'queue' | 'rules' | 'history'>('queue')
  const [cat, setCat] = useState('')
  const [selected, setSelected] = useState<ApprovalRequest | null>(null)
  const [toast, setToast] = useState('')
  const [error, setError] = useState('')

  const queueQ = useQuery({ queryKey: ['approvalQueue', cat], queryFn: () => getApprovalQueue(cat || undefined), retry: false })
  const policiesQ = useQuery({ queryKey: ['approvalPolicies'], queryFn: () => getApprovalPolicies(), retry: false })
  const historyQ = useQuery({ queryKey: ['approvalHistory'], queryFn: getApprovalHistory, enabled: tab === 'history', retry: false })
  const denied = queueQ.isError && [401, 403].includes((queueQ.error as { response?: { status?: number } })?.response?.status ?? 0)

  const requests = queueQ.data ?? []
  useEffect(() => { if (requests.length && (!selected || !requests.find((r) => r.id === selected.id))) setSelected(requests[0]) }, [requests, selected])

  const rule: ApprovalPolicy | undefined = policiesQ.data?.find((p) => p.category === selected?.category)

  function flash(m: string) { setToast(m); setError(''); setTimeout(() => setToast(''), 2500) }
  async function decide(kind: 'approve' | 'reject') {
    if (!selected) return
    setError('')
    try {
      if (kind === 'approve') await approveRequest(selected.id)
      else await rejectRequest(selected.id)
      flash(`Request ${kind === 'approve' ? 'approved' : 'rejected'}.`)
      setSelected(null)
      qc.invalidateQueries({ queryKey: ['approvalQueue'] })
      qc.invalidateQueries({ queryKey: ['approvalHistory'] })
    } catch (e) {
      // A refusal is not a success. It used to share the green toast and then
      // disappear after two seconds, so someone blocked from approving their
      // own request saw a tick and no explanation. Errors stay until dismissed.
      setToast('')
      setError(errorText(e, 'That decision could not be recorded. Please try again.'))
    }
  }

  if (denied) return <div className="flex items-center gap-2 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700"><ShieldAlert size={16} /> You do not have permission to view approvals.</div>

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <h1 className="flex items-center gap-2 text-display text-ink"><ClipboardCheck size={26} className="text-brand" /> Approvals</h1>
      </div>

      {toast && <div className="flex items-center gap-2 rounded-xl bg-emerald-50 px-4 py-3 text-sm text-emerald-700"><CheckCircle2 size={16} />{toast}</div>}
      {error && (
        <div className="flex items-start gap-2 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700">
          <ShieldAlert size={16} className="mt-0.5 shrink-0" />
          <span className="flex-1">{error}</span>
          <button onClick={() => setError('')}
            className="shrink-0 font-medium text-red-500 hover:text-red-700">
            Dismiss
          </button>
        </div>
      )}

      {/* Tabs */}
      <div className="flex gap-6 border-b border-slate-200 text-sm">
        {([['queue', `Approval Queue`], ['rules', 'Rules & Limits'], ['history', 'History']] as const).map(([k, lbl]) => (
          <button key={k} onClick={() => setTab(k)} className={`-mb-px flex items-center gap-2 border-b-2 pb-3 font-medium ${tab === k ? 'border-brand text-brand' : 'border-transparent text-slate-500 hover:text-slate-700'}`}>
            {lbl}{k === 'queue' && <span className="rounded-full bg-brand px-2 py-0.5 text-xs text-white">{requests.length}</span>}
          </button>
        ))}
      </div>

      {tab === 'queue' && (
        <div className="grid gap-5 xl:grid-cols-[1fr_380px]">
          {/* Left: queue */}
          <div className="space-y-4">
            {/* Category: one choice of five, as a segmented track. */}
            <div className="flex flex-wrap items-center gap-2">
              <span className="inline-flex shrink-0 items-center gap-0.5 rounded-lg border border-slate-200 bg-white p-0.5">
                {CATEGORIES.map((c) => {
                  const count = c.key ? requests.filter((r) => r.category === c.key).length : requests.length
                  const on = cat === c.key
                  return (
                    <button key={c.key} onClick={() => setCat(c.key)} aria-pressed={on}
                      className={`flex items-center gap-1.5 whitespace-nowrap rounded-md px-3 py-1.5 text-sm ${on
                        ? 'bg-brand font-semibold text-white'
                        : 'font-medium text-slate-500 hover:bg-slate-50 hover:text-slate-700'}`}>
                      {c.label}
                      <span className={`text-xs ${on ? 'text-white/75' : 'text-slate-400'}`}>{count}</span>
                    </button>
                  )
                })}
              </span>
            </div>

            {queueQ.isLoading && <div className="py-8 text-center text-sm text-slate-400"><Loader2 size={16} className="mx-auto animate-spin" /></div>}
            {requests.length === 0 && queueQ.data && <div className="rounded-2xl border border-slate-100 bg-white p-8 text-center text-sm text-slate-400">No pending approvals.</div>}

            {requests.map((r) => (
              <button key={r.id} onClick={() => setSelected(r)} className={`w-full rounded-2xl border bg-white p-4 text-left shadow-sm ${selected?.id === r.id ? 'border-brand ring-1 ring-brand' : 'border-slate-100 hover:border-slate-200'}`}>
                <div className="flex items-start gap-4">
                  <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-brand-light text-brand">{catIcon(r.category)}</span>
                  <div className="grid flex-1 grid-cols-2 gap-x-6 gap-y-1 md:grid-cols-5">
                    <div>
                      <div className="font-semibold text-slate-800">{r.title}</div>
                      <span className="mt-1 inline-block rounded-full bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-600">Pending</span>
                    </div>
                    <div className="text-xs"><div className="text-slate-400">Requested by</div><div className="text-slate-700">{r.requested_by_name}</div><div className="text-slate-400">{r.requested_by_role}</div></div>
                    <div className="text-xs"><div className="text-slate-400">Entity</div><div className="text-slate-700">{r.entity_ref}</div><div className="text-slate-400">Guest: {r.guest_name}</div></div>
                    <div className="text-xs"><div className="text-slate-400">Amount</div><div className="font-semibold text-slate-800">{r.amount > 0 ? money.format(r.amount) : '₹0'}</div><div className="text-slate-400">{r.amount_context}</div></div>
                    <div className="text-xs"><div className="text-slate-400">Due in</div><div className="flex items-center gap-1 font-medium text-red-500"><Clock size={12} /> {r.due_in}</div><div className="text-slate-400">{r.due_at}</div></div>
                  </div>
                </div>
                <div className="mt-2 pl-14 text-xs text-slate-500">{r.policy_rule_text}</div>
              </button>
            ))}
          </div>

          {/* Right: rule detail */}
          <div className="rounded-2xl border border-slate-100 bg-white p-5 shadow-sm">
            {!selected ? <div className="py-10 text-center text-sm text-slate-400">Select a request to review its rule.</div> : (
              <>
                <div className="mb-4 flex items-center justify-between">
                  <h2 className="flex items-center gap-2 text-lg font-semibold text-ink"><ShieldCheck size={18} className="text-brand" /> {rule?.name ?? 'Approval Rule'}</h2>
                </div>
                <div className="space-y-3 text-sm">
                  <Row label="Rule Name" value={rule?.name ?? '—'} />
                  <Row label="Applies To" value={rule?.applies_to ?? '—'} />
                  <div>
                    <div className="mb-1 text-xs font-medium text-slate-500">Initiator Roles</div>
                    <div className="flex flex-wrap gap-1">{(rule?.initiator_roles ?? []).map((x) => <Chip key={x}>{x}</Chip>)}</div>
                  </div>
                  <div>
                    <div className="mb-1 text-xs font-medium text-slate-500">Approver Roles</div>
                    <div className="flex flex-wrap gap-1">{(rule?.approver_roles ?? []).map((x) => <Chip key={x}>{x}</Chip>)}</div>
                  </div>
                  <Row label="Threshold" value={rule ? `${rule.threshold_value} ${rule.threshold_unit}` : '—'} />
                  <ToggleRow label="Two-level approval for high value" on={!!rule?.two_level} />
                  <ToggleRow label="Prohibit self-approval" on={!!rule?.prohibit_self_approval} />
                </div>
                <div className="mt-5 grid grid-cols-2 gap-2">
                  <button onClick={() => decide('approve')} className="flex items-center justify-center gap-2 rounded-xl bg-brand px-4 py-2.5 text-sm font-medium text-white hover:bg-brand/90"><Check size={16} /> Approve</button>
                  <button onClick={() => decide('reject')} className="flex items-center justify-center gap-2 rounded-xl border border-red-300 px-4 py-2.5 text-sm font-medium text-red-600 hover:bg-red-50"><X size={16} /> Reject</button>
                </div>
              </>
            )}
          </div>
        </div>
      )}

      {tab === 'rules' && (
        <div className="grid gap-4 md:grid-cols-2">
          {policiesQ.data?.map((p) => (
            <div key={p.id} className="rounded-2xl border border-slate-100 bg-white p-5 shadow-sm">
              <div className="flex items-center justify-between"><h3 className="text-lg font-semibold text-ink">{p.name}</h3><span className="rounded-full bg-slate-75 px-2 py-0.5 text-xs text-slate-500">{p.category}</span></div>
              <p className="mt-1 text-sm text-slate-400">{p.applies_to}</p>
              <div className="mt-3 text-sm"><Row label="Threshold" value={`${p.threshold_value} ${p.threshold_unit}`} /></div>
              <div className="mt-2"><div className="mb-1 text-xs text-slate-500">Approvers</div><div className="flex flex-wrap gap-1">{p.approver_roles.map((x) => <Chip key={x}>{x}</Chip>)}</div></div>
            </div>
          ))}
        </div>
      )}

      {tab === 'history' && (
        <div className="overflow-hidden rounded-2xl border border-slate-100 bg-white shadow-sm">
          {historyQ.isLoading && <div className="p-8 text-center text-sm text-slate-400"><Loader2 size={16} className="mx-auto animate-spin" /></div>}
          {historyQ.data && historyQ.data.length === 0 && <div className="p-8 text-center text-sm text-slate-400">No decisions yet.</div>}
          {historyQ.data && historyQ.data.length > 0 && (
            <table className="w-full text-sm">
              <thead><tr className="border-b border-slate-100 bg-slate-50/60 text-left text-sm text-slate-600"><th className="px-4 py-3 font-semibold">Request</th><th className="px-4 py-3 font-semibold">Entity</th><th className="px-4 py-3 font-semibold">Amount</th><th className="px-4 py-3 font-semibold">Decision</th><th className="px-4 py-3 font-semibold">By</th><th className="px-4 py-3 font-semibold">When</th></tr></thead>
              <tbody className="divide-y divide-slate-50">
                {historyQ.data.map((h) => (
                  <tr key={h.id} className="text-slate-700">
                    <td className="px-4 py-3 font-medium">{h.title}</td>
                    <td className="px-4 py-3 text-slate-500">{h.entity_ref}</td>
                    <td className="px-4 py-3">{h.amount > 0 ? money.format(h.amount) : '₹0'}</td>
                    <td className="px-4 py-3"><span className={`rounded-full px-2.5 py-1 text-xs font-medium ${h.status === 'approved' ? 'bg-emerald-50 text-emerald-600' : 'bg-red-50 text-red-600'}`}>{h.status}</span></td>
                    <td className="px-4 py-3 text-slate-500">{h.decided_by}</td>
                    <td className="px-4 py-3 text-slate-500">{h.decided_at}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  )
}

function Row({ label, value }: { label: string; value: string }) {
  return <div><div className="text-xs font-medium text-slate-500">{label}</div><div className="text-slate-700">{value}</div></div>
}
function Chip({ children }: { children: React.ReactNode }) {
  return <span className="rounded-md bg-blue-50 px-2 py-0.5 text-xs font-medium text-blue-600">{children}</span>
}
function ToggleRow({ label, on }: { label: string; on: boolean }) {
  return (
    <div className="flex items-center gap-3">
      <span className={`h-5 w-9 rounded-full p-0.5 ${on ? 'bg-brand' : 'bg-slate-200'}`}><span className={`block h-4 w-4 rounded-full bg-white ${on ? 'translate-x-4' : ''} transition-transform`} /></span>
      <span className="text-sm text-slate-600">{label}</span>
    </div>
  )
}
