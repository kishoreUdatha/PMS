import { usePropertyName } from '../hooks/useProperty'
import Select from '../components/Select'
import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Plus, Save, Copy, Loader2, ShieldAlert, CheckCircle2, Crown, User, Users2,
} from 'lucide-react'
import {
  getRoleCatalogue, getRolesWithCounts, getRoleMatrix, saveRoleMatrix,
  addRole, cloneRole, type RoleMatrix,
} from '../api'
import { Crumbs } from '../components/Crumbs'
import { askText } from '../components/AskDialog'

const ACTION_LABELS: Record<string, string> = {
  view: 'View', create: 'Create', edit: 'Edit', cancel: 'Cancel',
  approve: 'Approve', export: 'Export', configure: 'Configure',
}

export default function RolesMatrix() {
  const propertyName = usePropertyName()
  const qc = useQueryClient()
  const [selectedRole, setSelectedRole] = useState<string | null>(null)
  const [draft, setDraft] = useState<RoleMatrix | null>(null)
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [toast, setToast] = useState('')

  const catQ = useQuery({ queryKey: ['roleCatalogue'], queryFn: getRoleCatalogue, retry: false })
  const rolesQ = useQuery({ queryKey: ['rolesWithCounts'], queryFn: getRolesWithCounts, retry: false })
  const denied = (rolesQ.isError || catQ.isError) && [401, 403].includes(((rolesQ.error || catQ.error) as { response?: { status?: number } })?.response?.status ?? 0)

  // Auto-select first role.
  useEffect(() => {
    if (!selectedRole && rolesQ.data && rolesQ.data.length) setSelectedRole(rolesQ.data[0].id)
  }, [rolesQ.data, selectedRole])

  const matrixQ = useQuery({
    queryKey: ['roleMatrix', selectedRole],
    queryFn: () => getRoleMatrix(selectedRole as string),
    enabled: !!selectedRole,
    retry: false,
  })
  useEffect(() => { if (matrixQ.data) { setDraft(matrixQ.data); setDirty(false) } }, [matrixQ.data])

  function flash(m: string) { setToast(m); setTimeout(() => setToast(''), 2500) }

  function toggle(module: string, action: string) {
    if (!draft) return
    setDraft({ ...draft, permissions: { ...draft.permissions, [module]: { ...draft.permissions[module], [action]: !draft.permissions[module][action] } } })
    setDirty(true)
  }
  function setScope(field: 'record_scope' | 'property_scope', v: string) { if (draft) { setDraft({ ...draft, [field]: v }); setDirty(true) } }
  function setLimit(field: 'max_discount' | 'max_refund', v: number) { if (draft) { setDraft({ ...draft, [field]: v }); setDirty(true) } }

  async function save() {
    if (!draft || !selectedRole) return
    setSaving(true)
    try {
      await saveRoleMatrix(selectedRole, {
        permissions: draft.permissions,
        record_scope: draft.record_scope,
        property_scope: draft.property_scope,
        max_discount: draft.max_discount ?? null,
        max_refund: draft.max_refund ?? null,
      })
      flash('Permissions saved.')
      setDirty(false)
      qc.invalidateQueries({ queryKey: ['roleMatrix', selectedRole] })
      qc.invalidateQueries({ queryKey: ['rolesWithCounts'] })
    } catch { flash('Failed to save.') } finally { setSaving(false) }
  }

  async function handleAdd() {
    const name = await askText({
      title: 'New role',
      label: 'Role name',
      minLength: 2,
      hint: 'At least 2 characters.',
      confirmText: 'Create role',
    })
    if (!name) return
    try { const r = await addRole(name); qc.invalidateQueries({ queryKey: ['rolesWithCounts'] }); setSelectedRole(r.id); flash('Role created.') } catch { flash('Failed to add role.') }
  }
  async function handleClone() {
    if (!draft) return
    const name = await askText({
      title: `Clone ${draft.name}`,
      label: 'New role name',
      initial: `${draft.name} Copy`,
      minLength: 2,
      hint: 'At least 2 characters.',
      confirmText: 'Clone role',
    })
    if (!name) return
    try { const r = await cloneRole(draft.id, name); qc.invalidateQueries({ queryKey: ['rolesWithCounts'] }); setSelectedRole(r.id); flash('Role cloned.') } catch { flash('Failed to clone.') }
  }

  const modules = catQ.data?.modules ?? []
  const actions = catQ.data?.actions ?? []

  if (denied) return <div className="flex items-center gap-2 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700"><ShieldAlert size={16} /> You do not have permission to manage roles.</div>

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="relative overflow-hidden rounded-2xl bg-gradient-to-r from-slate-50 to-amber-50/50 px-6 py-5">
        <div className="relative z-10">
          <Crumbs title="Roles & Permissions" trail={[
            { label: 'Administration', to: '/admin' },
            { label: 'Roles & Permissions' }]} />
          <h1 className="mt-1 text-display text-ink">Roles &amp; Permissions</h1>
          <p className="text-slate-500">Manage user roles and control access across the PMS.</p>
        </div>
        <div className="pointer-events-none absolute right-8 top-6 z-10 hidden select-none text-right font-script text-xl italic leading-tight text-caution/70 lg:block">Right people. Right access.<br />A smoother stay for everyone.</div>
      </div>

      {toast && <div className="flex items-center gap-2 rounded-xl bg-emerald-50 px-4 py-3 text-sm text-emerald-700"><CheckCircle2 size={16} />{toast}</div>}

      <div className="grid gap-4 xl:grid-cols-[280px_1fr_300px]">
        {/* Roles list */}
        <div className="rounded-2xl border border-slate-100 bg-white p-4 shadow-sm">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-lg font-semibold text-ink">Roles</h2>
            <button onClick={handleAdd} className="flex items-center gap-1 rounded-lg bg-brand px-3 py-1.5 text-xs font-medium text-white hover:bg-brand/90"><Plus size={14} /> Add Role</button>
          </div>
          {rolesQ.isLoading && <div className="py-6 text-center text-sm text-slate-400"><Loader2 size={16} className="mx-auto animate-spin" /></div>}
          <ul className="space-y-1">
            {rolesQ.data?.map((r, i) => (
              <li key={r.id}>
                <button onClick={() => setSelectedRole(r.id)} className={`flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left ${selectedRole === r.id ? 'bg-brand-light' : 'hover:bg-slate-50'}`}>
                  <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-slate-75 text-slate-500">{i === 0 ? <Crown size={16} className="text-amber-500" /> : i % 2 ? <Users2 size={16} /> : <User size={16} />}</span>
                  <span className="flex-1"><span className="block text-sm font-medium text-slate-700">{r.name}</span><span className="block text-xs text-slate-400">{r.user_count} users</span></span>
                </button>
              </li>
            ))}
          </ul>
        </div>

        {/* Permissions Matrix */}
        <div className="rounded-2xl border border-slate-100 bg-white p-5 shadow-sm">
          <div className="mb-4 flex items-center justify-between">
            <div>
              <h2 className="text-lg font-semibold text-ink">Permissions Matrix</h2>
              <p className="text-sm text-slate-400">Role: <span className="font-medium text-slate-600">{draft?.name ?? '—'}</span></p>
            </div>
            <div className="flex items-center gap-4 text-xs text-slate-500">
              <span className="flex items-center gap-1.5"><span className="h-3 w-6 rounded-full bg-brand" /> Allowed</span>
              <span className="flex items-center gap-1.5"><span className="h-3 w-6 rounded-full bg-slate-200" /> Not Allowed</span>
            </div>
          </div>
          {matrixQ.isLoading || !draft ? (
            <div className="py-10 text-center text-sm text-slate-400"><Loader2 size={18} className="mx-auto animate-spin" /> Loading matrix…</div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-slate-100 text-left text-sm text-slate-600">
                    <th className="py-2 pr-2 font-semibold">Module</th>
                    {actions.map((a) => <th key={a} className="px-2 py-2 text-center font-semibold">{ACTION_LABELS[a] ?? a}</th>)}
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-50">
                  {modules.map((m) => (
                    <tr key={m.code}>
                      <td className="py-2 pr-2 font-medium text-slate-600">{m.label}</td>
                      {actions.map((a) => (
                        <td key={a} className="px-2 py-2 text-center">
                          <Switch on={!!draft.permissions[m.code]?.[a]} onClick={() => toggle(m.code, a)} />
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* Access Scope & Limits */}
        <div className="flex flex-col rounded-2xl border border-slate-100 bg-white p-5 shadow-sm">
          <h2 className="text-lg font-semibold text-ink">Access Scope &amp; Limits</h2>
          {draft && (
            <div className="mt-4 flex-1 space-y-4">
              <div>
                <label className="mb-1 block text-sm font-medium text-slate-600">Property Access</label>
                <Select value={draft.property_scope} onChange={(e) => setScope('property_scope', e.target.value)} className={inp}>
                  <option value="own">Own Property{propertyName ? ` (${propertyName})` : ''}</option>
                  <option value="all">All Properties</option>
                </Select>
              </div>
              <div>
                <label className="mb-1 block text-sm font-medium text-slate-600">Record Access</label>
                <Select value={draft.record_scope} onChange={(e) => setScope('record_scope', e.target.value)} className={inp}>
                  <option value="assigned">Assigned Records Only</option>
                  <option value="property">All Property Records</option>
                  <option value="own">Own Records</option>
                </Select>
                <p className="mt-1 text-xs text-slate-400">Users can access only records assigned to them (e.g. their check-ins, reservations).</p>
              </div>
              <div>
                <div className="mb-1 text-sm font-medium text-slate-600">Financial Limits (INR)</div>
                <label className="mb-1 block text-xs text-slate-500">Maximum Discount per Transaction</label>
                <input type="number" value={draft.max_discount ?? 0} onChange={(e) => setLimit('max_discount', Number(e.target.value))} className={inp} />
                <label className="mb-1 mt-3 block text-xs text-slate-500">Maximum Refund Amount</label>
                <input type="number" value={draft.max_refund ?? 0} onChange={(e) => setLimit('max_refund', Number(e.target.value))} className={inp} />
              </div>
              <div className="rounded-xl bg-amber-50 p-3 text-xs text-amber-700">Financial limits help prevent unauthorised discounts and refunds.</div>
            </div>
          )}
          <div className="mt-4 flex gap-2">
            <button onClick={handleClone} disabled={!draft} className="flex flex-1 items-center justify-center gap-2 rounded-xl border border-slate-200 px-3 py-2.5 text-sm font-medium text-slate-600 hover:bg-slate-50 disabled:opacity-50"><Copy size={15} /> Clone Role</button>
            <button onClick={save} disabled={!dirty || saving} className="flex flex-1 items-center justify-center gap-2 rounded-xl bg-brand px-3 py-2.5 text-sm font-medium text-white enabled:hover:bg-brand/90 disabled:opacity-50">{saving ? <Loader2 size={15} className="animate-spin" /> : <Save size={15} />} Save</button>
          </div>
        </div>
      </div>
    </div>
  )
}

const inp = 'w-full rounded-lg border border-slate-200 px-3 py-2 text-sm outline-none focus:border-brand'

function Switch({ on, onClick }: { on: boolean; onClick: () => void }) {
  return (
    <button onClick={onClick} className={`h-5 w-9 rounded-full p-0.5 transition-colors ${on ? 'bg-brand' : 'bg-slate-200'}`}>
      <span className={`block h-4 w-4 rounded-full bg-white shadow transition-transform ${on ? 'translate-x-4' : ''}`} />
    </button>
  )
}
