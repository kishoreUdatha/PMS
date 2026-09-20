/**
 * Pick lists shared by every form, in one place.
 *
 * These used to be typed by hand, or kept as a short local list on one screen
 * and a different one on the next. The database shows what that costs: guests
 * whose state is "Telangana", "AP" and "AndraPradash", and room types whose bed
 * setup is "King" on one screen and "1 King Bed" on another -- so a room type
 * made during onboarding opened blank on the Room Types screen.
 *
 * Nothing here is enforced by the database; these are what a person chooses
 * from. A value already stored that is not in a list is still shown and kept
 * (see ``withCurrent``), so switching a field to a dropdown never erases data.
 */
import { GST_STATE_CODES } from './gstin'

// ---------------------------------------------------------------- places
export const COUNTRIES: readonly string[] = [
  'India',
  'Afghanistan', 'Albania', 'Algeria', 'Andorra', 'Angola', 'Antigua and Barbuda',
  'Argentina', 'Armenia', 'Australia', 'Austria', 'Azerbaijan', 'Bahamas', 'Bahrain',
  'Bangladesh', 'Barbados', 'Belarus', 'Belgium', 'Belize', 'Benin', 'Bhutan',
  'Bolivia', 'Bosnia and Herzegovina', 'Botswana', 'Brazil', 'Brunei', 'Bulgaria',
  'Burkina Faso', 'Burundi', 'Cambodia', 'Cameroon', 'Canada', 'Cape Verde',
  'Central African Republic', 'Chad', 'Chile', 'China', 'Colombia', 'Comoros',
  'Congo', 'Costa Rica', 'Croatia', 'Cuba', 'Cyprus', 'Czech Republic',
  'Democratic Republic of the Congo', 'Denmark', 'Djibouti', 'Dominica',
  'Dominican Republic', 'Ecuador', 'Egypt', 'El Salvador', 'Equatorial Guinea',
  'Eritrea', 'Estonia', 'Eswatini', 'Ethiopia', 'Fiji', 'Finland', 'France', 'Gabon',
  'Gambia', 'Georgia', 'Germany', 'Ghana', 'Greece', 'Grenada', 'Guatemala', 'Guinea',
  'Guinea-Bissau', 'Guyana', 'Haiti', 'Honduras', 'Hong Kong', 'Hungary', 'Iceland',
  'Indonesia', 'Iran', 'Iraq', 'Ireland', 'Israel', 'Italy', 'Ivory Coast', 'Jamaica',
  'Japan', 'Jordan', 'Kazakhstan', 'Kenya', 'Kiribati', 'Kuwait', 'Kyrgyzstan', 'Laos',
  'Latvia', 'Lebanon', 'Lesotho', 'Liberia', 'Libya', 'Liechtenstein', 'Lithuania',
  'Luxembourg', 'Madagascar', 'Malawi', 'Malaysia', 'Maldives', 'Mali', 'Malta',
  'Marshall Islands', 'Mauritania', 'Mauritius', 'Mexico', 'Micronesia', 'Moldova',
  'Monaco', 'Mongolia', 'Montenegro', 'Morocco', 'Mozambique', 'Myanmar', 'Namibia',
  'Nauru', 'Nepal', 'Netherlands', 'New Zealand', 'Nicaragua', 'Niger', 'Nigeria',
  'North Korea', 'North Macedonia', 'Norway', 'Oman', 'Pakistan', 'Palau', 'Palestine',
  'Panama', 'Papua New Guinea', 'Paraguay', 'Peru', 'Philippines', 'Poland', 'Portugal',
  'Qatar', 'Romania', 'Russia', 'Rwanda', 'Saint Kitts and Nevis', 'Saint Lucia',
  'Saint Vincent and the Grenadines', 'Samoa', 'San Marino', 'Sao Tome and Principe',
  'Saudi Arabia', 'Senegal', 'Serbia', 'Seychelles', 'Sierra Leone', 'Singapore',
  'Slovakia', 'Slovenia', 'Solomon Islands', 'Somalia', 'South Africa', 'South Korea',
  'South Sudan', 'Spain', 'Sri Lanka', 'Sudan', 'Suriname', 'Sweden', 'Switzerland',
  'Syria', 'Taiwan', 'Tajikistan', 'Tanzania', 'Thailand', 'Timor-Leste', 'Togo',
  'Tonga', 'Trinidad and Tobago', 'Tunisia', 'Turkey', 'Turkmenistan', 'Tuvalu',
  'Uganda', 'Ukraine', 'United Arab Emirates', 'United Kingdom', 'United States',
  'Uruguay', 'Uzbekistan', 'Vanuatu', 'Vatican City', 'Venezuela', 'Vietnam', 'Yemen',
  'Zambia', 'Zimbabwe',
]

