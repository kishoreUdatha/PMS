"""Session tokens are signed, expire, and can be revoked.

No database: revocation is exercised through a stand-in session that answers
the one query ``is_revoked`` asks. The end-to-end behaviour -- sign in, use the
token at booking-core and finance, sign out, be refused everywhere -- needs
the running stack and is covered by the scripted check in the commit message.
"""

from __future__ import annotations

import base64
import json
import time

from chirala_common import session_tokens as st

KEY = "k" * 40


def _parts(tok):
    return tok.split(".")


def _b64json(obj):
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()


def test_round_trip_carries_the_claims():
    tok = st.issue("alice-1", key=KEY, ttl_seconds=60, environment="production")
    c = st.decode(tok, key=KEY, environment="production")
    assert c.subject == "alice-1"
    assert c.jti and len(c.jti) == 32
    assert c.expires_at - time.time() <= 60
    payload = json.loads(base64.urlsafe_b64decode(_parts(tok)[1] + "=="))
    assert set(payload) == {"sub", "iat", "exp", "jti"}


def test_every_token_is_distinct():
    a = st.issue("alice-1", key=KEY, ttl_seconds=60, environment="production")
    b = st.issue("alice-1", key=KEY, ttl_seconds=60, environment="production")
    assert a != b


def test_a_changed_subject_is_refused():
    tok = st.issue("alice-1", key=KEY, ttl_seconds=60, environment="production")
    h, p, s = _parts(tok)
    payload = json.loads(base64.urlsafe_b64decode(p + "=="))
    payload["sub"] = "mallory"
    assert st.decode(f"{h}.{_b64json(payload)}.{s}", key=KEY,
                     environment="production") is None


def test_the_wrong_key_is_refused():
    tok = st.issue("alice-1", key=KEY, ttl_seconds=60, environment="production")
    assert st.decode(tok, key="another-key-" * 4, environment="production") is None


def test_an_expired_token_is_refused():
    tok = st.issue("alice-1", key=KEY, ttl_seconds=60, environment="production")
    assert st.decode(tok, key=KEY, environment="production",
                     now=time.time() + 61) is None


def test_alg_none_is_refused():
    tok = st.issue("alice-1", key=KEY, ttl_seconds=60, environment="production")
    _, p, _ = _parts(tok)
    forged = f"{_b64json({'alg': 'none', 'typ': 'JWT'})}.{p}."
    assert st.decode(forged, key=KEY, environment="production") is None


def test_old_keys_still_verify_after_rotation():
    tok = st.issue("alice-1", key="old-key-" * 5, ttl_seconds=60,
                   environment="production")
    rotated = "new-key-" * 5 + "," + "old-key-" * 5
    assert st.decode(tok, key=rotated, environment="production").subject == "alice-1"


def test_dev_tokens_only_in_local():
    dev = base64.urlsafe_b64encode(b"dev:alice-1").decode()
    assert st.decode(dev, key="", environment="local").subject == "alice-1"
    assert st.decode(dev, key=KEY, environment="local").subject == "alice-1"
    for env in ("production", "staging", "Local", "local ", "dev", ""):
        assert st.decode(dev, key="", environment=env) is None, env
        assert st.decode(dev, key=KEY, environment=env) is None, env


def test_no_key_outside_local_refuses_to_issue():
    try:
        st.issue("alice-1", key="", environment="production")
    except RuntimeError:
        pass
    else:  # pragma: no cover
        raise AssertionError("issued a token with no signing key")
    # And local falls back to the dev token, so a developer's stack works.
    tok = st.issue("alice-1", key="", environment="local")
    assert base64.urlsafe_b64decode(tok).decode() == "dev:alice-1"


class _Db:
    """Answers ``is_revoked``'s one query from a set."""

    def __init__(self, revoked):
        self.revoked = revoked

    def execute(self, _sql, params):
        hit = params["j"] in self.revoked

        class _R:
            def first(self_inner):
                return (1,) if hit else None

        return _R()


def test_revoked_tokens_are_refused(monkeypatch):
    monkeypatch.setattr(st, "_keys", lambda raw=None: [KEY.encode()])
    tok = st.issue("alice-1", key=KEY, ttl_seconds=60, environment="production")
    jti = st.decode(tok, key=KEY, environment="production").jti
    assert st.subject_from_bearer(f"Bearer {tok}", _Db(set())) == "alice-1"
    assert st.subject_from_bearer(f"Bearer {tok}", _Db({jti})) is None
    assert st.subject_from_bearer(None, _Db(set())) is None
    assert st.subject_from_bearer(tok, _Db(set())) is None  # no "Bearer "
