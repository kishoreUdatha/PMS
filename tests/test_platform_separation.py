"""The platform tier and the tenant tier must not leak into each other.

A super admin is the one principal allowed to cross the boundary that the rest
of this system exists to hold. That is safe only while the two tiers stay
mechanically separate, and "stay separate" is not something a convention can
promise -- tests/test_tenant_guards.py exists because sixty-two handlers had
quietly forgotten a guard everybody agreed was required.

So the separation is asserted rather than assumed, and asserted by parsing the
source, so these tests cannot be skipped by a stack that is not running. They
are the reason platform_routes.py is exempted from the tenancy suite: the
exemption buys a stricter check, not a weaker one.

Two directions of leak, both fatal, neither obvious in review:

* a platform route that forgot ``require_platform`` would be reachable by any
  authenticated user in any tenant -- and since the tenancy guard is
  deliberately absent there, it would hand them every tenant's data;
* a tenant route that used ``require_platform`` would grant a platform
  operator permissions inside a customer's books that nobody granted them.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
SERVICES = REPO / "services"
IAM = SERVICES / "iam" / "iam_service"

PLATFORM_ROUTES = IAM / "platform_routes.py"
BILLING_ROUTES = IAM / "billing_routes.py"
OPS_ROUTES = IAM / "platform_ops_routes.py"
PLATFORM_AUTHZ = IAM / "platform_authz.py"

#: Modules allowed to mention require_capability at all.
#:
#: Adding a file here is not a way past this suite -- it is the way *into* it.
#: Every handler in a listed module is then audited by every test below, so a
#: new platform module joins the allowlist and the scrutiny in the same edit.
PLATFORM_FILES = {PLATFORM_ROUTES, BILLING_ROUTES, OPS_ROUTES}

#: Which router a handler hangs off decides which tier it belongs to.
PLATFORM_ROUTERS = {"platform_router", "platform_billing_router", "ops_router"}
TENANT_ROUTERS = {"tenant_billing_router"}

HTTP_VERBS = {"get", "post", "put", "patch", "delete"}
PLATFORM_DEP = "require_capability"
TENANT_DEPS = ("require_permission", "require_org_permission")
TENANCY_GUARD = "guard_request_tenancy"


def _src(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def _code(path: pathlib.Path) -> str:
    """The module's executable code, with docstrings and comments removed.

    The checks below look for names that must not appear -- the tenancy guard,
    anything touching guest or payment data. Run against raw text they match
    the prose explaining why those things are absent, and fail on a file that
    is correct precisely because it discusses what it does not do. Comments are
    dropped by parsing; docstrings have to be removed by hand.
    """
    tree = ast.parse(_src(path), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
            continue
        body = node.body
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def _route_handlers(path: pathlib.Path):
    """Every decorated route handler, with its METHOD + url and its router."""
    out = []
    tree = ast.parse(_src(path), filename=str(path))
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for d in fn.decorator_list:
            if (
                isinstance(d, ast.Call)
                and isinstance(d.func, ast.Attribute)
                and d.func.attr in HTTP_VERBS
                and d.args
                and isinstance(d.args[0], ast.Constant)
                and isinstance(d.func.value, ast.Name)
            ):
                out.append((f"{d.func.attr.upper()} {d.args[0].value}", fn,
                            d.func.value.id))
                break
    return out


_ALL = [h for p in sorted(PLATFORM_FILES) for h in _route_handlers(p)]
PLATFORM_HANDLERS = [(r, f) for r, f, router in _ALL
                     if router in PLATFORM_ROUTERS]
TENANT_HANDLERS = [(r, f) for r, f, router in _ALL
                   if router in TENANT_ROUTERS]


def _deps(fn) -> str:
    """The dependency expressions in a handler's signature, as text."""
    return " ".join(
        ast.unparse(d)
        for d in fn.args.defaults + [k for k in fn.args.kw_defaults if k])


# ------------------------------------------------- the platform tier holds --

def test_there_are_platform_routes_to_check():
    """Guard against the walk silently finding nothing.

    Every assertion below is a loop over the handlers. A renamed file or a
    changed decorator idiom would empty that list and turn this whole suite
    green while checking nothing at all.
    """
    assert len(PLATFORM_HANDLERS) >= 10, (
        f"only found {len(PLATFORM_HANDLERS)} handlers in "
        f"{PLATFORM_ROUTES.name}; the discovery is probably wrong"
    )


