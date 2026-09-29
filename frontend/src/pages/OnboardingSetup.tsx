import { useEffect, useRef, useState } from 'react'
import Select from '../components/Select'
import ListSelect, { CityInput, PostalCodeInput } from '../components/ListSelect'
import {
  COUNTRIES, CURRENCIES, INDIAN_STATES, TIMEZONES, postalCodeProblem, withCurrent,
} from '../lib/options'
import { Link, useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle, ArrowLeft, Building, Building2, Check, Eye,
  Loader2, Palmtree, Upload,
} from 'lucide-react'
import { WizardFrame, Labelled, ContinueButton, obInput, obLabel } from './Onboarding'
import { useAuth } from '../auth/AuthContext'
import {
  getPropertyProfile, savePropertyProfile, getPropertyLogo,
  uploadPropertyLogo, deletePropertyLogo, type PropertyProfile,
  signUp,
  sendEmailOtp, verifyEmailOtp,
} from '../api'
import { PLATFORM_NAME } from '../lib/brand'
import { errorText } from '../lib/forms'

/**
 * The first two onboarding steps: who is signing up, and what they run.
 *
 * Step 2 is a real form against a real table. Step 1 is not, and the reason is
 * worth stating where it will be read: this system has no password store and
 * no mail transport. `auth_routes.py` says so plainly — "No passwords are
 * stored or checked in this dev flow; production must use the OIDC provider" —
 * and Keycloak sits in the stack unwired.
 *
 * So the account form is built and the fields are real, but Create Account is
 * disabled and the verification panel says why rather than showing six boxes
 * that would accept any code. A sign-up that appears to work, stores a
 * password somewhere improvised, and verifies nothing is worse than one that
 * admits it is not ready.
 */

// States, countries, timezones and currencies come from lib/options, shared
// with every other form: the state decides the GST treatment on every invoice
// and "AP", "A.P." and "Andhra" are three spellings of one tax jurisdiction.

const DIAL_CODES = ['🇮🇳 +91', '🇦🇪 +971', '🇱🇰 +94', '🇳🇵 +977', '🇬🇧 +44', '🇺🇸 +1']

const PROPERTY_TYPES = [
  { key: 'hotel', label: 'Hotel',
    blurb: 'City hotel, business hotel, etc.', icon: Building2 },
  { key: 'resort', label: 'Resort',
    blurb: 'Beach resort, leisure resort, etc.', icon: Palmtree },
  { key: 'serviced_apartment', label: 'Serviced Apartment',
    blurb: 'Apartments with hotel services', icon: Building },
]

