import { Fragment, useEffect, useState } from 'react'
import { ChevronDown } from 'lucide-react'
import {
  listDemoRequests, updateDemoRequest, type DemoRequest, type DemoRequests,
} from '../opsApi'
import {
  Busy, Button, DataTable, ErrorNote, Field, FilterBar, Metrics, Note, Page,
  Pill, Select, Td, errorText, inputClass,
} from '../ui'

/** Leads from the "Book a demo" form on the public landing page.
 *
 *  Worked like the support queue: new first, then contacted, then scheduled,
 *  newest first inside each band. A lead goes cold in days, so the one that
 *  arrived this morning outranks one somebody has already called.
 *
 *  A row opens in place to show the full request and to change its status,
 *  owner and notes. There is no separate detail page, because there is
 *  nothing more to show than fits in the row.
 */

const STATUSES: { value: DemoRequest['status']; label: string }[] = [
  { value: 'new', label: 'New' },
  { value: 'contacted', label: 'Contacted' },
  { value: 'scheduled', label: 'Demo scheduled' },
  { value: 'converted', label: 'Became a customer' },
  { value: 'closed', label: 'Closed' },
]

function when(iso: string): string {
  return new Date(iso).toLocaleString('en-IN', {
    day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit',
  })
}

export default function DemoRequestsScreen() {
  const [d, setD] = useState<DemoRequests | null>(null)
  const [status, setStatus] = useState('')
  const [busy, setBusy] = useState(true)
  const [err, setErr] = useState('')
  const [open, setOpen] = useState<string | null>(null)

  function load() {
    setBusy(true)
    listDemoRequests(status || undefined).then(setD)
      .catch((e) => setErr(errorText(e, 'Could not load demo requests.')))
      .finally(() => setBusy(false))
  }
  useEffect(load, [status])

  if (busy && !d) return <Busy />
  const crumbs = [{ label: 'Overview', to: '/platform' }, { label: 'Demo requests' }]
  if (!d) {
    return (
      <Page title="Demo requests" eyebrow="Sales" crumbs={crumbs}>
        <ErrorNote>{err}</ErrorNote>
      </Page>
    )
  }

  const unassignedNew = d.requests.filter((r) => r.status === 'new' && !r.assigned_to)
  const total = Object.values(d.counts).reduce((a, b) => a + b, 0)

  return (
    <Page
      eyebrow="Sales"
      crumbs={crumbs}
      title="Demo requests"
      subtitle="Hotels that asked for a demo from the landing page.">
      <ErrorNote>{err}</ErrorNote>

      <Metrics items={[
        { label: 'New', value: d.counts.new,
          tone: d.counts.new ? 'warn' : 'good',
          caption: d.counts.new ? 'Waiting for a first call' : 'Everyone has been contacted' },
        { label: 'Demo scheduled', value: d.counts.scheduled,
          caption: `${d.counts.contacted} contacted, not yet booked` },
        { label: 'Became customers', value: d.counts.converted,
          caption: total ? `${Math.round((d.counts.converted / total) * 100)}% of ${total} requests` : 'No requests yet' },
        { label: 'Unassigned', value: unassignedNew.length,
          tone: unassignedNew.length ? 'warn' : 'good',
          caption: unassignedNew.length ? 'New, and nobody owns them' : 'Every new lead has an owner' },
      ]} />

      <FilterBar onApply={load} applying={busy}>
        <Select
          id="demo-status" label="Status" value={status} onChange={setStatus}
          options={[{ value: '', label: 'Everything' }, ...STATUSES]}
        />
      </FilterBar>

      <DataTable
        title="Requests"
        count={d.requests.length}
        head={['Received', 'Property', 'Contact', 'Location', 'Rooms', 'Status', 'Owner', '']}
        footnote="New first, then contacted, then scheduled. Newest first inside each."
        empty="No demo requests yet.">
        {d.requests.map((r) => (
          <Fragment key={r.id}>
            <tr className="cursor-pointer hover:bg-pf-bg"
              onClick={() => setOpen(open === r.id ? null : r.id)}>
              <Td className="whitespace-nowrap text-pf-muted">{when(r.created_at)}</Td>
              <Td className="font-medium text-pf-navy">{r.property_name}</Td>
              <Td>
                <div className="text-pf-navy">{r.full_name}</div>
                <div className="text-pf-help text-pf-muted">{r.phone} · {r.email}</div>
              </Td>
              <Td className="text-pf-muted">
                {[r.city, r.state].filter(Boolean).join(', ') || '—'}
              </Td>
              <Td className="text-pf-muted">{r.rooms ?? '—'}</Td>
              <Td><Pill value={STATUSES.find((s) => s.value === r.status)?.label ?? r.status} /></Td>
              <Td className={r.assignee ? 'text-pf-muted' : 'text-pf-warn-text'}>
                {r.assignee || 'unassigned'}
              </Td>
              <Td className="text-right">
                <ChevronDown size={16}
                  className={`inline text-pf-placeholder transition-transform ${open === r.id ? 'rotate-180' : ''}`} />
              </Td>
            </tr>
            {open === r.id && (
              <tr className="bg-pf-thead">
                <td colSpan={8} className="px-5 py-5">
                  <RequestEditor request={r} staff={d.staff}
                    onSaved={() => { setOpen(null); load() }} onError={setErr} />
                </td>
              </tr>
            )}
          </Fragment>
        ))}
      </DataTable>

      <Note>
        Requests come from the public landing page. The same email address
        can only send one request a day, so a visitor who presses the button
        twice shows up here once.
      </Note>
    </Page>
  )
}

