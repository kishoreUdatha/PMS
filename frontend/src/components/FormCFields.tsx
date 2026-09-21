import { ShieldCheck } from 'lucide-react'
import { FORM_C_REQUIRED, VISA_TYPES, type FormCValues } from '../lib/formC'

/**
 * The passport, visa and arrival-in-India details a Form C needs, placed
 * with the guest's address rather than on a screen of its own.
 *
 * This is where they have to be. The 24-hour clock starts at check-in, and
 * the only moment the property reliably has the passport in its hands is
 * while the address is being typed. A separate compliance screen means
 * somebody chases the guest for a visa number the next morning — by which
 * time they are at the beach, or gone.
 *
 * Shown only for a guest recorded as a foreign national. An Indian guest
 * should never see a visa field, and a desk trained to scroll past an
 * irrelevant block will scroll past a relevant one.
 *
 * The fields are not *required* to save. A desk collects a passport in
 * stages and refusing a partial record would lose the half that was
 * collected; the register is what says a form cannot be filed yet, and it
 * names the gaps. Required ones are marked so the desk knows which they
 * will be chased for.
 */
export default function FormCFields({ values, onChange, idPrefix = 'fc' }: {
  values: FormCValues
  onChange: (key: keyof FormCValues, value: string) => void
  idPrefix?: string
}) {
  const lbl = 'mb-1 block text-xs font-medium text-slate-500'
  const input = 'w-full rounded-lg border border-slate-200 px-3 py-2 text-sm outline-none focus:border-brand'

  const F = ({ k, label, type = 'text', wide, list }: {
    k: keyof FormCValues; label: string; type?: string
    wide?: boolean; list?: string
  }) => {
    const required = (FORM_C_REQUIRED as readonly string[]).includes(k)
    const id = `${idPrefix}-${k}`
    return (
      <div className={wide ? 'sm:col-span-2' : undefined}>
        <label className={lbl} htmlFor={id}>
          {label}
          {required && <span className="ml-1 text-rose-500" title="The FRRO will not accept the form without this">*</span>}
        </label>
        <input id={id} type={type} className={input} list={list}
          value={values[k] ?? ''}
          onChange={(e) => onChange(k, e.target.value)} />
      </div>
    )
  }

  return (
    <div className="mt-4 rounded-xl border border-amber-200 bg-amber-50/40 p-4">
      <div className="mb-1 flex items-center gap-2">
        <ShieldCheck size={15} className="text-amber-700" />
        <h3 className="text-sm font-semibold text-ink">
          Form C — foreign national
        </h3>
      </div>
      <p className="mb-3 text-xs text-slate-600">
        This guest must be reported to the Bureau of Immigration within 24
        hours of check-in. Take these from the passport now — chasing a visa
        number after the guest has gone up to their room is how the deadline
        gets missed. Fields marked <span className="text-rose-500">*</span> are
        the ones the FRRO will not accept the form without.
      </p>

      <datalist id={`${idPrefix}-visa-types`}>
        {VISA_TYPES.map((t) => <option key={t} value={t} />)}
      </datalist>

      <div className="grid gap-3 sm:grid-cols-2">
        <F k="passport_number" label="Passport number" />
        <F k="passport_issue_place" label="Passport place of issue" />
        <F k="passport_issue_date" label="Passport issued" type="date" />
        <F k="passport_expiry_date" label="Passport expires" type="date" />

        <F k="visa_number" label="Visa number" />
        <F k="visa_type" label="Visa type" list={`${idPrefix}-visa-types`} />
        <F k="visa_issue_place" label="Visa place of issue" />
        <F k="visa_expiry_date" label="Visa valid until" type="date" />

        {/* Spelled out, because filling in the hotel's check-in date here is
            the commonest way a Form C is filed wrong. A guest lands in Delhi
            on the 3rd and reaches a Chirala resort on the 6th; the form asks
            about the 3rd. */}
        <F k="arrived_in_india_on"
          label="Arrived in India on (not at this hotel)" type="date" />
        <F k="arrived_in_india_at" label="Arrived in India at (airport / port)" />
        <F k="address_in_india" label="Address in India" wide />
        <F k="next_destination" label="Next destination" wide />
      </div>
    </div>
  )
}
