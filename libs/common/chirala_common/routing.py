"""Route class that commits a request's transaction before the response is sent.

FastAPI runs the teardown of a ``yield`` dependency *after* the response has
already reached the client. With the usual "commit in ``get_session``" pattern
that has two consequences, both measured on this stack:

* **Read-your-writes breaks.** A client that acts on a ``201`` — say, creating a
  room block and immediately ending it — can issue the follow-up request inside
  the window before the commit lands and be told the record does not exist.
  The observed window was 2-22 ms, as wide as the commit itself takes.
* **A failed commit is reported as success.** This is the serious half. The
  status line has already gone out, so a deferred constraint violation, a
  serialization failure or a dropped connection during ``COMMIT`` leaves the
  caller holding a ``201`` for a write that no longer exists, with no way to
  find out.

Routers built with ``route_class=TransactionalRoute`` commit here instead: after
the endpoint has produced and serialized its response, but before Starlette
sends it. A commit failure then propagates as a normal error response, and any
follow-up request is guaranteed to see the write.

The session is found on ``request.state``; ``get_session`` puts it there. Routes
that never touch the database are unaffected.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import Request, Response
from fastapi.routing import APIRoute
from starlette.concurrency import run_in_threadpool

#: Attribute on ``request.state`` where the request's session is published.
SESSION_STATE_ATTR = "db_session"


class TransactionalRoute(APIRoute):
    """An ``APIRoute`` that commits before responding. See the module docstring."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original_handler = super().get_route_handler()

        async def transactional_handler(request: Request) -> Response:
            # If the endpoint raised, this propagates and the dependency's
            # teardown rolls back — we never reach the commit.
            response = await original_handler(request)
            session = getattr(request.state, SESSION_STATE_ATTR, None)
            if session is not None and session.in_transaction():
                # Blocking I/O, so keep it off the event loop.
                await run_in_threadpool(session.commit)
            return response

        return transactional_handler
