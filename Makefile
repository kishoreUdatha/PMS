.PHONY: help infra up down migrate migrate-iam migrate-finance migrate-booking lint test test-guards test-platform check-platform-ops test-money test-flows fmt frontend

help:
	@echo "Targets:"
	@echo "  infra           Start infrastructure only (postgres, redis, nats, minio, keycloak)"
	@echo "  up              Build and start all services"
	@echo "  down            Stop everything"
	@echo "  migrate         Run all service migrations (iam -> finance -> booking-core)"
	@echo "  migrate-iam     Run IAM migrations"
	@echo "  migrate-finance Run finance migrations"
	@echo "  migrate-booking Run booking-core migrations"
	@echo "  lint            Ruff + mypy across services and libs"
	@echo "  test            Run pytest across services and the repo-wide tests"
	@echo "  test-guards     Tenant-isolation checks only (no database needed)"
	@echo "  test-platform   Platform/tenant tier separation (no database needed)"
	@echo "  check-platform-ops  Platform operations writes against the running stack (rolls back)"
	@echo "  test-money      Money invariants against the running stack"
	@echo "  test-flows      Operational flows end to end (creates test data)"
	@echo "  fmt             Format with ruff"
	@echo "  frontend        Install and run the frontend dev server"

# How the host reaches the running services, and the service token.
# Ports are ASKED OF COMPOSE, not assumed: docker-compose.override.yml
# exists so a machine whose 8002 is already taken can publish elsewhere,
# and these tests SKIP when they cannot connect -- which on a terminal
# reads exactly like passing. Sixteen money invariants sat skipped on a
# developer machine for that reason alone.
BOOKING_BASE_CMD = http://localhost:$$(docker compose port booking-core 8002 | sed 's/.*://')
FINANCE_BASE_CMD = http://localhost:$$(docker compose port finance 8003 | sed 's/.*://')
TOKEN_CMD = $$(grep -E "^SERVICE_TOKEN=" .env | cut -d= -f2- | tr -d '')

infra:
	docker compose up -d postgres redis nats minio keycloak

up:
	docker compose up --build

down:
	docker compose down

# In this order and as one recipe, not as prerequisites: finance's migrations
# reference iam's tables and booking-core's reference finance.folios, and
# `make -j` would run prerequisites side by side. This target used to skip
# finance altogether, so booking-core failed at 0023 on a fresh database.
# The compose `migrate` service (infra/migrate/migrate.sh) runs the same order.
migrate:
	cd services/iam && uv run alembic upgrade head
	cd services/finance && uv run alembic upgrade head
	cd services/booking-core && uv run alembic upgrade head

migrate-iam:
	cd services/iam && uv run alembic upgrade head

migrate-finance:
	cd services/finance && uv run alembic upgrade head

migrate-booking:
	cd services/booking-core && uv run alembic upgrade head

lint:
	uv run ruff check services libs gateway
	uv run mypy services libs gateway

fmt:
	uv run ruff format services libs gateway

# `tests/` as well as `services/`: the repo-wide suite holds the tenant
# isolation checks, and leaving it out is how they would stop being run.
test:
	uv run pytest services tests

# The isolation checks alone. They parse the source rather than calling it, so
# unlike the rest of the suite they need no database and never skip -- which is
# the whole point of them. Cheap enough to run on every commit.
test-guards:
	uv run pytest tests/test_tenant_guards.py

# The platform tier and the tenant tier, kept apart, plus the rules the
# platform's own operations console rests on -- a credential that never comes
# back, an approver who is never the requester, a policy on every table. Parses
# source like test-guards does, so it needs no database and never skips. Run it
# with test-guards: between them they are the whole of the isolation story.
test-platform:
	uv run pytest tests/test_platform_separation.py tests/test_platform_operations.py

# The invariants every money screen has to keep, checked against the RUNNING
# services -- which is where the defects lived. Brings the stack up first,
# because a skipped test protects nothing and this one skips without it.
# REQUIRE_STACK=1 makes that skip a failure: if the stack came up but the
# tests still cannot reach it, the target goes red instead of green.
test-money: up
	REQUIRE_STACK=1 BOOKING_BASE=$(BOOKING_BASE_CMD) FINANCE_BASE=$(FINANCE_BASE_CMD) SERVICE_TOKEN=$(TOKEN_CMD) uv run pytest tests/test_money_invariants.py -v

# The platform operations console against the RUNNING stack: every handler
# called for real, every refusal checked, and the whole thing rolled back so
# the database ends as it started. Runs inside the iam container because it
# imports the service.
check-platform-ops: up
	docker compose cp scripts/check_platform_operations.py iam:/app/check.py
	docker compose exec -T iam python /app/check.py

# The flows a front desk performs, driven through the real endpoints in the
# real order. CREATES DATA: bookings, folios, payments and refunds, each
# tagged with a per-run marker. Posted folio entries are immutable by design,
# so this leaves a trail -- point it at a scratch property, not a tenant with
# paying guests.
test-flows: up
	REQUIRE_STACK=1 BOOKING_BASE=$(BOOKING_BASE_CMD) FINANCE_BASE=$(FINANCE_BASE_CMD) SERVICE_TOKEN=$(TOKEN_CMD) uv run pytest tests/test_operational_flows.py -v

frontend:
	cd frontend && npm install && npm run dev
