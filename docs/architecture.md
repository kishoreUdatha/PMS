# Architecture

## Principles (from the schema blueprint)

1. **Multi-tenant.** One organization owns many properties. Every tenant table
   carries `organization_id`; property-level tables also carry `property_id`.
   Cross-table references use composite foreign keys so a child in property A can
   never reference a parent in property B (§1).
2. **ACID where it matters.** Booking, inventory, and finance share one PostgreSQL
   cluster and, on the critical paths, one transaction. This is required to make
   the concurrency and money invariants hold (§4, §6, §13).
3. **Events for the rest.** Independent domains communicate through the
   transactional outbox (`integration.outbox_events`, §10) published to NATS.
4. **Deny by default.** Authorization = active membership AND enabled module AND
   scoped role permission AND record relationship AND action limit (§2).

## Service map

| Service | Owns schemas | Screens (examples) |
|---|---|---|
| iam | iam | User Mgmt, Roles & Permission Matrix, Approval Queue, Audit Log, Property Settings |
| booking-core | property, booking | Room Rack, New Reservation, Reservation Calendar, Check-in, Rooms & Inventory, Rate Plans |
| finance (later) | finance | Folio, Payments, Night Audit, Invoice & Credit Note, Cashiering |
| operations (later) | operations | Housekeeping, Maintenance, Inventory/Purchasing |
| pos (later) | pos | Restaurant POS, KDS, Menu |
| distribution (later) | distribution | Channel Manager, Channel Connection & Mapping |
| engagement (later) | engagement | Guest CRM, Conversation Inbox, Loyalty |
| ai (later) | ai | AI Command Center, Voice Agent, Knowledge Base |

## The concurrency-critical path (booking-core)

Creating a hold/reservation for N nights (§4):

1. Begin one transaction.
2. `SELECT ... FOR UPDATE` on `room_type_inventory_days` for each requested
   (property_id, room_type_id, stay_date), locked in deterministic order.
3. Validate every night: `sellable = physical_capacity - out_of_service -
   held_units - reserved_units - allotment_units >= requested`. Overbooking
   allowance is zero initially.
4. Insert `reservation_units` / `inventory_claims`, increment counters, insert the
   hold, insert an `outbox_events` row — all in the same transaction.
5. Commit. Any single-night shortage rolls back the whole request (no partial
   allocation).

Room-level double-booking is additionally prevented by a GiST exclusion
constraint over active `room_calendar_entries` using `tstzrange` (§4).

These paths use explicit SQL (SQLAlchemy Core / text), **not** ORM lazy-loading,
so lock ordering and constraint behavior are exact and predictable.

## Why modular-core-first (not full service-per-DB on day one)

The schema explicitly recommends starting on one cluster (§1): "Separate
microservice databases are unnecessary initially." Splitting the ACID trio across
databases would force distributed transactions/sagas for the exact invariants the
§13 acceptance tests check (last-room race, allocation ≤ captured funds). Clean
module boundaries + the outbox let us extract services later without changing the
messaging contract.
