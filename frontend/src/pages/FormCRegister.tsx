import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle, CheckCircle2, FileWarning, Loader2, ShieldCheck, X,
} from 'lucide-react'
import { CONTROL_TYPE } from '../lib/controls'
import { fmtDate } from '../lib/dates'
import {
  getFormCRegister, getFormC, saveFormC, fileFormC,
  type FormCRow, type FormCDetail,
} from '../api'
import { useActivePropertyId } from '../hooks/useProperty'
import { errorText } from '../lib/forms'

/**
 * Form C register — who has been reported to the Bureau of Immigration.
 *
 * Rule 14 of the Registration of Foreigners Rules 1992 gives a property 24
 * hours from check-in to report a foreign guest on the FRRO portal. Not
 * filing is an offence under the Foreigners Act, and this screen is the
 * evidence an inspection asks for.
 *
 * **Unknown nationality is shown apart from overdue filings, deliberately.**
 * The first version of this register counted a blank nationality as foreign,
 * on the reasoning that an unanswered question is not an exemption. Sound
 * reasoning, wrong result: nationality is recorded for almost nobody, so it
 * drew forty-eight rows headed overdue, every one a domestic guest and every
 * one a legal breach the property had not committed. A compliance screen
 * that is wrong forty-eight times on its first morning is a screen nobody
 * opens again — and then the one real arrival is buried in it.
 *
 * So the list is guests known to be foreign, and the unrecorded ones are a
 * separate count with their own button. Both need answering. Only one is a
 * missed filing.
 */

function Stat({ tone, icon, label, value, onClick, active }: {
  tone: string; icon: React.ReactNode; label: string; value: number
  onClick?: () => void; active?: boolean
}) {
  const Tag = (onClick ? 'button' : 'div') as 'button' | 'div'
  return (
    <Tag onClick={onClick}
      className={`flex items-center gap-2 rounded-xl border px-3 py-2 text-left ${
        active ? 'border-brand bg-brand-light/40' : 'border-slate-200 bg-white'
      } ${onClick ? 'hover:border-brand' : ''}`}>
      <span className={`grid h-7 w-7 place-items-center rounded-lg ${tone}`}>
        {icon}
      </span>
      <span>
        <span className="block text-sm font-semibold text-ink">{value}</span>
        <span className="block text-xs text-slate-500">{label}</span>
      </span>
    </Tag>
  )
}

