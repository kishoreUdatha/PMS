"""require_property_permission asks the grant question about one property.

The database is a stand-in that records what it was asked; the end-to-end
behaviour (a role on hotel A refused at sister hotel B, still allowed at A)
was checked against the running stack.
"""

from __future__ import annotations

import uuid

import pytest
from chirala_common import authz
from fastapi import HTTPException


class _Db:
    def __init__(self, grant: bool):
        self.grant = grant
        self.asked = []

    def execute(self, sql, params):
        self.asked.append((str(sql), params))
        grant = self.grant

        class _R:
            def first(self_inner):
                return (1,) if grant else None

        return _R()


@pytest.fixture(autouse=True)
def _same_org(monkeypatch):
    monkeypatch.setattr(authz, "assert_property_in_org", lambda db, c, p: None)


def _caller(**kw):
    return authz.Caller(subject="s", user_id=uuid.uuid4(),
                        organization_id=uuid.uuid4(), **kw)


def test_the_property_is_passed_to_the_grant_query():
    prop = uuid.uuid4()
    db = _Db(grant=True)
    authz.require_property_permission(db, _caller(), prop, "payments", "create")
    sql, params = db.asked[-1]
    assert "role_assignments" in sql
    assert params["prop"] == prop and params["res"] == "payments"


def test_no_grant_on_that_property_is_refused():
    with pytest.raises(HTTPException) as e:
        authz.require_property_permission(
            _Db(grant=False), _caller(), uuid.uuid4(), "payments", "create")
    assert e.value.status_code == 403


def test_a_service_caller_is_held_to_tenancy_only():
    db = _Db(grant=False)
    authz.require_property_permission(
        db, _caller(is_service=True), uuid.uuid4(), "payments", "create")
    assert db.asked == []
