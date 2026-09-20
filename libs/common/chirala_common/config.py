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

    environment: str = "local"
    log_level: str = "INFO"

    #: Shared secret for calls between this deployment's own services.
    #:
    #: A webhook, a scheduled job, or one service finishing work another
    #: started has no user to authenticate as. Unset, service authentication
    #: is off and every caller must be a person — the right default, so a
    #: deployment that forgot to configure a secret cannot accept an empty one.
    service_token: str = ""

    # Infrastructure
    redis_url: str = "redis://localhost:6379/0"
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
