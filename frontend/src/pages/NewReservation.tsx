import { usePropertyName } from '../hooks/useProperty'
import { usePaymentMethods } from '../lib/paymentMethods'
import Select from '../components/Select'
import ListSelect, { CityInput, PostalCodeInput, StateField } from '../components/ListSelect'
import { COUNTRIES, PURPOSES_OF_STAY, postalCodeProblem } from '../lib/options'
import RoomPicker from '../components/RoomPicker'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useQueries, useQuery, keepPreviousData } from '@tanstack/react-query'
import DateField from '../components/DateField'
import TimeField from '../components/TimeField'
import {
  Calendar,
  Users,
  Bed,
  Share2,
  Gift, Users2,
  FileText,
  CreditCard,
  CheckCircle2,
  Loader2,
  AlertTriangle,
  MapPin,
  Building2,
  Plus,
  X,
} from 'lucide-react'
import {
  listProperties, searchGuests, getAvailableRooms, assignRoomToUnit, listCommercialAccounts, getAvailabilityByRoomType, BOOKING_SOURCES, type RoomTypeAvailability, type BookingSource, type CandidateRoom, createGuest, createHold, confirmReservation, listBookingAttributes, settleReservation, getTaxQuote, listMealPlans, getPropertySettings, listAccountRatePlans, ratePlanPrice, getCompReasons, markComplimentary,
  blocksForDates,
} from '../api'
import { Crumbs } from '../components/Crumbs'
import { useActivePropertyId } from '../hooks/useProperty'


/**
 * One room on the booking.
 *
 * A booking is not N copies of one room. A family takes a suite and a deluxe;
 * a group takes two doubles on breakfast and one on full board. Each line
 * carries its own type, board, occupancy and rate, which is what
 * `reservation_units` has always stored and what this screen could not say.
 */
interface RoomLine {
  key: string
  roomTypeId: string
  /** Board basis — "Rate Type" on the reference screen. */
  mealPlanId: string
  /** Pre-assigned room, or '' to leave it for the day of arrival. */
  roomId: string
  adults: number
  children: number
  /** Per night, before tax. Tax is quoted per line from the real rules. */
  rate: number
}

let lineSeq = 0

/**
 * A new line, optionally carrying over the previous one's choices — adding a
 * second room usually means another of much the same thing. The room itself
 * is never copied: two lines cannot hold the same room.
 */
function blankLine(from?: RoomLine): RoomLine {
  lineSeq += 1
  return {
    key: `line-${lineSeq}`,
    roomTypeId: from?.roomTypeId ?? '',
    mealPlanId: from?.mealPlanId ?? '',
    roomId: '',
    adults: from?.adults ?? 2,
    children: from?.children ?? 0,
    // No room type, no price. This used to default to a hard-coded 8500 --
    // a number matching no room type in any property -- so a blank line
    // showed a rate and a total for a room nobody had chosen. The rate
    // arrives when the type does.
    rate: from?.rate ?? 0,
  }
}

function nightsBetween(a: string, b: string): number {
  if (!a || !b) return 0
  const d = (new Date(b).getTime() - new Date(a).getTime()) / 86400000
  return d > 0 ? Math.round(d) : 0
}

const STEPS = ['Stay Details', 'Guest Details', 'Add-ons', 'Payment']

