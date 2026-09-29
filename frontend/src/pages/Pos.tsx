import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  BedDouble, ChefHat, Loader2, Minus, Plus, Printer, Receipt, Search, Settings2,
  UtensilsCrossed, X,
} from 'lucide-react'
import {
  addPosLines, addPosTable, createPosOutlet, getPosCheck, listInHouseGuests,
  listPosChecks, listPosOutlets, listServiceCategories, listServiceItems,
  openPosCheck, sendPosKot, settlePosCheck, voidPosCheck, voidPosLine,
  type PosCheck, type PosLine, type PosOutlet,
} from '../api'
import { useActivePropertyId } from '../hooks/useProperty'
import { errorText, inputCls } from '../lib/forms'
import { usePaymentMethods } from '../lib/paymentMethods'

/**
 * Restaurant & POS — tables, checks, kitchen tickets, and settling the bill.
 *
 * The menu is Guest Services' menu, so items, prices and tax are set up once
 * (under Guest Services → menu). A check stays open through the meal; each
 * round goes to the kitchen on a numbered KOT; at the end the bill is paid at
 * the table or posted to an in-house guest's room.
 *
 * Nothing here prices anything. The server takes each item's price from the
 * menu and the tax from its category, and posts both through the ledger when
 * the check is settled.
 */

const money = (v: string | number | null | undefined, cur = 'INR') =>
  new Intl.NumberFormat('en-IN', { style: 'currency', currency: cur }).format(Number(v ?? 0))

const OUTLET_KINDS = [
  { value: 'restaurant', label: 'Restaurant' }, { value: 'bar', label: 'Bar' },
  { value: 'cafe', label: 'Café' }, { value: 'room_service', label: 'Room service' },
  { value: 'other', label: 'Other' },
]

function printTicket(title: string, body: string) {
  const w = window.open('', '_blank', 'width=380,height=600')
  if (!w) return
  w.document.write(`<!doctype html><html><head><title>${title}</title>
    <style>body{font:14px/1.4 monospace;margin:16px}h1{font-size:16px;margin:0 0 8px}
    table{width:100%;border-collapse:collapse}td{padding:3px 0;vertical-align:top}
    .r{text-align:right}.muted{color:#555}hr{border:0;border-top:1px dashed #000}</style>
    </head><body>${body}<script>window.onload=()=>{window.print()}</script></body></html>`)
  w.document.close()
}

const esc = (s: string) => s.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]!))

function kotHtml(c: PosCheck, number: number, lines: PosLine[]) {
  return `<h1>KOT #${number}</h1><div class="muted">${esc(c.outlet_name)}${c.table_label ? ` · Table ${esc(c.table_label)}` : ''} · ${esc(c.number)}</div>
    <div class="muted">${new Date().toLocaleString('en-IN')}</div><hr>
    <table>${lines.map((l) => `<tr><td><b>${l.quantity} ×</b></td><td>${esc(l.item_name)}${l.note ? `<br><i>${esc(l.note)}</i>` : ''}</td></tr>`).join('')}</table>`
}

function billHtml(c: PosCheck) {
  const active = c.lines.filter((l) => l.status === 'active')
  return `<h1>${esc(c.outlet_name)}</h1><div class="muted">Bill ${esc(c.number)}${c.table_label ? ` · Table ${esc(c.table_label)}` : ''}</div>
    <div class="muted">${new Date().toLocaleString('en-IN')}</div><hr>
    <table>${active.map((l) => `<tr><td>${l.quantity} × ${esc(l.item_name)}</td><td class="r">${money(l.amount, c.currency)}</td></tr>`).join('')}</table><hr>
    <table><tr><td>Subtotal</td><td class="r">${money(c.subtotal, c.currency)}</td></tr>
    ${c.tax ? `<tr><td>Tax</td><td class="r">${money(c.tax, c.currency)}</td></tr>` : ''}
    ${c.total ? `<tr><td><b>Total</b></td><td class="r"><b>${money(c.total, c.currency)}</b></td></tr>` : '<tr><td class="muted" colspan="2">Tax is added when the bill is settled.</td></tr>'}
    </table>`
}

