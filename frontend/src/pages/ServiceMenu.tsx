import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Loader2, Pencil, Plus, Tags, Trash2, UtensilsCrossed, X } from 'lucide-react'
import { Link } from 'react-router-dom'
import Select from '../components/Select'
import { Crumbs } from '../components/Crumbs'
import { notify } from '../components/AskDialog'
import { useActivePropertyId } from '../hooks/useProperty'
import {
  createServiceCategory, createServiceItem, deleteServiceCategory,
  listServiceCategories, listServiceDepartments, listServiceItems,
  retireServiceItem, updateServiceCategory, updateServiceItem,
  type ServiceCategory, type ServiceItem,
} from '../api'
import { errorText } from '../lib/forms'

/**
 * The priced list a property sells to guests in-house.
 *
 * Nothing here is a fixed list. Every property names its own categories --
 * "Tiffin" in Andhra, "Backwater Cruise" in Kerala, "Conference Hire" in a
 * city hotel -- and prices its own items. An earlier version shipped nine
 * categories in a check constraint, which meant anything a property actually
 * sold that was not on that list had to be filed as "Other".
 *
 * The one thing a property does not get to invent is the *department* a
 * category bills under. That decides the tax, the ledger resolves tax from a
 * known set, and a department it does not recognise would post a charge with
 * no tax on it and say nothing until a GST filing. So the category carries
 * the property's word for it and points at the ledger's word for it. Even
 * that list is fetched rather than copied here, so it cannot drift from the
 * tax engine.
 *
 * Items are retired, never deleted, and an order line snapshots the item's
 * name, price and category as they read at the moment the guest was billed.
 * Re-pricing or renaming in November must not rewrite an October bill.
 */

const money = new Intl.NumberFormat('en-IN', { minimumFractionDigits: 2 })
const field = 'w-full rounded-lg border border-slate-200 px-3 py-2 text-sm outline-none focus:border-brand'
const lbl = 'mb-1 block text-xs font-medium text-slate-500'