@pytest.mark.parametrize(
    "route,fn",
    PLATFORM_HANDLERS,
    ids=[r for r, _ in PLATFORM_HANDLERS],
)
def test_every_platform_route_is_gated_on_require_platform(route, fn):
    """No platform route is reachable without being platform staff.

    This is the load-bearing one. The tenancy guard does not run on these
    routes by design, so ``require_platform`` is the *only* thing standing
    between an ordinary tenant user and every organisation on the platform.
    One handler that forgets it is a total cross-tenant read.
    """
    defaults = " ".join(
        ast.unparse(d)
        for d in fn.args.defaults + [k for k in fn.args.kw_defaults if k]
    )
    assert PLATFORM_DEP in defaults, (
        f"{route} ({fn.name}) does not depend on {PLATFORM_DEP}. "
        "It is cross-tenant and ungated: any authenticated user in any "
        "tenant could read every organisation through it."
    )
    # And it names one, rather than accepting anyone holding platform access.
    # ast.unparse normalises to single quotes, so one form is enough.
    named = re.search(r"require_capability\('([a-z._]+)'\)", defaults)
    assert named, (
        f"{route} ({fn.name}) calls {PLATFORM_DEP} without naming a "
        "capability"
    )


@pytest.mark.parametrize(
    "route,fn",
    PLATFORM_HANDLERS,
    ids=[r for r, _ in PLATFORM_HANDLERS],
)
def test_platform_routes_do_not_use_tenant_dependencies(route, fn):
    """A platform route must not also ask for a tenant permission.

    Mixing them would mean the route resolves a *tenant* caller as well, and
    the tenancy guard inside those factories would then refuse the very
    cross-tenant access the route exists to provide -- or worse, pass, because
    the operator happened to hold a membership in the org being inspected.
    """
    defaults = " ".join(
        ast.unparse(d)
        for d in fn.args.defaults + [k for k in fn.args.kw_defaults if k]
    )
    used = [d for d in TENANT_DEPS if d in defaults]
    assert not used, (
        f"{route} ({fn.name}) uses {used}, mixing the tenant tier into a "
        "platform route"
    )


# ------------------------------------------- the tenant tier stays separate --

def test_no_tenant_route_uses_the_platform_dependency():
    """The other direction: platform access must not grant tenant powers.

    A tenant route gated on ``require_platform`` would let an operator act
    inside a customer's books -- post a charge, approve a refund -- with no
    role, no grant and no record that anyone chose to allow it.
    """
    offenders = []
    for path in sorted(SERVICES.rglob("*routes*.py")):
        if path in PLATFORM_FILES or "__pycache__" in str(path):
            continue
        if PLATFORM_DEP in _src(path):
            offenders.append(path.relative_to(REPO).as_posix())
    assert not offenders, (
        f"\n{PLATFORM_DEP} is used outside the platform modules:\n  "
        + "\n  ".join(offenders)
        + "\nPlatform access must not satisfy a tenant route.\n"
    )

    # And inside those modules, a tenant-router handler must not reach for it.
    # A file-level rule cannot make this distinction: billing keeps both tiers
    # in one module, because a tenant reads its own subscription through the
    # same tables the platform reads everyone's.
    mixed = [f"{r}  ({fn.name})" for r, fn in TENANT_HANDLERS
             if PLATFORM_DEP in _deps(fn)]
    assert not mixed, (
        f"\n{len(mixed)} tenant route(s) gated on {PLATFORM_DEP}:\n  "
        + "\n  ".join(mixed)
        + "\nA tenant route must be gated on a tenant permission, or a "
        "platform operator gains authority inside a customer's account.\n"
    )


def test_there_are_tenant_billing_routes_to_check():
    """The tenant half of billing exists and the walk actually found it."""
    assert len(TENANT_HANDLERS) >= 3, (
        f"only found {len(TENANT_HANDLERS)} tenant billing handlers; the "
        "router discovery is probably wrong"
    )


@pytest.mark.parametrize(
    "route,fn", TENANT_HANDLERS, ids=[r for r, _ in TENANT_HANDLERS])
def test_tenant_billing_routes_use_the_tenant_dependency(route, fn):
    """A tenant reading its own bill goes through the ordinary tenant gate.

    Which means guard_request_tenancy runs on it exactly as on every other
    tenant route. Billing is not an exception to tenancy just because the
    platform reads the same tables.
    """
    used = [d for d in TENANT_DEPS if d in _deps(fn)]
    assert used, (
        f"{route} ({fn.name}) depends on neither "
        f"{' nor '.join(TENANT_DEPS)}, so no tenancy check runs for it"
    )


