import { Navigate, Outlet, Route, Routes } from 'react-router-dom'
import { OverlayHost } from './components/AskDialog'
import AppLayout from './components/AppLayout'
import Dashboard from './pages/Dashboard'
import NewReservation from './pages/NewReservation'
import GroupBlocks from './pages/GroupBlocks'
import OtaActions from './pages/OtaActions'
import Reservations from './pages/Reservations'
import ReservationCalendar from './pages/ReservationCalendar'
import Guests from './pages/Guests'
import AppSearch from './pages/Search'
import GuestProfile from './pages/GuestProfile'
import RoomsInventory from './pages/RoomsInventory'
import RoomEditor from './pages/RoomEditor'
import RoomStatusHistory from './pages/RoomStatusHistory'
import RoomDetails from './pages/RoomDetails'
import TaxCharges from './pages/TaxCharges'
import PaymentSettings from './pages/PaymentSettings'
import SalesChannelsPage from './pages/SalesChannelsPage'
import ChannelPartners from './pages/ChannelPartners'
import AddPartner from './pages/AddPartner'
import EditChannelPartner from './pages/EditChannelPartner'
import RateRules from './pages/RateRules'
import RateRuleEditor from './pages/RateRuleEditor'
import {
  RoomTypesScreen, AmenitiesScreen, BuildingsFloorsScreen, RoomBlocksScreen,
  RatePlansScreen, RatesInventoryScreen, PackagesPromotionsScreen,
} from './pages/PropertyScreens'
import CheckoutFolio from './pages/CheckoutFolio'
import GuestCheckIn from './pages/GuestCheckIn'
import RoomRack from './pages/RoomRack'
import CommercialAccounts from './pages/CommercialAccounts'
import GuestOrders from './pages/GuestOrders'
import ServiceMenu from './pages/ServiceMenu'
import BookingAttributes from './pages/BookingAttributes'
import Enquiries from './pages/Enquiries'
import ReservationDetail from './pages/ReservationDetail'
import Invoices from './pages/Invoices'
import InvoiceDetail from './pages/InvoiceDetail'
import InvoiceSettings from './pages/InvoiceSettings'
import GuestCheckOut from './pages/GuestCheckOut'
import RoomMove from './pages/RoomMove'
import ModifyReservation from './pages/ModifyReservation'
import EditReservation from './pages/EditReservation'
import NoShowProcessing, { NoShowList } from './pages/NoShowProcessing'
import DepositSchedule from './pages/DepositSchedule'
import Housekeeping from './pages/Housekeeping'
import Cashiering from './pages/Cashiering'
import DayBook from './pages/DayBook'
import FormCRegister from './pages/FormCRegister'
import FolioAdjustment from './pages/FolioAdjustment'
import PaymentReversal from './pages/PaymentReversal'
import NightAudit from './pages/NightAudit'
import NightAuditHistory from './pages/NightAuditHistory'
import PropertySettings from './pages/PropertySettings'
import UserManagement from './pages/UserManagement'
import InviteUser from './pages/InviteUser'
import RolesMatrix from './pages/RolesMatrix'
import Approvals from './pages/Approvals'
import AuditLog from './pages/AuditLog'
import Billing from './pages/Billing'
import Login from './pages/Login'
import SetPassword from './pages/SetPassword'
import Placeholder from './pages/Placeholder'
import LedgerReportScreen from './pages/LedgerReport'
import ReportsCatalog from './pages/ReportsCatalog'
import BackOfficeReport from './pages/BackOfficeReport'
import WorkOrders from './pages/WorkOrders'
import ExpenseVouchers from './pages/ExpenseVouchers'
import UnitOwners from './pages/UnitOwners'
import {
  OnboardingEntry, OnboardingGate, OnboardingConnections, OnboardingGoLive,
} from './pages/Onboarding'
import { OnboardingAccount, OnboardingProperty } from './pages/OnboardingSetup'
import { OnboardingStructure, OnboardingRooms } from './pages/OnboardingEstate'
import { OnboardingTeam } from './pages/OnboardingTeam'
import { OnboardingImport } from './pages/OnboardingImport'
import { OnboardingRates, OnboardingBilling } from './pages/OnboardingMoney'
import ProtectedRoute from './auth/ProtectedRoute'
import PlatformRoute from './platform/PlatformRoute'
import PlatformLayout from './platform/PlatformLayout'
import PlatformLogin from './platform/PlatformLogin'
import PlatformOverview from './platform/screens/Overview'
import PlatformOtaActions from './platform/screens/OtaActions'
import PlatformTenants from './platform/screens/Tenants'
import PlatformTenantDetail from './platform/screens/TenantDetail'
import PlatformProperties from './platform/screens/Properties'
import PlatformUsers from './platform/screens/Users'
import PlatformSearch from './platform/screens/Search'
import PlatformRoles from './platform/screens/Roles'
import PlatformProviders from './platform/screens/Providers'
import PlatformDomains from './platform/screens/Domains'
import PlatformMessaging from './platform/screens/Messaging'
import PlatformSupport from './platform/screens/Support'
import PlatformSupportTicket from './platform/screens/SupportTicket'
import PlatformRecovery from './platform/screens/Recovery'
import PlatformSettingsScreen from './platform/screens/PlatformSettings'
import PlatformAnalytics from './platform/screens/Analytics'
import PlatformChannels from './platform/screens/Channels'
import PlatformOperations from './platform/screens/Operations'
import PlatformPropertyOverview from './platform/screens/PropertyOverview'
import PlatformOnboarding from './platform/screens/Onboarding'
import PlatformSecurity from './platform/screens/Security'
import PlatformSubscriptions from './platform/screens/Subscriptions'
import PlatformPlans from './platform/screens/Plans'
import PlatformInvoices from './platform/screens/Invoices'
import {
  BusinessDates as PlatformBusinessDates, Audit as PlatformAudit,
  Permissions as PlatformPermissions, Admins as PlatformAdmins,
} from './platform/screens/Rest'

export default function App() {
  return (
    <>
      {/* One host for the whole application. Both tiers' screens call
          askReason/askText/notify, which render through it. */}
      <OverlayHost />
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
    </>
  )
}
