import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { AlertTriangle, ArrowUpRight, Pencil } from 'lucide-react'
import {
  approveDeletion, correctInvitedUser, deletionSurvey, getOrganization,
  listDeletionRequests, renameOrganization, requestDeletion, resendInvitation,
  suspendOrganization, reactivateOrganization, whoami, withdrawDeletion,
  type DeletionRequestRow, type DeletionSurvey, type OrganizationDetail,
} from '../api'
import { subscriptionDetail, money, type SubscriptionDetail } from '../billingApi'
import {
  Busy, Button, Card, DataTable, ErrorNote, Metrics, Note, Page, Panel, Pill,
  Td, askReason, askText, errorText, notify,
} from '../ui'

/** Screen 06 — one tenant's account.
 *
 *  Everything a platform operator needs to answer "what is this customer on
 *  and who works there", and nothing about what their guests did. Opening a
 *  tenant's operational data is a separate, approved support path that does
 *  not exist yet — deliberately, because it needs consent and a time box
 *  rather than a button here.
 */
export default function TenantDetail() {
  const { orgId = '' } = useParams()
  const [org, setOrg] = useState<OrganizationDetail | null>(null)
  const [sub, setSub] = useState<SubscriptionDetail | null>(null)
  const [busy, setBusy] = useState(true)
  const [err, setErr] = useState('')
  const [caps, setCaps] = useState<string[]>([])
  const [meId, setMeId] = useState('')
  const [pending, setPending] = useState<DeletionRequestRow | null>(null)
  const [preview, setPreview] = useState<DeletionSurvey | null>(null)
  const [working, setWorking] = useState(false)

  // The buttons are hidden without the capability, and every route checks it
  // again server-side. Hiding is for clarity, not for security.
  const mayRequest = caps.includes('tenant.delete')
  const mayApprove = caps.includes('tenant.delete_approve')

  function load() {
    setBusy(true)
    Promise.all([
      getOrganization(orgId),
      subscriptionDetail(orgId).catch(() => null),
    ])
      .then(([o, s]) => { setOrg(o); setSub(s) })
      .catch((e) => setErr(errorText(e, 'Could not load this tenant.')))
      .finally(() => setBusy(false))
  }
  useEffect(load, [orgId])
  useEffect(() => {
    whoami().then((m) => { setCaps(m.capabilities); setMeId(m.user_id) })
      .catch(() => {})
  }, [])
  useEffect(() => { loadDeletion() }, [orgId])

  function loadDeletion() {
    listDeletionRequests()
      .then((all) => setPending(all.find(
        (d) => d.organization_id === orgId
          && (d.status === 'requested' || d.status === 'approved')) ?? null))
      .catch(() => setPending(null))
  }

  async function showWhatWouldGo() {
    setErr('')
    try { setPreview(await deletionSurvey(orgId)) }
    catch (e) { setErr(errorText(e, 'Could not read what this tenant holds.')) }
  }

  async function askToDelete() {
    if (!org) return
    const code = org.code ?? ''
    // The reason first, because it is the part worth thinking about; the code
    // second, as the deliberate act.
    const why = await askText({
      title: `Delete ${org.name}?`,
      body: `This removes everything the tenant holds. It cannot be undone, `
        + `and it needs a second person to approve it.`,
      label: 'Why is this tenant being deleted?',
      minLength: 10,
      hint: 'At least 10 characters — this is the permanent record.',
      confirmText: 'Continue',
    })
    if (!why) return
    const typed = await askText({
      title: `Type ${code} to confirm`,
      body: 'Naming the tenant is what separates this from a misclick.',
      label: 'Tenant code',
      minLength: 1,
      hint: `Type ${code}.`,
      confirmText: 'Request deletion',
    })
    if (!typed) return
    setWorking(true)
    try {
      const r = await requestDeletion(orgId, { reason: why, confirm_code: typed })
      notify(
        `${r.rows_to_remove} rows will go. Somebody else must approve it, and `
        + `not before ${new Date(r.executable_after).toLocaleString()}.`,
        'Deletion requested')
      loadDeletion()
    } catch (e) { setErr(errorText(e, 'The request was refused.')) }
    finally { setWorking(false) }
  }

  async function withdraw() {
    if (!pending) return
    setWorking(true)
    try {
      await withdrawDeletion(pending.id)
      notify(`${org?.name} will not be deleted.`, 'Request withdrawn')
      loadDeletion()
    } catch (e) { setErr(errorText(e, 'The request was not withdrawn.')) }
    finally { setWorking(false) }
  }

  async function carryOut() {
    if (!pending || !org) return
    const typed = await askText({
      title: `Delete ${org.name} permanently?`,
      body: `Requested by ${pending.requested_by_name ?? 'somebody else'}: `
        + `"${pending.reason}". Everything this tenant holds goes now.`,
      label: 'Type the tenant code to confirm',
      minLength: 1,
      hint: `Type ${pending.organization_code}.`,
      confirmText: 'Delete permanently',
    })
    if (!typed) return
    setWorking(true)
    try {
      const r = await approveDeletion(pending.id, typed)
      notify(`${r.organization} and ${r.rows_removed} rows are gone.`,
        'Tenant deleted')
      loadDeletion()
    } catch (e) { setErr(errorText(e, 'The deletion did not go ahead.')) }
    finally { setWorking(false) }
  }

  async function rename() {
    if (!org) return
    const name = await askText({
      title: `Rename ${org.name}`,
      label: 'Organisation name',
      initial: org.name,
      minLength: 2,
      hint: 'At least 2 characters.',
      confirmText: 'Rename',
    })
    if (!name || name.trim() === org.name) return
    try { await renameOrganization(orgId, name.trim()); load() }
    catch (e) { setErr(errorText(e)) }
  }

  async function correctEmail(u: { id: string; display_name: string; email: string | null }) {
    const email = await askText({
      title: `Correct the address for ${u.display_name}`,
      body: 'Their invitation is re-sent to the new address, and every link '
        + 'already issued stops working.',
      label: 'Email address',
      initial: u.email ?? '',
      minLength: 5,
      hint: 'A full email address.',
      confirmText: 'Correct and resend',
    })
    if (!email || email === u.email) return
    const why = await askReason(`Change ${u.display_name}'s address to ${email}?`)
    if (!why) return
    setErr('')
    try {
      const r = await correctInvitedUser(orgId, u.id, { email, reason: why })
      const sent = await resendInvitation(orgId, u.id)
      notify(
        sent.emailed
          ? `Invitation re-sent to ${r.email}. `
            + `${r.links_invalidated} old link(s) invalidated.`
          : `Address corrected, but the invitation could not be sent: `
            + sent.detail,
        sent.emailed ? 'Address corrected' : 'Address corrected — not sent')
      load()
    } catch (e) { setErr(errorText(e, 'The address was not changed.')) }
  }

  async function resend(u: { id: string; display_name: string }) {
    setErr('')
    try {
      const r = await resendInvitation(orgId, u.id)
      notify(
        r.emailed
          ? `A new link was sent to ${r.email}. It lasts ${r.hours} hours, `
            + 'and any earlier link has stopped working.'
          : `Nothing was sent: ${r.detail}`,
        r.emailed ? 'Invitation re-sent' : 'Not sent')
      load()
    } catch (e) { setErr(errorText(e, 'The invitation was not re-sent.')) }
  }

  async function toggle() {
    if (!org) return
    try {
      if (org.status === 'active') {
        const reason = await askReason(`Suspend ${org.name}?`)
        if (!reason) return
        const r = await suspendOrganization(orgId, reason)
        notify(`${r.sessions_revoked} session(s) revoked.`, 'Tenant suspended')
      } else {
        await reactivateOrganization(orgId)
      }
      load()
    } catch (e) { setErr(errorText(e, 'The change was refused.')) }
  }

  if (busy) return <Busy />
  if (!org) {
    return (
      <Page title="Tenant" eyebrow="Tenants"
        crumbs={[{ label: 'Overview', to: '/platform' },
          { label: 'Tenants', to: '/platform/tenants' }]}>
        <ErrorNote>{err}</ErrorNote>
      </Page>
    )
  }

  const s = sub?.subscription
  const activeUsers = org.users.filter(
    (u) => u.membership_status === 'active').length
  const rooms = sub?.usage?.rooms ?? 0
  const over = sub?.over_limit ?? []

  return (
    <Page
      eyebrow="Tenants"
      crumbs={[{ label: 'Overview', to: '/platform' },
        { label: 'Tenants', to: '/platform/tenants' },
        { label: org.name }]}
      title="Tenant overview"
      subtitle={`${org.name}${org.code ? ` · ${org.code}` : ''}`}
      actions={
        <div className="flex items-center gap-2">
          <Button onClick={rename}>
            <span className="flex items-center gap-1.5">
              <Pencil size={13} /> Rename
            </span>
          </Button>
          <Button tone={org.status === 'active' ? 'danger' : 'default'}
            onClick={toggle} className="px-5 py-2.5">
            {org.status === 'active' ? 'Suspend tenant' : 'Reactivate tenant'}
          </Button>
        </div>
      }>
      <ErrorNote>{err}</ErrorNote>

      <Metrics items={[
        { label: 'Plan', value: s?.plan_name || 'No plan',
          tone: s ? 'default' : 'warn',
          caption: s ? `${s.status} · v${s.version_no}` : 'Not subscribed' },
        { label: 'Properties', value: org.properties.length,
          caption: `${rooms} configured room(s)` },
        { label: 'People', value: activeUsers,
          caption: `${org.users.length} account(s) in total` },
        { label: 'Monthly recurring',
          value: s ? money(s.period_total) : money(0),
          caption: over.length
            ? `${over.length} limit exceeded` : 'Within plan limits',
          tone: over.length ? 'warn' : 'default' },
      ]} />

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel
          title="Account"
          rows={[
            { label: 'Tenant code', value: org.code || 'not assigned' },
            { label: 'Company', value: org.name },
            { label: 'Account status', value: <Pill value={org.status} /> },
            { label: 'Organisation id',
              value: <span className="font-mono text-[11px]">{org.id}</span> },
          ]}
        />
        <Panel
          title="Subscription"
          empty="This tenant is not on a plan."
          rows={s ? [
            { label: 'Plan', value: `${s.plan_name} v${s.version_no}` },
            { label: 'Status', value: <Pill value={s.status || 'unknown'} /> },
            { label: 'Billing cycle', value: s.billing_cycle || '—' },
            { label: 'Per period', value: money(s.period_total) },
            { label: 'Trial ends', value: s.trial_ends_on || 'no trial' },
            { label: 'Renews', value: s.current_period_end || '—' },
          ] : []}
        />
      </div>

      <div className="mt-5">
        <DataTable
          title="Properties"
          count={org.properties.length}
          head={['Code', 'Property', 'Status', 'Timezone', 'Currency',
            'Contact', '']}
          footnote={`Showing ${org.properties.length} of ${org.properties.length} records`}
          empty="This tenant has no properties.">
          {org.properties.map((p) => (
            <tr key={p.id} className="hover:bg-pf-bg">
              <Td className="font-mono tracking-widest text-pf-navy">{p.code}</Td>
              <Td>
                <Link to={`/platform/properties/${p.id}`}
                  className="text-pf-navy hover:text-pf-deep hover:underline">
                  {p.name}
                </Link>
              </Td>
              <Td><Pill value={p.status} /></Td>
              <Td className="text-pf-muted">{p.timezone}</Td>
              <Td className="text-pf-muted">{p.currency}</Td>
              <Td className="text-pf-muted">{p.contact_email || '—'}</Td>
              <Td className="text-right">
                <Link to={`/platform/properties/${p.id}`}
                  className="inline-flex rounded p-1 text-pf-placeholder hover:bg-pf-soft hover:text-pf-deep">
                  <ArrowUpRight size={15} />
                </Link>
              </Td>
            </tr>
          ))}
        </DataTable>
      </div>

      <div className="mt-5">
        <DataTable
          title="People"
          count={org.users.length}
          head={['Name', 'Email', 'Account', 'Membership', 'Last sign-in', '']}
          footnote={`Showing ${org.users.length} of ${org.users.length} records`}
          empty="Nobody belongs to this tenant.">
          {org.users.map((u) => (
            <tr key={u.id} className="hover:bg-pf-bg">
              <Td className="text-pf-navy">{u.display_name}</Td>
              <Td className="text-pf-muted">{u.email || '—'}</Td>
              <Td><Pill value={u.user_status} /></Td>
              <Td><Pill value={u.membership_status} /></Td>
              <Td className="text-pf-muted">
                {u.last_login_at
                  ? new Date(u.last_login_at).toLocaleDateString()
                  : 'never'}
              </Td>
              {/* Only for somebody who has not signed in yet. A live account
                  is its owner's to change, and the server refuses anyway. */}
              <Td className="text-right">
                {u.user_status === 'invited' && (
                  <div className="flex justify-end gap-1.5">
                    <Button onClick={() => correctEmail(u)}>Correct email</Button>
                    <Button onClick={() => resend(u)}>Resend invite</Button>
                  </div>
                )}
              </Td>
            </tr>
          ))}
        </DataTable>
      </div>

      {mayRequest && (
        <Card className="mt-5 border-amber-200 p-5">
          <div className="flex items-start gap-3">
            <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-amber-50 text-amber-600">
              <AlertTriangle size={17} />
            </span>
            <div className="min-w-0 flex-1">
              <h2 className="text-pf-card text-pf-navy">Delete this tenant</h2>
              <p className="mt-1 text-pf-help text-pf-muted">
                Permanent, and not the tool for most problems. Suspending is
                reversible, takes effect on the next request everywhere, and
                is undone by one click.
              </p>

              {pending ? (
                <div className="mt-4 rounded-lg border border-pf-border bg-pf-bg p-4">
                  <div className="text-pf-tab text-pf-navy">
                    Deletion requested by {pending.requested_by_name ?? 'somebody'}
                  </div>
                  <p className="mt-1 text-pf-help text-pf-muted">“{pending.reason}”</p>
                  <p className="mt-2 text-pf-help text-pf-muted">
                    Can be carried out after{' '}
                    {new Date(pending.executable_after).toLocaleString()}.
                    {' '}A second person has to approve it.
                  </p>
                  <div className="mt-3 flex flex-wrap items-center gap-2">
                    <Button onClick={withdraw} disabled={working}>
                      Withdraw request
                    </Button>
                    {/* The server refuses a requester approving their own
                        request. Saying so here is better than offering a
                        button that only ever produces a 403. */}
                    {mayApprove && (pending.requested_by === meId ? (
                      <span className="text-pf-help text-pf-muted">
                        Somebody else has to approve this — you asked for it.
                      </span>
                    ) : (
                      <Button tone="danger" onClick={carryOut} disabled={working}
                        className="px-5 py-2.5">
                        Approve and delete
                      </Button>
                    ))}
                  </div>
                </div>
              ) : org.status !== 'suspended' ? (
                <div className="mt-3">
                  <p className="text-pf-help text-pf-muted">
                    Suspend this tenant first. Deletion is only offered for an
                    account that has already been stopped, so it is never the
                    first thing anybody does.
                  </p>
                  {/* The instruction and the means to follow it, in the same
                      place. Saying "suspend first" and leaving the button at
                      the top of the page reads as the option being missing. */}
                  <Button onClick={toggle} className="mt-3">
                    Suspend this tenant
                  </Button>
                </div>
              ) : (
                <div className="mt-3">
                  {preview ? (
                    <div className="rounded-lg border border-pf-border bg-pf-bg p-4">
                      <div className="text-pf-tab text-pf-navy">
                        {preview.rows_total} rows across{' '}
                        {Object.keys(preview.tables).length} tables would be
                        destroyed
                      </div>
                      <div className="mt-2 flex flex-wrap gap-1.5">
                        {Object.entries(preview.tables)
                          .sort((a, b) => b[1] - a[1])
                          .map(([t, n]) => (
                            <span key={t}
                              className="rounded border border-pf-divider bg-white px-1.5 py-0.5 font-mono text-[11px] text-pf-body">
                              {t} {n}
                            </span>
                          ))}
                      </div>
                      <Button tone="danger" onClick={askToDelete}
                        disabled={working} className="mt-3 px-5 py-2.5">
                        Request deletion
                      </Button>
                    </div>
                  ) : (
                    <Button onClick={showWhatWouldGo}>
                      Show what would be destroyed
                    </Button>
                  )}
                </div>
              )}
            </div>
          </div>
        </Card>
      )}

      <Note>
        Opening this tenant's guest or booking records is not possible from
        here. That needs a scoped, time-boxed support session the tenant's own
        owner approves — a path this console does not yet have, deliberately.
      </Note>
    </Page>
  )
}