def test_require_platform_consults_no_role_or_membership():
    """Nothing a tenant can grant may produce a platform caller.

    The whole reason platform admins live in their own table rather than as a
    new ``scope_type`` on role_assignments: the permission query matches an
    organisation-scoped grant for *any* property, so a platform row in that
    table would be one missed OR away from becoming a cross-tenant grant.
    This test pins the property that made the separate table worth it.
    """
    tree = ast.parse(_src(PLATFORM_AUTHZ), filename=str(PLATFORM_AUTHZ))
    fn = next(
        (f for f in ast.walk(tree)
         if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
         and f.name in (PLATFORM_DEP, "_load")),
        None,
    )
    assert fn is not None, f"{PLATFORM_DEP} not found in {PLATFORM_AUTHZ.name}"
    # The whole module: the lookup lives in a helper the factory calls.
    body = _code(PLATFORM_AUTHZ)

    # The platform role tables share a naming shape with the tenant ones, so
    # they are masked out before the tenant names are looked for -- reading
    # iam.platform_role_permissions is exactly what this gate should do.
    body_t = (body.replace("platform_role_assignments", "")
                  .replace("platform_role_permissions", "")
                  .replace("platform_permissions", "")
                  .replace("platform_roles", ""))
    forbidden = [
        t for t in ("role_assignments", "role_permissions", "iam.permissions",
                    "iam.memberships", "scope_type")
        if t in body_t
    ]
    assert not forbidden, (
        f"{PLATFORM_DEP} consults {forbidden}. Platform access must depend on "
        "iam.platform_admins alone, or a tenant grant could confer it."
    )
    assert "platform_admins" in body, (
        f"{PLATFORM_DEP} does not read iam.platform_admins"
    )
    assert "status = 'active'" in body, (
        f"{PLATFORM_DEP} does not require the row to be active; a revoked "
        "administrator would still be admitted"
    )


def test_the_tenancy_guard_is_absent_from_platform_routes():
    """Documented, not accidental.

    The guard's absence here is deliberate and is the reason this file is
    exempt from test_tenant_guards.py. Asserting it keeps the two suites
    honest about each other: if somebody adds the guard, these routes start
    refusing every request and this test says why before anyone debugs it.
    """
    assert TENANCY_GUARD not in _code(PLATFORM_ROUTES), (
        f"{TENANCY_GUARD} appears in platform_routes.py. Platform routes name "
        "another tenant on purpose; the guard would refuse all of them."
    )


def test_platform_routes_are_exempted_explicitly_by_the_tenancy_suite():
    """The exemption is stated in the other suite, not implied by spelling.

    Without this, removing the exemption line would silently re-include these
    routes and the tenancy suite would fail for reasons that look like a
    tenancy bug. With it, the two files have to agree.
    """
    guards = _src(REPO / "tests" / "test_tenant_guards.py")
    assert "PLATFORM_ROUTES" in guards, (
        "test_tenant_guards.py no longer names the platform exemption"
    )


# ------------------------------------------------- what is deliberately out --

FORBIDDEN_TABLES = (
    "engagement.guests",
    "finance.folio_entries",
    "finance.payments",
    "finance.refunds",
    "credential",
)


def test_no_platform_route_reads_guest_or_money_detail():
    """The boundary the console itself is not allowed to cross.

    A platform operator runs the platform; they do not read a customer's guest
    list or move money in their books. Counts of reservations and folios are
    fine -- that is capacity planning -- but the rows themselves are not, and
    neither is anything touching payment credentials.
    """
    src = _code(PLATFORM_ROUTES)
    found = [t for t in FORBIDDEN_TABLES if t in src]
    assert not found, (
        f"platform_routes.py references {found}. Platform administration must "
        "not read guest data, folio detail or payment credentials."
    )


def test_destructive_platform_actions_require_a_reason():
    """Suspension is not something anyone should be able to do silently.

    The tenant's own audit log is where they will look for why they were cut
    off, so the reason is a required field rather than an optional one.
    """
    tree = ast.parse(_src(PLATFORM_ROUTES), filename=str(PLATFORM_ROUTES))
    models = {
        c.name: c for c in ast.walk(tree) if isinstance(c, ast.ClassDef)
    }
    for name in ("OrgSuspend", "PropertyStatusIn", "UserStatusIn",
                 "AdminRevoke"):
        cls = models.get(name)
        assert cls is not None, f"{name} is gone; was it renamed?"
        reason = next(
            (n for n in cls.body
             if isinstance(n, ast.AnnAssign)
             and isinstance(n.target, ast.Name)
             and n.target.id == "reason"),
            None,
        )
        assert reason is not None, f"{name} has no reason field"
        annotation = ast.unparse(reason.annotation)
        assert "None" not in annotation, (
            f"{name}.reason is optional ({annotation}); a suspension with no "
            "stated reason leaves the tenant no answer in their audit log"
        )


