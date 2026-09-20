import { useMemo, useState } from 'react'
import Select from '../components/Select'
import { useNavigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Users, UserPlus, Download, MoreHorizontal, Pencil, Pause, Play, Mail, UserX,
  ShieldCheck, Monitor, ChevronLeft, ChevronRight, ChevronsUpDown, X, Loader2,
  AlertTriangle, ShieldAlert, CheckCircle2, Search,
} from 'lucide-react'
import { FILTER_SELECT } from '../lib/controls'
import {
  listUsers, getUserStats, listDepartments, listRoles, listProperties,
  createUser, updateUser, setUserActive, revokeSessions, getUserActivity,
  type ManagedUser, type ActivityEntry,
} from '../api'
import { Crumbs } from '../components/Crumbs'

const PAGE_SIZE = 10
const AVATAR = ['bg-teal-500', 'bg-rose-500', 'bg-blue-500', 'bg-purple-500', 'bg-amber-500', 'bg-indigo-500', 'bg-teal-600', 'bg-slate-500']

function initials(n: string) { return n.split(' ').map((w) => w[0]).slice(0, 2).join('').toUpperCase() }
function statusView(s: string) {
  if (s === 'active') return { label: 'Active', cls: 'bg-emerald-50 text-emerald-600' }
  if (s === 'invited') return { label: 'Invited', cls: 'bg-amber-50 text-amber-600' }
  return { label: 'Suspended', cls: 'bg-red-50 text-red-600' }
}
function mfaView(m: string) {
  if (m === 'enabled') return { label: 'Enabled', cls: 'bg-emerald-50 text-emerald-600' }
  if (m === 'pending') return { label: 'Pending', cls: 'bg-amber-50 text-amber-600' }
  return { label: 'Disabled', cls: 'bg-red-50 text-red-500' }
}
function errMsg(e: unknown): string {
  const err = e as { response?: { data?: { detail?: string } }; message?: string }
  return err.response?.data?.detail ?? err.message ?? 'Something went wrong'
}

