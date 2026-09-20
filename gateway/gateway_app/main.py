"""API gateway / BFF.

Routes frontend requests to the backend services. Keeps a single public origin
for the web app, applies CORS, and forwards Authorization headers so downstream
services can validate the OIDC token. Service-to-service auth and fine-grained
authorization are enforced in each service; the gateway is the edge.

Routing:
  /api/iam/*      -> IAM service
  /api/booking/*  -> booking-core service
  /book/{code}    -> the guest booking engine (a page, not a proxy)
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import hmac
import httpx
from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .orchestration import (
    OrchestrationError,
    checkout_with_billing,
    ensure_folio_for_reservation,
    get_dashboard,
    get_folio_view,
    settle_reservation,
)

def _flow_client(
    authorization: str | None,
    service_token: str | None = None,
) -> httpx.AsyncClient:
    """An HTTP client that speaks downstream **as the caller**.

    The /flows/* endpoints fan out to booking-core and finance. Until now they
    called anonymously, which meant every downstream route they touched had to
    be unguarded for them to work — the aggregation was quietly a hole in the
    authorization model. Forwarding the caller's token makes each service apply
    its own permission checks to the real user, which is what makes guarding
    those routes possible at all.

    A flow reached without a user — a webhook, a schedule — instead presents
    the platform's own credential, but **only if the caller proved it holds
    that credential**. Attaching it to anonymous requests would mean anyone
    who names a tenant in ``X-Service-Org`` gets the gateway to fetch that
    tenant's data with the gateway's own token: a confused deputy, and a
    complete bypass of everything the services check.
    """
    if authorization:
        return httpx.AsyncClient(timeout=30.0,
                                 headers={"Authorization": authorization})
    if service_token and settings.service_token and hmac.compare_digest(
            service_token, settings.service_token):
        return httpx.AsyncClient(
            timeout=30.0, headers={"X-Service-Token": settings.service_token})
    # Neither: the downstream services refuse, which is the right answer.
    return httpx.AsyncClient(timeout=30.0)


def _stated_org(service_token: str | None, org: str | None) -> str | None:
    """The organisation a service caller named, once it has proved it is one.

    An organisation asserted without the credential is just a string from a
    stranger; returning it would let an anonymous request pick the tenant the
    gateway then fetches on its behalf.
    """
    if not org or not service_token or not settings.service_token:
        return None
    if not hmac.compare_digest(service_token, settings.service_token):
        return None
    return org


# A flow reached without a user -- a webhook, a schedule -- authenticates
# downstream with the service credential, and that credential is only a tenant
# context if an organisation travels with it. ``X-Service-Org`` on the flow
# endpoint is how the caller states one; it is ignored when a user token was
# forwarded, because then the tenant comes from the person.


_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "content-length",
    "host",
}


class GatewaySettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    iam_url: str = "http://localhost:8001"
    booking_url: str = "http://localhost:8002"
    finance_url: str = "http://localhost:8003"
    gateway_port: int = 8000
    cors_origins: str = "http://localhost:5173"
    #: Where MinIO actually is, on the inside. Media is proxied rather than
    #: linked to directly so that images come from this same origin -- see
    #: ``media`` below for why that turns out to be necessary.
    minio_url: str = "http://localhost:9000"
    minio_bucket: str = "chirala-pms"
    #: Presented on calls the gateway makes on its own behalf — a flow that
    #: has no user to borrow a token from. Shared with the services.
    service_token: str = ""


settings = GatewaySettings()

app = FastAPI(
    title="Chirala Bay PMS — API Gateway",
    version="0.1.0",
    description="Edge router / BFF for the PMS frontend.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_ROUTES = {
    "iam": settings.iam_url,
    "booking": settings.booking_url,
    "finance": settings.finance_url,
}


#: The guest booking engine, served as a file rather than proxied.
#:
#: It lives on the gateway because the gateway is the single public origin:
#: every call the page makes is then same-origin, so there is no CORS to
#: configure and no second hostname for a tenant to point at. Serving it from
#: the PMS frontend instead would ship the whole staff application -- a hundred
#: screens and the auth layer -- to somebody who wants to book a room.
_BOOKING_PAGE = Path(__file__).parent / "static" / "book.html"


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok", "service": "gateway"}


@app.get("/book/{property_code}", include_in_schema=False)
def booking_page(property_code: str) -> FileResponse:
    """The booking engine for one property.

    The code is in the path and nothing here looks at it: the page reads it
    back off its own URL and asks the API, which is the only thing that knows
    whether the property exists and is on sale. Answering that here would be a
    second copy of a rule booking-core owns, and the wrong one the first time
    somebody takes a property off sale.

    ``no-store`` because the page is the same bytes for everybody but its
    *meaning* is not: a cached copy at a shared proxy keyed only on the path is
    fine, but a stale one after a fix is not worth the saved kilobyte here.
    """
    if not _BOOKING_PAGE.is_file():  # pragma: no cover - packaging slip
        raise HTTPException(status_code=500, detail="Booking page missing.")
    return FileResponse(_BOOKING_PAGE, media_type="text/html",
                        headers={"Cache-Control": "no-store"})


# -------------------- BFF orchestration flows --------------------
class EnsureFolioIn(BaseModel):
    organization_id: str
    property_id: str
    reservation_id: str
    currency: str = "INR"


class CheckoutFlowIn(BaseModel):
    nightly_rate: Decimal = Field(gt=0)
    business_date: str | None = None


class SettleFlowIn(BaseModel):
    #: What the stay is worth. Reported back, never posted -- room revenue
    #: accrues nightly through the audit (see ``settle_reservation``). Zero is
    #: allowed because a room declared complimentary or house use is worth
    #: exactly that to bill, and ``gt=0`` refused to finish those bookings at
    #: the payment step.
    room_charge: Decimal = Field(ge=0)
    business_date: str
    method: str | None = None
    advance_amount: Decimal | None = None


@app.post("/flows/reservations/{reservation_id}/settle", tags=["flows"])
async def flow_settle(reservation_id: str, body: SettleFlowIn, authorization: str | None = Header(default=None), x_service_org: str | None = Header(default=None), x_service_token: str | None = Header(default=None)):
    """Create the folio and optionally take an advance.

    It does **not** post the room charge, whatever ``room_charge`` says: the
    nights post themselves through the night audit, and charging here as well
    would bill every guest twice. See ``settle_reservation``.
    """
    async with _flow_client(authorization, x_service_token) as client:
        try:
            result = await settle_reservation(
                client,
                booking_url=settings.booking_url,
                finance_url=settings.finance_url,
                reservation_id=reservation_id,
                room_charge=str(body.room_charge),
                business_date=body.business_date,
                method=body.method,
                advance_amount=str(body.advance_amount) if body.advance_amount else None,
                organization_id=_stated_org(x_service_token, x_service_org),
            )
        except OrchestrationError as exc:
            return Response(
                content=exc.detail.encode(),
                status_code=exc.status_code,
                media_type="application/json",
            )
    return result


@app.get("/flows/reservations/{reservation_id}/folio-view", tags=["flows"])
async def flow_folio_view(reservation_id: str, authorization: str | None = Header(default=None), x_service_org: str | None = Header(default=None), x_service_token: str | None = Header(default=None)):
    """Aggregated folio view (entries + summary) for the Checkout Folio screen."""
    async with _flow_client(authorization, x_service_token) as client:
        try:
            data = await get_folio_view(
                client,
                booking_url=settings.booking_url,
                finance_url=settings.finance_url,
                reservation_id=reservation_id,
                organization_id=_stated_org(x_service_token, x_service_org),
            )
        except OrchestrationError as exc:
            return Response(content=exc.detail.encode(), status_code=exc.status_code, media_type="application/json")
    return data


@app.get("/flows/dashboard", tags=["flows"])
async def flow_dashboard(property_id: str, business_date: str | None = None, authorization: str | None = Header(default=None), x_service_org: str | None = Header(default=None), x_service_token: str | None = Header(default=None)):
    """Aggregated operations dashboard (booking-core + finance)."""
    async with _flow_client(authorization, x_service_token) as client:
        try:
            data = await get_dashboard(
                client,
                booking_url=settings.booking_url,
                finance_url=settings.finance_url,
                property_id=property_id,
                business_date=business_date,
                organization_id=_stated_org(x_service_token, x_service_org),
            )
        except OrchestrationError as exc:
            return Response(
                content=exc.detail.encode(),
                status_code=exc.status_code,
                media_type="application/json",
            )
    return data


@app.post("/flows/reservations/{reservation_id}/folio", tags=["flows"])
async def flow_ensure_folio(
    reservation_id: str,
    body: EnsureFolioIn,
    authorization: str | None = Header(default=None),
    # Was referenced in the body without ever being a parameter, so every call
    # raised NameError and returned 500. Nothing in the product called this
    # endpoint, which is why a hard failure sat here unnoticed.
    x_service_token: str | None = Header(default=None),
):
    """Ensure a finance folio exists for a reservation (idempotent, Option A)."""
    async with _flow_client(authorization, x_service_token) as client:
        try:
            folio = await ensure_folio_for_reservation(
                client,
                finance_url=settings.finance_url,
                organization_id=body.organization_id,
                property_id=body.property_id,
                reservation_id=reservation_id,
                currency=body.currency,
            )
        except OrchestrationError as exc:
            return Response(
                content=exc.detail.encode(),
                status_code=exc.status_code,
                media_type="application/json",
            )
    return folio


@app.post(
    "/flows/reservation-units/{reservation_unit_id}/checkout", tags=["flows"]
)
async def flow_checkout(reservation_unit_id: str, body: CheckoutFlowIn, authorization: str | None = Header(default=None), x_service_org: str | None = Header(default=None), x_service_token: str | None = Header(default=None)):
    """Checkout + bill: post room charges to the folio and return the balance."""
    async with _flow_client(authorization, x_service_token) as client:
        try:
            result = await checkout_with_billing(
                client,
                booking_url=settings.booking_url,
                finance_url=settings.finance_url,
                reservation_unit_id=reservation_unit_id,
                nightly_rate=body.nightly_rate,
                business_date=body.business_date,
                organization_id=_stated_org(x_service_token, x_service_org),
            )
        except OrchestrationError as exc:
            return Response(
                content=exc.detail.encode(),
                status_code=exc.status_code,
                media_type="application/json",
            )
    return result


async def _proxy(target_base: str, path: str, request: Request) -> Response:
    url = f"{target_base}/{path}"
    body = await request.body()
    fwd_headers = {
        k: v for k, v in request.headers.items() if k.lower() not in _HOP_BY_HOP
    }
    # Say who is really calling, and overwrite rather than append.
    #
    # Upstream services need the caller's address to rate-limit a public
    # endpoint; without this they see the gateway for every request and would
    # throttle themselves. Overwriting matters as much as setting: a client can
    # put anything in X-Forwarded-For, so a value carried through from outside
    # is an assertion by a stranger, not evidence. There is exactly one hop
    # here, so the peer socket is the truth.
    fwd_headers.pop("x-forwarded-for", None)
    fwd_headers.pop("X-Forwarded-For", None)
    if request.client is not None:
        fwd_headers["X-Forwarded-For"] = request.client.host
    async with httpx.AsyncClient(timeout=30.0) as client:
        upstream = await client.request(
            request.method,
            url,
            content=body,
            headers=fwd_headers,
            params=request.query_params,
        )
    resp_headers = {
        k: v for k, v in upstream.headers.items() if k.lower() not in _HOP_BY_HOP
    }
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=resp_headers,
        media_type=upstream.headers.get("content-type"),
    )


@app.api_route(
    "/api/{service}/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    tags=["proxy"],
)
async def proxy(service: str, path: str, request: Request) -> Response:
    target = _ROUTES.get(service)
    if target is None:
        return Response(content=b'{"detail":"Unknown service"}', status_code=404,
                        media_type="application/json")
    return await _proxy(target, path, request)


@app.api_route("/{bucket}/{key:path}", methods=["GET", "HEAD"],
               include_in_schema=False)
async def media(bucket: str, key: str, request: Request) -> Response:
    """Serve an uploaded image from MinIO, through this origin.

    Room photos used to be linked straight at MinIO's own address, which worked
    for exactly one audience: a browser on the machine running Docker. Every
    real guest got a broken image -- the address was ``localhost``, and on the
    booking page (HTTPS, through a tunnel) an ``http://`` image is blocked as
    mixed content even when the host does resolve.

    So the bytes come through here instead, and the booking page loads its
    photos from the same origin it was served from.

    **This is a pass-through, not an unlocked door.** The URL is still a
    presigned one: MinIO checks the signature and the expiry itself, and a
    request without a valid signature for that exact key gets the same 403 it
    always did. That matters more than it might seem -- guest ID scans live in
    this bucket too, and a proxy that served any key by name would publish
    them. Nothing here grants access; it only carries a request that already
    had to prove itself.

    The ``Host`` header is forwarded deliberately, and this is why the route
    sits at the bucket root rather than under a tidy ``/media`` prefix.
    SigV4 signs the host and the path, so both have to arrive at MinIO exactly
    as they were signed. A prefix would change the path, and rewriting it would
    mean re-signing -- which would mean holding the credentials that make the
    signature meaningless.
    """
    # Scoped by name, so this does not quietly become a catch-all for every
    # unmatched path on the gateway.
    if bucket != settings.minio_bucket:
        raise HTTPException(status_code=404, detail="Not found.")

    upstream_url = f"{settings.minio_url.rstrip('/')}/{bucket}/{key}"
    # Host included on purpose (it is normally stripped as hop-by-hop): the
    # signature was computed over it. Range is forwarded so a browser can seek
    # in a large file rather than refetch it.
    forward = {
        k: v for k, v in request.headers.items()
        if k.lower() in ("host", "range", "if-none-match", "if-modified-since",
                         "accept", "accept-encoding")
    }

    client = httpx.AsyncClient(timeout=30.0)
    try:
        req = client.build_request(request.method, upstream_url,
                                   headers=forward,
                                   params=request.query_params)
        upstream = await client.send(req, stream=True)
    except httpx.HTTPError:
        await client.aclose()
        # A missing object store is not a broken page. The booking engine
        # renders without photographs; it does not render without a response.
        raise HTTPException(status_code=502,
                            detail="Media is unavailable.") from None

    passthrough = {
        k: v for k, v in upstream.headers.items()
        if k.lower() in ("content-type", "content-length", "etag",
                         "last-modified", "cache-control", "content-range",
                         "accept-ranges")
    }
    # An image keyed by a uuid never changes, and its URL expires long before
    # the cache entry would. Worth saying so: a booking page is mostly
    # photographs, and re-fetching them on every step is the difference between
    # quick and sluggish on a phone.
    passthrough.setdefault("Cache-Control", "public, max-age=3600")

    async def body():
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(body(), status_code=upstream.status_code,
                             headers=passthrough)
