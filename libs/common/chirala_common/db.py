"""Database engine and session helpers.

Provides a SQLAlchemy engine/session factory and a helper to bind the tenant
context to the transaction. Per schema §12, the tenant/property context is set
server-side as a *transaction-local* setting (never client-provided globally),
so RLS policies can read it via ``current_setting('app.organization_id')``.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

#: How long a pooled connection may live before it is thrown away and
#: reopened, in seconds.
#:
#: Thirty minutes. Long enough that recycling costs nothing measurable -- a
#: service handling one request a second reopens a connection every few
#: thousand -- and short enough to sit under the idle timeouts that actually
#: sever connections in the middle: Docker's userland proxy, a cloud NAT
#: gateway (commonly 350s but often much longer), a load balancer, a firewall
#: state table. Any of those can drop a TCP connection without either end
#: being told.
DEFAULT_POOL_RECYCLE_SECONDS = 1800

#: Seconds a connection sits idle before the kernel starts probing the far
#: end, and how those probes are spaced.
#:
#: These are TCP keepalives, and they are the only thing that bounds the case
#: neither pooling setting can reach: a connection already checked out and in
#: use when its socket dies. Nothing arrives, nothing errors, and the read
#: blocks for as long as the operating system is willing to retransmit --
#: which on Linux defaults to around fifteen minutes, and on a connection idle
#: mid-transaction can be far longer.
#:
#: 30 + 10 x 5 means a dead peer is detected in roughly eighty seconds. That
#: is slow enough never to trouble a healthy but quiet connection, and fast
#: enough that a sweep, a request or a night audit fails and retries instead
#: of hanging until somebody notices.
DEFAULT_KEEPALIVE_IDLE_SECONDS = 30
KEEPALIVE_INTERVAL_SECONDS = 10
KEEPALIVE_COUNT = 5

#: Seconds to wait for a NEW connection to be established.
#:
#: The last unbounded wait. Keepalives protect a connection that is already
#: up; this protects the attempt to bring one up at all. Without it libpq
#: waits for the operating system to give up on the TCP handshake, which is
#: around two minutes on Linux and unbounded in the case that matters most --
#: a host that accepts the SYN and then goes silent, which is what a dead
#: container or a black-holing firewall looks like.
#:
#: Ten seconds is enormous for a database on the same network (the real
#: figure is single-digit milliseconds) and still leaves room for a container
#: that is up but briefly busy. A failure here is a clean error the caller can
#: retry rather than a request that never returns.
DEFAULT_CONNECT_TIMEOUT_SECONDS = 10

_ENV_RECYCLE = "DB_POOL_RECYCLE_SECONDS"
_ENV_KEEPALIVE = "DB_KEEPALIVE_IDLE_SECONDS"
_ENV_CONNECT_TIMEOUT = "DB_CONNECT_TIMEOUT_SECONDS"


def _env_int(name: str, default: int) -> int:
    """An integer from the environment, or the default if it is not usable.

    Read from the environment rather than a settings class because these are
    shared by four services with four settings classes, and because the one
    time anybody changes them will be while chasing a connection problem in a
    deployment they would rather not rebuild.

    A malformed value falls back and says so. Refusing to start over a typo in
    a tuning knob would turn a cosmetic mistake into an outage.
    """
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        logging.getLogger(__name__).warning(
            "%s=%r is not a number; using %d", name, raw, default)
        return default


def _libpq_args(database_url: str, *, keepalive_idle: int,
                connect_timeout: int) -> dict:
    """The libpq connection parameters this deployment wants, if any.

    PostgreSQL only. These are libpq parameters and handing them to another
    driver is an immediate TypeError -- the test suites open engines against
    whatever URL they are given, so this stays a no-op rather than a trap for
    a future caller.

    Each setting is independently disableable by setting it to zero, so a
    deployment that needs one and not the other does not have to take both.
    """
    if not database_url.startswith(("postgresql", "postgres:")):
        return {}
    args: dict = {}
    if keepalive_idle > 0:
        args["keepalives"] = 1
        args["keepalives_idle"] = keepalive_idle
        args["keepalives_interval"] = KEEPALIVE_INTERVAL_SECONDS
        args["keepalives_count"] = KEEPALIVE_COUNT
    if connect_timeout > 0:
        # libpq silently raises anything under 2 to 2; saying so beats a
        # reader wondering why their 1 became something else.
        args["connect_timeout"] = max(2, connect_timeout)
    return args


def make_engine(database_url: str, echo: bool = False,
                pool_recycle: int | None = None,
                keepalive_idle: int | None = None,
                connect_timeout: int | None = None):
    """Create an engine that cannot wait forever on a connection.

    Four settings, each covering a case the others cannot, and together they
    close every unbounded wait between this process and Postgres:

    ``pool_pre_ping`` tests a pooled connection before handing it out, so one
    that died while idle produces a reconnect rather than an error in the
    caller's query.

    ``pool_recycle`` discards a connection once it reaches this age, whether
    or not anything is wrong with it. The ping is itself a round trip: if the
    connection was severed silently -- by a NAT gateway, a firewall state
    table, a container network hiccup -- the ping does not fail fast, it
    *blocks*. Recycling means an old connection is never reached for.

    **TCP keepalives** cover what neither pooling setting can: a connection
    already checked out and in use when its socket dies, where no pool setting
    is consulted again until it goes back. The kernel probes and tears it
    down, so a read on a dead socket fails in about eighty seconds instead of
    blocking for as long as the OS will retransmit.

    **connect_timeout** bounds the remaining case, opening a new connection to
    a host that is gone.

    Override any of them per call, or on the deployment with
    ``DB_POOL_RECYCLE_SECONDS``, ``DB_KEEPALIVE_IDLE_SECONDS`` and
    ``DB_CONNECT_TIMEOUT_SECONDS``. A negative recycle disables recycling,
    which is SQLAlchemy's own convention for "never"; zero or less disables
    keepalives or the connect timeout.
    """
    recycle = (_env_int(_ENV_RECYCLE, DEFAULT_POOL_RECYCLE_SECONDS)
               if pool_recycle is None else pool_recycle)
    idle = (_env_int(_ENV_KEEPALIVE, DEFAULT_KEEPALIVE_IDLE_SECONDS)
            if keepalive_idle is None else keepalive_idle)
    timeout = (_env_int(_ENV_CONNECT_TIMEOUT, DEFAULT_CONNECT_TIMEOUT_SECONDS)
               if connect_timeout is None else connect_timeout)
    return create_engine(
        database_url,
        echo=echo,
        pool_pre_ping=True,
        pool_recycle=recycle,
        connect_args=_libpq_args(database_url, keepalive_idle=idle,
                                 connect_timeout=timeout),
        future=True,
    )


def make_session_factory(engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@contextmanager
def tenant_session(
    session_factory: sessionmaker[Session],
    organization_id: str,
    property_id: str | None = None,
) -> Iterator[Session]:
    """Open a session with transaction-local tenant context for RLS.

    Uses ``set_config(..., is_local => true)`` so the setting is scoped to the
    current transaction and cannot leak across pooled connections (§12).
    """
    session = session_factory()
    try:
        bind_tenant_context(session, organization_id=organization_id,
                            property_id=property_id)
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def bind_tenant_context(
    session: Session,
    *,
    organization_id=None,
    property_id=None,
    user_id=None,
    is_service: bool = False,
) -> None:
    """Publish who this transaction acts for, for row-level security.

    Every setting is transaction-local (``set_config(..., true)``), so it ends
    with the transaction and cannot leak to the next request that borrows the
    same pooled connection (§12). Values come from the authenticated caller,
    never from anything the client sent -- the permission dependency calls
    this only after the tenancy guard has vetted the request.

    An empty string means "not known", which a policy must treat as matching
    nothing rather than everything.
    """
    session.execute(
        text(
            "SELECT set_config('app.organization_id', :org, true),"
            "       set_config('app.property_id', :prop, true),"
            "       set_config('app.user_id', :uid, true),"
            "       set_config('app.is_service', :svc, true),"
            "       set_config('app.system', 'off', true),"
            "       set_config('app.identity_subject', '', true),"
            "       set_config('app.property_code', '', true)"
        ),
        {
            "org": str(organization_id or ""),
            "prop": str(property_id or ""),
            "uid": str(user_id or ""),
            "svc": "on" if is_service else "off",
        },
    )


def system_context(session: Session, *, reason: str) -> None:
    """Let this transaction see every tenant's rows, for one stated reason.

    Row-level security shows a transaction only its own tenant's rows. A few
    jobs are above the tenant boundary by nature and need all of them: working
    out which tenant a payment callback belongs to before anything about it is
    trusted, the night-audit sweep that lists every property, and platform
    administration. They call this, and nothing else should.

    Transaction-local like the tenant binding, so it ends at commit or
    rollback, and ``bind_tenant_context`` switches it off again -- a job that
    has found its tenant narrows itself before touching that tenant's money.
    Every use is logged with its reason.
    """
    import logging

    if not reason or not reason.strip():
        raise ValueError("system_context needs a reason")
    logging.getLogger(__name__).info("system database context: %s", reason)
    session.execute(
        text(
            "SELECT set_config('app.system', 'on', true),"
            "       set_config('app.system_reason', :reason, true),"
            "       set_config('app.organization_id', '', true),"
            "       set_config('app.property_id', '', true),"
            "       set_config('app.identity_subject', '', true),"
            "       set_config('app.property_code', '', true)"
        ),
        {"reason": reason[:200]},
    )


def identity_context(session: Session, *, subject: str) -> None:
    """Let this transaction read one subject's own identity, and nothing else.

    A request has to learn who is calling before it can know whose data they
    may touch. While it does, row-level security allows exactly that subject's
    user row, their memberships, and the organisations, properties and roles
    those memberships belong to. The caller resolution replaces this with the
    caller's tenant as soon as the lookup is done.
    """
    session.execute(
        text(
            "SELECT set_config('app.identity_subject', :subject, true),"
            "       set_config('app.system', 'off', true),"
            "       set_config('app.organization_id', '', true),"
            "       set_config('app.property_id', '', true),"
            "       set_config('app.property_code', '', true)"
        ),
        {"subject": subject or ""},
    )


def property_code_context(session: Session, *, code: str) -> None:
    """Let this transaction read the one property whose public code this is.

    For the booking engine, which is addressed by property code and has no
    login: the code in the URL is the only thing that can say which tenant a
    guest is booking with. Once the property is found, the route binds that
    property's tenant.
    """
    session.execute(
        text(
            "SELECT set_config('app.property_code', :code, true),"
            "       set_config('app.system', 'off', true),"
            "       set_config('app.identity_subject', '', true),"
            "       set_config('app.organization_id', '', true)"
        ),
        {"code": code or ""},
    )
