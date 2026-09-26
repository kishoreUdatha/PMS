import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Settings, Save, Loader2, AlertTriangle, CheckCircle2, ShieldAlert } from 'lucide-react'
import SalesChannels from '../components/SalesChannels'
import TimeField from '../components/TimeField'
import Select from '../components/Select'
import ListSelect from '../components/ListSelect'
import { CURRENCIES, TIMEZONES, withCurrent } from '../lib/options'
import {
  
  getPropertySettings,
  updatePropertySettings,
  type PropertySettings as PS,
} from '../api'
import { Crumbs } from '../components/Crumbs'
import { useActivePropertyId } from '../hooks/useProperty'
import { errorText } from '../lib/forms'


export default function PropertySettings() {
  const propertyId = useActivePropertyId()

  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['propertySettings', propertyId],
    queryFn: () => getPropertySettings(propertyId),
    enabled: propertyId !== '',
    retry: false,
  })

  const [form, setForm] = useState<PS | null>(null)
  const [reason, setReason] = useState('')
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [msg, setMsg] = useState<{ tone: 'error' | 'warn'; text: string } | null>(null)

  useEffect(() => {
    if (data) setForm(data)
  }, [data])

  const status = (error as { response?: { status?: number } })?.response?.status
  const permissionDenied = isError && status === 403
  const unauthenticated = isError && status === 401

  function useDevAdmin() {
    localStorage.setItem('debug_subject', 'admin-user')
    refetch()
  }

  async function handleSave() {
    if (!form) return
    setMsg(null)
    setSaved(false)
    setSaving(true)
    try {
      const updated = await updatePropertySettings(propertyId, {
        version: form.version,
        name: form.name,
        timezone: form.timezone,
        currency: form.currency,
        checkin_time: form.checkin_time ?? null,
        checkout_time: form.checkout_time ?? null,
        address: form.address ?? null,
        reason: reason || null,
      })
      setForm(updated)
      setSaved(true)
      setReason('')
    } catch (e) {
      const err = e as { response?: { status?: number; data?: { detail?: string } } }
      const status = err.response?.status
      if (status === 409) {
        setMsg({ tone: 'warn', text: 'This record changed since you loaded it. Reloading latest…' })
        await refetch()
      } else if (status === 403) {
        setMsg({ tone: 'error', text: 'You do not have permission to update property settings.' })
      } else {
        setMsg({ tone: 'error', text: errorText(err, 'Failed to save settings.') })
      }
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="space-y-5">
      <div>
        <Crumbs title="Property Settings" trail={[
          { label: 'Property', to: '/property' },
          { label: 'Property Settings' }]} />
        <h1 className="mt-1 flex items-center gap-2 text-display text-ink">
          <Settings size={26} className="text-brand" /> Property Settings
        </h1>
        <p className="text-slate-500">View and update your property configuration.</p>
      </div>

      {unauthenticated && (
        <Banner tone="warn" icon={<ShieldAlert size={16} />}>
          You are not signed in. Authentication (Keycloak) isn't wired yet — for
          local development, sign in as the demo admin to continue.
          <button onClick={useDevAdmin} className="ml-3 rounded-lg bg-brand px-3 py-1 text-xs font-medium text-white hover:bg-brand/90">
            Use demo admin
          </button>
        </Banner>
      )}
      {permissionDenied && (
        <Banner tone="error" icon={<ShieldAlert size={16} />}>
          Access denied. Your role does not permit viewing Property Settings.
        </Banner>
      )}
      {isLoading && <Banner tone="info" icon={<Loader2 size={16} className="animate-spin" />}>Loading settings…</Banner>}
      {isError && !permissionDenied && !unauthenticated && (
        <Banner tone="error" icon={<AlertTriangle size={16} />}>Failed to load: {(error as Error)?.message}</Banner>
      )}
      {msg && <Banner tone={msg.tone} icon={<AlertTriangle size={16} />}>{msg.text}</Banner>}
      {saved && <Banner tone="success" icon={<CheckCircle2 size={16} />}>Settings saved. Change recorded in the audit log.</Banner>}

      {form && (
        <div className="max-w-3xl rounded-2xl border border-slate-100 bg-white p-6 shadow-sm">
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <Field label="Property Code"><input value={form.code} readOnly className={`${inputCls} bg-slate-50`} /></Field>
            <Field label="Status"><input value={form.status} readOnly className={`${inputCls} bg-slate-50`} /></Field>
            <Field label="Property Name" required><input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className={inputCls} /></Field>
            <Field label="Address"><input value={form.address ?? ''} onChange={(e) => setForm({ ...form, address: e.target.value })} className={inputCls} /></Field>
            <Field label="Timezone" required><ListSelect value={form.timezone} options={TIMEZONES} placeholder="Select timezone" onChange={(v) => setForm({ ...form, timezone: v })} className={inputCls} /></Field>
            <Field label="Currency" required>
              <Select value={form.currency} onChange={(e) => setForm({ ...form, currency: e.target.value })} className={inputCls}>
                {withCurrent(CURRENCIES.map((c) => c.code), form.currency).map((code) => {
                  const c = CURRENCIES.find((x) => x.code === code)
                  return <option key={code} value={code}>{c ? `${code} (${c.symbol}) · ${c.name}` : code}</option>
                })}
              </Select>
            </Field>
            <Field label="Check-in Time"><TimeField value={form.checkin_time ?? ''} onChange={(v) => setForm({ ...form, checkin_time: v })} label="Check-in time" className="w-full" /></Field>
            <Field label="Check-out Time"><TimeField value={form.checkout_time ?? ''} onChange={(v) => setForm({ ...form, checkout_time: v })} label="Check-out time" className="w-full" /></Field>
          </div>

          <Field label="Reason for change (recorded in audit)">
            <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Optional — required for sensitive overrides" className={inputCls} />
          </Field>

          <div className="mt-5 flex items-center justify-between">
            <span className="text-xs text-slate-400">Version {form.version} · optimistic locking prevents overwrites</span>
            <button onClick={handleSave} disabled={saving}
              className="flex items-center gap-2 rounded-xl bg-brand px-5 py-2.5 text-sm font-medium text-white enabled:hover:bg-brand/90 disabled:opacity-50">
              {saving ? <Loader2 size={16} className="animate-spin" /> : <Save size={16} />} Save Changes
            </button>
          </div>
        </div>
      )}

      {/* Its own card, and saved on its own. Putting a property on sale is not
          a field on a form -- it publishes the hotel to the internet, so it
          should not ride along with a phone number correction under one Save
          button. */}
      {form && (
        <SalesChannels propertyId={propertyId} propertyCode={form.code} />
      )}
    </div>
  )
}

const inputCls = 'w-full rounded-lg border border-slate-200 px-3 py-2 text-sm outline-none focus:border-brand'

function Field({ label, required, children }: { label: string; required?: boolean; children: React.ReactNode }) {
  return (
    <label className="mt-4 block">
      <span className="mb-1 block text-sm font-medium text-slate-600">{label}{required && <span className="text-red-500"> *</span>}</span>
      {children}
    </label>
  )
}

function Banner({ children, icon, tone }: { children: React.ReactNode; icon: React.ReactNode; tone: 'info' | 'warn' | 'error' | 'success' }) {
  const cls = {
    info: 'bg-blue-50 text-blue-700',
    warn: 'bg-amber-50 text-amber-700',
    error: 'bg-red-50 text-red-700',
    success: 'bg-emerald-50 text-emerald-700',
  }[tone]
  return <div className={`flex items-center gap-2 rounded-xl px-4 py-3 text-sm ${cls}`}>{icon}{children}</div>
}
