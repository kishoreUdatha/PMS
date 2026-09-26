"""A Channex revision id is a UUID before it goes anywhere near a URL.

Refused before the database or Channex is touched, so neither is needed.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch):
    from booking_core import channel_routes
    from booking_core.main import app

    monkeypatch.setattr(channel_routes.settings, "channex_webhook_secret", "s3cret")

    def refuse(*_a, **_k):  # pragma: no cover - reaching it is the failure
        raise AssertionError("an invalid revision id reached ingestion")

    monkeypatch.setattr(channel_routes, "ingest", refuse)
    return TestClient(app)


@pytest.mark.parametrize("rid", [
    "../../properties/abc", "abc?x=1", "abc#frag", "not-a-uuid",
    "00000000-0000-0000-0000-000000000000/../x"])
def test_a_revision_id_that_is_not_a_uuid_is_not_fetched(client, rid):
    r = client.post("/channels/channex/webhook",
                    headers={"x-channex-webhook-secret": "s3cret"},
                    json={"event": "booking_new", "payload": {"revision_id": rid}})
    assert r.status_code == 200
    assert r.json()["status"] == "invalid_revision_id"
