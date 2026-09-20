"""Razorpay, behind the provider seam the ledger already depends on.

Razorpay rather than Stripe because this is an Indian property taking INR with
GST, and Razorpay is what an Indian hotel's bank and accountant expect. Nothing
below is Razorpay-shaped outside this file: the ledger talks to the
``PaymentProvider`` protocol, so a second provider is a second adapter.

**Credentials belong to the platform, not to each property.** There is no
secret-at-rest vault in this system, and building one is not a prerequisite for
taking a payment: one platform account that settles to tenants is how a SaaS of
this size starts, and it keeps gateway secrets in the environment rather than in
a database column somebody will eventually SELECT. Per-tenant gateway accounts
are a real requirement later; they need a vault first, and that is a decision
to make deliberately rather than by accident.

Two shapes of payment, and they are not the same:

* **Server-initiated** — a cashier records money already taken, or a refund
  goes back. Synchronous, request/response, and what ``capture`` and ``refund``
  below are for.
* **Guest-initiated** — the booking engine. An order is created here, the guest
  pays on Razorpay's own page, and the money is confirmed by a *webhook*
  minutes later. The folio is credited then, never on the redirect back, which
  a guest can forge simply by visiting the success URL.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from decimal import Decimal

import httpx

from .provider import ProviderResult
from .settings import settings

log = logging.getLogger("uvicorn.error").getChild("razorpay")

_API = "https://api.razorpay.com/v1"


class RazorpayNotConfigured(RuntimeError):
    pass


@dataclass
class RazorpayOrder:
    """What the guest's browser needs to open the checkout."""

    order_id: str
    amount_paise: int
    currency: str
    key_id: str


def _paise(amount: Decimal) -> int:
    """Razorpay works in the smallest currency unit, always as an integer.

    Sending 4300.00 where 430000 is meant undercharges by a factor of a
    hundred, and sending a float invites a rounding error into a payment.
    """
    return int((amount * 100).quantize(Decimal("1")))


class RazorpayProvider:
    """Adapter for the live gateway."""

    def __init__(self, key_id: str = "", key_secret: str = "",
                 timeout: float = 20.0) -> None:
        self.key_id = key_id or settings.razorpay_key_id
        self.key_secret = key_secret or settings.razorpay_key_secret
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.key_id and self.key_secret)

    def _auth(self) -> dict[str, str]:
        if not self.configured:
            raise RazorpayNotConfigured(
                "RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET are not set, so no "
                "payment can be taken. The mock provider is used until they "
                "are.")
        token = base64.b64encode(
            f"{self.key_id}:{self.key_secret}".encode()).decode()
        return {"Authorization": f"Basic {token}"}

    # ------------------------------------------------- guest-initiated ----
    def create_order(
        self, *, amount: Decimal, currency: str, receipt: str,
        notes: dict | None = None,
    ) -> RazorpayOrder:
        """Open an order for the guest to pay against.

        This reserves nothing and moves no money; it is a quote the checkout
        can be pointed at. The booking's own hold is what protects the room
        while the guest pays, which is why holds have to expire.
        """
        resp = httpx.post(
            f"{_API}/orders",
            headers=self._auth(),
            json={
                "amount": _paise(amount),
                "currency": currency,
                # Razorpay dedupes on this, so a guest who double-submits the
                # checkout gets one order rather than two.
                "receipt": receipt,
                "notes": notes or {},
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        body = resp.json()
        return RazorpayOrder(
            order_id=body["id"], amount_paise=body["amount"],
            currency=body["currency"], key_id=self.key_id,
        )

    # ------------------------------------------------ server-initiated ----
    def capture(
        self, *, amount: Decimal, currency: str, method: str, reference: str
    ) -> ProviderResult:
        """Capture an authorised payment by its Razorpay payment id.

        ``reference`` must be that id. There is no way to charge a card from
        here without one — a gateway does not let a server debit an arbitrary
        customer, and a method that pretended to would be lying about what it
        can do.
        """
        if amount <= 0:
            return ProviderResult(False, "", "amount must be positive")
        if not reference.startswith("pay_"):
            return ProviderResult(
                False, "",
                "A Razorpay payment id is required to capture. Money taken "
                "at the desk is recorded, not captured — use the cash or card "
                "method instead.")
        try:
            resp = httpx.post(
                f"{_API}/payments/{reference}/capture",
                headers=self._auth(),
                json={"amount": _paise(amount), "currency": currency},
                timeout=self.timeout,
            )
            if resp.status_code >= 400:
                return ProviderResult(
                    False, "", _detail(resp))
            return ProviderResult(True, resp.json().get("id", reference))
        except (httpx.HTTPError, RazorpayNotConfigured) as exc:
            log.warning("razorpay capture failed: %s", exc)
            return ProviderResult(False, "", str(exc))

    def refund(
        self, *, amount: Decimal, currency: str, provider_transaction_id: str
    ) -> ProviderResult:
        if amount <= 0:
            return ProviderResult(False, "", "amount must be positive")
        try:
            resp = httpx.post(
                f"{_API}/payments/{provider_transaction_id}/refund",
                headers=self._auth(),
                json={"amount": _paise(amount)},
                timeout=self.timeout,
            )
            if resp.status_code >= 400:
                return ProviderResult(False, "", _detail(resp))
            return ProviderResult(True, resp.json().get("id", ""))
        except (httpx.HTTPError, RazorpayNotConfigured) as exc:
            log.warning("razorpay refund failed: %s", exc)
            return ProviderResult(False, "", str(exc))


def _detail(resp: httpx.Response) -> str:
    """The gateway's own words, not ours.

    A refusal is usually specific — the card was declined, the amount exceeds
    what was authorised — and translating it into "payment failed" throws away
    the only thing that tells a cashier what to do next.
    """
    try:
        return resp.json().get("error", {}).get("description", resp.text[:200])
    except ValueError:
        return resp.text[:200]