/* ------------------------------------------------------------ 01 account --- */
export function OnboardingAccount() {
  const navigate = useNavigate()
  const { adopt } = useAuth()
  const [show, setShow] = useState(false)
  const [dial, setDial] = useState('+91')
  const [err, setErr] = useState('')
  const [form, setForm] = useState({
    full_name: '', email: '', phone: '', password: '', agreed: false,
  })
  const set = <K extends keyof typeof form>(k: K, v: (typeof form)[K]) =>
    setForm((f) => ({ ...f, [k]: v }))

  const emailOk = /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(form.email.trim())
  const [digits, setDigits] = useState(['', '', '', '', '', ''])
  const [sentTo, setSentTo] = useState('')
  const [verified, setVerified] = useState('')
  const boxes = useRef<(HTMLInputElement | null)[]>([])
  const code = digits.join('')

  // Changing the address invalidates a code sent to the previous one.
  useEffect(() => {
    if (verified && verified !== form.email.trim().toLowerCase()) {
      setVerified(''); setSentTo(''); setDigits(['', '', '', '', '', ''])
    }
  }, [form.email, verified])

  const sendCode = useMutation({
    mutationFn: () => sendEmailOtp(form.email.trim(), form.full_name.trim()),
    onSuccess: () => {
      setErr('')
      setSentTo(form.email.trim().toLowerCase())
      setDigits(['', '', '', '', '', ''])
      boxes.current[0]?.focus()
    },
    onError: (e) => setErr(
      errorText(e, 'The code could not be sent.')),
  })

  const checkCode = useMutation({
    mutationFn: () => verifyEmailOtp(form.email.trim(), code),
    onSuccess: () => { setErr(''); setVerified(form.email.trim().toLowerCase()) },
    onError: (e) => setErr(
      errorText(e, 'That code could not be checked.')),
  })

  /** Type, paste or backspace across six boxes without thinking about it. */
  const putDigit = (i: number, raw: string) => {
    const typed = raw.replace(/\D/g, '')
    if (typed === '') { setDigits((d) => d.map((x, n) => (n === i ? '' : x))); return }
    setDigits((d) => {
      const next = [...d]
      // A pasted code fills from here rather than landing entirely in one box.
      for (let k = 0; k < typed.length && i + k < 6; k += 1) next[i + k] = typed[k]
      return next
    })
    const landed = Math.min(i + typed.length, 5)
    boxes.current[landed]?.focus()
  }

  const ready = form.full_name.trim() !== ''
    && emailOk
    && form.password.length >= 10
    && form.agreed
    && verified === form.email.trim().toLowerCase()

  const create = useMutation({
    mutationFn: () => signUp({
      full_name: form.full_name.trim(),
      email: form.email.trim(),
      password: form.password,
      phone: form.phone.trim() ? `${dial} ${form.phone.trim()}` : undefined,
      agreed: form.agreed,
    }),
    onSuccess: (out) => {
      // The property the rest of the wizard writes to. Set before navigating,
      // because step 2 reads it the moment it mounts.
      localStorage.setItem('property_id', out.property_id)
      adopt(out.session)
      navigate('/onboarding/property')
    },
    onError: (e) => setErr(
      errorText(e, 'The account could not be created.')),
  })

  return (
    <WizardFrame step="account" eyebrow="01 — Account" stepNo={1}
      title="Create your account" blurb="Start setting up your property.">
      <div className="rounded-2xl border border-slate-100 bg-white p-6">
        <div className="grid gap-4 sm:grid-cols-2">
          <Labelled label="Full name">
            <input className={obInput} value={form.full_name}
              onChange={(e) => set('full_name', e.target.value)} />
          </Labelled>
          <Labelled label="Work email">
            <input className={obInput} type="email" value={form.email}
              placeholder="owner@example.com"
              onChange={(e) => set('email', e.target.value)} />
          </Labelled>
          <Labelled label="Mobile number">
            <span className="flex gap-2">
              <Select value={dial} onChange={(e) => setDial(e.target.value)}
                className="shrink-0 rounded-lg border border-slate-200 px-2 text-sm text-slate-600">
                <option value="+91">🇮🇳 +91</option>
                <option value="+971">🇦🇪 +971</option>
                <option value="+44">🇬🇧 +44</option>
                <option value="+1">🇺🇸 +1</option>
              </Select>
              <input className={`${obInput} min-w-0 flex-1`} value={form.phone}
                placeholder="Enter your mobile number"
                onChange={(e) => set('phone', e.target.value)} />
            </span>
          </Labelled>
          <Labelled label="Password">
            <span className="relative block">
              <input className={obInput} type={show ? 'text' : 'password'}
                value={form.password}
                onChange={(e) => set('password', e.target.value)} />
              <button type="button" onClick={() => setShow((v) => !v)}
                className="absolute right-3 top-1/2 flex -translate-y-1/2 items-center gap-1 text-xs font-medium text-slate-500">
                {show ? 'Hide' : 'Show'} <Eye size={14} />
              </button>
            </span>
          </Labelled>
        </div>

        <div className="mt-6 border-t border-slate-100 pt-5">
          <p className="text-base font-semibold text-slate-800">Verify your email</p>
          <p className="mt-0.5 text-sm text-slate-500">
            {verified
              ? <>This address is verified.</>
              : sentTo
                ? <>A 6-digit code was sent to{' '}
                    <span className="font-medium text-slate-700">{sentTo}</span>.
                    It expires in 10 minutes.</>
                : <>A 6-digit code will be sent to{' '}
                    <span className="font-medium text-slate-700">
                      {form.email || 'your work email'}
                    </span>.</>}
          </p>

          <div className="mt-3 flex flex-wrap items-center gap-2">
            {[0, 1, 2, 3, 4, 5].map((i) => (
              <input key={i} maxLength={6} aria-label={`Digit ${i + 1}`}
                inputMode="numeric" autoComplete="one-time-code"
                disabled={sentTo === '' || verified !== ''}
                ref={(el) => { boxes.current[i] = el }}
                value={digits[i]}
                onChange={(e) => putDigit(i, e.target.value)}
                onKeyDown={(e) => {
                  // Backspace on an empty box steps back, which is what
                  // everybody expects and nothing does by default.
                  if (e.key === 'Backspace' && digits[i] === '' && i > 0) {
                    boxes.current[i - 1]?.focus()
                  }
                }}
                className={`h-11 w-11 rounded-lg border text-center text-lg outline-none ${
                  verified ? 'border-emerald-300 bg-emerald-50 text-emerald-800'
                    : sentTo ? 'border-slate-300 focus:border-brand'
                    : 'border-slate-200 bg-slate-50'}`} />
            ))}

            {verified ? (
              <span className="flex items-center gap-1.5 rounded-lg bg-emerald-50 px-4 py-2.5 text-sm font-semibold text-emerald-700">
                <Check size={15} /> Verified
              </span>
            ) : sentTo ? (
              <button onClick={() => checkCode.mutate()}
                disabled={code.length < 6 || checkCode.isPending}
                className="flex items-center gap-2 rounded-lg bg-brand px-5 py-2.5 text-sm font-semibold text-white hover:bg-brand-dark disabled:opacity-40">
                {checkCode.isPending && <Loader2 size={14} className="animate-spin" />}
                Verify email
              </button>
            ) : (
              <button onClick={() => { setErr(''); sendCode.mutate() }}
                disabled={!emailOk || sendCode.isPending}
                title={emailOk ? undefined : 'Enter a work email first'}
                className="flex items-center gap-2 rounded-lg bg-brand px-5 py-2.5 text-sm font-semibold text-white hover:bg-brand-dark disabled:opacity-40">
                {sendCode.isPending && <Loader2 size={14} className="animate-spin" />}
                Send code
              </button>
            )}
          </div>

          {sentTo && !verified && (
            <p className="mt-2 text-xs text-slate-500">
              Didn&rsquo;t arrive? Check spam, then{' '}
              <button onClick={() => { setErr(''); sendCode.mutate() }}
                disabled={sendCode.isPending}
                className="font-semibold text-brand hover:underline disabled:opacity-40">
                send another
              </button>. A new code replaces the old one.
            </p>
          )}
        </div>

        <label className="mt-5 flex items-start gap-2 text-sm text-slate-600">
          <input type="checkbox" className="mt-0.5" checked={form.agreed}
            onChange={(e) => set('agreed', e.target.checked)} />
          <span>
            I agree to the <span className="font-medium text-brand">Terms of Service</span>
            {' '}and <span className="font-medium text-brand">Privacy Policy</span>
          </span>
        </label>

        {err && (
          <p className="mt-4 flex items-start gap-2 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700">
            <AlertTriangle size={16} className="mt-0.5 shrink-0" /> {err}
          </p>
        )}

        <div className="mt-5 flex flex-wrap items-center gap-4">
          <button disabled={!ready || create.isPending}
            onClick={() => { setErr(''); create.mutate() }}
            className="flex items-center gap-2 rounded-xl bg-brand px-6 py-3 text-sm font-semibold text-white hover:bg-brand-dark disabled:opacity-50">
            {create.isPending && <Loader2 size={15} className="animate-spin" />}
            Create Account
          </button>
          <span className="text-sm text-slate-500">
            Already registered?{' '}
            <Link to="/login" className="font-medium text-brand hover:underline">
              Sign in
            </Link>
          </span>
        </div>
        {!verified && emailOk && form.password.length >= 10 && form.agreed && (
          <p className="mt-2 text-xs text-amber-700">
            Verify the email address to finish.
          </p>
        )}
        {form.password !== '' && form.password.length < 10 && (
          <p className="mt-2 text-xs text-amber-700">
            {10 - form.password.length} more character
            {10 - form.password.length === 1 ? '' : 's'} for the password.
          </p>
        )}
      </div>


    </WizardFrame>
  )
}

