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

GRANT USAGE ON SCHEMA iam, property, booking, operations, finance,
  distribution, pos, engagement, ai, integration TO pms_app;
