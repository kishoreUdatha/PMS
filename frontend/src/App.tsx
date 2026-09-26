import { lazy, Suspense } from 'react'
import { Navigate, Outlet, Route, Routes } from 'react-router-dom'
import { OverlayHost } from './components/AskDialog'
import AppLayout from './components/AppLayout'
import ProtectedRoute from './auth/ProtectedRoute'
import PlatformRoute from './platform/PlatformRoute'
import PlatformLayout from './platform/PlatformLayout'
import PageSpinner from './components/PageSpinner'

// Every screen is its own chunk, fetched when first visited. The shell
// (layouts, guards, the overlay host) stays in the entry bundle so the frame
// paints at once; AppLayout and PlatformLayout hold their own Suspense so
// only the content area waits while a screen loads.
const Dashboard = lazy(() => import('./pages/Dashboard'))
const NewReservation = lazy(() => import('./pages/NewReservation'))
const GroupBlocks = lazy(() => import('./pages/GroupBlocks'))
const OtaActions = lazy(() => import('./pages/OtaActions'))
const Reservations = lazy(() => import('./pages/Reservations'))
const ReservationCalendar = lazy(() => import('./pages/ReservationCalendar'))
const Guests = lazy(() => import('./pages/Guests'))
const AppSearch = lazy(() => import('./pages/Search'))
const GuestProfile = lazy(() => import('./pages/GuestProfile'))
const RoomsInventory = lazy(() => import('./pages/RoomsInventory'))
const RoomEditor = lazy(() => import('./pages/RoomEditor'))
const RoomStatusHistory = lazy(() => import('./pages/RoomStatusHistory'))
const RoomDetails = lazy(() => import('./pages/RoomDetails'))
const TaxCharges = lazy(() => import('./pages/TaxCharges'))
const PaymentSettings = lazy(() => import('./pages/PaymentSettings'))
const SalesChannelsPage = lazy(() => import('./pages/SalesChannelsPage'))
const ChannelPartners = lazy(() => import('./pages/ChannelPartners'))
const AddPartner = lazy(() => import('./pages/AddPartner'))
const EditChannelPartner = lazy(() => import('./pages/EditChannelPartner'))
const RateRules = lazy(() => import('./pages/RateRules'))
const RateRuleEditor = lazy(() => import('./pages/RateRuleEditor'))
const RoomTypesScreen = lazy(() => import('./pages/PropertyScreens').then((m) => ({ default: m.RoomTypesScreen })))
const AmenitiesScreen = lazy(() => import('./pages/PropertyScreens').then((m) => ({ default: m.AmenitiesScreen })))
const BuildingsFloorsScreen = lazy(() => import('./pages/PropertyScreens').then((m) => ({ default: m.BuildingsFloorsScreen })))
const RoomBlocksScreen = lazy(() => import('./pages/PropertyScreens').then((m) => ({ default: m.RoomBlocksScreen })))
const RatePlansScreen = lazy(() => import('./pages/PropertyScreens').then((m) => ({ default: m.RatePlansScreen })))
const RatesInventoryScreen = lazy(() => import('./pages/PropertyScreens').then((m) => ({ default: m.RatesInventoryScreen })))
const PackagesPromotionsScreen = lazy(() => import('./pages/PropertyScreens').then((m) => ({ default: m.PackagesPromotionsScreen })))
const CheckoutFolio = lazy(() => import('./pages/CheckoutFolio'))
const GuestCheckIn = lazy(() => import('./pages/GuestCheckIn'))
const RoomRack = lazy(() => import('./pages/RoomRack'))
const CommercialAccounts = lazy(() => import('./pages/CommercialAccounts'))
const GuestOrders = lazy(() => import('./pages/GuestOrders'))
const ServiceMenu = lazy(() => import('./pages/ServiceMenu'))
const BookingAttributes = lazy(() => import('./pages/BookingAttributes'))
const Enquiries = lazy(() => import('./pages/Enquiries'))
const ReservationDetail = lazy(() => import('./pages/ReservationDetail'))
const Invoices = lazy(() => import('./pages/Invoices'))
const InvoiceDetail = lazy(() => import('./pages/InvoiceDetail'))
const InvoiceSettings = lazy(() => import('./pages/InvoiceSettings'))
const GuestCheckOut = lazy(() => import('./pages/GuestCheckOut'))
const RoomMove = lazy(() => import('./pages/RoomMove'))
const ModifyReservation = lazy(() => import('./pages/ModifyReservation'))
const EditReservation = lazy(() => import('./pages/EditReservation'))
const NoShowProcessing = lazy(() => import('./pages/NoShowProcessing'))
const NoShowList = lazy(() => import('./pages/NoShowProcessing').then((m) => ({ default: m.NoShowList })))
const DepositSchedule = lazy(() => import('./pages/DepositSchedule'))
const Housekeeping = lazy(() => import('./pages/Housekeeping'))
const Cashiering = lazy(() => import('./pages/Cashiering'))
const DayBook = lazy(() => import('./pages/DayBook'))
const FormCRegister = lazy(() => import('./pages/FormCRegister'))
const FolioAdjustment = lazy(() => import('./pages/FolioAdjustment'))
const PaymentReversal = lazy(() => import('./pages/PaymentReversal'))
const NightAudit = lazy(() => import('./pages/NightAudit'))
const NightAuditHistory = lazy(() => import('./pages/NightAuditHistory'))
const PropertySettings = lazy(() => import('./pages/PropertySettings'))
const UserManagement = lazy(() => import('./pages/UserManagement'))
const InviteUser = lazy(() => import('./pages/InviteUser'))
const RolesMatrix = lazy(() => import('./pages/RolesMatrix'))
const Approvals = lazy(() => import('./pages/Approvals'))
const AuditLog = lazy(() => import('./pages/AuditLog'))
const Billing = lazy(() => import('./pages/Billing'))
const Login = lazy(() => import('./pages/Login'))
const SetPassword = lazy(() => import('./pages/SetPassword'))
const Placeholder = lazy(() => import('./pages/Placeholder'))
const LedgerReportScreen = lazy(() => import('./pages/LedgerReport'))
const ReportsCatalog = lazy(() => import('./pages/ReportsCatalog'))
const BackOfficeReport = lazy(() => import('./pages/BackOfficeReport'))
const WorkOrders = lazy(() => import('./pages/WorkOrders'))
const ExpenseVouchers = lazy(() => import('./pages/ExpenseVouchers'))
const UnitOwners = lazy(() => import('./pages/UnitOwners'))
const OnboardingEntry = lazy(() => import('./pages/Onboarding').then((m) => ({ default: m.OnboardingEntry })))
const OnboardingGate = lazy(() => import('./pages/Onboarding').then((m) => ({ default: m.OnboardingGate })))
const OnboardingConnections = lazy(() => import('./pages/Onboarding').then((m) => ({ default: m.OnboardingConnections })))
const OnboardingGoLive = lazy(() => import('./pages/Onboarding').then((m) => ({ default: m.OnboardingGoLive })))
const OnboardingAccount = lazy(() => import('./pages/OnboardingSetup').then((m) => ({ default: m.OnboardingAccount })))
const OnboardingProperty = lazy(() => import('./pages/OnboardingSetup').then((m) => ({ default: m.OnboardingProperty })))
const OnboardingStructure = lazy(() => import('./pages/OnboardingEstate').then((m) => ({ default: m.OnboardingStructure })))
const OnboardingRooms = lazy(() => import('./pages/OnboardingEstate').then((m) => ({ default: m.OnboardingRooms })))
const OnboardingTeam = lazy(() => import('./pages/OnboardingTeam').then((m) => ({ default: m.OnboardingTeam })))
const OnboardingImport = lazy(() => import('./pages/OnboardingImport').then((m) => ({ default: m.OnboardingImport })))
const OnboardingRates = lazy(() => import('./pages/OnboardingMoney').then((m) => ({ default: m.OnboardingRates })))
const OnboardingBilling = lazy(() => import('./pages/OnboardingMoney').then((m) => ({ default: m.OnboardingBilling })))
const PlatformLogin = lazy(() => import('./platform/PlatformLogin'))
const PlatformOverview = lazy(() => import('./platform/screens/Overview'))
const PlatformOtaActions = lazy(() => import('./platform/screens/OtaActions'))
const PlatformTenants = lazy(() => import('./platform/screens/Tenants'))
const PlatformTenantDetail = lazy(() => import('./platform/screens/TenantDetail'))
const PlatformProperties = lazy(() => import('./platform/screens/Properties'))
const PlatformUsers = lazy(() => import('./platform/screens/Users'))
const PlatformSearch = lazy(() => import('./platform/screens/Search'))
const PlatformRoles = lazy(() => import('./platform/screens/Roles'))
const PlatformProviders = lazy(() => import('./platform/screens/Providers'))
const PlatformDomains = lazy(() => import('./platform/screens/Domains'))
const PlatformMessaging = lazy(() => import('./platform/screens/Messaging'))
const PlatformSupport = lazy(() => import('./platform/screens/Support'))
const PlatformSupportTicket = lazy(() => import('./platform/screens/SupportTicket'))
const PlatformRecovery = lazy(() => import('./platform/screens/Recovery'))
const PlatformSettingsScreen = lazy(() => import('./platform/screens/PlatformSettings'))
const PlatformAnalytics = lazy(() => import('./platform/screens/Analytics'))
const PlatformChannels = lazy(() => import('./platform/screens/Channels'))
const PlatformOperations = lazy(() => import('./platform/screens/Operations'))
const PlatformPropertyOverview = lazy(() => import('./platform/screens/PropertyOverview'))
const PlatformOnboarding = lazy(() => import('./platform/screens/Onboarding'))
const PlatformSecurity = lazy(() => import('./platform/screens/Security'))
const PlatformSubscriptions = lazy(() => import('./platform/screens/Subscriptions'))
const PlatformPlans = lazy(() => import('./platform/screens/Plans'))
const PlatformInvoices = lazy(() => import('./platform/screens/Invoices'))
const PlatformBusinessDates = lazy(() => import('./platform/screens/Rest').then((m) => ({ default: m.BusinessDates })))
const PlatformAudit = lazy(() => import('./platform/screens/Rest').then((m) => ({ default: m.Audit })))
const PlatformPermissions = lazy(() => import('./platform/screens/Rest').then((m) => ({ default: m.Permissions })))
const PlatformAdmins = lazy(() => import('./platform/screens/Rest').then((m) => ({ default: m.Admins })))

