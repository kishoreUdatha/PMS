import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  AlertTriangle, CheckCircle2, Info, Loader2, Plus, Power, Trash2,
} from 'lucide-react'
import Select from './Select'
import {
  getOtaMapping, setOtaLive, setOtaMapping,
  type OtaMapping, type OtaPair,
} from '../api'
import { errorText } from '../lib/forms'

/**
 * Which of the OTA's own rooms and rates sells as which of our rate plans, and
 * the switch that puts the hotel on sale there.
 *
 * The last step of connecting an OTA, and until now the one a hotel could not
 * do: provisioning builds the channel switched off, and the pairing lived in
 * the channel manager's own admin. The OTA's rooms can only be listed once
 * the hotel has authorised the channel manager in the OTA's extranet; until
 * then this says so, and the codes can still be typed in from the extranet.
 *
 * Every dropdown offers this property's rate plans and nothing else -- the
 * server refuses anything else too, because the channel manager itself does
 * not check whose rate plan a channel is given.
 */
export default function OtaMappingTab({ connectionId, partnerName, onLiveChange }: {
  connectionId: string
  partnerName: string
  onLiveChange?: () => void
}) {
  const q = useQuery({
    queryKey: ['ota-mapping', connectionId],
    queryFn: () => getOtaMapping(connectionId),
    retry: false,
  })
  const [data, setData] = useState<OtaMapping | null>(null)
  const [pairs, setPairs] = useState<Record<string, string>>({})
  const [manual, setManual] = useState<OtaPair[]>([])
  const [busy, setBusy] = useState<'' | 'save' | 'live'>('')
  const [err, setErr] = useState('')
  const [ok, setOk] = useState('')

  const current = data ?? q.data ?? null
  useEffect(() => {
    if (!q.data) return
    setData(q.data)
    load(q.data)
  }, [q.data])

  function load(m: OtaMapping) {
    const listed = new Set(m.ota_rooms.flatMap((r) => r.rates.map((x) => `${r.code}|${x.code}`)))
    const next: Record<string, string> = {}
    const extra: OtaPair[] = []
    for (const p of m.pairs) {
      const key = `${p.ota_room_code}|${p.ota_rate_code}`
      if (listed.has(key)) next[key] = p.rate_plan_id
      else extra.push(p)
    }
    setPairs(next)
    setManual(extra)
  }

  const planLabel = useMemo(() => {
    const out: Record<string, string> = {}
    for (const p of current?.plans ?? []) {
      out[p.rate_plan_id] = p.room_type_name ? `${p.room_type_name} · ${p.name}` : p.name
    }
    return out
  }, [current])

  function problem(e: unknown, fallback: string) {
    setErr(errorText(e, fallback))
  }

  function collected(): OtaPair[] {
    const fromList = Object.entries(pairs)
      .filter(([, plan]) => plan)
      .map(([key, plan]) => {
        const [room, rate] = key.split('|')
        return { ota_room_code: room, ota_rate_code: rate, rate_plan_id: plan }
      })
    const typed = manual.filter((p) => p.ota_room_code.trim() && p.ota_rate_code.trim()
      && p.rate_plan_id)
    return [...fromList, ...typed]
  }

  async function save() {
    setErr(''); setOk(''); setBusy('save')
    try {
      const m = await setOtaMapping(connectionId, collected())
      setData(m); load(m)
      setOk(`Mapping saved at the channel manager. ${m.pairs.length} ${partnerName} `
        + `rate${m.pairs.length === 1 ? '' : 's'} paired.`)
    } catch (e) { problem(e, 'Could not save the mapping.') } finally { setBusy('') }
  }

  async function toggleLive() {
    if (!current) return
    const on = !current.live
    if (on && !window.confirm(`Switch ${partnerName} on? Your rooms go on sale there `
      + 'with the rates and availability this PMS sends.')) return
    setErr(''); setOk(''); setBusy('live')
    try {
      const m = await setOtaLive(connectionId, on)
      setData(m); load(m)
      setOk(m.live ? `${partnerName} is live.` : `${partnerName} is switched off.`)
      onLiveChange?.()
    } catch (e) { problem(e, 'The channel could not be switched.') } finally { setBusy('') }
  }

  if (q.isLoading) {
    return <div className="grid place-items-center py-10"><Loader2 className="animate-spin text-slate-400" /></div>
  }
  if (q.isError || !current) {
    return (
      <p className="mt-4 flex items-start gap-2 rounded-xl bg-slate-50 px-3 py-2.5 text-sm text-slate-600">
        <Info size={15} className="mt-0.5 shrink-0" />
        {errorText(q.error, `The ${partnerName} channel could not be read just now.`)}
      </p>
    )
  }

  const plans = current.plans
  const INPUT = 'w-full rounded-lg border border-slate-200 px-2.5 py-2 text-sm outline-none focus:border-brand'

  return (
    <div className="mt-4 space-y-4">
      <div className={`flex flex-wrap items-center justify-between gap-3 rounded-xl px-4 py-3 ${
        current.live ? 'bg-emerald-50' : 'bg-slate-50'}`}>
        <p className={`flex items-center gap-2 text-sm font-semibold ${
          current.live ? 'text-emerald-800' : 'text-slate-700'}`}>
          {current.live ? <CheckCircle2 size={16} /> : <Power size={16} />}
          {current.live
            ? `Live on ${partnerName}`
            : `${partnerName} is switched off: nothing is on sale there yet`}
        </p>
        <button type="button" onClick={() => void toggleLive()} disabled={busy !== ''}
          className={`rounded-lg px-3.5 py-2 text-sm font-semibold disabled:opacity-50 ${
            current.live ? 'border border-slate-200 bg-white text-slate-700 hover:border-red-300 hover:text-red-700'
              : 'bg-brand text-white hover:opacity-90'}`}>
          {busy === 'live' ? <Loader2 size={15} className="animate-spin" />
            : current.live ? 'Switch off' : 'Go live'}
        </button>
      </div>

      {current.foreign_pairs > 0 && (
        <p className="flex items-start gap-2 rounded-xl bg-amber-50 px-3 py-2.5 text-xs text-caution">
          <AlertTriangle size={14} className="mt-0.5 shrink-0" />
          This channel holds {current.foreign_pairs} pairing{current.foreign_pairs === 1 ? '' : 's'} for
          a rate plan that is not this property&apos;s. Save the mapping to remove
          {current.foreign_pairs === 1 ? ' it' : ' them'} before going live.
        </p>
      )}
      {err && (
        <p className="flex items-start gap-2 rounded-xl bg-red-50 px-3 py-2.5 text-sm text-red-700">
          <AlertTriangle size={15} className="mt-0.5 shrink-0" /> {err}
        </p>
      )}
      {ok && (
        <p className="flex items-start gap-2 rounded-xl bg-emerald-50 px-3 py-2.5 text-sm text-emerald-700">
          <CheckCircle2 size={15} className="mt-0.5 shrink-0" /> {ok}
        </p>
      )}
      {plans.length === 0 && (
        <p className="rounded-xl bg-slate-50 px-3 py-2.5 text-xs text-slate-600">
          None of this property&apos;s rate plans is set up at the channel manager yet.
          Map them on the Room &amp; rate mappings tab first.
        </p>
      )}

      {current.ota_rooms.length > 0 ? (
        <div className="overflow-x-auto rounded-xl border border-slate-100">
          <table className="w-full min-w-[520px] text-sm">
            <thead className="bg-slate-50 text-left text-xs font-semibold text-slate-500">
              <tr>
                <th className="px-3 py-2">{partnerName} room</th>
                <th className="px-3 py-2">{partnerName} rate</th>
                <th className="px-3 py-2">Sold as</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {current.ota_rooms.flatMap((room) => room.rates.map((rate) => {
                const key = `${room.code}|${rate.code}`
                return (
                  <tr key={key}>
                    <td className="px-3 py-2">
                      {room.title ?? room.code}
                      <span className="ml-1 font-mono text-xs text-slate-400">{room.code}</span>
                    </td>
                    <td className="px-3 py-2">
                      {rate.title ?? rate.code}
                      <span className="ml-1 font-mono text-xs text-slate-400">{rate.code}</span>
                    </td>
                    <td className="px-3 py-2">
                      <Select value={pairs[key] ?? ''} className={INPUT}
                        onChange={(e) => setPairs({ ...pairs, [key]: e.target.value })}>
                        <option value="">Not sold</option>
                        {plans.map((p) => (
                          <option key={p.rate_plan_id} value={p.rate_plan_id}>
                            {planLabel[p.rate_plan_id]}
                          </option>
                        ))}
                      </Select>
                    </td>
                  </tr>
                )
              }))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="flex items-start gap-2 rounded-xl bg-blue-50 px-3 py-2.5 text-xs text-blue-900">
          <Info size={14} className="mt-0.5 shrink-0" />
          {current.ota_rooms_error
            ?? `${partnerName} has not listed any rooms for this hotel yet.`}
        </p>
      )}

      {(current.ota_rooms.length === 0 || manual.length > 0) && (
        <div>
          <p className="text-xs font-semibold text-slate-500">
            {current.ota_rooms.length === 0
              ? `Or type the codes from the ${partnerName} extranet`
              : 'Pairs for codes not in the list above'}
          </p>
          <div className="mt-2 space-y-2">
            {manual.map((p, i) => (
              <div key={i} className="grid grid-cols-[1fr_1fr_1.6fr_auto] gap-2">
                <input value={p.ota_room_code} placeholder="Room code" maxLength={80}
                  onChange={(e) => setManual(manual.map((m, j) => j === i ? { ...m, ota_room_code: e.target.value } : m))}
                  className={`${INPUT} font-mono`} />
                <input value={p.ota_rate_code} placeholder="Rate code" maxLength={80}
                  onChange={(e) => setManual(manual.map((m, j) => j === i ? { ...m, ota_rate_code: e.target.value } : m))}
                  className={`${INPUT} font-mono`} />
                <Select value={p.rate_plan_id} className={INPUT}
                  onChange={(e) => setManual(manual.map((m, j) => j === i ? { ...m, rate_plan_id: e.target.value } : m))}>
                  <option value="">Choose a rate plan</option>
                  {plans.map((pl) => (
                    <option key={pl.rate_plan_id} value={pl.rate_plan_id}>{planLabel[pl.rate_plan_id]}</option>
                  ))}
                </Select>
                <button type="button" aria-label="Remove"
                  onClick={() => setManual(manual.filter((_, j) => j !== i))}
                  className="rounded-lg border border-slate-200 px-2.5 text-slate-400 hover:text-red-600">
                  <Trash2 size={15} />
                </button>
              </div>
            ))}
            {current.ota_rooms.length === 0 && (
              <button type="button" disabled={plans.length === 0}
                onClick={() => setManual([...manual, { ota_room_code: '', ota_rate_code: '', rate_plan_id: '' }])}
                className="flex items-center gap-1.5 text-sm font-semibold text-brand hover:underline disabled:opacity-50">
                <Plus size={15} /> Add a pair
              </button>
            )}
          </div>
        </div>
      )}

      <div className="flex justify-end">
        <button type="button" onClick={() => void save()} disabled={busy !== '' || plans.length === 0}
          className="rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white hover:opacity-90 disabled:opacity-50">
          {busy === 'save' ? <Loader2 size={15} className="animate-spin" /> : 'Save mapping'}
        </button>
      </div>
    </div>
  )
}
