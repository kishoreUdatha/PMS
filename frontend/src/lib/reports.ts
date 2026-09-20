/**
 * What the reports catalog and the report screen share.
 *
 * Kept out of the page files so each page exports only its component; a page
 * that also exports helpers loses Fast Refresh, as Onboarding already does.
 */
import {
  BarChart3, BedDouble, Briefcase, Handshake, ShieldCheck, Utensils, Wallet,
} from 'lucide-react'
import type { BackOfficeReportInfo } from '../api'

export const CATEGORY_ICON: Record<string, typeof Wallet> = {
  'Finance & ledgers': Wallet,
  'Revenue & sales': BarChart3,
  'Rooms & operations': BedDouble,
  'Tax & compliance': ShieldCheck,
  Management: Briefcase,
  Meals: Utensils,
  'Travel agents': Handshake,
}

/** The Hotel Ledger has its own screen; every other report is one route. */
export function reportHref(r: BackOfficeReportInfo): string {
  return r.href ?? `/reports/${r.slug}`
}

const FAVORITES_KEY = 'report-favorites'

/** Favourite reports, per browser. A convenience, so a blocked or cleared
 *  localStorage costs the star and nothing else. */
export function readFavorites(): string[] {
  try {
    const v: unknown = JSON.parse(localStorage.getItem(FAVORITES_KEY) ?? '[]')
    return Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : []
  } catch {
    return []
  }
}

export function toggleFavorite(slug: string): string[] {
  const current = readFavorites()
  const next = current.includes(slug)
    ? current.filter((x) => x !== slug)
    : [...current, slug]
  try {
    localStorage.setItem(FAVORITES_KEY, JSON.stringify(next))
  } catch {
    // Private window or storage blocked: the star just will not persist.
  }
  return next
}