export default function App() {
  return (
    <>
      {/* One host for the whole application. Both tiers' screens call
          askReason/askText/notify, which render through it. */}
      <OverlayHost />
    <Suspense fallback={<PageSpinner full />}>
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/set-password" element={<SetPassword />} />

      {/* The platform console: a separate tier, so a separate shell, a
          separate sign-in and a separate guard. PlatformRoute admits only a
          session with is_platform, and ProtectedRoute turns those away from
          the tenant application — neither tier can wander into the other. */}
      <Route path="/platform/login" element={<PlatformLogin />} />
      <Route
        element={
          <PlatformRoute>
            <PlatformLayout />
          </PlatformRoute>
        }
      >
        <Route path="/platform" element={<PlatformOverview />} />
        <Route path="/platform/tenants" element={<PlatformTenants />} />
        <Route path="/platform/tenants/:orgId" element={<PlatformTenantDetail />} />
        <Route path="/platform/properties" element={<PlatformProperties />} />
        <Route path="/platform/properties/:propertyId" element={<PlatformPropertyOverview />} />
        <Route path="/platform/onboarding" element={<PlatformOnboarding />} />
        <Route path="/platform/security" element={<PlatformSecurity />} />
        <Route path="/platform/users" element={<PlatformUsers />} />
        <Route path="/platform/search" element={<PlatformSearch />} />
        <Route path="/platform/business-dates" element={<PlatformBusinessDates />} />
        <Route path="/platform/ota-actions" element={<PlatformOtaActions />} />
        <Route path="/platform/audit" element={<PlatformAudit />} />
        <Route path="/platform/permissions" element={<PlatformPermissions />} />
        <Route path="/platform/subscriptions" element={<PlatformSubscriptions />} />
        <Route path="/platform/plans" element={<PlatformPlans />} />
        <Route path="/platform/invoices" element={<PlatformInvoices />} />
        <Route path="/platform/roles" element={<PlatformRoles />} />
        <Route path="/platform/admins" element={<PlatformAdmins />} />
        <Route path="/platform/channels" element={<PlatformChannels />} />
        <Route path="/platform/integrations" element={<PlatformProviders />} />
        <Route path="/platform/booking-engine" element={<PlatformDomains />} />
        <Route path="/platform/messaging" element={<PlatformMessaging />} />
        <Route path="/platform/support" element={<PlatformSupport />} />
        <Route path="/platform/support/:ticketId" element={<PlatformSupportTicket />} />
        <Route path="/platform/operations" element={<PlatformOperations />} />
        <Route path="/platform/recovery" element={<PlatformRecovery />} />
        <Route path="/platform/settings" element={<PlatformSettingsScreen />} />
        <Route path="/platform/analytics" element={<PlatformAnalytics />} />
      </Route>
      {/* The wizard runs outside the app shell. It has its own step rail, and
          a property still being set up has nothing behind most of the main
          menu — offering Housekeeping and Invoices to someone who has not
          added a room yet is offering empty screens. */}
      <Route path="/onboarding/account" element={<OnboardingAccount />} />
      {/* The front door, deliberately outside ProtectedRoute. Somebody who has
          not signed up yet has no account to sign in with, so bouncing them to
          /login is a dead end; the entry decides for itself whether to resume
          a property or start one. */}
      <Route path="/onboarding" element={<OnboardingEntry />} />
      <Route
        element={<ProtectedRoute><Outlet /></ProtectedRoute>}
      >
        <Route path="/onboarding/property" element={<OnboardingProperty />} />
        <Route path="/onboarding/structure" element={<OnboardingStructure />} />
        <Route path="/onboarding/rooms" element={<OnboardingRooms />} />
        <Route path="/onboarding/rates" element={<OnboardingRates />} />
        <Route path="/onboarding/billing" element={<OnboardingBilling />} />
        <Route path="/onboarding/team" element={<OnboardingTeam />} />
        <Route path="/onboarding/import" element={<OnboardingImport />} />
        <Route path="/onboarding/connections" element={<OnboardingConnections />} />
        <Route path="/onboarding/golive" element={<OnboardingGoLive />} />
      </Route>
      <Route
        element={
          <ProtectedRoute>
            <AppLayout />
          </ProtectedRoute>
        }
      >
        {/* The dashboard is for a property that exists. Until setup
            is finished the wizard is the page — see OnboardingGate. */}
        <Route index element={
          <OnboardingGate><Dashboard /></OnboardingGate>} />
        <Route path="reservations/new" element={<NewReservation />} />
        <Route path="reservations/group-blocks" element={<GroupBlocks />} />
        <Route path="distribution/ota-actions" element={<OtaActions />} />
        <Route path="finance/invoices" element={<Invoices />} />
        <Route path="finance/invoices/:invoiceId" element={<InvoiceDetail />} />
        <Route path="finance/invoice-settings" element={<InvoiceSettings />} />
        <Route path="finance/expenses" element={<ExpenseVouchers />} />
        <Route path="finance/owners" element={<UnitOwners />} />
        {/* Front Desk was retired as a section: arrivals and departures
            are states of a booking, so they became tabs on Reservations.
            These redirects keep old links and bookmarks working. */}
        <Route path="front-desk" element={<Navigate to="/rooms/rack" replace />} />
        <Route path="front-desk/arrivals"
          element={<Navigate to="/reservations/list?tab=arrivals" replace />} />
        <Route path="rooms/rack" element={<RoomRack />} />
        <Route path="front-desk/check-in/:unitId" element={<GuestCheckIn />} />
        <Route path="front-desk/check-out/:unitId" element={<GuestCheckOut />} />
        <Route path="front-desk/room-move/:unitId" element={<RoomMove />} />
        <Route path="reservations" element={<ReservationCalendar />} />
        <Route path="reservations/list" element={<Reservations />} />
        <Route path="reservations/enquiries" element={<Enquiries />} />
        <Route path="reservations/no-show" element={<NoShowList />} />
        <Route path="reservations/no-show/:unitId" element={<NoShowProcessing />} />
        <Route path="reservations/:reservationId/folio" element={<CheckoutFolio />} />
        <Route path="reservations/:reservationId/deposits" element={<DepositSchedule />} />
        <Route path="reservations/:reservationId" element={<ReservationDetail />} />
        <Route path="reservations/:reservationId/edit" element={<EditReservation />} />
        <Route path="reservations/:reservationId/modify" element={<ModifyReservation />} />
        <Route path="rooms" element={<RoomsInventory />} />
        <Route path="rooms/new" element={<RoomEditor />} />
        <Route path="rooms/:roomId" element={<RoomDetails />} />
        <Route path="rooms/:roomId/edit" element={<RoomEditor />} />
        <Route path="rooms/:roomId/history" element={<RoomStatusHistory />} />
        <Route path="property/room-types" element={<RoomTypesScreen />} />
        <Route path="property/amenities" element={<AmenitiesScreen />} />
        <Route path="property/buildings" element={<BuildingsFloorsScreen />} />
        <Route path="rooms/blocks" element={<RoomBlocksScreen />} />
        {/* Group headers, not screens. The sidebar expands them rather than
            navigating, but they are reachable by URL and by clicking the group
            row -- and without these they resolved to a blank page. Each goes
            to its own first child, the way /rates already did. */}
        <Route path="rates" element={<Navigate to="/rates/plans" replace />} />
        <Route path="property" element={<Navigate to="/settings" replace />} />
        <Route path="admin" element={<Navigate to="/staff" replace />} />
        <Route path="rates/plans" element={<RatePlansScreen />} />
        <Route path="rates/calendar" element={<RatesInventoryScreen />} />
        <Route path="rates/packages" element={<PackagesPromotionsScreen />} />
        <Route path="rates/rules" element={<RateRules />} />
        <Route path="rates/rules/new" element={<RateRuleEditor />} />
        <Route path="rates/rules/:ruleId" element={<RateRuleEditor />} />
        <Route path="search" element={<AppSearch />} />
        <Route path="guests" element={<Guests />} />
        <Route path="guests/companies" element={<CommercialAccounts />} />
        <Route path="guests/:guestId" element={<GuestProfile />} />
        <Route path="reservations/sources" element={<BookingAttributes />} />
        <Route path="channels" element={<ChannelPartners />} />
        <Route path="channels/add" element={<AddPartner />} />
        <Route path="channels/:partnerId/edit"
          element={<EditChannelPartner />} />
        <Route path="housekeeping" element={<Housekeeping />} />
        <Route path="work-orders" element={<WorkOrders />} />
        <Route path="pos" element={<Placeholder title="Restaurant & POS" />} />
        <Route path="services" element={<GuestOrders />} />
        <Route path="services/menu" element={<ServiceMenu />} />
        <Route path="payments" element={<Cashiering />} />
        {/* The same screen on its Cashier Shifts panel. A route rather than a
            tab click so the sidebar's Cash Drawer has somewhere to point and
            the view can be linked to. */}
        <Route path="payments/drawer" element={<Cashiering />} />
        {/* Every folio movement for a day, not just the ones that crossed a
            counter. Its own screen rather than a third tab on Cashiering:
            the drawer figures on that tab row describe till money and mean
            nothing beside a list that includes charges. */}
        <Route path="payments/daybook" element={<DayBook />} />
        {/* Foreign guest reporting to the Bureau of Immigration. Under
            reservations rather than finance: it is a front-desk duty that
            starts at check-in and is measured in hours from it. */}
        <Route path="reservations/form-c" element={<FormCRegister />} />
        <Route path="payments/folios/:folioId/adjust" element={<FolioAdjustment />} />
        <Route path="payments/:paymentId/reverse" element={<PaymentReversal />} />
        <Route path="night-audit" element={<NightAudit />} />
        <Route path="night-audit/history" element={<NightAuditHistory />} />
        {/* The catalog lists every back-office report; the Hotel Ledger keeps
            its own screen and the rest render through one report screen. */}
        <Route path="reports" element={<ReportsCatalog />} />
        <Route path="reports/ledger" element={<LedgerReportScreen />} />
        <Route path="reports/:slug" element={<BackOfficeReport />} />
        <Route path="staff" element={<UserManagement />} />
        <Route path="admin/users" element={<UserManagement />} />
        <Route path="admin/users/invite" element={<InviteUser />} />
        <Route path="admin/users/:userId/edit" element={<InviteUser />} />
        <Route path="admin/roles" element={<RolesMatrix />} />
        <Route path="admin/approvals" element={<Approvals />} />
        <Route path="admin/audit" element={<AuditLog />} />
        <Route path="admin/billing" element={<Billing />} />
        <Route path="admin/taxes" element={<TaxCharges />} />
        <Route path="admin/payment-gateway" element={<PaymentSettings />} />
        <Route path="distribution/sales-channels" element={<SalesChannelsPage />} />
        {/* The screen used to live under /admin, which put "Administration"
            in its breadcrumb while the sidebar said Distribution. Moved, with
            the old address kept as a redirect so anything bookmarked or
            linked still lands. */}
        <Route path="admin/sales-channels"
          element={<Navigate to="/distribution/sales-channels" replace />} />
        <Route path="settings" element={<PropertySettings />} />
      </Route>
    </Routes>
    </Suspense>
    </>
  )
}
