import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowUpRight } from 'lucide-react'
import { listOrganizations, type Organization } from '../api'
import { openTicket, supportInbox, type SupportInbox } from '../opsApi'
import {
  Busy, Button, Card, DataTable, ErrorNote, Field, FilterBar, Metrics, Note,
  Page, Panel, Pill, Select, Td, errorText, inputClass,
} from '../ui'

/** Screen 20 — the support queue.
 *
 *  Sorted the way it should be worked, not by when things arrived: open
 *  before waiting, urgent before low, oldest first inside each band. A queue
 *  sorted by date puts a week-old "how do I change my logo" above an urgent
 *  ticket raised this morning, and whoever works from the top does the wrong
 *  thing all day.
 *
 *  Breach is computed on the server so this screen, an export and any future
 *  report cannot disagree about whether a target was missed.
 */

function due(iso: string | null): { text: string; late: boolean } {
  if (!iso) return { text: '—', late: false }
  const ms = new Date(iso).getTime() - Date.now()
  const late = ms < 0
  const hours = Math.round(Math.abs(ms) / 3600000)
  const text = hours >= 24
    ? `${Math.round(hours / 24)} day${hours >= 48 ? 's' : ''}`
    : `${hours} hour${hours === 1 ? '' : 's'}`
  return { text: late ? `${text} over` : `in ${text}`, late }
}

