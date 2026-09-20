"""Every write path in the platform operations console, against live data.

    docker compose cp scripts/check_platform_operations.py iam:/app/check.py
    docker compose exec iam python /app/check.py

tests/test_platform_operations.py checks these rules by reading the source and
needs no database. This runs them: it calls each handler for real, against the
real schema, and asserts both that the allowed thing happens and that the
forbidden thing is refused for the right reason -- a constraint nobody has
watched reject anything is a constraint nobody knows works.

**It leaves nothing behind.** Everything runs on one session that is rolled
back at the end, so the database finishes exactly as it started. That matters
more than it sounds: this deployment has already had one false alarm caused by
synthetic rows somebody left in a real table and a later reader took for
production data.

Each check is wrapped in its own savepoint, because a failed statement poisons
the whole Postgres transaction and without that the first fault turns every
later check into InFailedSqlTransaction and hides what else is broken.
"""
import uuid
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import text

from chirala_common.db import system_context
from iam_service.database import SessionFactory
from iam_service.platform_authz import PlatformCaller
from iam_service import platform_ops_routes as ops

REQ = SimpleNamespace(headers={}, state=SimpleNamespace())
passed, failed = [], []
DB = []  # the session, once it exists


def _savepoint():
    """Isolate each check.

    A failed statement poisons the whole Postgres transaction, so without
    this the first fault turns every later check into
    InFailedSqlTransaction and hides what else is broken -- which is exactly
    what happened on the first run.
    """
    if DB:
        DB[0].execute(text("SAVEPOINT sp_check"))


def _release(failed_check):
    if DB:
        DB[0].execute(text("ROLLBACK TO SAVEPOINT sp_check" if failed_check
                           else "RELEASE SAVEPOINT sp_check"))


def ok(name, fn):
    _savepoint()
    try:
        fn()
        passed.append(name)
        print("  OK      " + name)
        _release(False)
    except Exception as exc:
        failed.append(name)
        line = str(exc).splitlines()[0][:150] if str(exc) else ""
        print("  FAIL    %s: %s: %s" % (name, type(exc).__name__, line))
        _release(True)


def refuses(name, fn, status, contains=""):
    """The interesting half: refused, and for the right reason."""
    _savepoint()
    try:
        fn()
    except HTTPException as exc:
        _release(True)
        if exc.status_code != status:
            failed.append(name)
            print("  FAIL    %s: refused %s, expected %s"
                  % (name, exc.status_code, status))
        elif contains and contains.lower() not in str(exc.detail).lower():
            failed.append(name)
            print("  FAIL    %s: wrong reason -- %r" % (name, exc.detail))
        else:
            passed.append(name)
            print("  REFUSED %s  (%s: %s)"
                  % (name, exc.status_code, str(exc.detail)[:64]))
        return
    except Exception as exc:
        _release(True)
        failed.append(name)
        print("  FAIL    %s: %s: %s" % (name, type(exc).__name__, exc))
        return
    _release(True)
    failed.append(name)
    print("  FAIL    %s: it was ALLOWED and should not have been" % name)


def assert_that(condition, message):
    if not condition:
        raise AssertionError(message)


