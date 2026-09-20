import { useId } from 'react'
import Select from './Select'
import {
  CITIES_BY_STATE, INDIAN_STATES, isIndia, isListed, normaliseState, pinStateWarning,
  postalCodeProblem, withCurrent,
} from '../lib/options'

/**
 * A postal code field that says what is wrong under itself. For India it takes
 * digits only, up to six, so a PIN code cannot be typed wrong by a stray space.
 * The form decides whether a problem blocks saving (``postalCodeProblem``).
 *
 * Given the state, it also warns -- without blocking -- when the PIN code is
 * issued somewhere else, e.g. a 400xxx Mumbai code on a Kerala address.
 */
export function PostalCodeInput({
  value, onChange, country, state, className,
}: {
  value: string | null | undefined
  onChange: (value: string) => void
  country?: string | null
  state?: string | null
  className?: string
}) {
  const india = isIndia(country)
  const problem = postalCodeProblem(value, country)
  const warning = !problem && india ? pinStateWarning(value, state) : null
  return (
    <>
      <input value={value ?? ''} aria-invalid={problem !== null}
        inputMode={india ? 'numeric' : 'text'} maxLength={india ? 6 : 10}
        placeholder={india ? '6-digit PIN code' : 'Postal code'}
        className={`${className ?? ''}${problem ? ' border-red-400' : warning ? ' border-amber-400' : ''}`}
        onChange={(e) => onChange(india ? e.target.value.replace(/\D/g, '') : e.target.value)} />
      {problem && <span className="mt-1 block text-xs text-red-600">{problem}</span>}
      {warning && <span className="mt-1 block text-xs text-amber-700">{warning}</span>}
    </>
  )
}

/**
 * A state field: India's states as a dropdown, free text for anywhere else.
 * With no country chosen it assumes India.
 *
 * A stored abbreviation or misspelling ("TN", "Orissa", "AndraPradash") opens
 * as the state it means rather than as an unknown value; the services store
 * the official name when the form is saved.
 */
export function StateField({
  value, onChange, country, className, placeholder = 'Select state',
}: {
  value: string | null | undefined
  onChange: (value: string) => void
  country?: string | null
  className?: string
  placeholder?: string
}) {
  const c = (country ?? '').trim().toLowerCase()
  if (c && c !== 'india') {
    return <input value={value ?? ''} className={className} placeholder="State / region"
      onChange={(e) => onChange(e.target.value)} />
  }
  return <ListSelect value={normaliseState(value)} onChange={onChange} options={INDIAN_STATES}
    className={className} placeholder={placeholder} />
}

/**
 * A dropdown over one of the shared pick lists.
 *
 * Built on the app's Select, so it looks and keys like every other dropdown --
 * including type-ahead, which is how a long list like countries is searched:
 * typing "sri" jumps to Sri Lanka.
 *
 * **It never loses a stored value.** A value that is not in the list is still
 * offered, marked as not in the list, so the form opens showing it and saving
 * leaves it alone until somebody chooses otherwise.
 */
export default function ListSelect({
  value, onChange, options, placeholder = 'Select', className, disabled,
  labelFor,
}: {
  value: string | null | undefined
  onChange: (value: string) => void
  options: readonly string[]
  placeholder?: string
  className?: string
  disabled?: boolean
  /** Display text for an option, when it should differ from its value. */
  labelFor?: (value: string) => string
}) {
  const current = value ?? ''
  return (
    <Select value={current} className={className} disabled={disabled}
      onChange={(e) => onChange(e.target.value)}>
      <option value="">{placeholder}</option>
      {withCurrent(options, current).map((o) => (
        <option key={o} value={o}>
          {(labelFor ? labelFor(o) : o)}
          {isListed(options, o) ? '' : ' (not in list)'}
        </option>
      ))}
    </Select>
  )
}

/**
 * A city field: type anything, with the chosen state's cities suggested.
 *
 * Not a dropdown on purpose. No list of every town a guest might come from is
 * complete, and refusing a real address because it is missing from ours would
 * be worse than a typo. Suggestions catch the common spellings.
 */
export function CityInput({
  value, onChange, state, className, placeholder,
}: {
  value: string | null | undefined
  onChange: (value: string) => void
  state?: string | null
  className?: string
  placeholder?: string
}) {
  const listId = useId()
  const name = normaliseState(state)
  const suggestions = name ? CITIES_BY_STATE[name] ?? [] : Object.values(CITIES_BY_STATE).flat()
  return (
    <>
      <input value={value ?? ''} list={listId} className={className}
        placeholder={placeholder ?? (name && CITIES_BY_STATE[name] ? `City in ${name}` : 'City')}
        onChange={(e) => onChange(e.target.value)} autoComplete="off" />
      <datalist id={listId}>
        {[...new Set(suggestions)].map((c) => <option key={c} value={c} />)}
      </datalist>
    </>
  )
}