export default function Support() {
  const [d, setD] = useState<SupportInbox | null>(null)
  const [orgs, setOrgs] = useState<Organization[]>([])
  const [status, setStatus] = useState('')
  const [busy, setBusy] = useState(true)
  const [err, setErr] = useState('')
  const [adding, setAdding] = useState(false)
  const [form, setForm] = useState({
    organization_id: '', subject: '', priority: 'normal',
    opened_by: '', body: '',
  })

  function load() {
    setBusy(true)
    supportInbox(status || undefined).then(setD)
      .catch((e) => setErr(errorText(e, 'Could not load the support queue.')))
      .finally(() => setBusy(false))
  }
  useEffect(load, [status])
  useEffect(() => { listOrganizations().then(setOrgs).catch(() => {}) }, [])

  async function create() {
    if (!form.organization_id || !form.subject.trim() || !form.body.trim()) {
      setErr('A ticket needs a tenant, a subject and a description.')
      return
    }
    try {
      await openTicket({
        organization_id: form.organization_id,
        subject: form.subject.trim(), priority: form.priority,
        opened_by: form.opened_by.trim() || undefined,
        body: form.body.trim(),
      })
      setAdding(false)
      setForm({ organization_id: '', subject: '', priority: 'normal',
        opened_by: '', body: '' })
      load()
    } catch (e) { setErr(errorText(e, 'The ticket was not opened.')) }
  }

  if (busy && !d) return <Busy />
  if (!d) {
    return (
      <Page title="Support" eyebrow="Support"
        crumbs={[{ label: 'Overview', to: '/platform' },
          { label: 'Support' }]}>
        <ErrorNote>{err}</ErrorNote>
      </Page>
    )
  }

  const open = d.tickets.filter((t) => t.status === 'open')
  const breached = d.tickets.filter((t) => t.breached)
  const unassigned = d.tickets.filter(
    (t) => !t.assignee && ['open', 'waiting'].includes(t.status))
  const urgent = d.tickets.filter(
    (t) => t.priority === 'urgent' && ['open', 'waiting'].includes(t.status))

  return (
    <Page
      eyebrow="Support"
      crumbs={[{ label: 'Overview', to: '/platform' },
        { label: 'Support inbox' }]}
      title="Support inbox"
      subtitle="Tenant tickets, response targets and who is carrying what."
      actions={
        <Button tone="primary" className="px-5 py-2.5"
          onClick={() => setAdding((v) => !v)}>
          {adding ? 'Cancel' : 'Log a ticket'}
        </Button>
      }>
      <ErrorNote>{err}</ErrorNote>

      <Metrics items={[
        { label: 'Open', value: open.length,
          caption: `${d.tickets.length} in this view` },
        { label: 'Urgent', value: urgent.length,
          tone: urgent.length ? 'warn' : 'good',
          caption: urgent.length
            ? 'Target: reply within the hour' : 'Nothing urgent' },
        { label: 'Past target', value: breached.length,
          tone: breached.length ? 'warn' : 'good',
          caption: breached.length
            ? 'Resolution target already missed' : 'Every target still met' },
        { label: 'Unassigned', value: unassigned.length,
          tone: unassigned.length ? 'warn' : 'good',
          caption: unassigned.length
            ? 'Nobody has picked these up' : 'Everything has an owner' },
      ]} />

      {adding && (
        <Card className="mb-5 max-w-3xl p-5">
          <h2 className="text-pf-card text-pf-navy">Log a ticket</h2>
          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            <Select
              id="ticket-org" label="Tenant" value={form.organization_id}
              onChange={(v) => setForm({ ...form, organization_id: v })}
              options={[{ value: '', label: 'Choose a tenant' },
                ...orgs.map((o) => ({ value: o.id, label: o.name }))]}
            />
            <Select
              id="ticket-priority" label="Priority" value={form.priority}
              onChange={(v) => setForm({ ...form, priority: v })}
              options={['urgent', 'high', 'normal', 'low'].map((p) => ({
                value: p,
                label: `${p} · reply in ${d.sla[p]?.first_response_hours}h, `
                  + `resolve in ${d.sla[p]?.resolution_hours}h` }))}
            />
            <Field label="Subject">
              <input id="ticket-subject" className={inputClass}
                value={form.subject}
                onChange={(e) => setForm({ ...form, subject: e.target.value })} />
            </Field>
            <Field label="Raised by (optional)">
              <input id="ticket-by" className={inputClass}
                value={form.opened_by} placeholder="who reported it"
                onChange={(e) => setForm({ ...form, opened_by: e.target.value })} />
            </Field>
          </div>
          <div className="mt-3">
            <Field label="What happened">
              <textarea id="ticket-body" rows={4}
                className={`${inputClass} resize-y`} value={form.body}
                onChange={(e) => setForm({ ...form, body: e.target.value })} />
            </Field>
          </div>
          <div className="mt-4">
            <Button tone="primary" onClick={create} className="px-5 py-2.5">
              Open ticket
            </Button>
          </div>
        </Card>
      )}

      <FilterBar onApply={load} applying={busy}>
        <Select
          id="support-status" label="Status" value={status} onChange={setStatus}
          options={[
            { value: '', label: 'Everything' },
            { value: 'open', label: 'Open' },
            { value: 'waiting', label: 'Waiting on the tenant' },
            { value: 'resolved', label: 'Resolved' },
            { value: 'closed', label: 'Closed' },
          ]}
        />
      </FilterBar>

      <DataTable
        title="Queue"
        count={d.tickets.length}
        head={['Reference', 'Subject', 'Tenant', 'Priority', 'Status',
          'Assignee', 'Resolve by', '']}
        footnote="Ordered the way it should be worked: open first, then by priority, oldest first."
        empty="Nothing in this queue.">
        {d.tickets.map((t) => {
          const dueAt = due(t.resolution_due)
          return (
            <tr key={t.id} className="hover:bg-pf-bg">
              <Td className="font-mono text-[12px] text-pf-navy">
                {t.reference}
              </Td>
              <Td>
                <Link to={`/platform/support/${t.id}`}
                  className="text-pf-navy hover:text-pf-deep hover:underline">
                  {t.subject}
                </Link>
              </Td>
              <Td className="text-pf-muted">{t.tenant_name}</Td>
              <Td><Pill value={t.priority} /></Td>
              <Td><Pill value={t.status} /></Td>
              <Td className={t.assignee ? 'text-pf-muted' : 'text-pf-warn-text'}>
                {t.assignee || 'unassigned'}
              </Td>
              <Td className={t.breached ? 'text-pf-warn-text' : 'text-pf-muted'}>
                {['resolved', 'closed'].includes(t.status) ? '—' : dueAt.text}
              </Td>
              <Td className="text-right">
                <Link to={`/platform/support/${t.id}`}
                  className="inline-flex rounded p-1 text-pf-placeholder hover:bg-pf-soft hover:text-pf-deep">
                  <ArrowUpRight size={15} />
                </Link>
              </Td>
            </tr>
          )
        })}
      </DataTable>

      <div className="mt-5 grid gap-4 lg:grid-cols-2">
        <Panel
          title="Who is carrying what"
          empty="No active platform staff."
          rows={d.agents.map((a) => ({
            label: a.display_name,
            value: `${a.open_tickets} open`,
            tone: a.open_tickets > 5 ? ('warn' as const) : undefined,
          }))}
        />
        <Panel
          title="Response targets"
          rows={Object.entries(d.sla).map(([k, v]) => ({
            label: k,
            value: `reply ${v.first_response_hours}h · `
              + `resolve ${v.resolution_hours}h`,
          }))}
        />
      </div>

      <Note>
        First response and resolution are tracked apart because they fail
        apart: a customer whose urgent ticket was acknowledged in ten minutes
        and fixed in two days had a very different week from one who heard
        nothing for two days.
      </Note>
    </Page>
  )
}
