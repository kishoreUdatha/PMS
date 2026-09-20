"""Finance service settings."""

from __future__ import annotations

from chirala_common.config import BaseServiceSettings


class FinanceSettings(BaseServiceSettings):
    finance_database_url: str = (
        "postgresql+psycopg://pms:pms_dev_password@localhost:5432/chirala_pms"
    )
    #: The schema owner, for migrations and grants only. The app itself uses
    #: the URL above, which in a deployment is the runtime login that owns
    #: nothing and cannot bypass row-level security. Empty means "use the URL
    #: above", which keeps a service run straight from the host working.
    finance_migration_database_url: str = ""
    finance_port: int = 8003
    #: Whether this process runs the night audit on a schedule. On by default
    #: -- an audit nothing calls is the same as no audit at all. Turn it off
    #: for a process that should only serve requests, or while testing a day
    #: by hand.
    night_audit_enabled: bool = True
    #: Local hour, at the property, after which a finished day may be closed.
    #: 03:00 is the usual dead spot: late arrivals are in, breakfast has not
    #: started. Set per deployment rather than per property -- a group whose
    #: resorts genuinely differ needs a column, not an env var.
    night_audit_hour: int = 3
    #: How often to look. The sweep is a no-op unless a day is genuinely due,
    #: so this only needs to be fine enough to start the audit near its hour.
    night_audit_poll_seconds: int = 15 * 60

    # --- SMTP, for the morning summary --------------------------------
    # Read from the same .env the other services use. With no host the mailer
    # refuses rather than pretending to send, and the audit carries on: a day
    # must close whether or not mail is reachable.
    # SMTP, app_base_url and mail_config come from BaseServiceSettings.

    # --- payment gateway --------------------------------------------
    #: 'mock' or 'razorpay'. Mock by default: a deployment without keys must
    #: keep working, and silently switching to a live gateway because someone
    #: set one variable is not a thing that should happen.
    #: Where booking-core lives. The webhook confirms a booking there
    #: rather than reimplementing inventory logic it does not own.
    booking_url: str = "http://localhost:8002"
    payment_provider: str = "mock"
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    #: Shared with the gateway's webhook configuration. Without it the webhook
    #: endpoint refuses everything rather than trusting unsigned events.
    razorpay_webhook_secret: str = ""



settings = FinanceSettings()
