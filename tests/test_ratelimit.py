"""The shared limiter counts per address and believes X-Forwarded-For only
from a configured proxy. Redis is replaced by a dict."""

from __future__ import annotations

import pytest
from chirala_common import ratelimit
from fastapi import HTTPException
from starlette.requests import Request


def _req(peer: str, xff: str | None = None) -> Request:
    headers = [(b"x-forwarded-for", xff.encode())] if xff else []
    return Request({"type": "http", "client": (peer, 1234), "headers": headers,
                    "method": "POST", "path": "/"})


class _FakeRedis:
    def __init__(self):
        self.n = {}

    def incr(self, key):
        self.n[key] = self.n.get(key, 0) + 1
        return self.n[key]

    def expire(self, key, seconds):
        pass


def test_forwarded_for_is_believed_only_from_a_trusted_proxy():
    assert ratelimit.client_ip(_req("10.0.0.5", "1.2.3.4"), "") == "10.0.0.5"
    assert ratelimit.client_ip(_req("10.0.0.5", "1.2.3.4"), "10.0.0.5") == "1.2.3.4"


def test_limit_is_per_address_and_per_scope(monkeypatch):
    monkeypatch.setitem(ratelimit._clients, "redis://fake", _FakeRedis())
    kw = dict(limit=3, window_seconds=60, redis_url="redis://fake",
              trusted_proxies="")
    for _ in range(3):
        ratelimit.enforce(_req("1.1.1.1"), scope="auth:login", **kw)
    with pytest.raises(HTTPException) as e:
        ratelimit.enforce(_req("1.1.1.1"), scope="auth:login", **kw)
    assert e.value.status_code == 429 and "Retry-After" in e.value.headers
    # Another address, and another route, each have their own allowance.
    ratelimit.enforce(_req("2.2.2.2"), scope="auth:login", **kw)
    ratelimit.enforce(_req("1.1.1.1"), scope="auth:forgot-password", **kw)


def test_fails_open_without_redis(monkeypatch):
    monkeypatch.setitem(ratelimit._clients, "redis://down", None)
    for _ in range(10):
        ratelimit.enforce(_req("1.1.1.1"), scope="x", limit=1, window_seconds=60,
                          redis_url="redis://down", trusted_proxies="")
