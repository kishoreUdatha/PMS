import { useState } from 'react'
import Select from './Select'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Loader2, X } from 'lucide-react'
import { createBlock, BLOCK_REASONS } from '../api'

import DateField from '../components/DateField'
/**
 * Create a block on one room (screen 063's action, offered from 008 and 011).
 *
 * Shared rather than duplicated: both entry points must apply the same rules,
 * and a block that behaves differently depending on where it was raised from
 * would be a bug waiting to happen.
 */
export default function BlockDialog({
  propertyId, roomId, roomCode, defaultBlockType = 'out_of_order', onClose, onBlocked,
}: {
  propertyId: string
  roomId: string
  roomCode: string
  defaultBlockType?: 'out_of_order' | 'room_block'
  onClose: () => void
  onBlocked?: () => void
}) {
  const qc = useQueryClient()
  const today = new Date().toISOString().slice(0, 10)
  const [form, setForm] = useState({
    block_type: defaultBlockType as string,
    reason_category: 'maintenance_scheduled',
    reason: '',
    start_date: today,
    end_date: today,
  })
  const [error, setError] = useState<string | null>(null)

  const save = useMutation({
    mutationFn: () =>
      createBlock(propertyId, {
        ...form, room_ids: [roomId], severity: 'medium',
        reason: form.reason || null,
      }),
    onSuccess: (res) => {
      // A clash comes back as a conflict rather than an error, so surface it
      // here instead of closing on a block that was never created.
      if (res.conflicts.length) {
        setError(res.conflicts[0].detail)
        return
      }
      qc.invalidateQueries({ queryKey: ['rooms', propertyId] })
      qc.invalidateQueries({ queryKey: ['roomBlocks', propertyId] })
      qc.invalidateQueries({ queryKey: ['blockStats', propertyId] })
      qc.invalidateQueries({ queryKey: ['roomOverview', propertyId, roomId] })
      qc.invalidateQueries({ queryKey: ['roomMaintenance', propertyId, roomId] })
      qc.invalidateQueries({ queryKey: ['statusHistory', propertyId, roomId] })
      onBlocked?.()
      onClose()
    },
    onError: (e: unknown) => {
      const d = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setError(d ?? 'Could not block the room.')
    },
  })

  const field = 'w-full rounded-lg border border-slate-200 px-3 py-2 text-sm'
  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-slate-900/30 p-4">
      <div className="w-full max-w-md rounded-xl bg-white shadow-lg">
        <div className="flex items-start justify-between border-b border-slate-200 px-6 py-4">
          <div>
            <h2 className="text-lg font-semibold text-ink">Block Room {roomCode}</h2>
            <p className="text-sm text-slate-500">
              The room will be removed from sale for these dates.
            </p>
          </div>
          <button onClick={onClose} aria-label="Close"
            className="text-slate-400 hover:text-slate-600">
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="space-y-4 px-6 py-5">
          <div>
            <span className="mb-1 block text-sm font-medium text-slate-700">Block Type</span>
            {([['out_of_order', 'Out of Order (not sellable)'],
               ['room_block', 'Room Block (hold inventory)']] as const).map(([v, l]) => (
              <label key={v} className="flex items-center gap-2 py-0.5 text-sm text-slate-700">
                <input type="radio" name="rc-block-type" checked={form.block_type === v}
                  onChange={() => setForm({ ...form, block_type: v })}
                  className="h-4 w-4 border-slate-300 text-brand" />
                {l}
              </label>
            ))}
          </div>
          <label className="block">
            <span className="mb-1 block text-sm font-medium text-slate-700">Reason</span>
            <Select value={form.reason_category}
              onChange={(e) => setForm({ ...form, reason_category: e.target.value })}
              className={field}>
              {BLOCK_REASONS.map((r) => (
                <option key={r.code} value={r.code}>{r.label}</option>
              ))}
            </Select>
          </label>
          <div className="grid grid-cols-2 gap-3">
            <label>
              <span className="mb-1 block text-sm font-medium text-slate-700">From</span>
              <DateField value={form.start_date} onChange={(v) => setForm({ ...form, start_date: v })} className={field} />
            </label>
            <label>
              <span className="mb-1 block text-sm font-medium text-slate-700">To</span>
              <DateField value={form.end_date} onChange={(v) => setForm({ ...form, end_date: v })} className={field} />
            </label>
          </div>
          <label className="block">
            <span className="mb-1 block text-sm font-medium text-slate-700">Remarks</span>
            <textarea value={form.reason} rows={2} maxLength={500}
              onChange={(e) => setForm({ ...form, reason: e.target.value })}
              className={field} />
          </label>
          {error && (
            <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>
          )}
        </div>

        <div className="flex justify-end gap-3 border-t border-slate-200 px-6 py-4">
          <button onClick={onClose}
            className="rounded-lg border border-slate-200 px-4 py-2 text-sm font-semibold text-slate-600">
            Cancel
          </button>
          <button onClick={() => { setError(null); save.mutate() }}
            disabled={save.isPending}
            className="inline-flex items-center gap-2 rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white hover:bg-brand/90 disabled:opacity-50">
            {save.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
            Block Room
          </button>
        </div>
      </div>
    </div>
  )
}
