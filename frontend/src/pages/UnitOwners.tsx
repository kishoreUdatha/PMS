import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  BedDouble, CheckCircle2, KeyRound, Loader2, Pencil, Plus, UserRound,
} from 'lucide-react'
import {
  createOwnerContract, createUnitOwner, listUnitOwners, updateOwnerContract,
  updateUnitOwner,
  type OwnerContract, type OwnerContractIn, type UnitOwner, type UnitOwnerIn,
  type UnitOwnerList,
} from '../api'
import DateField from '../components/DateField'
import { Badge, Field, Modal, ModalButtons } from '../components/FormBits'
import Select from '../components/Select'
import { useActivePropertyId } from '../hooks/useProperty'
import { fmtDate } from '../lib/dates'
import { errorText, inputCls, orNull } from '../lib/forms'

/**
 * Unit owners — who owns which unit, and on what fee.
 *
 * The facts behind the Owner Statement report. A contract is current when
 * today falls inside its dates; ending one is setting its end date, so there
 * is no status that could be left saying "active" on a unit that changed
 * hands. The server refuses two contracts for the same room on the same
 * night, because that night's revenue would otherwise be paid out twice.
 */

type Editing =
  | { kind: 'owner'; owner: UnitOwner | null }
  | { kind: 'contract'; owner: UnitOwner; contract: OwnerContract | null }

