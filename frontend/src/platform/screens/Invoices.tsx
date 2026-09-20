import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import {
  listInvoices, voidInvoice, money, type Invoice,
} from '../billingApi'
import {
  Busy, Button, DataTable, ErrorNote, FilterBar, Metrics, Note, Page,
  Pill, Select, Td, askReason, errorText,
} from '../ui'

/** Screen 13 — subscription invoices.
 *
 *  These are invoices for the software. A hotel's own guest bills live in the
 *  tenant's PMS and are nothing to do with this screen; the empty state says
 *  so, because the two being confused is the single most expensive mistake
 *  somebody could make reading it.
 */
export default function Invoices() {
  const [rows, setRows] = useState<Invoice[]>([])
  const [status, setStatus] = useState('')
  const [busy, setBusy] = useState(true)
  const [err, setErr] = useState('')

  function load(s = status) {
    setBusy(true)
    listInvoices(s ? { status: s } : {})
      .then(setRows)
      .catch((e) => setErr(errorText(e, 'Could not load invoices.')))
      .finally(() => setBusy(false))
  }
  useEffect(() => { load('') }, [])

  async function doVoid(inv: Invoice) {
    const reason = await askReason(`Void ${inv.series}-${inv.number}?`)
    if (!reason) return
    setErr('')
    try {
      await voidInvoice(inv.id, reason)
      load()
    } catch (e) { setErr(errorText(e, 'The invoice could not be voided.')) }
  }

  const outstanding = rows
    .filter((r) => r.status === 'issued')
    .reduce((t, r) => t + Number(r.outstanding ?? 0), 0)
  const paid = rows.filter((r) => r.status === 'paid')
  const collected = paid.reduce((t, r) => t + Number(r.amount_paid ?? 0), 0)

  return (
    <Page
      eyebrow="Billing"
      crumbs={[{ label: 'Overview', to: '/platform' },
        { label: 'Invoices & payments' }]}
      title="Invoices &amp; payments"
      subtitle="Review invoices for subscriptions to your PMS software.">
      <ErrorNote>{err}</ErrorNote>

      <Metrics items={[
        { label: 'Outstanding', value: money(outstanding),
          tone: outstanding ? 'warn' : 'default', caption: 'Issued and unpaid' },
        { label: 'Collected', value: money(collected),
          caption: paid.length + ' invoice(s) settled' },
        { label: 'Issued', value: rows.filter((r) => r.status === 'issued').length,
          caption: 'Awaiting payment' },
        { label: 'Total invoices', value: rows.length, caption: 'All statuses' },
      ]} />

      <FilterBar onApply={() => load()} applying={busy}>
        <Select id="inv-status" label="Invoice status" value={status}
          onChange={setStatus} options={[
            { value: '', label: 'All statuses' },
            { value: 'draft', label: 'Draft' },
            { value: 'issued', label: 'Issued' },
            { value: 'paid', label: 'Paid' },
            { value: 'void', label: 'Void' },
            { value: 'uncollectible', label: 'Uncollectible' },
          ]} />
      </FilterBar>

      {busy ? <Busy /> : (
        <DataTable
          title="Subscription invoices"
          count={rows.length}
          head={['Invoice', 'Tenant', 'Status', 'Period', 'Total', 'Paid',
            'Outstanding', '']}
          footnote={'Showing ' + rows.length + ' of ' + rows.length + ' records'}
          empty="Nothing has been billed for the software yet.">
          {rows.map((r) => (
            <tr key={r.id} className="group hover:bg-pf-bg">
              <Td className="font-medium text-pf-navy">
                {r.series}-{r.number}
              </Td>
              <Td>
                <Link to={'/platform/tenants/' + r.organization_id}
                  className="hover:text-pf-deep hover:underline">
                  {r.organization_name}
                </Link>
              </Td>
              <Td><Pill value={r.status} /></Td>
              <Td className="text-pf-muted">
                {r.period_start ? r.period_start + ' \u2192 ' + r.period_end : '\u2014'}
              </Td>
              <Td className="tabular-nums">{money(r.total)}</Td>
              <Td className="tabular-nums text-pf-muted">{money(r.amount_paid)}</Td>
              <Td className={Number(r.outstanding) > 0
                ? 'tabular-nums text-pf-warn-text' : 'tabular-nums text-pf-muted'}>
                {money(r.outstanding)}
              </Td>
              <Td className="text-right">
                {r.status === 'issued' && (
                  <Button tone="danger" onClick={() => doVoid(r)}
                    className="opacity-0 transition group-hover:opacity-100">
                    Void
                  </Button>
                )}
              </Td>
            </tr>
          ))}
        </DataTable>
      )}

      <Note>
        This is SaaS billing. Guest bills and hotel payment collection remain
        in each tenant's own PMS and never appear on this screen.
      </Note>
    </Page>
  )
}
