"""Make `pytest` work from the repository root.

Without this there was no root ``conftest.py``, no root ``pyproject.toml``,
and therefore nothing putting the services on the import path or telling the
tests where the database is. ``make test`` ran ``pytest services tests`` and
reported green while quietly skipping the seventeen finance tests -- night
audit, drawer reconciliation, refund bounds, the money paths -- because
``FINANCE_DATABASE_URL`` was unset and the suite is guarded by a ``skipif``.

A suite that passes by not running is worse than a red one. A red suite gets
fixed; a green one gets trusted.

Two things are fixed here, and nothing else:

**The import path.** ``finance_service`` and ``chirala_common`` live in
``services/*`` and ``libs/common`` and are installed inside the containers,
not on the host. Adding them to ``sys.path`` is what lets a test import the
code it is testing.

**The port.** ``.env`` says ``localhost:5432`` because it was written for
running the services directly on the host. Compose publishes Postgres on
whatever port is free -- 5544 on this machine -- so every connection from a
host-run test timed out against a port with nothing behind it. The published
port is asked for rather than assumed, because it is not stable between
machines or even between ``compose down`` cycles.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent

# --------------------------------------------------------------------------
# Import path
# --------------------------------------------------------------------------
# `gateway` is named separately: it sits at the top level rather than under
# `services/`, so globbing the services directory alone left `gateway_app`
# unimportable and the full-loop integration test failing on the import.
for path in [ROOT / "libs" / "common", ROOT / "gateway",
             *(ROOT / "services").glob("*")]:
    if path.is_dir() and str(path) not in sys.path:
        sys.path.insert(0, str(path))


# --------------------------------------------------------------------------
# Environment
# --------------------------------------------------------------------------
def _load_env() -> dict[str, str]:
    """`.env`, parsed. Absent is fine -- CI may set the variables directly."""
    out: dict[str, str] = {}
    env = ROOT / ".env"
    if not env.exists():
        return out
    for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _published_port() -> str | None:
    """The host port compose has Postgres on, or None if it is not running.

    Shelling out to docker from a conftest is not elegant. The alternative is
    every developer exporting a port that changes under them, which is the
    situation that left this suite silently skipped for its whole life.
    """
    if not shutil.which("docker"):
        return None
    try:
        out = subprocess.run(
            ["docker", "compose", "port", "postgres", "5432"],
            cwd=ROOT, capture_output=True, text=True, timeout=30,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    if out.returncode != 0 or ":" not in out.stdout:
        return None
    return out.stdout.strip().rsplit(":", 1)[-1] or None


def _to_host(url: str, port: str) -> str:
    """Point a database URL at the published port on this machine.

    Host as well as port. `.env` is inconsistent about it -- IAM and finance
    say ``localhost``, booking says ``postgres``, which is the name the
    service containers resolve on the compose network and nothing else can.
    A test running on the host cannot reach ``postgres`` at all, so the host
    is rewritten rather than trusted: from here the database is on localhost
    at whatever port compose published, whatever the file claims.

    Credentials and database name are left exactly as they are.
    """
    return re.sub(r"@[^:/@]+:\d+/", f"@localhost:{port}/", url, count=1)


def _compose_url(role: str) -> str | None:
    """A database URL for this role, as compose has it written down.

    The runtime role's password lives only in the compose environment -- not
    in `.env`, which is why the row-security suites had nothing to connect
    as. Read rather than guessed, so rotating it changes nothing here.
    """
    if not shutil.which("docker"):
        return None
    try:
        out = subprocess.run(["docker", "compose", "config"], cwd=ROOT,
                             capture_output=True, text=True, timeout=60)
    except (subprocess.SubprocessError, OSError):
        return None
    if out.returncode != 0:
        return None
    found = re.search(
        rf"postgresql\+psycopg://{re.escape(role)}:[^@\s]+@[^\s/]+/\w+",
        out.stdout)
    return found.group(0) if found else None


def _setup() -> None:
    envfile = _load_env()

    # Anything already exported wins: CI, and a developer pointing at another
    # database, both have to be able to override the file.
    for key in ("FINANCE_DATABASE_URL", "BOOKING_DATABASE_URL",
                "IAM_DATABASE_URL", "SERVICE_TOKEN"):
        if not os.environ.get(key) and envfile.get(key):
            os.environ[key] = envfile[key]

    port = _published_port()
    if port:
        for key in ("FINANCE_DATABASE_URL", "BOOKING_DATABASE_URL",
                    "IAM_DATABASE_URL"):
            if os.environ.get(key):
                os.environ[key] = _to_host(os.environ[key], port)

    # ---- the two roles the row-security suites need ----------------------
    #
    # Those tests compare what the OWNER can see against what the RUNTIME
    # role can, which is the only way to prove a policy does anything: run
    # both halves as the owner and every table looks perfectly isolated
    # because the owner was never filtered in the first place.
    #
    # `.env` only ever had the owner, under the plain name, so
    # `*_MIGRATION_DATABASE_URL` was unset and twenty-eight isolation tests
    # -- the ones that matter most in a multi-tenant PMS -- skipped on every
    # run. The owner moves to the _MIGRATION_ name where it belongs and the
    # runtime role is read from compose, which is the only place it is
    # written down.
    runtime = _compose_url("pms_app")
    if port and runtime:
        runtime = _to_host(runtime, port)
        for prefix in ("FINANCE", "BOOKING", "IAM"):
            plain, owner = f"{prefix}_DATABASE_URL", f"{prefix}_MIGRATION_DATABASE_URL"
            if os.environ.get(plain) and not os.environ.get(owner):
                os.environ[owner] = os.environ[plain]
                os.environ[plain] = runtime

    # The HTTP suites in tests/ address the services the same way, and were
    # skipping for the same reason: a hardcoded port that compose does not
    # use. Only filled in when absent, so `make test-money` still governs.
    for key, service, internal in (("BOOKING_BASE", "booking-core", "8002"),
                                   ("FINANCE_BASE", "finance", "8003"),
                                   # Defaulted to `http://iam:8001` in the
                                   # tests -- a name only the compose network
                                   # resolves, so three tenant and role tests
                                   # died on DNS rather than on anything they
                                   # were written to check.
                                   ("IAM_BASE", "iam", "8001")):
        if os.environ.get(key) or not shutil.which("docker"):
            continue
        try:
            out = subprocess.run(
                ["docker", "compose", "port", service, internal],
                cwd=ROOT, capture_output=True, text=True, timeout=30)
        except (subprocess.SubprocessError, OSError):
            continue
        if out.returncode == 0 and ":" in out.stdout:
            os.environ[key] = f"http://localhost:{out.stdout.strip().rsplit(':', 1)[-1]}"


_setup()


def pytest_report_header(config) -> list[str]:
    """Say up front what the run can and cannot reach.

    The skips were never hidden -- pytest counted them every time. They were
    just easy not to read, and "17 skipped" at the end of a green run is not
    a sentence anybody stops on. Saying it at the TOP, in terms of what is
    not being tested, is harder to scroll past.
    """
    lines = []
    db = os.environ.get("FINANCE_DATABASE_URL")
    # Masked before printing. This header is the line somebody pastes into a
    # chat window when asking why the tests will not run, so it must not
    # carry the password.
    masked = re.sub(r"//([^:]+):[^@]+@", r"//\1:***@", db) if db else None
    lines.append(
        f"database: {masked}" if masked else
        "database: NOT CONFIGURED — the finance ledger tests will skip")
    api = os.environ.get("FINANCE_BASE")
    lines.append(f"services: {api}" if api
                 else "services: NOT REACHABLE — the HTTP suites will skip")
    return lines
