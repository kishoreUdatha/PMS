"""Request ids, readiness and loop heartbeats -- without a database.

``/ready`` exists to say 503 when the database is gone, so the case that
matters is checked against an engine pointed at a port with nothing on it,
not by mocking the check away.
"""

from __future__ import annotations

import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine


def _app(**kwargs) -> FastAPI:
    from chirala_common.observability import install_observability

    app = FastAPI()

    @app.get("/echo")
    def echo() -> dict:
        from chirala_common.observability import request_id
        logging.getLogger("test.echo").warning("handled")
        return {"id": request_id.get()}

    install_observability(app, service="test", **kwargs)
    return app


def test_an_incoming_request_id_is_kept_and_returned():
    client = TestClient(_app())
    resp = client.get("/echo", headers={"X-Request-ID": "gw-123"})
    assert resp.headers["x-request-id"] == "gw-123"
    assert resp.json()["id"] == "gw-123"


def test_a_missing_or_unsafe_id_is_replaced():
    client = TestClient(_app())
    minted = client.get("/echo")
    assert len(minted.headers["x-request-id"]) == 32
    # A caller-controlled header that ends up in logs must not carry a
    # newline -- that would let a caller forge log lines.
    forged = client.get("/echo", headers={"X-Request-ID": "a b\tc"})
    assert forged.headers["x-request-id"] != "a b\tc"
    assert forged.json()["id"] == forged.headers["x-request-id"]


def test_log_records_carry_the_request_id(caplog):
    from chirala_common.observability import RequestIdFilter

    client = TestClient(_app())
    caplog.handler.addFilter(RequestIdFilter())
    with caplog.at_level(logging.WARNING, logger="test.echo"):
        client.get("/echo", headers={"X-Request-ID": "log-me"})
    assert [r.request_id for r in caplog.records if r.name == "test.echo"] == ["log-me"]


def test_ready_is_503_when_the_database_is_unreachable():
    dead = create_engine("postgresql+psycopg://x:y@127.0.0.1:1/none?connect_timeout=1")
    resp = TestClient(_app(engine=dead)).get("/ready")
    assert resp.status_code == 503
    assert resp.json()["status"] == "unavailable"
    assert resp.json()["checks"]["database"] != "ok"


def test_an_optional_redis_down_degrades_but_stays_ready():
    resp = TestClient(_app(redis_url="redis://127.0.0.1:1/0")).get("/ready")
    if resp.json()["checks"]["redis"].startswith("ModuleNotFoundError"):
        pytest.skip("redis client not installed")  # only booking-core has it
    assert resp.status_code == 200
    assert resp.json()["status"] == "degraded"


def test_a_stale_loop_degrades_but_stays_ready(monkeypatch):
    from chirala_common import observability

    beats = observability.Heartbeats()
    monkeypatch.setattr(observability, "heartbeats", beats)
    beats.beat("fresh", 60)
    beats.beat("stuck", 1)
    # Three missed intervals of one second each.
    beats._beats["stuck"] = (beats._beats["stuck"][0] - 10, 1.0)
    resp = TestClient(_app(report_heartbeats=True)).get("/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["checks"]["loops"]["stuck"]["stale"] is True
    assert body["checks"]["loops"]["fresh"]["stale"] is False
