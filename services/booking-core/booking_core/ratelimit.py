"""Rate limiting for the public booking API.

The public availability endpoint is behind nothing, and a booking engine's
search is the cheapest way to scrape a competitor's rates for a year. The
mechanism -- Redis, fixed windows, which X-Forwarded-For to believe, failing
open -- lives in ``chirala_common.ratelimit``, shared with iam's sign-in
routes; this is booking-core's allowance.
"""

from __future__ import annotations

from chirala_common.ratelimit import enforce
from fastapi import Request

from .settings import settings


def rate_limit(request: Request) -> None:
    """Allow the request, or refuse it with 429 and a Retry-After."""
    if not settings.public_rate_limit_enabled:
        return
    enforce(
        request, scope="public",
        limit=settings.public_rate_limit,
        window_seconds=settings.public_rate_limit_seconds,
        redis_url=settings.redis_url,
        trusted_proxies=settings.trusted_proxies,
    )
