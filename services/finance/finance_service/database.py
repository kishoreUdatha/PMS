"""Database wiring for the finance service."""

from __future__ import annotations

from collections.abc import Iterator

from chirala_common.db import make_engine, make_session_factory
from fastapi import Request
from sqlalchemy.orm import Session

from .settings import settings

engine = make_engine(settings.finance_database_url)
SessionFactory = make_session_factory(engine)


def get_session(request: Request) -> Iterator[Session]:
    """FastAPI dependency: one request == one transaction.

    Financial writes (allocations, refunds, postings) depend on this so that
    SELECT FOR UPDATE locks are held until commit/rollback.
    """
    session = SessionFactory()
    # Published for TransactionalRoute, which commits before the response is
    # sent. Committing here instead would happen after the client already has
    # the response — see chirala_common.routing.
    request.state.db_session = session
    try:
        yield session
        if session.in_transaction():
            # Fallback for any route not built with TransactionalRoute; keeps
            # the old behaviour rather than silently dropping the write.
            session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
