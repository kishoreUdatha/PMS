"""Base configuration shared by all services.

Each service subclasses ``BaseServiceSettings`` and adds its own
``database_url``. Values are read from environment variables (12-factor).
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict

from .mailer import MailConfig


class BaseServiceSettings(BaseSettings):
    """Configuration common to every service."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    #: ``local`` switches on the developer conveniences -- the X-Debug-Subject
    #: header, password-less sign-in by subject, unsigned dev tokens -- and
    #: nothing else does. Defaults to ``production`` so that a deployment
    #: which forgot to say what it is gets the strict behaviour; a developer
    #: sets ``ENVIRONMENT=local`` in ``.env`` once. The comparison is always
    #: exact: ``Local`` or ``dev`` is not local.
    environment: str = "production"
    log_level: str = "INFO"

    #: Shared secret for calls between this deployment's own services.
    #:
    #: A webhook, a scheduled job, or one service finishing work another
    #: started has no user to authenticate as. Unset, service authentication
    #: is off and every caller must be a person — the right default, so a
    #: deployment that forgot to configure a secret cannot accept an empty one.
    service_token: str = ""

    #: Signs the session tokens iam issues and every service checks.
    #:
    #: Comma-separated, newest first: the first key signs and any of them
    #: verifies, so a key can be rotated without signing everybody out. Shared
    #: by all three services because a token iam issued has to be accepted by
    #: booking-core and finance. Generate one with::
    #:
    #:     python -c "import secrets; print(secrets.token_urlsafe(48))"
    #:
    #: Empty is tolerated only in local mode, where sign-in falls back to the
    #: unsigned dev token; anywhere else the service refuses to start.
    session_signing_key: str = ""
    #: How long a session lasts before its holder has to sign in again. A
    #: shift and a half: long enough that nobody is thrown out mid-shift,
    #: short enough that yesterday's leaked token is dead today.
    session_ttl_minutes: int = 12 * 60

    # Infrastructure
    redis_url: str = "redis://localhost:6379/0"
    #: Peers whose X-Forwarded-For may be believed when rate limiting. Only
    #: our own gateway, which overwrites the header with the socket address
    #: it saw; a header from anyone else is a claim, not evidence.
    #:
    #: Defaults to the gateway's compose service name. Without it every
    #: caller behind the gateway looks like one address and shares one
    #: allowance -- a handful of failed sign-ins anywhere would lock the
    #: whole deployment out. Where the name does not resolve (a service run
    #: on the host) it matches no peer and the socket address is used.
    trusted_proxies: str = "gateway"
    nats_url: str = "nats://localhost:4222"

    # OIDC (Keycloak)
    oidc_issuer: str = "http://localhost:8080/realms/chirala"
    oidc_audience: str = "chirala-pms"
    oidc_jwks_url: str = (
        "http://localhost:8080/realms/chirala/protocol/openid-connect/certs"
    )

    # Object storage (MinIO, S3-compatible).
    # ``minio_endpoint`` is how *services* reach MinIO (inside the compose
    # network). ``minio_public_endpoint`` is how a *browser* reaches it, and is
    # what presigned URLs are signed against — the two differ in Docker, so
    # they are configured separately rather than derived from one another.
    minio_endpoint: str = "localhost:9010"
    minio_public_endpoint: str = "http://localhost:9010"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "chirala-pms"
    minio_secure: bool = False
    # How long a presigned image URL stays valid.
    minio_url_ttl_seconds: int = 7 * 24 * 3600

    #: Keys that encrypt stored credentials, newest first, comma-separated.
    #:
    #: Shared rather than per-service because a credential sealed by one
    #: service has to be readable by another: finance writes a tenant's gateway
    #: secret, and whatever later calls the gateway must open it. Two services
    #: with different keys would look like corruption.
    #:
    #: Generate one with::
    #:
    #:     python -c "from chirala_common.secretbox import generate_key; print(generate_key())"
    credential_encryption_keys: str = ""

    # --- outgoing mail ------------------------------------------------
    # Here rather than per-service because three services now send mail —
    # iam a welcome, finance the audit summary, booking-core a guest's
    # confirmation — and they must all reach the same mailbox from the same
    # envelope. Three copies of these eight names is three places for a
    # deployment's SMTP settings to disagree.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_sender: str = ""
    smtp_sender_name: str = "Chirala Bay PMS"
    smtp_starttls: bool = True

    #: The address a person reaches this deployment at, used to build links
    #: inside mail. Empty means "no link", which every sender must handle: a
    #: link to localhost in somebody's inbox is worse than no link at all.
    app_base_url: str = ""

    @property
    def mail_config(self) -> MailConfig:
        return MailConfig(
            host=self.smtp_host, port=self.smtp_port,
            username=self.smtp_username, password=self.smtp_password,
            sender=self.smtp_sender, sender_name=self.smtp_sender_name,
            starttls=self.smtp_starttls,
        )


#: The password the repository's own examples use. A database URL carrying it
#: outside local is a deployment that copied ``.env.example`` and stopped.
_DEV_DB_PASSWORD = "pms_dev_password"


def unsafe_for_deployment(settings: BaseServiceSettings) -> list[str]:
    """What is wrong with these settings for anything but a developer's machine.

    Empty in local mode: the repository's defaults are meant for exactly
    that. Everywhere else each entry is a plain sentence naming the variable
    to set, because the person reading it is at a terminal with a service
    that will not start.
    """
    if settings.environment == "local":
        return []
    problems = []
    if not settings.session_signing_key.strip():
        problems.append(
            "SESSION_SIGNING_KEY is not set, so sessions could not be signed.")
    elif min(len(k.strip()) for k in settings.session_signing_key.split(",")
             if k.strip()) < 32:
        problems.append("SESSION_SIGNING_KEY is shorter than 32 characters.")
    if "minioadmin" in (settings.minio_access_key, settings.minio_secret_key):
        problems.append(
            "MINIO_ACCESS_KEY / MINIO_SECRET_KEY are still the MinIO defaults "
            "(minioadmin), which anyone can guess.")
    if not settings.credential_encryption_keys.strip():
        problems.append(
            "CREDENTIAL_ENCRYPTION_KEYS is not set, so stored credentials "
            "and second-factor secrets cannot be sealed.")
    # Every service names its database URLs differently (IAM_DATABASE_URL,
    # BOOKING_MIGRATION_DATABASE_URL, ...), so they are found by shape rather
    # than listed here, where a new one would be missed.
    for name, value in settings.model_dump().items():
        if (name.endswith("database_url") and isinstance(value, str)
                and _DEV_DB_PASSWORD in value):
            problems.append(
                f"{name.upper()} uses the example database password.")
    return problems


def refuse_unsafe_boot(settings: BaseServiceSettings, service: str) -> None:
    """Stop a service starting with settings only fit for a laptop.

    Called first thing in each service's lifespan. A refusal to start is
    loud, immediate and seen by whoever deployed it; the same mistake left
    running is a forgeable session or a guessable object store, seen first by
    somebody else.
    """
    problems = unsafe_for_deployment(settings)
    if problems:
        raise RuntimeError(
            f"{service} will not start with ENVIRONMENT={settings.environment!r}:"
            "\n  - " + "\n  - ".join(problems)
            + "\nFix these, or set ENVIRONMENT=local on a development machine.")