def test_every_platform_write_is_audited():
    """A tier that can suspend a customer must leave a trail, per route.

    Checked per handler rather than per file: one unaudited write among twenty
    audited ones is exactly the gap nobody notices, and it would be the one
    action a tenant could not account for.
    """
    # "Non-GET" is the wrong question. A preview is a POST because it takes a
    # body, and it writes nothing -- demanding an audit row for it would make
    # the suite reward writing one, which is noise in the trail. And a handler
    # that delegates its write to a shared helper audits there, not inline.
    # So: does this handler write, and if it does, does it audit?
    WRITES = ("INSERT ", "UPDATE ", "DELETE ")

    def _helpers(path):
        """Module-level functions that write, and ones that audit."""
        tree = ast.parse(_src(path))
        writes, audits = set(), set()
        for fn in tree.body:
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            body = ast.unparse(fn)
            if any(w in body.upper() for w in WRITES):
                writes.add(fn.name)
            if "record_audit" in body:
                audits.add(fn.name)
        return writes, audits

    unaudited = []
    for path in sorted(PLATFORM_FILES):
        writers, auditors = _helpers(path)
        for route, fn, router in _route_handlers(path):
            if router not in PLATFORM_ROUTERS:
                continue
            body = ast.unparse(fn)
            touches = [n for n in writers if n != fn.name and n + "(" in body]
            does_write = (any(w in body.upper() for w in WRITES)
                          or bool(touches))
            if not does_write:
                continue
            audited = ("record_audit" in body
                       or any(n in auditors for n in touches))
            if not audited:
                unaudited.append(f"{route}  ({fn.name})")
    assert not unaudited, (
        f"\n{len(unaudited)} platform write(s) leave no audit trail:\n  "
        + "\n  ".join(unaudited)
        + "\n"
    )


# ---------------------------------------------------- suspension has teeth --

def test_suspension_is_enforced_where_the_caller_is_resolved():
    """A status column nothing reads is a promise, not a control.

    ``organizations.status`` existed before any of this and was checked
    nowhere: suspending a tenant would have changed a letter in a table and
    nothing else. Enforcement belongs with caller resolution so it applies to
    sessions already issued, in every service, rather than only at the next
    sign-in.
    """
    shared = _code(REPO / "libs" / "common" / "chirala_common" / "authz.py")
    iam_authz = _code(IAM / "authz.py")
    for name, src in (("chirala_common.authz", shared), ("iam.authz", iam_authz)):
        assert "org_suspended" in src, (
            f"{name} does not resolve organisation suspension, so a suspended "
            "tenant keeps working until its sessions expire"
        )
        assert "iam.organizations" in src, (
            f"{name} never joins iam.organizations, so it cannot know"
        )


# --------------------------------------- the tiers are separate identities --

def test_a_tenant_user_cannot_be_granted_platform_access():
    """One person, one tier. Enforced, not advised.

    A platform administrator who is also a member of an organisation is two
    principals sharing an identity: they see every tenant through the platform
    routes and their own through the tenant routes, and no audit row can say
    which they were acting as. It also creates a "their own" organisation for
    a console whose whole discipline is naming its target explicitly.
    """
    src = _code(PLATFORM_ROUTES)
    tree = ast.parse(src)
    fn = next(
        (f for f in ast.walk(tree)
         if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
         and f.name == "grant_admin"),
        None,
    )
    assert fn is not None, "grant_admin is gone; was it renamed?"
    body = ast.unparse(fn)
    assert "iam.memberships" in body, (
        "grant_admin does not check memberships, so a tenant user could be "
        "promoted to platform staff"
    )
    assert "409" in body, "grant_admin does not refuse the case it detects"


def test_the_platform_caller_carries_no_tenant():
    """There is no 'own' organisation at this tier, so there is no field.

    A PlatformCaller with an organization_id invites code to fall back on it
    the way every tenant route legitimately does -- and a platform route that
    scopes itself implicitly is a platform route that silently acts on the
    wrong customer.
    """
    tree = ast.parse(_src(PLATFORM_AUTHZ), filename=str(PLATFORM_AUTHZ))
    cls = next(
        (c for c in ast.walk(tree)
         if isinstance(c, ast.ClassDef) and c.name == "PlatformCaller"),
        None,
    )
    assert cls is not None, "PlatformCaller is gone; was it renamed?"
    fields = {
        n.target.id for n in cls.body
        if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)
    }
    assert "organization_id" not in fields, (
        "PlatformCaller carries an organization_id; a platform operator "
        "belongs to no tenant and must have no default one"
    )


