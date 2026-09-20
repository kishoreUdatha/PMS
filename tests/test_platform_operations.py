"""The rules screens 16-27 rest on, checked against the source.

Parses rather than calls, like test_tenant_guards and test_platform_separation
do, so it needs no database and never skips. That matters here for the same
reason it matters there: these are the checks that would be quietly dropped
first, and a suite that skips when the stack is down protects nothing.

Three claims are made in comments throughout ``platform_ops_routes`` and on
the screens above them. A comment is not a control, so each is asserted here:

* a provider secret goes in and never comes back;
* a restore and a support-access grant are approved by somebody other than
  the person who asked, in the schema and not only in a handler;
* every table in the ``platform`` schema carries a row-level security policy.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
IAM = REPO / "services" / "iam" / "iam_service"
OPS_ROUTES = IAM / "platform_ops_routes.py"
MIGRATION = (REPO / "services" / "iam" / "alembic" / "versions"
             / "0032_platform_operations.py")


def _src(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


# =========================================== a secret never comes back =====

def test_no_route_can_decrypt_a_stored_credential():
    """``open_sealed`` has no business in this module.

    Sealing is one-way here by construction: the console shows a hint and a
    rotation date, and an operator who needs the real key gets it from the
    provider. Importing the opener would make "just show it to me" a
    two-line change, and the next person to make that change would be doing
    it at speed during an incident.
    """
    src = _src(OPS_ROUTES)
    assert "open_sealed" not in src, (
        "platform_ops_routes imports or calls open_sealed. A credential "
        "stored by this console must not be readable through it."
    )
    assert "seal" in src, "the module should still seal what it stores"


def test_no_query_returns_the_secret_column_itself():
    """Every mention of the column must be a test of it, not a projection.

    ``SELECT secret`` would hand ciphertext to a browser. That is not the
    plaintext, but it is the whole input to an offline attack on it, and
    there is no reason a screen needs it -- the listing deliberately returns
    ``right(secret, 4)`` and a boolean instead.
    """
    src = _src(OPS_ROUTES)
    offenders = []
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith(("#", "--", '"""', "*")):
            continue
        # Bare `secret` in a select list, as opposed to `secret IS NOT NULL`,
        # `right(secret, 4)` or the INSERT column list.
        if re.search(r"SELECT[^;]*\bsecret\b(?!\s*IS\b)", stripped, re.I):
            offenders.append(stripped[:100])
    assert not offenders, (
        "\nA query projects the secret column:\n  " + "\n  ".join(offenders)
        + "\nReturn right(secret, 4) and (secret IS NOT NULL) instead.\n"
    )


def test_the_secret_field_is_write_only_in_the_response_models():
    """No response type carries a secret back out.

    The request model has the field because a secret has to arrive somehow.
    Nothing that goes the other way may.
    """
    tree = ast.parse(_src(OPS_ROUTES))
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        fields = [t.target.id for t in node.body
                  if isinstance(t, ast.AnnAssign)
                  and isinstance(t.target, ast.Name)]
        if "secret" in fields:
            assert node.name == "ProviderIn", (
                f"{node.name} carries a `secret` field. Only the inbound "
                f"model may."
            )


# ====================================== nobody approves their own ==========

@pytest.mark.parametrize("constraint,table", [
    ("ck_grant_separate_approver", "support_access_grants"),
    ("ck_restore_separate_approver", "restore_requests"),
])
def test_the_schema_refuses_a_self_approval(constraint, table):
    """In the database, not only in the route.

    A handler's check produces a decent error message and is the right place
    for one. It is also one `if` away from being removed by somebody adding a
    convenience path, and separation of duties that a future route can skip
    is not separation of duties.
    """
    src = _src(MIGRATION)
    assert constraint in src, f"{constraint} is missing from the migration"
    pattern = re.compile(
        r"CONSTRAINT\s+" + constraint
        + r"\s*\n?\s*CHECK\s*\(approved_by IS NULL OR approved_by <> "
        r"(requested_by)\)", re.I)
    assert pattern.search(src), (
        f"{constraint} on {table} no longer compares approved_by with "
        f"requested_by."
    )


