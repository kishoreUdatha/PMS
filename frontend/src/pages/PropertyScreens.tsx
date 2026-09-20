import RoomTypeManagement from './RoomTypeManagement'
import AmenitiesManagement from './AmenitiesManagement'
import BuildingsFloors from './BuildingsFloors'
import RoomBlocks from './RoomBlocks'
import RatePlans from './RatePlans'
import RatesInventory from './RatesInventory'
import PackagesPromotions from './PackagesPromotions'
import type { ReactNode } from 'react'
import { Building2 } from 'lucide-react'
import { Crumbs } from '../components/Crumbs'
import { useActivePropertyId } from '../hooks/useProperty'

/**
 * Standalone routes for screens 060 and 062.
 *
 * Both also appear as tabs on Rooms & Villas (screen 008). The management
 * components are shared; only this page header differs, so the two entry
 * points can never drift apart.
 */


/** Crumbs, then the title with the screen's actions on the same line.
 *  Most management components keep their actions inside their own card;
 *  Buildings & Floors hands its actions back through a `header` prop. */
function Header({ crumb, title, actions }: {
  crumb: string; title: string; actions?: ReactNode
}) {
  return (
    <div>
      <Crumbs title={title} trail={[{ label: 'Property', to: '/property' },
        { label: crumb }]} />
      <div className="mt-1 flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <h1 className="flex items-center gap-2 text-display text-ink">
          <Building2 size={26} className="text-brand" /> {title}
        </h1>
        {actions}
      </div>
    </div>
  )
}

export function RoomTypesScreen() {
  const propertyId = useActivePropertyId()
  return (
    <div className="space-y-4">
      <Header crumb="Room Types" title="Room Type Management" />
      <RoomTypeManagement propertyId={propertyId} />
    </div>
  )
}

export function AmenitiesScreen() {
  const propertyId = useActivePropertyId()
  return (
    <div className="space-y-4">
      <Header crumb="Amenities" title="Amenities Management" />
      <AmenitiesManagement propertyId={propertyId} />
    </div>
  )
}

export function BuildingsFloorsScreen() {
  const propertyId = useActivePropertyId()
  return (
    <div className="space-y-4">
      <BuildingsFloors propertyId={propertyId} header={(actions) => (
        <Header crumb="Buildings & Floors" title="Buildings & Floors" actions={actions} />
      )} />
    </div>
  )
}

export function RoomBlocksScreen() {
  const propertyId = useActivePropertyId()
  return (
    <div className="space-y-4">
      <Header crumb="Block / Out of Order" title="Room Block and Out of Order" />
      <RoomBlocks propertyId={propertyId} />
    </div>
  )
}

/** Screen 033 carries its own heading, so it needs no `Header` wrapper. */
export function RatePlansScreen() {
  const propertyId = useActivePropertyId()
  return <RatePlans propertyId={propertyId} />
}

/** Screen 034 also carries its own heading. */
export function RatesInventoryScreen() {
  const propertyId = useActivePropertyId()
  return <RatesInventory propertyId={propertyId} />
}

/** Screen 035 also carries its own heading. */
export function PackagesPromotionsScreen() {
  const propertyId = useActivePropertyId()
  return <PackagesPromotions propertyId={propertyId} />
}
