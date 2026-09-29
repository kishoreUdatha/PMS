import { useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useMutation } from '@tanstack/react-query'
import {
  AlertTriangle, ArrowLeft, Check, CheckCircle2, Download, FileSpreadsheet,
  Info, Loader2, UploadCloud, X,
} from 'lucide-react'
import { WizardFrame, ContinueButton } from './Onboarding'
import { useProperty } from '../hooks/useProperty'
import {
  checkBookingImport, commitBookingImport, downloadImportTemplate,
  type ImportCheck, type ImportResult,
} from '../api'
import { errorText } from '../lib/forms'

/**
 * Onboarding step 8 — the bookings a property already has.
 *
 * The file is checked in full before anything is written. A hundred-row
 * spreadsheet always has a few rows naming a room that does not exist or a
 * departure before its arrival, and discovering that halfway through — with
 * fifty bookings already created — is the worst possible moment to find out.
 */

const money = (v: string | number, currency: string) =>
  new Intl.NumberFormat('en-IN', {
    style: 'currency', currency: currency || 'INR', maximumFractionDigits: 0,
  }).format(Number(v))

/** Where a row's fix lives, by the field the server blamed. */
const FIX_LINK: Record<string, { label: string; to: string }> = {
  room_number: { label: 'Map room', to: '/rooms' },
  room_type: { label: 'Add room type', to: '/onboarding/rooms' },
  arrival: { label: 'Edit dates', to: '#' },
  departure: { label: 'Edit dates', to: '#' },
  guest_name: { label: 'Edit file', to: '#' },
  total_amount: { label: 'Edit file', to: '#' },
  advance_paid: { label: 'Edit file', to: '#' },
}