def test_an_approved_grant_must_carry_an_approver_and_an_expiry():
    """An approval with no expiry is a standing grant nobody agreed to."""
    src = _src(MIGRATION)
    assert "ck_grant_approved_has_approver" in src
    for required in ("approved_by IS NOT NULL", "approved_at IS NOT NULL",
                     "expires_at IS NOT NULL"):
        assert required in src, (
            f"an approved access grant no longer requires {required}")


def test_nothing_acts_on_an_approved_grant():
    """The deliberate gap, asserted so it stays deliberate.

    Support access is a record, not a session: no code reads a grant and
    widens anybody's visibility. If that changes it must be a designed
    decision with the tenant in the room, not a helper somebody added -- so
    this fails the moment a grant is read outside the two routes that write
    and list them.
    """
    readers = []
    for path in sorted((REPO / "services").rglob("*.py")):
        # The migration that creates the table names it, necessarily; and the
        # module that writes and lists grants is the one place allowed to.
        if ("__pycache__" in str(path) or path == OPS_ROUTES
                or "alembic" in path.parts):
            continue
        if "support_access_grants" in _src(path):
            readers.append(path.relative_to(REPO).as_posix())
    assert not readers, (
        "\nsupport_access_grants is read outside platform_ops_routes:\n  "
        + "\n  ".join(readers)
        + "\nApproving is not entering. If that is changing, change it "
          "deliberately and update this test with the reasoning.\n"
    )


# =========================================== every table is protected ======

def test_every_platform_table_has_a_policy():
    """The migration's own guard, restated where a reader will find it.

    ``0032`` raises if a table it created has no entry in ``POLICIES``, which
    catches this at deploy time. This catches it at review time, which is
    cheaper and is where somebody adding a table will be looking.
    """
    src = _src(MIGRATION)
    created = set(re.findall(
        r"CREATE TABLE IF NOT EXISTS platform\.(\w+)", src))
    policied = set(re.findall(r'^\s*"(\w+)":\s*\(', src, re.M))
    missing = sorted(created - policied)
    assert not missing, (
        f"\nplatform tables created with no RLS policy: {missing}\n"
        "Add them to POLICIES with a read and a write expression.\n"
    )
    assert created, "the migration creates no tables -- has it been renamed?"


def test_every_platform_table_forces_row_level_security():
    """FORCE, not merely ENABLE.

    Without FORCE, the table's owner bypasses every policy. The application
    role is not the owner today, and a future migration run as the owner
    would silently see everything.
    """
    src = _src(MIGRATION)
    assert "FORCE ROW LEVEL SECURITY" in src
    enable = src.count("ENABLE ROW LEVEL SECURITY")
    force = src.count("FORCE ROW LEVEL SECURITY")
    assert enable == force, (
        f"{enable} tables enable RLS but {force} force it")


def test_writes_are_reserved_to_system_context():
    """A tenant may read its own rows here and write none of them.

    Everything in this schema is operated by the platform. A write policy
    that admitted ``org_visible`` would let a tenant-scoped request insert
    its own support ticket, backup record or feature override -- each of
    which is a claim about what the platform did.
    """
    src = _src(MIGRATION)
    block = re.search(r"POLICIES:.*?\n\}", src, re.S)
    assert block, "POLICIES is no longer a literal this test can read"
    writes = re.findall(r'^\s*"\w+":\s*\([^,]+,\s*([^)]+)\),\s*$',
                        block.group(0), re.M)
    assert writes, "no write expressions found"
    for expr in writes:
        assert expr.strip() == "_SYSTEM", (
            f"a platform table accepts writes under {expr.strip()}; "
            f"every write here belongs to system context")
