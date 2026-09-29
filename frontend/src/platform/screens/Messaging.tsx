import { useEffect, useState } from 'react'
import {
  messaging, updateTemplate, type MessagingPage, type MessageTemplate,
} from '../opsApi'
import {
  Busy, Button, Card, DataTable, ErrorNote, Field, Metrics, Note, Page, Pill,
  Td, errorText, inputClass,
} from '../ui'

/** Screen 19 — what this platform says in a customer's name, and to whom.
 *
 *  Two halves that answer different questions. The templates answer "what do
 *  we send"; the delivery log answers "did they get it". Until now neither had
 *  an answer outside the source tree — the four messages were Python
 *  functions, and a send that failed was a boolean on a response nobody kept.
 *
 *  The variables a template may use are fixed by the code that renders it, so
 *  they are shown and not editable. A screen that let somebody paste
 *  {{refund_amount}} into the welcome mail would produce an email with a
 *  literal {{refund_amount}} in it and no error anywhere.
 */
export default function Messaging() {
  const [d, setD] = useState<MessagingPage | null>(null)
  const [busy, setBusy] = useState(true)
  const [err, setErr] = useState('')
  const [editing, setEditing] = useState<MessageTemplate | null>(null)
  const [name, setName] = useState('')
  const [subject, setSubject] = useState('')
  const [providerId, setProviderId] = useState('')
  const [language, setLanguage] = useState('en')
  const [saving, setSaving] = useState(false)

  function load() {
    messaging().then(setD)
      .catch((e) => setErr(errorText(e, 'Could not load messaging.')))
      .finally(() => setBusy(false))
  }
  useEffect(load, [])

  function edit(t: MessageTemplate) {
    setEditing(t); setName(t.name); setSubject(t.subject || '')
    setProviderId(t.provider_template_id || ''); setLanguage(t.language || 'en')
    setErr('')
  }

  const isPhone = (t: MessageTemplate | null) =>
    !!t && (t.channel === 'sms' || t.channel === 'whatsapp')

  async function save(status?: 'draft' | 'published') {
    if (!editing) return
    setSaving(true)
    try {
      await updateTemplate(editing.id, isPhone(editing)
        ? { name: name.trim(), provider_template_id: providerId.trim(),
            language: language.trim() || 'en', status }
        : { name: name.trim(), subject: subject.trim(), status })
      setEditing(null); load()
    } catch (e) { setErr(errorText(e, 'The template was not saved.')) }
    finally { setSaving(false) }
  }

  if (busy) return <Busy />
  if (!d) {
    return (
      <Page title="Messaging" eyebrow="Messaging"
        crumbs={[{ label: 'Overview', to: '/platform' },
          { label: 'Messaging' }]}>
        <ErrorNote>{err}</ErrorNote>
      </Page>
    )
  }

  const failed = d.totals.failed || 0
  const total = d.totals.total || 0
  const rate = total ? Math.round((100 * (d.totals.sent || 0)) / total) : null

  return (
    <Page
      eyebrow="Messaging"
      crumbs={[{ label: 'Overview', to: '/platform' },
        { label: 'Messaging' }]}
      title="Messaging"
      subtitle="Platform templates, and every message sent with them.">
      <ErrorNote>{err}</ErrorNote>

      <div className="mb-4 flex flex-wrap gap-2 text-pf-help">
        {([
          ['Email', d.mail_configured],
          ['SMS (MSG91)', d.sms_configured],
          ['WhatsApp (MSG91)', d.whatsapp_configured],
        ] as const).map(([label, ok]) => (
          <span key={label}
            className={`rounded-full px-3 py-1 ${ok ? 'bg-pf-ok-bg text-pf-ok-text' : 'bg-pf-bg text-pf-muted'}`}>
            {label}: {ok ? 'configured' : 'not configured'}
          </span>
        ))}
        {d.messaging_test_mode && (
          <span className="rounded-full bg-pf-warn-bg px-3 py-1 text-pf-warn-text">
            Test mode: SMS and WhatsApp are logged, not sent
          </span>
        )}
      </div>

      <Metrics items={[
        { label: 'Templates', value: d.templates.length,
          caption: 'An edit makes a new version' },
        { label: `Sent · ${d.window_days} days`, value: total,
          caption: d.totals.last_sent
            ? `last ${new Date(d.totals.last_sent).toLocaleString('en-IN',
              { day: '2-digit', month: 'short', hour: '2-digit',
                minute: '2-digit' })}`
            : 'Nothing sent in this window' },
        { label: 'Accepted', value: rate === null ? '—' : `${rate}%`,
          tone: rate !== null && rate < 95 ? 'warn' : 'good',
          caption: 'Relay took it, not inboxed' },
        { label: 'Failed', value: failed,
          tone: failed ? 'warn' : 'good',
          caption: d.mail_configured
            ? 'Refused or unreachable' : 'No mail server is configured' },
      ]} />

      <DataTable
        title="Templates"
        count={d.templates.length}
        head={['Code', 'Channel', 'Name', 'Subject / provider id', 'Variables', 'Status', 'Sent', '']}
        footnote={`Sent counts cover the last ${d.window_days} days.`}
        empty="No template is registered.">
        {d.templates.map((t) => (
          <tr key={t.id} className="hover:bg-pf-bg">
            <Td className="font-mono text-[12px] text-pf-navy">
              {t.code} <span className="text-pf-muted">v{t.version}</span>
            </Td>
            <Td className="text-pf-muted">
              {t.channel === 'sms' ? 'SMS' : t.channel === 'whatsapp' ? 'WhatsApp' : 'Email'}
            </Td>
            <Td className="text-pf-navy">{t.name}</Td>
            <Td className="max-w-[320px] truncate text-pf-muted">
              {isPhone(t)
                ? (t.provider_template_id
                  ? <span className="font-mono text-[12px]">{t.provider_template_id}</span>
                  : <span className="text-pf-warn-text">not registered yet</span>)
                : (t.subject || '—')}
            </Td>
            <Td className="text-pf-muted">{t.variables.length}</Td>
            <Td><Pill value={t.status} /></Td>
            <Td className="text-pf-muted">{t.sent}</Td>
            <Td className="text-right">
              <Button onClick={() => edit(t)}>Edit</Button>
            </Td>
          </tr>
        ))}
      </DataTable>

      {editing && (
        <Card className="mt-5 max-w-3xl p-5">
          <h2 className="text-pf-card text-pf-navy">
            {editing.code} · {editing.channel} · version {editing.version}
          </h2>
          <div className="mt-4 grid gap-3">
            <Field label="Name">
              <input id="tpl-name" className={inputClass} value={name}
                onChange={(e) => setName(e.target.value)} />
            </Field>
            {isPhone(editing) ? (
              <>
                <Field label="Wording to register">
                  <textarea readOnly rows={4} value={editing.body_text}
                    className={`${inputClass} resize-none bg-pf-bg font-mono text-[12px]`} />
                </Field>
                <p className="-mt-1 text-pf-help text-pf-muted">
                  {editing.channel === 'sms'
                    ? 'Register this wording as a DLT template, create an MSG91 flow for it with variables named as below, and paste the flow id here.'
                    : 'Submit this wording to Meta as a WhatsApp template through MSG91, with the variables below as {{1}}, {{2}} and so on, in that order. Paste the approved template name here.'}
                </p>
                <div className="grid gap-3 sm:grid-cols-[1fr_140px]">
                  <Field label={editing.channel === 'sms' ? 'MSG91 flow id' : 'Approved template name'}>
                    <input id="tpl-provider" className={inputClass} value={providerId}
                      onChange={(e) => setProviderId(e.target.value)} />
                  </Field>
                  <Field label="Language">
                    <input id="tpl-lang" className={inputClass} value={language}
                      onChange={(e) => setLanguage(e.target.value)} placeholder="en" />
                  </Field>
                </div>
              </>
            ) : (
              <Field label="Subject line">
                <input id="tpl-subject" className={inputClass} value={subject}
                  onChange={(e) => setSubject(e.target.value)} />
              </Field>
            )}
          </div>
          <div className="mt-4">
            <div className="mb-2 text-pf-label text-pf-muted">
              Variables this template receives
            </div>
            <div className="flex flex-wrap gap-1.5">
              {editing.variables.map((v) => (
                <code key={v}
                  className="rounded bg-pf-bg px-2 py-1 font-mono text-[11px] text-pf-body">
                  {'{{'}{v}{'}}'}
                </code>
              ))}
            </div>
            <p className="mt-2 text-pf-help text-pf-muted">
              Fixed by the code that renders this message. A name not on this
              list would reach somebody's inbox as literal text, so the server
              refuses it.
            </p>
          </div>
          <div className="mt-4 flex items-center gap-2">
            <Button tone="primary" onClick={() => save()} disabled={saving}
              className="px-5 py-2.5">
              {saving ? 'Saving…' : 'Save'}
            </Button>
            {isPhone(editing) && editing.status !== 'published' && (
              <Button onClick={() => save('published')} disabled={saving || !providerId.trim()}>
                Save and publish
              </Button>
            )}
            {isPhone(editing) && editing.status === 'published' && (
              <Button onClick={() => save('draft')} disabled={saving}>
                Unpublish
              </Button>
            )}
            <Button onClick={() => setEditing(null)}>Cancel</Button>
          </div>
        </Card>
      )}

      <div className="mt-5">
        <DataTable
          title="Delivery log"
          count={d.deliveries.length}
          head={['When', 'Template', 'Channel', 'Recipient', 'Tenant', 'Result', 'Detail']}
          footnote="Recorded on its own transaction, so a request that rolled back after the mail went out still shows it."
          empty="Nothing has been sent since the delivery log was added.">
          {d.deliveries.map((x) => (
            <tr key={x.id} className="hover:bg-pf-bg">
              <Td className="whitespace-nowrap text-pf-muted">
                {new Date(x.sent_at).toLocaleString('en-IN',
                  { day: '2-digit', month: 'short', hour: '2-digit',
                    minute: '2-digit' })}
              </Td>
              <Td className="font-mono text-[11px] text-pf-navy">
                {x.template_code || '—'}
              </Td>
              <Td className="text-pf-muted">{x.channel}</Td>
              <Td className="text-pf-muted">{x.recipient}</Td>
              <Td className="text-pf-muted">{x.tenant_name || '—'}</Td>
              <Td><Pill value={x.status} /></Td>
              <Td className="max-w-[280px] truncate text-pf-muted">
                {x.detail || '—'}
              </Td>
            </tr>
          ))}
        </DataTable>
      </div>

      <Note>
        “Accepted” means the mail server took the message. A bounce afterwards
        is a different fact and would arrive as its own row — nothing here
        claims a message was read, or even that it reached an inbox. Message
        bodies stay in code so that a tenant's data cannot reshape them; the
        wording and subject are editable here, and a change creates a new
        version rather than overwriting what was already sent.
      </Note>
    </Page>
  )
}
