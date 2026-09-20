"""Rate limiting for the endpoints that face the internet.

Everything else in this service is behind a session; abusing it costs you an
account. The public availability endpoint is behind nothing, and a booking
engine's search is the cheapest way to scrape a competitor's rates for a year.

Shared, not per-process. A counter in memory resets whenever a container
restarts and is invisible to the other replicas, so two workers would each
grant a full allowance and a rolling deploy would hand out a fresh one. Redis
is already running for this system; the count belongs there.

**It fails open.** If Redis is unreachable the request is allowed and the
failure is logged loudly. That is a deliberate trade: rate limiting protects
margins, and a booking site that stops selling rooms because a cache is down
has turned a nuisance into an outage.
"""

from __future__ import annotations

import logging
import time

from fastapi import HTTPException, Request

from .settings import settings

log = logging.getLogger("uvicorn.error").getChild("ratelimit")

_client = None
_client_failed = False


def _redis():
    """One lazily-made client, and never retried in a hot loop.

    A dead Redis should cost one log line, not a connection attempt per
    request — which under the load this exists to survive would be its own
    denial of service.
    """
    global _client, _client_failed
    if _client is not None or _client_failed:
        return _client
    try:
        import redis  # imported here so the service starts without it

        _client = redis.Redis.from_url(
            settings.redis_url, socket_timeout=0.25,
            socket_connect_timeout=0.25, decode_responses=True)
        _client.ping()
    except Exception as exc:  # noqa: BLE001
        _client_failed = True
        _client = None
        log.warning("rate limiting is OFF: redis unavailable (%s)", exc)
    return _client


_trusted_cache: tuple[float, frozenset[str]] = (0.0, frozenset())


def trusted_peers() -> frozenset[str]:
    """The configured proxies, as addresses.

    Configured by name — ``gateway`` — because that is what the deployment
    calls it, but the peer arriving on the socket is an address. Comparing the
    two never matches, which silently degrades every caller behind the proxy
    into one shared bucket: the whole site throttled as if it were one visitor,
    and no error to say so.

    Resolved with a short cache, because a container's address survives a
    request but not a redeploy.
    """
    global _trusted_cache
    now = time.time()
    cached_at, value = _trusted_cache
    if now - cached_at < 60 and value:
        return value

    import socket

    out: set[str] = set()
    for entry in settings.trusted_proxies.split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            for info in socket.getaddrinfo(entry, None):
                out.add(info[4][0])
        except OSError:
            # A name that does not resolve is not a proxy we can recognise.
            # Keeping the literal means an operator who configured an address
            # rather than a name still works.
            out.add(entry)
    resolved = frozenset(out)
    _trusted_cache = (now, resolved)
    return resolved


def client_ip(request: Request) -> str:
    """Who is calling, as far as can honestly be told.

    ``X-Forwarded-For`` is trusted only when the connection itself came from a
    proxy we run. Anyone can send that header; taking it at face value would
    let a caller mint a new identity per request and walk straight through the
    limit. From an untrusted peer the socket address is the only fact
    available, so that is what is used.
    """
    peer = request.client.host if request.client else "unknown"
    if peer in trusted_peers():
        fwd = request.headers.get("x-forwarded-for", "")
        first = fwd.split(",")[0].strip()
        if first:
            return first
    return peer


def rate_limit(request: Request) -> None:
    """Allow the request, or refuse it with 429 and a Retry-After.

    A fixed window per caller: cheap, one round trip, and accurate enough for
    the thing it defends against. Its known flaw is the boundary — a caller can
    spend a full allowance at the end of one window and another at the start of
    the next. For scraping protection that burst is not worth a sliding log and
    the extra Redis traffic it costs.
    """
    if not settings.public_rate_limit_enabled:
        return
    conn = _redis()
    if conn is None:
        return  # fail open, already logged

    window = max(settings.public_rate_limit_seconds, 1)
    bucket = int(time.time()) // window
    key = f"rl:public:{client_ip(request)}:{bucket}"
    try:
        used = conn.incr(key)
        if used == 1:
            # Only the first request in a window sets the expiry, so the
            # window cannot be pushed forward indefinitely by more traffic.
            conn.expire(key, window)
    except Exception as exc:  # noqa: BLE001
        log.warning("rate limit check failed, allowing: %s", exc)
        return

    if used > settings.public_rate_limit:
        retry = window - int(time.time()) % window
        raise HTTPException(
            status_code=429,
            detail="Too many requests. Please slow down and try again shortly.",
            headers={"Retry-After": str(retry)},
        )
