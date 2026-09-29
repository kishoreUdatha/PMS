"""The gateway tells browsers how little to trust its responses."""

from __future__ import annotations

from fastapi.testclient import TestClient
from gateway_app.main import app

client = TestClient(app)


def test_api_responses_get_the_strict_set():
    r = client.get("/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert r.headers["content-security-policy"] == (
        "default-src 'none'; frame-ancestors 'none'")
    # Plain HTTP: no HSTS, or a local stack would teach the browser to refuse
    # itself.
    assert "strict-transport-security" not in r.headers


def test_hsts_only_over_https():
    r = client.get("/health", headers={"X-Forwarded-Proto": "https"})
    assert r.headers["strict-transport-security"].startswith("max-age=")


def test_the_booking_page_keeps_its_scripts_but_cannot_be_framed():
    r = client.get("/book/ANY")
    assert r.status_code == 200
    assert r.headers["content-security-policy"] == "frame-ancestors 'none'"
    assert r.headers["x-frame-options"] == "DENY"


def test_guest_id_scans_are_never_cached(monkeypatch):
    import httpx
    from gateway_app import main as gw

    real = httpx.AsyncClient

    class Stub(real):
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.MockTransport(
                lambda req: httpx.Response(
                    200, stream=httpx.ByteStream(b"\xff\xd8\xff"),
                    headers={"content-type": "image/jpeg"}))
            super().__init__(*a, **kw)

    monkeypatch.setattr(gw.httpx, "AsyncClient", Stub)
    bucket = gw.settings.minio_bucket
    scan = client.get(f"/{bucket}/guest-docs/g/id_front/x.jpg")
    photo = client.get(f"/{bucket}/rooms/r/x.jpg")
    assert scan.headers["cache-control"] == "private, no-store"
    assert photo.headers["cache-control"].startswith("public")
