"""A deployment that forgot to configure itself fails closed.

No database and no running services: the settings are built directly and the
gateway is driven with its upstream replaced by a recorder.
"""

from __future__ import annotations

import httpx
import pytest
from chirala_common.config import (
    BaseServiceSettings,
    refuse_unsafe_boot,
    unsafe_for_deployment,
)

SAFE = dict(
    session_signing_key="s" * 40,
    minio_access_key="real-access", minio_secret_key="real-secret",
    credential_encryption_keys="fernet-key",
)


def _settings(monkeypatch, **overrides):
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    return BaseServiceSettings(_env_file=None, **overrides)


def test_unset_environment_means_production(monkeypatch):
    assert _settings(monkeypatch).environment == "production"


def test_local_accepts_the_repository_defaults(monkeypatch):
    s = _settings(monkeypatch, environment="local")
    assert unsafe_for_deployment(s) == []
    refuse_unsafe_boot(s, "test")


def test_each_unsafe_default_is_named(monkeypatch):
    s = _settings(monkeypatch)
    problems = " ".join(unsafe_for_deployment(s))
    for name in ("SESSION_SIGNING_KEY", "MINIO_ACCESS_KEY",
                 "CREDENTIAL_ENCRYPTION_KEYS"):
        assert name in problems
    with pytest.raises(RuntimeError, match="will not start"):
        refuse_unsafe_boot(s, "test")


def test_fully_configured_boots(monkeypatch):
    refuse_unsafe_boot(_settings(monkeypatch, **SAFE), "test")


def test_short_signing_key_is_refused(monkeypatch):
    s = _settings(monkeypatch, **{**SAFE, "session_signing_key": "short"})
    assert any("32" in p for p in unsafe_for_deployment(s))


def test_example_database_password_is_refused(monkeypatch):
    class WithDb(BaseServiceSettings):
        iam_database_url: str = ""

    monkeypatch.delenv("ENVIRONMENT", raising=False)
    s = WithDb(_env_file=None, **SAFE, iam_database_url=(
        "postgresql+psycopg://pms:pms_dev_password@db:5432/chirala_pms"))
    assert unsafe_for_deployment(s) == [
        "IAM_DATABASE_URL uses the example database password."]


def test_near_misses_are_not_local(monkeypatch):
    for env in ("Local", "dev", "development", "local "):
        s = _settings(monkeypatch, environment=env)
        assert unsafe_for_deployment(s), env


def test_gateway_strips_debug_headers(monkeypatch):
    from fastapi.testclient import TestClient
    from gateway_app import main as gw

    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update({k.lower(): v for k, v in request.headers.items()})
        return httpx.Response(200, json={"ok": True})

    real = httpx.AsyncClient

    class Recording(real):
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.MockTransport(handler)
            super().__init__(*a, **kw)

    monkeypatch.setattr(gw.httpx, "AsyncClient", Recording)
    r = TestClient(gw.app).get(
        "/api/booking/dashboard",
        headers={"X-Debug-Subject": "admin-user", "X-Debug-Anything": "1",
                 "Authorization": "Bearer abc"})
    assert r.status_code == 200
    assert "x-debug-subject" not in seen
    assert "x-debug-anything" not in seen
    assert seen.get("authorization") == "Bearer abc"
