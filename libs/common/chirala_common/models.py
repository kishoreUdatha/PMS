"""Declarative base and standard column mixins.

Encodes the schema conventions from §1:
- UUID ``id`` primary keys (via pgcrypto ``gen_random_uuid()``).
- ``created_at`` / ``updated_at`` (timestamptz).
- ``created_by`` actor reference.
- ``version`` bigint for optimistic concurrency.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class UUIDPrimaryKeyMixin:
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class VersionMixin:
    """Optimistic-concurrency version counter (§1)."""

    version: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