export default function Pos() {
  const propertyId = useActivePropertyId()
  const qc = useQueryClient()
  const [outletId, setOutletId] = useState('')
  const [checkId, setCheckId] = useState('')
  const [setup, setSetup] = useState(false)
  const [err, setErr] = useState('')

  const outletsQ = useQuery({
    queryKey: ['pos-outlets', propertyId], queryFn: () => listPosOutlets(propertyId),
    enabled: propertyId !== '',
  })
  const outlets = (outletsQ.data ?? []).filter((o) => o.is_active)
  const outlet = outlets.find((o) => o.id === outletId) ?? outlets[0]

  const checksQ = useQuery({
    queryKey: ['pos-checks', propertyId], queryFn: () => listPosChecks(propertyId, 'open'),
    enabled: propertyId !== '', refetchInterval: 15000,
  })
  const openHere = (checksQ.data ?? []).filter((c) => c.outlet_id === outlet?.id)

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['pos-checks', propertyId] })
    if (checkId) qc.invalidateQueries({ queryKey: ['pos-check', checkId] })
  }

  const open = useMutation({
    mutationFn: (tableId: string | null) => openPosCheck(propertyId, {
      outlet_id: outlet!.id, table_id: tableId }),
    onSuccess: (c) => { setErr(''); setCheckId(c.id); refresh() },
    onError: (e) => setErr(errorText(e, 'Could not open a check.')),
  })

  if (outletsQ.isLoading) return <Loader2 className="animate-spin text-slate-300" />

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="flex items-center gap-2 text-display text-ink">
          <UtensilsCrossed size={26} className="text-brand" /> Restaurant & POS
        </h1>
        <button onClick={() => setSetup(true)}
          className="flex items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-2 text-sm font-medium text-slate-600 hover:bg-slate-50">
          <Settings2 size={15} /> Outlets & tables
        </button>
      </div>

      {err && <p className="rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700">{err}</p>}

      {outlets.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-slate-200 bg-white p-10 text-center">
          <p className="font-semibold text-ink">Set up your first outlet</p>
          <p className="mt-1 text-sm text-slate-500">
            A restaurant, bar or room service, with its tables. Items and prices come from the
            Guest Services menu.
          </p>
          <button onClick={() => setSetup(true)}
            className="mt-4 rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white hover:bg-brand-dark">
            Add an outlet
          </button>
        </div>
      ) : (
        <>
          <div className="flex gap-2 overflow-x-auto">
            {outlets.map((o) => (
              <button key={o.id} onClick={() => { setOutletId(o.id); setCheckId('') }}
                className={`shrink-0 rounded-full px-4 py-2 text-sm font-semibold ${o.id === outlet?.id
                  ? 'bg-ink text-white' : 'border border-slate-200 bg-white text-slate-600 hover:border-brand'}`}>
                {o.name}
                <span className="ml-1.5 text-xs opacity-70">
                  {(checksQ.data ?? []).filter((c) => c.outlet_id === o.id).length || ''}
                </span>
              </button>
            ))}
          </div>

          <div className="grid gap-4 xl:grid-cols-[1fr_1.35fr]">
            <Floor outlet={outlet!} checks={openHere} selected={checkId}
              onPick={(id) => setCheckId(id)} onOpen={(t) => open.mutate(t)} opening={open.isPending} />
            {checkId
              ? <CheckPanel key={checkId} propertyId={propertyId} checkId={checkId}
                  onClose={() => setCheckId('')} onChanged={refresh} />
              : <div className="flex min-h-[320px] items-center justify-center rounded-2xl border border-dashed border-slate-200 bg-white p-8 text-center text-sm text-slate-400">
                  Pick a table to open or continue its check.
                </div>}
          </div>
        </>
      )}

      {setup && <SetupDialog propertyId={propertyId} outlets={outletsQ.data ?? []}
        onClose={() => setSetup(false)}
        onChanged={() => qc.invalidateQueries({ queryKey: ['pos-outlets', propertyId] })} />}
    </div>
  )
}

