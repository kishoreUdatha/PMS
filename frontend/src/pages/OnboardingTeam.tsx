import { useState } from 'react'
import Select from '../components/Select'
import { Link, useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle, ArrowLeft, ArrowRight, BedDouble, Building2, CheckCircle2,
  ClipboardList, Clock, Info, Loader2, Plus, Shield, Sparkles, Trash2,
  UserPlus, Users, Wallet,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { WizardFrame, ContinueButton, obInput } from './Onboarding'
import ActionsMenu from '../components/ActionsMenu'
import { useAuth } from '../auth/AuthContext'
import {
  listRoles, listInvitations, createInvitation,
  type NamedOption, type InvitationResult,
} from '../api'
import { errorText } from '../lib/forms'

/**
 * Onboarding step 7 — the people who will run the property.
 *
 * Invitations are queued on the screen and created in one go, the way the
 * mockup has it. That is not only presentation: creating an invitation makes a
 * user record and grants roles, so building the list first lets a property get
 * three names and three roles right before any of it is written.
 *
 * What this cannot do is send an email. There is no mail transport configured
 * in this deployment, so an invitation exists and is listed, but nobody is
 * told about it. Saying so is better than a "Send invites" button that quietly
 * sends nothing.
 */

/** An icon for a role, chosen from what the role is called. */
const ROLE_ICONS: [RegExp, LucideIcon][] = [
  [/manager|admin/i, Shield],
  [/front|reservation|reception/i, BedDouble],
  [/housekeep|cleaning/i, Sparkles],
  [/account|finance|report|cashier/i, Wallet],
  [/sales|distribution/i, Building2],
  [/pos|outlet|restaurant/i, ClipboardList],
]
const iconFor = (name: string): LucideIcon =>
  ROLE_ICONS.find(([re]) => re.test(name))?.[1] ?? Users

/** One line on what a role is for, in the property's terms. */
const ROLE_BLURB: [RegExp, string][] = [
  [/resort manager|administrator/i, 'Property operations'],
  [/front desk/i, 'Bookings and check-ins'],
  [/housekeep/i, 'Room tasks and status'],
  [/report|finance|account/i, 'Payments and reports'],
  [/rates/i, 'Pricing and inventory'],
  [/reservation/i, 'Bookings and enquiries'],
]
const blurbFor = (name: string): string =>
  ROLE_BLURB.find(([re]) => re.test(name))?.[1] ?? 'Custom access'

/** A queued row: entered here, not yet written anywhere. */
interface Queued {
  key: string
  full_name: string
  email: string
  role_id: string
  role_name: string
  /** Set once it has been sent, so a retry does not invite twice. */
  result?: 'done' | string
}

const initials = (name: string) =>
  name.trim().split(/\s+/).slice(0, 2).map((p) => p[0] ?? '').join('').toUpperCase()

export function OnboardingTeam() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const { session } = useAuth()
  const propertyId = localStorage.getItem('property_id') ?? ''

  const { data: roles } = useQuery({
    queryKey: ['roles'],
    queryFn: listRoles,
  })
  const { data: invited } = useQuery({
    queryKey: ['invitations'],
    queryFn: listInvitations,
  })

  const [queue, setQueue] = useState<Queued[]>([])
  const [draft, setDraft] = useState({ full_name: '', email: '', role_id: '' })
  const [err, setErr] = useState('')

  const roleName = (id: string) =>
    (roles ?? []).find((r) => r.id === id)?.name ?? ''

  const valid = draft.full_name.trim() !== ''
    && /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(draft.email.trim())
    && draft.role_id !== ''

  const add = () => {
    setErr('')
    const email = draft.email.trim().toLowerCase()
    if (queue.some((q) => q.email === email)) {
      setErr(`${email} is already in the list.`)
      return
    }
    if ((invited ?? []).some((i) => i.email.toLowerCase() === email)) {
      setErr(`${email} has already been invited to this property.`)
      return
    }
    setQueue((q) => [...q, {
      key: `${email}-${Date.now()}`,
      full_name: draft.full_name.trim(),
      email,
      role_id: draft.role_id,
      role_name: roleName(draft.role_id),
    }])
    setDraft({ full_name: '', email: '', role_id: draft.role_id })
  }

  const send = useMutation({
    mutationFn: async () => {
      const out: Queued[] = []
      for (const row of queue) {
        if (row.result === 'done') { out.push(row); continue }
        try {
          await createInvitation({
            property_id: propertyId,
            full_name: row.full_name,
            email: row.email,
            role_ids: [row.role_id],
            mfa_required: false,
            temporary_access: false,
            refund_approval: false,
          })
          out.push({ ...row, result: 'done' })
        } catch (e) {
          const detail = errorText(e, 'Could not be invited.')
          out.push({ ...row, result: detail })
        }
      }
      return out
    },
    onSuccess: (out) => {
      setQueue(out)
      qc.invalidateQueries({ queryKey: ['invitations'] })
      qc.invalidateQueries({ queryKey: ['onboarding', propertyId] })
      const failed = out.filter((r) => r.result && r.result !== 'done')
      if (failed.length === 0) navigate('/onboarding/import')
      else setErr(`${failed.length} of ${out.length} could not be invited.`)
    },
    onError: () => setErr('Those invitations could not be sent.'),
  })

  const pending = queue.filter((q) => q.result !== 'done')

  return (
    <WizardFrame step="team" stepNo={7}
      title="Invite your team"
      blurb="Give each team member the access they need."
      footer={<>
        <span className="flex gap-2">
          <button onClick={() => navigate('/onboarding/billing')}
            className="flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-medium text-slate-600 hover:bg-slate-50">
            <ArrowLeft size={15} /> Back
          </button>
          <button onClick={() => navigate('/onboarding/import')}
            className="rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-medium text-slate-600 hover:bg-slate-50">
            Skip for now
          </button>
        </span>
        {pending.length > 0 ? (
          <button onClick={() => send.mutate()} disabled={send.isPending}
            className="flex items-center gap-2 rounded-xl bg-brand px-6 py-3 text-sm font-semibold text-white hover:bg-brand-dark disabled:opacity-50">
            {send.isPending && <Loader2 size={15} className="animate-spin" />}
            {/* The mockup says "Send Invites & Continue". Nothing is sent —
                there is no mail transport — so the verb is the one that is
                actually true of what the button does. */}
            Create {pending.length} invitation{pending.length === 1 ? '' : 's'}
            {' '}&amp; continue <ArrowRight size={15} />
          </button>
        ) : (
          <ContinueButton step="team"
            onClick={() => navigate('/onboarding/import')} />
        )}
      </>}>

      {/* Whoever is setting the property up. Their access is not in question
          here -- they already have it -- so the row states it rather than
          offering to change it. */}
      <div className="flex flex-wrap items-center gap-3 rounded-2xl border border-slate-100 bg-white px-5 py-4">
        <span className="grid h-11 w-11 shrink-0 place-items-center rounded-full bg-brand/10 text-sm font-bold text-brand">
          {initials(session?.display_name ?? '?')}
        </span>
        <span className="flex-1">
          <span className="block font-semibold text-slate-800">
            {session?.display_name ?? 'You'}
          </span>
          <span className="block text-sm text-slate-500">Owner</span>
        </span>
        <span className="rounded-full bg-brand/10 px-3 py-1 text-xs font-semibold text-brand">
          Full access
        </span>
        <span className="text-sm text-slate-400">Account owner</span>
      </div>

      <div className="mt-4 rounded-2xl border border-slate-100 bg-white p-5">
        <h2 className="text-lg font-semibold text-ink">Add a team member</h2>
        <div className="mt-4 grid items-end gap-3 lg:grid-cols-[1fr_1fr_14rem_auto]">
          <label className="block">
            <span className="mb-1 block text-sm font-medium text-slate-600">
              Full name
            </span>
            <input className={obInput} value={draft.full_name}
              placeholder="e.g. Priya Sharma"
              onChange={(e) => setDraft({ ...draft, full_name: e.target.value })}
              onKeyDown={(e) => { if (e.key === 'Enter' && valid) add() }} />
          </label>
          <label className="block">
            <span className="mb-1 block text-sm font-medium text-slate-600">
              Email address
            </span>
            <input className={obInput} type="email" value={draft.email}
              placeholder="e.g. priya@hotel.com"
              onChange={(e) => setDraft({ ...draft, email: e.target.value })}
              onKeyDown={(e) => { if (e.key === 'Enter' && valid) add() }} />
          </label>
          <label className="block">
            <span className="mb-1 block text-sm font-medium text-slate-600">
              Role
            </span>
            <Select className={obInput} value={draft.role_id}
              onChange={(e) => setDraft({ ...draft, role_id: e.target.value })}>
              <option value="">Select a role</option>
              {(roles ?? []).map((r: NamedOption) => (
                <option key={r.id} value={r.id}>{r.name}</option>
              ))}
            </Select>
          </label>
          <button onClick={add} disabled={!valid}
            className="flex items-center gap-2 rounded-xl bg-brand px-5 py-2.5 text-sm font-semibold text-white hover:bg-brand-dark disabled:opacity-40">
            <Plus size={15} /> Add member
          </button>
        </div>
        {err && (
          <p className="mt-3 flex items-start gap-2 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700">
            <AlertTriangle size={16} className="mt-0.5 shrink-0" /> {err}
          </p>
        )}
      </div>

      {/* A standing section, not one that appears once something is in it.
          An empty table with its headings showing is what tells you the
          screen has a queue at all, and what the queue will hold. */}
      <div className="mt-4 overflow-hidden rounded-2xl border border-slate-100 bg-white">
          <h2 className="px-5 py-4 text-lg font-semibold text-ink">
            Queued invitations ({queue.length})
          </h2>
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-y border-slate-100 bg-slate-50/60 text-slate-600">
                <th className="px-5 py-2.5 font-semibold">Name</th>
                <th className="px-3 py-2.5 font-semibold">Email</th>
                <th className="px-3 py-2.5 font-semibold">Role</th>
                <th className="px-3 py-2.5 font-semibold">Status</th>
                <th className="w-12 px-3 py-2.5" />
              </tr>
            </thead>
            <tbody>
              {queue.length === 0 && (
                <tr>
                  <td colSpan={5} className="px-5 py-10 text-center text-sm text-slate-400">
                    Nobody queued yet. Add a team member above — nothing is
                    written until you create the invitations.
                  </td>
                </tr>
              )}
              {queue.map((row) => (
                <tr key={row.key} className="border-b border-slate-100 last:border-0">
                  <td className="px-5 py-3 font-medium text-slate-800">
                    {row.full_name}
                  </td>
                  <td className="px-3 py-3 text-slate-600">{row.email}</td>
                  <td className="px-3 py-3 text-slate-600">{row.role_name}</td>
                  <td className="px-3 py-3">
                    {row.result === 'done' ? (
                      <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-50 px-2.5 py-1 text-xs font-medium text-emerald-700">
                        <CheckCircle2 size={13} /> Invitation created
                      </span>
                    ) : row.result ? (
                      <span className="inline-flex items-center gap-1.5 rounded-full bg-red-50 px-2.5 py-1 text-xs font-medium text-red-700">
                        <AlertTriangle size={13} /> {row.result}
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1.5 rounded-full bg-slate-75 px-2.5 py-1 text-xs font-medium text-slate-600">
                        <Clock size={13} /> Ready to invite
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-3">
                    <ActionsMenu label={`Actions for ${row.full_name}`} items={[
                      { label: 'Remove from list', icon: Trash2, tone: 'danger',
                        disabled: row.result === 'done',
                        hint: row.result === 'done'
                          ? 'Already created — manage it under Staff'
                          : 'Takes it off the list; nothing has been written yet',
                        onSelect: () => setQueue((q) =>
                          q.filter((x) => x.key !== row.key)) },
                    ]} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
      </div>

      {(invited ?? []).length > 0 && (
        <div className="mt-4 rounded-2xl border border-slate-100 bg-white p-5">
          <h2 className="text-lg font-semibold text-ink">
            Already invited ({(invited ?? []).length})
          </h2>
          <div className="mt-3 flex flex-wrap gap-2">
            {(invited ?? []).map((i: InvitationResult) => (
              <span key={i.id}
                className="flex items-center gap-2 rounded-lg bg-slate-50 px-3 py-1.5 text-sm text-slate-600">
                <UserPlus size={13} className="text-slate-400" />
                {i.full_name}
                <span className="text-xs text-slate-400">{i.email}</span>
              </span>
            ))}
          </div>
        </div>
      )}

      <div className="mt-4 rounded-2xl border border-slate-100 bg-white p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-lg font-semibold text-ink">Role overview</h2>
          <Link to="/admin/roles"
            className="flex items-center gap-1.5 text-sm font-semibold text-brand hover:underline">
            Customize permissions <ArrowRight size={14} />
          </Link>
        </div>
        {/* The property's own roles, not a fixed four. The mockup names
            Manager, Front Desk, Housekeeping and Accountant; inventing those
            here would describe access that does not exist. */}
        <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {(roles ?? []).slice(0, 8).map((r) => {
            const Icon = iconFor(r.name)
            return (
              <div key={r.id} className="rounded-xl border border-slate-200 p-4">
                <Icon size={20} className="text-brand" />
                <p className="mt-2 font-semibold text-slate-800">{r.name}</p>
                <p className="text-sm text-slate-500">{blurbFor(r.name)}</p>
              </div>
            )
          })}
          {(roles ?? []).length === 0 && (
            <p className="text-sm text-slate-400">No roles set up yet.</p>
          )}
        </div>
      </div>

      {/* There is no mail transport in this deployment. An invitation is a real
          record with real access attached, but nothing lands in an inbox, and a
          button labelled "Send invites" would be claiming otherwise. */}
      <p className="mt-4 flex items-start gap-2 rounded-xl bg-sky-50 px-4 py-3 text-sm text-sky-800">
        <Info size={16} className="mt-0.5 shrink-0" />
        <span>
          <strong>No email is sent.</strong> Creating an invitation sets up the
          account and its access, but there is no mail service configured yet —
          tell people their sign-in yourself, or manage them later under{' '}
          <Link to="/staff" className="font-semibold underline">Staff</Link>.
        </span>
      </p>
    </WizardFrame>
  )
}