/* ----------------------------------------------------------- 02 property --- */
export function OnboardingProperty() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const propertyId = localStorage.getItem('property_id') ?? ''

  const { data } = useQuery({
    queryKey: ['onboarding-property', propertyId],
    queryFn: () => getPropertyProfile(propertyId),
    enabled: propertyId !== '',
  })
  const fileRef = useRef<HTMLInputElement>(null)
  const { data: logo } = useQuery({
    queryKey: ['property-logo', propertyId],
    queryFn: () => getPropertyLogo(propertyId),
    enabled: propertyId !== '',
  })
  const [logoErr, setLogoErr] = useState('')
  const onLogoDone = () => {
    setLogoErr('')
    qc.invalidateQueries({ queryKey: ['property-logo', propertyId] })
  }
  const onLogoFail = (e: unknown) => setLogoErr(
    errorText(e, 'The logo could not be saved.'))

  const upload = useMutation({
    mutationFn: (f: File) => uploadPropertyLogo(propertyId, f),
    onSuccess: onLogoDone, onError: onLogoFail,
  })
  const removeLogo = useMutation({
    mutationFn: () => deletePropertyLogo(propertyId),
    onSuccess: onLogoDone, onError: onLogoFail,
  })

  const [form, setForm] = useState<PropertyProfile | null>(null)
  useEffect(() => { if (data && !form) setForm(data) }, [data, form])
  const set = <K extends keyof PropertyProfile>(k: K, v: PropertyProfile[K]) =>
    setForm((f) => (f ? { ...f, [k]: v } : f))

  const save = useMutation({
    mutationFn: () => savePropertyProfile(propertyId, form!),
    onSuccess: () => {
      // The readiness checklist counts this property, so it has to be re-read.
      qc.invalidateQueries({ queryKey: ['onboarding', propertyId] })
      // Step 3, not step 9. This read '/onboarding/connections' and skipped
      // six steps of the wizard on save.
      navigate('/onboarding/structure')
    },
  })
  const err = save.error as { response?: { data?: { detail?: string } } } | null

  if (!form) {
    return (
      <WizardFrame step="property" eyebrow="02 — Property Details" stepNo={2}
        title="Tell us about your property" blurb="Loading your property…">
        <Loader2 className="animate-spin text-slate-400" />
      </WizardFrame>
    )
  }

  return (
    <WizardFrame step="property" eyebrow="02 — Property Details" stepNo={2}
      title="Tell us about your property"
      blurb={`This information helps us tailor ${PLATFORM_NAME} to your needs.`}>
      <div className="rounded-2xl border border-slate-100 bg-white p-6">
        <p className={obLabel}>Property type</p>
        <div className="mt-2 grid gap-3 sm:grid-cols-3">
          {PROPERTY_TYPES.map((t) => {
            const on = form.property_type === t.key
            return (
              <button key={t.key} onClick={() => set('property_type', t.key)}
                className={`flex items-start gap-3 rounded-xl border p-3 text-left ${
                  on ? 'border-brand bg-brand-light'
                     : 'border-slate-200 hover:bg-slate-50'}`}>
                <t.icon size={20} className={on ? 'text-brand' : 'text-slate-400'} />
                <span className="min-w-0 flex-1">
                  <span className="block text-sm font-semibold text-slate-800">
                    {t.label}
                  </span>
                  <span className="block text-xs text-slate-500">{t.blurb}</span>
                </span>
                <span className={`mt-0.5 grid h-4 w-4 shrink-0 place-items-center rounded-full border ${
                  on ? 'border-brand bg-brand' : 'border-slate-300'}`}>
                  {on && <span className="h-1.5 w-1.5 rounded-full bg-white" />}
                </span>
              </button>
            )
          })}
        </div>

        <div className="mt-5 grid gap-4 sm:grid-cols-3">
          <Labelled label="Property name" required>
            <input className={obInput} value={form.name}
              onChange={(e) => set('name', e.target.value)} />
          </Labelled>
          <Labelled label="Contact email">
            <input className={obInput} type="email" value={form.contact_email ?? ''}
              onChange={(e) => set('contact_email', e.target.value || null)} />
          </Labelled>
          <Labelled label="Contact phone">
            <span className="flex gap-2">
              <Select className="shrink-0 rounded-lg border border-slate-200 px-2 text-sm text-slate-600">
                {DIAL_CODES.map((d) => <option key={d}>{d}</option>)}
              </Select>
              <input className={`${obInput} min-w-0 flex-1`}
                value={form.contact_phone ?? ''}
                placeholder="Enter contact number"
                onChange={(e) => set('contact_phone', e.target.value || null)} />
            </span>
          </Labelled>
        </div>

        <p className={`${obLabel} mt-5`}>Address</p>
        <div className="mt-2">
          <Labelled label="Street address">
            <input className={obInput} value={form.address_line ?? ''}
              onChange={(e) => set('address_line', e.target.value || null)} />
          </Labelled>
        </div>
        <div className="mt-4 grid gap-4 sm:grid-cols-4">
          <Labelled label="City">
            <CityInput className={obInput} value={form.city} state={form.state}
              onChange={(v) => set('city', v || null)} />
          </Labelled>
          <Labelled label="State">
            <ListSelect className={obInput} value={form.state} options={INDIAN_STATES}
              placeholder="Select state" onChange={(v) => set('state', v || null)} />
          </Labelled>
          <Labelled label="Postal code">
            <PostalCodeInput className={obInput} value={form.postal_code} country={form.country}
              state={form.state}
              onChange={(v) => set('postal_code', v || null)} />
          </Labelled>
          <Labelled label="Country">
            <ListSelect className={obInput} value={form.country} options={COUNTRIES}
              placeholder="Select country" onChange={(v) => set('country', v || null)} />
          </Labelled>
        </div>

        <div className="mt-5 grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
          <div>
            <p className={obLabel}>
              Property logo <span className="font-normal text-slate-400">(optional)</span>
            </p>
            <div className="mt-2 rounded-xl border border-dashed border-slate-200 p-4 text-center">
              {logo?.url ? (
                <>
                  <img src={logo.url} alt="Property logo"
                    className="mx-auto h-20 w-auto max-w-full object-contain" />
                  <div className="mt-3 flex justify-center gap-3 text-xs font-medium">
                    <button onClick={() => fileRef.current?.click()}
                      className="text-brand hover:underline">Replace</button>
                    <button onClick={() => removeLogo.mutate()}
                      className="text-red-600 hover:underline">Remove</button>
                  </div>
                </>
              ) : (
                <button onClick={() => fileRef.current?.click()}
                  disabled={upload.isPending}
                  className="flex w-full flex-col items-center gap-1 py-4 text-sm text-slate-500 hover:text-slate-700 disabled:opacity-50">
                  {upload.isPending
                    ? <Loader2 size={18} className="animate-spin" />
                    : <Upload size={18} className="text-slate-400" />}
                  Upload logo
                  <span className="text-xs text-slate-400">
                    PNG, JPG, WebP or SVG · max 2MB
                  </span>
                </button>
              )}
              <input ref={fileRef} type="file" hidden
                accept="image/png,image/jpeg,image/webp,image/svg+xml"
                onChange={(e) => {
                  const f = e.target.files?.[0]
                  // Reset so choosing the same file twice still fires.
                  e.target.value = ''
                  if (f) upload.mutate(f)
                }} />
            </div>
            {logoErr && (
              <p className="mt-2 text-xs text-red-600">{logoErr}</p>
            )}
          </div>

          <div className="rounded-xl bg-slate-50 p-4">
            <p className={obLabel}>Settings</p>
            <div className="mt-2 grid gap-4 sm:grid-cols-2">
              <Labelled label="Timezone">
                <Select className={obInput} value={form.timezone ?? ''}
                  onChange={(e) => set('timezone', e.target.value || null)}>
                  {withCurrent(TIMEZONES, form.timezone).map((t) => <option key={t} value={t}>{t}</option>)}
                </Select>
              </Labelled>
              <Labelled label="Currency">
                <Select className={obInput} value={form.currency ?? ''}
                  onChange={(e) => set('currency', e.target.value || null)}>
                  {CURRENCIES.map(({ code, symbol }) => (
                    <option key={code} value={code}>{code} ({symbol})</option>
                  ))}
                </Select>
              </Labelled>
            </div>
          </div>
        </div>

        {err && (
          <p className="mt-4 flex items-start gap-2 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700">
            <AlertTriangle size={16} className="mt-0.5 shrink-0" />
            {errorText(err, 'The property could not be saved.')}
          </p>
        )}
      </div>

      <div className="mt-6 flex items-center justify-between">
        <button onClick={() => navigate('/onboarding/account')}
          className="flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-medium text-slate-600 hover:bg-slate-50">
          <ArrowLeft size={15} /> Back
        </button>
        <ContinueButton step="property" mode="save" busy={save.isPending}
          hint={postalCodeProblem(form.postal_code, form.country) ? 'Fix the postal code to continue.' : undefined}
          onClick={() => {
            if (postalCodeProblem(form.postal_code, form.country) === null) save.mutate()
          }} />
      </div>
    </WizardFrame>
  )
}