export default function ServiceMenu() {
  const propertyId = useActivePropertyId()
  const qc = useQueryClient()
  const [showRetired, setShowRetired] = useState(false)
  const [editing, setEditing] = useState<ServiceItem | 'new' | null>(null)
  const [managingCategories, setManagingCategories] = useState(false)

  const catsQ = useQuery({
    queryKey: ['service-categories', propertyId],
    queryFn: () => listServiceCategories(propertyId),
    enabled: propertyId !== '',
  })
  const itemsQ = useQuery({
    queryKey: ['service-items', propertyId, showRetired],
    queryFn: () => listServiceItems(propertyId, showRetired),
    enabled: propertyId !== '',
  })
  const categories = catsQ.data ?? []
  const items = itemsQ.data ?? []

  const retire = useMutation({
    mutationFn: (id: string) => retireServiceItem(id, propertyId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['service-items'] })
      void qc.invalidateQueries({ queryKey: ['service-categories'] })
      notify('Taken off the menu. Bills that already used it are unchanged.')
    },
  })

  return (
    <div className="space-y-4">
      <div className="space-y-1">
        <Crumbs title="Menu & Price List"
          trail={[{ label: 'Guest Services', to: '/services' },
            { label: 'Menu & Price List' }]} />
        <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
          <h1 className="flex items-center gap-2 text-display text-ink">
            <UtensilsCrossed size={26} className="text-brand" /> Menu &amp; Price List
          </h1>
          <div className="flex flex-wrap items-center gap-2">
            <Link to="/services"
              className="flex items-center gap-1.5 whitespace-nowrap rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-600 hover:bg-slate-50">
              Take an order →
            </Link>
            <button onClick={() => setManagingCategories(true)}
              className="flex items-center gap-1.5 whitespace-nowrap rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-600 hover:bg-slate-50">
              <Tags size={15} /> Categories
              <span className="rounded-full bg-slate-75 px-1.5 text-xs tabular-nums text-slate-500">
                {categories.length}
              </span>
            </button>
            <button onClick={() => setEditing('new')}
              disabled={categories.length === 0}
              title={categories.length === 0
                ? 'Add a category first — an item has to belong to one.'
                : undefined}
              className="flex items-center gap-1.5 whitespace-nowrap rounded-lg bg-brand px-3 py-2 text-sm font-medium text-white hover:bg-brand-dark disabled:cursor-not-allowed disabled:opacity-40">
              <Plus size={15} /> Add Item
            </button>
          </div>
        </div>
      </div>

      {catsQ.isSuccess && categories.length === 0 && (
        <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3">
          <p className="text-sm font-semibold text-caution">
            Start by naming what you sell.
          </p>
          <p className="mt-0.5 text-sm text-slate-600">
            Categories are how your menu is grouped, and every property's are
            different — food and tiffin at one, cruises and guided treks at
            another. Nothing is filled in for you.
          </p>
          <button onClick={() => setManagingCategories(true)}
            className="mt-2 rounded-lg bg-brand px-3 py-1.5 text-sm font-semibold text-white hover:bg-brand-dark">
            Add your first category
          </button>
        </div>
      )}

      {/* The only filter. "Take an order" moved up among the header actions. */}
      <div className="flex flex-wrap items-center gap-2">
        <label className="flex items-center gap-2 text-sm text-slate-600">
          <input type="checkbox" checked={showRetired}
            onChange={(e) => setShowRetired(e.target.checked)}
            className="rounded border-slate-300 text-brand focus:ring-brand" />
          Show retired items
        </label>
      </div>

      <div className="overflow-hidden rounded-2xl border border-slate-100 bg-white shadow-sm">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="border-b border-slate-100 bg-slate-50/60">
              <tr className="text-left text-xs font-semibold uppercase tracking-wide text-slate-500">
                <th className="whitespace-nowrap px-4 py-3">Item</th>
                <th className="whitespace-nowrap px-4 py-3">Code</th>
                <th className="whitespace-nowrap px-4 py-3">Category</th>
                <th className="whitespace-nowrap px-4 py-3 text-right">Price</th>
                <th className="whitespace-nowrap px-4 py-3">Status</th>
                <th className="whitespace-nowrap px-4 py-3 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-50">
              {itemsQ.isLoading && (
                <tr><td colSpan={6} className="px-4 py-10 text-center">
                  <Loader2 className="mx-auto animate-spin text-slate-400" />
                </td></tr>
              )}
              {itemsQ.isSuccess && items.length === 0 && (
                <tr><td colSpan={6} className="px-4 py-10 text-center text-slate-400">
                  {categories.length === 0
                    ? 'Add a category, then the things you sell.'
                    : 'Nothing on the menu yet. Add the first thing this property sells.'}
                </td></tr>
              )}
              {items.map((it) => (
                <tr key={it.id} className="hover:bg-slate-50/60">
                  <td className="px-4 py-3 font-medium text-slate-800">{it.name}</td>
                  <td className="px-4 py-3 font-mono text-xs text-slate-500">{it.code}</td>
                  <td className="px-4 py-3 text-slate-600">{it.category_label}</td>
                  <td className="px-4 py-3 text-right tabular-nums text-slate-800">
                    ₹{money.format(Number(it.price))}
                  </td>
                  <td className="px-4 py-3">
                    <span className={`rounded-full px-2 py-0.5 text-xs font-semibold ${
                      it.is_active
                        ? 'bg-emerald-50 text-emerald-700'
                        : 'bg-slate-75 text-slate-500'}`}>
                      {it.is_active ? 'On the menu' : 'Retired'}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center justify-end gap-1">
                      <button onClick={() => setEditing(it)} aria-label={`Edit ${it.name}`}
                        className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-600">
                        <Pencil size={15} />
                      </button>
                      {it.is_active && (
                        <button onClick={() => retire.mutate(it.id)}
                          aria-label={`Retire ${it.name}`}
                          className="rounded-lg p-1.5 text-slate-400 hover:bg-red-50 hover:text-red-500">
                          <X size={15} />
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {editing && (
        <ItemModal propertyId={propertyId} categories={categories}
          item={editing === 'new' ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null)
            void qc.invalidateQueries({ queryKey: ['service-items'] })
            void qc.invalidateQueries({ queryKey: ['service-categories'] })
          }} />
      )}

      {managingCategories && (
        <CategoryModal propertyId={propertyId} categories={categories}
          onClose={() => setManagingCategories(false)} />
      )}
    </div>
  )
}

/* ------------------------------------------------------------- modals --- */
function Shell({ title, subtitle, onClose, wide, children }: {
  title: string; subtitle?: string; onClose: () => void
  wide?: boolean; children: React.ReactNode
}) {
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-slate-900/50 p-4"
      role="dialog" aria-modal="true" onClick={onClose}>
      <div className={`w-full ${wide ? 'max-w-xl' : 'max-w-md'} max-h-[90vh] overflow-y-auto rounded-2xl bg-white shadow-xl`}
        onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start justify-between border-b border-slate-100 px-4 py-3">
          <div>
            <h2 className="text-sm font-semibold text-ink">{title}</h2>
            {subtitle && <p className="mt-0.5 text-xs text-slate-500">{subtitle}</p>}
          </div>
          <button onClick={onClose} aria-label="Close"
            className="rounded-lg p-1 text-slate-400 hover:bg-slate-100">
            <X size={16} />
          </button>
        </div>
        <div className="space-y-3 p-4">{children}</div>
      </div>
    </div>
  )
}

function ItemModal({ propertyId, categories, item, onClose, onSaved }: {
  propertyId: string; categories: ServiceCategory[]
  item: ServiceItem | null; onClose: () => void; onSaved: () => void
}) {
  const [name, setName] = useState(item?.name ?? '')
  const [code, setCode] = useState(item?.code ?? '')
  const [categoryId, setCategoryId] = useState(
    item?.category_id ?? categories[0]?.id ?? '')
  const [price, setPrice] = useState(item ? String(Number(item.price)) : '')
  const [err, setErr] = useState('')

  const save = useMutation({
    mutationFn: async () => {
      const body = {
        name: name.trim(), code: code.trim().toUpperCase(),
        category_id: categoryId, price: Number(price),
      }
      if (item) return updateServiceItem(item.id, propertyId, body)
      return createServiceItem({ property_id: propertyId, ...body })
    },
    onSuccess: onSaved,
    onError: (e) => {
      setErr(errorText(e, 'That did not save.'))
    },
  })

  const valid = name.trim() && code.trim() && categoryId
    && price !== '' && Number(price) >= 0

  return (
    <Shell title={item ? `Edit ${item.name}` : 'Add an item'} onClose={onClose}>
      <div>
        <label className={lbl} htmlFor="svc-name">Name</label>
        <input id="svc-name" className={field} value={name} autoFocus
          onChange={(e) => setName(e.target.value)}
          placeholder="What the guest orders" />
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <label className={lbl} htmlFor="svc-code">Code</label>
          <input id="svc-code" className={field} value={code}
            onChange={(e) => setCode(e.target.value)}
            placeholder="Short code" />
        </div>
        <div>
          <label className={lbl} htmlFor="svc-price">Price (₹)</label>
          <input id="svc-price" className={field} value={price}
            inputMode="decimal" onChange={(e) => setPrice(e.target.value)}
            placeholder="0.00" />
        </div>
      </div>
      <div>
        <label className={lbl} htmlFor="svc-cat">Category</label>
        <Select id="svc-cat" className={field} value={categoryId}
          onChange={(e) => setCategoryId(e.target.value)}>
          {categories.map((c) => (
            <option key={c.id} value={c.id}>{c.name}</option>
          ))}
        </Select>
      </div>
      {err && <p className="text-xs text-red-600">{err}</p>}
      <button onClick={() => save.mutate()} disabled={!valid || save.isPending}
        className="flex w-full items-center justify-center gap-2 rounded-lg bg-brand px-3 py-2 text-sm font-semibold text-white hover:bg-brand-dark disabled:opacity-50">
        {save.isPending && <Loader2 size={14} className="animate-spin" />}
        {item ? 'Save changes' : 'Add to the menu'}
      </button>
    </Shell>
  )
}

function CategoryModal({ propertyId, categories, onClose }: {
  propertyId: string; categories: ServiceCategory[]; onClose: () => void
}) {
  const qc = useQueryClient()
  const [name, setName] = useState('')
  const [billsAs, setBillsAs] = useState('')
  const [err, setErr] = useState('')

  // Fetched, not copied: this list belongs to the tax engine.
  const deptQ = useQuery({
    queryKey: ['service-departments'],
    queryFn: listServiceDepartments,
  })
  const departments = deptQ.data ?? []
  const effectiveBillsAs = billsAs || departments[0]?.value || ''

  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ['service-categories'] })
    void qc.invalidateQueries({ queryKey: ['service-items'] })
  }

  const add = useMutation({
    mutationFn: () => createServiceCategory({
      property_id: propertyId, name: name.trim(),
      bills_as: effectiveBillsAs, sort_order: categories.length,
    }),
    onSuccess: () => { setName(''); setErr(''); refresh() },
    onError: (e) => {
      setErr(errorText(e, 'That did not save.'))
    },
  })

  const remove = useMutation({
    mutationFn: (id: string) => deleteServiceCategory(id, propertyId),
    onSuccess: () => { setErr(''); refresh() },
    onError: (e) => {
      setErr(errorText(e, 'That could not be removed.'))
    },
  })

  const rename = useMutation({
    mutationFn: ({ id, value }: { id: string; value: string }) =>
      updateServiceCategory(id, propertyId, { bills_as: value }),
    onSuccess: refresh,
  })

  return (
    <Shell title="Categories" wide onClose={onClose}
      subtitle="How your menu is grouped. Name them whatever you actually sell.">
      {categories.length > 0 && (
        <ul className="divide-y divide-slate-50 rounded-lg border border-slate-100">
          {categories.map((c) => (
            <li key={c.id} className="flex items-center gap-3 px-3 py-2">
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-medium text-slate-800">
                  {c.name}
                </div>
                <div className="text-xs text-slate-400">
                  {c.item_count} item{c.item_count === 1 ? '' : 's'}
                </div>
              </div>
              <Select value={c.bills_as} aria-label={`${c.name} bills as`}
                onChange={(e) => rename.mutate({ id: c.id, value: e.target.value })}
                className="rounded-lg border border-slate-200 px-2 py-1.5 text-xs text-slate-600 outline-none focus:border-brand">
                {departments.map((d) => (
                  <option key={d.value} value={d.value}>{d.label}</option>
                ))}
              </Select>
              <button onClick={() => remove.mutate(c.id)}
                aria-label={`Remove ${c.name}`}
                className="rounded-lg p-1.5 text-slate-300 hover:bg-red-50 hover:text-red-500">
                <Trash2 size={15} />
              </button>
            </li>
          ))}
        </ul>
      )}

      <div className="rounded-lg border border-dashed border-slate-200 p-3">
        <div className="grid gap-2 sm:grid-cols-[1fr,auto]">
          <div>
            <label className={lbl} htmlFor="cat-name">New category</label>
            <input id="cat-name" className={field} value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Tiffin, Backwater Cruise, Conference Hire…" />
          </div>
          <div>
            <label className={lbl} htmlFor="cat-bills">Bills as</label>
            <Select id="cat-bills" className={field} value={effectiveBillsAs}
              onChange={(e) => setBillsAs(e.target.value)}>
              {departments.map((d) => (
                <option key={d.value} value={d.value}>{d.label}</option>
              ))}
            </Select>
          </div>
        </div>
        {/* Said here rather than in a tooltip: it is the one field whose
            meaning is not obvious from its name. */}
        <p className="mt-2 text-xs text-slate-400">
          “Bills as” decides which department the charge lands in, and so which
          of your tax rules apply. The name is yours; this is the accounting
          side of it.
        </p>
        {err && <p className="mt-2 text-xs text-red-600">{err}</p>}
        <button onClick={() => add.mutate()}
          disabled={!name.trim() || !effectiveBillsAs || add.isPending}
          className="mt-2 flex items-center gap-2 rounded-lg bg-brand px-3 py-1.5 text-sm font-semibold text-white hover:bg-brand-dark disabled:opacity-50">
          {add.isPending && <Loader2 size={14} className="animate-spin" />}
          <Plus size={15} /> Add category
        </button>
      </div>
    </Shell>
  )
}
