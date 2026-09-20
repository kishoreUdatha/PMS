/**
 * Market Segment master.
 *
 * A controlled vocabulary bookings are classified by. It was free text until
 * now, which meant "Corporate", "corporate" and "CORP" were three segments to
 * every report and one to everybody else.
 *
 * It used to carry a second tab for Business Sources. That list is the set of
 * partners a property sells through, and it now has a screen of its own —
 * Channel Partners — which shows the same registry alongside the bookings and
 * revenue each partner has actually produced, and which can rename, classify
 * and retire them. Two screens listing the same rows was one screen too many,
 * and the one that knew less was this one.
 *
 * Entries in use are deactivated, never deleted — a segment with bookings
 * against it is part of what those bookings say. The row count makes that
 * visible before anybody tries.
 */
import { useMemo, useState } from 'react'
import Select from '../components/Select'
import { Link } from 'react-router-dom'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ArrowLeft, Plus, Search, Share2, Tag, Pencil, Trash2, X, Loader2,
  AlertTriangle, EyeOff, Check,
} from 'lucide-react'
import {
  listProperties,
  listBookingAttributes,
  createBookingAttribute,
  updateBookingAttribute,
  deleteBookingAttribute,
  type BookingAttribute,
  type BookingAttributeKind,
} from '../api'
import { useActivePropertyId } from '../hooks/useProperty'

//: The one vocabulary this screen still owns. Kept in the shape the rest of
//: the file expects, rather than inlined, so reintroducing a second list later
//: is a matter of adding an entry.
const TABS: { kind: BookingAttributeKind; label: string; hint: string }[] = [
  {
    kind: 'market_segment',
    label: 'Market Segment',
    hint: 'What kind of business it is — Corporate, Leisure, Group, Crew.',
  },
]

const inputCls = 'w-full rounded-lg border border-slate-200 px-3 py-2.5 '
  + 'text-sm outline-none focus:border-brand'

function extractError(e: unknown): string {
  const d = (e as { response?: { data?: { detail?: unknown } } })
    ?.response?.data?.detail
  if (typeof d === 'string') return d
  if (Array.isArray(d) && d.length) return String((d[0] as { msg?: string })?.msg ?? d[0])
  return (e as Error)?.message ?? 'Something went wrong.'
}

