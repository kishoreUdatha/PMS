/**
 * What this deployment calls itself.
 *
 * "Chirala Bay PMS" was written into the onboarding wizard's own copy and
 * into the browser tab title, so every property signing up — in Kerala, in
 * Rajasthan, anywhere — was told the product was named after the first hotel
 * that used it. The same mistake as the brand logo that showed one resort's
 * mark to every tenant.
 *
 * The backend has settled this already: `settings.platform_name` is what the
 * invitation and welcome emails say. This is its counterpart on the screen,
 * read from the environment so a deployment sets it once, with a neutral
 * default so an unconfigured install names nobody in particular rather than
 * naming somebody else's hotel.
 */
export const PLATFORM_NAME: string =
  (import.meta.env.VITE_PLATFORM_NAME as string | undefined)?.trim()
  || 'your property management system'

/** The browser tab. The property's own name where we know it, because that is
 *  what distinguishes one open tab from another for the person using it. */
export function documentTitle(propertyName?: string | null): string {
  return propertyName ? `${propertyName} — PMS` : 'Property Management System'
}
