import { useEffect, useState } from 'react'
import { listOrganizations, type Organization } from '../api'
import {
  platformSettings, putFlag, putSetting, type SettingsPage,
} from '../opsApi'
import {
  Busy, Button, Card, DataTable, ErrorNote, Metrics, Note, Page, Panel, Pill,
  Select, Td, errorText, inputClass,
} from '../ui'

/** Screen 26 — platform defaults and feature flags.
 *
 *  The third panel is the honest one. A settings screen that shows stored
 *  values and nothing else implies the running services read them, and here
 *  they mostly do not yet — mail, encryption keys and the payment provider
 *  still come from each service's environment. Showing the stored default
 *  beside what the runtime actually uses is the difference between a settings
 *  screen and a settings-shaped text box.
 *
 *  Flags default off and can be overridden per tenant. A flag that defaults
 *  on is a release nobody chose.
 */

function show(v: unknown): string {
  if (typeof v === 'string') return v
  if (Array.isArray(v)) return v.join(', ')
  return JSON.stringify(v)
}

export default function PlatformSettings() {
  const [d, setD] = useState<SettingsPage | null>(null)
  const [orgs, setOrgs] = useState<Organization[]>([])
  const [busy, setBusy] = useState(true)
  const [err, setErr] = useState('')
  const [draft, setDraft] = useState<Record<string, string>>({})
  const [overrideFor, setOverrideFor] = useState('')
  const [overrideOrg, setOverrideOrg] = useState('')

  function load() {
    platformSettings().then((x) => {
      setD(x)
      setDraft(Object.fromEntries(x.settings.map((s) => [s.key, show(s.value)])))
    })
      .catch((e) => setErr(errorText(e, 'Could not load settings.')))
      .finally(() => setBusy(false))
  }
  useEffect(load, [])
  useEffect(() => { listOrganizations().then(setOrgs).catch(() => {}) }, [])

  async function save(key: string) {
    const raw = draft[key] ?? ''
    const original = d?.settings.find((s) => s.key === key)?.value
    // Keep the stored type. A locale list is an array and a name is a string,
    // and turning one into the other from a text box is how a reader of this
    // value starts seeing a character at a time.
    const value = Array.isArray(original)
      ? raw.split(',').map((x) => x.trim()).filter(Boolean)
      : raw
    setErr('')
    try { await putSetting(key, value); load() }
    catch (e) { setErr(errorText(e, 'That setting was not saved.')) }
  }

  async function toggle(code: string, enabled: boolean) {
    setErr('')
    try { await putFlag(code, { enabled }); load() }
    catch (e) { setErr(errorText(e)) }
  }

  async function override(code: string, enabled: boolean) {
    if (!overrideOrg) { setErr('Choose a tenant first.'); return }
    setErr('')
    try {
      await putFlag(code, { enabled, organization_id: overrideOrg })
      setOverrideFor(''); setOverrideOrg(''); load()
    } catch (e) { setErr(errorText(e)) }
  }

  if (busy) return <Busy />
  if (!d) {
    return (
      <Page title="Platform settings" eyebrow="Settings"
        crumbs={[{ label: 'Overview', to: '/platform' },
          { label: 'Platform settings' }]}>
        <ErrorNote>{err}</ErrorNote>
      </Page>
    )
  }

  const on = d.flags.filter((f) => f.enabled).length
  const gaps = d.runtime.filter(
    (r) => r.value === 'not configured' || r.value === 'absent').length

  return (
    <Page
      eyebrow="Settings"
      crumbs={[{ label: 'Overview', to: '/platform' },
        { label: 'Platform settings' }]}
      title="Platform settings"
      subtitle="Defaults for a new property, and the flags that gate unfinished work.">
      <ErrorNote>{err}</ErrorNote>

      <Metrics items={[
        { label: 'Defaults', value: d.settings.length,
          caption: 'Applied to new properties' },
        { label: 'Flags on', value: `${on} of ${d.flags.length}`,
          caption: 'Everything defaults off' },
        { label: 'Tenant overrides', value: d.overrides.length,
          caption: d.overrides.length
            ? 'Flags set for one tenant only' : 'No tenant is an exception' },
        { label: 'Runtime gaps', value: gaps,
          tone: gaps ? 'warn' : 'good',
          caption: gaps
            ? 'Something the services need is unset' : 'Everything configured' },
      ]} />

      <DataTable
        title="Defaults"
        count={d.settings.length}
        head={['Setting', 'Value', 'What it does', 'Last changed', '']}
        footnote="Declared in a migration, edited here. A key cannot be created from this screen, so a typo cannot become a setting nothing reads."
        empty="No setting is registered.">
        {d.settings.map((s) => (
          <tr key={s.key} className="hover:bg-pf-bg">
            <Td className="font-mono text-[11px] text-pf-navy">{s.key}</Td>
            <Td>
              <input id={`setting-${s.key}`}
                className={`${inputClass} max-w-[260px] py-1.5`}
                value={draft[s.key] ?? ''}
                onChange={(e) => setDraft({ ...draft, [s.key]: e.target.value })} />
            </Td>
            <Td className="max-w-[280px] text-pf-muted">{s.description}</Td>
            <Td className="text-pf-muted">
              {new Date(s.updated_at).toLocaleDateString()}
              {s.updated_by_name && ` · ${s.updated_by_name}`}
            </Td>
            <Td className="text-right">
              <Button onClick={() => save(s.key)}
                disabled={(draft[s.key] ?? '') === show(s.value)}>
                Save
              </Button>
            </Td>
          </tr>
        ))}
      </DataTable>

      <div className="mt-5">
        <DataTable
          title="Feature flags"
          count={d.flags.length}
          head={['Flag', 'What it gates', 'Platform-wide', 'Overrides', '']}
          footnote="Off by default. A flag that defaults on is a release nobody chose."
          empty="No flag is registered.">
          {d.flags.map((f) => (
            <tr key={f.code} className="hover:bg-pf-bg">
              <Td className="text-pf-navy">{f.name}</Td>
              <Td className="max-w-[320px] text-pf-muted">{f.description}</Td>
              <Td><Pill value={f.enabled ? 'on' : 'off'} /></Td>
              <Td className="text-pf-muted">{f.overrides || '—'}</Td>
              <Td className="text-right">
                <div className="flex justify-end gap-1.5">
                  <Button onClick={() => toggle(f.code, !f.enabled)}>
                    Turn {f.enabled ? 'off' : 'on'}
                  </Button>
                  <Button onClick={() => setOverrideFor(
                    overrideFor === f.code ? '' : f.code)}>
                    One tenant
                  </Button>
                </div>
              </Td>
            </tr>
          ))}
        </DataTable>
      </div>

      {overrideFor && (
        <Card className="mt-5 max-w-2xl p-5">
          <h2 className="text-pf-card text-pf-navy">
            Override <span className="font-mono text-[12px]">{overrideFor}</span>
            {' '}for one tenant
          </h2>
          <div className="mt-4">
            <Select
              id="override-org" label="Tenant" value={overrideOrg}
              onChange={setOverrideOrg}
              options={[{ value: '', label: 'Choose a tenant' },
                ...orgs.map((o) => ({ value: o.id, label: o.name }))]}
            />
          </div>
          <div className="mt-4 flex items-center gap-2">
            <Button tone="primary" onClick={() => override(overrideFor, true)}>
              Turn on for them
            </Button>
            <Button onClick={() => override(overrideFor, false)}>
              Turn off for them
            </Button>
            <Button onClick={() => setOverrideFor('')}>Cancel</Button>
          </div>
        </Card>
      )}

      <div className="mt-5 grid gap-4 lg:grid-cols-2">
        <Panel
          title="What the runtime actually reads"
          rows={d.runtime.map((r) => ({
            label: r.key,
            value: r.value,
            tone: ['not configured', 'absent'].includes(r.value)
              ? ('warn' as const) : ('good' as const),
          }))}
        />
        <Panel
          title="Tenant overrides"
          empty="No tenant has a flag set differently."
          rows={d.overrides.map((o) => ({
            label: `${o.tenant_name} · ${o.flag_code}`,
            value: o.enabled ? 'on' : 'off',
          }))}
        />
      </div>

      <Note>
        The values above are the register. Mail, encryption keys and the
        payment provider are still read from each service's environment at
        start-up, so changing a row here does not change what a running service
        does — the right-hand panel shows which is which rather than letting
        the two be confused.
      </Note>
    </Page>
  )
}
