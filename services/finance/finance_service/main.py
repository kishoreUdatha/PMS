"""Finance service FastAPI application."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

from chirala_common.config import refuse_unsafe_boot
from fastapi import FastAPI

from .adjustment_routes import adjustment_router
from .folio_ops_routes import folio_ops_router
from .invoice_routes import invoice_router
from .reversal_routes import reversal_router
from .cashiering_routes import cashiering_router
from .daybook_routes import daybook_router
from .report_routes import report_router
from .backoffice_reports import backoffice_router
from .expense_routes import expense_router
from .owner_routes import owner_router
from .deposit_routes import deposit_router
from .routes import router
from .checkout_routes import checkout_router
from .credentials_routes import credentials_router
from .webhook_routes import webhook_router
from .scheduler import night_audit_loop
from .settings import settings
from .service_routes import service_router
from .tax_routes import tax_router


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Own the night-audit loop for as long as the service is up.

    Started here rather than as a separate cron container so the schedule
    cannot drift away from the code it runs. It is safe to have more than one:
    the audit is idempotent per room per night and only one run per day can
    complete.
    """
    refuse_unsafe_boot(settings, "finance")
    task = (
        asyncio.create_task(night_audit_loop())
        if settings.night_audit_enabled
        else None
    )
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


app = FastAPI(
    title="Chirala Bay PMS — Finance Service",
    version="0.1.0",
    description="Folios, charges, payments, refunds, invoices, and night audit.",
    lifespan=lifespan,
)


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok", "service": "finance"}


app.include_router(adjustment_router)
app.include_router(invoice_router)
app.include_router(reversal_router)
app.include_router(cashiering_router)
app.include_router(daybook_router)
app.include_router(report_router)
app.include_router(backoffice_router)
app.include_router(expense_router)
app.include_router(owner_router)
app.include_router(deposit_router)
app.include_router(service_router)
app.include_router(tax_router)
app.include_router(checkout_router)
app.include_router(credentials_router)
app.include_router(webhook_router)
app.include_router(router)
app.include_router(folio_ops_router)