export function OnboardingImport() {
  const navigate = useNavigate()
  const propertyId = localStorage.getItem('property_id') ?? ''
  const { property } = useProperty()
  const currency = property?.currency ?? 'INR'

  const picker = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [dragging, setDragging] = useState(false)
  const [validOnly, setValidOnly] = useState(false)
  const [err, setErr] = useState('')

  const check = useMutation({
    mutationFn: (f: File) => checkBookingImport(propertyId, f),
    onError: (e) => setErr(
      errorText(e, 'That file could not be read.')),
  })
  const result: ImportCheck | undefined = check.data

  const commit = useMutation({
    mutationFn: () => commitBookingImport(propertyId, file!, validOnly),
    onError: (e) => setErr(
      errorText(e, 'Those bookings could not be imported.')),
  })
  const done: ImportResult | undefined = commit.data

  const take = (f: File | undefined) => {
    if (!f) return
    setErr('')
    check.reset()
    commit.reset()
    setFile(f)
    check.mutate(f)
  }

  const clear = () => {
    setFile(null); check.reset(); commit.reset(); setErr('')
  }

  const stage = result ? 3 : check.isPending ? 2 : file ? 2 : 1
  const importable = result
    ? (validOnly ? result.valid_rows : (result.issues.length === 0 ? result.total_rows : 0))
    : 0

  return (
    <WizardFrame step="import" stepNo={8}
      title="Bring your existing bookings"
      blurb="Import reservations, in-house guests and opening balances."
      footer={<>
        <span className="flex gap-2">
          <button onClick={() => navigate('/onboarding/team')}
            className="flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-medium text-slate-600 hover:bg-slate-50">
            <ArrowLeft size={15} /> Back
          </button>
          <button onClick={() => navigate('/onboarding/connections')}
            className="rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-medium text-slate-600 hover:bg-slate-50">
            Skip for now
          </button>
        </span>
        <ContinueButton step="import"
          onClick={() => navigate('/onboarding/connections')} />
      </>}>

      <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-slate-100 bg-white p-5">
        <p className="flex items-start gap-3">
          <FileSpreadsheet size={22} className="mt-0.5 shrink-0 text-brand" />
          <span>
            <span className="block font-semibold text-slate-800">
              Download our template
            </span>
            <span className="block text-sm text-slate-500">
              The exact columns the importer reads, with one example row.
            </span>
          </span>
        </p>
        <button
          onClick={() => downloadImportTemplate(propertyId)
            .catch(() => setErr('The template could not be downloaded.'))}
          className="flex items-center gap-2 rounded-xl border border-slate-200 px-4 py-2.5 text-sm font-semibold text-slate-700 hover:bg-slate-50">
          <Download size={15} /> Download template
        </button>
      </div>

      <div
        onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault(); setDragging(false)
          take(e.dataTransfer.files?.[0])
        }}
        className={`mt-4 flex flex-wrap items-center justify-center gap-4 rounded-2xl border-2 border-dashed p-8 ${
          dragging ? 'border-brand bg-brand/5' : 'border-slate-200 bg-white'}`}>
        <UploadCloud size={26} className="text-brand" />
        <span>
          <span className="block font-semibold text-slate-800">
            Drop your CSV here
          </span>
          <button onClick={() => picker.current?.click()}
            className="text-sm font-medium text-brand hover:underline">
            or click to browse files
          </button>
        </span>
        <button onClick={() => picker.current?.click()}
          className="rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-semibold text-slate-700 hover:bg-slate-50">
          Browse files
        </button>
        <input ref={picker} type="file" hidden accept=".csv,text/csv"
          onChange={(e) => { take(e.target.files?.[0]); e.target.value = '' }} />
      </div>

      {file && (
        <div className="mt-3 inline-flex items-center gap-3 rounded-xl border border-slate-200 bg-white px-3 py-2">
          <FileSpreadsheet size={18} className="text-emerald-600" />
          <span>
            <span className="block text-sm font-medium text-slate-700">
              {file.name}
            </span>
            <span className="block text-xs text-slate-400">
              {(file.size / 1024).toFixed(0)} KB
            </span>
          </span>
          <button onClick={clear} aria-label="Remove file"
            className="text-slate-400 hover:text-red-600">
            <X size={16} />
          </button>
        </div>
      )}

      {err && (
        <p className="mt-3 flex items-start gap-2 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700">
          <AlertTriangle size={16} className="mt-0.5 shrink-0" /> {err}
        </p>
      )}

      {file && !err && (
        <div className="mt-4 flex flex-wrap items-center gap-3 rounded-2xl border border-slate-100 bg-white px-5 py-4">
          {([
            ['Upload complete', result ? `${result.total_rows} rows detected` : 'Reading…'],
            ['Map columns', result
              ? (result.ignored_columns.length > 0
                  ? `${result.ignored_columns.length} column(s) ignored`
                  : 'All required fields mapped')
              : '—'],
            ['Review', result
              ? (result.issues.length > 0
                  ? `${result.issues.length} need review`
                  : 'No issues found')
              : '—'],
          ] as const).map(([label, sub], i) => {
            const n = i + 1
            const done = stage > n || (stage === 3 && n === 3 && result?.issues.length === 0)
            return (
              <span key={label} className="flex flex-1 items-center gap-3">
                <span className={`grid h-8 w-8 shrink-0 place-items-center rounded-full text-sm font-semibold ${
                  done ? 'bg-emerald-100 text-emerald-700'
                    : stage === n ? 'bg-brand text-white'
                    : 'bg-slate-75 text-slate-400'}`}>
                  {done ? <Check size={15} /> : n}
                </span>
                <span>
                  <span className="block text-sm font-semibold text-slate-800">
                    {label}
                  </span>
                  <span className="block text-xs text-slate-500">{sub}</span>
                </span>
                {i < 2 && <span className="hidden h-px flex-1 bg-slate-200 sm:block" />}
              </span>
            )
          })}
          {check.isPending && <Loader2 size={16} className="animate-spin text-slate-400" />}
        </div>
      )}

      {result && (
        <div className="mt-4 grid gap-4 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
          <div className="overflow-hidden rounded-2xl border border-slate-100 bg-white">
            <div className="flex flex-wrap items-center justify-between gap-3 px-5 py-4">
              <h2 className="text-lg font-semibold text-ink">
                {result.issues.length > 0
                  ? `Rows that need your attention (${result.issues.length})`
                  : 'Every row checks out'}
              </h2>
              <span className="flex items-center gap-2 text-sm">
                <span className="text-slate-500">
                  {result.total_rows} rows · {result.valid_rows} valid
                </span>
                {result.issues.length > 0 && (
                  <span className="rounded-full bg-red-100 px-2.5 py-0.5 text-xs font-semibold text-red-700">
                    {result.issues.length} need review
                  </span>
                )}
              </span>
            </div>

            {result.issues.length > 0 && (
              <div className="scroll-slim max-h-80 overflow-y-auto">
                <table className="w-full text-left text-sm">
                  <thead className="sticky top-0 bg-slate-50">
                    <tr className="border-y border-slate-100 text-slate-600">
                      <th className="px-5 py-2.5 font-semibold">Row</th>
                      <th className="px-3 py-2.5 font-semibold">Guest</th>
                      <th className="px-3 py-2.5 font-semibold">Issue</th>
                      <th className="px-3 py-2.5 font-semibold">Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.issues.map((row) => {
                      const fix = FIX_LINK[row.field]
                      return (
                        <tr key={`${row.row}-${row.field}`}
                          className="border-b border-slate-100 last:border-0">
                          <td className="px-5 py-2.5 tabular-nums text-slate-600">
                            {row.row}
                          </td>
                          <td className="px-3 py-2.5 text-slate-700">{row.guest}</td>
                          <td className="px-3 py-2.5 text-slate-600">{row.issue}</td>
                          <td className="px-3 py-2.5">
                            {fix && fix.to !== '#' ? (
                              <Link to={fix.to}
                                className="font-medium text-brand hover:underline">
                                {fix.label}
                              </Link>
                            ) : (
                              <span className="text-slate-400">
                                {fix?.label ?? 'Fix in the file'}
                              </span>
                            )}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )}

            {result.issues.length > 0 && (
              <label className="flex cursor-pointer items-start gap-3 border-t border-slate-100 px-5 py-4">
                <input type="checkbox" className="mt-0.5 accent-brand"
                  checked={validOnly}
                  onChange={(e) => setValidOnly(e.target.checked)} />
                <span>
                  <span className="block text-sm font-medium text-slate-700">
                    Import valid rows only
                  </span>
                  <span className="block text-xs text-slate-500">
                    Only {result.valid_rows} valid booking
                    {result.valid_rows === 1 ? '' : 's'} would be imported. You
                    can fix the {result.issues.length} issue
                    {result.issues.length === 1 ? '' : 's'} later.
                  </span>
                </span>
              </label>
            )}

            {result.ignored_columns.length > 0 && (
              <p className="flex items-start gap-2 border-t border-slate-100 px-5 py-3 text-xs text-slate-500">
                <Info size={14} className="mt-0.5 shrink-0" />
                Columns the importer does not read, left alone:{' '}
                {result.ignored_columns.join(', ')}.
              </p>
            )}
          </div>

          <div className="rounded-2xl border border-slate-100 bg-white p-5">
            <h2 className="flex items-center gap-2 text-lg font-semibold text-ink">
              What the file says <Info size={15} className="text-slate-300" />
            </h2>
            <div className="mt-4 space-y-4">
              <div>
                <p className="text-sm text-slate-500">Advance payments</p>
                <p className="text-2xl font-bold tabular-nums text-slate-800">
                  {money(result.advance_total, currency)}
                </p>
              </div>
              <div className="border-t border-slate-100 pt-4">
                <p className="text-sm text-slate-500">Outstanding balances</p>
                <p className="text-2xl font-bold tabular-nums text-slate-800">
                  {money(result.outstanding_total, currency)}
                </p>
              </div>
            </div>
            <p className="mt-4 text-xs text-slate-400">
              Totalled from the valid rows only — a row that cannot be read
              cannot be counted.
            </p>
          </div>
        </div>
      )}

      {result && !done && (
        <div className={`mt-4 flex flex-wrap items-center justify-between gap-3 rounded-2xl px-5 py-4 ${
          result.issues.length > 0 ? 'bg-amber-50' : 'bg-emerald-50'}`}>
          <p className={`flex items-start gap-2 text-sm ${
            result.issues.length > 0 ? 'text-amber-900' : 'text-emerald-800'}`}>
            {result.issues.length > 0
              ? <AlertTriangle size={16} className="mt-0.5 shrink-0" />
              : <CheckCircle2 size={16} className="mt-0.5 shrink-0" />}
            {result.issues.length > 0
              ? `Resolve ${result.issues.length} issue${result.issues.length === 1 ? '' : 's'} to import all ${result.total_rows} bookings.`
              : `${result.total_rows} booking${result.total_rows === 1 ? '' : 's'} ready to import.`}
          </p>
          <span className="text-right">
            <button
              disabled={importable === 0 || commit.isPending}
              onClick={() => { setErr(''); commit.mutate() }}
              className="flex items-center gap-2 rounded-xl bg-brand px-5 py-2.5 text-sm font-semibold text-white hover:bg-brand-dark disabled:opacity-40">
              {commit.isPending && <Loader2 size={15} className="animate-spin" />}
              Import {importable} booking{importable === 1 ? '' : 's'}
            </button>
            <span className="mt-1 block max-w-xs text-xs text-slate-500">
              {importable === 0
                ? 'Fix the rows above, or tick “Import valid rows only”.'
                : 'Creates confirmed bookings, charges and advances.'}
            </span>
          </span>
        </div>
      )}

      {done && (
        <div className={`mt-4 rounded-2xl px-5 py-4 ${
          done.created > 0 ? 'bg-emerald-50' : 'bg-amber-50'}`}>
          <p className={`flex items-center gap-2 text-sm font-semibold ${
            done.created > 0 ? 'text-emerald-800' : 'text-amber-900'}`}>
            {done.created > 0
              ? <CheckCircle2 size={16} /> : <AlertTriangle size={16} />}
            {done.created} booking{done.created === 1 ? '' : 's'} imported
            {done.skipped > 0 && `, ${done.skipped} skipped`}
            {done.created > 0 && ` · ${money(done.charged, currency)} charged, `
              + `${money(done.paid, currency)} already paid`}
          </p>
          <div className="scroll-slim mt-3 max-h-64 overflow-y-auto">
            <table className="w-full text-left text-sm">
              <tbody>
                {done.rows.map((r) => (
                  <tr key={`${r.row}-${r.guest}`} className="border-b border-white/70 last:border-0">
                    <td className="py-1.5 pr-3 tabular-nums text-slate-500">{r.row}</td>
                    <td className="py-1.5 pr-3 font-medium text-slate-700">{r.guest}</td>
                    <td className="py-1.5 text-slate-600">
                      {r.created
                        ? <span className="text-positive">Created · {r.number}</span>
                        : <span className="text-caution">{r.reason}</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {/* Re-importing the same file is safe, so say so rather than
              leaving the operator to guess whether to risk it. */}
          <p className="mt-3 text-xs text-slate-500">
            A booking reference that is already here is skipped, so the same
            file can be uploaded again without duplicating anything.
          </p>
        </div>
      )}

      {!file && (
        <p className="mt-4 flex items-start gap-2 rounded-xl bg-sky-50 px-4 py-3 text-sm text-sky-800">
          <Info size={16} className="mt-0.5 shrink-0" />
          <span>
            <strong>CSV for now.</strong> Excel files need a reader the service
            does not carry yet — open the sheet and use{' '}
            <em>File → Save As → CSV</em>. Column headings are matched loosely,
            so <em>Check-in</em>, <em>Arrival</em> and <em>Arrival date</em> all
            work.
          </span>
        </p>
      )}

      {result && result.issues.length === 0 && result.total_rows > 0 && (
        <p className="mt-3 flex items-center gap-2 text-sm text-positive">
          <CheckCircle2 size={16} /> Every row matches a real room type and a
          sensible pair of dates.
        </p>
      )}
    </WizardFrame>
  )
}