function Floor({ outlet, checks, selected, onPick, onOpen, opening }: {
  outlet: PosOutlet
  checks: { id: string; number: string; table_id: string | null; subtotal: string; items: number; guest_label: string | null }[]
  selected: string
  onPick: (id: string) => void
  onOpen: (tableId: string | null) => void
  opening: boolean
}) {
  const byTable = new Map(checks.filter((c) => c.table_id).map((c) => [c.table_id!, c]))
  const loose = checks.filter((c) => !c.table_id)
  return (
    <div className="rounded-2xl border border-slate-100 bg-white p-4">
      <div className="grid grid-cols-3 gap-2.5 sm:grid-cols-4">
        {outlet.tables.filter((t) => t.is_active).map((t) => {
          const c = byTable.get(t.id)
          return (
            <button key={t.id} disabled={opening}
              onClick={() => (c ? onPick(c.id) : onOpen(t.id))}
              className={`flex aspect-square flex-col items-center justify-center rounded-xl border-2 p-2 text-center transition ${
                c ? (c.id === selected ? 'border-brand bg-brand text-white' : 'border-brand/40 bg-brand-light/50 text-ink')
                  : 'border-slate-100 bg-slate-50 text-slate-500 hover:border-brand/40'}`}>
              <span className="text-lg font-bold">{t.label}</span>
              {c ? (
                <span className="mt-1 text-xs font-medium">{money(c.subtotal)}</span>
              ) : <span className="mt-1 text-[11px]">{t.seats} seats</span>}
            </button>
          )
        })}
      </div>
      <div className="mt-4 border-t border-slate-100 pt-3">
        <div className="flex items-center justify-between">
          <p className="text-sm font-semibold text-ink">Without a table</p>
          <button onClick={() => onOpen(null)} disabled={opening}
            className="flex items-center gap-1 text-sm font-semibold text-brand hover:underline">
            <Plus size={14} /> New check
          </button>
        </div>
        <ul className="mt-2 space-y-1.5">
          {loose.map((c) => (
            <li key={c.id}>
              <button onClick={() => onPick(c.id)}
                className={`flex w-full items-center justify-between rounded-lg px-3 py-2 text-sm ${
                  c.id === selected ? 'bg-brand text-white' : 'bg-slate-50 text-slate-700 hover:bg-slate-75'}`}>
                <span>{c.number}{c.guest_label ? ` · ${c.guest_label}` : ''}</span>
                <span>{money(c.subtotal)}</span>
              </button>
            </li>
          ))}
          {loose.length === 0 && <li className="text-xs text-slate-400">Takeaway, bar tabs and walk-ups go here.</li>}
        </ul>
      </div>
    </div>
  )
}

