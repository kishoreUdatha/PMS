import { useEffect, useState } from 'react'
import { KeyRound, ShieldCheck } from 'lucide-react'
import {
  providers, saveProvider, verifyProvider,
  type ProviderCatalogueEntry, type Providers as P,
} from '../opsApi'
import {
  Busy, Button, Card, DataTable, ErrorNote, Field, Metrics, Note, Page, Panel,
  Pill, Td, errorText, inputClass,
} from '../ui'

/** Screen 16 — the platform's own integration credentials.
 *
 *  Two rules this screen keeps visible rather than merely obeying.
 *
 *  A secret goes in and never comes back. The form's secret field is always
 *  blank, including when editing a connection that has one, because there is
 *  no endpoint that would return it — leaving it blank means "keep what is
 *  stored". The only trace shown is the last four characters of the
 *  *ciphertext*, which distinguishes one row from another after a rotation
 *  and is worthless to anybody who copies it.
 *
 *  The platform's gateway is not a tenant's gateway. The second table exists
 *  because those two are easy to confuse and expensive to confuse: one takes
 *  subscription money from hotels, the other takes room money from their
 *  guests, and they are different merchant accounts.
 */
export default function Providers() {
  const [d, setD] = useState<P | null>(null)
  const [busy, setBusy] = useState(true)
  const [err, setErr] = useState('')
  const [editing, setEditing] = useState<{
    entry: ProviderCatalogueEntry; environment: 'sandbox' | 'production'
  } | null>(null)
  const [label, setLabel] = useState('')
  const [secret, setSecret] = useState('')
  const [cfg, setCfg] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)

  function load() {
    providers().then(setD)
      .catch((e) => setErr(errorText(e, 'Could not load provider setup.')))
      .finally(() => setBusy(false))
  }
  useEffect(load, [])

  function edit(entry: ProviderCatalogueEntry,
    environment: 'sandbox' | 'production') {
    const row = d?.connections.find(
      (c) => c.provider === entry.provider && c.environment === environment)
    setEditing({ entry, environment })
    setLabel(row?.label || `${entry.name} · ${environment}`)
    setCfg((row?.config as Record<string, string>) || {})
    // Always blank. See the note above the component.
    setSecret('')
    setErr('')
  }

  async function save() {
    if (!editing) return
    setSaving(true)
    try {
      await saveProvider({
        provider: editing.entry.provider,
        environment: editing.environment,
        label: label.trim(),
        config: cfg,
        ...(secret.trim() ? { secret: secret.trim() } : {}),
      })
      setEditing(null); setSecret(''); load()
    } catch (e) { setErr(errorText(e, 'The connection was not saved.')) }
    finally { setSaving(false) }
  }

  async function check(id: string) {
    try { await verifyProvider(id); load() }
    catch (e) { setErr(errorText(e)) }
  }

  if (busy) return <Busy />
  if (!d) {
    return (
      <Page title="Provider setup" eyebrow="Integrations"
        crumbs={[{ label: 'Overview', to: '/platform' },
          { label: 'Provider setup' }]}>
        <ErrorNote>{err}</ErrorNote>
      </Page>
    )
  }

  const production = d.connections.filter((c) => c.environment === 'production')
  const sealed = d.connections.filter((c) => c.has_secret).length
  const incomplete = d.connections.filter(
    (c) => c.status === 'unverified').length
  const slots = d.catalogue.length * 2

  return (
    <Page
      eyebrow="Integrations"
      crumbs={[{ label: 'Overview', to: '/platform' },
        { label: 'Integrations', to: '/platform/channels' },
        { label: 'Provider setup' }]}
      title="Provider setup"
      subtitle="Credentials this platform holds for the services it depends on.">
      <ErrorNote>{err}</ErrorNote>

      <Metrics items={[
        { label: 'Connections', value: `${d.connections.length} of ${slots}`,
          caption: `${d.catalogue.length} providers × 2 environments` },
        { label: 'Production', value: production.length,
          tone: production.length ? 'good' : 'warn',
          caption: production.length
            ? 'Configured for live traffic' : 'Nothing set up for live' },
        { label: 'Secrets sealed', value: sealed,
          tone: d.runtime.encryption_configured ? 'good' : 'warn',
          caption: d.runtime.encryption_configured
            ? 'Fernet, at rest' : 'No encryption key — nothing can be stored' },
        { label: 'Incomplete', value: incomplete,
          tone: incomplete ? 'warn' : 'good',
          caption: incomplete
            ? 'Missing a secret or a required field' : 'Every row is complete' },
      ]} />

      <div className="grid gap-4 lg:grid-cols-2">
        {d.catalogue.map((entry) => (
          <Card key={entry.provider} className="p-5">
            <div className="flex items-start justify-between gap-3">
              <div>
                <h2 className="text-pf-card text-pf-navy">{entry.name}</h2>
                <p className="mt-0.5 text-pf-help text-pf-muted">
                  {entry.kind} · secret is the {entry.secret_label.toLowerCase()}
                </p>
              </div>
              <KeyRound size={16} className="mt-1 shrink-0 text-pf-placeholder" />
            </div>
            <div className="mt-4 space-y-2">
              {entry.environments.map((env) => {
                const row = d.connections.find(
                  (c) => c.provider === entry.provider
                    && c.environment === env.environment)
                return (
                  <div key={env.environment}
                    className="flex items-center justify-between gap-3 rounded-md border border-pf-divider px-3 py-2.5">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="text-pf-td font-medium text-pf-navy">
                          {env.environment}
                        </span>
                        {row ? <Pill value={row.status} />
                          : <span className="text-pf-help text-pf-muted">
                            not configured
                          </span>}
                      </div>
                      {row && (
                        <div className="mt-0.5 truncate text-pf-help text-pf-muted">
                          {row.label}
                          {row.secret_hint && ` · …${row.secret_hint}`}
                          {row.rotated_at && ` · rotated ${
                            new Date(row.rotated_at).toLocaleDateString()}`}
                        </div>
                      )}
                    </div>
                    <div className="flex shrink-0 items-center gap-1.5">
                      {row && (
                        <Button onClick={() => check(row.id)}>Check</Button>
                      )}
                      <Button tone={row ? 'default' : 'primary'}
                        onClick={() => edit(entry, env.environment)}>
                        {row ? 'Rotate' : 'Set up'}
                      </Button>
                    </div>
                  </div>
                )
              })}
            </div>
          </Card>
        ))}
      </div>

      {editing && (
        <Card className="mt-5 max-w-2xl p-5">
          <h2 className="text-pf-card text-pf-navy">
            {editing.entry.name} · {editing.environment}
          </h2>
          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            <Field label="Label">
              <input id="provider-label" className={inputClass} value={label}
                onChange={(e) => setLabel(e.target.value)} />
            </Field>
            {editing.entry.fields.map((f) => (
              <Field key={f} label={f.replace(/_/g, ' ')}>
                <input id={`provider-${f}`} className={inputClass}
                  value={cfg[f] || ''}
                  onChange={(e) => setCfg({ ...cfg, [f]: e.target.value })} />
              </Field>
            ))}
            <Field label={editing.entry.secret_label}>
              <input id="provider-secret" type="password" className={inputClass}
                value={secret} autoComplete="new-password"
                placeholder="leave blank to keep the stored secret"
                onChange={(e) => setSecret(e.target.value)} />
            </Field>
          </div>
          <p className="mt-3 flex items-start gap-2 text-pf-help text-pf-muted">
            <ShieldCheck size={14} className="mt-0.5 shrink-0" />
            Sealed with Fernet before it reaches the database, and never
            returned by any endpoint. Nobody can read it back from here —
            including you, after you save.
          </p>
          <div className="mt-4 flex items-center gap-2">
            <Button tone="primary" onClick={save} disabled={saving}
              className="px-5 py-2.5">
              {saving ? 'Saving…' : 'Save connection'}
            </Button>
            <Button onClick={() => setEditing(null)}>Cancel</Button>
          </div>
        </Card>
      )}

      <div className="mt-5">
        <DataTable
          title="Tenant payment gateways"
          count={d.tenant_gateways.length}
          head={['Tenant', 'Provider', 'Key', 'Webhook secret', 'State',
            'Updated']}
          footnote="Each tenant's own merchant account, used to charge their guests. Separate from the platform's gateway above, which charges tenants."
          empty="No tenant has connected a payment gateway.">
          {d.tenant_gateways.map((g) => (
            <tr key={`${g.tenant_code}-${g.provider}`} className="hover:bg-pf-bg">
              <Td className="text-pf-navy">{g.tenant_name}</Td>
              <Td className="text-pf-muted">{g.provider}</Td>
              <Td>{g.has_key
                ? <Pill value="sealed" />
                : <span className="text-pf-warn-text">missing</span>}</Td>
              <Td>{g.has_webhook_secret
                ? <Pill value="sealed" />
                : <span className="text-pf-warn-text">missing</span>}</Td>
              <Td><Pill value={g.enabled ? 'enabled' : 'disabled'} /></Td>
              <Td className="text-pf-muted">
                {g.updated_at
                  ? new Date(g.updated_at).toLocaleDateString() : '—'}
              </Td>
            </tr>
          ))}
        </DataTable>
      </div>

      <div className="mt-5">
        <Panel
          title="What the runtime reads today"
          rows={[
            { label: 'Mail', value: d.runtime.mail_configured
              ? 'configured' : 'not configured',
              tone: d.runtime.mail_configured ? 'good' : 'warn' },
            { label: 'Credential encryption',
              value: d.runtime.encryption_configured ? 'present' : 'absent',
              tone: d.runtime.encryption_configured ? 'good' : 'warn' },
          ]}
        />
      </div>

      <Note>
        {d.runtime.note} The “Check” button reads the stored row only — it
        does not call the provider, because an admin console that can reach a
        payment gateway on demand is a console that can be used to probe one.
      </Note>
    </Page>
  )
}
