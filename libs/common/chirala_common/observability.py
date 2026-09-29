"""Request IDs, one log format, background-loop heartbeats and ``/ready``.

Four services, and until now no way to follow one request through them: a
booking made at the gateway, confirmed by booking-core, billed by finance
left three unrelated log lines. ``settings.log_level`` existed and nothing
read it, and ``/health`` answered "ok" from a process whose database had gone
away. This module is the small, dependency-free fix for all three.

Wire it with one call per app::

    install_observability(app, service="finance", engine=engine,
                          log_level=settings.log_level)

**Request IDs.** An incoming ``X-Request-ID`` is kept (so the gateway's id,
or a load balancer's, follows the request downstream); otherwise one is made.
It is written back into the request's own headers -- which is what makes the
gateway, which forwards request headers, pass it on without further code --
returned on the response, and put on every log record made while handling the
request, including in worker threads (``asyncio.to_thread`` copies context).

**Logging.** Plain text, one format, request id on every line::

    2026-09-26 10:00:00,123 INFO booking_core.x [3f2a...] message

Plain rather than JSON on purpose: JSON is a change to every consumer of
these logs at once, and the id is what was missing. The level comes from
``settings.log_level`` (``LOG_LEVEL``).

**Readiness.** ``/health`` stays liveness -- "the process is up" -- so an
orchestrator does not restart a healthy process because Postgres blinked.
``/ready`` is "send me traffic": it runs ``SELECT 1`` and answers 503 if that
fails. See :func:`install_observability` for what else it reports.

Follow-ups deliberately not done here, because each is a new dependency and a
deployment decision: Sentry for exceptions, Prometheus metrics, JSON logs via
structlog (already a dependency, unused).
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
import uuid
from contextvars import ContextVar
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_ID_HEADER = "x-request-id"

#: The current request's id; ``-`` outside a request (startup, background
#: loops), so the log format never has a hole in it.
request_id: ContextVar[str] = ContextVar("request_id", default="-")

#: What an incoming id may look like. Anything else is replaced rather than
#: logged: this header is caller-controlled, and a value with a newline in it
#: would let a caller forge whole log lines.
_VALID_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s"
ACCESS_FORMAT = ('%(asctime)s %(levelname)s %(name)s [%(request_id)s] '
                 '%(client_addr)s "%(request_line)s" %(status_code)s')


class RequestIdMiddleware:
    """Pure ASGI, so it wraps streaming responses without buffering them."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = None
        for name, value in scope.get("headers", []):
            if name == b"x-request-id":
                incoming = value.decode("latin-1")
                break
        rid = incoming if incoming and _VALID_ID.match(incoming) else uuid.uuid4().hex
        if rid != incoming:
            # Into the request itself, so anything that forwards the caller's
            # headers downstream -- the gateway proxy -- forwards this one.
            scope = dict(scope)
            scope["headers"] = [(k, v) for k, v in scope.get("headers", [])
                                if k != b"x-request-id"] + [(b"x-request-id", rid.encode())]
        token = request_id.set(rid)

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                # Set, not appended: a proxied upstream response already
                # carries the same id, and two copies would confuse clients.
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = rid
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            request_id.reset(token)