export default function UnitOwners() {
  const propertyId = useActivePropertyId()
  const qc = useQueryClient()
  const [editing, setEditing] = useState<Editing | null>(null)
  const [toast, setToast] = useState('')

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['unit-owners', propertyId],
    queryFn: () => listUnitOwners(propertyId),
    enabled: propertyId !== '',
  })

  function done(message: string, fresh: UnitOwnerList) {
    qc.setQueryData(['unit-owners', propertyId], fresh)
    setToast(message)
    setTimeout(() => setToast(''), 3500)
    setEditing(null)
  }

  const owners = data?.rows ?? []
  const held = (data?.rooms ?? []).filter((r) => r.owner).length

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <div className="flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-1">
          <h1 className="flex items-center gap-2 text-display text-ink">
            <KeyRound size={26} className="text-brand" /> Unit Owners
          </h1>
          {data && (
            <span className="text-sm text-slate-500">
              {held} of {data.rooms.length} rooms are owned today
            </span>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {data?.can_edit && (
            <button onClick={() => setEditing({ kind: 'owner', owner: null })}
              className="flex items-center gap-1.5 whitespace-nowrap rounded-lg bg-brand px-3 py-2 text-sm font-medium text-white hover:bg-brand-dark disabled:opacity-40">
              <Plus size={15} /> New Owner
            </button>
          )}
        </div>
      </div>

      {toast && (
        <p className="flex items-start gap-2 rounded-xl bg-emerald-50 px-4 py-3 text-sm text-emerald-700">
          <CheckCircle2 size={16} className="mt-0.5 shrink-0" /> {toast}
        </p>
      )}

      {(isLoading || propertyId === '') && (
        <p className="flex items-center gap-2 py-10 text-sm text-slate-400">
          <Loader2 size={15} className="animate-spin" /> Loading owners…
        </p>
      )}
      {isError && (
        <p className="py-10 text-center text-sm text-red-600">
          {errorText(error, 'Owners could not be loaded.')}
        </p>
      )}
      {data && owners.length === 0 && (
        <div className="rounded-2xl border border-slate-100 bg-white px-4 py-14 text-center">
          <p className="text-sm font-medium text-slate-700">No unit owners yet</p>
          <p className="mt-1 text-xs text-slate-500">
            Add an owner, then assign the units they hold and the fee the property keeps.
          </p>
        </div>
      )}

      <div className="space-y-4">
        {owners.map((o) => (
          <section key={o.id} className="overflow-hidden rounded-2xl border border-slate-100 bg-white">
            <header className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-100 px-5 py-4">
              <div className="flex items-start gap-3">
                <span className="grid h-10 w-10 place-items-center rounded-full bg-brand/10 text-brand">
                  <UserRound size={18} />
                </span>
                <div>
                  <p className="flex items-center gap-2 font-semibold text-slate-800">
                    {o.name}
                    {o.status !== 'active' && <Badge tone="bg-slate-100 text-slate-500">Inactive</Badge>}
                  </p>
                  <p className="text-xs text-slate-500">
                    {[o.phone, o.email, o.pan && `PAN ${o.pan}`, o.gstin && `GSTIN ${o.gstin}`]
                      .filter(Boolean).join(' · ') || 'No contact details'}
                  </p>
                </div>
              </div>
              {data?.can_edit && (
                <div className="flex gap-2">
                  <button onClick={() => setEditing({ kind: 'owner', owner: o })}
                    className="flex items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-50">
                    <Pencil size={14} /> Edit
                  </button>
                  <button onClick={() => setEditing({ kind: 'contract', owner: o, contract: null })}
                    className="flex items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-50">
                    <BedDouble size={14} /> Assign Unit
                  </button>
                </div>
              )}
            </header>
            {o.contracts.length === 0 ? (
              <p className="px-5 py-4 text-sm text-slate-400">No unit assigned yet.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="bg-slate-50/60 text-left text-xs text-slate-500">
                    <tr>
                      <th className="px-5 py-2 font-semibold">Unit</th>
                      <th className="px-4 py-2 font-semibold">Room type</th>
                      <th className="px-4 py-2 text-right font-semibold">Management fee</th>
                      <th className="px-4 py-2 font-semibold">From</th>
                      <th className="px-4 py-2 font-semibold">To</th>
                      <th className="px-4 py-2 font-semibold">Contract</th>
                      <th className="w-12 px-4 py-2" />
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-50">
                    {o.contracts.map((c) => (
                      <tr key={c.id} className="text-slate-700">
                        <td className="px-5 py-2.5 font-medium text-slate-800">Room {c.room_code ?? '—'}</td>
                        <td className="px-4 py-2.5 text-slate-600">{c.room_type ?? '—'}</td>
                        <td className="px-4 py-2.5 text-right">{Number(c.management_fee_percent)}%</td>
                        <td className="px-4 py-2.5 text-slate-600">{fmtDate(c.start_date)}</td>
                        <td className="px-4 py-2.5 text-slate-600">{c.end_date ? fmtDate(c.end_date) : 'Open-ended'}</td>
                        <td className="px-4 py-2.5">
                          <Badge tone={c.current ? 'bg-emerald-50 text-emerald-700' : 'bg-slate-75 text-slate-500'}>
                            {c.current ? 'Current' : 'Not current'}
                          </Badge>
                        </td>
                        <td className="px-4 py-2.5 text-right">
                          {data?.can_edit && (
                            <button onClick={() => setEditing({ kind: 'contract', owner: o, contract: c })}
                              title={`Edit contract for room ${c.room_code}`}
                              aria-label={`Edit contract for room ${c.room_code}`}
                              className="rounded-lg p-2 text-slate-400 hover:bg-slate-50 hover:text-brand">
                              <Pencil size={15} />
                            </button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        ))}
      </div>

      {editing?.kind === 'owner' && (
        <OwnerModal propertyId={propertyId} owner={editing.owner}
          onClose={() => setEditing(null)} onDone={done} />
      )}
      {editing?.kind === 'contract' && data && (
        <ContractModal propertyId={propertyId} list={data} owner={editing.owner}
          contract={editing.contract} onClose={() => setEditing(null)} onDone={done} />
      )}
    </div>
  )
}

function OwnerModal({ propertyId, owner, onClose, onDone }: {
  propertyId: string; owner: UnitOwner | null
  onClose: () => void; onDone: (message: string, fresh: UnitOwnerList) => void
}) {
  const [f, setF] = useState({
    name: owner?.name ?? '', email: owner?.email ?? '', phone: owner?.phone ?? '',
    pan: owner?.pan ?? '', gstin: owner?.gstin ?? '',
    bank_details: owner?.bank_details ?? '', status: owner?.status ?? 'active',
    notes: owner?.notes ?? '',
  })
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const set = (k: keyof typeof f) => (v: string) => setF((p) => ({ ...p, [k]: v }))

  async function save() {
    setErr('')
    setBusy(true)
    const body: UnitOwnerIn = {
      name: f.name.trim(), email: orNull(f.email), phone: orNull(f.phone),
      pan: orNull(f.pan), gstin: orNull(f.gstin),
      bank_details: orNull(f.bank_details), status: f.status, notes: orNull(f.notes),
    }
    try {
      const fresh = owner
        ? await updateUnitOwner(propertyId, owner.id, body)
        : await createUnitOwner(propertyId, body)
      onDone(owner ? `${body.name} updated.` : `${body.name} added.`, fresh)
    } catch (e) {
      setErr(errorText(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal title={owner ? `Edit ${owner.name}` : 'New Unit Owner'} onClose={onClose} error={err}
      footer={<ModalButtons onClose={onClose} onSave={save} busy={busy}
        disabled={f.name.trim() === ''} label={owner ? 'Save Changes' : 'Add Owner'} />}>
      <Field label="Name" required>
        <input value={f.name} onChange={(e) => set('name')(e.target.value)}
          className={inputCls} autoFocus />
      </Field>
      <Field label="Status">
        <Select value={f.status} className={inputCls} onChange={(e) => set('status')(e.target.value)}>
          <option value="active">Active</option>
          <option value="inactive">Inactive</option>
        </Select>
      </Field>
      <Field label="Phone">
        <input value={f.phone} onChange={(e) => set('phone')(e.target.value)} className={inputCls} />
      </Field>
      <Field label="Email">
        <input value={f.email} type="email" onChange={(e) => set('email')(e.target.value)} className={inputCls} />
      </Field>
      <Field label="PAN">
        <input value={f.pan} maxLength={20} onChange={(e) => set('pan')(e.target.value)}
          className={`${inputCls} uppercase`} />
      </Field>
      <Field label="GSTIN">
        <input value={f.gstin} maxLength={20} onChange={(e) => set('gstin')(e.target.value)}
          className={`${inputCls} uppercase`} />
      </Field>
      <Field label="Bank details" wide hint="Where the owner is paid.">
        <input value={f.bank_details} maxLength={300}
          onChange={(e) => set('bank_details')(e.target.value)} className={inputCls} />
      </Field>
      <Field label="Notes" wide>
        <textarea value={f.notes} rows={2} className={`${inputCls} resize-none`}
          onChange={(e) => set('notes')(e.target.value)} />
      </Field>
    </Modal>
  )
}

function ContractModal({ propertyId, list, owner, contract, onClose, onDone }: {
  propertyId: string; list: UnitOwnerList; owner: UnitOwner
  contract: OwnerContract | null
  onClose: () => void; onDone: (message: string, fresh: UnitOwnerList) => void
}) {
  const [f, setF] = useState({
    room_id: contract?.room_id ?? '',
    management_fee_percent: contract?.management_fee_percent ?? '20',
    start_date: contract?.start_date ?? new Date().toISOString().slice(0, 10),
    end_date: contract?.end_date ?? '',
    notes: contract?.notes ?? '',
  })
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const set = (k: keyof typeof f) => (v: string) => setF((p) => ({ ...p, [k]: v }))
  const fee = Number(f.management_fee_percent)
  const valid = f.room_id !== '' && f.start_date !== '' && fee >= 0 && fee <= 100
    && f.management_fee_percent !== ''

  async function save() {
    setErr('')
    setBusy(true)
    const body: OwnerContractIn = {
      room_id: f.room_id, management_fee_percent: fee,
      start_date: f.start_date, end_date: f.end_date || null, notes: orNull(f.notes),
    }
    try {
      const fresh = contract
        ? await updateOwnerContract(propertyId, contract.id, body)
        : await createOwnerContract(propertyId, owner.id, body)
      onDone(contract ? 'Contract updated.' : `Unit assigned to ${owner.name}.`, fresh)
    } catch (e) {
      setErr(errorText(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal title={contract ? `Contract: room ${contract.room_code}` : `Assign a unit to ${owner.name}`}
      onClose={onClose} error={err}
      footer={<ModalButtons onClose={onClose} onSave={save} busy={busy}
        disabled={!valid} label={contract ? 'Save Contract' : 'Assign Unit'} />}>
      <Field label="Room" required wide>
        <Select blankIsChoice value={f.room_id} className={inputCls}
          onChange={(e) => set('room_id')(e.target.value)}>
          <option value="">Choose a room</option>
          {list.rooms.map((r) => (
            <option key={r.id} value={r.id}>
              {r.code}{r.room_type ? ` · ${r.room_type}` : ''}{r.owner ? ` (owned by ${r.owner})` : ''}
            </option>
          ))}
        </Select>
      </Field>
      <Field label="Management fee %" required hint="The share of room revenue the property keeps.">
        <input value={f.management_fee_percent} type="number" min={0} max={100} step="0.01"
          className={inputCls} onChange={(e) => set('management_fee_percent')(e.target.value)} />
      </Field>
      <Field label="Starts" required>
        <DateField className="w-full" value={f.start_date} onChange={set('start_date')}
          label="Contract start" />
      </Field>
      <Field label="Ends" hint="Leave empty while the contract runs. Set it to end the contract.">
        <DateField className="w-full" value={f.end_date} onChange={set('end_date')}
          min={f.start_date} label="Contract end" />
      </Field>
      <Field label="Notes" wide>
        <input value={f.notes} maxLength={500} onChange={(e) => set('notes')(e.target.value)}
          className={inputCls} />
      </Field>
    </Modal>
  )
}