# ------------------------------------------------------ the platform login --

AUTH_ROUTES = IAM / "auth_routes.py"


def test_platform_sign_in_is_its_own_route():
    """Tenant login cannot serve these accounts, and must not try.

    ``_credential_login`` resolves a user through a property code and a
    membership in that property's organisation. A platform operator has
    neither, so without a separate route a platform account can be created and
    granted and still never sign in -- which is exactly the state this was in
    when the tiers were first split.
    """
    tree = ast.parse(_src(AUTH_ROUTES), filename=str(AUTH_ROUTES))
    fn = next(
        (f for f in ast.walk(tree)
         if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
         and f.name == "platform_login"),
        None,
    )
    assert fn is not None, "there is no platform_login route"
    body = ast.unparse(fn)

    assert "platform_admins" in body, (
        "platform_login does not check iam.platform_admins, so any account "
        "with a password could sign in through the platform route"
    )
    assert "property_code" not in body, (
        "platform_login asks for a property code; a platform account belongs "
        "to no property and has none to give"
    )
    # The password must be checked, and a failure counted, before the platform
    # table is consulted -- otherwise the route answers "is this address
    # platform staff" for free.
    assert "_record_failure" in body, (
        "platform_login does not count a failed attempt, so it is exempt from "
        "the lockout every other sign-in is subject to"
    )
    # The column is selected up front; what matters is where the *guard* sits.
    # Comparing against the SELECT would compare against the wrong thing and
    # pass whatever the order of the checks.
    guard = "if not user['is_platform_admin']"
    assert guard in body, (
        "platform_login never refuses a non-platform account; selecting the "
        "flag is not the same as acting on it"
    )
    assert body.index("verify_password") < body.index(guard), (
        "platform_login checks platform staff before the password, turning it "
        "into an oracle for which addresses hold platform access"
    )


def test_platform_login_body_takes_only_an_email_and_password():
    tree = ast.parse(_src(AUTH_ROUTES), filename=str(AUTH_ROUTES))
    cls = next(
        (c for c in ast.walk(tree)
         if isinstance(c, ast.ClassDef) and c.name == "PlatformLoginIn"),
        None,
    )
    assert cls is not None, "PlatformLoginIn is gone; was it renamed?"
    fields = {
        n.target.id for n in cls.body
        if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)
    }
    assert fields == {"email", "password"}, (
        f"PlatformLoginIn takes {sorted(fields)}; it should take exactly an "
        "email and a password"
    )


# ------------------------------------------------- row-level security tier --

def test_platform_access_is_verified_before_the_transaction_is_elevated():
    """Establish that the caller is platform staff, then widen what they see.

    Elevating first is the tempting shape, because get_caller has already
    cleared the identity binding and a bound transaction can see neither every
    tenant nor its own platform row. But system context before the check means
    every caller who merely *attempts* a platform route spends the next
    statement able to read every tenant, with only the 403 holding the window
    shut -- safe exactly until somebody adds a line inside it.

    The RLS policy on iam.platform_admins is written to make that unnecessary:
    `system_context() OR user_id = identity_user_id()` admits a caller to
    their own row under identity context alone. This test pins the ordering
    that policy exists to permit.
    """
    src = _code(PLATFORM_AUTHZ)
    fn = next(
        (f for f in ast.walk(ast.parse(src))
         if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
         and f.name == PLATFORM_DEP),
        None,
    )
    assert fn is not None, f"{PLATFORM_DEP} is gone; was it renamed?"
    body = ast.unparse(fn)

    for call in ("identity_context", "system_context", "_load"):
        assert call in body, f"{PLATFORM_DEP} no longer calls {call}"

    assert body.index("identity_context") < body.index("_load"), (
        "the platform lookup runs before identity context is set, so the "
        "policy's own-row branch cannot match it"
    )
    assert body.index("_load") < body.index("system_context"), (
        "system_context is set before the platform-staff check: an "
        "unauthorised caller's transaction is elevated to see every tenant, "
        "and only the 403 that follows keeps that window shut"
    )


def test_system_context_is_always_given_a_reason():
    """Every widening of the tenant boundary says why, in the log.

    chirala_common.db.system_context refuses an empty reason, so this is about
    the reasons being *distinct and meaningful* rather than present: a file
    where every call passes the same string tells a reader nothing about which
    job widened the boundary.
    """
    import re
    for path in (PLATFORM_AUTHZ, PLATFORM_ROUTES):
        for call in re.findall(r"system_context\([^)]*\)", _code(path)):
            if call.startswith("system_context(db"):
                assert "reason=" in call, (
                    f"{path.name}: system_context called without a reason: {call}"
                )
