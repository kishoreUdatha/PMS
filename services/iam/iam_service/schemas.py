"""Pydantic request/response models for the IAM service."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class OrganizationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class OrganizationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    status: str
    created_at: datetime


class PropertyCreate(BaseModel):
    #: Accepted and ignored. The code is allocated by the server, because it
    #: has to be unique across every tenant and a caller cannot check that
    #: without being told about properties that are not theirs.
    code: str | None = Field(default=None, max_length=30, deprecated=True)
    name: str = Field(min_length=1, max_length=200)
    timezone: str = Field(default="Asia/Kolkata", max_length=64)
    currency: str = Field(default="INR", min_length=3, max_length=3)
    checkin_time: str | None = None
    checkout_time: str | None = None


class PropertyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    code: str
    name: str
    timezone: str
    currency: str
    status: str


class PropertySettingsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    code: str
    name: str
    timezone: str
    currency: str
    checkin_time: str | None = None
    checkout_time: str | None = None
    address: str | None = None
    status: str
    version: int


class PropertySettingsUpdate(BaseModel):
    # Optimistic concurrency: the version the client last read.
    version: int
    name: str = Field(min_length=1, max_length=200)
    timezone: str = Field(min_length=1, max_length=64)
    currency: str = Field(min_length=3, max_length=3)
    checkin_time: str | None = None
    checkout_time: str | None = None
    address: str | None = Field(default=None, max_length=400)
    reason: str | None = Field(default=None, max_length=400)


class UserCreate(BaseModel):
    identity_provider: str = Field(max_length=100)
    subject_id: str = Field(max_length=255)
    display_name: str = Field(max_length=200)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    identity_provider: str
    subject_id: str
    display_name: str
    status: str


class UserListItem(BaseModel):
    id: uuid.UUID
    identity_provider: str
    subject_id: str
    display_name: str
    status: str
    version: int
    membership_status: str | None = None
    employee_code: str | None = None
    department: str | None = None
    properties: list[str] = []
    roles: list[str] = []
    last_login_at: str | None = None
    mfa_status: str = "disabled"
    active_sessions: int = 0


class DepartmentOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str


class RoleOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str


class ActivityOut(BaseModel):
    action: str
    entity_type: str
    reason: str | None = None
    occurred_at: str


class InvitationCreate(BaseModel):
    property_id: uuid.UUID
    full_name: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=3, max_length=200)
    phone: str | None = Field(default=None, max_length=40)
    employee_code: str | None = Field(default=None, max_length=30)
    department_id: uuid.UUID | None = None
    role_ids: list[uuid.UUID] = Field(default_factory=list)
    outlet_ids: list[uuid.UUID] = Field(default_factory=list)
    mfa_required: bool = False
    temporary_access: bool = False
    access_expiry: date | None = None
    max_discount_approval: Decimal | None = None
    refund_approval: bool = False


class InvitationOut(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    status: str
    created_user_id: uuid.UUID | None = None
    role_count: int = 0


class UserAccessOut(BaseModel):
    user_id: uuid.UUID
    full_name: str
    email: str
    phone: str | None = None
    employee_code: str | None = None
    department_id: uuid.UUID | None = None
    property_id: uuid.UUID | None = None
    role_ids: list[uuid.UUID] = []
    mfa_required: bool = False
    temporary_access: bool = False
    access_expiry: date | None = None
    max_discount_approval: Decimal | None = None
    refund_approval: bool = False
    version: int = 0


class UserAccessUpdate(BaseModel):
    version: int
    full_name: str = Field(min_length=1, max_length=200)
    phone: str | None = None
    employee_code: str | None = None
    department_id: uuid.UUID | None = None
    property_id: uuid.UUID
    role_ids: list[uuid.UUID] = Field(default_factory=list)
    mfa_required: bool = False
    temporary_access: bool = False
    access_expiry: date | None = None
    max_discount_approval: Decimal | None = None
    refund_approval: bool = False
    reason: str | None = Field(default=None, max_length=400)


class ModuleDef(BaseModel):
    code: str
    label: str


class RoleCatalogue(BaseModel):
    modules: list[ModuleDef]
    actions: list[str]


class RoleListItem(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    user_count: int = 0


class RoleMatrixOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    user_count: int
    permissions: dict[str, dict[str, bool]]
    record_scope: str
    property_scope: str
    max_discount: Decimal | None = None
    max_refund: Decimal | None = None


class RoleMatrixUpdate(BaseModel):
    permissions: dict[str, dict[str, bool]]
    record_scope: str = "assigned"
    property_scope: str = "own"
    max_discount: Decimal | None = None
    max_refund: Decimal | None = None
    reason: str | None = Field(default=None, max_length=400)


class RoleCreate2(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    code: str | None = None


class RoleClone(BaseModel):
    name: str = Field(min_length=1, max_length=150)


class ApprovalRequestOut(BaseModel):
    id: uuid.UUID
    category: str
    title: str
    entity_ref: str | None = None
    guest_name: str | None = None
    requested_by_name: str | None = None
    requested_by_role: str | None = None
    policy_rule_text: str | None = None
    amount: Decimal
    amount_context: str | None = None
    status: str
    due_at: str | None = None
    due_in: str | None = None


class ApprovalPolicyOut(BaseModel):
    id: uuid.UUID
    name: str
    category: str
    applies_to: str | None = None
    initiator_roles: list[str] = []
    approver_roles: list[str] = []
    threshold_value: Decimal | None = None
    threshold_unit: str = "%"
    two_level: bool = False
    prohibit_self_approval: bool = True


class ApprovalDecisionIn(BaseModel):
    comment: str | None = Field(default=None, max_length=400)


class ApprovalHistoryOut(BaseModel):
    id: uuid.UUID
    category: str
    title: str
    entity_ref: str | None = None
    amount: Decimal
    status: str
    decided_by: str | None = None
    decided_at: str | None = None


class AuditEventOut(BaseModel):
    id: uuid.UUID
    occurred_at: str
    actor_subject: str | None = None
    action: str
    entity_type: str
    entity_id: str | None = None
    summary: str
    risk: str
    result: str = "Success"
    correlation_id: str | None = None


class AuditEventDetail(BaseModel):
    id: uuid.UUID
    occurred_at: str
    actor_subject: str | None = None
    action: str
    entity_type: str
    entity_id: str | None = None
    reason: str | None = None
    correlation_id: str | None = None
    risk: str
    result: str = "Success"
    before: dict | None = None
    after: dict | None = None


class UserManagementCreate(BaseModel):
    identity_provider: str = Field(default="keycloak", max_length=100)
    subject_id: str = Field(min_length=1, max_length=255)
    display_name: str = Field(min_length=1, max_length=200)


class UserManagementUpdate(BaseModel):
    version: int
    display_name: str = Field(min_length=1, max_length=200)
    reason: str | None = Field(default=None, max_length=400)


class UserActiveToggle(BaseModel):
    reason: str | None = Field(default=None, max_length=400)


class UserStatsOut(BaseModel):
    active: int
    invited: int
    suspended: int
    active_sessions: int


class ApprovalRequestIn(BaseModel):
    """Raise an approval request against whatever policy governs the category."""

    category: str = Field(max_length=40)
    title: str = Field(max_length=200)
    amount: Decimal = Field(default=Decimal("0"))
    amount_context: str | None = Field(default=None, max_length=200)
    entity_ref: str | None = Field(default=None, max_length=120)
    guest_name: str | None = Field(default=None, max_length=200)
    property_id: uuid.UUID | None = None
    # How long the decision has before it is overdue in the queue.
    due_in_hours: int | None = Field(default=None, ge=1, le=720)


class ApprovalRequestCreated(BaseModel):
    id: uuid.UUID
    status: str
    category: str
    # Which policy caught it, and what that policy says. Null when no policy
    # governs the category — the request is still raised, and says so.
    policy_name: str | None = None
    policy_rule_text: str | None = None
    approver_roles: list[str] = []
    required: bool


# ---- Module entitlements (what a property is allowed to use) --------------
class ModuleOut(BaseModel):
    """One module and whether this property has it."""

    module_code: str
    label: str
    #: A property with no row is not entitled, so this is False for a module
    #: nobody has ever switched on.
    enabled: bool
    enabled_at: datetime | None = None


class ModuleIn(BaseModel):
    enabled: bool