export default function NewReservation() {
  const propertyName = usePropertyName()
  const navigate = useNavigate()
  const propertyId = useActivePropertyId()

  const { data: properties } = useQuery({ queryKey: ['properties'], queryFn: listProperties })
  const organizationId = properties?.find((p) => p.id === propertyId)?.organization_id ?? ''

  const [step, setStep] = useState(0)

  // Stay details
  const [checkIn, setCheckIn] = useState('')
  const [checkOut, setCheckOut] = useState('')
  // One entry per room. Adding a room adds a line rather than bumping a
  // count, so the second room can be a different type on a different board
  // for a different number of people — which is how bookings actually arrive.
  const [lines, setLines] = useState<RoomLine[]>(() => [blankLine()])
  const [bookingSource, setBookingSource] = useState<BookingSource>('direct')
  const [purpose, setPurpose] = useState('')
  const [segmentId, setSegmentId] = useState('')
  const [company, setCompany] = useState('')
  const [agent, setAgent] = useState('')
  const [reference, setReference] = useState('')
  // engagement.guests has carried these columns since the first migration and
  // nothing has ever filled them at booking, which is the one moment the
  // guest is on the phone to be asked.
  const [addr, setAddr] = useState('')
  const [city, setCity] = useState('')
  const [stateName, setStateName] = useState('')
  const [zip, setZip] = useState('')
  const [country, setCountry] = useState('India')
  const [title, setTitle] = useState('')
  // Confirm takes inventory now; a tentative hold parks it and expires.
  const [reservationType, setReservationType] = useState<'confirm' | 'hold'>('confirm')
  const [businessSourceId, setBusinessSourceId] = useState('')
  const [eta, setEta] = useState('')
  const [etd, setEtd] = useState('')

  // The property's own check-in and check-out times, which step 5 of
  // onboarding exists to collect. Nothing read them, so every booking started
  // with two empty time fields and the desk either left them blank or typed
  // the same two times a hundred times a week.
  const { data: propertySettings } = useQuery({
    queryKey: ['property-settings', propertyId],
    queryFn: () => getPropertySettings(propertyId),
    enabled: propertyId !== '',
  })
  // Seeded once, and only into an untouched field: a clerk who has said the
  // guest lands at 2am must not have it overwritten when the query resolves.
  const seeded = useRef(false)
  useEffect(() => {
    if (seeded.current || !propertySettings) return
    seeded.current = true
    if (eta === '' && propertySettings.checkin_time) {
      setEta(propertySettings.checkin_time)
    }
    if (etd === '' && propertySettings.checkout_time) {
      setEtd(propertySettings.checkout_time)
    }
  }, [propertySettings, eta, etd])
  // Picking an existing guest instead of creating another one. Without this
  // the screen made a new record every time, which is how the directory ended
  // up with eight people called kishore.
  const [guestId, setGuestId] = useState<string | null>(null)
  const [guestQuery, setGuestQuery] = useState('')
  // Who the bill goes to. 'company' needs an account — the API and the
  // database both refuse the pair without one.
  const [billTo, setBillTo] = useState<'guest' | 'company'>('guest')
  // Declared here rather than only after the booking exists. A room given
  // away is decided when it is sold, and a comp that has to be marked
  // afterwards is un-declared for however long it takes somebody to remember
  // -- which is exactly the gap that made the old report guess from a zero
  // rate and name nobody.
  const [blockId, setBlockId] = useState('')
  const [compKind, setCompKind] = useState<'' | 'complimentary' | 'house_use'>('')
  const [compReason, setCompReason] = useState('')
  const [compNote, setCompNote] = useState('')
  const [accountId, setAccountId] = useState('')
  const [specialRequests, setSpecialRequests] = useState('')

  // Guest details
  const [guestName, setGuestName] = useState('')
  const [guestPhone, setGuestPhone] = useState('')
  const [guestEmail, setGuestEmail] = useState('')

  // Payment
  // '' means no advance is being taken — what the old "Pay Later" button
  // meant. It was sitting in the same list as the real methods, so a way of
  // *not* paying looked like a way of paying.
  const [payMethod, setPayMethod] = useState('')
  const [advanceAmount, setAdvanceAmount] = useState<number>(0)

  const [reservation, setReservation] = useState<{ id: string; number: string } | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [result, setResult] = useState<{ number: string; balance?: string; advancePaid?: string } | null>(null)
  const [errorMsg, setErrorMsg] = useState('')

  const nights = nightsBetween(checkIn, checkOut)
  // Every room's own rate, for every night. The screen previously multiplied
  // a single rate by the nights and ignored the room count entirely, so a
  // three-room booking quoted — and billed — for one.
  const roomCharge = nights * lines.reduce((sum, l) => sum + (l.rate || 0), 0)
  // Served, not written down here. This screen's own list spelled them
  // "UPI"/"Cash", and those are the spellings sitting in finance.payments
  // beside "upi"/"cash", splitting every revenue-by-method report in half.
  const { methods: payMethods } = usePaymentMethods(propertyId)

  // Blocks that still hold a room on every night of these dates. Offered
  // rather than remembered: a desk taking the fourteenth name off a wedding
  // list should not have to know the block exists, and a group member booked
  // *without* the block is refused outright — the rooms they were promised
  // are the rooms the block has taken off sale.
  const blocks = useQuery({
    queryKey: ['blocks-for-dates', propertyId, checkIn, checkOut],
    queryFn: () => blocksForDates(propertyId, checkIn, checkOut),
    enabled: propertyId !== '' && checkIn !== '' && checkOut > checkIn,
  })
  const blockOptions = blocks.data ?? []
  const block = blockOptions.find((b) => b.id === blockId)

  /** The block's agreed rate for a room type, if it named one. */
  function blockRate(roomTypeId: string): number | null {
    const r = block?.rates.find((x) => x.room_type_id === roomTypeId)
    return r ? Number(r.nightly_rate) : null
  }

  const compReasons = useQuery({
    queryKey: ['comp-reasons'], queryFn: getCompReasons,
    enabled: compKind !== '',
  })
  const compReasonList = (compKind === 'house_use'
    ? compReasons.data?.house_use : compReasons.data?.complimentary) ?? []
  const comped = compKind !== ''

  const totalAdults = lines.reduce((n, l) => n + l.adults, 0)
  const totalChildren = lines.reduce((n, l) => n + l.children, 0)

  // Tax comes from finance.tax_rules — the same rules the folio will post
  // from — rather than a flat percentage in this file. A hard-coded 18% here
  // quoted 23,010 on a stay that actually bills 20,685, and nobody would have
  // found out until checkout.
  const { data: accounts } = useQuery({
    queryKey: ['commercial-accounts', organizationId, 'active'],
    queryFn: () => listCommercialAccounts(organizationId, { status: 'active' }),
    enabled: organizationId !== '' && billTo === 'company',
  })
  const account = (accounts?.rows ?? []).find((a) => a.id === accountId)

  // The rate agreed with this company, if there is one. Fetched as soon as
  // the account is chosen, because the whole point is that the desk should
  // not have to know a negotiated rate exists.
  const { data: accountPlans } = useQuery({
    queryKey: ['account-rate-plans', propertyId, accountId],
    queryFn: () => listAccountRatePlans(propertyId, accountId),
    enabled: propertyId !== '' && billTo === 'company' && accountId !== '',
  })
  const corporatePlan = accountPlans?.[0] ?? null

  // Rooms free across the stay, per room type on the booking. One query per
  // distinct type rather than one per line, so three deluxe rooms ask once.
  const lineTypes = useMemo(
    () => [...new Set(lines.map((l) => l.roomTypeId).filter(Boolean))],
    [lines],
  )
  const roomQueries = useQueries({
    queries: lineTypes.map((rt) => ({
      queryKey: ['free-rooms', propertyId, rt, checkIn, checkOut],
      queryFn: () => getAvailableRooms({
        property_id: propertyId, room_type_id: rt,
        arrival_date: checkIn, departure_date: checkOut,
      }),
      enabled: propertyId !== '' && nights > 0,
    })),
  })
  const freeRoomsByType: Record<string, CandidateRoom[]> = {}
  lineTypes.forEach((rt, i) => { freeRoomsByType[rt] = roomQueries[i]?.data ?? [] })

  const { data: guestMatches } = useQuery({
    queryKey: ['guest-search', organizationId, guestQuery],
    queryFn: () => searchGuests(organizationId, guestQuery),
    enabled: organizationId !== '' && guestQuery.trim().length >= 2
      && guestId === null,
  })

  // The two classification masters. Only active entries — a retired
  // source stays on the bookings that already carry it but must not be
  // offered on a new one.
  const { data: sources } = useQuery({
    queryKey: ['booking-attributes', organizationId, 'business_source'],
    queryFn: () => listBookingAttributes(organizationId,
      { kind: 'business_source', status: 'active' }),
    enabled: organizationId !== '',
  })
  const { data: segments } = useQuery({
    queryKey: ['booking-attributes', organizationId, 'market_segment'],
    queryFn: () => listBookingAttributes(organizationId,
      { kind: 'market_segment', status: 'active' }),
    enabled: organizationId !== '',
  })

  const { data: mealPlans } = useQuery({
    queryKey: ['mealPlans', propertyId],
    queryFn: () => listMealPlans(propertyId),
    enabled: propertyId !== '',
  })

  // Tax is quoted per room, not on the booking total. Two of this property's
  // room levies are flat amounts per night — a beach cess and an environment
  // fee — and those are charged for every room every night, so quoting the
  // combined charge once would bill a three-room booking one room's cess.
  const quoteQueries = useQueries({
    queries: lines.map((l) => ({
      queryKey: ['tax-quote', propertyId, l.rate * nights, nights, checkIn],
      queryFn: () => getTaxQuote({
        property_id: propertyId, amount: l.rate * nights, nights,
        on_date: checkIn || undefined,
      }),
      enabled: propertyId !== '' && l.rate > 0 && nights > 0,
      placeholderData: keepPreviousData,
    })),
  })
  /** Each room's own tax, in line order — what the grid shows per row. */
  const lineTax = quoteQueries.map((q) => Number(q.data?.tax_total ?? 0))
  const taxes = lineTax.reduce((a, b) => a + b, 0)
  // One row per rule that applies, summed across the rooms, so the summary
  // still reads as a tax breakdown rather than a single opaque figure.
  const taxLines = (() => {
    const by = new Map<string,
      { code: string; rate: number; flat: boolean; amount: number }>()
    for (const q of quoteQueries) {
      for (const l of q.data?.lines ?? []) {
        const at = by.get(l.code) ?? {
          code: l.code, rate: Number(l.rate),
          flat: l.rate_type === 'amount', amount: 0,
        }
        at.amount += Number(l.tax_amount)
        by.set(l.code, at)
      }
    }
    return [...by.values()]
  })()
  const hasTaxRules = quoteQueries.some((q) => q.data?.has_rules)
  const taxQuoted = quoteQueries.some((q) => q.data !== undefined)
  // What the stay is worth. Kept even when it is being given away: this is
  // the figure the Complimentary Room Report calls "value given", and a comp
  // whose worth is unknown cannot be reported on.
  const grossTotal = roomCharge + taxes
  // What will actually be billed. A declared comp bills nothing -- charging
  // it and reversing it later is how a guest who was promised a free room
  // ends up with an invoice.
  const total = comped ? 0 : grossTotal
  const payableNow = comped ? 0 : Math.round(total * 0.33)

  // Every room type with what it can actually sell for these dates. One call
  // for all of them, so the picker can show the counts before a choice is made
  // instead of refusing the choice afterwards.
  const { data: types, isFetching: checkingAvailability } = useQuery({
    queryKey: ['availabilityByType', propertyId, checkIn, checkOut],
    queryFn: () =>
      getAvailabilityByRoomType({
        property_id: propertyId,
        arrival_date: checkIn,
        departure_date: checkOut,
      }),
    enabled: propertyId !== '' && nights > 0,
    placeholderData: keepPreviousData,
  })

  const typeById = useMemo(() => {
    const m = new Map<string, RoomTypeAvailability>()
    for (const t of types ?? []) m.set(t.room_type_id, t)
    return m
  }, [types])

  // Applied, never forced. The desk can still type over any line — a rate is
  // a starting point and somebody at the counter may have agreed otherwise.
  // Re-running when the account changes is deliberate: switching the company
  // should re-quote, or the booking keeps the previous company's rate.
  const appliedFor = useRef<string>('')
  useEffect(() => {
    if (!corporatePlan) { appliedFor.current = ''; return }
    if (appliedFor.current === corporatePlan.id) return
    appliedFor.current = corporatePlan.id
    setLines((prev) => prev.map((l) => {
      if (!l.roomTypeId) return l
      const t = types?.find((x) => x.room_type_id === l.roomTypeId)
      const base = t?.rate != null ? Number(t.rate) : l.rate
      return { ...l, rate: ratePlanPrice(corporatePlan, base) }
    }))
  }, [corporatePlan, types])

  // Rooms wanted per type across every line. Two lines of the same type
  // compete for one inventory counter, so they are checked together — the API
  // checks the same way, and would otherwise only refuse at submit.
  const demand = useMemo(() => {
    const m = new Map<string, number>()
    for (const l of lines) {
      if (l.roomTypeId) m.set(l.roomTypeId, (m.get(l.roomTypeId) ?? 0) + 1)
    }
    return m
  }, [lines])

  const overbooked = useMemo(
    () => [...demand.entries()]
      .map(([rt, wanted]) => ({ type: typeById.get(rt), wanted }))
      .filter((d): d is { type: RoomTypeAvailability; wanted: number } =>
        d.type !== undefined && d.wanted > d.type.sellable),
    [demand, typeById],
  )

  // Changing the dates can sell out a type already picked. Leaving it
  // selected would let the clerk carry on to a hold that is bound to fail.
  useEffect(() => {
    if (!types) return
    setLines((prev) => {
      let touched = false
      const next = prev.map((l) => {
        const t = l.roomTypeId ? typeById.get(l.roomTypeId) : undefined
        if (t && t.blocked_reason) {
          touched = true
          return { ...l, roomTypeId: '', roomId: '' }
        }
        return l
      })
      return touched ? next : prev
    })
  }, [types, typeById])

  /** Edit one line in place. */
  function setLine(key: string, patch: Partial<RoomLine>) {
    setLines((prev) => prev.map((l) => (l.key === key ? { ...l, ...patch } : l)))
  }

  const money = new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 })

  const canProceedStay = nights > 0 && lines.length > 0
    && lines.every((l) => l.roomTypeId !== '' && l.adults >= 1
      && typeById.get(l.roomTypeId)?.blocked_reason === null)
    && overbooked.length === 0
    && (billTo === 'guest' || accountId !== '')
    && (!comped || (compReason !== ''
                    && (compReason !== 'other' || compNote.trim() !== '')))
  const canCreate = canProceedStay && guestName.trim() !== '' && !submitting

  /*
   * What Confirm is still waiting for.
   *
   * The button was simply disabled, at half opacity, and clicking it did
   * nothing at all — no request, no error, no explanation. The guest name it
   * wants lives on step 2, so someone who has filled in step 1 sees a button
   * that looks available and is inert, with nothing anywhere saying why.
   *
   * The check-in screen already does this properly ("Still needed: Address,
   * ID Number, ID Proof"). This is the same idea: name the gap rather than
   * leave the user to guess which of a dozen fields it is.
   */
  const missing: string[] = []
  if (nights <= 0) missing.push('Check-in and check-out dates')
  if (lines.length === 0 || lines.some((l) => l.roomTypeId === '')) {
    missing.push('A room type on every line')
  }
  if (lines.some((l) => l.adults < 1)) missing.push('At least one adult per room')
  if (lines.some((l) => l.roomTypeId !== ''
      && typeById.get(l.roomTypeId)?.blocked_reason !== null)) {
    missing.push('A room type that is actually available')
  }
  if (overbooked.length > 0) missing.push('Fewer rooms than are on sale')
  if (billTo !== 'guest' && accountId === '') missing.push('An account to bill')
  if (comped && compReason === '') missing.push('A reason for the free room')
  if (comped && compReason === 'other' && compNote.trim() === '') {
    missing.push('A note saying what the free room was for')
  }
  if (guestName.trim() === '') missing.push('Guest name (step 2)')
  if (postalCodeProblem(zip, country)) missing.push('A valid postal code (step 2)')

  // Step 1 (Guest) -> create guest + hold + confirm, then advance to Add-ons.
  async function handleCreateReservation() {
    setErrorMsg('')
    setSubmitting(true)
    try {
      // An existing guest was picked, or a new one is created — never both.
      let resolvedGuestId: string | undefined = guestId ?? undefined
      if (!resolvedGuestId && guestName.trim()) {
        const g = await createGuest({
          title: title || undefined,
          organization_id: organizationId,
          full_name: guestName.trim(),
          phone: guestPhone || undefined,
          email: guestEmail || undefined,
          address_line: addr.trim() || undefined,
          city: city.trim() || undefined,
          state: stateName.trim() || undefined,
          postal_code: zip.trim() || undefined,
          country: country.trim() || undefined,
        })
        resolvedGuestId = g.id
      }
      const hold = await createHold({
        organization_id: organizationId,
        property_id: propertyId,
        arrival_date: checkIn,
        departure_date: checkOut,
        // One line per room, each with its own type, board and occupancy.
        // The API returns a unit per line, in this order.
        lines: lines.map((l) => ({
          room_type_id: l.roomTypeId,
          units: 1,
          adults: l.adults,
          children: l.children,
          meal_plan_id: l.mealPlanId || undefined,
          // The agreed rate, so check-in shows what was quoted rather than
          // the room type's list price.
          nightly_rate: l.rate || undefined,
        })),
        idempotency_key: `ui-${crypto.randomUUID()}`,
        guest_id: resolvedGuestId,
        // Recorded at the point of sale, because this is the only moment
        // anybody knows how the booking arrived.
        source: bookingSource,
        purpose_of_stay: purpose.trim() || undefined,
        market_segment_id: segmentId || undefined,
        company_name: company.trim() || undefined,
        travel_agent: agent.trim() || undefined,
        reference: reference.trim() || undefined,
        business_source_id: businessSourceId || undefined,
        bill_to: billTo,
        commercial_account_id: billTo === 'company' ? accountId : undefined,
        // Draw the rooms from the block rather than from general availability.
        group_block_id: blockId || undefined,
        expected_arrival_time: eta || undefined,
        expected_departure_time: etd || undefined,
        special_requests: specialRequests.trim() || undefined,
      })
      // A tentative booking stays held: inventory is parked, not sold.
      if (reservationType === 'confirm') {
        await confirmReservation(hold.reservation_id)
      }

      // Assign the rooms chosen at booking, one per unit. Done after confirm
      // because assignment writes a calendar entry the exclusion constraint
      // guards — if a room went in the meantime this refuses, and the booking
      // still stands with the room left to assign later.
      const units = hold.reservation_unit_ids ?? [hold.reservation_unit_id]
      for (let i = 0; i < units.length && i < lines.length; i++) {
        if (!lines[i].roomId) continue
        try {
          await assignRoomToUnit(units[i], lines[i].roomId)
        } catch {
          setErrorMsg('The booking was created, but a room could not be '
            + 'assigned — it was taken in the meantime. Assign it from the '
            + 'Front Desk.')
        }
      }
      // Declared on every room of the booking, through the same endpoint the
      // front desk uses, so the approver and the audit entry are recorded
      // identically whether the decision is made now or next week.
      if (comped) {
        for (const unitId of units) {
          try {
            await markComplimentary(unitId, propertyId, {
              kind: compKind as 'complimentary' | 'house_use',
              reason: compReason,
              note: compNote.trim() || null,
            })
          } catch {
            // The booking is real and must not be lost over this. Say what is
            // missing so it can be put right from the Front Desk, rather than
            // leaving a room that is quietly being charged.
            setErrorMsg('The booking was created, but it could not be marked '
              + 'free — mark it from the Front Desk, or it will be charged.')
          }
        }
      }
      setReservation({ id: hold.reservation_id, number: hold.number })
      setAdvanceAmount(payableNow)
      setStep(2) // Add-ons
    } catch (e) {
      setErrorMsg(extractError(e))
    } finally {
      setSubmitting(false)
    }
  }

  // Step 3 (Payment) -> post room charge + optional advance via finance.
  async function handleSettle(withAdvance: boolean) {
    if (!reservation) return
    setErrorMsg('')
    setSubmitting(true)
    try {
      const res = await settleReservation(reservation.id, {
        room_charge: total, // room + taxes billed to folio
        business_date: checkIn,
        method: withAdvance ? payMethod : undefined,
        advance_amount: withAdvance ? advanceAmount : undefined,
      })
      setResult({ number: reservation.number, balance: res.balance, advancePaid: res.advance_paid })
    } catch (e) {
      setErrorMsg(extractError(e))
    } finally {
      setSubmitting(false)
    }
  }

  if (result) {
    const money = new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 })
    return (
      <div className="mx-auto max-w-lg rounded-2xl border border-slate-100 bg-white p-8 text-center shadow-sm">
        <CheckCircle2 className="mx-auto text-emerald-500" size={48} />
        <h2 className="mt-4 text-2xl font-bold text-ink">Reservation Confirmed</h2>
        <p className="mt-2 text-slate-500">
          Reservation <span className="font-semibold text-slate-700">{result.number}</span> was created and confirmed.
        </p>
        {result.balance !== undefined && (
          <div className="mx-auto mt-5 w-full max-w-xs space-y-2 rounded-xl bg-slate-50 p-4 text-sm">
            <div className="flex justify-between"><span className="text-slate-500">Advance Paid</span><span className="font-medium text-emerald-600">{money.format(Number(result.advancePaid ?? 0))}</span></div>
            <div className="flex justify-between"><span className="text-slate-500">Folio Balance</span><span className="font-semibold text-slate-800">{money.format(Number(result.balance))}</span></div>
          </div>
        )}
        <div className="mt-6 flex justify-center gap-3">
          <button onClick={() => navigate('/reservations')} className="rounded-xl border border-slate-200 px-4 py-2.5 text-sm font-medium text-slate-600 hover:bg-slate-50">
            View Reservations
          </button>
          <button onClick={() => window.location.reload()} className="rounded-xl bg-brand px-4 py-2.5 text-sm font-medium text-white hover:bg-brand/90">
            New Reservation
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <Crumbs title="Create New Reservation" trail={[
            { label: 'Reservations', to: '/reservations/list' },
            { label: 'New Reservation' }]} />
          <h1 className="mt-1 text-display text-ink">Create New Reservation</h1>
          <p className="text-slate-500">Add guest details, select rooms and complete the booking.</p>
        </div>
        <div className="flex flex-col items-end gap-1.5">
          <button
            onClick={handleCreateReservation}
            disabled={!canCreate || reservation !== null}
            className="flex items-center gap-2 rounded-xl bg-brand px-5 py-3 text-sm font-medium text-white shadow-sm enabled:hover:bg-brand/90 disabled:opacity-50"
          >
            {submitting && !reservation ? <Loader2 size={18} className="animate-spin" /> : <CheckCircle2 size={18} />}
            Confirm Reservation
          </button>
          {reservation === null && missing.length > 0 && (
            <span className="flex max-w-xs flex-wrap items-center justify-end gap-x-1.5 gap-y-1 text-right text-xs text-slate-500">
              <AlertTriangle size={13} className="shrink-0 text-amber-500" />
              Still needed:
              {missing.map((m) => (
                <span key={m}
                  className="rounded bg-amber-50 px-1.5 py-0.5 text-caution">
                  {m}
                </span>
              ))}
            </span>
          )}
        </div>
      </div>

      {/* Stepper */}
      <div className="flex items-center gap-2 rounded-2xl border border-slate-100 bg-white px-6 py-4 shadow-sm">
        {STEPS.map((label, i) => (
          <div key={label} className="flex flex-1 items-center">
            <div className={`flex h-8 w-8 items-center justify-center rounded-full text-sm font-semibold ${i <= step ? 'bg-brand text-white' : 'bg-slate-75 text-slate-400'}`}>
              {i + 1}
            </div>
            <span className={`ml-2 text-sm ${i <= step ? 'font-medium text-slate-700' : 'text-slate-400'}`}>{label}</span>
            {i < STEPS.length - 1 && <div className="mx-3 h-px flex-1 bg-slate-200" />}
          </div>
        ))}
      </div>

      {errorMsg && (
        <div className="flex items-center gap-2 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700">
          <AlertTriangle size={16} /> {errorMsg}
        </div>
      )}

      <div className="grid gap-5 xl:grid-cols-[1fr_360px]">
        {/* Left: forms */}
        <div className="space-y-5">
          {step === 0 && (
            <div className="rounded-2xl border border-slate-100 bg-white p-6 shadow-sm">
              <h2 className="flex items-center gap-2 text-lg font-semibold text-ink">
                <Calendar size={18} className="text-brand" /> Stay Details
              </h2>
              <p className="mt-1 text-sm text-slate-400">Select your desired dates, room preferences and booking information.</p>

              <div className="mt-5 grid grid-cols-1 gap-4 [&>*]:min-w-0 md:grid-cols-3">
                {/* The time sits with its date because that is how it is
                    given: "the 10th, arriving about two". The time is what the
                    guest said; blank means nobody asked and the property's
                    published check-in applies. */}
                {/* min-w-0 on both the row and the date: a flex item will not
                    shrink below its intrinsic width by default, and a native
                    date input is wide, so without this the pair overflows its
                    grid column and runs under Nights. */}
                <Field label="Check-in" required>
                  <div className="flex min-w-0 gap-2">
                    <DateField value={checkIn} onChange={(v) => setCheckIn(v)} className={`${fieldCls} min-w-0 flex-1`} />
                    <TimeField value={eta} title="Expected arrival time" label="Expected arrival time"
                      className="w-[7.75rem] shrink-0"
                      onChange={(v) => setEta(v)} />
                  </div>
                </Field>
                <Field label="Check-out" required>
                  <div className="flex min-w-0 gap-2">
                    <DateField value={checkOut} onChange={(v) => setCheckOut(v)} className={`${fieldCls} min-w-0 flex-1`} />
                    <TimeField value={etd} title="Expected departure time" label="Expected departure time"
                      className="w-[7.75rem] shrink-0"
                      onChange={(v) => setEtd(v)} />
                  </div>
                </Field>
                {/* Derived from the dates, never typed — so it is sized to
                    the number it holds rather than to its grid column. */}
                {/* Derived from the dates, never typed — sized to the number
                    it holds rather than to its grid column. The width goes on
                    a wrapper because inputCls already carries w-full, and
                    Tailwind resolves competing width utilities by stylesheet
                    order, not by the order they appear in the class string. */}
                <Field label="Nights">
                  <div className="w-20">
                    <input value={nights || ''} readOnly tabIndex={-1}
                      className={`${inputCls} bg-slate-50 text-center font-semibold text-slate-600`} />
                  </div>
                </Field>
              </div>

              {/* How the booking is being taken, above the rooms it is for.
                  These four describe the booking rather than any one room, so
                  they belong with the dates — and whether it is a confirmation
                  or a tentative hold decides whether the rooms below come off
                  sale at all. */}
              <div className="mt-4 grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-4">
                {/* Confirming sells the inventory now; a tentative booking
                    parks it as a hold that expires on its own. */}
                <Field label="Reservation Type" required icon={<CheckCircle2 size={15} />}>
                  <Select value={reservationType} className={inputCls}
                    onChange={(e) => setReservationType(e.target.value as 'confirm' | 'hold')}>
                    <option value="confirm">Confirm Booking</option>
                    <option value="hold">Tentative — hold only</option>
                  </Select>
                </Field>
                {/* How the booking reached us — a closed set. */}
                <Field label="Booking Source" required icon={<Share2 size={15} />}>
                  <Select value={bookingSource} className={inputCls}
                    onChange={(e) => setBookingSource(e.target.value as BookingSource)}>
                    {BOOKING_SOURCES.map((so) => (
                      <option key={so.value} value={so.value}>{so.label}</option>
                    ))}
                  </Select>
                </Field>
                {/* Which OTA, which agent — a different question from the
                    source, which is only how the booking reached us. */}
                <Field label="Business Source" icon={<Share2 size={15} />}>
                  <AttributePicker value={businessSourceId} on={setBusinessSourceId}
                    rows={sources?.rows} label="business source" />
                </Field>
                <Field label="Market Segment" icon={<Share2 size={15} />}>
                  <AttributePicker value={segmentId} on={setSegmentId}
                    rows={segments?.rows} label="market segment" />
                </Field>
              </div>

              {/* One line per room, laid out as a grid the way the reference
                  screen does it: type, board, room, occupancy and rate all on
                  one line, and Add Room for the next one. The whole grid
                  scrolls sideways on a narrow screen rather than wrapping,
                  because a half-wrapped row stops reading as a row. */}
              <div className="mt-6">
                <div className="flex items-center justify-between">
                  <h3 className="flex items-center gap-2 text-sm font-semibold text-slate-700">
                    <Bed size={15} className="text-brand" /> Rooms
                    <span className="rounded-full bg-slate-75 px-2 py-0.5 text-xs font-medium text-slate-500">
                      {lines.length}
                    </span>
                  </h3>
                  <button type="button" onClick={() => setLines((p) =>
                    [...p, blankLine(p[p.length - 1])])}
                    disabled={nights <= 0 || lines.length >= 10}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-brand/30 bg-brand/5 px-3 py-1.5 text-sm font-medium text-brand hover:bg-brand/10 disabled:cursor-not-allowed disabled:border-slate-200 disabled:bg-slate-50 disabled:text-slate-400">
                    <Plus size={15} /> Add Room
                  </button>
                </div>

                <div className="mt-3 overflow-x-auto rounded-xl border border-slate-200">
                  <div className="min-w-[62rem]">
                    <div className={`${gridCols} border-b border-slate-200 bg-slate-50 px-3 py-2 text-xs font-semibold uppercase tracking-wide text-slate-500`}>
                      <div>Room Type <span className="text-red-400">*</span></div>
                      <div>Rate Type</div>
                      <div>Room</div>
                      <div className="text-center">Adult</div>
                      <div className="text-center">Child</div>
                      <div className="text-right">Rate (₹) / Night</div>
                      <div className="text-right">Amount (Tax Inc.)</div>
                      <div />
                    </div>
                    {lines.map((line, i) => {
                      const t = line.roomTypeId ? typeById.get(line.roomTypeId) : undefined
                      const cap = t?.max_occupancy ?? 6
                      const candidates = freeRoomsByType[line.roomTypeId] ?? []
                      const amount = line.rate * nights + (lineTax[i] ?? 0)
                      return (
                        <div key={line.key}
                          className={`${gridCols} items-center border-b border-slate-100 px-3 py-2 last:border-0 hover:bg-slate-50/60`}>
                          <div>
                            <Select value={line.roomTypeId} disabled={nights <= 0}
                              onChange={(e) => {
                                const picked = types?.find(
                                  (t) => t.room_type_id === e.target.value)
                                setLine(line.key, {
                                  roomTypeId: e.target.value, roomId: '',
                                  // The block's agreed rate where there is
                                  // one, otherwise the type's own rate, so the
                                  // desk starts from what the room actually
                                  // sells at and overrides that deliberately
                                  // rather than correcting a number nobody
                                  // chose. Picking the type after the block is
                                  // as common as the other way round, so both
                                  // orders have to price it.
                                  rate: blockRate(e.target.value)
                                    ?? (picked?.rate != null
                                      ? Number(picked.rate) : 0),
                                })
                              }}
                              className={`${cellCls} disabled:bg-slate-50 disabled:text-slate-400`}>
                              <option value="">
                                {nights <= 0 ? 'Dates first' : 'Select…'}
                              </option>
                              {types?.map((rt) => (
                                // The name is the label; availability is a
                                // hint, shown right-aligned in the list and
                                // dropped from the closed control. It is what
                                // you choose *by*, not what you chose -- and
                                // keeping it out of the label stops it
                                // widening the row past the field.
                                <option key={rt.room_type_id} value={rt.room_type_id}
                                  data-hint={
                                    rt.blocked_reason === 'sold_out' ? 'sold out'
                                      : rt.blocked_reason === 'not_loaded'
                                        ? `not on sale (${rt.nights_loaded}/${rt.nights})`
                                        : `${rt.sellable} left`}
                                  disabled={rt.blocked_reason !== null}>
                                  {rt.name}
                                </option>
                              ))}
                            </Select>
                          </div>
                          {/* The board basis. Stored on the unit, so the two
                              rooms on one booking can genuinely differ. */}
                          <div>
                            <Select value={line.mealPlanId} className={cellCls}
                              onChange={(e) => setLine(line.key,
                                { mealPlanId: e.target.value })}>
                              <option value="">Room only</option>
                              {(mealPlans ?? []).map((m) => (
                                <option key={m.id} value={m.id}>{m.name}</option>
                              ))}
                            </Select>
                          </div>
                          {/* Optional. A stay weeks out usually should not pin
                              a room yet — the Assign Rooms panel does it on
                              the day — but a walk-in wants it now. */}
                          <div>
                            <RoomPicker
                              value={line.roomId} placeholder="Assign later"
                              allowClear
                              disabled={line.roomTypeId === '' || nights <= 0}
                              onChange={(roomId) => setLine(line.key, { roomId })}
                              // A room already on another line of this same
                              // booking is ruled out here rather than by the
                              // server, which has no idea what the other
                              // lines of an unsaved booking are.
                              rooms={candidates.map((c) =>
                                lines.some((o) => o.key !== line.key
                                                && o.roomId === c.room_id)
                                  ? { ...c, available: false,
                                      blocked_reason: null,
                                      blocked_by: 'On another line' }
                                  : c)} />
                          </div>
                          {/* Capped at what the room type actually sleeps, so
                              the desk cannot sell four into a double. */}
                          <div>
                            <Select value={line.adults} className={`${cellCls} text-center`}
                              onChange={(e) => setLine(line.key, { adults: Number(e.target.value) })}>
                              {Array.from({ length: cap }, (_, n) => n + 1).map((n) => (
                                <option key={n} value={n}>{n}</option>
                              ))}
                            </Select>
                          </div>
                          <div>
                            <Select value={line.children} className={`${cellCls} text-center`}
                              onChange={(e) => setLine(line.key, { children: Number(e.target.value) })}>
                              {Array.from({ length: Math.max(cap - line.adults, 0) + 1 },
                                (_, n) => n).map((n) => (
                                  <option key={n} value={n}>{n}</option>
                                ))}
                            </Select>
                          </div>
                          <div>
                            <input type="number" min={0} value={line.rate}
                              className={`${cellCls} text-right`}
                              onChange={(e) => setLine(line.key, { rate: Number(e.target.value) })} />
                          </div>
                          {/* Rate for the stay plus the tax the folio will
                              actually post — quoted from finance.tax_rules,
                              not a percentage written into this screen. */}
                          <div className="text-right text-sm font-semibold text-slate-700">
                            {nights > 0 && line.rate > 0 ? money.format(amount) : '—'}
                          </div>
                          <div className="text-right">
                            <button type="button" title="Remove this room"
                              disabled={lines.length === 1}
                              onClick={() => setLines((p) => p.filter((o) => o.key !== line.key))}
                              className="rounded-md p-1.5 text-slate-400 hover:bg-red-50 hover:text-red-500 disabled:cursor-not-allowed disabled:opacity-30 disabled:hover:bg-transparent disabled:hover:text-slate-400">
                              <X size={15} />
                            </button>
                          </div>
                        </div>
                      )
                    })}
                  </div>
                </div>

                {/* Two lines of one type compete for the same counter. The
                    API checks their combined demand and would refuse at
                    submit; saying so here saves the round trip. */}
                {overbooked.length > 0 && (
                  <p className="mt-2 flex items-start gap-1.5 text-sm text-red-500">
                    <AlertTriangle size={15} className="mt-0.5 shrink-0" />
                    <span>
                      {overbooked.map((d) =>
                        `${d.wanted} × ${d.type.name} asked for, ${d.type.sellable} sellable`,
                      ).join('; ')}.
                    </span>
                  </p>
                )}
              </div>

              <div className="mt-5 grid grid-cols-1 gap-4 md:grid-cols-2">
                <Field label="Purpose of Stay" icon={<Gift size={15} />}>
                  <ListSelect value={purpose} onChange={setPurpose} options={PURPOSES_OF_STAY}
                    placeholder="Select purpose" className={inputCls} />
                </Field>
                <Field label="Bill To" required icon={<Building2 size={15} />}>
                  <Select value={billTo} className={inputCls}
                    onChange={(e) => {
                      setBillTo(e.target.value as 'guest' | 'company')
                      if (e.target.value === 'guest') setAccountId('')
                    }}>
                    <option value="guest">Guest</option>
                    <option value="company">Company / Agent</option>
                  </Select>
                </Field>
                {billTo === 'company' && (
                  <Field label="Account" required icon={<Building2 size={15} />}>
                    <Select value={accountId} className={inputCls}
                      onChange={(e) => setAccountId(e.target.value)}>
                      <option value="">Select an account</option>
                      {(accounts?.rows ?? []).map((a) => (
                        <option key={a.id} value={a.id}>
                          {a.code} — {a.name}
                          {a.over_limit ? ' · over credit limit' : ''}
                        </option>
                      ))}
                    </Select>
                    {/* Warned, not blocked: refusing the booking is the
                        property's call, not this screen's. */}
                    {account?.over_limit && (
                      <span className="mt-1 block text-xs text-amber-600">
                        {account.name} is already past its credit limit.
                      </span>
                    )}
                    {(accounts?.rows.length ?? 0) === 0 && (
                      <span className="mt-1 block text-xs text-slate-400">
                        No accounts yet — add one under Companies &amp; Agents.
                      </span>
                    )}
                    {/* Say that the rate changed and why. A price that moves
                        on its own with no explanation is worse than one the
                        clerk had to look up. */}
                    {corporatePlan && (
                      <span className="mt-1 block text-xs font-medium text-brand">
                        Using the agreed rate “{corporatePlan.name}”. Type over
                        any line to quote something else.
                      </span>
                    )}
                    {accountId && accountPlans?.length === 0 && (
                      <span className="mt-1 block text-xs text-slate-400">
                        No rate agreed with this company — the room's own rate
                        applies.
                      </span>
                    )}
                  </Field>
                )}
                {/* Only when a block could actually supply these dates. An
                    empty dropdown on every ordinary booking would be noise on
                    the screen the desk uses most. */}
                {blockOptions.length > 0 && (
                  <Field label="Part Of A Group Block" icon={<Users2 size={15} />}>
                    <Select value={blockId} className={inputCls}
                      onChange={(e) => {
                        const id = e.target.value
                        setBlockId(id)
                        // Quote the rate that was agreed. Choosing the block is
                        // the moment the desk says which deal applies, so it is
                        // the moment to price it -- leaving the list price on a
                        // group booking is how a wedding gets charged rack.
                        const chosen = blockOptions.find((b) => b.id === id)
                        setLines((ls) => ls.map((l) => {
                          const r = chosen?.rates.find(
                            (x) => x.room_type_id === l.roomTypeId)
                          return r ? { ...l, rate: Number(r.nightly_rate) } : l
                        }))
                      }}>
                      <option value="">Not part of a block</option>
                      {blockOptions.map((b) => (
                        <option key={b.id} value={b.id}>
                          {b.code} — {b.name} ({b.rooms_still_held} left)
                        </option>
                      ))}
                    </Select>
                    {block && (
                      <span className="mt-1 block text-xs text-brand">
                        These rooms come out of {block.code}, which is holding
                        them already — the booking will not be refused for a
                        full house.
                      </span>
                    )}
                  </Field>
                )}
                {/* Next to Bill To, because both answer the same question:
                    who pays for this room. "Nobody, and here is why" belongs
                    beside "the company", not on a screen visited later. */}
                <Field label="Charge For This Booking" icon={<Gift size={15} />}>
                  <Select value={compKind} className={inputCls}
                    onChange={(e) => {
                      setCompKind(e.target.value as '' | 'complimentary' | 'house_use')
                      setCompReason('')
                    }}>
                    <option value="">Charge normally</option>
                    <option value="complimentary">Complimentary — guest is not charged</option>
                    <option value="house_use">House use — the hotel takes the room</option>
                  </Select>
                  {comped && (
                    <span className="mt-1 block text-xs text-slate-400">
                      {compKind === 'house_use'
                        ? 'The room stays in occupancy but is not a guest stay.'
                        : 'The room stays in occupancy but is left out of ADR.'}
                    </span>
                  )}
                </Field>
                {comped && (
                  <Field label="Reason" required icon={<Gift size={15} />}>
                    <Select value={compReason} className={inputCls}
                      onChange={(e) => setCompReason(e.target.value)}>
                      <option value="">Choose a reason…</option>
                      {compReasonList.map((r) => (
                        <option key={r.value} value={r.value}>{r.label}</option>
                      ))}
                    </Select>
                  </Field>
                )}
                {comped && (
                  <Field
                    label="Note"
                    required={compReason === 'other'}
                    icon={<Gift size={15} />}
                  >
                    <input className={inputCls} value={compNote}
                      onChange={(e) => setCompNote(e.target.value)}
                      placeholder="What was agreed, and with whom" />
                    {compReason === 'other' && compNote.trim() === '' && (
                      <span className="mt-1 block text-xs text-amber-600">
                        Say what it was for — “Other” with nothing written down
                        is the row nobody can act on later.
                      </span>
                    )}
                  </Field>
                )}
                <Field label="Agent / Company" icon={<Share2 size={15} />}>
                  <input value={company} onChange={(e) => setCompany(e.target.value)}
                    placeholder="Booking on behalf of" className={inputCls} />
                </Field>
                <Field label="Travel Agent" icon={<Share2 size={15} />}>
                  <input value={agent} onChange={(e) => setAgent(e.target.value)}
                    placeholder="Agency that made the booking" className={inputCls} />
                </Field>
                <Field label="Reference" icon={<FileText size={15} />}>
                  <input value={reference} onChange={(e) => setReference(e.target.value)}
                    placeholder="Their PO or OTA booking number" className={inputCls} />
                </Field>
              </div>

              <Field label="Special Requests" icon={<FileText size={15} />}>
                <textarea value={specialRequests} onChange={(e) => setSpecialRequests(e.target.value)} maxLength={500} rows={2}
                  placeholder="e.g. High floor, early check-in, anniversary, etc." className={`${inputCls} resize-none`} />
              </Field>

              {nights > 0 && (
                <p className="mt-2 text-sm text-slate-500">
                  {checkingAvailability && !types ? 'Checking availability…'
                    : types?.every((t) => t.blocked_reason) ? (
                      <span className="text-red-500">
                        Nothing is sellable for these dates.
                      </span>
                    ) : (
                      <span>
                        {types?.filter((t) => !t.blocked_reason).length ?? 0} room
                        {' '}type(s) available for these dates.
                      </span>
                    )}
                </p>
              )}

              <div className="mt-5 flex justify-end">
                <button disabled={!canProceedStay} onClick={() => setStep(1)}
                  className="rounded-xl bg-brand px-5 py-2.5 text-sm font-medium text-white enabled:hover:bg-brand/90 disabled:opacity-50">
                  Continue to Guest Details
                </button>
              </div>
            </div>
          )}

          {step === 1 && (
            <div className="rounded-2xl border border-slate-100 bg-white p-6 shadow-sm">
              <h2 className="flex items-center gap-2 text-lg font-semibold text-ink">
                <Users size={18} className="text-brand" /> Guest Details
              </h2>
              <p className="mt-1 text-sm text-slate-400">Primary guest for this reservation.</p>
              <div className="mt-5 grid grid-cols-1 gap-4 md:grid-cols-2">
                <Field label="Title">
                  <Select value={title} className={inputCls}
                    onChange={(e) => setTitle(e.target.value)}>
                    <option value="">—</option>
                    {['Mr', 'Ms', 'Mrs', 'Miss', 'Dr', 'Prof'].map((t) => (
                      <option key={t} value={t}>{t}</option>
                    ))}
                  </Select>
                </Field>
                {/* Typing a name searches the directory. Picking a match
                    links the booking to that guest instead of creating a
                    second record — which is how eight people called kishore
                    ended up in there. */}
                <Field label="Full Name" required>
                  <div className="relative">
                    <input value={guestName} className={inputCls}
                      placeholder="e.g. Ravi Teja"
                      onChange={(e) => {
                        setGuestName(e.target.value)
                        setGuestQuery(e.target.value)
                        setGuestId(null)
                      }} />
                    {guestId && (
                      <button onClick={() => { setGuestId(null); setGuestQuery(guestName) }}
                        className="absolute right-2 top-1/2 -translate-y-1/2 rounded-full bg-emerald-50 px-2 py-0.5 text-[11px] font-semibold text-emerald-700">
                        existing guest · change
                      </button>
                    )}
                    {!guestId && (guestMatches?.length ?? 0) > 0 && (
                      <ul className="absolute z-20 mt-1 max-h-52 w-full overflow-y-auto rounded-xl border border-slate-200 bg-white shadow-lg">
                        {guestMatches!.slice(0, 6).map((g) => (
                          <li key={g.id}>
                            <button
                              onClick={() => {
                                setGuestId(g.id)
                                setGuestName(g.full_name)
                                setGuestPhone(g.phone ?? '')
                                setGuestEmail(g.email ?? '')
                                setGuestQuery('')
                              }}
                              className="block w-full px-3 py-2 text-left text-sm hover:bg-slate-50">
                              <span className="font-medium text-slate-800">
                                {g.full_name}
                              </span>
                              <span className="block text-xs text-slate-500">
                                {[g.phone, g.email].filter(Boolean).join(' · ') || 'No contact details'}
                              </span>
                            </button>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                  {guestId && (
                    <span className="mt-1 block text-xs text-emerald-600">
                      Linked to an existing guest — no new record will be created.
                    </span>
                  )}
                </Field>
                <Field label="Phone">
                  <input value={guestPhone} onChange={(e) => setGuestPhone(e.target.value)} placeholder="+91 …" className={inputCls} />
                </Field>
                <Field label="Email">
                  <input value={guestEmail} onChange={(e) => setGuestEmail(e.target.value)} placeholder="guest@example.com" className={inputCls} />
                </Field>
              </div>

              {/* The address block. These columns have existed on
                  engagement.guests since the first migration and nothing has
                  ever filled them at booking — which is the one moment the
                  guest is on the phone to be asked, and what an invoice needs
                  to carry a Bill To. */}
              <p className="mt-6 text-xs font-semibold uppercase tracking-wide text-slate-400">
                Address
              </p>
              <div className="mt-2 grid grid-cols-1 gap-4 md:grid-cols-2">
                <Field label="Address" icon={<MapPin size={15} />}>
                  <input value={addr} onChange={(e) => setAddr(e.target.value)}
                    placeholder="Street, building" className={inputCls} />
                </Field>
                <Field label="City">
                  <CityInput value={city} onChange={setCity} state={stateName}
                    className={inputCls} />
                </Field>
                <Field label="State">
                  <StateField value={stateName} onChange={setStateName} country={country}
                    className={inputCls} />
                </Field>
                <Field label="Postal Code">
                  <PostalCodeInput value={zip} onChange={setZip} country={country} state={stateName}
                    className={inputCls} />
                </Field>
                <Field label="Country">
                  <ListSelect value={country} onChange={setCountry} options={COUNTRIES}
                    placeholder="Select country" className={inputCls} />
                </Field>
              </div>
              <div className="mt-5 flex justify-between">
                <button onClick={() => setStep(0)} className="rounded-xl border border-slate-200 px-5 py-2.5 text-sm font-medium text-slate-600 hover:bg-slate-50">
                  Back
                </button>
                <button disabled={!canCreate} onClick={handleCreateReservation}
                  className="flex items-center gap-2 rounded-xl bg-brand px-5 py-2.5 text-sm font-medium text-white enabled:hover:bg-brand/90 disabled:opacity-50">
                  {submitting ? <Loader2 size={16} className="animate-spin" /> : <CheckCircle2 size={16} />}
                  Confirm &amp; Continue
                </button>
              </div>
            </div>
          )}

          {step === 2 && (
            <div className="rounded-2xl border border-slate-100 bg-white p-6 shadow-sm">
              <h2 className="flex items-center gap-2 text-lg font-semibold text-ink">
                <Gift size={18} className="text-brand" /> Add-ons
              </h2>
              <p className="mt-1 text-sm text-slate-400">
                Optional extras. Reservation <span className="font-medium text-slate-600">{reservation?.number}</span> is confirmed — you can skip add-ons.
              </p>
              <div className="mt-4 rounded-xl border border-dashed border-slate-200 p-4 text-sm text-slate-400">
                Add-ons (airport transfer, spa, breakfast) — coming soon.
              </div>
              <div className="mt-5 flex justify-end">
                <button onClick={() => setStep(3)} className="rounded-xl bg-brand px-5 py-2.5 text-sm font-medium text-white hover:bg-brand/90">
                  Continue to Payment
                </button>
              </div>
            </div>
          )}

          {step === 3 && (
            <div className="rounded-2xl border border-slate-100 bg-white p-6 shadow-sm">
              <h2 className="flex items-center gap-2 text-lg font-semibold text-ink">
                <CreditCard size={18} className="text-brand" /> Payment
              </h2>
              <p className="mt-1 text-sm text-slate-400">Collect an advance now, or complete without payment.</p>
              {/* Otherwise this reads as a screen waiting for money on a room
                  somebody has just been told is free. */}
              {comped && (
                <p className="mt-3 rounded-lg bg-brand-light px-3 py-2 text-sm text-brand">
                  This booking is {compKind === 'house_use' ? 'house use' : 'complimentary'}
                  {' '}— there is nothing to collect. Finish without payment.
                </p>
              )}

              <div className="mt-4">
                <span className="mb-2 block text-sm font-medium text-slate-600">Payment Method</span>
                <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                  {payMethods.map((m) => (
                    <button key={m.value} onClick={() => setPayMethod(m.value)}
                      className={`flex items-center justify-center gap-1.5 rounded-xl border px-3 py-3 text-sm font-medium ${payMethod === m.value ? 'border-brand bg-brand-light text-brand' : 'border-slate-200 text-slate-600 hover:bg-slate-50'}`}>
                      {m.icon}{m.label}
                    </button>
                  ))}
                </div>
              </div>

              <div className="mt-4 max-w-xs">
                <Field label="Advance Amount (₹)">
                  <input type="number" value={advanceAmount} onChange={(e) => setAdvanceAmount(Number(e.target.value))} className={inputCls} />
                </Field>
              </div>

              <div className="mt-5 flex justify-between">
                <button onClick={() => handleSettle(false)} disabled={submitting}
                  className="rounded-xl border border-slate-200 px-5 py-2.5 text-sm font-medium text-slate-600 hover:bg-slate-50 disabled:opacity-50">
                  Complete without payment
                </button>
                <button onClick={() => handleSettle(true)} disabled={submitting || payMethod === '' || advanceAmount <= 0}
                  className="flex items-center gap-2 rounded-xl bg-brand px-5 py-2.5 text-sm font-medium text-white enabled:hover:bg-brand/90 disabled:opacity-50">
                  {submitting ? <Loader2 size={16} className="animate-spin" /> : <CreditCard size={16} />}
                  Collect Advance &amp; Finish
                </button>
              </div>
            </div>
          )}
        </div>

        {/* Right: booking summary */}
        <div className="rounded-2xl border border-slate-100 bg-white p-6 shadow-sm">
          <h2 className="flex items-center gap-2 text-lg font-semibold text-ink">
            <FileText size={18} className="text-brand" /> Booking Summary
          </h2>
          <p className="mt-1 text-sm text-slate-400">Review your booking details.</p>

          <div className="mt-4 rounded-xl bg-gradient-to-br from-teal-600 to-teal-800 p-4 text-white">
            <div className="font-serif text-lg">{propertyName}</div>
          </div>

          <div className="mt-4 space-y-2 border-b border-slate-100 pb-4 text-sm">
            <div className="flex items-center gap-2 text-slate-600"><Calendar size={15} className="text-brand" /> {nights || 0} Nights {checkIn && checkOut ? `· ${checkIn} → ${checkOut}` : ''}</div>
            <div className="flex items-center gap-2 text-slate-600"><Bed size={15} className="text-brand" /> {lines.length} Room{lines.length === 1 ? '' : 's'}</div>
            <div className="flex items-center gap-2 text-slate-600"><Users size={15} className="text-brand" /> {totalAdults} Adults · {totalChildren} Children</div>
          </div>

          <div className="mt-4 space-y-2 text-sm">
            <Row label="Room Charges" value={money.format(roomCharge)} />
            {/* One row per rule that actually applies, so the quote reads
                the same as the folio the guest will be shown. */}
            {/* A flat levy is a sum per night, not a share of the price —
                labelling BEACH_CESS as "20%" would misstate the bill. */}
            {taxLines.map((l) => (
              <Row key={l.code}
                label={l.flat
                  ? `${l.code} (${money.format(l.rate)}/night/room)`
                  : `${l.code}${l.rate ? ` (${l.rate}%)` : ''}`}
                value={money.format(l.amount)} />
            ))}
            {taxQuoted && !hasTaxRules && (
              <Row label="Taxes" value="No tax rules configured" />
            )}
            {!taxQuoted && roomCharge > 0 && <Row label="Taxes" value="…" />}
          </div>
          <div className="mt-3 flex items-center justify-between rounded-lg bg-amber-50 px-3 py-2 text-sm font-semibold text-slate-800">
            <span>Total</span><span>{money.format(total)}</span>
          </div>
          {/* The worth of what is being given away, said out loud at the
              moment of the decision. A comp that shows only ₹0 hides the one
              number the person approving it should see. */}
          {comped && (
            <p className="mt-2 rounded-lg bg-brand-light px-3 py-2 text-xs text-brand">
              {compKind === 'house_use' ? 'House use' : 'Complimentary'} —
              nothing will be charged. {money.format(grossTotal)} of room
              revenue is being given up.
            </p>
          )}
          <div className="mt-2 flex items-center justify-between rounded-lg bg-emerald-50 px-3 py-2 text-sm font-semibold text-emerald-700">
            <span>Payable Now (Advance)</span><span>{money.format(payableNow)}</span>
          </div>
          <p className="mt-3 text-xs text-slate-400">You can modify room selection and guest details before confirming.</p>
        </div>
      </div>
    </div>
  )
}

// Split so a caller can set its own width. Tailwind does not resolve two
// width utilities by which is written last — it resolves them by stylesheet
// order — so `${inputCls} w-[7.75rem]` lost to the w-full baked into
// inputCls, and the time field grew to fill the row while the date field
// beside it collapsed to 26px. Anything sizing itself uses fieldCls.
const fieldCls = 'rounded-lg border border-slate-200 px-3 py-2.5 text-sm outline-none focus:border-brand'
const inputCls = `w-full ${fieldCls}`

/**
 * The room grid's columns, shared by the header and every row so the two
 * cannot drift apart. Fixed tracks for the narrow numeric columns; the type,
 * board and room pickers take what is left.
 */
const gridCols = 'grid grid-cols-[minmax(12rem,1.5fr)_minmax(9rem,1fr)_minmax(10rem,1.2fr)_4.5rem_4.5rem_7.5rem_8rem_2.5rem] gap-2'

/** Inputs inside the grid: tighter than a form field, same focus treatment. */
const cellCls = 'w-full min-w-0 rounded-md border border-slate-200 bg-white px-2 py-1.5 text-sm outline-none focus:border-brand disabled:bg-slate-50 disabled:text-slate-400'

/**
 * A classification picker.
 *
 * Empty rather than a free-text box when the master has nothing in it: the
 * point of the master is that only what is on the list can be chosen, so an
 * empty list has to say where the list comes from rather than quietly let
 * somebody type past it.
 */
function AttributePicker({ value, on, rows, label }: {
  value: string
  on: (v: string) => void
  rows: { id: string; name: string }[] | undefined
  label: string
}) {
  if (rows && rows.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-slate-200 px-3 py-2.5 text-sm text-slate-400">
        No {label} set up —{' '}
        <Link to="/reservations/sources" className="text-brand hover:underline">
          add one
        </Link>
      </div>
    )
  }
  return (
    <Select value={value} className={inputCls}
      onChange={(e) => on(e.target.value)}>
      <option value="">Not recorded</option>
      {(rows ?? []).map((r) => (
        <option key={r.id} value={r.id}>{r.name}</option>
      ))}
    </Select>
  )
}

function Field({ label, required, icon, children }: { label: string; required?: boolean; icon?: React.ReactNode; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 flex items-center gap-1.5 text-sm font-medium text-slate-600">
        {icon}{label}{required && <span className="text-red-500">*</span>}
      </span>
      {children}
    </label>
  )
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between text-slate-600">
      <span>{label}</span><span className="font-medium text-slate-800">{value}</span>
    </div>
  )
}

function extractError(e: unknown): string {
  const err = e as { response?: { data?: { detail?: unknown } }; message?: string }
  const detail = err.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (detail) return JSON.stringify(detail)
  return err.message ?? 'Something went wrong'
}
