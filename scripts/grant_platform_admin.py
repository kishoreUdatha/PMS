"""Grant or revoke platform administrator, out of band.

The first platform administrator cannot be created through the API, because
every route that grants one is itself gated on being one. Something has to
break that circle, and the honest place is here: whoever runs this already has
the database credentials, which is a strictly higher privilege than the row it
writes. A bootstrap endpoint protected by a shared secret would be a second,
weaker way in that lives forever.

There is deliberately no seed in the migration either. A migration runs in
every environment, and none of them knows who its operators are; one that
seeded an address would put the same super admin in every deployment.

A platform account belongs to no tenant. That is enforced rather than
advised: the in-app grant refuses any user holding an active membership, so a
platform operator cannot be an existing tenant login that has been promoted.
It follows that nothing in the application can create one -- every account
route makes a member of some organisation -- which is why --create lives here.

Usage, from the repository root:

    python scripts/grant_platform_admin.py --create \
        --email ops@example.com --name "Platform Operations"
    python scripts/grant_platform_admin.py --email ops@example.com
    python scripts/grant_platform_admin.py --email ops@example.com --revoke
    python scripts/grant_platform_admin.py --list

Runs against DATABASE_URL, or IAM_DATABASE_URL, or the compose default.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import secrets
import sys
import uuid

from sqlalchemy import create_engine, text

DEFAULT_URL = (
    "postgresql+psycopg://pms:pms_dev_password@localhost:5432/chirala_pms"
)


def _url() -> str:
    return (
        os.getenv("IAM_DATABASE_URL")
        or os.getenv("DATABASE_URL")
        or DEFAULT_URL
    )


def _list(conn) -> int:
    rows = conn.execute(
        text(
            """
            SELECT u.display_name, u.email, pa.status, pa.granted_at,
                   pa.note
            FROM iam.platform_admins pa
            JOIN iam.users u ON u.id = pa.user_id
            ORDER BY pa.status, u.display_name
            """
        )
    ).mappings().all()
    if not rows:
        print("No platform administrators. The console is unreachable until "
              "one is granted.")
        return 0
    print(f"{'Name':<28} {'Email':<34} {'Status':<9} Granted")
    for r in rows:
        print(f"{(r['display_name'] or ''):<28} {(r['email'] or ''):<34} "
              f"{r['status']:<9} {r['granted_at']:%Y-%m-%d %H:%M}")
    return 0


def _create(conn, email: str, args) -> int:
    """Provision a platform account: a user with no membership, and a link.

    No password is set here. Nobody -- including whoever runs this script --
    should ever know a platform operator's credential, so what comes out is a
    single-use link the operator uses to set their own. It is printed rather
    than emailed because bootstrapping happens on a terminal, and because an
    email at this point would depend on SMTP being configured before the
    platform has an administrator to configure it.
    """
    if not args.name:
        print("--create needs --name for the display name.", file=sys.stderr)
        return 1
    if conn.execute(text("SELECT 1 FROM iam.users WHERE lower(email) = :em"),
                    {"em": email}).first():
        print(f"A user already exists for {email}. Use it without --create, "
              f"or pick another address.", file=sys.stderr)
        return 1

    user_id = uuid.uuid4()
    # Recognisable in an audit log, and marked as platform so nobody mistakes
    # one of these for a tenant account when reading a trail.
    subject = f"platform-{email.split('@')[0]}-{uuid.uuid4().hex[:6]}"
    conn.execute(
        text(
            """
            INSERT INTO iam.users
                (id, identity_provider, subject_id, display_name, status, email)
            VALUES (:id, 'local', :sub, :name, 'active', :em)
            """
        ),
        {"id": user_id, "sub": subject, "name": args.name.strip(), "em": email},
    )
    # Deliberately no membership, no organisation, no property, no role.
    conn.execute(
        text("INSERT INTO iam.platform_admins (user_id, status, note) "
             "VALUES (:u, 'active', :note)"),
        {"u": user_id, "note": args.note},
    )

    token = secrets.token_urlsafe(32)
    conn.execute(
        text(
            """
            INSERT INTO iam.password_tokens
                (id, user_id, token_hash, purpose, expires_at)
            VALUES (gen_random_uuid(), :u, :h, 'welcome',
                    now() + interval '72 hours')
            """
        ),
        {"u": user_id, "h": hashlib.sha256(token.encode()).hexdigest()},
    )
    conn.execute(
        text(
            """
            INSERT INTO iam.audit_events
                (id, actor_subject, action, entity_type, entity_id, reason,
                 occurred_at)
            VALUES (gen_random_uuid(), 'cli', 'platform.admin.granted',
                    'platform_admin', :eid, :note, now())
            """
        ),
        {"eid": str(user_id), "note": args.note},
    )

    print(f"Platform account created for {args.name.strip()} <{email}>.")
    print(f"  subject : {subject}")
    print("  tenant  : none (this account belongs to no organisation)")
    print()
    print("Set the password with this single-use link, valid 72 hours:")
    print()
    print(f"  {args.base_url.rstrip('/')}/set-password?token={token}")
    print()
    print("Then sign in at /auth/platform-login with the email and password "
          "-- no property code.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--email", help="the user to elevate or demote")
    ap.add_argument("--revoke", action="store_true",
                    help="take platform access away instead of granting it")
    ap.add_argument("--note", default="granted out of band",
                    help="why, recorded on the row")
    ap.add_argument("--list", action="store_true", dest="do_list")
    ap.add_argument("--create", action="store_true",
                    help="provision a new platform account (no tenant) and "
                         "grant it, printing a set-password link")
    ap.add_argument("--name", help="display name, with --create")
    ap.add_argument("--base-url", default=os.getenv("APP_BASE_URL",
                                                    "http://localhost:5173"),
                    help="where the set-password link should point")
    args = ap.parse_args()

    engine = create_engine(_url())
    with engine.begin() as conn:
        if args.do_list:
            return _list(conn)
        if not args.email:
            ap.error("--email is required unless --list")

        email = args.email.strip().lower()
        if args.create:
            return _create(conn, email, args)

        user = conn.execute(
            text("SELECT id, display_name, status FROM iam.users "
                 "WHERE lower(email) = :em"),
            {"em": email},
        ).mappings().first()
        if user is None:
            print(f"No user with email {email}. The account has to exist "
                  f"first -- sign up or be invited, then run this.",
                  file=sys.stderr)
            return 1
        if user["status"] != "active" and not args.revoke:
            print(f"{email} is '{user['status']}', not active. Activate the "
                  f"account before elevating it.", file=sys.stderr)
            return 1

        member_of = conn.execute(
            text("SELECT o.name FROM iam.memberships m "
                 "JOIN iam.organizations o ON o.id = m.organization_id "
                 "WHERE m.user_id = :u AND m.status = 'active'"),
            {"u": user["id"]},
        ).scalars().all()
        if member_of and not args.revoke:
            # The same refusal the API gives, for the same reason. A script
            # that could do what the route forbids would make the rule
            # advisory, and this script is the more privileged of the two.
            print(f"{email} belongs to {', '.join(member_of)}. A tenant user "
                  f"cannot hold platform access -- create a separate platform "
                  f"account with --create.", file=sys.stderr)
            return 1

        if args.revoke:
            n = conn.execute(
                text("UPDATE iam.platform_admins "
                     "SET status = 'revoked', revoked_at = now(), "
                     "    updated_at = now() "
                     "WHERE user_id = :u AND status = 'active'"),
                {"u": user["id"]},
            ).rowcount
            print(f"Revoked platform access for {email}." if n
                  else f"{email} was not platform staff.")
        else:
            conn.execute(
                text(
                    """
                    INSERT INTO iam.platform_admins
                        (user_id, status, note)
                    VALUES (:u, 'active', :note)
                    ON CONFLICT (user_id) DO UPDATE
                       SET status = 'active', revoked_at = NULL,
                           granted_at = now(), note = EXCLUDED.note,
                           updated_at = now()
                    """
                ),
                {"u": user["id"], "note": args.note},
            )
            print(f"{user['display_name']} <{email}> is now a platform "
                  f"administrator.")

        # Written by hand rather than through record_audit: this runs outside
        # the application, so there is no request, no correlation id and no
        # authenticated actor -- 'cli' is the truthful actor, and a grant made
        # this way must still appear in the trail alongside the in-app ones.
        conn.execute(
            text(
                """
                INSERT INTO iam.audit_events
                    (id, actor_subject, action, entity_type, entity_id,
                     reason, occurred_at)
                VALUES (gen_random_uuid(), 'cli', :action, 'platform_admin',
                        :eid, :note, now())
                """
            ),
            {"action": "platform.admin.revoked" if args.revoke
                       else "platform.admin.granted",
             "eid": str(user["id"]), "note": args.note},
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
