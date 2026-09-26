"""Sign-in challenges carry a one-time nonce and refuse the old shape.

The spending of that nonce, the lockout at verification and the code needed
to re-enrol an active factor all need the database; they were checked
against the running stack (see the commit that added this file).
"""

from __future__ import annotations

import base64
import time
import uuid

import pytest
from chirala_common.secretbox import generate_key, seal
from fastapi import HTTPException


@pytest.fixture()
def mfa(monkeypatch):
    from iam_service import mfa_routes

    monkeypatch.setattr(mfa_routes.settings, "credential_encryption_keys",
                        generate_key())
    return mfa_routes


def test_each_challenge_has_its_own_nonce(mfa):
    uid = uuid.uuid4()
    a = mfa.read_challenge(mfa.make_challenge(uid))
    b = mfa.read_challenge(mfa.make_challenge(uid))
    assert a[0] == b[0] == uid
    assert a[1] != b[1] and len(a[1]) == 32
    assert a[2] > time.time()


def test_a_challenge_without_a_nonce_is_refused(mfa):
    keys = mfa.settings.credential_encryption_keys
    legacy = f"{uuid.uuid4()}:{int(time.time()) + 300}"
    token = base64.urlsafe_b64encode(seal(keys, legacy).encode()).decode()
    with pytest.raises(HTTPException) as e:
        mfa.read_challenge(token)
    assert e.value.status_code == 401


def test_an_expired_challenge_is_refused(mfa):
    keys = mfa.settings.credential_encryption_keys
    old = f"{uuid.uuid4()}:{'a' * 32}:{int(time.time()) - 1}"
    token = base64.urlsafe_b64encode(seal(keys, old).encode()).decode()
    with pytest.raises(HTTPException):
        mfa.read_challenge(token)
