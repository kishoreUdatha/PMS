#!/bin/sh
# Migrate every service's schema, in dependency order, then grant the runtime
# login. Run once per deploy, before any service starts -- the `migrate`
# service in docker-compose.yml, or by hand against any database.
#
# The ORDER is the point. Migrations are per service but not independent:
# finance's reference iam's tables, and booking-core's reference both
# (0023_commercial_accounts adds a foreign key to finance.folios). Each
# service used to migrate itself as its container started, so on a fresh
# database booking-core could reach 0023 before finance.folios existed, fail,
# and -- with no restart policy -- stay down. iam -> finance -> booking-core
# is the only order that works on an empty database.
#
# Concurrent runs are safe: each alembic env.py takes a Postgres advisory lock
# (chirala_common.locks.migration_lock), so a second runner waits and then
# finds nothing to do.
#
# Needs IAM_/FINANCE_/BOOKING_MIGRATION_DATABASE_URL (the schema owner) and
# APP_DB_USER / APP_DB_PASSWORD for the runtime login. SERVICES_ROOT points at
# the directory holding services/; it defaults to the image layout.
set -eu

ROOT="${SERVICES_ROOT:-/app}"

for svc in iam finance booking-core; do
    echo "migrate: $svc"
    (cd "$ROOT/services/$svc" && alembic upgrade head)
done

# After every migration, never between: it grants on the tables that exist,
# and default privileges cover only tables created later by the same owner.
# One database, one owner, so any service's owner URL will do.
python -m chirala_common.runtime_role --owner-url-env IAM_MIGRATION_DATABASE_URL
echo "migrate: done"