class RequestIdFilter(logging.Filter):
    """Stamp every record with the current request id."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id.get()
        return True


def configure_logging(level: str | None = None) -> None:
    """One format and one level for this process's logs, idempotently.

    Runs when the app module is imported, which under uvicorn is AFTER uvicorn
    has installed its own handlers -- so those are re-formatted in place
    rather than replaced, and the service's own loggers (several log through
    ``uvicorn.error`` children so that their lines are not lost) come out in
    the same shape.
    """
    level_name = (level or os.environ.get("LOG_LEVEL") or "INFO").upper()
    numeric = logging.getLevelName(level_name)
    if not isinstance(numeric, int):
        numeric = logging.INFO
    formatter = logging.Formatter(LOG_FORMAT)
    id_filter = RequestIdFilter()

    root = logging.getLogger()
    if not root.handlers:
        root.addHandler(logging.StreamHandler())
    root.setLevel(numeric)

    for logger in (root, logging.getLogger("uvicorn"),
                   logging.getLogger("uvicorn.error"),
                   logging.getLogger("uvicorn.access")):
        for handler in logger.handlers:
            if not any(isinstance(f, RequestIdFilter) for f in handler.filters):
                handler.addFilter(id_filter)
            # uvicorn's access records carry their own fields (client_addr,
            # request_line, status_code) that only its AccessFormatter fills
            # in, so that class is kept and given the shared layout.
            if logger.name == "uvicorn.access" and handler.formatter is not None \
                    and "request_line" in (getattr(handler.formatter, "_fmt", "") or ""):
                handler.setFormatter(type(handler.formatter)(ACCESS_FORMAT,
                                                             use_colors=False))
            else:
                handler.setFormatter(formatter)
    # The service loggers that hang off uvicorn.error follow LOG_LEVEL too.
    logging.getLogger("uvicorn.error").setLevel(numeric)


# ---------------------------------------------------------------------------
# Background-loop heartbeats
# ---------------------------------------------------------------------------
class Heartbeats:
    """When each background loop last started a cycle, in this process.

    In memory and per process on purpose: the question ``/ready`` answers is
    "is THIS replica's loop alive", and a shared store would let a healthy
    replica vouch for a wedged one.
    """

    #: A loop is stale once it has missed this many of its own intervals.
    STALE_AFTER_INTERVALS = 3

    def __init__(self) -> None:
        self._beats: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def beat(self, name: str, interval_seconds: float) -> None:
        with self._lock:
            self._beats[name] = (time.monotonic(), float(interval_seconds))

    def report(self) -> dict[str, dict[str, Any]]:
        now = time.monotonic()
        with self._lock:
            beats = dict(self._beats)
        out = {}
        for name, (last, interval) in sorted(beats.items()):
            age = now - last
            out[name] = {
                "seconds_since_last_run": round(age, 1),
                "interval_seconds": interval,
                "stale": age > self.STALE_AFTER_INTERVALS * max(interval, 1.0),
            }
        return out


#: The process-wide registry. Loops call ``heartbeats.beat(...)`` once per
#: cycle; ``/ready`` reads it.
heartbeats = Heartbeats()


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------
def _check_db(engine: Any) -> str | None:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return None
    except Exception as exc:  # noqa: BLE001 -- any failure means not ready
        return f"{type(exc).__name__}: {exc}"[:300]


def _check_redis(url: str) -> str | None:
    try:
        import redis  # only the services that use Redis have it installed

        client = redis.Redis.from_url(url, socket_timeout=0.5,
                                      socket_connect_timeout=0.5)
        try:
            client.ping()
        finally:
            client.close()
        return None
    except Exception as exc:  # noqa: BLE001
        return f"{type(exc).__name__}: {exc}"[:300]


def install_observability(app: FastAPI, *, service: str, engine: Any = None,
                          log_level: str | None = None,
                          redis_url: str | None = None,
                          redis_required: bool = False,
                          report_heartbeats: bool = False,
                          ready_endpoint: bool = True) -> None:
    """Request ids, logging, and ``GET /ready`` on ``app``.

    ``/ready`` answers **503** when the database is unreachable, or Redis when
    ``redis_required`` -- the instance cannot do its job, so it should get no
    traffic. It answers **200 with ``"status": "degraded"``** when an optional
    dependency (a Redis that is only used failing open) is down or a
    background loop has gone stale (no cycle in 3x its interval): the API is
    still serving correctly, and taking every replica out of rotation over a
    stuck sweep would turn one broken job into a full outage. The details say
    which, for a human or an alert rule to act on.
    """
    configure_logging(log_level)
    app.add_middleware(RequestIdMiddleware)
    if not ready_endpoint:
        return

    @app.get("/ready", tags=["meta"], include_in_schema=False)
    def ready() -> JSONResponse:
        checks: dict[str, Any] = {}
        fatal = degraded = False
        if engine is not None:
            err = _check_db(engine)
            checks["database"] = "ok" if err is None else err
            fatal |= err is not None
        if redis_url:
            err = _check_redis(redis_url)
            checks["redis"] = "ok" if err is None else err
            if err is not None:
                if redis_required:
                    fatal = True
                else:
                    degraded = True
        if report_heartbeats:
            loops = heartbeats.report()
            checks["loops"] = loops
            degraded |= any(v["stale"] for v in loops.values())
        status = "unavailable" if fatal else "degraded" if degraded else "ok"
        return JSONResponse(
            {"status": status, "service": service, "checks": checks},
            status_code=503 if fatal else 200,
        )