export const INDIAN_STATES: readonly string[] = [
  'Andaman and Nicobar Islands', 'Andhra Pradesh', 'Arunachal Pradesh', 'Assam',
  'Bihar', 'Chandigarh', 'Chhattisgarh', 'Dadra and Nagar Haveli and Daman and Diu',
  'Delhi', 'Goa', 'Gujarat', 'Haryana', 'Himachal Pradesh', 'Jammu and Kashmir',
  'Jharkhand', 'Karnataka', 'Kerala', 'Ladakh', 'Lakshadweep', 'Madhya Pradesh',
  'Maharashtra', 'Manipur', 'Meghalaya', 'Mizoram', 'Nagaland', 'Odisha',
  'Puducherry', 'Punjab', 'Rajasthan', 'Sikkim', 'Tamil Nadu', 'Telangana',
  'Tripura', 'Uttar Pradesh', 'Uttarakhand', 'West Bengal',
]

/** The GST state code for a state name, or '' when there is none. */
export function gstCodeForState(state: string | null | undefined): string {
  const name = (state ?? '').trim().toLowerCase()
  if (!name) return ''
  for (const [code, n] of Object.entries(GST_STATE_CODES)) {
    if (n.toLowerCase() === name) return code
  }
  return ''
}

/**
 * Suggestions, not a limit, for every state and union territory: the larger
 * cities first, then the towns travellers stay in. Properties on this platform
 * are anywhere in India, so no state is favoured. A city not listed can still
 * be typed.
 */
