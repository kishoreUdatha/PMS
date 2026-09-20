import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { Lock } from 'lucide-react'
import {
  replyToTicket, requestAccess, revokeAccess, ticketDetail, updateTicket,
  type TicketDetail,
} from '../opsApi'
import {
  Busy, Button, Card, DataTable, ErrorNote, Field, Metrics, Note, Page, Panel,
  Pill, Select, SplitLayout, Td, errorText, inputClass,
} from '../ui'

/** Screen 21 — one ticket, its conversation, and access requests against it.
 *
 *  The access panel is the part worth reading carefully. It creates a
 *  *request*: who asked, for which read-only scopes, for how many minutes, and
 *  why. Approval is the tenant owner's, recorded against their user, and the
 *  database refuses a row where the approver is the requester.
 *
 *  What it does not do is open anything. No code path in this system reads an
 *  approved grant and widens somebody's visibility, and the screen says so
 *  where an operator will read it rather than leaving them to assume a button
 *  exists somewhere. Building the record before the door is deliberate: the
 *  hard part of consented access is the consent, and a mechanism shipped
 *  first would be a mechanism used before the consent worked.
 */
export default function SupportTicket() {
  const { ticketId = '' } = useParams()
  const [d, setD] = useState<TicketDetail | null>(null)
  const [busy, setBusy] = useState(true)
  const [err, setErr] = useState('')
  const [reply, setReply] = useState('')
  const [asking, setAsking] = useState(false)
  const [reason, setReason] = useState('')
  const [minutes, setMinutes] = useState('15')
  const [scopes, setScopes] = useState<string[]>([])

  function load() {
    ticketDetail(ticketId).then(setD)
      .catch((e) => setErr(errorText(e, 'Could not load this ticket.')))
      .finally(() => setBusy(false))
  }
  useEffect(load, [ticketId])

  async function send() {
    if (!reply.trim()) return
    try { await replyToTicket(ticketId, reply.trim()); setReply(''); load() }
    catch (e) { setErr(errorText(e, 'The reply was not posted.')) }
  }

  async function set(body: { status?: string; priority?: string }) {
    setErr('')
    try { await updateTicket(ticketId, body); load() }
    catch (e) { setErr(errorText(e)) }
  }

  async function ask() {
    if (!d) return
    if (scopes.length === 0) { setErr('Choose at least one scope.'); return }
    if (reason.trim().length < 10) {
      setErr('Say why, in a sentence the tenant will read.')
      return
    }
    try {
      await requestAccess(ticketId, {
        organization_id: String(d.ticket.organization_id),
        property_id: (d.ticket.property_id as string) || null,
        reason: reason.trim(), scope: scopes, minutes: Number(minutes),
      })
      setAsking(false); setReason(''); setScopes([]); load()
    } catch (e) { setErr(errorText(e, 'The request was not raised.')) }
  }

  async function withdraw(id: string) {
    try { await revokeAccess(id); load() }
    catch (e) { setErr(errorText(e)) }
  }

  if (busy) return <Busy />
  if (!d) {
    return (
      <Page title="Ticket" eyebrow="Support"
        crumbs={[{ label: 'Overview', to: '/platform' },
          { label: 'Support', to: '/platform/support' }]}>
        <ErrorNote>{err}</ErrorNote>
      </Page>
    )
  }

  const t = d.ticket
  const replies = d.messages.filter((m) => m.from_side === 'platform').length
  const valid = d.grants.filter((g) => g.currently_valid).length

  return (
    <Page
      eyebrow="Support"
      crumbs={[{ label: 'Overview', to: '/platform' },
        { label: 'Support', to: '/platform/support' },
        { label: t.reference }]}
      title={t.subject}
      subtitle={`${t.reference} · ${t.tenant_name}${
        t.property_name ? ` · ${t.property_name}` : ''}`}
      actions={
        <div className="flex items-center gap-2">
          {t.status !== 'resolved' && t.status !== 'closed' && (
            <Button onClick={() => set({ status: 'resolved' })}>
              Mark resolved
            </Button>
          )}
          {t.status === 'resolved' && (
            <>
              <Button onClick={() => set({ status: 'open' })}>Reopen</Button>
              <Button tone="primary" className="px-5 py-2.5"
                onClick={() => set({ status: 'closed' })}>Close</Button>
            </>
          )}
        </div>
      }>
      <ErrorNote>{err}</ErrorNote>

      <Metrics items={[
        { label: 'Status', value: t.status,
          tone: t.status === 'open' ? 'warn' : 'default',
          caption: `Raised ${new Date(t.created_at).toLocaleDateString()}` },
        { label: 'Priority', value: t.priority,
          tone: ['urgent', 'high'].includes(t.priority) ? 'warn' : 'default',
          caption: t.assignee ? `Owned by ${t.assignee}` : 'Unassigned' },
        { label: 'Replies', value: replies,
          caption: t.first_response_at
            ? `First ${new Date(t.first_response_at).toLocaleDateString()}`
            : 'No reply from us yet',
          tone: replies ? 'default' : 'warn' },
        { label: 'Access grants', value: d.grants.length,
          tone: valid ? 'warn' : 'default',
          caption: valid ? `${valid} approved and unexpired` : 'None active' },
      ]} />

      <SplitLayout
        main={
          <>
            <Card className="p-5">
              <h2 className="text-pf-card text-pf-navy">Conversation</h2>
              <div className="mt-4 space-y-3">
                {d.messages.map((m) => (
                  <div key={m.id}
                    className={`rounded-md border px-4 py-3 ${
                      m.from_side === 'platform'
                        ? 'border-pf-teal/40 bg-pf-soft'
                        : 'border-pf-divider bg-white'}`}>
                    <div className="flex items-baseline justify-between gap-3">
                      <span className="text-pf-td font-medium text-pf-navy">
                        {m.author}
                      </span>
                      <span className="text-pf-help text-pf-muted">
                        {m.from_side === 'platform' ? 'Platform' : 'Tenant'}
                        {' · '}
                        {new Date(m.created_at).toLocaleString('en-IN',
                          { day: '2-digit', month: 'short', hour: '2-digit',
                            minute: '2-digit' })}
                      </span>
                    </div>
                    <p className="mt-1.5 whitespace-pre-wrap text-pf-td text-pf-body">
                      {m.body}
                    </p>
                  </div>
                ))}
              </div>
              {t.status !== 'closed' && (
                <div className="mt-4 border-t border-pf-divider pt-4">
                  <Field label="Reply">
                    <textarea id="ticket-reply" rows={3}
                      className={`${inputClass} resize-y`} value={reply}
                      onChange={(e) => setReply(e.target.value)} />
                  </Field>
                  <div className="mt-3">
                    <Button tone="primary" onClick={send}
                      disabled={!reply.trim()} className="px-5 py-2.5">
                      Send reply
                    </Button>
                  </div>
                </div>
              )}
            </Card>

            <div className="mt-5">
              <DataTable
                title="Access requests"
                count={d.grants.length}
                head={['Asked', 'Scopes', 'For', 'Requested by', 'Approved by',
                  'Status', '']}
                footnote="A record of who asked for what, and who agreed. Approval does not open anything."
                empty="No access has been requested for this ticket.">
                {d.grants.map((g) => (
                  <tr key={g.id} className="hover:bg-pf-bg">
                    <Td className="whitespace-nowrap text-pf-muted">
                      {new Date(g.created_at).toLocaleDateString()}
                    </Td>
                    <Td className="max-w-[220px] text-pf-muted">
                      {g.scope.join(', ')}
                    </Td>
                    <Td className="text-pf-muted">{g.minutes} min</Td>
                    <Td className="text-pf-navy">{g.requested_by_name}</Td>
                    <Td className={g.approved_by_name
                      ? 'text-pf-navy' : 'text-pf-muted'}>
                      {g.approved_by_name || 'awaiting the tenant'}
                    </Td>
                    <Td><Pill value={g.status} /></Td>
                    <Td className="text-right">
                      {['requested', 'approved'].includes(g.status) && (
                        <Button onClick={() => withdraw(g.id)}>Withdraw</Button>
                      )}
                    </Td>
                  </tr>
                ))}
              </DataTable>
            </div>
          </>
        }
        side={
          <>
            <Panel
              title="Ticket"
              rows={[
                { label: 'Reference', value: t.reference },
                { label: 'Tenant', value: t.tenant_name },
                { label: 'Property', value: t.property_name || '—' },
                { label: 'Raised by', value: t.opened_by || '—' },
                { label: 'Assignee', value: t.assignee || 'unassigned',
                  tone: t.assignee ? undefined : ('warn' as const) },
                { label: 'Resolve by',
                  value: t.resolution_due
                    ? new Date(t.resolution_due).toLocaleString('en-IN',
                      { day: '2-digit', month: 'short', hour: '2-digit',
                        minute: '2-digit' })
                    : '—',
                  tone: t.breached ? ('warn' as const) : undefined },
              ]}
            />

            <Card className="p-5">
              <div className="flex items-center gap-2">
                <Lock size={15} className="text-pf-deep" />
                <h2 className="text-pf-card text-pf-navy">Tenant data access</h2>
              </div>
              <p className="mt-2 text-pf-help text-pf-muted">
                Support staff cannot see this tenant's reservations, folios or
                guests. Asking records a request the tenant's own owner has to
                approve.
              </p>
              {!asking ? (
                <div className="mt-4">
                  <Button onClick={() => setAsking(true)}>
                    Request scoped access
                  </Button>
                </div>
              ) : (
                <div className="mt-4 space-y-3">
                  <div>
                    <div className="mb-1.5 text-pf-label text-pf-muted">
                      Scopes — every one is read-only
                    </div>
                    <div className="space-y-1.5">
                      {d.scopes.map((s) => (
                        <label key={s}
                          className="flex items-center gap-2 text-pf-td text-pf-body">
                          <input type="checkbox" id={`scope-${s}`}
                            checked={scopes.includes(s)}
                            onChange={(e) => setScopes(e.target.checked
                              ? [...scopes, s]
                              : scopes.filter((x) => x !== s))} />
                          <span className="font-mono text-[11px]">{s}</span>
                        </label>
                      ))}
                    </div>
                  </div>
                  <Select
                    id="access-minutes" label="Time box" value={minutes}
                    onChange={setMinutes}
                    options={[
                      { value: '15', label: '15 minutes' },
                      { value: '30', label: '30 minutes' },
                      { value: '60', label: '1 hour' },
                      { value: '240', label: '4 hours' },
                    ]}
                  />
                  <Field label="Why — the tenant reads this">
                    <textarea id="access-reason" rows={3}
                      className={`${inputClass} resize-y`} value={reason}
                      onChange={(e) => setReason(e.target.value)} />
                  </Field>
                  <div className="flex items-center gap-2">
                    <Button tone="primary" onClick={ask}>Send request</Button>
                    <Button onClick={() => setAsking(false)}>Cancel</Button>
                  </div>
                </div>
              )}
            </Card>
          </>
        }
      />

      <Note>
        An approved request is a record, not a session. Nothing in this system
        reads a grant and opens a tenant's data — the mechanism is deliberately
        not built, because consented access needs designing with the customer
        rather than switching on from an admin console. Until it exists, a
        question that genuinely needs their data is answered by somebody inside
        that tenant.
      </Note>
    </Page>
  )
}
