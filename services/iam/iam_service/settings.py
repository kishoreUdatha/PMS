"""IAM service settings."""

from __future__ import annotations

from chirala_common.config import BaseServiceSettings


class IamSettings(BaseServiceSettings):
    iam_database_url: str = (
        "postgresql+psycopg://pms:pms_dev_password@localhost:5432/chirala_pms"
    )
    #: The schema owner, for migrations and grants only. The app itself uses
    #: the URL above, which in a deployment is the runtime login that owns
    #: nothing and cannot bypass row-level security. Empty means "use the URL
    #: above", which keeps a service run straight from the host working.
    iam_migration_database_url: str = ""
    iam_port: int = 8001

    #: Where booking-core lives. Going live asks it to set the property up at
    #: the channel manager: those mappings belong to the service that owns
    #: them, and a second copy of the rules here would be a second thing to
    #: keep right.
    booking_url: str = "http://localhost:8002"

    #: Where the app is reachable, for the links inside emails. A link to
    #: localhost is useless in somebody's inbox, so this has to be set to the
    #: address staff actually use before any mail is worth sending.
    app_base_url: str = "http://localhost:5173"

    #: What the product is called in an email. A tenant white-labelling this
    #: changes one setting rather than editing templates.
    platform_name: str = "Chirala Bay PMS"
    #: Where staff who cannot sign in should turn. Deliberately separate from
    #: the property's own contact details: those are the guest line, and
    #: sending a housekeeper with a broken password to the reservations desk
    #: helps nobody. Left out of the email entirely when unset.
    support_email: str = ""
    support_phone: str = ""
    website_url: str = ""

    #: Refuse a platform sign-in that has no second factor.
    #:
    #: Off until the operators have enrolled, because switching it on first
    #: locks every one of them out of the console that grants access back --
    #: and the only way in would be the database. Turn it on once
    #: /auth/mfa/status reports active for everybody.
    platform_mfa_required: bool = False

    #: Tax on a subscription invoice, as a percentage. Zero by default and
    #: deliberately so: guessing 18% would produce invoices that look
    #: authoritative and are wrong for anyone the guess does not fit, whereas
    #: an invoice with no tax on it is obviously incomplete and gets noticed.
    saas_tax_rate: float = 0.0
    #: Days from issue to due date.
    saas_net_days: int = 7
    #: How long a past-due subscription keeps working before somebody decides
    #: what to do about it. Suspension is never automatic.
    saas_grace_days: int = 14
    #: How often the billing loop wakes. Hourly: a period closes once a month,
    #: so anything faster is only load, and anything slower delays an invoice
    #: by more than a working day.
    billing_poll_seconds: int = 3600
    #: Off by default. A second deployment pointed at the same database would
    #: otherwise both bill -- harmless, because the unique constraint holds,
    #: but every replica writing invoices is not a thing to switch on by
    #: accident.
    billing_run_enabled: bool = False

    # SMTP and mail_config come from BaseServiceSettings — every service that
    # sends mail must use the same envelope.


settings = IamSettings()
