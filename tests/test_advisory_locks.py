"""One replica per sweep cycle, and one migration runner at a time.

Every booking-core replica runs the same background loops. Without a lock,
N replicas poll the channel feed N times a cycle, provision at the channel
manager N times, and re-price the same calendar N times at once. These check
the lock that stops that, against a real Postgres -- advisory locks are a
server feature, and a fake would only prove the fake.
"""

from __future__ import annotations

import os
import threading
import time

import pytest

DB_URL = os.getenv("BOOKING_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DB_URL, reason="BOOKING_DATABASE_URL not set; integration test skipped")


@pytest.fixture(scope="module")
def engine():
    from chirala_common.db import make_engine
    eng = make_engine(DB_URL)
    yield eng
    eng.dispose()


def test_two_replicas_one_sweep(engine):
    from chirala_common.locks import run_exclusively

    ran: list[str] = []
    results: dict[str, str] = {}
    barrier = threading.Barrier(2)

    def sweep(who: str) -> str:
        ran.append(who)
        time.sleep(0.5)  # the other replica arrives while this one is working
        return who

    def replica(who: str) -> None:
        barrier.wait()
        results[who] = run_exclusively(engine, "chirala:test:sweep", sweep, who,
                                       if_busy="skipped")

    threads = [threading.Thread(target=replica, args=(w,)) for w in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(ran) == 1, f"both replicas ran the sweep: {ran}"
    assert sorted(results.values()) == sorted([ran[0], "skipped"])


def test_the_lock_is_released_even_when_the_sweep_fails(engine):
    from chirala_common.locks import run_exclusively

    def boom() -> None:
        raise RuntimeError("sweep failed")

    with pytest.raises(RuntimeError):
        run_exclusively(engine, "chirala:test:fail", boom)
    # The next cycle is not locked out by the one that failed.
    assert run_exclusively(engine, "chirala:test:fail", lambda: "ran",
                           if_busy="skipped") == "ran"


def test_migration_lock_blocks_a_second_runner(engine):
    from chirala_common.locks import migration_lock
    from sqlalchemy import text

    with engine.connect() as first, migration_lock(first, "chirala:test:migrate"):
        with engine.connect() as second:
            got = second.execute(text(
                "SELECT pg_try_advisory_lock(hashtext('chirala:test:migrate'))")).scalar()
            second.rollback()
        assert got is False
    with engine.connect() as third:
        got = third.execute(text(
            "SELECT pg_try_advisory_lock(hashtext('chirala:test:migrate'))")).scalar()
        third.execute(text("SELECT pg_advisory_unlock(hashtext('chirala:test:migrate'))"))
        third.rollback()
    assert got is True
