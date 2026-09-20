import { useEffect, useMemo, useState } from 'react'
import Select from '../components/Select'
import { useNavigate, useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import DateField from '../components/DateField'
import {
  User, Building2, Users2, Settings, Eye, Send, Loader2, AlertTriangle,
  ShieldAlert, Lock, Save,
} from 'lucide-react'
import {
  listProperties, listDepartments, listRoles, createInvitation,
  getUserAccess, updateUserAccess,
} from '../api'
import { Crumbs } from '../components/Crumbs'
import { useActivePropertyId } from '../hooks/useProperty'


// Access levels shown in the preview per role (simple derivation).
const MODULE_ACCESS = [
  { icon: '🏠', label: 'Dashboard', level: 'View only' },
  { icon: '🛎', label: 'Front Desk', level: 'View & Manage' },
  { icon: '📅', label: 'Reservations', level: 'View & Manage' },
  { icon: '🛏', label: 'Rooms', level: 'View (Limited)' },
  { icon: '👥', label: 'Guests', level: 'View & Manage' },
  { icon: '💳', label: 'Payments', level: 'View & Process' },
  { icon: '🍽', label: 'POS', level: 'View & Process' },
  { icon: '📊', label: 'Reports', level: 'View (Limited)' },
]

export default function InviteUser() {
  const navigate = useNavigate()
  const { userId } = useParams()
  const editMode = !!userId
  const propertyId = useActivePropertyId()
  const { data: properties } = useQuery({ queryKey: ['properties'], queryFn: listProperties })
  const property = properties?.find((p) => p.id === propertyId)

  const rolesQ = useQuery({ queryKey: ['roles'], queryFn: listRoles, retry: false })
  const deptsQ = useQuery({ queryKey: ['departments'], queryFn: listDepartments, retry: false })
  const denied = (rolesQ.isError) && [401, 403].includes((rolesQ.error as { response?: { status?: number } })?.response?.status ?? 0)

  const [fullName, setFullName] = useState('')
  const [email, setEmail] = useState('')
  const [phone, setPhone] = useState('')
  const [empId, setEmpId] = useState('')
  const [deptId, setDeptId] = useState('')
  const [roleIds, setRoleIds] = useState<string[]>([])
  const [mfa, setMfa] = useState(true)
  const [tempAccess, setTempAccess] = useState(false)
  const [expiry, setExpiry] = useState('')
  const [maxDiscount, setMaxDiscount] = useState<number>(5000)
  const [refundApproval, setRefundApproval] = useState(false)
  const [version, setVersion] = useState(0)

  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  // Edit mode: load the user's current access and prefill.
  const accessQ = useQuery({
    queryKey: ['userAccess', userId],
    queryFn: () => getUserAccess(userId as string),
    enabled: editMode,
    retry: false,
  })
  useEffect(() => {
    const a = accessQ.data
    if (!a) return
    setFullName(a.full_name)
    setEmail(a.email)
    setPhone(a.phone ?? '')
    setEmpId(a.employee_code ?? '')
    setDeptId(a.department_id ?? '')
    setRoleIds(a.role_ids)
    setMfa(a.mfa_required)
    setTempAccess(a.temporary_access)
    setExpiry(a.access_expiry ?? '')
    setMaxDiscount(a.max_discount_approval ?? 0)
    setRefundApproval(a.refund_approval)
    setVersion(a.version)
  }, [accessQ.data])

  function toggleRole(id: string) {
    setRoleIds((r) => (r.includes(id) ? r.filter((x) => x !== id) : [...r, id]))
  }

  const canSend = fullName.trim() && (editMode || email.trim()) && roleIds.length > 0 && propertyId && !busy

  const restrictions = useMemo(() => {
    const items: string[] = []
    items.push(maxDiscount > 0 ? `Discount approval limited to ₹${maxDiscount.toLocaleString('en-IN')} per transaction` : 'No discount approval rights')
    items.push(refundApproval ? 'Can approve refunds' : 'No refund approval rights')
    if (tempAccess && expiry) items.push(`Access valid from invite acceptance until ${expiry}`)
    if (mfa) items.push('MFA will be required at first login')
    return items
  }, [maxDiscount, refundApproval, tempAccess, expiry, mfa])

  async function handleSend() {
    setError('')
    setBusy(true)
    try {
      if (editMode) {
        await updateUserAccess(userId as string, {
          version,
          full_name: fullName.trim(),
          phone: phone || null,
          employee_code: empId || null,
          department_id: deptId || null,
          property_id: propertyId,
          role_ids: roleIds,
          mfa_required: mfa,
          temporary_access: tempAccess,
          access_expiry: tempAccess && expiry ? expiry : null,
          max_discount_approval: maxDiscount || null,
          refund_approval: refundApproval,
        })
      } else {
        await createInvitation({
          property_id: propertyId,
          full_name: fullName.trim(),
          email: email.trim(),
          phone: phone || undefined,
          employee_code: empId || undefined,
          department_id: deptId || undefined,
          role_ids: roleIds,
          mfa_required: mfa,
          temporary_access: tempAccess,
          access_expiry: tempAccess && expiry ? expiry : null,
          max_discount_approval: maxDiscount || null,
          refund_approval: refundApproval,
        })
      }
      navigate('/staff')
    } catch (e) {
      const err = e as { response?: { status?: number; data?: { detail?: string } }; message?: string }
      const st = err.response?.status
      setError(
        st === 403 ? 'You do not have permission for this action.'
          : st === 409 ? 'This user changed since you loaded it. Reload and retry.'
          : (err.response?.data?.detail ?? err.message ?? 'Failed to save'),
      )
    } finally {
      setBusy(false)
    }
  }

  if (denied) {
    return <div className="flex items-center gap-2 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700"><ShieldAlert size={16} /> You do not have permission to invite users.</div>
  }

  return (
    <div className="space-y-5 pb-24">
      {/* Header */}
      <div className="relative overflow-hidden rounded-2xl bg-gradient-to-r from-slate-50 to-orange-50/40 px-6 py-5">
        <div className="relative z-10">
          <Crumbs trail={[{ label: 'Administration', to: '/admin' },
            { label: 'Users', to: '/staff' }]} />
          <h1 className="mt-1 text-display text-ink">{editMode ? 'Edit User Access' : 'Invite User'}</h1>
          <p className="text-slate-500">{editMode ? 'Update roles and access' : 'Create a new user account and define their access'} for <span className="font-medium text-slate-600">{property?.name ?? 'this property'}</span>.</p>
        </div>
        <div className="pointer-events-none absolute right-8 top-6 z-10 hidden select-none text-right font-script text-2xl leading-tight text-teal-800/70 lg:block">Good People<br />Great Stays</div>
      </div>

      {error && <div className="flex items-center gap-2 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700"><AlertTriangle size={16} /> {error}</div>}

      <div className="grid gap-5 xl:grid-cols-[1fr_380px]">
        {/* Left: form */}
        <div className="space-y-5">
          {/* User Details */}
          <Card icon={<User size={18} />} title="User Details">
            <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
              <Field label="Full Name" required><input value={fullName} onChange={(e) => setFullName(e.target.value)} className={inp} /></Field>
              <Field label="Email Address" required><input value={email} onChange={(e) => setEmail(e.target.value)} type="email" readOnly={editMode} className={editMode ? `${inp} bg-slate-50` : inp} /></Field>
              <Field label="Mobile Number"><div className="flex gap-2"><span className="flex items-center rounded-lg border border-slate-200 px-2 text-sm text-slate-500">+91</span><input value={phone} onChange={(e) => setPhone(e.target.value)} className={inp} /></div></Field>
              <Field label="Employee ID"><input value={empId} onChange={(e) => setEmpId(e.target.value)} placeholder="e.g. EMP1012" className={inp} /></Field>
              <Field label="Department" required>
                <Select value={deptId} onChange={(e) => setDeptId(e.target.value)} className={inp}>
                  <option value="">Select department</option>
                  {deptsQ.data?.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
                </Select>
              </Field>
            </div>
          </Card>

          {/* Property and Outlet Scope */}
          <Card icon={<Building2 size={18} />} title="Property and Outlet Scope">
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
              <Field label="Property" required><input value={property?.name ?? ''} readOnly className={`${inp} bg-slate-50`} /></Field>
              <Field label="Outlet Scope (Optional)">
                <Select className={inp} disabled title="Outlet scoping — outlets module pending"><option>Select outlets</option></Select>
              </Field>
            </div>
            <p className="mt-1 text-xs text-slate-400">Leave blank to grant access to all outlets at this property.</p>
          </Card>

          {/* Role Assignments */}
          <Card icon={<Users2 size={18} />} title="Role Assignments" subtitle="Select one or more roles to define base permissions.">
            {rolesQ.isLoading && <div className="flex items-center gap-2 text-sm text-slate-400"><Loader2 size={15} className="animate-spin" /> Loading roles…</div>}
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
              {rolesQ.data?.map((r) => {
                const sel = roleIds.includes(r.id)
                return (
                  <button key={r.id} onClick={() => toggleRole(r.id)} className={`flex items-start gap-3 rounded-xl border p-4 text-left ${sel ? 'border-brand bg-brand-light' : 'border-slate-200 hover:bg-slate-50'}`}>
                    <span className={`mt-0.5 flex h-5 w-5 items-center justify-center rounded ${sel ? 'bg-brand text-white' : 'border border-slate-300'}`}>{sel && '✓'}</span>
                    <div><div className="font-medium text-slate-700">{r.name}</div><div className="text-xs text-slate-400">Base permissions for {r.name}.</div></div>
                  </button>
                )
              })}
            </div>
          </Card>

          {/* Additional Settings */}
          <Card icon={<Settings size={18} />} title="Additional Settings">
            <div className="grid grid-cols-1 gap-5 md:grid-cols-2">
              <Toggle label="MFA Required" desc="User must set up Multi-Factor Authentication at first login." value={mfa} onChange={setMfa} />
              <div>
                <Toggle label="Temporary Access" desc="Access will be automatically revoked after the selected date." value={tempAccess} onChange={setTempAccess} />
                {tempAccess && (
                  <div className="mt-3"><Field label="Access Expiry Date" required><DateField value={expiry} onChange={(v) => setExpiry(v)} className={inp} /></Field></div>
                )}
              </div>
              <Field label="Maximum Discount Approval (₹)">
                <input type="number" value={maxDiscount} onChange={(e) => setMaxDiscount(Number(e.target.value))} className={inp} />
                <span className="mt-1 block text-xs text-slate-400">User can approve discounts up to this amount per transaction.</span>
              </Field>
              <Toggle label="Refund Approval" desc={refundApproval ? 'User can approve refunds.' : 'User cannot approve refunds.'} value={refundApproval} onChange={setRefundApproval} />
            </div>
          </Card>
        </div>

        {/* Right: Effective Access Preview */}
        <div className="rounded-2xl border border-slate-100 bg-white p-5 shadow-sm">
          <h2 className="flex items-center gap-2 text-lg font-semibold text-ink"><Eye size={18} className="text-brand" /> Effective Access Preview</h2>
          <p className="mt-1 text-xs text-slate-400">This is a summary of what the user will be able to access based on the selected roles and settings.</p>
          <div className="mt-4 space-y-2 border-t border-slate-100 pt-4">
            {MODULE_ACCESS.map((m) => (
              <div key={m.label} className="flex items-center justify-between text-sm">
                <span className="flex items-center gap-2 text-slate-600">{m.label}</span>
                <span className="text-slate-400">{roleIds.length ? m.level : '—'}</span>
              </div>
            ))}
          </div>
          <div className="mt-4 rounded-xl bg-amber-50 p-4">
            <div className="flex items-center gap-2 font-medium text-caution"><Lock size={15} /> Key Restrictions</div>
            <ul className="mt-2 space-y-1 text-xs text-amber-700">
              {restrictions.map((r, i) => <li key={i} className="flex gap-1.5"><span>•</span>{r}</li>)}
            </ul>
          </div>
        </div>
      </div>

      {/* Footer actions */}
      <div className="fixed bottom-0 right-0 left-64 z-10 flex justify-end gap-3 border-t border-slate-200 bg-white/90 px-8 py-4 backdrop-blur">
        <button onClick={() => navigate('/staff')} className="rounded-xl border border-slate-200 px-5 py-2.5 text-sm font-medium text-slate-600 hover:bg-slate-50">Cancel</button>
        <button onClick={handleSend} disabled={!canSend} className="flex items-center gap-2 rounded-xl bg-brand px-5 py-2.5 text-sm font-medium text-white enabled:hover:bg-brand/90 disabled:opacity-50">
          {busy ? <Loader2 size={16} className="animate-spin" /> : (editMode ? <Save size={16} /> : <Send size={16} />)} {editMode ? 'Save Changes' : 'Send Invitation'}
        </button>
      </div>
    </div>
  )
}

const inp = 'w-full rounded-lg border border-slate-200 px-3 py-2.5 text-sm outline-none focus:border-brand'

function Card({ icon, title, subtitle, children }: { icon: React.ReactNode; title: string; subtitle?: string; children: React.ReactNode }) {
  return (
    <div className="rounded-2xl border border-slate-100 bg-white p-6 shadow-sm">
      <h2 className="flex items-center gap-2 text-lg font-semibold text-ink"><span className="text-brand">{icon}</span> {title}</h2>
      {subtitle && <p className="mt-1 text-sm text-slate-400">{subtitle}</p>}
      <div className="mt-4">{children}</div>
    </div>
  )
}

function Field({ label, required, children }: { label: string; required?: boolean; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-sm font-medium text-slate-600">{label}{required && <span className="text-red-500"> *</span>}</span>
      {children}
    </label>
  )
}

function Toggle({ label, desc, value, onChange }: { label: string; desc: string; value: boolean; onChange: (v: boolean) => void }) {
  return (
    <div className="flex items-start gap-3">
      <button onClick={() => onChange(!value)} className={`mt-0.5 h-6 w-11 shrink-0 rounded-full p-0.5 transition-colors ${value ? 'bg-brand' : 'bg-slate-300'}`}>
        <span className={`block h-5 w-5 rounded-full bg-white transition-transform ${value ? 'translate-x-5' : ''}`} />
      </button>
      <div><div className="text-sm font-medium text-slate-700">{label}</div><div className="text-xs text-slate-400">{desc}</div></div>
    </div>
  )
}