function CheckPanel({ propertyId, checkId, onClose, onChanged }: {
  propertyId: string; checkId: string; onClose: () => void; onChanged: () => void
}) {
  const qc = useQueryClient()
  const key = ['pos-check', checkId]
  const { data: c } = useQuery({ queryKey: key, queryFn: () => getPosCheck(propertyId, checkId) })
  const cats = useQuery({ queryKey: ['svc-cats', propertyId], queryFn: () => listServiceCategories(propertyId) })
  const items = useQuery({ queryKey: ['svc-items', propertyId], queryFn: () => listServiceItems(propertyId) })
  const [cat, setCat] = useState('')
  const [q, setQ] = useState('')
  const [err, setErr] = useState('')
  const [settling, setSettling] = useState(false)

  const done = (next: PosCheck) => { setErr(''); qc.setQueryData(key, next); onChanged() }
  const fail = (e: unknown) => setErr(errorText(e, 'That did not work.'))
  const add = useMutation({
    mutationFn: (item_id: string) => addPosLines(propertyId, checkId, [{ item_id, quantity: 1 }]),
    onSuccess: done, onError: fail,
  })
  const voidLine = useMutation({
    mutationFn: ({ id, reason }: { id: string; reason?: string }) => voidPosLine(propertyId, checkId, id, reason),
    onSuccess: done, onError: fail,
  })
  const kot = useMutation({
    mutationFn: () => sendPosKot(propertyId, checkId),
    onSuccess: (next) => {
      done(next)
      if (next.kot) printTicket(`KOT ${next.kot.number}`, kotHtml(next, next.kot.number, next.kot.lines))
    },
    onError: fail,
  })
  const voidCheck = useMutation({
    mutationFn: (reason?: string) => voidPosCheck(propertyId, checkId, reason),
    onSuccess: (next) => { done(next); onClose() }, onError: fail,
  })

  const menu = useMemo(() => (items.data ?? []).filter((i) => i.is_active
    && (!cat || i.category_id === cat)
    && (!q || i.name.toLowerCase().includes(q.toLowerCase()))), [items.data, cat, q])

  if (!c) return <div className="rounded-2xl border border-slate-100 bg-white p-6"><Loader2 className="animate-spin text-slate-300" /></div>
  const open = c.status === 'open'
  const active = c.lines.filter((l) => l.status === 'active')

  return (
    <div className="rounded-2xl border border-slate-100 bg-white">
      <div className="flex items-start justify-between gap-3 border-b border-slate-100 px-5 py-4">
        <div>
          <p className="text-lg font-bold text-ink">
            {c.table_label ? `Table ${c.table_label}` : 'No table'} <span className="text-slate-400">· {c.number}</span>
          </p>
          <p className="text-xs text-slate-500">
            {c.outlet_name} · opened {new Date(c.opened_at).toLocaleTimeString('en-IN', { hour: 'numeric', minute: '2-digit' })}
            {!open && ` · ${c.status === 'room' ? 'posted to room' : c.status}`}
          </p>
        </div>
        <button onClick={onClose} aria-label="Close check" className="rounded-lg p-1 text-slate-400 hover:bg-slate-50"><X size={18} /></button>
      </div>

      {err && <p className="mx-5 mt-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{err}</p>}

      <div className="grid gap-0 lg:grid-cols-2">
        {open && (
          <div className="border-b border-slate-100 p-4 lg:border-b-0 lg:border-r">
            <div className="relative">
              <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
              <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search the menu"
                className={`${inputCls} pl-9`} />
            </div>
            <div className="mt-3 flex flex-wrap gap-1.5">
              <button onClick={() => setCat('')}
                className={`rounded-full px-3 py-1 text-xs font-semibold ${!cat ? 'bg-ink text-white' : 'bg-slate-75 text-slate-600'}`}>All</button>
              {(cats.data ?? []).filter((x) => x.is_active).map((x) => (
                <button key={x.id} onClick={() => setCat(x.id)}
                  className={`rounded-full px-3 py-1 text-xs font-semibold ${cat === x.id ? 'bg-ink text-white' : 'bg-slate-75 text-slate-600'}`}>
                  {x.name}
                </button>
              ))}
            </div>
            <div className="mt-3 grid max-h-[360px] grid-cols-2 gap-2 overflow-y-auto">
              {menu.map((i) => (
                <button key={i.id} onClick={() => add.mutate(i.id)} disabled={add.isPending}
                  className="rounded-xl border border-slate-100 bg-slate-50 p-3 text-left hover:border-brand hover:bg-white">
                  <p className="text-sm font-semibold leading-tight text-ink">{i.name}</p>
                  <p className="mt-1 text-xs text-slate-500">{money(i.price, i.currency)}</p>
                </button>
              ))}
              {menu.length === 0 && (
                <p className="col-span-2 py-6 text-center text-xs text-slate-400">
                  No items. Add them under Guest Services → menu.
                </p>
              )}
            </div>
          </div>
        )}

        <div className={`flex flex-col p-4 ${open ? '' : 'lg:col-span-2'}`}>
          <ul className="flex-1 space-y-1.5">
            {c.lines.map((l) => (
              <li key={l.id} className={`flex items-start justify-between gap-2 rounded-lg px-2 py-1.5 text-sm ${l.status === 'void' ? 'text-slate-400 line-through' : 'text-slate-700'}`}>
                <span>
                  {l.quantity} × {l.item_name}
                  {l.kot_number
                    ? <span className="ml-1.5 rounded bg-slate-75 px-1.5 text-[10px] font-semibold text-slate-500 no-underline">KOT {l.kot_number}</span>
                    : l.status === 'active' && <span className="ml-1.5 rounded bg-amber-50 px-1.5 text-[10px] font-semibold text-amber-700">new</span>}
                </span>
                <span className="flex items-center gap-2">
                  {money(l.amount, c.currency)}
                  {open && l.status === 'active' && (
                    <button aria-label={`Remove ${l.item_name}`} title="Remove"
                      onClick={() => {
                        const reason = l.kot_number ? window.prompt('This has gone to the kitchen. Why is it coming off?') ?? '' : undefined
                        if (l.kot_number && !reason) return
                        voidLine.mutate({ id: l.id, reason })
                      }}
                      className="rounded p-0.5 text-slate-300 hover:bg-red-50 hover:text-red-600"><Minus size={14} /></button>
                  )}
                </span>
              </li>
            ))}
            {c.lines.length === 0 && <li className="py-8 text-center text-sm text-slate-400">Add items from the menu.</li>}
          </ul>

          <div className="mt-3 space-y-1 border-t border-slate-100 pt-3 text-sm">
            <div className="flex justify-between text-slate-600"><span>Subtotal</span><span>{money(c.subtotal, c.currency)}</span></div>
            {c.tax && Number(c.tax) > 0 && <div className="flex justify-between text-slate-600"><span>Tax</span><span>{money(c.tax, c.currency)}</span></div>}
            {c.total
              ? <div className="flex justify-between font-bold text-ink"><span>Total</span><span>{money(c.total, c.currency)}</span></div>
              : <p className="text-xs text-slate-400">Tax is worked out from each item's category when the bill is settled.</p>}
          </div>

          <div className="mt-4 flex flex-wrap gap-2">
            {open && (
              <>
                <button onClick={() => kot.mutate()} disabled={c.unsent === 0 || kot.isPending}
                  className="flex items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-40">
                  <ChefHat size={15} /> Send KOT{c.unsent ? ` (${c.unsent})` : ''}
                </button>
                <button onClick={() => setSettling(true)} disabled={active.length === 0}
                  className="flex items-center gap-1.5 rounded-lg bg-brand px-3 py-2 text-sm font-semibold text-white hover:bg-brand-dark disabled:opacity-40">
                  <Receipt size={15} /> Settle
                </button>
              </>
            )}
            <button onClick={() => printTicket(`Bill ${c.number}`, billHtml(c))} disabled={active.length === 0}
              className="flex items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-40">
              <Printer size={15} /> Print bill
            </button>
            {open && (
              <button onClick={() => {
                const sent = c.lines.some((l) => l.status === 'active' && l.kot_number)
                const reason = sent ? window.prompt('Food has gone to the kitchen. Why is this check being voided?') ?? '' : undefined
                if (sent && !reason) return
                voidCheck.mutate(reason)
              }}
                className="ml-auto rounded-lg px-3 py-2 text-sm font-medium text-slate-400 hover:text-red-600">
                Void check
              </button>
            )}
          </div>
        </div>
      </div>

      {settling && <SettleDialog propertyId={propertyId} check={c}
        onClose={() => setSettling(false)}
        onDone={(next) => { setSettling(false); done(next); printTicket(`Bill ${next.number}`, billHtml(next)) }} />}
    </div>
  )
}

function SettleDialog({ propertyId, check, onClose, onDone }: {
  propertyId: string; check: PosCheck; onClose: () => void; onDone: (c: PosCheck) => void
}) {
  const { methods } = usePaymentMethods(propertyId)
  const [mode, setMode] = useState<'pay' | 'room'>('pay')
  const [method, setMethod] = useState('')
  const [reference, setReference] = useState('')
  const [folio, setFolio] = useState('')
  const [err, setErr] = useState('')
  const guests = useQuery({
    queryKey: ['in-house', propertyId], queryFn: () => listInHouseGuests(propertyId),
    enabled: mode === 'room',
  })
  const chosen = methods.find((m) => m.value === method)
  const settle = useMutation({
    mutationFn: () => settlePosCheck(propertyId, check.id, mode === 'pay'
      ? { mode, method, reference: reference.trim() || null }
      : { mode, folio_id: folio }),
    onSuccess: onDone,
    onError: (e) => setErr(errorText(e, 'The bill was not settled.')),
  })
  const ready = mode === 'pay' ? !!method && (!chosen?.needs_reference || reference.trim()) : !!folio

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div role="dialog" aria-modal="true" className="w-full max-w-md rounded-2xl bg-white p-6 shadow-xl">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold text-ink">Settle {check.number}</h2>
          <button onClick={onClose} aria-label="Close" className="rounded-lg p-1 text-slate-400 hover:bg-slate-50"><X size={18} /></button>
        </div>
        <p className="mt-1 text-sm text-slate-500">
          {money(check.subtotal, check.currency)} before tax. Tax is added from each item's category.
        </p>
        <div className="mt-4 grid grid-cols-2 gap-2">
          {([['pay', 'Pay now', Receipt], ['room', 'Post to room', BedDouble]] as const).map(([v, label, Icon]) => (
            <button key={v} onClick={() => setMode(v)}
              className={`flex items-center justify-center gap-2 rounded-xl border-2 px-3 py-3 text-sm font-semibold ${mode === v ? 'border-brand bg-brand-light/40 text-brand-deep' : 'border-slate-100 text-slate-600'}`}>
              <Icon size={16} /> {label}
            </button>
          ))}
        </div>

        {mode === 'pay' ? (
          <div className="mt-4">
            <div className="grid grid-cols-2 gap-2">
              {methods.map((m) => (
                <button key={m.value} onClick={() => setMethod(m.value)}
                  className={`flex items-center gap-2 rounded-lg border px-3 py-2 text-sm ${method === m.value ? 'border-brand bg-brand-light/40 font-semibold text-ink' : 'border-slate-200 text-slate-600'}`}>
                  {m.icon} {m.label}
                </button>
              ))}
            </div>
            {chosen?.needs_reference && (
              <input value={reference} onChange={(e) => setReference(e.target.value)}
                placeholder="Payment reference" className={`${inputCls} mt-3`} />
            )}
          </div>
        ) : (
          <div className="mt-4">
            <select value={folio} onChange={(e) => setFolio(e.target.value)} className={inputCls}>
              <option value="">Choose an in-house guest</option>
              {(guests.data ?? []).filter((g) => g.folio_id).map((g) => (
                <option key={g.reservation_id} value={g.folio_id!}>
                  {g.room_code ? `Room ${g.room_code} · ` : ''}{g.guest_name ?? 'Guest'} · {g.reservation_number}
                </option>
              ))}
            </select>
            <p className="mt-2 text-xs text-slate-500">The guest pays it at check-out with the rest of the stay.</p>
          </div>
        )}

        {err && <p className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{err}</p>}
        <button onClick={() => settle.mutate()} disabled={!ready || settle.isPending}
          className="mt-5 flex w-full items-center justify-center gap-2 rounded-xl bg-brand px-4 py-3 font-semibold text-white hover:bg-brand-dark disabled:opacity-50">
          {settle.isPending && <Loader2 size={16} className="animate-spin" />}
          {mode === 'pay' ? 'Take payment and close' : 'Post to room and close'}
        </button>
      </div>
    </div>
  )
}

function SetupDialog({ propertyId, outlets, onClose, onChanged }: {
  propertyId: string; outlets: PosOutlet[]; onClose: () => void; onChanged: () => void
}) {
  const [name, setName] = useState('')
  const [kind, setKind] = useState('restaurant')
  const [tables, setTables] = useState('8')
  const [err, setErr] = useState('')
  const create = useMutation({
    mutationFn: () => createPosOutlet(propertyId, { name: name.trim(), kind, tables: Number(tables) || 0 }),
    onSuccess: () => { setName(''); setErr(''); onChanged() },
    onError: (e) => setErr(errorText(e, 'Could not add the outlet.')),
  })
  const table = useMutation({
    mutationFn: ({ outletId, label }: { outletId: string; label: string }) =>
      addPosTable(propertyId, outletId, { label, seats: 4 }),
    onSuccess: () => { setErr(''); onChanged() },
    onError: (e) => setErr(errorText(e, 'Could not add the table.')),
  })
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div role="dialog" aria-modal="true" className="max-h-full w-full max-w-lg overflow-y-auto rounded-2xl bg-white p-6 shadow-xl">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold text-ink">Outlets & tables</h2>
          <button onClick={onClose} aria-label="Close" className="rounded-lg p-1 text-slate-400 hover:bg-slate-50"><X size={18} /></button>
        </div>
        <ul className="mt-4 space-y-2">
          {outlets.map((o) => (
            <li key={o.id} className="rounded-xl border border-slate-100 p-3">
              <div className="flex items-center justify-between">
                <span className="font-semibold text-ink">{o.name}</span>
                <span className="text-xs text-slate-500">{OUTLET_KINDS.find((k) => k.value === o.kind)?.label} · {o.tables.length} tables</span>
              </div>
              <button onClick={() => table.mutate({ outletId: o.id, label: `T${o.tables.length + 1}` })}
                className="mt-2 text-xs font-semibold text-brand hover:underline">+ Add table T{o.tables.length + 1}</button>
            </li>
          ))}
        </ul>
        <div className="mt-5 space-y-3 rounded-xl bg-slate-50 p-4">
          <p className="text-sm font-semibold text-ink">New outlet</p>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Poolside Grill" className={inputCls} />
          <div className="grid grid-cols-2 gap-3">
            <select value={kind} onChange={(e) => setKind(e.target.value)} className={inputCls}>
              {OUTLET_KINDS.map((k) => <option key={k.value} value={k.value}>{k.label}</option>)}
            </select>
            <input type="number" min={0} max={100} value={tables} onChange={(e) => setTables(e.target.value)}
              className={inputCls} aria-label="Number of tables" />
          </div>
          <button onClick={() => create.mutate()} disabled={!name.trim() || create.isPending}
            className="rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white hover:bg-brand-dark disabled:opacity-50">
            Add outlet
          </button>
        </div>
        {err && <p className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{err}</p>}
      </div>
    </div>
  )
}