export const CITIES_BY_STATE: Record<string, readonly string[]> = {
  'Andaman and Nicobar Islands': ['Port Blair', 'Havelock Island', 'Neil Island',
    'Diglipur', 'Mayabunder', 'Car Nicobar'],
  'Andhra Pradesh': ['Visakhapatnam', 'Vijayawada', 'Guntur', 'Nellore', 'Tirupati',
    'Kurnool', 'Kakinada', 'Rajamahendravaram', 'Kadapa', 'Anantapur', 'Eluru',
    'Ongole', 'Vizianagaram', 'Srikakulam', 'Machilipatnam', 'Chittoor', 'Tenali',
    'Chirala', 'Bapatla', 'Amaravati', 'Puttaparthi'],
  'Arunachal Pradesh': ['Itanagar', 'Naharlagun', 'Tawang', 'Pasighat', 'Ziro',
    'Bomdila', 'Tezu', 'Roing'],
  Assam: ['Guwahati', 'Dibrugarh', 'Silchar', 'Jorhat', 'Tezpur', 'Nagaon',
    'Tinsukia', 'Bongaigaon', 'Kaziranga'],
  Bihar: ['Patna', 'Gaya', 'Bodh Gaya', 'Bhagalpur', 'Muzaffarpur', 'Darbhanga',
    'Purnia', 'Rajgir', 'Arrah', 'Begusarai'],
  Chandigarh: ['Chandigarh'],
  Chhattisgarh: ['Raipur', 'Bhilai', 'Bilaspur', 'Durg', 'Korba', 'Jagdalpur',
    'Rajnandgaon', 'Ambikapur'],
  'Dadra and Nagar Haveli and Daman and Diu': ['Daman', 'Diu', 'Silvassa'],
  Delhi: ['New Delhi', 'Delhi'],
  Goa: ['Panaji', 'Margao', 'Vasco da Gama', 'Mapusa', 'Ponda', 'Calangute',
    'Candolim'],
  Gujarat: ['Ahmedabad', 'Surat', 'Vadodara', 'Rajkot', 'Gandhinagar', 'Bhavnagar',
    'Jamnagar', 'Junagadh', 'Anand', 'Bhuj', 'Dwarka', 'Somnath'],
  Haryana: ['Gurugram', 'Faridabad', 'Panipat', 'Ambala', 'Karnal', 'Hisar',
    'Rohtak', 'Sonipat', 'Panchkula', 'Kurukshetra'],
  'Himachal Pradesh': ['Shimla', 'Manali', 'Dharamshala', 'Kullu', 'Mandi', 'Solan',
    'Dalhousie', 'Kasauli', 'McLeod Ganj'],
  'Jammu and Kashmir': ['Srinagar', 'Jammu', 'Gulmarg', 'Pahalgam', 'Katra',
    'Anantnag', 'Baramulla', 'Sonamarg'],
  Jharkhand: ['Ranchi', 'Jamshedpur', 'Dhanbad', 'Bokaro', 'Deoghar', 'Hazaribagh',
    'Giridih'],
  Karnataka: ['Bengaluru', 'Mysuru', 'Mangaluru', 'Hubballi', 'Dharwad', 'Belagavi',
    'Kalaburagi', 'Davanagere', 'Ballari', 'Udupi', 'Shivamogga', 'Hampi',
    'Madikeri', 'Chikkamagaluru', 'Gokarna'],
  Kerala: ['Thiruvananthapuram', 'Kochi', 'Kozhikode', 'Thrissur', 'Kollam',
    'Kannur', 'Alappuzha', 'Kottayam', 'Palakkad', 'Munnar', 'Varkala', 'Kovalam',
    'Wayanad', 'Kumarakom'],
  Ladakh: ['Leh', 'Kargil'],
  Lakshadweep: ['Kavaratti', 'Agatti', 'Minicoy', 'Bangaram'],
  'Madhya Pradesh': ['Bhopal', 'Indore', 'Jabalpur', 'Gwalior', 'Ujjain', 'Sagar',
    'Rewa', 'Satna', 'Khajuraho', 'Pachmarhi', 'Orchha'],
  Maharashtra: ['Mumbai', 'Pune', 'Nagpur', 'Nashik', 'Chhatrapati Sambhajinagar',
    'Thane', 'Navi Mumbai', 'Kolhapur', 'Solapur', 'Amravati', 'Nanded', 'Lonavala',
    'Mahabaleshwar', 'Shirdi', 'Alibag'],
  Manipur: ['Imphal', 'Thoubal', 'Churachandpur', 'Ukhrul', 'Bishnupur'],
  Meghalaya: ['Shillong', 'Tura', 'Sohra', 'Jowai', 'Nongpoh'],
  Mizoram: ['Aizawl', 'Lunglei', 'Champhai', 'Serchhip', 'Kolasib'],
  Nagaland: ['Kohima', 'Dimapur', 'Mokokchung', 'Tuensang', 'Wokha', 'Mon'],
  Odisha: ['Bhubaneswar', 'Cuttack', 'Puri', 'Rourkela', 'Berhampur', 'Sambalpur',
    'Balasore', 'Konark', 'Gopalpur'],
  Puducherry: ['Puducherry', 'Karaikal', 'Mahe', 'Yanam'],
  Punjab: ['Ludhiana', 'Amritsar', 'Jalandhar', 'Patiala', 'Bathinda', 'Mohali',
    'Pathankot', 'Hoshiarpur'],
  Rajasthan: ['Jaipur', 'Jodhpur', 'Udaipur', 'Kota', 'Ajmer', 'Bikaner', 'Jaisalmer',
    'Pushkar', 'Mount Abu', 'Alwar', 'Bharatpur', 'Sawai Madhopur', 'Chittorgarh'],
  Sikkim: ['Gangtok', 'Namchi', 'Pelling', 'Lachung', 'Mangan', 'Ravangla'],
  'Tamil Nadu': ['Chennai', 'Coimbatore', 'Madurai', 'Tiruchirappalli', 'Salem',
    'Tirunelveli', 'Vellore', 'Erode', 'Thoothukudi', 'Kanchipuram', 'Ooty',
    'Kodaikanal', 'Rameswaram', 'Kanyakumari', 'Mahabalipuram', 'Thanjavur'],
  Telangana: ['Hyderabad', 'Secunderabad', 'Warangal', 'Karimnagar', 'Nizamabad',
    'Khammam', 'Nalgonda', 'Mahbubnagar', 'Adilabad', 'Siddipet'],
  Tripura: ['Agartala', 'Udaipur', 'Dharmanagar', 'Kailashahar', 'Belonia'],
  'Uttar Pradesh': ['Lucknow', 'Kanpur', 'Varanasi', 'Agra', 'Noida', 'Greater Noida',
    'Ghaziabad', 'Prayagraj', 'Meerut', 'Mathura', 'Vrindavan', 'Ayodhya', 'Bareilly',
    'Aligarh', 'Gorakhpur', 'Jhansi'],
  Uttarakhand: ['Dehradun', 'Haridwar', 'Rishikesh', 'Nainital', 'Haldwani',
    'Mussoorie', 'Roorkee', 'Almora', 'Ramnagar'],
  'West Bengal': ['Kolkata', 'Howrah', 'Durgapur', 'Siliguri', 'Asansol', 'Darjeeling',
    'Kalimpong', 'Digha', 'Shantiniketan', 'Kharagpur'],
}

