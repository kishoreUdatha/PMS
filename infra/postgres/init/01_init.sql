-- Chirala Bay PMS — database bootstrap
-- Runs once on first container start (docker-entrypoint-initdb.d).
-- Creates required extensions and the per-service schemas.
-- Per schema blueprint §1: schemas iam, property, booking, operations, finance,
-- distribution, pos, engagement, ai, integration on one cluster initially.

-- Extensions required by the design:
--   btree_gist  -> GiST exclusion constraints with UUID equality + range overlap (§4)
--   pgcrypto    -> gen_random_uuid() for UUID primary keys (§1)
CREATE EXTENSION IF NOT EXISTS btree_gist;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Logical schemas (one per domain/service).
CREATE SCHEMA IF NOT EXISTS iam;
CREATE SCHEMA IF NOT EXISTS property;
CREATE SCHEMA IF NOT EXISTS booking;
CREATE SCHEMA IF NOT EXISTS operations;
CREATE SCHEMA IF NOT EXISTS finance;
CREATE SCHEMA IF NOT EXISTS distribution;
CREATE SCHEMA IF NOT EXISTS pos;
CREATE SCHEMA IF NOT EXISTS engagement;
CREATE SCHEMA IF NOT EXISTS ai;
CREATE SCHEMA IF NOT EXISTS integration;

-- Runtime application role (schema §12): must NOT own tables and must NOT have
-- BYPASSRLS. RLS is enforced as defense-in-depth. In local dev the migration
-- owner is the default 'pms' superuser-less role; a hardened deploy separates
-- the DDL owner from this runtime role. Placeholder for parity with production.
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'pms_app') THEN
    CREATE ROLE pms_app NOLOGIN NOBYPASSRLS;
  END IF;
END
$$;

-- An abandoned transaction must not be able to take the property offline.
--
-- A request that dies mid-flight -- a closed browser tab, a dropped
-- connection, a client timeout -- can leave its transaction open and its
-- locks held. Postgres will wait for that client forever. It happened here:
-- one booking-core connection sat `idle in transaction` for four minutes
-- after an audit-event insert, the channel-push sweep blocked behind it,
-- and within seconds every request was queued behind that. The dashboard
-- and the reservations list both timed out; the desk would have seen a
-- hotel that had simply stopped.
--
-- Sixty seconds, not thirty: this only kills a session sitting IDLE inside
-- a transaction, never one running a query, but a batch job doing real
-- work between two statements is idle by this definition. The night audit
-- and the sweeps pause for a moment per row, not for a minute, so sixty
-- clears the abandoned without reaching anything legitimate.
--
-- Set on the runtime role only. Migrations run as the owner and are
-- allowed to take as long as they take.
ALTER ROLE pms_app SET idle_in_transaction_session_timeout = '60s';

GRANT USAGE ON SCHEMA iam, property, booking, operations, finance,
  distribution, pos, engagement, ai, integration TO pms_app;
