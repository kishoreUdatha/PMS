import { useQuery } from '@tanstack/react-query'
import { Banknote, CreditCard, Landmark, ReceiptText, Smartphone, Wallet } from 'lucide-react'
import { getCashOptions } from '../api'

/**
 * The ways this deployment can take money, fetched rather than restated.
 *
 * There were five lists. The server had two that disagreed — cashiering knew
 * about a wallet, invoicing did not — the onboarding screen offered four in
 * lower case, and three collection screens each kept their own in title case,
 * one of them offering "Link", which is a way of *asking* for money rather
 * than a way of receiving it.
 *
 * That was not untidiness. `finance.payments` held `upi` and `UPI` as
 * separate values, and `cash` and `Cash` likewise, because different screens
 * wrote different spellings of one method. The back-office reports normalised
 * on the way out and so read correctly, but the rows themselves disagreed,
 * and the two paths that wrote them — the deposit taken at check-in and the
 * balance taken at check-out — validated nothing at all. Migration 0032
 * merged the rows; the services now canonicalise on the way in.
 *
 * So no screen writes the list down any more. The icons stay here because
 * they are presentation, and the server has no business holding them; the
 * fallback covers a method added server-side before anyone picks an icon for
 * it, so a new method appears as a usable button rather than not at all.
 */

const ICONS: Record<string, React.ReactNode> = {
  cash: <Banknote size={18} />,
  card: <CreditCard size={18} />,
  upi: <Smartphone size={18} />,
  bank_transfer: <Landmark size={18} />,
  cheque: <ReceiptText size={18} />,
  wallet: <Wallet size={18} />,
}

export interface PaymentMethod {
  value: string
  label: string
  needs_reference: boolean
  icon: React.ReactNode
}

export function usePaymentMethods(propertyId: string) {
  const q = useQuery({
    queryKey: ['cashOptions', propertyId],
    queryFn: () => getCashOptions(propertyId),
    enabled: propertyId !== '',
    // The list changes when the code does, not while somebody is at the desk.
    staleTime: 10 * 60_000,
  })
  const methods: PaymentMethod[] = (q.data?.methods ?? []).map((m) => ({
    ...m,
    icon: ICONS[m.value] ?? <Wallet size={18} />,
  }))
  return { methods, isLoading: q.isLoading }
}

/**
 * A method's display name, for places that read a stored value back.
 *
 * Reports and shift summaries hold the canonical spelling (`bank_transfer`),
 * and screens showing one used to map the two or three they expected and
 * silently mislabel or omit the rest. This falls back to a readable form of
 * whatever arrives, so a method added server-side reads as "Bank Transfer"
 * rather than vanishing.
 */
export function useMethodLabel(propertyId: string) {
  const { methods } = usePaymentMethods(propertyId)
  return (value: string) => {
    const known = methods.find((m) => m.value === value)
    if (known) return known.label
    return value
      .split('_')
      .map((w) => (w ? w[0].toUpperCase() + w.slice(1) : w))
      .join(' ')
  }
}
