/** Who needs a Form C, decided in one place.
 *
 * Rule 14 of the Registration of Foreigners Rules 1992: a foreign guest's
 * arrival is reported to the Bureau of Immigration within 24 hours. An
 * Indian passport holder needs nothing.
 *
 * This mirrors `nationality_state` in `formc_routes.py` and has to keep
 * mirroring it. Two answers to "is this guest foreign" — one deciding
 * whether the desk is shown the visa fields, another deciding whether the
 * register counts a breach — is how a property ends up with a guest the
 * form was never asked for and the register never listed.
 */

/** Spellings of India seen in a free-text column filled by hand, by an
 *  importer and by a channel. */
const INDIAN = new Set(['india', 'indian', 'in', 'ind', 'bharat'])

/** Documents only an Indian citizen or resident holds. Not proof — a
 *  long-resident foreigner can hold an Aadhaar — but enough to keep a
 *  domestic guest from being shown a visa form. */
const INDIAN_IDS = new Set(['aadhaar', 'voter_id'])

export type NationalityState = 'foreign' | 'indian' | 'unknown'

export function nationalityState(
  nationality?: string | null, idType?: string | null,
): NationalityState {
  const n = (nationality ?? '').trim().toLowerCase()
  if (n) return INDIAN.has(n) ? 'indian' : 'foreign'
  if (INDIAN_IDS.has((idType ?? '').toLowerCase())) return 'indian'
  return 'unknown'
}

/** Whether to show the Form C fields. Unknown does NOT count as foreign:
 *  nationality is blank on most guest records, and opening a visa form for
 *  every walk-in would train the desk to scroll past it. */
export function needsFormC(
  nationality?: string | null, idType?: string | null,
): boolean {
  return nationalityState(nationality, idType) === 'foreign'
}

/** The fields the FRRO will not accept a form without. Kept in step with
 *  REQUIRED_FIELDS in `formc_routes.py`. */
export const FORM_C_REQUIRED = [
  'passport_number', 'visa_number', 'visa_type',
  'arrived_in_india_on', 'arrived_in_india_at',
] as const

export interface FormCValues {
  passport_number?: string | null
  passport_issue_place?: string | null
  passport_issue_date?: string | null
  passport_expiry_date?: string | null
  visa_number?: string | null
  visa_type?: string | null
  visa_issue_place?: string | null
  visa_issue_date?: string | null
  visa_expiry_date?: string | null
  arrived_in_india_on?: string | null
  arrived_in_india_at?: string | null
  address_in_india?: string | null
  next_destination?: string | null
}

/** Visa types the FRRO form offers. Free text is still allowed — the list
 *  changes by notification and a dropdown that cannot be overridden would
 *  block a check-in over a category nobody here had heard of. */
export const VISA_TYPES = [
  'e-Tourist', 'Tourist', 'Business', 'e-Business', 'Employment',
  'Student', 'Medical', 'e-Medical', 'Conference', 'Journalist',
  'Research', 'Transit', 'OCI', 'Other',
]
