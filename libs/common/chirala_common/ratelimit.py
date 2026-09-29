"""Rate limiting for the endpoints that face the internet.

Most routes are behind a session; abusing them costs you an account. A few
are behind nothing: the public booking search, which is the cheapest way to
scrape a competitor's rates for a year, and the sign-in routes, which are the
cheapest way to guess passwords or flood somebody's inbox with reset mail.

Shared, not per-process. A counter in memory resets whenever a container
restarts and is invisible to the other replicas, so two workers would each
grant a full allowance and a rolling deploy would hand out a fresh one. Redis
is already running for this system; the count belongs there.

**It fails open.** If Redis is unreachable the request is allowed and the
failure is logged loudly. That is a deliberate trade: a booking site that
stops selling rooms, or a hotel whose staff cannot sign in, because a cache is
down has turned a nuisance into an outage. Sign-in keeps its per-account
lockout either way; this is the layer in front of it that also catches one
address trying many accounts.

Originally booking-core's alone; moved here when iam needed the same thing,
so there is one idea of who a caller is and one set of keys in Redis.
"""

from __future__ import annotations

import logging
import socket
import time

from fastapi import HTTPException, Request

log = logging.getLogger("uvicorn.error").getChild("ratelimit")

#: url -> client, or None once it has failed.
_clients: dict[str, object] = {}


def _redis(url: str):
    """One lazily-made client per URL, and never retried in a hot loop.

    A dead Redis should cost one log line, not a connection attempt per
    request — which under the load this exists to survive would be its own
    denial of service.
    """
    if url in _clients:
        return _clients[url]
    try:
        import redis  # imported here so a service starts without it

        client = redis.Redis.from_url(
            url, socket_timeout=0.25, socket_connect_timeout=0.25,
            decode_responses=True)
        client.ping()
    except Exception as exc:  # noqa: BLE001
        client = None
        log.warning("rate limiting is OFF: redis unavailable (%s)", exc)
    _clients[url] = client
    return client


_trusted_cache: dict[str, tuple[float, frozenset[str]]] = {}


def trusted_peers(configured: str) -> frozenset[str]:
    """The configured proxies, as addresses.

    Configured by name — ``gateway`` — because that is what the deployment
    calls it, but the peer arriving on the socket is an address. Comparing the
    two never matches, which silently degrades every caller behind the proxy
    into one shared bucket: the whole site throttled as if it were one visitor,
    and no error to say so.

    Resolved with a short cache, because a container's address survives a
    request but not a redeploy.
    """
    now = time.time()
    cached_at, value = _trusted_cache.get(configured, (0.0, frozenset()))
    if now - cached_at < 60 and value:
        return value

    out: set[str] = set()
    for entry in configured.split(","):
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
    _trusted_cache[configured] = (now, resolved)
    return resolved


def client_ip(request: Request, trusted_proxies: str) -> str:
    """Who is calling, as far as can honestly be told.

    ``X-Forwarded-For`` is trusted only when the connection itself came from a
    proxy we run. Anyone can send that header; taking it at face value would
    let a caller mint a new identity per request and walk straight through the
    limit. From an untrusted peer the socket address is the only fact
    available, so that is what is used.
    """
    peer = request.client.host if request.client else "unknown"
    if peer in trusted_peers(trusted_proxies):
        fwd = request.headers.get("x-forwarded-for", "")
        first = fwd.split(",")[0].strip()
        if first:
            return first
    return peer


def enforce(
    request: Request, *, scope: str, limit: int, window_seconds: int,
    redis_url: str, trusted_proxies: str,
) -> None:
    """Allow the request, or refuse it with 429 and a Retry-After.

    A fixed window per caller and ``scope``: cheap, one round trip, and
    accurate enough for the things it defends against. Its known flaw is the
    boundary — a caller can spend a full allowance at the end of one window
    and another at the start of the next. Neither scraping nor password
    guessing is helped much by a doubled burst, and a sliding log would cost
    Redis traffic on every request.
    """
    conn = _redis(redis_url)
    if conn is None:
        return  # fail open, already logged

    window = max(window_seconds, 1)
    bucket = int(time.time()) // window
    key = f"rl:{scope}:{client_ip(request, trusted_proxies)}:{bucket}"
    try:
        used = conn.incr(key)
        if used == 1:
            # Only the first request in a window sets the expiry, so the
            # window cannot be pushed forward indefinitely by more traffic.
            conn.expire(key, window)
    except Exception as exc:  # noqa: BLE001
        log.warning("rate limit check failed, allowing: %s", exc)
        return

    if used > limit:
        retry = window - int(time.time()) % window
        raise HTTPException(
            status_code=429,
            detail="Too many requests. Please slow down and try again shortly.",
            headers={"Retry-After": str(retry)},
        )
