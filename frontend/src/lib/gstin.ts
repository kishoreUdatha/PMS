/**
 * The GSTIN rule, as the field sees it.
 *
 * The server is the authority — `chirala_common/gstin.py` holds the same rule
 * and every writer goes through it. This exists so the answer arrives while
 * you are typing rather than on save, and the two are deliberately kept
 * identical, message for message, so the screen never contradicts the refusal.
 */

const ALPHABET = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'
const SHAPE = /^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]$/

/** GST state codes, as issued. 25 merged into 26; 28 became 37. */
export const GST_STATE_CODES: Record<string, string> = {
  '01': 'Jammu and Kashmir', '02': 'Himachal Pradesh', '03': 'Punjab',
  '04': 'Chandigarh', '05': 'Uttarakhand', '06': 'Haryana', '07': 'Delhi',
  '08': 'Rajasthan', '09': 'Uttar Pradesh', '10': 'Bihar', '11': 'Sikkim',
  '12': 'Arunachal Pradesh', '13': 'Nagaland', '14': 'Manipur',
  '15': 'Mizoram', '16': 'Tripura', '17': 'Meghalaya', '18': 'Assam',
  '19': 'West Bengal', '20': 'Jharkhand', '21': 'Odisha',
  '22': 'Chhattisgarh', '23': 'Madhya Pradesh', '24': 'Gujarat',
  '26': 'Dadra and Nagar Haveli and Daman and Diu', '27': 'Maharashtra',
  '29': 'Karnataka', '30': 'Goa', '31': 'Lakshadweep', '32': 'Kerala',
  '33': 'Tamil Nadu', '34': 'Puducherry',
  '35': 'Andaman and Nicobar Islands', '36': 'Telangana',
  '37': 'Andhra Pradesh', '38': 'Ladakh',
}

/** Upper-case and strip; a number pasted out of an email carries spaces. */
export function normaliseGstin(value: string | null | undefined): string | null {
  if (value == null) return null
  const cleaned = value.replace(/\s+/g, '').toUpperCase()
  return cleaned === '' ? null : cleaned
}

/** The fifteenth character the first fourteen imply. */
export function gstinCheckDigit(first14: string): string {
  let total = 0
  for (let i = 0; i < first14.length; i += 1) {
    const product = ALPHABET.indexOf(first14[i]) * (i % 2 ? 2 : 1)
    total += Math.floor(product / 36) + (product % 36)
  }
  return ALPHABET[(36 - (total % 36)) % 36]
}

/** What is wrong with it, or null when it looks right. */
export function gstinProblem(
  value: string | null | undefined, stateCode?: string | null,
): string | null {
  const gstin = normaliseGstin(value)
  if (gstin === null) return null          // absence is a different question

  if (gstin.length !== 15) {
    return `A GSTIN is 15 characters; this one is ${gstin.length}.`
  }
  if (!SHAPE.test(gstin)) {
    return 'That is not the shape of a GSTIN — two digits, then a PAN '
      + '(ABCDE1234F), an entity character, a Z, and a check digit, '
      + 'as in 37ABCDE1234F1ZZ.'
  }
  if (!(gstin.slice(0, 2) in GST_STATE_CODES)) {
    return `${gstin.slice(0, 2)} is not a GST state code.`
  }
  if (gstin[14] !== gstinCheckDigit(gstin.slice(0, 14))) {
    return 'The check digit does not match the rest of the number, so '
      + 'something in it is mistyped.'
  }
  const want = (stateCode ?? '').trim()
  if (want && gstin.slice(0, 2) !== want) {
    const belongs = GST_STATE_CODES[gstin.slice(0, 2)] ?? 'another state'
    return `This GSTIN is registered in ${belongs} (${gstin.slice(0, 2)}), but `
      + `the place of supply is ${want}. One of the two is wrong, and together `
      + 'they decide whether CGST+SGST or IGST applies.'
  }
  return null
}
