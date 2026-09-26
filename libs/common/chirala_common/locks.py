"""Postgres advisory locks: one runner at a time, across processes and hosts.

Two places need "only one of us does this":

* **Migrations.** Every service used to run ``alembic upgrade head`` as it
  started, so three containers (or three replicas of one) could migrate the
  same database at once. Alembic has no locking of its own: two runners both
  read the same ``alembic_version`` and both try to apply the next revision.
  :func:`migration_lock` makes them queue.

* **Background sweeps.** A loop that runs in every replica runs N times per
  cycle with N replicas -- N polls of a channel feed, N occupancy recounts.
  :func:`try_advisory_xact_lock` lets the first replica take the cycle and the
  others skip it quietly.

Keys are derived from a readable name with ``hashtext`` inside Postgres, the
same way ``runtime_role`` names its lock, so a name is the only thing a caller
has to keep stable -- and ``pg_locks`` can be matched against it when somebody
needs to know who is holding what.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import text
from sqlalchemy.engine import Connection

#: One key for every service's migrations, not one per service. The schemas
#: are separate but the migrations are not independent -- booking-core's
#: reference finance.folios and iam's tables -- so letting iam and
#: booking-core migrate concurrently is the same race in a different shape.
MIGRATION_LOCK = "chirala:migrations"


@contextmanager
def migration_lock(connection: Connection, name: str = MIGRATION_LOCK) -> Iterator[None]:
    """Hold a session-level advisory lock on ``connection`` for the block.

    Session-level rather than transaction-level because Alembic may commit
    more than once (``transaction_per_migration``) and the lock must outlast
    every one of those commits.

    The ``commit()`` after taking it matters: SQLAlchemy 2 autobegins a
    transaction on the first statement, and Alembic's ``begin_transaction``
    treats an already-open transaction as the caller's to commit -- so
    without it the migrations would run and then be rolled back when the
    connection closed. The lock itself survives the commit.
    """
    connection.execute(text("SELECT pg_advisory_lock(hashtext(:n))"), {"n": name})
    connection.commit()
    try:
        yield
    finally:
        # A failed migration leaves the transaction aborted; roll it back
        # first or the unlock itself is refused.
        if connection.in_transaction():
            connection.rollback()
        connection.execute(text("SELECT pg_advisory_unlock(hashtext(:n))"), {"n": name})
        connection.commit()
