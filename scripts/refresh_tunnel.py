#!/usr/bin/env python
"""Start the quick tunnel and point the deployment at it, in one step.

A quick tunnel gets a new random hostname every time it starts. That matters
more than it sounds: the hostname is what a webhook URL is built from, so every
restart silently invalidates the URL sitting in Razorpay's dashboard, and the
symptom is a guest who paid and a booking that stays held. Nothing errors --
the callback is simply delivered to a hostname that no longer exists.

So the three steps that must happen together are done together::

    python scripts/refresh_tunnel.py

1. Start (or restart) the tunnel and read the hostname it was given.
2. Write it to ``APP_BASE_URL`` in ``.env``.
3. Restart finance, which is what builds tenant webhook URLs from that value.

Then it checks the public path actually reaches the webhook handler, because
the only failure worth catching here is the one where everything looks fine
and callbacks go nowhere.

This exists because there is no domain on the account. With one, a named
tunnel gives a hostname that never changes and none of this is needed -- see
infra/cloudflared/README.md.
"""
from __future__ import annotations

import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"

#: Matches the hostname cloudflared prints once the tunnel is registered.
URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def compose(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "compose", *args],
        cwd=ROOT, capture_output=True, text=True, check=check,
    )


def container_started_at() -> str:
    """When the running tunnel container last started, as an ISO timestamp.

    Needed to read only *this* run's log. A container keeps the output of
    previous runs, so a restart leaves the old hostname sitting above the new
    one -- and a naive search finds the dead one and reports success.
    """
    out = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.StartedAt}}",
         container_name()],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.strip()
    # Docker prints nanoseconds; --since wants something it can parse back.
    return out[:19] + "Z" if len(out) >= 19 else out


def container_name() -> str:
    out = compose("ps", "-q", "cloudflared").stdout.strip()
    if not out:
        raise SystemExit("The cloudflared container is not running.")
    return out.splitlines()[0]


def tunnel_url(timeout_s: int = 60) -> str:
    """The hostname this tunnel was given, once it has one.

    Polled rather than read once: the URL appears a second or two after the
    container starts, and reading too early gets an empty log.

    Scoped to the current run and taking the *last* match, because both of the
    ways this goes wrong end in the same place -- reporting a hostname that no
    longer routes, which looks exactly like success until a callback vanishes.
    """
    since = container_started_at()
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        logs = compose("logs", "--since", since, "cloudflared", check=False)
        found = URL_RE.findall(logs.stdout + logs.stderr)
        if found:
            return found[-1]
        time.sleep(2)
    raise SystemExit(
        "The tunnel did not report a URL within "
        f"{timeout_s}s. Check: docker compose logs cloudflared")


def set_app_base_url(url: str) -> None:
    """Point APP_BASE_URL at the tunnel, leaving the rest of .env alone."""
    text = ENV.read_text(encoding="utf-8")
    new, count = re.subn(r"(?m)^APP_BASE_URL=.*$", f"APP_BASE_URL={url}", text)
    if count != 1:
        raise SystemExit(
            f"Expected exactly one APP_BASE_URL line in {ENV}, found {count}. "
            "Not guessing which to change -- fix it by hand.")
    ENV.write_text(new, encoding="utf-8")


def wait_for_finance(timeout_s: int = 60) -> None:
    """Block until finance answers again.

    It runs its migrations before binding the port, so there is a window of a
    few seconds after ``up -d`` where the gateway cannot reach it and proxies a
    500. Checking during that window reports a broken tunnel and a working one
    looks identical -- so the check waits rather than guesses.
    """
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if probe("http://localhost:8103/health", quiet=True) == 200:
            return
        time.sleep(1)
    print("  ! finance did not come back within "
          f"{timeout_s}s; the checks below may be misleading")


def probe(url: str, method: str = "GET", quiet: bool = False) -> int:
    req = urllib.request.Request(url, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except Exception as exc:  # noqa: BLE001
        if not quiet:
            print(f"  ! {exc}")
        return 0


def main() -> int:
    print("Starting the quick tunnel...")
    compose("--profile", "tunnel", "up", "-d", "cloudflared")

    url = tunnel_url()
    print(f"  tunnel  {url}")

    set_app_base_url(url)
    print(f"  .env    APP_BASE_URL={url}")

    # Every service that builds a URL for a browser reads APP_BASE_URL once, at
    # startup, so all of them have to be restarted or they keep handing out the
    # old hostname. Finance builds each tenant's webhook URL from it;
    # booking-core and iam sign presigned photo URLs against it, and because
    # SigV4 signs the host, a service left behind serves image links that 403
    # rather than merely looking wrong.
    print("Restarting services so they serve the new hostname...")
    compose("up", "-d", "finance", "booking-core", "iam")
    wait_for_finance()

    print("\nChecking the public path:")
    health = probe(f"{url}/health")
    print(f"  {health}  {url}/health"
          f"{'  (gateway reachable)' if health == 200 else '  << expected 200'}")

    # The check that matters. A 404 here is success: it means the request
    # reached finance's webhook handler, which refused an unknown callback ref.
    # A 502 or 000 means a real signed callback would never arrive.
    hook = probe(f"{url}/api/finance/webhooks/razorpay/probe", method="POST")
    ok = hook == 404
    print(f"  {hook}  {url}/api/finance/webhooks/razorpay/<ref>"
          f"{'  (reaches the webhook handler)' if ok else '  << expected 404'}")

    if not (health == 200 and ok):
        print("\nThe tunnel is up but the path is wrong. Callbacks would not "
              "arrive; do not paste this into Razorpay yet.")
        return 1

    print(f"\nDone. Webhook URLs and room photos are now built on {url}")
    print("Re-paste the callback URL from Administration > Payment Gateway "
          "into the Razorpay dashboard -- the old one has stopped working.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
