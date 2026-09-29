import { BedDouble } from 'lucide-react'
import { Link } from 'react-router-dom'
import { PLATFORM_NAME } from '../lib/brand'

/**
 * The product's own mark: the bed on brand teal, and the product name.
 *
 * The sign-in, set-password, wizard and console screens each hard-coded
 * "CHIRALA BAY / RESORT", the first customer's name, so every hotel's staff
 * signed in under somebody else's brand. The name comes from PLATFORM_NAME
 * (lib/brand.ts), so a white-label deployment changes it in one place. The
 * mark matches the favicon and the landing page's header.
 *
 * `home` makes it a link to the landing page. The sign-in screens need
 * that: a visitor who pressed Sign in by mistake had no way back.
 */
export default function BrandMark({ size = 'lg', home = false, caption }: {
  size?: 'sm' | 'lg'
  home?: boolean
  /** A line under the name, such as "Platform console". */
  caption?: string
}) {
  const big = size === 'lg'
  const mark = (
    <span className="inline-flex items-center gap-2.5">
      <span className={`flex items-center justify-center rounded-lg bg-brand text-white ${big ? 'h-10 w-10' : 'h-8 w-8'}`}>
        <BedDouble size={big ? 22 : 18} />
      </span>
      <span className="text-left">
        <span className={`block font-bold tracking-tight text-ink ${big ? 'text-2xl' : 'text-base'}`}>
          {PLATFORM_NAME}
        </span>
        {caption && <span className="block text-xs text-slate-400">{caption}</span>}
      </span>
    </span>
  )
  return home
    ? <Link to="/" title={`${PLATFORM_NAME} home`} className="inline-block">{mark}</Link>
    : mark
}