export default function BookingAttributes() {
  const qc = useQueryClient()
  const propertyId = useActivePropertyId()
  const { data: properties } = useQuery({
    queryKey: ['properties'], queryFn: listProperties,
  })
  const organizationId =
    properties?.find((p) => p.id === propertyId)?.organization_id
    ?? properties?.[0]?.organization_id ?? ''

  const [kind, setKind] = useState<BookingAttributeKind>('market_segment')
  const [q, setQ] = useState('')
  const [showInactive, setShowInactive] = useState(true)
  const [editing, setEditing] = useState<BookingAttribute | 'new' | null>(null)
  const [error, setError] = useState('')

  const { data, isLoading } = useQuery({
    queryKey: ['booking-attributes', organizationId, kind, q],
    queryFn: () => listBookingAttributes(organizationId,
      { kind, q: q.trim() || undefined }),
    enabled: organizationId !== '',
    placeholderData: keepPreviousData,
  })

  const rows = useMemo(
    () => (data?.rows ?? []).filter((r) => showInactive || r.status === 'active'),
    [data, showInactive],
  )
  const tab = TABS.find((t) => t.kind === kind)!
  const canConfigure = data?.can_configure !== false

  const remove = useMutation({
    mutationFn: (row: BookingAttribute) =>
      deleteBookingAttribute(row.id, organizationId),
    onSuccess: () => {
      setError('')
      qc.invalidateQueries({ queryKey: ['booking-attributes'] })
    },
    onError: (e) => setError(extractError(e)),
  })

  return (
    <div className="space-y-4">
      <Link to="/reservations/list"
        className="inline-flex items-center gap-1.5 text-sm text-slate-500 hover:text-brand">
        <ArrowLeft size={15} /> Back to Reservations
      </Link>
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        {/* The vocabulary's one-line hint rides as a tooltip rather than a
            paragraph that cost a row of entries. */}
        <h1 title={tab.hint} className="flex items-center gap-2 text-display text-ink">
          <Tag size={26} className="text-brand" /> Market Segments
        </h1>
        <div className="flex flex-wrap items-center gap-2">
          {canConfigure && (
            <button onClick={() => { setEditing('new'); setError('') }}
              className="flex items-center gap-1.5 whitespace-nowrap rounded-lg bg-brand px-3 py-2 text-sm font-medium text-white hover:bg-brand-dark disabled:opacity-40">
              <Plus size={15} /> Add {tab.label}
            </button>
          )}
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {/* No tab row while there is one vocabulary: a switch with a single
            position is a control that does nothing. It returns by itself if a
            second entry is ever added to TABS. */}
        {TABS.length > 1 && (
          <span className="inline-flex shrink-0 items-center gap-0.5 rounded-lg border border-slate-200 bg-white p-0.5">
            {TABS.map((t) => (
              <button key={t.kind} onClick={() => { setKind(t.kind); setError('') }}
                aria-pressed={t.kind === kind}
                className={`flex items-center gap-1.5 whitespace-nowrap rounded-md px-3 py-1.5 text-sm ${
                  t.kind === kind ? 'bg-brand font-semibold text-white'
                    : 'font-medium text-slate-500 hover:bg-slate-50 hover:text-slate-700'}`}>
                <Share2 size={14} /> {t.label}
              </button>
            ))}
          </span>
        )}
        <label className="flex shrink-0 items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-600">
          <input type="checkbox" checked={showInactive}
            onChange={(e) => setShowInactive(e.target.checked)} />
          Show inactive
        </label>
        <div className="flex min-w-[240px] flex-1 items-center justify-end gap-2">
          <div className="relative min-w-0 max-w-sm flex-1">
            <Search size={16}
              className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <input value={q} onChange={(e) => setQ(e.target.value)}
              placeholder={`Search ${tab.label.toLowerCase()}s`}
              className="w-full rounded-lg border border-slate-200 bg-slate-50 py-2 pl-9 pr-3 text-sm outline-none focus:border-brand" />
          </div>
        </div>
      </div>

      {error && (
        <div className="flex items-start gap-2 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-600">
          <AlertTriangle size={16} className="mt-0.5 shrink-0" />
          <span>{error}</span>
          <button onClick={() => setError('')} className="ml-auto shrink-0">
            <X size={15} />
          </button>
        </div>
      )}

      <div className="overflow-hidden rounded-2xl border border-slate-100 bg-white shadow-sm">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-100 bg-slate-50 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">
              <th className="px-4 py-3">Code</th>
              <th className="px-4 py-3">Name</th>
              <th className="px-4 py-3">Notes</th>
              <th className="px-4 py-3 text-center">Order</th>
              <th className="px-4 py-3 text-right">Bookings</th>
              <th className="px-4 py-3">Status</th>
              <th className="px-4 py-3" />
            </tr>
          </thead>
          <tbody>
            {isLoading && (
              <tr><td colSpan={7} className="px-4 py-10 text-center text-slate-400">
                <Loader2 size={18} className="mx-auto animate-spin" />
              </td></tr>
            )}
            {!isLoading && rows.length === 0 && (
              <tr><td colSpan={7} className="px-4 py-10 text-center text-slate-400">
                {q
                  ? `No ${tab.label.toLowerCase()} matches “${q}”.`
                  : `No ${tab.label.toLowerCase()} yet. Add the ones this `
                    + `property actually books through — only you know them.`}
              </td></tr>
            )}
            {rows.map((r) => (
              <tr key={r.id}
                className={`border-b border-slate-50 last:border-0 hover:bg-slate-50/60 ${
                  r.status === 'inactive' ? 'text-slate-400' : ''}`}>
                <td className="px-4 py-3 font-mono text-xs">{r.code}</td>
                <td className="px-4 py-3 font-medium">{r.name}</td>
                <td className="px-4 py-3 text-slate-500">{r.notes || '—'}</td>
                <td className="px-4 py-3 text-center text-slate-500">{r.sort_order}</td>
                <td className="px-4 py-3 text-right tabular-nums">{r.bookings}</td>
                <td className="px-4 py-3">
                  {r.status === 'active' ? (
                    <span className="inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2 py-0.5 text-xs font-medium text-emerald-600">
                      <Check size={12} /> Active
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1 rounded-full bg-slate-75 px-2 py-0.5 text-xs font-medium text-slate-500">
                      <EyeOff size={12} /> Inactive
                    </span>
                  )}
                </td>
                <td className="px-4 py-3 text-right">
                  {canConfigure && (
                    <span className="inline-flex gap-1">
                      <button title="Edit" onClick={() => { setEditing(r); setError('') }}
                        className="rounded-md p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-600">
                        <Pencil size={15} />
                      </button>
                      {/* Deleting an entry in use is refused by the API with
                          the count and the reason; the button stays enabled
                          so the explanation is what the user gets. */}
                      <button title={r.bookings
                        ? `Used by ${r.bookings} booking(s) — deactivate instead`
                        : 'Delete'}
                        onClick={() => remove.mutate(r)}
                        className="rounded-md p-1.5 text-slate-400 hover:bg-red-50 hover:text-red-500">
                        <Trash2 size={15} />
                      </button>
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {editing && (
        <EditModal kind={kind} kindLabel={tab.label} organizationId={organizationId}
          row={editing === 'new' ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null)
            qc.invalidateQueries({ queryKey: ['booking-attributes'] })
          }} />
      )}
    </div>
  )
}

function EditModal({ kind, kindLabel, organizationId, row, onClose, onSaved }: {
  kind: BookingAttributeKind
  kindLabel: string
  organizationId: string
  row: BookingAttribute | null
  onClose: () => void
  onSaved: () => void
}) {
  const [name, setName] = useState(row?.name ?? '')
  const [code, setCode] = useState(row?.code ?? '')
  const [notes, setNotes] = useState(row?.notes ?? '')
  const [sortOrder, setSortOrder] = useState(row?.sort_order ?? 0)
  const [status, setStatus] = useState(row?.status ?? 'active')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  async function save() {
    setErr(''); setBusy(true)
    try {
      const body = {
        organization_id: organizationId, kind, name: name.trim(),
        code: code.trim() || null, status, sort_order: sortOrder,
        notes: notes.trim() || null,
      }
      if (row) await updateBookingAttribute(row.id, body)
      else await createBookingAttribute(body)
      onSaved()
    } catch (e) {
      setErr(extractError(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4"
      onClick={onClose}>
      <div className="w-full max-w-lg rounded-2xl bg-white p-6 shadow-xl"
        onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start justify-between">
          <h2 className="text-lg font-semibold text-ink">
            {row ? `Edit ${kindLabel}` : `Add ${kindLabel}`}
          </h2>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600">
            <X size={18} />
          </button>
        </div>

        {row && row.bookings > 0 && (
          <p className="mt-3 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-700">
            {row.bookings} booking(s) are classified by this entry. Renaming it
            changes what they appear to say; setting it inactive takes it out
            of the pickers and leaves them alone.
          </p>
        )}

        <div className="mt-4 space-y-4">
          <label className="block">
            <span className="mb-1 block text-sm font-medium text-slate-600">
              Name <span className="text-red-400">*</span>
            </span>
            <input value={name} onChange={(e) => setName(e.target.value)}
              className={inputCls}
              placeholder="e.g. Corporate" />
          </label>
          <div className="grid grid-cols-2 gap-4">
            <label className="block">
              <span className="mb-1 block text-sm font-medium text-slate-600">Code</span>
              <input value={code} onChange={(e) => setCode(e.target.value)}
                className={inputCls} placeholder="From the name" />
              <span className="mt-1 block text-xs text-slate-400">
                What reports group by. Left blank, it is taken from the name.
              </span>
            </label>
            <label className="block">
              <span className="mb-1 block text-sm font-medium text-slate-600">
                Sort Order
              </span>
              <input type="number" value={sortOrder}
                onChange={(e) => setSortOrder(Number(e.target.value))}
                className={inputCls} />
              <span className="mt-1 block text-xs text-slate-400">
                Lower comes first in the picker. Ties fall back to name.
              </span>
            </label>
          </div>
          <label className="block">
            <span className="mb-1 block text-sm font-medium text-slate-600">Notes</span>
            <input value={notes} onChange={(e) => setNotes(e.target.value)}
              className={inputCls} placeholder="Optional" />
          </label>
          <label className="block">
            <span className="mb-1 block text-sm font-medium text-slate-600">Status</span>
            <Select value={status} className={inputCls}
              onChange={(e) => setStatus(e.target.value as 'active' | 'inactive')}>
              <option value="active">Active — offered when taking a booking</option>
              <option value="inactive">Inactive — hidden, history kept</option>
            </Select>
          </label>
        </div>

        {err && (
          <p className="mt-3 flex items-start gap-1.5 text-sm text-red-500">
            <AlertTriangle size={15} className="mt-0.5 shrink-0" /> {err}
          </p>
        )}

        <div className="mt-5 flex justify-end gap-2">
          <button onClick={onClose}
            className="rounded-xl border border-slate-200 px-4 py-2.5 text-sm font-medium text-slate-600 hover:bg-slate-50">
            Cancel
          </button>
          <button onClick={save} disabled={busy || name.trim() === ''}
            className="flex items-center gap-2 rounded-xl bg-brand px-5 py-2.5 text-sm font-medium text-white hover:bg-brand/90 disabled:opacity-50">
            {busy && <Loader2 size={15} className="animate-spin" />}
            {row ? 'Save Changes' : `Add ${kindLabel}`}
          </button>
        </div>
      </div>
    </div>
  )
}