function RequestEditor({ request: r, staff, onSaved, onError }: {
  request: DemoRequest
  staff: DemoRequests['staff']
  onSaved: () => void
  onError: (msg: string) => void
}) {
  const [status, setStatus] = useState<string>(r.status)
  const [owner, setOwner] = useState(r.assigned_to ?? '')
  const [notes, setNotes] = useState(r.notes ?? '')
  const [saving, setSaving] = useState(false)

  async function save() {
    setSaving(true)
    try {
      await updateDemoRequest(r.id, {
        status: status !== r.status ? status : undefined,
        assigned_to: owner && owner !== r.assigned_to ? owner : undefined,
        notes: notes !== (r.notes ?? '') ? notes : undefined,
      })
      onSaved()
    } catch (e) {
      onError(errorText(e, 'The request was not updated.'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="grid gap-5 lg:grid-cols-[1fr_1.2fr]">
      <div className="space-y-2 text-sm">
        <p className="text-pf-label text-pf-navy">What they told us</p>
        <p className="whitespace-pre-wrap text-pf-muted">
          {r.message || 'No message.'}
        </p>
        <p className="pt-2 text-pf-help text-pf-muted">
          <a href={`tel:${r.phone.replace(/[^\d+]/g, '')}`}
            className="text-pf-deep hover:underline">Call {r.phone}</a>
          {' · '}
          <a href={`mailto:${r.email}`} className="text-pf-deep hover:underline">
            Email {r.email}
          </a>
        </p>
        <p className="text-pf-help text-pf-muted">Last updated {when(r.updated_at)}</p>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <Select id={`st-${r.id}`} label="Status" value={status} onChange={setStatus}
          options={STATUSES} />
        <Select id={`ow-${r.id}`} label="Owner" value={owner} onChange={setOwner}
          options={[{ value: '', label: r.assigned_to ? 'Keep current owner' : 'Nobody yet' },
            ...staff.map((s) => ({ value: s.id, label: s.display_name }))]} />
        <div className="sm:col-span-2">
          <Field label="Notes">
            <textarea rows={3} className={`${inputClass} resize-y`} value={notes}
              maxLength={2000} placeholder="Calls made, demo date, what they need"
              onChange={(e) => setNotes(e.target.value)} />
          </Field>
        </div>
        <div className="sm:col-span-2">
          <Button tone="primary" onClick={save} disabled={saving} className="px-5 py-2.5">
            {saving ? 'Saving…' : 'Save'}
          </Button>
        </div>
      </div>
    </div>
  )
}