export default function FormCRegister() {
  const propertyId = useActivePropertyId()
  const qc = useQueryClient()
  const [showUnknown, setShowUnknown] = useState(false)
  const [open, setOpen] = useState<string | null>(null)

  const reg = useQuery({
    queryKey: ['form-c-register', propertyId, showUnknown],
    enabled: !!propertyId,
    queryFn: () => getFormCRegister(propertyId!, {
      days: '90', include_unknown: showUnknown ? 'true' : undefined,
    }),
  })

  const rows = reg.data?.rows ?? []

  return (
    <div className="space-y-4">
      <div>
        <h1 className="flex items-center gap-2 text-display text-ink">
          <ShieldCheck size={26} className="text-brand" /> Form C
        </h1>
        <p className="mt-1 max-w-3xl text-sm text-slate-500">
          Foreign guests must be reported to the Bureau of Immigration within
          24 hours of check-in — Rule 14 of the Registration of Foreigners
          Rules 1992. Filing happens on the FRRO portal; this is the record
          of what was filed and what is still owed.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Stat tone="bg-rose-100 text-rose-700" value={reg.data?.overdue ?? 0}
          icon={<AlertTriangle size={15} />} label="Past 24 hours" />
        <Stat tone="bg-amber-100 text-amber-700" value={reg.data?.pending ?? 0}
          icon={<FileWarning size={15} />} label="Awaiting filing" />
        <Stat tone="bg-emerald-100 text-emerald-700" value={reg.data?.filed ?? 0}
          icon={<CheckCircle2 size={15} />} label="Filed" />
        {/* Its own button, not a row in the list above: this is missing data,
            not a missed filing, and merging the two is what made the first
            version of this screen unusable. */}
        <Stat tone="bg-slate-100 text-slate-600" value={reg.data?.unknown ?? 0}
          icon={<FileWarning size={15} />} label="Nationality not recorded"
          active={showUnknown} onClick={() => setShowUnknown((v) => !v)} />
      </div>

      {showUnknown && (
        <p className="rounded-lg bg-slate-50 p-3 text-xs text-slate-600">
          These guests have no nationality on file, so whether they need a
          Form C is unknown. They are not counted as overdue. Record a
          nationality at check-in and they leave this list.
        </p>
      )}

      <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
        <table className="w-full min-w-[52rem] text-sm">
          <thead className="border-b border-slate-200 bg-slate-50 text-left
            text-xs uppercase tracking-wide text-slate-500">
            <tr>
              <th className="px-4 py-3 font-medium">Guest</th>
              <th className="px-4 py-3 font-medium">Booking</th>
              <th className="px-4 py-3 font-medium">Room</th>
              <th className="px-4 py-3 font-medium">Nationality</th>
              <th className="px-4 py-3 font-medium">Checked in</th>
              <th className="px-4 py-3 font-medium">Status</th>
              <th className="px-4 py-3 text-right font-medium">Time left</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {reg.isLoading && (
              <tr><td colSpan={7} className="px-4 py-10 text-center">
                <Loader2 className="mx-auto h-4 w-4 animate-spin text-slate-400" />
              </td></tr>
            )}
            {!reg.isLoading && rows.length === 0 && (
              <tr><td colSpan={7}
                className="px-4 py-10 text-center text-sm text-slate-400">
                No foreign guest has checked in. Nothing is owed to the
                Bureau of Immigration.
              </td></tr>
            )}
            {rows.map((r: FormCRow) => (
              <tr key={r.reservation_unit_id}
                onClick={() => setOpen(r.reservation_unit_id)}
                className="cursor-pointer hover:bg-slate-50">
                <td className="px-4 py-3 text-slate-700">
                  {r.guest_name ?? '—'}
                </td>
                <td className="px-4 py-3 text-slate-600">
                  {r.reservation_number ?? '—'}
                </td>
                <td className="px-4 py-3 text-slate-600">{r.room_code ?? '—'}</td>
                <td className="px-4 py-3">
                  {r.nationality_state === 'unknown' ? (
                    <span className="rounded-full bg-slate-100 px-2 py-0.5
                      text-xs text-slate-500">Not recorded</span>
                  ) : (
                    <span className="text-slate-700">{r.nationality}</span>
                  )}
                </td>
                <td className="whitespace-nowrap px-4 py-3 text-slate-600">
                  {r.checked_in_at
                    ? fmtDate(new Date(r.checked_in_at)) : '—'}
                </td>
                <td className="px-4 py-3">
                  {r.status === 'filed' ? (
                    <span className="rounded-full bg-emerald-100 px-2 py-0.5
                      text-xs font-medium text-emerald-700">
                      Filed
                    </span>
                  ) : (
                    <span className="rounded-full bg-amber-100 px-2 py-0.5
                      text-xs font-medium text-amber-700">
                      {r.missing_count > 0
                        ? `${r.missing_count} field${
                          r.missing_count === 1 ? '' : 's'} missing`
                        : 'Ready to file'}
                    </span>
                  )}
                </td>
                <td className="whitespace-nowrap px-4 py-3 text-right
                  tabular-nums">
                  {r.status === 'filed' ? (
                    <span className="text-xs text-slate-400">
                      {r.acknowledgement_no}
                    </span>
                  ) : r.hours_left === null ? (
                    <span className="text-xs text-slate-400">—</span>
                  ) : r.hours_left < 0 ? (
                    <span className="text-xs font-semibold text-rose-600">
                      {Math.abs(Math.round(r.hours_left))}h overdue
                    </span>
                  ) : (
                    <span className="text-xs text-slate-600">
                      {Math.round(r.hours_left)}h
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {open && (
        <FormCDialog unitId={open} propertyId={propertyId!}
          onClose={() => setOpen(null)}
          onSaved={() => {
            qc.invalidateQueries({ queryKey: ['form-c-register'] })
          }} />
      )}
    </div>
  )
}

const field = 'w-full rounded-lg border border-slate-200 px-3 py-2 text-sm outline-none focus:border-brand'
const lbl = 'mb-1 block text-xs font-medium text-slate-500'

/** The form itself, grouped the way the FRRO asks for it. */
function FormCDialog({ unitId, propertyId, onClose, onSaved }: {
  unitId: string; propertyId: string; onClose: () => void; onSaved: () => void
}) {
  const [form, setForm] = useState<Partial<FormCDetail> | null>(null)
  const [ack, setAck] = useState('')
  const [err, setErr] = useState('')

  const detail = useQuery({
    queryKey: ['form-c', unitId],
    queryFn: async () => {
      const d = await getFormC(unitId, propertyId)
      setForm(d)
      return d
    },
  })
  const d = detail.data
  const filed = d?.status === 'filed'
  const set = (k: keyof FormCDetail) => (v: string) =>
    setForm((f) => ({ ...(f ?? {}), [k]: v || null }))

  const run = async (fn: () => Promise<unknown>) => {
    setErr('')
    try { await fn(); onSaved() } catch (e) {
      setErr(errorText(e, 'That did not go through.'))
    }
  }

  const save = useMutation({
    mutationFn: () => saveFormC(unitId, propertyId, form ?? {}),
    onSuccess: (d) => { setForm(d); detail.refetch(); onSaved() },
  })
  const file = useMutation({
    mutationFn: () => fileFormC(unitId, propertyId, { acknowledgement_no: ack }),
    onSuccess: (d) => { setForm(d); detail.refetch(); onSaved() },
  })

  const Text = ({ k, label, type = 'text' }: {
    k: keyof FormCDetail; label: string; type?: string
  }) => (
    <div>
      <label className={lbl}>{label}</label>
      <input type={type} className={field} disabled={filed}
        defaultValue={(form?.[k] as string) ?? ''}
        onBlur={(e) => set(k)(e.target.value)} />
    </div>
  )

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-slate-900/50 p-4"
      role="dialog" aria-modal="true" onClick={onClose}>
      <div className="max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-2xl
        bg-white shadow-xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b
          border-slate-100 px-4 py-3">
          <h2 className="text-sm font-semibold text-ink">
            Form C — {d?.reservation_number ?? ''}
            {d?.room_code ? ` · Room ${d.room_code}` : ''}
          </h2>
          <button onClick={onClose} aria-label="Close"
            className="rounded-lg p-1 text-slate-400 hover:bg-slate-100">
            <X size={16} />
          </button>
        </div>

        <div className="space-y-4 p-4">
          {detail.isLoading && (
            <Loader2 className="mx-auto h-4 w-4 animate-spin text-slate-400" />
          )}

          {d && !d.required && (
            <p className="rounded-lg bg-slate-50 p-3 text-xs text-slate-600">
              This guest is not recorded as a foreign national, so no Form C
              is required. Filling one in anyway is harmless — but check the
              nationality first.
            </p>
          )}
          {filed && (
            <p className="rounded-lg bg-emerald-50 p-3 text-xs text-emerald-800">
              Filed{d?.filed_at ? ` on ${fmtDate(new Date(d.filed_at))}` : ''} ·
              acknowledgement <strong>{d?.acknowledgement_no}</strong>.
              The fields are locked: a correction goes on the FRRO portal, not
              here.
            </p>
          )}
          {d && !filed && d.missing.length > 0 && (
            <p className="rounded-lg bg-amber-50 p-3 text-xs text-amber-800">
              The FRRO will not accept this yet — missing:{' '}
              {d.missing.join(', ')}.
            </p>
          )}

          {d && (
            <>
              <section>
                <h3 className="mb-2 text-xs font-semibold uppercase
                  tracking-wide text-slate-400">Guest</h3>
                <div className="grid gap-3 sm:grid-cols-2">
                  <Text k="full_name" label="Full name (as on passport)" />
                  <Text k="nationality" label="Nationality" />
                  <Text k="sex" label="Sex" />
                  <Text k="date_of_birth" label="Date of birth" type="date" />
                </div>
              </section>

              <section>
                <h3 className="mb-2 text-xs font-semibold uppercase
                  tracking-wide text-slate-400">Passport</h3>
                <div className="grid gap-3 sm:grid-cols-2">
                  <Text k="passport_number" label="Passport number" />
                  <Text k="passport_issue_place" label="Place of issue" />
                  <Text k="passport_issue_date" label="Date of issue" type="date" />
                  <Text k="passport_expiry_date" label="Expiry" type="date" />
                </div>
              </section>

              <section>
                <h3 className="mb-2 text-xs font-semibold uppercase
                  tracking-wide text-slate-400">Visa</h3>
                <div className="grid gap-3 sm:grid-cols-2">
                  <Text k="visa_number" label="Visa number" />
                  <Text k="visa_type" label="Visa type" />
                  <Text k="visa_issue_place" label="Place of issue" />
                  <Text k="visa_issue_date" label="Date of issue" type="date" />
                  <Text k="visa_expiry_date" label="Valid until" type="date" />
                </div>
              </section>

              <section>
                <h3 className="mb-2 text-xs font-semibold uppercase
                  tracking-wide text-slate-400">Arrival in India</h3>
                {/* Laboured in the label on purpose: this is the border, not
                    the front desk, and filling in the hotel's check-in date
                    here is the commonest way a Form C is filed wrong. */}
                <div className="grid gap-3 sm:grid-cols-2">
                  <Text k="arrived_in_india_on"
                    label="Date of arrival in India (not at the hotel)"
                    type="date" />
                  <Text k="arrived_in_india_at"
                    label="Place of arrival (airport / port)" />
                  <Text k="permanent_address" label="Permanent address abroad" />
                  <Text k="address_in_india" label="Address in India" />
                  <Text k="purpose_of_visit" label="Purpose of visit" />
                  <Text k="next_destination" label="Next destination" />
                </div>
              </section>

              {err && <p className="text-xs text-rose-600">{err}</p>}
              {(save.error || file.error) && (
                <p className="text-xs text-rose-600">
                  {errorText((save.error ?? file.error), 'That did not go through.')}
                </p>
              )}

              {!filed && (
                <div className="flex flex-wrap items-end gap-2 border-t
                  border-slate-100 pt-3">
                  <button onClick={() => run(() => save.mutateAsync())}
                    disabled={save.isPending}
                    className={`rounded-lg border border-slate-200 px-3 py-2
                      ${CONTROL_TYPE} text-slate-600 hover:border-brand
                      disabled:opacity-50`}>
                    {save.isPending ? 'Saving…' : 'Save'}
                  </button>
                  <div className="flex-1 min-w-[12rem]">
                    <label className={lbl}>FRRO acknowledgement number</label>
                    <input className={field} value={ack}
                      placeholder="From the portal, after submitting"
                      onChange={(e) => setAck(e.target.value)} />
                  </div>
                  {/* Records a filing; it does not perform one. The portal
                      login belongs to the property, and a button here that
                      implied submission would be the most dangerous thing on
                      this screen — the register is what an inspection reads. */}
                  <button onClick={() => run(() => file.mutateAsync())}
                    disabled={!ack.trim() || (d.missing.length > 0)
                      || file.isPending}
                    title={d.missing.length > 0
                      ? 'Complete the required fields first'
                      : !ack.trim()
                        ? 'Enter the acknowledgement number the portal gave you'
                        : undefined}
                    className={`rounded-lg bg-brand px-3 py-2 ${CONTROL_TYPE}
                      text-white hover:opacity-90 disabled:opacity-40`}>
                    Mark as filed
                  </button>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}
