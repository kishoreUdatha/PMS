"""A record of every message this platform tried to send.

Until now a send either worked or raised, and in several places the raise was
caught and turned into a flag on a response nobody kept. So "did that owner
ever get their welcome mail?" had no answer, and the only way to find out was
to ask them.

Two decisions worth stating.

**The log records the attempt, not the delivery.** SMTP hands back an accept,
not a confirmation, so ``sent`` here means the server took it. A later bounce
is a separate fact and gets its own row when something learns of one. Claiming
more than that would be inventing certainty.

**It writes on its own connection, like ``_record_denial``.** A mail that has
left the building has left it, and a request that rolls back afterwards must
not erase the evidence -- otherwise the one case where the log matters most,
the failed operation that nonetheless mailed somebody, is the one case it
stays silent about. The same connection also carries system context, which the
table's write policy requires and a tenant-scoped request session does not
have.

Recording never raises. A logging failure must not turn a message that was
delivered into an error the caller reports as a failure to send.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text

log = logging.getLogger("uvicorn.error").getChild("delivery")

_INSERT = text(
    """
    INSERT INTO platform.message_deliveries
        (organization_id, template_code, channel, recipient, subject,
         status, detail, attempts)
    VALUES (CAST(:org AS uuid), :code, :channel, :to, :subject,
            :status, :detail, :attempts)
    """
)


def record_delivery(
    session_factory: Any,
    *,
    template_code: str,
    recipient: str,
    subject: str | None = None,
    status: str = "sent",
    organization_id: Any = None,
    detail: str | None = None,
    channel: str = "email",
    attempts: int = 1,
) -> None:
    """Note one send attempt on its own transaction. Never raises.

    ``session_factory`` is the service's own ``SessionFactory``; passing the
    factory rather than a session is what keeps this independent of whatever
    the caller's transaction goes on to do.
    """
    # Imported here so this module can be used by a service whose settings
    # have not finished loading, and so the common package keeps no import
    # cycle back into db configuration.
    from .db import system_context

    params = {
        "org": str(organization_id) if organization_id else None,
        "code": template_code,
        "channel": channel,
        "to": recipient,
        "subject": (subject or "")[:300] or None,
        "status": status,
        "detail": (detail or "")[:400] or None,
        "attempts": attempts,
    }

    def write(values: dict[str, Any]) -> None:
        with session_factory() as session:
            system_context(session, reason="record an outbound message")
            session.execute(_INSERT, values)
            session.commit()

    try:
        write(params)
    except Exception:  # noqa: BLE001
        # If it was the tenant link that failed -- an organisation this
        # connection cannot see yet, or one already gone -- the message
        # still went out, and a row naming the recipient is worth far
        # more than no row at all. Losing the evidence is the one outcome
        # this module exists to prevent, so try again without the link.
        if params["org"] is not None:
            try:
                write({**params, "org": None})
                log.warning(
                    "recorded a %s delivery to %s without its tenant link",
                    template_code, recipient)
                return
            except Exception:  # noqa: BLE001 - same reason as below
                pass
        # Logged loudly, swallowed deliberately. See the module docstring.
        log.exception("could not record a %s delivery to %s",
                      template_code, recipient)