/** Abbreviations and old names, keyed compact (see ``compactState``). */
const STATE_ALIASES: Record<string, string> = {
  an: 'Andaman and Nicobar Islands', andaman: 'Andaman and Nicobar Islands',
  andamanandnicobar: 'Andaman and Nicobar Islands', ap: 'Andhra Pradesh',
  ar: 'Arunachal Pradesh', as: 'Assam', br: 'Bihar', ch: 'Chandigarh',
  cg: 'Chhattisgarh', ct: 'Chhattisgarh', chattisgarh: 'Chhattisgarh',
  chhatisgarh: 'Chhattisgarh',
  dn: 'Dadra and Nagar Haveli and Daman and Diu', dd: 'Dadra and Nagar Haveli and Daman and Diu',
  dnhdd: 'Dadra and Nagar Haveli and Daman and Diu',
  dadraandnagarhaveli: 'Dadra and Nagar Haveli and Daman and Diu',
  damananddiu: 'Dadra and Nagar Haveli and Daman and Diu',
  dl: 'Delhi', newdelhi: 'Delhi', nctofdelhi: 'Delhi', nctdelhi: 'Delhi', ga: 'Goa',
  gj: 'Gujarat', hr: 'Haryana', hp: 'Himachal Pradesh', jk: 'Jammu and Kashmir',
  jandk: 'Jammu and Kashmir', kashmir: 'Jammu and Kashmir', jh: 'Jharkhand',
  ka: 'Karnataka', kl: 'Kerala', la: 'Ladakh', ld: 'Lakshadweep', mp: 'Madhya Pradesh',
  mh: 'Maharashtra', mn: 'Manipur', ml: 'Meghalaya', mz: 'Mizoram', nl: 'Nagaland',
  od: 'Odisha', or: 'Odisha', orissa: 'Odisha', py: 'Puducherry',
  pondicherry: 'Puducherry', pondy: 'Puducherry', pb: 'Punjab', rj: 'Rajasthan',
  sk: 'Sikkim', tn: 'Tamil Nadu', ts: 'Telangana', tg: 'Telangana', tr: 'Tripura',
  up: 'Uttar Pradesh', uk: 'Uttarakhand', ua: 'Uttarakhand', uttaranchal: 'Uttarakhand',
  wb: 'West Bengal',
}

function compactState(v: string): string {
  return v.toLowerCase().replace(/&/g, 'and').replace(/[\s.\-_,]/g, '')
}

/** Similarity 0-1, the same measure the services use (difflib ratio). */
function ratio(a: string, b: string): number {
  // Longest-common-subsequence based, close enough to difflib for short names.
  const m = a.length, n = b.length
  if (!m || !n) return 0
  const dp = Array.from({ length: m + 1 }, () => new Array<number>(n + 1).fill(0))
  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      dp[i][j] = a[i - 1] === b[j - 1] ? dp[i - 1][j - 1] + 1 : Math.max(dp[i - 1][j], dp[i][j - 1])
    }
  }
  return (2 * dp[m][n]) / (m + n)
}

/**
 * The official state a typed name means -- "AP", "Orissa", "AndraPradash" --
 * or the value unchanged when it is not recognisably one (a foreign region).
 * The services apply the same rule on save (``chirala_common.india``).
 */
export function normaliseState(value: string | null | undefined): string {
  const raw = (value ?? '').trim()
  if (!raw) return ''
  const key = compactState(raw)
  const exact = INDIAN_STATES.find((s) => compactState(s) === key)
  if (exact) return exact
  if (STATE_ALIASES[key]) return STATE_ALIASES[key]
  if (key.length >= 5) {
    const scored = INDIAN_STATES.map((s) => ({ s, r: ratio(key, compactState(s)) }))
      .filter((x) => x.r >= 0.8).sort((x, y) => y.r - x.r)
    if (scored.length === 1 || (scored.length > 1 && scored[0].r - scored[1].r >= 0.05)) {
      return scored[0].s
    }
  }
  return raw
}

