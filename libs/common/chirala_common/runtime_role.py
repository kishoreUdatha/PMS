"""Give the services a database login that row-level security can hold.

Schema blueprint §12: the runtime role must not own tables and must not have
BYPASSRLS. Until now every service connected as the migration owner -- a
superuser that owns every table -- so any row-level policy would have been
skipped for every query the application ever ran. This is the first stage of
fixing that: the application gets its own login, and the owner is kept for
migrations only.

Run as the schema owner, after migrations and before the app starts::

    alembic upgrade head \\
      && python -m chirala_common.runtime_role --owner-url-env FINANCE_MIGRATION_DATABASE_URL \\
      && uvicorn ...

What it does, idempotently and under an advisory lock so three services
starting together do not race each other's GRANTs:

* creates or updates the runtime login (``APP_DB_USER``, default ``pms_app``)
  as ``NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE``, with the password in
  ``APP_DB_PASSWORD``;
* grants it data access -- SELECT, INSERT, UPDATE, DELETE -- on every table in
  the application schemas, sequence use, and function execution; never
  ownership, never DDL;
* sets default privileges, so tables a later migration creates are granted
  the same way without anyone remembering to;
* makes each schema's ``alembic_version`` read-only to it, so the app cannot
  rewrite which migrations have run.

If the owner URL variable is not set, it does nothing and says so. A developer
running a service straight from the host keeps working as before.
"""

from __future__ import annotations

import argparse
import os
import re
import sys

from sqlalchemy import create_engine, text

SCHEMAS = ("iam", "property", "booking", "operations", "finance",
           "distribution", "pos", "engagement", "ai", "integration", "tenancy")

#: Role names are spliced into DDL, which cannot take bind parameters, so
#: they are held to plain identifiers first.
_IDENT = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def _ident(name: str) -> str:
    if not _IDENT.match(name):
        raise ValueError(f"Not a safe role or schema name: {name!r}")
    return f'"{name}"'


def ensure_runtime_role(owner_url: str, role: str, password: str) -> dict:
    """Create or update the runtime login and its grants. Returns a summary."""
    if not password:
        raise SystemExit(
            "APP_DB_PASSWORD is not set. The runtime database login needs a "
            "password; refusing to create one without it.")
    role_q = _ident(role)
    engine = create_engine(owner_url, future=True)
    with engine.begin() as conn:
        conn.execute(text("SELECT pg_advisory_xact_lock(hashtext('chirala:runtime-role'))"))
        owner = conn.execute(text("SELECT current_user")).scalar()
        if owner == role:
            raise SystemExit(
                "The owner URL connects as the runtime role itself. Migrations "
                "and grants must run as the schema owner.")
        owner_q = _ident(owner)

        # The password travels as a bind parameter into a transaction-local
        # setting, and is quoted by Postgres itself -- never pasted into SQL.
        conn.execute(text("SELECT set_config('chirala.runtime_password', :pw, true)"),
                     {"pw": password})
        conn.execute(text(f"""
            DO $$
            BEGIN
              IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{role}') THEN
                EXECUTE 'CREATE ROLE ' || quote_ident('{role}')
                     || ' LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE PASSWORD '
                     || quote_literal(current_setting('chirala.runtime_password'));
              ELSE
                EXECUTE 'ALTER ROLE ' || quote_ident('{role}')
                     || ' LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE PASSWORD '
                     || quote_literal(current_setting('chirala.runtime_password'));
              END IF;
            END
            $$
        """))

        granted = []
        for schema in SCHEMAS:
            if conn.execute(text("SELECT 1 FROM pg_namespace WHERE nspname = :s"),
                            {"s": schema}).first() is None:
                continue
            s = _ident(schema)
            for stmt in (
                f"GRANT USAGE ON SCHEMA {s} TO {role_q}",
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {s} TO {role_q}",
                f"GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA {s} TO {role_q}",
                f"GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA {s} TO {role_q}",
                f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner_q} IN SCHEMA {s} "
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {role_q}",
                f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner_q} IN SCHEMA {s} "
                f"GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO {role_q}",
                f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner_q} IN SCHEMA {s} "
                f"GRANT EXECUTE ON FUNCTIONS TO {role_q}",
            ):
                conn.execute(text(stmt))
            if conn.execute(text("SELECT to_regclass(:t)"),
                            {"t": f"{schema}.alembic_version"}).scalar():
                conn.execute(text(
                    f"REVOKE INSERT, UPDATE, DELETE ON {s}.alembic_version FROM {role_q}"))
            granted.append(schema)

        attrs = conn.execute(text(
            "SELECT rolsuper, rolbypassrls, rolcanlogin FROM pg_roles WHERE rolname = :r"),
            {"r": role}).one()
        owns = conn.execute(text(
            "SELECT count(*) FROM pg_tables WHERE tableowner = :r"), {"r": role}).scalar()
        if attrs.rolsuper or attrs.rolbypassrls or owns:
            raise SystemExit(
                f"Runtime role {role} is not safe: superuser={attrs.rolsuper} "
                f"bypassrls={attrs.rolbypassrls} owns {owns} tables.")
    return {"role": role, "owner": owner, "schemas": granted,
            "login": attrs.rolcanlogin}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--owner-url-env", required=True,
                        help="Name of the environment variable holding the owner URL")
    args = parser.parse_args(argv)
    owner_url = os.environ.get(args.owner_url_env, "")
    if not owner_url:
        print(f"runtime_role: {args.owner_url_env} is not set; leaving database "
              "roles unchanged.")
        return 0
    summary = ensure_runtime_role(
        owner_url,
        os.environ.get("APP_DB_USER", "pms_app"),
        os.environ.get("APP_DB_PASSWORD", ""),
    )
    print(f"runtime_role: {summary['role']} ready (owner {summary['owner']}, "
          f"schemas {', '.join(summary['schemas'])})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
