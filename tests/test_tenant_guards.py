"""The tenancy guards cannot be omitted, and this is what holds that true.

The guards now run inside the permission dependencies, so a route is guarded
by existing: ``guard_request_tenancy`` reads the tenant and entity ids off the
request and refuses before the handler body starts. A new route written with
no check at all is still refused -- that is the property these tests defend.

Three things have to stay true for that to keep working, and each has a test:

1. Both permission dependencies, in both authz modules, call the guard. If one
   stops, every route depending on it silently loses its tenancy check while
   still looking authorised.
2. Every route that takes a tenant goes through one of those dependencies.
   A route resolving its caller some other way gets no guard.
3. The guard still refuses. A guard softened to a warning satisfies every
   structural check ever written.

The body case is the exception and is tested separately: a tenant inside a
parsed Pydantic body is not visible to a dependency that reads the path and
query string, so those handlers check their own and test 4 keeps them honest.

This is deliberately a *static* test. The integration tests in this directory
skip themselves when their database URLs are unset, which is exactly the
condition under which a security regression would sail through CI unnoticed.
This one parses the source and needs nothing: no database, no containers, no
environment. It cannot skip.

----

Original note, still true of the per-handler checks further down:

Every route that accepts a tenant from the caller must check it.

This is the test for a bug that reached the codebase in three services and was
invisible in the browser every time -- because the UI derives
``organization_id`` and ``property_id`` from whatever the user already has
open, so the honest client never asks for anyone else's data. Editing one uuid
in the address bar was enough to read another hotel's guest list, with names,
email addresses and phone numbers.

The rule the routes have to follow is one sentence: a permission answers "may
this caller do this?", never "whose data is this?". ``require_permission``
confirms the caller holds a role granting the action -- and in iam an
organisation-scoped assignment grants it for *any* property, since the grant is
not tied to one. Nothing in that check looks at who owns the row being read, so
each handler that takes a tenant id from the caller has to compare it itself:

    property_id      -> assert_property_in_org(db, caller, property_id)
    organization_id  -> assert_org_matches_caller(caller, organization_id)

This is deliberately a *static* test. The integration tests in this directory
skip themselves when their database URLs are unset, which is exactly the
condition under which a security regression would sail through CI unnoticed.
This one parses the source and needs nothing: no database, no containers, no
environment. It cannot skip.

It is also deliberately allowlist-free. Every handler in the tree passes today,
so there is no roster of known exceptions for a new one to be quietly added to
-- the only way to make this test green is to write the check.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
SERVICES = REPO / "services"

HTTP_VERBS = {"get", "post", "put", "patch", "delete"}

#: The tenant a handler may accept, and the call that has to vet it.
GUARDS = {
    "property_id": "assert_property_in_org",
    "organization_id": "assert_org_matches_caller",
}

#: Path parameters that name a single tenant-owned row. A handler addressed by
#: one of these and by no tenant at all is the case the two checks above cannot
#: see: there is nothing to compare, so the row came back to whoever asked.
#: Its lookup has to be scoped instead -- see assert_entity_in_org.
ENTITY_ARGS = {"reservation_id", "guest_id", "unit_id", "folio_id"}

ENTITY_GUARD = "assert_entity_in_org"

#: Ways a handler can scope its own lookup instead of calling the guard, all of
#: them meaning "the id and the tenant are checked together, so a foreign row
#: is indistinguishable from a missing one".
SELF_SCOPING = (
    "caller.organization_id",
    "_or_404",
    "tenant_session",
    # Resolving the row's owning property and handing it to the property guard
    # is the older idiom and an equally good answer -- confirm_endpoint does
    # exactly this, deliberately, because it is the route the payment webhook
    # calls.
    "assert_property_in_org",
)


class Handler:
    """One FastAPI route handler, with the bits this test cares about."""

    def __init__(self, path: pathlib.Path, fn, route: str):
        self.path = path
        self.fn = fn
        self.route = route
        self.name = fn.name
        self.args = [a.arg for a in fn.args.args + fn.args.kwonlyargs]

    @property
    def where(self) -> str:
        rel = self.path.relative_to(REPO).as_posix()
        return f"{rel}:{self.fn.lineno} {self.route} -> {self.name}()"

    def _call_lines(self, func: str) -> list[int]:
        return [
            n.lineno
            for n in ast.walk(self.fn)
            if isinstance(n, ast.Call)
            and (
                (isinstance(n.func, ast.Name) and n.func.id == func)
                or (isinstance(n.func, ast.Attribute) and n.func.attr == func)
            )
        ]

    def calls(self, func: str) -> bool:
        return bool(self._call_lines(func))

    def line_of_call(self, func: str) -> int | None:
        lines = self._call_lines(func)
        return min(lines) if lines else None

    def first_query_line(self) -> int | None:
        """Line of the first database read in the body."""
        lines = [
            n.lineno
            for n in ast.walk(self.fn)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr in ("execute", "scalar", "scalars")
        ]
        return min(lines) if lines else None


def _route_of(fn) -> str | None:
    """The HTTP route this function is decorated with, if any."""
    for d in fn.decorator_list:
        if (
            isinstance(d, ast.Call)
            and isinstance(d.func, ast.Attribute)
            and d.func.attr in HTTP_VERBS
            and d.args
            and isinstance(d.args[0], ast.Constant)
        ):
            return f"{d.func.attr.upper()} {d.args[0].value}"
    return None


#: The two platform routers, cross-tenant on purpose, and the only exemption
#: in this file. Platform administration exists above the tenant boundary: its
#: routes name somebody else's organisation deliberately, so running the
#: tenancy guard on them would refuse every one.
#:
#: The exemption is written down here rather than falling out of how the
#: handlers happen to be spelled, because an accidental exemption is
#: indistinguishable from a missing check. What replaces it is stricter, not
#: looser: tests/test_platform_separation.py asserts that every route in these
#: files is gated on require_platform, that require_platform consults no role
#: or membership, and that no route anywhere else uses it -- and that suite
#: reads exactly this set, so a module cannot be exempt here without being
#: audited there.
PLATFORM_ROUTES = {
    SERVICES / "iam" / "iam_service" / "platform_routes.py",
    SERVICES / "iam" / "iam_service" / "platform_ops_routes.py",
}
#: billing_routes.py is NOT exempt: its tenant half must obey the tenancy
#: rules like any other tenant route, and its platform half takes no
#: tenant parameter this suite recognises.


def _handlers() -> list[Handler]:
    found: list[Handler] = []
    for path in sorted(SERVICES.rglob("*routes*.py")):
        if path in PLATFORM_ROUTES:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            route = _route_of(fn)
            if route:
                found.append(Handler(path, fn, route))
    return found


HANDLERS = _handlers()


#: The two authz modules and the dependency factories each must guard.
AUTHZ_MODULES = [
    REPO / "libs" / "common" / "chirala_common" / "authz.py",
    REPO / "services" / "iam" / "iam_service" / "authz.py",
]
GUARD_CALL = "guard_request_tenancy"
DEP_FACTORIES = ("require_permission", "require_org_permission")


def test_permission_dependencies_call_the_guard():
    """The chokepoint holds: asking for a permission also vets the tenant.

    This is the test that makes the others redundant rather than essential.
    Every authenticated route depends on one of these two factories, so as
    long as both call the guard, no route can be written without one.
    """
    missing = []
    for path in AUTHZ_MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        found = {}
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if fn.name not in DEP_FACTORIES:
                continue
            found[fn.name] = GUARD_CALL in ast.unparse(fn)
        rel = path.relative_to(REPO).as_posix()
        for factory in DEP_FACTORIES:
            if factory not in found:
                missing.append(f"{rel}: {factory}() not found")
            elif not found[factory]:
                missing.append(f"{rel}: {factory}() does not call {GUARD_CALL}()")

    assert not missing, (
        "\nThe tenancy guard is no longer part of asking for a permission.\n"
        "Every route depending on these factories has silently lost its "
        "tenancy check while still looking authorised.\n\n  "
        + "\n  ".join(missing)
        + "\n"
    )


def test_every_tenant_route_goes_through_a_permission_dependency():
    """A route that resolves its caller some other way gets no guard."""
    offenders = []
    for h in HANDLERS:
        url = h.route.split(" ", 1)[1]
        in_path = {p.strip("{}") for p in url.split("/") if p.startswith("{")}
        relevant = (set(h.args) & set(GUARDS)) | ((set(h.args) | in_path) & ENTITY_ARGS)
        if not relevant:
            continue
        defaults = " ".join(
            ast.unparse(d) for d in h.fn.args.defaults
            + [k for k in h.fn.args.kw_defaults if k is not None]
        )
        if any(f in defaults for f in DEP_FACTORIES):
            continue
        if "caller" not in h.args:
            continue  # public/webhook route, authorised some other way
        offenders.append(f"{h.where}  {sorted(relevant)}")

    assert not offenders, (
        f"\n{len(offenders)} handler(s) take a tenant but do not depend on "
        f"{' or '.join(DEP_FACTORIES)}, so guard_request_tenancy never runs "
        "for them.\n\n  "
        + "\n  ".join(offenders)
        + "\n"
    )


def test_there_are_handlers_to_check():
    """Guard against the walk silently finding nothing.

    Every other test here passes vacuously on an empty list, so a rename of
    the routes files or a move of the services directory would turn this whole
    module into a no-op that still reports green.
    """
    assert len(HANDLERS) > 100, (
        f"only found {len(HANDLERS)} route handlers under {SERVICES}; "
        "the discovery glob is probably wrong"
    )


@pytest.mark.parametrize("tenant_arg,guard", sorted(GUARDS.items()))
def test_tenant_parameters_are_checked_against_the_caller(tenant_arg, guard):
    """A handler taking a tenant id from the caller must vet it."""
    offenders = [
        h.where for h in HANDLERS if tenant_arg in h.args and not h.calls(guard)
    ]
    assert not offenders, (
        f"\n{len(offenders)} handler(s) accept `{tenant_arg}` from the caller "
        f"but never call {guard}().\n"
        "A permission check is not a tenancy check: it confirms the caller may "
        "perform the action, not that the data belongs to them.\n"
        f"Add `{guard}(...)` before the first query.\n\n  "
        + "\n  ".join(offenders)
        + "\n"
    )


@pytest.mark.parametrize("tenant_arg,guard", sorted(GUARDS.items()))
def test_the_check_happens_before_the_query(tenant_arg, guard):
    """The guard is worthless if it runs after the read it should prevent."""
    offenders = []
    for h in HANDLERS:
        if tenant_arg not in h.args or not h.calls(guard):
            continue
        guard_at = h.line_of_call(guard)
        query_at = h.first_query_line()
        if query_at is not None and guard_at is not None and guard_at > query_at:
            offenders.append(
                f"{h.where} (guard line {guard_at}, query line {query_at})"
            )
    assert not offenders, (
        f"\n{len(offenders)} handler(s) call {guard}() only after already "
        "querying. The refusal has to come first.\n\n  "
        + "\n  ".join(offenders)
        + "\n"
    )


def test_routes_addressed_by_bare_id_scope_their_lookup():
    """A route identified only by a uuid must still check whose row it is.

    The two checks above only apply to handlers that accept a tenant. A
    handler taking nothing but ``/folios/{folio_id}`` accepts none, so they
    both pass it in silence -- and it returned another tenant's folio to
    anyone holding the id. A uuid is not a secret: it travels in URLs,
    exports, confirmation emails and webhook payloads.
    """
    offenders = []
    for h in HANDLERS:
        if set(h.args) & set(GUARDS):
            continue  # names a tenant; covered above
        if not set(h.args) & ENTITY_ARGS:
            continue
        if "caller" not in h.args:
            continue  # public or webhook route; a different question
        body = ast.unparse(h.fn)
        if h.calls(ENTITY_GUARD) or any(s in body for s in SELF_SCOPING):
            continue
        offenders.append(h.where)

    assert not offenders, (
        f"\n{len(offenders)} handler(s) are addressed by a bare entity id and "
        "never establish whose row it is.\n"
        f"Call {ENTITY_GUARD}(db, caller, table=..., entity_id=...), or scope "
        "the lookup by the caller's tenant in the same query as the id.\n\n  "
        + "\n  ".join(offenders)
        + "\n"
    )


def _body_models() -> dict[str, set[str]]:
    """Pydantic models carrying a tenant field, by class name.

    Scans every module, not just ``schemas.py``: several routers define their
    own body models inline, and two of those -- AccountIn and AttributeIn --
    were writing rows into whichever tenant the caller named. A scan limited
    to schemas.py did not see them.
    """
    tenant_fields = {"property_id", "organization_id"}
    models: dict[str, set[str]] = {}
    for path in SERVICES.rglob("*.py"):
        if "__pycache__" in str(path):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        for cls in ast.walk(tree):
            if not isinstance(cls, ast.ClassDef):
                continue
            bases = {ast.unparse(b).split(".")[-1] for b in cls.bases}
            if "BaseModel" not in bases:
                continue
            fields = {
                n.target.id for n in cls.body
                if isinstance(n, ast.AnnAssign)
                and isinstance(n.target, ast.Name)
                and n.target.id in tenant_fields
            }
            if fields:
                models[cls.name] = fields
    return models


def _carried_by(handler, models) -> set[str]:
    """Tenant fields this handler accepts through a request body."""
    carried: set[str] = set()
    for a in handler.fn.args.args + handler.fn.args.kwonlyargs:
        if a.annotation is None:
            continue
        ann = ast.unparse(a.annotation).split(".")[-1].strip("\"'")
        carried |= models.get(ann, set())
    return carried


def test_no_request_body_accepts_an_organisation():
    """An organisation is never something a caller may state.

    This is the structural half of the body problem, and the reason the field
    is gone rather than guarded. ``organization_id`` is always derivable --
    it is the caller's own -- so a body that accepts one exists only to be
    trusted wrongly: every one of these handlers inserted the value verbatim,
    putting the row in whichever tenant the request asked for.

    Removing the field is backward compatible, which is why it was the right
    fix rather than a risky one: no model sets ``extra="forbid"``, so a client
    still sending organization_id has it ignored. Handlers take it from
    ``caller_org(caller)`` instead, where there is nothing to get wrong.
    """
    models = _body_models()
    offenders = []
    for h in HANDLERS:
        if "organization_id" in _carried_by(h, models):
            offenders.append(h.where)

    assert not offenders, (
        f"\n{len(offenders)} handler(s) accept `organization_id` in a request "
        "body.\nAn organisation is the caller's own, never theirs to state: "
        "drop the field from the model and use caller_org(caller).\n\n  "
        + "\n  ".join(offenders)
        + "\n"
    )


def test_property_ids_in_a_request_body_are_checked_by_hand():
    """The one case the chokepoint genuinely cannot cover.

    Unlike an organisation, a property is a real choice -- an organisation has
    several -- so it cannot be derived from the caller and has to stay in the
    body. ``guard_request_tenancy`` reads the path and the query string; a
    value inside a parsed Pydantic body is invisible to it, and reading the
    body there would mean an async dependency doing synchronous database work
    on the event loop.

    So these handlers check their own, and this keeps them honest. The check
    is ``require_property_permission``, not ``assert_property_in_org``: the
    dependency's grant query ran with no property and so matched a role on
    any hotel in the organisation, and "same organisation" is all the older
    assert establishes.
    """
    models = _body_models()
    offenders = []
    for h in HANDLERS:
        carried = _carried_by(h, models)
        # Also present as a path or query argument means the dependency
        # already vetted it; only body-only values are this test's business.
        if "property_id" not in carried or "property_id" in h.args:
            continue
        if "require_property_permission" not in ast.unparse(h.fn):
            offenders.append(h.where)

    assert not offenders, (
        f"\n{len(offenders)} handler(s) take `property_id` only in the request "
        "body, which guard_request_tenancy cannot see, and never check it.\n"
        "Call require_property_permission(db, caller, body.property_id, "
        "<resource>, <action>).\n\n  "
        + "\n  ".join(offenders)
        + "\n"
    )


GATEWAY = REPO / "gateway" / "gateway_app"


def test_orchestration_names_a_tenant_on_every_downstream_call():
    """A service credential is only a tenant context with an organisation.

    The gateway fans one request out into several. On the service path each of
    those carries the platform's own credential, and a credential that names
    no organisation is a caller belonging to nowhere -- every tenancy guard
    downstream refuses it. So each call has to say which tenant it is for.

    Missing this is quiet: the flows are reached with a user token in normal
    use, so the service path can stay broken indefinitely without a screen
    ever failing.
    """
    path = GATEWAY / "orchestration.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id in ("_get", "_post")):
            continue
        if "_org" not in {k.arg for k in node.keywords}:
            target = ast.unparse(node.args[2])[:60] if len(node.args) > 2 else "?"
            offenders.append(f"orchestration.py:{node.lineno}  {node.func.id}({target})")

    assert not offenders, (
        f"\n{len(offenders)} downstream call(s) go out without naming a "
        "tenant.\nOn the service path they reach the services as a caller "
        "belonging to no organisation, and every tenancy guard refuses.\n\n  "
        + "\n  ".join(offenders)
        + "\n"
    )


def test_gateway_does_not_lend_its_credential_to_strangers():
    """The gateway must not act as a service for a caller who is not one.

    It holds a credential the services trust. If that is attached to requests
    from anyone, then naming a tenant in ``X-Service-Org`` is enough to have
    the gateway fetch that tenant's data on a stranger's behalf -- a confused
    deputy, and a complete bypass of everything the services check. The token
    the caller presents must be verified first, and with a constant-time
    comparison, since a plain ``==`` leaks how much of a guess was right.
    """
    src = (GATEWAY / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    funcs = {
        fn.name: ast.unparse(fn) for fn in ast.walk(tree)
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    assert "_flow_client" in funcs, "gateway no longer has _flow_client"
    client = funcs["_flow_client"]
    assert "compare_digest" in client, (
        "_flow_client attaches the gateway's service credential without "
        "verifying the caller presented it (or verifies with ==, which leaks "
        "by timing). Any caller could then borrow the gateway's identity."
    )

    assert "_stated_org" in funcs, (
        "the helper that only trusts X-Service-Org from a verified service "
        "caller is gone; the org is being taken at face value again"
    )
    assert "compare_digest" in funcs["_stated_org"], (
        "_stated_org no longer verifies the service token before trusting "
        "the organisation a caller named"
    )

    # And the raw header must not reach the orchestration unchecked.
    assert "organization_id=x_service_org" not in src, (
        "a flow route passes the caller's X-Service-Org straight through; "
        "it must go through _stated_org() first"
    )


def test_guards_can_actually_refuse():
    """The guards reject a foreign tenant rather than merely existing.

    The checks above confirm the call is written; this confirms the thing being
    called still says no. A guard quietly softened to a warning would satisfy
    every other test in this file.
    """
    import sys
    import uuid

    sys.path.insert(0, str(REPO / "libs" / "common"))
    from fastapi import HTTPException

    from chirala_common.authz import Caller, assert_org_matches_caller

    mine, theirs = uuid.uuid4(), uuid.uuid4()
    caller = Caller(subject="s", user_id=uuid.uuid4(), organization_id=mine)

    # The caller's own organisation is allowed through.
    assert_org_matches_caller(caller, mine)

    # Anyone else's is refused, with the same 403 the property guard uses.
    with pytest.raises(HTTPException) as exc:
        assert_org_matches_caller(caller, theirs)
    assert exc.value.status_code == 403

    # A caller with no organisation cannot borrow one by naming it.
    homeless = Caller(subject="s", user_id=uuid.uuid4(), organization_id=None)
    with pytest.raises(HTTPException) as exc:
        assert_org_matches_caller(homeless, theirs)
    assert exc.value.status_code == 403

    # A service caller is not exempt: it names the org it acts for and is held
    # to it, which is what keeps an internal bug inside one tenant.
    svc = Caller(subject="svc", user_id=None, organization_id=mine, is_service=True)
    with pytest.raises(HTTPException):
        assert_org_matches_caller(svc, theirs)


def test_entity_guard_refuses_with_404_and_rejects_unknown_tables():
    """The entity guard hides existence, and cannot be pointed anywhere.

    404 rather than 403 is load-bearing: the id *is* the request, so a 403
    would confirm the row exists and make the endpoint an oracle for another
    tenant's volume. The table allowlist is load-bearing too -- the name is
    interpolated into SQL.
    """
    import sys
    import uuid

    sys.path.insert(0, str(REPO / "libs" / "common"))
    from fastapi import HTTPException

    from chirala_common.authz import Caller, assert_entity_in_org

    caller = Caller(subject="s", user_id=uuid.uuid4(), organization_id=None)

    # No organisation cannot be resolved to one by naming a row, and the
    # refusal is a 404 so it says nothing about whether the row exists.
    with pytest.raises(HTTPException) as exc:
        assert_entity_in_org(
            None, caller, table="finance.folios", entity_id=uuid.uuid4()
        )
    assert exc.value.status_code == 404

    # A table outside the allowlist is a programming error, not a 404.
    with pytest.raises(ValueError):
        assert_entity_in_org(
            None, caller, table="iam.users; DROP TABLE", entity_id=uuid.uuid4()
        )


def test_guard_reads_tenants_off_the_request_and_refuses():
    """The chokepoint refuses, rather than merely being wired in.

    Structural tests prove the call is there. This proves that a request
    naming another tenant is stopped by it -- from the path and from the query
    string, since routes use both and both were leaking.
    """
    import sys
    import uuid

    sys.path.insert(0, str(REPO / "libs" / "common"))
    from fastapi import HTTPException

    from chirala_common.authz import Caller, guard_request_tenancy

    mine, theirs = uuid.uuid4(), uuid.uuid4()
    caller = Caller(subject="s", user_id=uuid.uuid4(), organization_id=mine)

    class FakeRequest:
        """Just the two mappings the guard reads."""

        def __init__(self, path=None, query=None):
            self.path_params = path or {}
            self.query_params = query or {}

    # An organisation named in the query string, belonging to someone else.
    with pytest.raises(HTTPException) as exc:
        guard_request_tenancy(
            FakeRequest(query={"organization_id": str(theirs)}), None, caller
        )
    assert exc.value.status_code == 403

    # The same, from the path.
    with pytest.raises(HTTPException):
        guard_request_tenancy(
            FakeRequest(path={"organization_id": str(theirs)}), None, caller
        )

    # The caller's own organisation passes, and no database is touched: the
    # comparison is against the resolved caller, so `db` is never used.
    guard_request_tenancy(
        FakeRequest(query={"organization_id": str(mine)}), None, caller
    )

    # A request carrying no tenant at all is not the guard's business.
    guard_request_tenancy(FakeRequest(), None, caller)

    # Malformed ids are left to FastAPI's own validation rather than turned
    # into a confusing 403.
    guard_request_tenancy(
        FakeRequest(query={"organization_id": "not-a-uuid"}), None, caller
    )
    guard_request_tenancy(FakeRequest(query={"organization_id": ""}), None, caller)



CONTEXT_CALL = "bind_tenant_context"


def test_permission_dependencies_bind_the_tenant_context():
    """Asking for a permission also tells the database whose data it is.

    Row-level security reads the tenant from the transaction. A permission
    dependency that vets the tenant but never binds it would leave every policy
    matching nothing -- or, if a policy were ever written loosely, everything.
    """
    missing = []
    for path in AUTHZ_MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for fn in ast.walk(tree):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.name in DEP_FACTORIES:
                body = ast.unparse(fn)
                if CONTEXT_CALL not in body:
                    missing.append(f"{path.relative_to(REPO).as_posix()}: {fn.name}()")
                elif body.rindex(CONTEXT_CALL) < body.index(GUARD_CALL):
                    missing.append(f"{path.relative_to(REPO).as_posix()}: {fn.name}() binds before the guard")
    assert not missing, "Tenant context not bound after the guard in:\n  " + "\n  ".join(missing)


def test_services_do_not_connect_as_the_schema_owner():
    """The runtime URL of every service is the RLS-bound login, not the owner.

    A superuser or table owner skips row-level security entirely, so a service
    left on the owner URL would make every policy decorative.
    """
    compose = (REPO / "docker-compose.yml").read_text(encoding="utf-8")
    wrong = []
    for name in ("IAM_DATABASE_URL", "BOOKING_DATABASE_URL", "FINANCE_DATABASE_URL"):
        lines = [ln for ln in compose.splitlines() if ln.strip().startswith(f"{name}:")]
        if not lines:
            wrong.append(f"{name}: not set")
        for ln in lines:
            if "APP_DB_USER" not in ln or "POSTGRES_USER" in ln:
                wrong.append(ln.strip())
    assert not wrong, "Services connecting as the schema owner:\n  " + "\n  ".join(wrong)


def test_every_exemption_here_is_audited_by_the_platform_suite():
    """The exemption above is only safe because something stricter replaces it.

    Skipping a file in this suite removes the tenancy checks from every handler
    in it. That is correct for the platform tier and catastrophic for anything
    else, and the difference currently rests on a comment.

    So assert the substitution rather than describing it: a file exempt here
    must appear in test_platform_separation.PLATFORM_FILES, where every one of
    its handlers is required to name a capability and to be gated on a
    dependency that consults no tenant role or membership. Widening the
    exemption without widening the audit now fails here.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_platform_separation",
        pathlib.Path(__file__).with_name("test_platform_separation.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    unaudited = sorted(
        p.relative_to(REPO).as_posix()
        for p in PLATFORM_ROUTES - set(module.PLATFORM_FILES))
    assert not unaudited, (
        "\nThese files skip the tenancy guards here but are not audited by "
        "tests/test_platform_separation.py:\n  "
        + "\n  ".join(unaudited)
        + "\nAdd them to PLATFORM_FILES there, or stop exempting them here.\n"
    )