/** When a GST state code belongs to another state, say which; else null. */
export function stateCodeProblem(
  state: string | null | undefined, code: string | null | undefined,
): string | null {
  const c = (code ?? '').trim()
  const name = normaliseState(state)
  const expected = gstCodeForState(name)
  if (!c || !expected || c === expected) return null
  const other = GST_STATE_CODES[c]
  return other
    ? `GST state code ${c} is ${other}, but the state is ${name}.`
    : `${c} is not a GST state code.`
}

/**
 * The states each PIN code's first two digits are issued in. India Post zones
 * cross state lines in places (Bihar/Jharkhand, UP/Uttarakhand, and the small
 * territories inside larger circles), so a prefix can name several.
 * 90-99 are Army Postal Service codes and belong to no state.
 */
const PIN_PREFIX_STATES: Record<string, readonly string[]> = (() => {
  const m: Record<string, string[]> = {}
  const add = (prefixes: string, ...states: string[]) => {
    for (const p of prefixes.split(' ')) m[p] = [...(m[p] ?? []), ...states]
  }
  add('11', 'Delhi')
  add('12 13', 'Haryana')
  add('14 15', 'Punjab')
  add('16', 'Punjab', 'Chandigarh', 'Haryana', 'Himachal Pradesh')
  add('17', 'Himachal Pradesh')
  add('18', 'Jammu and Kashmir')
  add('19', 'Jammu and Kashmir', 'Ladakh')
  add('20 21 22 23 25 27 28', 'Uttar Pradesh')
  add('24 26', 'Uttar Pradesh', 'Uttarakhand')
  add('30 31 32 33 34', 'Rajasthan')
  add('36 39', 'Gujarat', 'Dadra and Nagar Haveli and Daman and Diu')
  add('37 38', 'Gujarat')
  add('40', 'Maharashtra', 'Goa')
  add('41 42 43 44', 'Maharashtra')
  add('45 46 47 48', 'Madhya Pradesh')
  add('49', 'Chhattisgarh')
  add('50', 'Telangana')
  add('51 52', 'Andhra Pradesh')
  add('53', 'Andhra Pradesh', 'Puducherry')
  add('56 57 58 59', 'Karnataka')
  add('60', 'Tamil Nadu', 'Puducherry')
  add('61 62 63 64', 'Tamil Nadu')
  add('67', 'Kerala', 'Puducherry')
  add('68', 'Kerala', 'Lakshadweep')
  add('69', 'Kerala')
  add('70 71 72', 'West Bengal')
  add('73', 'West Bengal', 'Sikkim')
  add('74', 'West Bengal', 'Andaman and Nicobar Islands')
  add('75 76 77', 'Odisha')
  add('78', 'Assam')
  add('79', 'Arunachal Pradesh', 'Meghalaya', 'Manipur', 'Mizoram', 'Nagaland',
    'Tripura', 'Assam')
  add('80 84 85', 'Bihar')
  add('81 82 83', 'Jharkhand', 'Bihar')
  return m
})()

/**
 * A warning -- not a refusal -- when a valid PIN code is not issued in the
 * chosen state. The prefix table is coarse at zone borders, so it stays
 * advice; the format rule (``postalCodeProblem``) is the one that blocks.
 */
export function pinStateWarning(
  code: string | null | undefined, state: string | null | undefined,
): string | null {
  const v = (code ?? '').trim()
  const name = normaliseState(state)
  if (!/^[1-9][0-9]{5}$/.test(v) || !INDIAN_STATES.includes(name)) return null
  const prefix = v.slice(0, 2)
  if (prefix >= '90') return null
  const states = PIN_PREFIX_STATES[prefix]
  if (!states) return `No PIN code in India starts with ${prefix}; please check it.`
  if (states.includes(name)) return null
  return `PIN codes starting ${prefix} are in ${states.join(' / ')}, not ${name}.`
}

/**
 * What is wrong with a postal code, or null when nothing is.
 *
 * Empty is not wrong -- the field is optional everywhere it appears. In India
 * (or with no country chosen) it must be a PIN code: six digits, the first
 * never 0. Elsewhere formats vary too much to check more than the shape.
 */
export function postalCodeProblem(
  code: string | null | undefined, country: string | null | undefined,
): string | null {
  const v = (code ?? '').trim()
  if (!v) return null
  if (isIndia(country)) {
    return /^[1-9][0-9]{5}$/.test(v) ? null : 'A PIN code is 6 digits and does not start with 0.'
  }
  return /^[A-Za-z0-9][A-Za-z0-9 -]{1,9}$/.test(v)
    ? null : 'A postal code is 2–10 letters, digits, spaces or hyphens.'
}

