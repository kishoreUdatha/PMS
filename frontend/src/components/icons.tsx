import type { SVGProps } from 'react'

/**
 * Icons the mockups use that lucide-react does not ship.
 *
 * Drawn to lucide's conventions so they sit correctly beside the rest of the
 * set: 24x24 viewBox, no fill, `currentColor` stroke at width 2, round caps
 * and joins. Size them with the same height/width classes as any lucide icon.
 */

/**
 * Broom — the Cleaning / Housekeeping glyph on screens 008 and 059.
 * Handle runs from the top right down to a collar, with the bristle block
 * flaring toward the bottom left, matching the mockup's orientation.
 */
export function Broom({
  className,
  size,
  ...props
}: SVGProps<SVGSVGElement> & { size?: number | string }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      // `size` mirrors lucide's API so these drop into the same call sites.
      width={size ?? props.width ?? 24}
      height={size ?? props.height ?? 24}
      className={className}
      aria-hidden="true"
      {...props}
    >
      {/* handle */}
      <path d="M20 3.4 14.6 8.8" />
      {/* binding band where the handle enters the bristles */}
      <path d="M12.9 7.9 15.7 10.7" />
      {/* Bristle head — narrow at the band, flaring to the sweeping edge.
          Filled rather than outlined: at 16-20px an outlined head closes up
          and reads as a paper plane. The mockup's glyph is solid too. */}
      <path
        d="M12 9.3 5.4 11.5 11 18.3 14.6 11.9Z"
        fill="currentColor"
        strokeWidth={1.4}
      />
    </svg>
  )
}
