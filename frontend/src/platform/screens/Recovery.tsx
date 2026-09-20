import { useEffect, useState } from 'react'
import { ShieldAlert } from 'lucide-react'
import {
  decideRestore, recovery, registerSnapshot, requestRestore, verifySnapshot,
  type RecoveryPage,
} from '../opsApi'
import { listOrganizations, type Organization } from '../api'
import {
  Busy, Button, Card, DataTable, ErrorNote, Field, Metrics, Note, Page, Panel,
  Pill, Select, Td, askText, errorText, inputClass,
} from '../ui'

/** Screen 25 — backups, and the queue for restoring from one.
 *
 *  The column that matters is `verified_at`. A backup nobody has restored is
 *  a hope, so this screen leads with how many have actually been proven and
 *  how old the newest proof is, rather than with how many files exist — the
 *  count of files is the number that reassures people right up until the day
 *  it matters.
 *
 *  Restores are approved by somebody other than the person who asked. That is
 *  checked in the route so the refusal reads like a sentence, and again in a
 *  database constraint so it is true regardless of which route is added next.
 */

function size(bytes: number | null): string {
  // Registered backups are gigabytes and a tenant export can be a few
  // kilobytes, and the old version rounded everything below half a megabyte
  // to "0 MB" -- which reads as an empty file rather than a small one.
  if (!bytes) return '—'
  if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(1)} GB`
  if (bytes >= 1e6) return `${(bytes / 1e6).toFixed(1)} MB`
  if (bytes >= 1e3) return `${Math.round(bytes / 1e3)} KB`
  return `${bytes} B`
}

export default function Recovery() {
  const [d, setD] = useState<RecoveryPage | null>(null)
  const [busy, setBusy] = useState(true)
  const [err, setErr] = useState('')
  const [asking, setAsking] = useState(false)
  const [snapshotId, setSnapshotId] = useState('')
  const [scope, setScope] = useState('')
  const [reason, setReason] = useState('')
  const [orgs, setOrgs] = useState<Organization[]>([])
  const [adding, setAdding] = useState(false)
  const [snap, setSnap] = useState({
    label: '', scope: 'platform' as 'platform' | 'tenant',
    organization_id: '', taken_at: '', size_gb: '', location: '',
    retain_until: '',
  })

  function load() {
    recovery().then(setD)
      .catch((e) => setErr(errorText(e, 'Could not load recovery.')))
      .finally(() => setBusy(false))
  }
  useEffect(load, [])
  useEffect(() => { listOrganizations().then(setOrgs).catch(() => {}) }, [])

  async function addSnapshot() {
    if (snap.label.trim().length < 3) {
      setErr('Give the snapshot a label somebody will recognise later.')
      return
    }
    if (snap.scope === 'tenant' && !snap.organization_id) {
      setErr('A tenant snapshot has to name the tenant it covers.')
      return
    }
    setErr('')
    try {
      await registerSnapshot({
        label: snap.label.trim(),
        scope: snap.scope,
        organization_id: snap.scope === 'tenant'
          ? snap.organization_id : null,
        // A date-only input means midnight local; the server rejects a future
        // timestamp, so an empty field sends nothing and it defaults to now.
        taken_at: snap.taken_at
          ? new Date(snap.taken_at).toISOString() : null,
        size_bytes: snap.size_gb
          ? Math.round(Number(snap.size_gb) * 1e9) : null,
        location: snap.location.trim() || null,
        retain_until: snap.retain_until || null,
      })
      setAdding(false)
      setSnap({ label: '', scope: 'platform', organization_id: '',
        taken_at: '', size_gb: '', location: '', retain_until: '' })
      load()
    } catch (e) { setErr(errorText(e, 'The snapshot was not recorded.')) }
  }

  async function markVerified(id: string, label: string) {
    const detail = await askText({
      title: `Verifying "${label}"`,
      body: 'Recorded against this snapshot.',
      label: 'What did you restore, and where?',
      minLength: 10,
      hint: 'Say what was restored and where — at least a short sentence. '
        + 'A tick with no statement behind it is what makes a register '
        + 'worthless.',
      confirmText: 'Mark verified',
    })
    if (detail === null) return
    if (detail.trim().length < 10) {
      setErr('Say what was restored and where — at least a short sentence. '
        + 'A tick with no statement behind it is what makes a register '
        + 'worthless.')
      return
    }
    setErr('')
    try { await verifySnapshot(id, detail.trim()); load() }
    catch (e) { setErr(errorText(e, 'That was not recorded.')) }
  }

  async function ask() {
    if (!snapshotId || scope.trim().length < 3 || reason.trim().length < 10) {
      setErr('Choose a snapshot, say what to restore, and say why.')
      return
    }
    try {
      await requestRestore({
        snapshot_id: snapshotId, scope: scope.trim(), reason: reason.trim() })
      setAsking(false); setScope(''); setReason(''); load()
    } catch (e) { setErr(errorText(e, 'The request was not raised.')) }
  }

  async function decide(id: string, decision: 'approve' | 'refuse') {
    setErr('')
    try { await decideRestore(id, decision); load() }
    catch (e) { setErr(errorText(e)) }
  }

  if (busy) return <Busy />
  if (!d) {
    return (
      <Page title="Backups & recovery" eyebrow="System operations"
        crumbs={[{ label: 'Overview', to: '/platform' },
          { label: 'Backups & recovery' }]}>
        <ErrorNote>{err}</ErrorNote>
      </Page>
    )
  }

  const unverified = d.summary.snapshots - d.summary.verified
  const pending = d.requests.filter((r) => r.status === 'requested')
  const newestDays = d.summary.newest
    ? Math.floor((Date.now() - new Date(d.summary.newest).getTime()) / 86400000)
    : null

  return (
    <Page
      eyebrow="System operations"
      crumbs={[{ label: 'Overview', to: '/platform' },
        { label: 'System operations', to: '/platform/operations' },
        { label: 'Backups & recovery' }]}
      title="Backups &amp; recovery"
      subtitle="What has been captured, what has been proven, and who asked to restore it."
      actions={
        <div className="flex items-center gap-2">
          <Button onClick={() => { setAdding((v) => !v); setAsking(false) }}>
            {adding ? 'Cancel' : 'Register a snapshot'}
          </Button>
          {/* Still conditional, but on something the operator can now fix:
              with a register that can be written to, an empty inventory is a
              missing entry rather than a permanent dead end. */}
          <Button tone="primary" className="px-5 py-2.5"
            disabled={d.snapshots.length === 0}
            onClick={() => { setAsking((v) => !v); setAdding(false) }}>
            {asking ? 'Cancel' : 'Request a restore'}
          </Button>
        </div>
      }>
      <ErrorNote>{err}</ErrorNote>

      <Metrics items={[
        { label: 'Snapshots', value: d.summary.snapshots,
          tone: d.summary.snapshots ? 'default' : 'warn',
          caption: newestDays === null ? 'Nothing recorded'
            : newestDays === 0 ? 'Newest taken today'
              : `Newest ${newestDays} day(s) old` },
        { label: 'Proven by restore', value: d.summary.verified,
          tone: d.summary.verified ? 'good' : 'warn',
          caption: d.summary.last_verified
            ? `Last ${new Date(d.summary.last_verified).toLocaleDateString()}`
            : 'None restored and checked' },
        { label: 'Unproven', value: unverified,
          tone: unverified ? 'warn' : 'good',
          caption: unverified
            ? 'Never restored — a hope, not a backup' : 'All verified' },
        { label: 'Awaiting approval', value: pending.length,
          tone: pending.length ? 'warn' : 'good',
          caption: d.can_approve
            ? 'You can approve others’'
            : 'You do not hold recovery approval' },
      ]} />

      {d.summary.snapshots === 0 && (
        <Card className="mb-5 flex items-start gap-3 border-pf-warn-text/40 p-5">
          <ShieldAlert size={18} className="mt-0.5 shrink-0 text-pf-warn-text" />
          <div>
            <div className="text-pf-desc font-semibold text-pf-navy">
              No backup has been recorded for this deployment
            </div>
            <p className="mt-1 text-pf-help text-pf-muted">
              This screen is the register, not the scheduler. Backups are taken
              by the infrastructure that runs Postgres; nothing here is
              inventing one, and an empty table means the register has not been
              filled in — which is itself worth knowing before somebody needs
              it.
            </p>
            <div className="mt-3">
              <Button onClick={() => setAdding(true)}>
                Register the first one
              </Button>
            </div>
          </div>
        </Card>
      )}

      {adding && (
        <Card className="mb-5 max-w-2xl p-5">
          <h2 className="text-pf-card text-pf-navy">Register a snapshot</h2>
          <p className="mt-1 text-pf-help text-pf-muted">
            Recording a backup that already exists. Nothing here takes one.
          </p>
          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            <Field label="Label">
              <input id="snap-label" className={inputClass} value={snap.label}
                placeholder="nightly full — 15 Sep"
                onChange={(e) => setSnap({ ...snap, label: e.target.value })} />
            </Field>
            <Select
              id="snap-scope" label="Covers" value={snap.scope}
              onChange={(v) => setSnap({
                ...snap, scope: v as 'platform' | 'tenant',
                organization_id: v === 'platform' ? '' : snap.organization_id })}
              options={[
                { value: 'platform', label: 'The whole platform' },
                { value: 'tenant', label: 'One tenant' },
              ]}
            />
            {snap.scope === 'tenant' && (
              <Select
                id="snap-org" label="Tenant" value={snap.organization_id}
                onChange={(v) => setSnap({ ...snap, organization_id: v })}
                options={[{ value: '', label: 'Choose a tenant' },
                  ...orgs.map((o) => ({ value: o.id, label: o.name }))]}
              />
            )}
            <Field label="Taken (blank = now)">
              <input id="snap-taken" type="datetime-local" className={inputClass}
                value={snap.taken_at}
                onChange={(e) => setSnap({ ...snap, taken_at: e.target.value })} />
            </Field>
            <Field label="Size in GB (optional)">
              <input id="snap-size" type="number" min="0" step="0.1"
                className={inputClass} value={snap.size_gb}
                onChange={(e) => setSnap({ ...snap, size_gb: e.target.value })} />
            </Field>
            <Field label="Where it lives (optional)">
              <input id="snap-location" className={inputClass}
                value={snap.location} placeholder="s3://backups/…"
                onChange={(e) => setSnap({ ...snap, location: e.target.value })} />
            </Field>
            <Field label="Keep until (optional)">
              <input id="snap-retain" type="date" className={inputClass}
                value={snap.retain_until}
                onChange={(e) => setSnap({
                  ...snap, retain_until: e.target.value })} />
            </Field>
          </div>
          <div className="mt-4">
            <Button tone="primary" onClick={addSnapshot} className="px-5 py-2.5">
              Record it
            </Button>
          </div>
        </Card>
      )}

      {asking && (
        <Card className="mb-5 max-w-2xl p-5">
          <h2 className="text-pf-card text-pf-navy">Request a restore</h2>
          <div className="mt-4 space-y-3">
            <Select
              id="restore-snapshot" label="Snapshot" value={snapshotId}
              onChange={setSnapshotId}
              options={[{ value: '', label: 'Choose a snapshot' },
                ...d.snapshots.map((s) => ({
                  value: s.id,
                  label: `${s.label} · ${new Date(s.taken_at).toLocaleDateString()}`
                    + (s.verified_at ? ' · verified' : ' · unverified') }))]}
            />
            <Field label="What to restore">
              <input id="restore-scope" className={inputClass} value={scope}
                placeholder="e.g. billing.invoices for TN-002"
                onChange={(e) => setScope(e.target.value)} />
            </Field>
            <Field label="Why">
              <textarea id="restore-reason" rows={3}
                className={`${inputClass} resize-y`} value={reason}
                onChange={(e) => setReason(e.target.value)} />
            </Field>
          </div>
          <p className="mt-3 text-pf-help text-pf-muted">
            Somebody else has to approve this. You cannot approve your own
            request, and the database will not record one that says otherwise.
          </p>
          <div className="mt-4">
            <Button tone="primary" onClick={ask} className="px-5 py-2.5">
              Raise request
            </Button>
          </div>
        </Card>
      )}

      <DataTable
        title="Backup inventory"
        count={d.snapshots.length}
        head={['Label', 'Scope', 'Origin', 'Taken', 'Size', 'Proven',
          'Keep until', '']}
        footnote="Verified means somebody restored it and checked the result. A tenant export is verified on the way out: it is written, read back and compared before the tenant is removed."
        empty="No snapshot has been registered.">
        {d.snapshots.map((s) => (
          <tr key={s.id} className="hover:bg-pf-bg">
            <Td className="text-pf-navy">
              {s.label}
              {/* The reason the tenant went, kept with the only copy of
                  their data. */}
              {s.deletion_reason && (
                <div className="mt-0.5 max-w-[360px] truncate text-pf-help text-pf-muted"
                  title={s.deletion_reason}>
                  “{s.deletion_reason}”
                </div>
              )}
            </Td>
            <Td className="text-pf-muted">
              {s.scope === 'tenant' ? s.tenant_name || 'tenant' : 'platform'}
            </Td>
            <Td>
              {s.deleted_tenant_code ? (
                <div>
                  <Pill value="deleted tenant" />
                  <div className="mt-0.5 text-pf-help text-pf-muted">
                    {s.deleted_tenant_code}
                    {s.deletion_approved_by && ` · ${s.deletion_approved_by}`}
                  </div>
                </div>
              ) : (
                <span className="text-pf-help text-pf-muted">registered</span>
              )}
            </Td>
            <Td className="text-pf-muted">
              {new Date(s.taken_at).toLocaleDateString()}
              {' · '}{s.age_days}d
            </Td>
            <Td className="text-pf-muted">{size(s.size_bytes)}</Td>
            <Td>{s.verified_at
              ? <Pill value="verified" />
              : <span className="text-pf-warn-text">never restored</span>}</Td>
            <Td className="text-pf-muted">{s.retain_until || '—'}</Td>
            <Td className="text-right">
              {d.can_approve && (
                <Button onClick={() => markVerified(s.id, s.label)}>
                  {s.verified_at ? 'Re-verify' : 'Mark verified'}
                </Button>
              )}
            </Td>
          </tr>
        ))}
      </DataTable>

      <div className="mt-5">
        <DataTable
          title="Restore queue"
          count={d.requests.length}
          head={['Raised', 'Scope', 'Snapshot', 'Requested by', 'Approved by',
            'Status', '']}
          footnote="The approver is never the requester — enforced in the route and again in the schema."
          empty="No restore has been requested.">
          {d.requests.map((r) => (
            <tr key={r.id} className="hover:bg-pf-bg">
              <Td className="whitespace-nowrap text-pf-muted">
                {new Date(r.created_at).toLocaleDateString()}
              </Td>
              <Td className="max-w-[240px] truncate text-pf-navy">{r.scope}</Td>
              <Td className="text-pf-muted">{r.snapshot_label}</Td>
              <Td className="text-pf-muted">{r.requested_by_name}</Td>
              <Td className={r.approved_by_name
                ? 'text-pf-muted' : 'text-pf-warn-text'}>
                {r.approved_by_name || 'awaiting a second person'}
              </Td>
              <Td><Pill value={r.status} /></Td>
              <Td className="text-right">
                {r.status === 'requested' && d.can_approve && (
                  <div className="flex justify-end gap-1.5">
                    <Button onClick={() => decide(r.id, 'approve')}>
                      Approve
                    </Button>
                    <Button onClick={() => decide(r.id, 'refuse')}>
                      Refuse
                    </Button>
                  </div>
                )}
              </Td>
            </tr>
          ))}
        </DataTable>
      </div>

      <div className="mt-5">
        <Panel
          title="What this screen does not do"
          rows={[
            { label: 'Take a backup', value: 'Infrastructure, not this app' },
            { label: 'Execute a restore', value: 'By hand, against the record' },
            { label: 'Delete a snapshot', value: 'Not exposed anywhere' },
          ]}
        />
      </div>

      <Note>{d.note}</Note>
    </Page>
  )
}