export default function UserManagement() {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const [query, setQuery] = useState('')
  const [statusF, setStatusF] = useState('')
  const [roleF, setRoleF] = useState('')
  const [propF, setPropF] = useState('')
  const [deptF, setDeptF] = useState('')
  const [page, setPage] = useState(1)
  const [menuFor, setMenuFor] = useState<string | null>(null)
  const [modal, setModal] = useState<{ mode: 'create' | 'edit'; user?: ManagedUser } | null>(null)
  const [activityFor, setActivityFor] = useState<ManagedUser | null>(null)
  const [toast, setToast] = useState('')

  const usersQ = useQuery({
    queryKey: ['users', query, statusF, roleF, propF, deptF],
    queryFn: () => listUsers({ query: query || undefined, status: statusF || undefined, roleId: roleF || undefined, propertyId: propF || undefined, departmentId: deptF || undefined }),
    retry: false,
  })
  const statsQ = useQuery({ queryKey: ['userStats'], queryFn: getUserStats, retry: false })
  const deptsQ = useQuery({ queryKey: ['departments'], queryFn: listDepartments, retry: false })
  const rolesQ = useQuery({ queryKey: ['roles'], queryFn: listRoles, retry: false })
  const propsQ = useQuery({ queryKey: ['properties'], queryFn: listProperties, retry: false })

  const denied = usersQ.isError && [401, 403].includes((usersQ.error as { response?: { status?: number } })?.response?.status ?? 0)
  const users = usersQ.data ?? []
  const pageCount = Math.max(1, Math.ceil(users.length / PAGE_SIZE))
  const pageUsers = useMemo(() => users.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE), [users, page])

  function refresh() { qc.invalidateQueries({ queryKey: ['users'] }); qc.invalidateQueries({ queryKey: ['userStats'] }) }
  function flash(m: string) { setToast(m); setTimeout(() => setToast(''), 2500) }

  async function act(fn: () => Promise<unknown>, msg: string) {
    try { await fn(); flash(msg); refresh() } catch (e) { flash(errMsg(e)) }
    setMenuFor(null)
  }

  return (
    <div className="space-y-4">
      <Crumbs title="Users" trail={[{ label: 'Administration', to: '/admin' },
        { label: 'Users' }]} />
      {/* Title and actions on one line, as on Reservations. */}
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <h1 className="flex items-center gap-2 text-display text-ink"><Users size={26} className="text-brand" /> Users</h1>
        {!denied && (
          <div className="flex flex-wrap items-center gap-2">
            <button disabled title="Export — coming soon" className="flex items-center gap-1.5 whitespace-nowrap rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-600 hover:bg-slate-50 disabled:opacity-40"><Download size={15} /> Export</button>
            <button onClick={() => navigate('/admin/users/invite')} className="flex items-center gap-1.5 whitespace-nowrap rounded-lg bg-brand px-3 py-2 text-sm font-medium text-white hover:bg-brand-dark disabled:opacity-40"><UserPlus size={15} /> Invite User</button>
          </div>
        )}
      </div>

      {toast && <div className="flex items-center gap-2 rounded-xl bg-emerald-50 px-4 py-3 text-sm text-emerald-700"><CheckCircle2 size={16} />{toast}</div>}
      {denied && <div className="flex items-center gap-2 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700"><ShieldAlert size={16} />You do not have permission to manage users.</div>}

      {!denied && (
        <>
          {/* KPI cards (live) */}
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <Kpi label="Active Users" value={statsQ.data?.active} delta="12%" up tone="emerald" icon={<Users size={20} />} />
            <Kpi label="Invited" value={statsQ.data?.invited} delta="20%" up tone="amber" icon={<Mail size={20} />} />
            <Kpi label="Suspended" value={statsQ.data?.suspended} delta="0%" tone="red" icon={<UserX size={20} />} />
            <Kpi label="Active Sessions" value={statsQ.data?.active_sessions} delta="14%" up tone="blue" icon={<Monitor size={20} />} />
          </div>

          {/* Filters (live, server-side): one row, no captions -- each
              select's "All X" option says what it filters. */}
          <div className="flex flex-wrap items-center gap-2">
            <Filter label="Role" value={roleF} onChange={(v) => { setRoleF(v); setPage(1) }} options={rolesQ.data} all="All Roles" />
            <Filter label="Property / Outlet" value={propF} onChange={(v) => { setPropF(v); setPage(1) }}
              options={propsQ.data?.map((p) => ({ id: p.id, name: p.name }))} all="All Properties" />
            <Filter label="Department" value={deptF} onChange={(v) => { setDeptF(v); setPage(1) }} options={deptsQ.data} all="All Departments" />
            <Filter label="Status" value={statusF} onChange={(v) => { setStatusF(v); setPage(1) }}
              options={[{ id: 'active', name: 'Active' }, { id: 'invited', name: 'Invited' }, { id: 'suspended', name: 'Suspended' }]} all="All Statuses" />
            <div className="flex min-w-[240px] flex-1 items-center justify-end gap-2">
              {(query || roleF || propF || deptF || statusF) && usersQ.data && (
                <span className="whitespace-nowrap text-sm text-slate-500">
                  {users.length} {users.length === 1 ? 'user' : 'users'}
                </span>
              )}
              <div className="relative min-w-0 max-w-sm flex-1">
                <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                <input value={query} onChange={(e) => { setQuery(e.target.value); setPage(1) }} placeholder="Search users by name, ID or email…"
                  aria-label="Search users"
                  className="w-full rounded-lg border border-slate-200 bg-slate-50 py-2 pl-9 pr-3 text-sm outline-none focus:border-brand" />
              </div>
            </div>
          </div>

          {/* Table */}
          <div className="rounded-2xl border border-slate-100 bg-white shadow-sm">
            {usersQ.isLoading && <div className="flex items-center gap-2 p-8 text-sm text-slate-400"><Loader2 size={16} className="animate-spin" /> Loading users…</div>}
            {usersQ.isError && !denied && <div className="flex items-center gap-2 p-8 text-sm text-red-600"><AlertTriangle size={16} /> {(usersQ.error as Error)?.message}</div>}
            {users.length === 0 && usersQ.data && <div className="p-8 text-center text-sm text-slate-400">No users match your filters.</div>}
            {users.length > 0 && (
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-slate-100 bg-slate-50/60 text-left text-sm text-slate-600">
                    {['User', 'Employee ID', 'Department', 'Properties / Outlets', 'Roles', 'Status', 'Last Login', 'MFA'].map((h) => (
                      <th key={h} className="px-4 py-3 font-semibold">{h} <ChevronsUpDown size={12} className="ml-0.5 inline text-slate-300" /></th>
                    ))}
                    <th className="px-4 py-3 text-right font-semibold">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-50">
                  {pageUsers.map((u, i) => {
                    const sv = statusView(u.status); const mv = mfaView(u.mfa_status)
                    return (
                      <tr key={u.id} className="text-slate-700">
                        <td className="px-4 py-3">
                          <div className="flex items-center gap-3">
                            <span className={`flex h-9 w-9 items-center justify-center rounded-full text-xs font-semibold text-white ${AVATAR[i % AVATAR.length]}`}>{initials(u.display_name)}</span>
                            <div><div className="font-medium">{u.display_name}</div><div className="text-xs text-slate-400">{u.subject_id}</div></div>
                          </div>
                        </td>
                        <td className="px-4 py-3 text-slate-600">{u.employee_code ?? '—'}</td>
                        <td className="px-4 py-3 text-slate-600">{u.department ?? '—'}</td>
                        <td className="px-4 py-3 text-slate-600">{u.properties.join(', ') || '—'}</td>
                        <td className="px-4 py-3"><div className="flex flex-wrap gap-1">{u.roles.length ? u.roles.map((r) => <span key={r} className="rounded-md bg-blue-50 px-2 py-0.5 text-xs font-medium text-blue-600">{r}</span>) : '—'}</div></td>
                        <td className="px-4 py-3"><span className={`rounded-full px-2.5 py-1 text-xs font-medium ${sv.cls}`}>{sv.label}</span></td>
                        <td className="px-4 py-3 text-xs text-slate-500">{u.last_login_at ?? '-'}</td>
                        <td className="px-4 py-3"><span className={`rounded-full px-2.5 py-1 text-xs font-medium ${mv.cls}`}>{mv.label}</span></td>
                        <td className="px-4 py-3">
                          <div className="relative flex justify-end">
                            <button onClick={() => setMenuFor(menuFor === u.id ? null : u.id)} className="rounded-lg p-2 text-slate-400 hover:bg-slate-50 hover:text-slate-600"><MoreHorizontal size={16} /></button>
                            {menuFor === u.id && (
                              <div className="absolute right-0 top-9 z-20 w-44 rounded-xl border border-slate-100 bg-white py-1 shadow-lg">
                                <button onClick={() => { navigate(`/admin/users/${u.id}/edit`); setMenuFor(null) }} className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-slate-600 hover:bg-slate-50"><Pencil size={14} /> Edit Access</button>
                                {u.status === 'active'
                                  ? <button onClick={() => act(() => setUserActive(u.id, false), `${u.display_name} suspended.`)} className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-red-600 hover:bg-slate-50"><Pause size={14} /> Suspend</button>
                                  : <button onClick={() => act(() => setUserActive(u.id, true), `${u.display_name} activated.`)} className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-emerald-600 hover:bg-slate-50"><Play size={14} /> Activate</button>}
                                <div className="my-1 border-t border-slate-100" />
                                <button onClick={() => act(() => revokeSessions(u.id), `Sessions revoked for ${u.display_name}.`)} className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-slate-600 hover:bg-slate-50"><Monitor size={14} /> Revoke Sessions</button>
                                <button onClick={() => { setActivityFor(u); setMenuFor(null) }} className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-slate-600 hover:bg-slate-50"><ShieldCheck size={14} /> View Activity</button>
                              </div>
                            )}
                          </div>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            )}
            {users.length > 0 && (
              <div className="flex items-center justify-between border-t border-slate-100 px-4 py-3 text-sm text-slate-500">
                <span>Showing {(page - 1) * PAGE_SIZE + 1} – {Math.min(page * PAGE_SIZE, users.length)} of {users.length} users</span>
                <div className="flex items-center gap-1">
                  <button disabled={page <= 1} onClick={() => setPage((p) => p - 1)} className="rounded-lg p-1.5 hover:bg-slate-50 disabled:opacity-40"><ChevronLeft size={16} /></button>
                  {Array.from({ length: pageCount }, (_, i) => i + 1).map((p) => (
                    <button key={p} onClick={() => setPage(p)} className={`h-8 w-8 rounded-lg text-sm ${p === page ? 'bg-brand text-white' : 'hover:bg-slate-50'}`}>{p}</button>
                  ))}
                  <button disabled={page >= pageCount} onClick={() => setPage((p) => p + 1)} className="rounded-lg p-1.5 hover:bg-slate-50 disabled:opacity-40"><ChevronRight size={16} /></button>
                </div>
              </div>
            )}
          </div>
        </>
      )}

      {modal && <UserModal mode={modal.mode} user={modal.user} onClose={() => setModal(null)} onDone={(m) => { flash(m); refresh() }} />}
      {activityFor && <ActivityModal user={activityFor} onClose={() => setActivityFor(null)} />}
    </div>
  )
}

function Kpi({ label, value, delta, up, tone, icon }: { label: string; value?: number; delta: string; up?: boolean; tone: string; icon: React.ReactNode }) {
  const tile = { emerald: 'bg-emerald-100 text-emerald-600', amber: 'bg-amber-100 text-amber-600', red: 'bg-red-100 text-red-500', blue: 'bg-blue-100 text-blue-600' }[tone] ?? 'bg-slate-75'
  const stroke = { emerald: '#059669', amber: '#d97706', red: '#ef4444', blue: '#2563eb' }[tone] ?? '#0f766e'
  const pts = { emerald: '0,24 20,20 40,22 60,14 80,16 100,8', amber: '0,26 20,22 40,24 60,18 80,20 100,12', red: '0,20 20,18 40,20 60,17 80,20 100,18', blue: '0,26 20,20 40,22 60,12 80,14 100,6' }[tone] ?? ''
  return (
    <div className="rounded-2xl border border-slate-100 bg-white p-4 shadow-sm">
      <div className="flex items-center gap-3">
        <span className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-full ${tile}`}>{icon}</span>
        <div className="min-w-0 flex-1">
          {/* One line, and wide enough to actually read it. "Active Users"
              and "Active Sessions" wrapped over two lines while "Invited" and
              "Suspended" did not, and a grid row is as tall as its tallest
              cell -- so two short tiles carried 24px of blank space to keep
              the long ones company. Truncating instead just moved the fault:
              the label became "Active Us...". The space came from the
              sparkline, which is a decoration, not the content. */}
          <div className="truncate text-sm font-semibold text-slate-800" title={label}>{label}</div>
          <div className="mt-0.5 flex items-center gap-2">
            <span className="text-display text-slate-800">{value ?? '—'}</span>
            <span className={`text-xs font-medium ${up ? 'text-emerald-600' : 'text-slate-400'}`}>{up ? '↑' : '→'} {delta}</span>
          </div>
        </div>
        <svg viewBox="0 0 100 32" className="h-8 w-12 shrink-0" preserveAspectRatio="none"><polyline points={pts} fill="none" stroke={stroke} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>
      </div>
    </div>
  )
}

function Filter({ label, value, onChange, options, all }: { label: string; value: string; onChange: (v: string) => void; options?: { id: string; name: string }[]; all: string }) {
  return (
    <Select blankIsChoice value={value} onChange={(e) => onChange(e.target.value)} aria-label={label}
      className={`${FILTER_SELECT} bg-white outline-none focus:border-brand`}>
      <option value="">{all}</option>
      {options?.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
    </Select>
  )
}

function UserModal({ mode, user, onClose, onDone }: { mode: 'create' | 'edit'; user?: ManagedUser; onClose: () => void; onDone: (m: string) => void }) {
  const [name, setName] = useState(user?.display_name ?? '')
  const [subject, setSubject] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  async function submit() {
    setErr(''); setBusy(true)
    try {
      if (mode === 'edit' && user) { await updateUser(user.id, { version: user.version, display_name: name.trim() }); onDone('User updated.') }
      else { const u = await createUser({ subject_id: subject.trim(), display_name: name.trim() }); onDone(`User ${u.display_name} invited.`) }
      onClose()
    } catch (e) { setErr(errMsg(e)) } finally { setBusy(false) }
  }
  return (
    <Modal title={mode === 'edit' ? 'Edit User' : 'Invite User'} onClose={onClose}>
      {err && <div className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{err}</div>}
      <label className="mt-3 block"><span className="mb-1 block text-sm font-medium text-slate-600">Display Name</span><input value={name} onChange={(e) => setName(e.target.value)} className={inp} autoFocus /></label>
      {mode === 'create' && <label className="mt-3 block"><span className="mb-1 block text-sm font-medium text-slate-600">Subject / Login ID</span><input value={subject} onChange={(e) => setSubject(e.target.value)} placeholder="e.g. jdoe" className={inp} /></label>}
      <div className="mt-5 flex justify-end gap-2">
        <button onClick={onClose} className="rounded-lg border border-slate-200 px-4 py-2 text-sm text-slate-600 hover:bg-slate-50">Cancel</button>
        <button onClick={submit} disabled={busy || !name.trim() || (mode === 'create' && !subject.trim())} className="flex items-center gap-2 rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white enabled:hover:bg-brand/90 disabled:opacity-50">{busy ? <Loader2 size={15} className="animate-spin" /> : (mode === 'edit' ? <Pencil size={15} /> : <UserPlus size={15} />)} {mode === 'edit' ? 'Save' : 'Invite'}</button>
      </div>
    </Modal>
  )
}

function ActivityModal({ user, onClose }: { user: ManagedUser; onClose: () => void }) {
  const { data, isLoading } = useQuery({ queryKey: ['activity', user.id], queryFn: () => getUserActivity(user.id) })
  return (
    <Modal title={`Activity — ${user.display_name}`} onClose={onClose}>
      {isLoading && <div className="mt-4 flex items-center gap-2 text-sm text-slate-400"><Loader2 size={15} className="animate-spin" /> Loading…</div>}
      {data && data.length === 0 && <div className="mt-4 text-sm text-slate-400">No recorded activity.</div>}
      <ul className="mt-3 max-h-80 space-y-2 overflow-y-auto">
        {data?.map((a: ActivityEntry, i) => (
          <li key={i} className="rounded-lg bg-slate-50 px-3 py-2 text-sm">
            <div className="flex justify-between"><span className="font-medium text-slate-700">{a.action}</span><span className="text-xs text-slate-400">{a.occurred_at}</span></div>
            {a.reason && <div className="text-xs text-slate-500">{a.reason}</div>}
          </li>
        ))}
      </ul>
    </Modal>
  )
}

const inp = 'w-full rounded-lg border border-slate-200 px-3 py-2.5 text-sm outline-none focus:border-brand'
function Modal({ title, children, onClose }: { title: string; children: React.ReactNode; onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4" onClick={onClose}>
      <div className="w-full max-w-md rounded-2xl bg-white p-6 shadow-xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between"><h2 className="text-lg font-semibold text-ink">{title}</h2><button onClick={onClose} className="text-slate-400 hover:text-slate-600"><X size={18} /></button></div>
        {children}
      </div>
    </div>
  )
}
