"""IAM service FastAPI application."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

from chirala_common.config import refuse_unsafe_boot
from fastapi import FastAPI

from .approvals_routes import approvals_router
from .audit_routes import audit_router
from .auth_routes import auth_router
from .roles_routes import roles_router
from .routes import router
from .onboarding_routes import onboarding_router
from .billing_routes import platform_billing_router, tenant_billing_router
from .billing_scheduler import billing_loop
from .mfa_routes import mfa_router
from .settings import settings
from .platform_ops_routes import ops_router
from .platform_routes import platform_router

@contextlib.asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Own the billing loop for as long as the service is up.

    Started here rather than as a separate cron container so the schedule
    cannot drift away from the code it runs — the same reasoning finance uses
    for the night audit. Safe to have more than one: an invoice is unique per
    subscription per period, so a second replica produces nothing.
    """
    refuse_unsafe_boot(settings, "iam")
    task = (
        asyncio.create_task(billing_loop())
        if settings.billing_run_enabled
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
    lifespan=lifespan,
    title="Chirala Bay PMS — IAM Service",
    version="0.1.0",
    description="Organizations, properties, users, memberships, roles, permissions.",
)


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok", "service": "iam"}


app.include_router(auth_router)
app.include_router(roles_router)
app.include_router(approvals_router)
app.include_router(audit_router)
app.include_router(onboarding_router)
app.include_router(platform_router)
app.include_router(ops_router)
app.include_router(mfa_router)
app.include_router(platform_billing_router)
app.include_router(tenant_billing_router)
app.include_router(router)
