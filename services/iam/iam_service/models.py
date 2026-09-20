"""IAM domain models (schema §2).

Covers the core of the organization/property/access model: organizations,
properties, property_modules, users, memberships, permissions, roles,
role_permissions, role_assignments. Additional §2 tables (screens, invitations,
sessions, approval_policies, support access) are added in later migrations.

All IAM tables live in the ``iam`` PostgreSQL schema.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from chirala_common.models import (
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    VersionMixin,
)
from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

SCHEMA = "iam"


class Organization(Base, UUIDPrimaryKeyMixin, TimestampMixin, VersionMixin):
    """Tenant boundary (§2)."""

    __tablename__ = "organizations"
    __table_args__ = {"schema": SCHEMA}

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="active"
    )


class Property(Base, UUIDPrimaryKeyMixin, TimestampMixin, VersionMixin):
    """A hotel/resort property. Unique organization + code (§2)."""

    __tablename__ = "properties"
    __table_args__ = (
        UniqueConstraint("organization_id", "code", name="uq_property_org_code"),
        # Parent unique key for property-scoped composite FKs.
        UniqueConstraint("organization_id", "id", name="uq_property_org_id"),
        {"schema": SCHEMA},
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.organizations.id"),
        nullable=False,
    )
    code: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    checkin_time: Mapped[str | None] = mapped_column(String(5), nullable=True)
    checkout_time: Mapped[str | None] = mapped_column(String(5), nullable=True)
    address: Mapped[str | None] = mapped_column(String(400), nullable=True)
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="active"
    )


class PropertyModule(Base, TimestampMixin):
    """Module entitlement per property (§2). PK = (property_id, module_code)."""

    __tablename__ = "property_modules"
    __table_args__ = {"schema": SCHEMA}

    property_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.properties.id"),
        primary_key=True,
    )
    module_code: Mapped[str] = mapped_column(String(50), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    enabled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class User(Base, UUIDPrimaryKeyMixin, TimestampMixin, VersionMixin):
    """Global identity (§2). No plaintext passwords; unique provider + subject."""

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint(
            "identity_provider", "subject_id", name="uq_user_provider_subject"
        ),
        {"schema": SCHEMA},
    )

    identity_provider: Mapped[str] = mapped_column(String(100), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    # What the person types to sign in, and where their welcome link goes.
    # Added in 0017/0019 but never declared here, so anything writing a user
    # through the ORM silently dropped both.
    email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="active"
    )
    mfa_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="disabled"
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Membership(Base, UUIDPrimaryKeyMixin, TimestampMixin, VersionMixin):
    """A user's membership in an organization (§2). Unique organization + user."""

    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "user_id", name="uq_membership_org_user"
        ),
        UniqueConstraint(
            "organization_id", "id", name="uq_membership_org_id"
        ),
        {"schema": SCHEMA},
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.organizations.id"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.users.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="active"
    )


class Permission(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Global permission catalogue (§2). Unique resource + action pair."""

    __tablename__ = "permissions"
    __table_args__ = (
        UniqueConstraint(
            "resource_code", "action_code", name="uq_permission_resource_action"
        ),
        {"schema": SCHEMA},
    )

    resource_code: Mapped[str] = mapped_column(String(100), nullable=False)
    action_code: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(String(300), nullable=True)


class Role(Base, UUIDPrimaryKeyMixin, TimestampMixin, VersionMixin):
    """Organization-owned role (§2). Unique organization + code."""

    __tablename__ = "roles"
    __table_args__ = (
        UniqueConstraint("organization_id", "code", name="uq_role_org_code"),
        UniqueConstraint("organization_id", "id", name="uq_role_org_id"),
        {"schema": SCHEMA},
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.organizations.id"),
        nullable=False,
    )
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    template_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class RolePermission(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Role↔permission with record scope (§2). Unique role + permission."""

    __tablename__ = "role_permissions"
    __table_args__ = (
        UniqueConstraint(
            "role_id", "permission_id", name="uq_role_permission"
        ),
        {"schema": SCHEMA},
    )

    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.roles.id"), nullable=False
    )
    permission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.permissions.id"), nullable=False
    )
    # Scope of records the permission applies to: property | assigned | own.
    record_scope: Mapped[str] = mapped_column(
        String(20), nullable=False, default="property"
    )


class RoleAssignment(Base, UUIDPrimaryKeyMixin, TimestampMixin, VersionMixin):
    """Scoped role grant to a membership (§2).

    Scope is explicit (organization/property/outlet). ``property_id`` and
    ``outlet_id`` are nullable and validated by ``scope_type`` in the service
    layer; an outlet must belong to its property.
    """

    __tablename__ = "role_assignments"
    __table_args__ = {"schema": SCHEMA}

    membership_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.memberships.id"), nullable=False
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.roles.id"), nullable=False
    )
    property_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.properties.id"), nullable=True
    )
    outlet_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    scope_type: Mapped[str] = mapped_column(String(20), nullable=False)
    valid_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)