with SessionFactory() as db:
    system_context(db, reason="write-path test")
    DB.append(db)

    staff = db.execute(text(
        "SELECT u.id FROM iam.platform_admins a JOIN iam.users u "
        "ON u.id = a.user_id WHERE a.status = 'active' LIMIT 2"
    )).scalars().all()
    admin_id = staff[0]
    other_id = staff[1] if len(staff) > 1 else None

    me = PlatformCaller(subject="writer@example.invalid", user_id=admin_id,
                        admin_id=uuid.uuid4(),
                        capabilities=frozenset({"recovery.approve"}),
                        roles=("test",))

    org = db.execute(text(
        "SELECT id, name FROM iam.organizations LIMIT 1")).mappings().first()
    prop = db.execute(text(
        "SELECT id, code FROM iam.properties WHERE organization_id = :o LIMIT 1"
    ), {"o": org["id"]}).mappings().first()

    # ------------------------------------------------- 16 providers -------
    conn = {}
    ok("provider: store a sealed secret", lambda: conn.update(
        ops.upsert_provider(
            ops.ProviderIn(provider="razorpay", environment="sandbox",
                           label="Test sandbox",
                           config={"key_id": "rzp_test_x",
                                   "webhook_path": "/hooks/rzp"},
                           secret="not-a-real-secret"),
            REQ, db, me)))

    def secret_is_sealed():
        stored = db.execute(
            text("SELECT secret FROM platform.provider_credentials "
                 "WHERE id = :i"), {"i": conn["id"]}).scalar()
        assert_that(stored, "no secret was stored")
        assert_that("not-a-real-secret" not in stored,
                    "the plaintext is in the column")
    ok("provider: the stored secret is ciphertext", secret_is_sealed)

    def verify_is_complete():
        r = ops.verify_provider(conn["id"], REQ, db, me)
        assert_that(r["status"] == "configured", r)
        assert_that("does not call the provider" in r["detail"], r["detail"])
    ok("provider: verify reports completeness, not a live call",
       verify_is_complete)

    def rotate_keeps_secret():
        before = db.execute(
            text("SELECT secret FROM platform.provider_credentials "
                 "WHERE id = :i"), {"i": conn["id"]}).scalar()
        ops.upsert_provider(
            ops.ProviderIn(provider="razorpay", environment="sandbox",
                           label="Renamed", config={"key_id": "rzp_test_x"}),
            REQ, db, me)
        after = db.execute(
            text("SELECT secret FROM platform.provider_credentials "
                 "WHERE id = :i"), {"i": conn["id"]}).scalar()
        assert_that(before == after, "an omitted secret wiped the stored one")
    ok("provider: saving without a secret keeps the stored one",
       rotate_keeps_secret)

    refuses("provider: an unknown provider",
            lambda: ops.upsert_provider(
                ops.ProviderIn(provider="not-a-provider",
                               environment="sandbox", label="x"),
                REQ, db, me),
            400, "unknown provider")
    refuses("provider: a blank secret",
            lambda: ops.upsert_provider(
                ops.ProviderIn(provider="smtp", environment="sandbox",
                               label="x", secret="   "),
                REQ, db, me),
            400, "blank")

    # --------------------------------------------------- 18 domains -------
    dom = {}

    def register_domain():
        dom.update(ops.add_domain(
            ops.DomainIn(property_id=prop["id"],
                         hostname="https://Book.Example.COM/rooms"),
            REQ, db, me))
        assert_that(dom["hostname"] == "book.example.com", dom["hostname"])
        assert_that(dom["status"] == "pending", dom["status"])
    ok("domain: a pasted URL is reduced to a hostname", register_domain)

    refuses("domain: the same hostname twice",
            lambda: ops.add_domain(
                ops.DomainIn(property_id=prop["id"],
                             hostname="book.example.com"), REQ, db, me),
            409, "already registered")
    refuses("domain: something that is not a hostname",
            lambda: ops.add_domain(
                ops.DomainIn(property_id=prop["id"], hostname="not a host"),
                REQ, db, me),
            400, "hostname")
    refuses("domain: go live before the DNS proof",
            lambda: ops.set_domain_state(
                dom["id"], ops.DomainStateIn(action="activate"), REQ, db, me),
            409, "verified")

    def verify_then_live():
        ops.set_domain_state(dom["id"], ops.DomainStateIn(action="verify"),
                             REQ, db, me)
        r = ops.set_domain_state(dom["id"],
                                 ops.DomainStateIn(action="activate"),
                                 REQ, db, me)
        assert_that(r["status"] == "live", r)
        assert_that(r["tls_expires_at"] is not None, "no certificate expiry")
    ok("domain: verify, then go live", verify_then_live)

    # ------------------------------------------------- 19 messaging -------
    tpl = db.execute(text("SELECT id FROM platform.message_templates "
                          "WHERE code = 'welcome' AND version = 1")).scalar()
    ok("template: edit the subject",
       lambda: ops.update_template(tpl, ops.TemplateIn(subject="Hello!"),
                                   REQ, db, me))
    ok("template: a variable it does receive",
       lambda: ops.update_template(
           tpl, ops.TemplateIn(body_text="Hello {{first_name}}"),
           REQ, db, me))
    refuses("template: a variable it never receives",
            lambda: ops.update_template(
                tpl, ops.TemplateIn(body_text="You are owed {{refund_amount}}"),
                REQ, db, me),
            400, "refund_amount")

    # ------------------------------------------------- 20/21 support ------
    tkt = {}

    def open_ticket():
        tkt.update(ops.open_ticket(
            ops.TicketIn(organization_id=org["id"],
                         subject="Rates are not pushing", priority="urgent",
                         opened_by="ops@example.invalid",
                         body="Since this morning."),
            REQ, db, me))
        due = db.execute(
            text("SELECT resolution_due, first_response_due "
                 "FROM platform.support_tickets WHERE id = :i"),
            {"i": tkt["id"]}).mappings().first()
        assert_that(due["resolution_due"], "no resolution target was set")
        assert_that(due["first_response_due"], "no response target was set")
    ok("ticket: opening one sets both SLA targets", open_ticket)

    ok("ticket: reply", lambda: ops.reply_to_ticket(
        tkt["id"], ops.ReplyIn(body="Looking at it now."), REQ, db, me))
    refuses("ticket: assign to somebody who is not platform staff",
            lambda: ops.update_ticket(
                tkt["id"], ops.TicketUpdateIn(assigned_to=uuid.uuid4()),
                REQ, db, me),
            400, "platform staff")

    grant = {}
    ok("access: request read-only scopes", lambda: grant.update(
        ops.request_access(tkt["id"], ops.AccessRequestIn(
            organization_id=org["id"],
            reason="The guest says the rate they were quoted is wrong.",
            scope=["reservations.read"], minutes=30), REQ, db, me)))
    refuses("access: a scope that would write",
            lambda: ops.request_access(tkt["id"], ops.AccessRequestIn(
                organization_id=org["id"],
                reason="Just fix it for them, it will be quicker.",
                scope=["folio.write"], minutes=30), REQ, db, me),
            400, "not a grantable scope")

    def schema_refuses_self_approval():
        """The control itself, at the layer that cannot be routed around."""
        db.execute(text("SAVEPOINT sp_constraint"))
        try:
            db.execute(
                text("UPDATE platform.support_access_grants "
                     "SET status = 'approved', approved_by = :u, "
                     "    approved_at = now(), "
                     "    expires_at = now() + interval '30 minutes' "
                     "WHERE id = :i"),
                {"u": admin_id, "i": grant["id"]})
        except Exception:
            db.execute(text("ROLLBACK TO SAVEPOINT sp_constraint"))
            return
        db.execute(text("ROLLBACK TO SAVEPOINT sp_constraint"))
        raise AssertionError("the database allowed a self-approval")
    ok("access: the schema refuses requester == approver",
       schema_refuses_self_approval)

    ok("access: withdraw a request",
       lambda: ops.revoke_access(grant["id"], REQ, db, me))

    def ticket_detail_reads():
        """Screen 21's only endpoint. Nothing else exercises this SQL, and it
        cannot be reached from a browser until a ticket exists."""
        d = ops.ticket_detail(tkt["id"], db, me)
        assert_that(d["ticket"]["reference"] == tkt["reference"], d["ticket"])
        assert_that(len(d["messages"]) == 2,
                    "expected the opening message and our reply")
        assert_that(len(d["grants"]) == 1, d["grants"])
        assert_that(d["grants"][0]["requested_by_name"], "no requester name")
        assert_that(all(s.endswith(".read") for s in d["scopes"]),
                    "a grantable scope is not read-only: %s" % (d["scopes"],))
    ok("ticket detail: conversation, grants and scopes", ticket_detail_reads)

    # ------------------------------------------------- 25 recovery --------
    snap = db.execute(text(
        "INSERT INTO platform.backup_snapshots (label, scope, taken_at) "
        "VALUES ('test snapshot', 'platform', now()) RETURNING id")).scalar()
    req = {}
    ok("restore: request one", lambda: req.update(ops.request_restore(
        ops.RestoreIn(snapshot_id=snap, scope="billing.invoices",
                      reason="A tenant's September invoice has vanished."),
        REQ, db, me)))
    refuses("restore: approve your own",
            lambda: ops.decide_restore(
                req["id"], ops.RestoreDecisionIn(decision="approve"),
                REQ, db, me),
            403, "cannot be approved by the person")

    if other_id is not None:
        them = PlatformCaller(subject="other@example.invalid",
                              user_id=other_id, admin_id=uuid.uuid4(),
                              capabilities=frozenset({"recovery.approve"}),
                              roles=("test",))
        ok("restore: a second person may approve", lambda: ops.decide_restore(
            req["id"], ops.RestoreDecisionIn(decision="approve"),
            REQ, db, them))
    else:
        print("  SKIP    restore: a second approver "
              "(this deployment has one platform admin)")

    # ------------------------------------------------- 26 settings --------
    ok("setting: change a default", lambda: ops.put_setting(
        "platform.default_currency", ops.SettingIn(value="INR"), REQ, db, me))
    refuses("setting: a key nobody declared",
            lambda: ops.put_setting("platform.made_up",
                                    ops.SettingIn(value="x"), REQ, db, me),
            404, "no such setting")
    ok("flag: turn one on platform-wide", lambda: ops.put_flag(
        "tenant_billing_portal", ops.FlagIn(enabled=True), REQ, db, me))
    ok("flag: override it for one tenant", lambda: ops.put_flag(
        "tenant_billing_portal",
        ops.FlagIn(enabled=False, organization_id=org["id"],
                   note="they asked us to keep control"), REQ, db, me))
    refuses("flag: one nobody declared",
            lambda: ops.put_flag("made_up", ops.FlagIn(enabled=True),
                                 REQ, db, me),
            404, "no such flag")

    # Nothing above is kept. The screens should show what this deployment
    # really has, which is currently very little, rather than test furniture
    # somebody later mistakes for a live domain or a real ticket.
    db.rollback()

print("\n%d passed, %d failed" % (len(passed), len(failed)))
if failed:
    print("failed: " + ", ".join(failed))
raise SystemExit(1 if failed else 0)
