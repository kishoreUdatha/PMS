import { useState } from 'react'
import Select from './Select'
import { useMutation } from '@tanstack/react-query'
import { AlertCircle, BarChart3, Loader2, X } from 'lucide-react'
import { simulateRate, type SimulateResult } from '../api'

import DateField from '../components/DateField'
/**
 * "Simulate Rate" from screen 119, shared by the rule list and the rule editor.
 *
 * It calls the same resolution the rest of the system uses — calendar rate,
 * then rate plan, then each rule in priority order — so what it previews is
 * what the configuration actually produces.
 */

const input = 'w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700'
const today = () => new Date().toISOString().slice(0, 10)
const money = (v: string | null) =>
  v === null ? '—' : `₹${Number(v).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`

function apiError(e: unknown): string {
  const d = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  if (typeof d === 'string') return d
  return 'Could not simulate. Please try again.'
}

function Field({ label, hint, children }: {
  label: string; hint?: string; children: React.ReactNode
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-sm font-medium text-slate-700">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-slate-400">{hint}</span>}
    </label>
  )
}

export default function SimulateRateDialog({
  propertyId, roomTypes, ratePlans, onClose,
}: {
  propertyId: string
  roomTypes: { id: string; name: string }[]
  ratePlans: { id: string; code: string; name: string }[]
  onClose: () => void
}) {
  const [stayDate, setStayDate] = useState(today())
  const [roomTypeId, setRoomTypeId] = useState(roomTypes[0]?.id ?? '')
  const [ratePlanId, setRatePlanId] = useState('')
  const [nights, setNights] = useState(2)
  const [leadDays, setLeadDays] = useState('')
  const [result, setResult] = useState<SimulateResult | null>(null)
  const [error, setError] = useState<string | null>(null)

  const run = useMutation({
    mutationFn: () => simulateRate(propertyId, {
      stay_date: stayDate, room_type_id: roomTypeId,
      rate_plan_id: ratePlanId || undefined, nights,
      lead_days: leadDays === '' ? undefined : Number(leadDays),
    }),
    onSuccess: (r) => { setResult(r); setError(null) },
    onError: (e) => { setError(apiError(e)); setResult(null) },
  })

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-slate-900/40 p-4">
      <div className="flex max-h-[90vh] w-full max-w-2xl flex-col rounded-xl bg-white shadow-xl">
        <div className="flex items-start justify-between border-b border-slate-100 px-5 py-4">
          <div>
            <h3 className="text-lg font-bold text-ink">Simulate Rate</h3>
            <p className="text-sm text-slate-500">
              What one night would cost, and which rules got it there
            </p>
          </div>
          <button onClick={onClose} aria-label="Close"
            className="rounded p-1 text-slate-400 hover:bg-slate-100">
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-5 py-4">
          <div className="grid grid-cols-2 gap-3">
            <Field label="Stay date">
              <DateField value={stayDate} onChange={(v) => setStayDate(v)} className={input} />
            </Field>
            <Field label="Room type">
              <Select className={input} value={roomTypeId}
                onChange={(e) => setRoomTypeId(e.target.value)}>
                {roomTypes.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
              </Select>
            </Field>
            <Field label="Rate plan">
              <Select className={input} value={ratePlanId}
                onChange={(e) => setRatePlanId(e.target.value)}>
                <option value="">None (room type rate)</option>
                {ratePlans.map((p) => (
                  <option key={p.id} value={p.id}>{p.name} ({p.code})</option>
                ))}
              </Select>
            </Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Nights">
                <input type="number" min={1} className={input} value={nights}
                  onChange={(e) => setNights(Number(e.target.value))} />
              </Field>
              <Field label="Booked days ahead" hint="Blank = from today">
                <input type="number" min={0} className={input} value={leadDays}
                  placeholder="auto"
                  onChange={(e) => setLeadDays(e.target.value)} />
              </Field>
            </div>
          </div>

          {result && (
            <div className="space-y-3">
              <div className="overflow-hidden rounded-lg border border-slate-200">
                {result.steps.map((s, i) => (
                  <div key={i}
                    className={`flex items-center justify-between gap-3 px-4 py-2.5 text-sm ${
                      i > 0 ? 'border-t border-slate-100' : ''}`}>
                    <div className="min-w-0">
                      <p className="font-medium text-slate-800">{s.label}</p>
                      <p className="truncate text-xs text-slate-500">{s.detail}</p>
                    </div>
                    <span className="shrink-0 font-semibold tabular-nums text-slate-800">
                      {money(s.amount)}
                    </span>
                  </div>
                ))}
              </div>
              <div className="flex items-center justify-between rounded-lg bg-brand-light/60 px-4 py-3">
                <span className="font-semibold text-slate-800">Final rate</span>
                <span className="text-xl font-bold text-slate-800">
                  {money(result.final_rate)}
                </span>
              </div>
              {result.stop_sell && (
                <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">
                  A rule stops sale on this date, so the room cannot be sold at any rate.
                </p>
              )}
              {result.skipped.length > 0 && (
                <div className="rounded-lg bg-slate-50 px-3 py-2">
                  <p className="mb-1 text-xs font-semibold text-slate-600">
                    Rules that did not apply
                  </p>
                  {result.skipped.map((s, i) => (
                    <p key={i} className="text-xs text-slate-500">· {s}</p>
                  ))}
                </div>
              )}
            </div>
          )}

          {error && (
            <p className="flex items-start gap-2 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" /> {error}
            </p>
          )}
        </div>

        <div className="flex justify-end gap-2 border-t border-slate-100 px-5 py-4">
          <button onClick={onClose}
            className="rounded-lg border border-slate-200 px-4 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50">
            Close
          </button>
          <button onClick={() => run.mutate()} disabled={run.isPending || !roomTypeId}
            className="flex items-center gap-2 rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white hover:bg-brand-dark disabled:opacity-50">
            {run.isPending ? <Loader2 className="h-4 w-4 animate-spin" />
              : <BarChart3 className="h-4 w-4" />}
            Run
          </button>
        </div>
      </div>
    </div>
  )
}

