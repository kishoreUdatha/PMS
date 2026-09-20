# Chirala Bay Resort — Property Management System (SaaS)

A multi-tenant resort/hotel Property Management System built as a Python/FastAPI
microservices monorepo on PostgreSQL, matching the design blueprint in
`Data Base Schema/Resort_PMS_Database_Design.md` and the 120 screen mockups in
`Chirala_Bay_Resort_PMS_120_Screens/`.

## Technology stack

| Layer | Technology |
|---|---|
| Language | Python 3.12 |
| Services | FastAPI |
| DB access | SQLAlchemy 2.0 (general) + raw SQL / asyncpg (inventory & finance core) |
| Migrations | Alembic |
| Database | PostgreSQL 16 (schema-per-service, shared cluster initially) |
| Events | NATS JetStream + transactional outbox |
| Cache / locks / jobs | Redis |
| Auth | Keycloak (OIDC) |
| Object storage | MinIO (S3-compatible) |
| Frontend | React + TypeScript + Vite + Tailwind + TanStack Query |
| Local infra | Docker Compose |

## Architecture

The three ACID-critical domains — **IAM**, **Booking + Inventory**, and **Finance** —
begin as clean modules sharing one PostgreSQL cluster so that inventory and money
stay transactionally correct (see schema §4 and §6). Genuinely independent domains
(notifications, AI/voice, channel sync, reporting) run as separate services and
communicate through the transactional outbox + NATS.

```
Web (React) ──▶ API Gateway / BFF ──▶ IAM service
                                   ├─▶ Booking-core service (property + booking + inventory)
                                   └─▶ Finance service (later)
                                          │ outbox events
                                          ▼
                                   NATS ──▶ independent services (notifications, AI, channel, reporting)
```

## Repository layout

```
.
├── services/
│   ├── iam/                 # Identity, orgs, properties, users, roles, permissions
│   └── booking-core/        # Property inventory, reservations, holds, room calendar
├── gateway/                 # API gateway / BFF (FastAPI)
├── libs/
│   └── common/              # Shared config, DB, tenant context, auth, outbox
├── frontend/                # React + TS + Vite + Tailwind app shell
├── infra/                   # Local infra config (keycloak realm, etc.)
├── docs/                    # Architecture notes
├── docker-compose.yml       # Local dev infrastructure + services
├── .env.example
└── Makefile
```

## Quick start (local)

Prerequisites: Docker Desktop, `uv` (Python), Node 20+.

```bash
cp .env.example .env
docker compose up -d postgres redis nats minio keycloak   # infrastructure
make migrate                                               # run DB migrations
docker compose up --build iam booking-core gateway         # services
cd frontend && npm install && npm run dev                  # frontend
```

See `docs/` for module details and the schema blueprint for the full data model.

## Troubleshooting

If `docker compose up` fails with `port is already allocated`, another service on
your machine is using one of the default host ports (5432 Postgres, 6379 Redis,
4222 NATS, 9000/9001 MinIO, 8080 Keycloak, 8000/8001/8002 services, 5173
frontend). Override the conflicting host-side port mapping in a local
`docker-compose.override.yml`, e.g.:

```yaml
services:
  postgres:
    ports: ["5544:5432"]
```

The scaffold was verified end-to-end against an isolated Postgres on host port
5544: both Alembic migrations apply (including the GiST exclusion constraint) and
the concurrency acceptance tests pass (two simultaneous last-room bookings yield
exactly one success; a single-night shortage rolls back the whole request).

## Status

Scaffolding in progress. See `Chirala_Bay_Resort_PMS_Development_Tracker.xlsx`
and the in-repo task plan for progress across the 120 screens.
