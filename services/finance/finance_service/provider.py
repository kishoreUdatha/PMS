"""Payment provider adapter seam.

The finance ledger depends only on the ``PaymentProvider`` protocol, so a real
provider (Razorpay, Stripe, etc.) can be dropped in without touching ledger
logic. Only settled/successful provider results are allowed to affect
collection (§6): the ledger posts a credit only after ``capture`` succeeds.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, runtime_checkable


@dataclass
class ProviderResult:
    success: bool
    provider_transaction_id: str
    detail: str = ""


@runtime_checkable
class PaymentProvider(Protocol):
    """Contract every payment provider adapter must satisfy."""

    def capture(
        self, *, amount: Decimal, currency: str, method: str, reference: str
    ) -> ProviderResult:
        """Capture funds. Returns success + a provider transaction id."""
        ...

    def refund(
        self, *, amount: Decimal, currency: str, provider_transaction_id: str
    ) -> ProviderResult:
        """Refund captured funds."""
        ...


class MockPaymentProvider:
    """Deterministic in-process provider for development and tests.

    Succeeds for any positive amount and returns a stable synthetic transaction
    id. Fails for non-positive amounts. No network, no credentials.
    """

    def capture(
        self, *, amount: Decimal, currency: str, method: str, reference: str
    ) -> ProviderResult:
        if amount <= 0:
            return ProviderResult(False, "", "amount must be positive")
        return ProviderResult(True, f"mock_txn_{uuid.uuid4().hex[:16]}")

    def refund(
        self, *, amount: Decimal, currency: str, provider_transaction_id: str
    ) -> ProviderResult:
        if amount <= 0:
            return ProviderResult(False, "", "amount must be positive")
        return ProviderResult(True, f"mock_rfnd_{uuid.uuid4().hex[:16]}")

    def create_order(self, *, amount: Decimal, currency: str, receipt: str,
                     notes: dict | None = None):
        """A stand-in order, so the whole booking loop runs with no account.

        The id says ``mock`` on purpose. One that looked real would let a
        deployment run for weeks believing it was taking money.
        """
        from dataclasses import dataclass

        @dataclass
        class _Order:
            order_id: str
            amount_paise: int
            currency: str
            key_id: str

        return _Order(f"mock_order_{uuid.uuid4().hex[:16]}",
                      int(amount * 100), currency, "")


def _select() -> PaymentProvider:
    """The provider this deployment is configured for.

    Mock unless asked otherwise, and mock again if the live one is asked for
    without credentials. Falling back is the right failure: a service that
    refuses to start because a gateway key is missing takes the whole PMS down
    over a feature most of it does not use — and the fallback says so loudly
    rather than pretending to be live.
    """
    from .settings import settings

    if settings.payment_provider.lower() != "razorpay":
        return MockPaymentProvider()

    from .razorpay_provider import RazorpayProvider

    live = RazorpayProvider()
    if not live.configured:
        import logging

        logging.getLogger("uvicorn.error").error(
            "PAYMENT_PROVIDER=razorpay but RAZORPAY_KEY_ID/SECRET are unset — "
            "falling back to the mock provider. No real money will move.")
        return MockPaymentProvider()
    return live


# Default provider instance used by the service. Swap here (or via DI) to plug
# in a real adapter.
default_provider: PaymentProvider = _select()


def provider_for(config) -> PaymentProvider:
    """A provider bound to one tenant's credentials.

    ``default_provider`` above is the deployment's, chosen once at import. This
    is the per-tenant equivalent and is built per call: a platform has as many
    gateway accounts as it has tenants, and one module-level instance cannot
    speak for all of them.

    Falls back to mock on incomplete credentials for the same reason ``_select``
    does -- refusing to serve is worse than serving a tenant that plainly is not
    live -- and the caller can tell from ``GatewayConfig.live``.
    """
    if config.provider != "razorpay" or not config.key_id:
        return MockPaymentProvider()

    from .razorpay_provider import RazorpayProvider

    live = RazorpayProvider(key_id=config.key_id,
                            key_secret=config.key_secret)
    if not live.configured:
        import logging

        logging.getLogger("uvicorn.error").error(
            "tenant gateway is set to razorpay but its credentials are "
            "incomplete — using the mock provider. No real money will move.")
        return MockPaymentProvider()
    return live