export function isIndia(country: string | null | undefined): boolean {
  const c = (country ?? '').trim().toLowerCase()
  return c === '' || c === 'india'
}

// ---------------------------------------------------------------- property
export const TIMEZONES: readonly string[] = [
  'Asia/Kolkata', 'Asia/Dubai', 'Asia/Colombo', 'Asia/Kathmandu', 'Asia/Dhaka',
  'Asia/Thimphu', 'Indian/Maldives', 'Asia/Singapore', 'Asia/Kuala_Lumpur',
  'Asia/Bangkok', 'Asia/Jakarta', 'Asia/Hong_Kong', 'Asia/Tokyo', 'Australia/Sydney',
  'Europe/London', 'Europe/Paris', 'Europe/Berlin', 'Africa/Nairobi',
  'Africa/Johannesburg', 'America/New_York', 'America/Chicago',
  'America/Los_Angeles', 'UTC',
]

export const CURRENCIES: readonly { code: string; symbol: string; name: string }[] = [
  { code: 'INR', symbol: '₹', name: 'Indian Rupee' },
  { code: 'AED', symbol: 'د.إ', name: 'UAE Dirham' },
  { code: 'LKR', symbol: 'Rs', name: 'Sri Lankan Rupee' },
  { code: 'NPR', symbol: 'Rs', name: 'Nepalese Rupee' },
  { code: 'BDT', symbol: '৳', name: 'Bangladeshi Taka' },
  { code: 'BTN', symbol: 'Nu', name: 'Bhutanese Ngultrum' },
  { code: 'MVR', symbol: 'Rf', name: 'Maldivian Rufiyaa' },
  { code: 'SGD', symbol: 'S$', name: 'Singapore Dollar' },
  { code: 'MYR', symbol: 'RM', name: 'Malaysian Ringgit' },
  { code: 'THB', symbol: '฿', name: 'Thai Baht' },
  { code: 'IDR', symbol: 'Rp', name: 'Indonesian Rupiah' },
  { code: 'HKD', symbol: 'HK$', name: 'Hong Kong Dollar' },
  { code: 'JPY', symbol: '¥', name: 'Japanese Yen' },
  { code: 'AUD', symbol: 'A$', name: 'Australian Dollar' },
  { code: 'GBP', symbol: '£', name: 'Pound Sterling' },
  { code: 'EUR', symbol: '€', name: 'Euro' },
  { code: 'KES', symbol: 'KSh', name: 'Kenyan Shilling' },
  { code: 'ZAR', symbol: 'R', name: 'South African Rand' },
  { code: 'USD', symbol: '$', name: 'US Dollar' },
]

// ---------------------------------------------------------------- stays
export const PURPOSES_OF_STAY: readonly string[] = [
  'Leisure', 'Business', 'Wedding / Event', 'Conference', 'Family visit',
  'Pilgrimage', 'Medical', 'Transit', 'Other',
]

// ---------------------------------------------------------------- rooms
/**
 * One vocabulary for every screen that sets a bed setup. The short forms are
 * the ones onboarding already stored, so existing room types stay matched.
 */
export const BED_SETUPS: readonly string[] = [
  'Single', 'Twin', 'Double', 'Queen', 'King', '2 Double', '2 Queen',
  'King + Single', 'King + 2 Single', 'King + Sofa bed', 'Bunk', 'Sofa bed',
]

export const ROOM_VIEWS: readonly string[] = [
  'Sea View', 'Partial Sea View', 'Beachfront', 'Garden View', 'Pool View',
  'City View', 'Mountain View', 'Courtyard View', 'No View',
]

// ---------------------------------------------------------------- helpers
/**
 * The list, plus the value already stored when it is not one of them.
 *
 * A dropdown that cannot show the current value opens blank, and saving it
 * would erase what was there. Kept at the end so the real list stays in order.
 */
export function withCurrent(list: readonly string[], value: string | null | undefined): string[] {
  const v = (value ?? '').trim()
  if (!v || list.some((x) => x.toLowerCase() === v.toLowerCase())) return [...list]
  return [...list, v]
}

export function isListed(list: readonly string[], value: string | null | undefined): boolean {
  const v = (value ?? '').trim().toLowerCase()
  return !v || list.some((x) => x.toLowerCase() === v)
}
